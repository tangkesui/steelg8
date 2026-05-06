"""
steelg8 轻量 agent loop
------------------------

- 消息历史 / 系统 prompt 拼装
- 上游 OpenAI 兼容 HTTP，流式 + 非流式都支持
- 上游返回的 `usage` 字段会顺便带回（供 usage.py 记账）

流式协议（SSE event dict）：
  {"type": "meta",  "decision": {...}}
  {"type": "delta", "content": "部分文本"}
  {"type": "_transcript", "message": {...}}  # 内部事件：server 用来落库，不转前端
  {"type": "usage", "usage": {prompt_tokens, completion_tokens, total_tokens}, "cost_usd": ...}
  {"type": "done",  "full": "完整文本", "source": "provider:kimi"}
  {"type": "error", "error": "..."}
"""

from __future__ import annotations

import json
import time as _time
from dataclasses import dataclass, field
from typing import Any, Iterator
from urllib import request

from providers import Provider
from router import RoutingDecision
import pricing
import cache_markers
import logger
import network


# 某些"thinking"类模型只接受固定 temperature，并且 API 会检查
# 历史里的 assistant(带 tool_calls) 消息必须带 reasoning_content 字段。
_FIXED_TEMPERATURE_MODELS: tuple[tuple[str, float], ...] = (
    ("kimi-k2.5", 1.0),
    ("kimi-k2-thinking", 1.0),
    ("kimi-thinking", 1.0),
    ("deepseek-reasoner", 1.0),   # DS R1 也是固定
    ("o1", 1.0),
    ("openai/o1", 1.0),
)


def _effective_temperature(model: str, requested: float) -> float:
    for prefix, fixed in _FIXED_TEMPERATURE_MODELS:
        if model and (model == prefix or model.startswith(prefix)):
            return fixed
    return requested


def _is_thinking_model(model: str) -> bool:
    """判断模型是否走 thinking 协议（输出 reasoning_content，要求回传）。"""
    if not model:
        return False
    for prefix, _ in _FIXED_TEMPERATURE_MODELS:
        if model == prefix or model.startswith(prefix):
            return True
    return False


@dataclass
class ChatMessage:
    role: str
    content: str
    name: str | None = None

    def to_openai(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            out["name"] = self.name
        return out


@dataclass
class AgentContext:
    system_prompt: str = ""
    history: list[ChatMessage] = field(default_factory=list)
    # 允许直接喂 OpenAI-format 的 dict 历史（来自 DB），绕过 ChatMessage dataclass
    history_dicts: list[dict[str, Any]] | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    conversation_id: int | None = None

    def build_messages(
        self,
        user_message: str,
        *,
        provider_name: str = "",
        model: str = "",
    ) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = []
        if self.system_prompt.strip():
            sys_content = cache_markers.build_system_payload(
                self.system_prompt.strip(),
                provider_name=provider_name,
                model=model,
            )
            msgs.append({"role": "system", "content": sys_content})
        if self.history_dicts:
            for raw in self.history_dicts:
                msg = _clone_message(raw)
                if (
                    _is_thinking_model(model)
                    and msg.get("role") == "assistant"
                    and msg.get("tool_calls")
                    and "reasoning_content" not in msg
                ):
                    msg["reasoning_content"] = ""
                msgs.append(msg)
        else:
            for m in self.history:
                msgs.append(m.to_openai())
        msgs.append({"role": "user", "content": user_message})
        return msgs


@dataclass
class AgentResult:
    content: str
    decision: RoutingDecision
    error: str | None = None
    source: str = ""
    usage: dict[str, int] | None = None  # {prompt_tokens, completion_tokens, total_tokens}
    cost_usd: float = 0.0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # 历次 tool call + result
    transcript_messages: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "model": self.decision.model,
            "provider": self.decision.provider,
            "routingLayer": self.decision.layer,
            "routingReason": self.decision.reason,
            "source": self.source,
            "error": self.error,
            "usage": self.usage,
            "costUsd": round(self.cost_usd, 8),
            "toolCalls": list(self.tool_calls),
        }


def _clone_message(message: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe copy before the loop mutates/reuses message objects."""
    return json.loads(json.dumps(message, ensure_ascii=False))


# ---------- 非流式 ----------


MAX_TOOL_ITER = 6    # 防止 LLM 卡在循环里；一般 1~2 轮就够


def run_once(
    user_message: str,
    context: AgentContext,
    provider: Provider | None,
    decision: RoutingDecision,
    *,
    temperature: float = 0.4,
    timeout: int = 30,
    tools: list[dict[str, Any]] | None = None,
    tool_dispatch: Any = None,
) -> AgentResult:
    if provider is None or decision.layer == "mock":
        return AgentResult(
            content=_mock_content(user_message, decision),
            decision=decision,
            source="mock-fallback",
        )

    messages = context.build_messages(
        user_message,
        provider_name=provider.name,
        model=decision.model or "",
    )
    accumulated_tool_calls: list[dict[str, Any]] = []
    transcript_messages: list[dict[str, Any]] = []
    total_prompt = total_completion = 0
    total_cost = 0.0
    resolved_model = decision.model or (provider.models[0] if provider.models else "")
    final_content = ""
    last_error: str | None = None
    extra_headers = cache_markers.extra_headers(
        provider.name,
        conversation_id=context.conversation_id,
        model=decision.model or "",
    )

    for _ in range(MAX_TOOL_ITER):
        model_id = decision.model or (provider.models[0] if provider.models else "")
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "temperature": _effective_temperature(model_id, temperature),
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            body = _post_json(
                f"{provider.base_url}/chat/completions",
                payload,
                api_key=provider.api_key(),
                timeout=timeout,
                extra_headers=extra_headers,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            break

        msg = body.get("choices", [{}])[0].get("message", {}) or {}
        resolved_model = body.get("model") or payload["model"]
        usage = _extract_usage(body.get("usage"))
        if usage:
            total_prompt += usage.get("prompt_tokens", 0)
            total_completion += usage.get("completion_tokens", 0)
        total_cost += pricing.cost_usd(
            resolved_model,
            provider.name,
            usage.get("prompt_tokens", 0) if usage else 0,
            usage.get("completion_tokens", 0) if usage else 0,
        )

        tcs = msg.get("tool_calls") or []
        if tcs and tool_dispatch:
            # 追加 assistant turn（带 tool_calls）。thinking 模型要求带上 reasoning_content
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": tcs,
            }
            rc = msg.get("reasoning_content")
            if rc:
                assistant_msg["reasoning_content"] = rc
            elif _is_thinking_model(decision.model or ""):
                assistant_msg["reasoning_content"] = ""
            messages.append(assistant_msg)
            transcript_messages.append(_clone_message(assistant_msg))
            for tc in tcs:
                fname = (tc.get("function") or {}).get("name", "")
                raw_args = (tc.get("function") or {}).get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except json.JSONDecodeError:
                    args = {}
                result = tool_dispatch(fname, args)
                accumulated_tool_calls.append({
                    "id": tc.get("id"),
                    "name": fname,
                    "args": args,
                    "result": result,
                })
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": json.dumps(result, ensure_ascii=False),
                }
                messages.append(tool_msg)
                transcript_messages.append(_clone_message(tool_msg))
            # 继续 loop，让 LLM 看到 tool result 生成最终答案
            continue

        # 没有 tool_calls → 终局
        final_content = (msg.get("content") or "").strip()
        break
    else:
        last_error = f"tool loop 超过 {MAX_TOOL_ITER} 轮未收敛"

    final_decision = RoutingDecision(
        model=resolved_model,
        provider=decision.provider,
        layer=decision.layer,
        reason=decision.reason,
    )
    return AgentResult(
        content=final_content or (
            _mock_content(user_message, decision, error=last_error) if last_error else "上游返回了空响应。"
        ),
        decision=final_decision,
        source=f"provider:{provider.name}" if not last_error else "mock-fallback",
        usage={"prompt_tokens": total_prompt, "completion_tokens": total_completion,
               "total_tokens": total_prompt + total_completion} if (total_prompt or total_completion) else None,
        cost_usd=total_cost,
        tool_calls=accumulated_tool_calls,
        transcript_messages=transcript_messages,
        error=last_error,
    )


# ---------- 流式 ----------


def run_stream(
    user_message: str,
    context: AgentContext,
    provider: Provider | None,
    decision: RoutingDecision,
    *,
    temperature: float = 0.4,
    timeout: int = 60,
    tools: list[dict[str, Any]] | None = None,
    tool_dispatch: Any = None,
) -> Iterator[dict[str, Any]]:
    yield {"type": "meta", "decision": decision.to_dict()}

    if provider is None or decision.layer == "mock":
        full = _mock_content(user_message, decision)
        for chunk in _fake_stream_chunks(full):
            yield {"type": "delta", "content": chunk}
        yield {
            "type": "usage",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "costUsd": 0.0,
        }
        yield {"type": "done", "full": full, "source": "mock-fallback"}
        return

    messages = context.build_messages(
        user_message,
        provider_name=provider.name,
        model=decision.model or "",
    )
    extra_headers = cache_markers.extra_headers(
        provider.name,
        conversation_id=context.conversation_id,
        model=decision.model or "",
    )
    buffered: list[str] = []                    # 跨所有 iter 的 assistant 文本
    iter_buffered: list[str]
    total_prompt = total_completion = 0
    total_cost = 0.0
    resolved_model: str | None = None

    iter_idx = 0
    for _ in range(MAX_TOOL_ITER):
        iter_idx += 1
        model_id = decision.model or (provider.models[0] if provider.models else "")
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "temperature": _effective_temperature(model_id, temperature),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        iter_started = _time.time()
        logger.info("agent.iter.start",
                    conversation_id=context.conversation_id,
                    iter=iter_idx,
                    provider=provider.name,
                    model=model_id,
                    messages_count=len(messages),
                    has_tools=bool(tools),
                    extra_headers=list(extra_headers.keys()) if extra_headers else [])

        # 本轮 assistant 文本 / tool_calls 的聚合
        iter_buffered = []
        iter_reasoning: list[str] = []   # thinking 模型的推理过程增量
        tc_accum: dict[int, dict[str, Any]] = {}   # index → {id, type, function.name, function.arguments}
        iter_usage: dict[str, int] | None = None
        iter_model: str | None = None

        try:
            for chunk in _post_sse(
                f"{provider.base_url}/chat/completions",
                payload,
                api_key=provider.api_key(),
                timeout=timeout,
                extra_headers=extra_headers,
            ):
                if chunk.get("model") and not iter_model:
                    iter_model = chunk["model"]
                    resolved_model = chunk["model"]
                if chunk.get("delta"):
                    text = chunk["delta"]
                    iter_buffered.append(text)
                    buffered.append(text)
                    yield {"type": "delta", "content": text}
                if chunk.get("reasoning_delta"):
                    iter_reasoning.append(chunk["reasoning_delta"])
                if chunk.get("tool_calls_delta"):
                    _merge_tool_calls_delta(tc_accum, chunk["tool_calls_delta"])
                if chunk.get("usage"):
                    iter_usage = _extract_usage(chunk["usage"])
        except Exception as exc:  # noqa: BLE001
            logger.error("agent.iter.exception",
                         exc=exc,
                         conversation_id=context.conversation_id,
                         iter=iter_idx,
                         provider=provider.name,
                         model=model_id,
                         duration_ms=int((_time.time() - iter_started) * 1000))
            yield {"type": "error", "error": str(exc)}
            tail = _mock_content(user_message, decision, error=str(exc))
            yield {"type": "delta", "content": f"\n\n[stream 失败，降级 mock] {tail}"}
            yield {"type": "done", "full": "".join(buffered) + f"\n\n{tail}", "source": "mock-fallback"}
            return

        logger.info("agent.iter.end",
                    conversation_id=context.conversation_id,
                    iter=iter_idx,
                    model=iter_model or model_id,
                    duration_ms=int((_time.time() - iter_started) * 1000),
                    prompt_tokens=(iter_usage or {}).get("prompt_tokens", 0),
                    completion_tokens=(iter_usage or {}).get("completion_tokens", 0),
                    content_len=sum(len(s) for s in iter_buffered),
                    tool_calls_count=len(tc_accum),
                    tool_names=[
                        (tc_accum[i].get("function", {}) or {}).get("name", "")
                        for i in sorted(tc_accum.keys())
                    ])

        # usage 统计
        if iter_usage:
            total_prompt += iter_usage.get("prompt_tokens", 0)
            total_completion += iter_usage.get("completion_tokens", 0)
        total_cost += pricing.cost_usd(
            iter_model or payload["model"],
            provider.name,
            iter_usage.get("prompt_tokens", 0) if iter_usage else 0,
            iter_usage.get("completion_tokens", 0) if iter_usage else 0,
        )

        # 本轮 tool calls 收尾
        if tc_accum and tool_dispatch:
            tool_calls_list = [tc_accum[i] for i in sorted(tc_accum.keys())]
            # 补齐 arguments 可能是空字符串
            for tc in tool_calls_list:
                tc.setdefault("type", "function")
                tc.setdefault("id", tc.get("id") or f"call_{id(tc)}")
                tc.setdefault("function", {"name": "", "arguments": ""})
                tc["function"].setdefault("arguments", "")
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(iter_buffered),
                "tool_calls": tool_calls_list,
            }
            # thinking 模型（Kimi K2.5 / DeepSeek R1 等）要求回发时带上 reasoning_content
            if iter_reasoning:
                assistant_msg["reasoning_content"] = "".join(iter_reasoning)
            elif _is_thinking_model(decision.model or ""):
                # 即便本轮没流出 reasoning，也给个空串兜底（Kimi 校验字段存在性）
                assistant_msg["reasoning_content"] = ""
            messages.append(assistant_msg)
            yield {"type": "_transcript", "message": _clone_message(assistant_msg)}

            # 一轮内多个互相依赖的 docx 插入类工具：前一个失败就短路后续
            # （避免 AI 并发丢 9 个 insert_section、锚点全部断链的情况）
            _docx_chain = {"docx_insert_section", "docx_append_paragraphs"}
            _chain_broken = False

            for tc in tool_calls_list:
                fname = tc["function"].get("name") or ""
                raw_args = tc["function"].get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {}
                yield {"type": "tool_start", "id": tc.get("id"), "name": fname, "args": args}

                # 短路：链条已断就不再执行，直接告诉 LLM 这组 call 被拒
                if _chain_broken and fname in _docx_chain:
                    result = {
                        "error": "chain_skipped",
                        "hint": ("同轮前序的 docx 插入已经失败，锚点链断裂。"
                                 "请先根据上一条 tool 返回的 available_headings 调整策略，"
                                 "**一次只插一个章节**，收到成功回执再发下一条。"),
                    }
                else:
                    t0 = _time.time()
                    result = tool_dispatch(fname, args)
                    logger.info("tool.call",
                                conversation_id=context.conversation_id,
                                iter=iter_idx,
                                tool=fname,
                                duration_ms=int((_time.time() - t0) * 1000),
                                success=not bool(isinstance(result, dict) and result.get("error")))
                    # docx 链首次失败 → 后续同类 call 短路
                    if (fname in _docx_chain
                            and isinstance(result, dict)
                            and result.get("error")):
                        _chain_broken = True
                        logger.warn("tool.chain_broken",
                                    conversation_id=context.conversation_id,
                                    iter=iter_idx,
                                    at_tool=fname,
                                    error=str(result.get("error"))[:120])
                yield {"type": "tool_result", "id": tc.get("id"), "name": fname, "result": result}
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": json.dumps(result, ensure_ascii=False),
                }
                messages.append(tool_msg)
                yield {"type": "_transcript", "message": _clone_message(tool_msg)}
            # 继续下一轮让 LLM 基于 tool result 继续输出
            continue

        # 没有 tool_calls：终局
        break

    # 聚合 usage + done
    model_id = resolved_model or (decision.model or (provider.models[0] if provider.models else ""))
    yield {
        "type": "usage",
        "usage": {"prompt_tokens": total_prompt, "completion_tokens": total_completion,
                  "total_tokens": total_prompt + total_completion},
        "costUsd": round(total_cost, 8),
        "model": model_id,
    }
    yield {
        "type": "done",
        "full": "".join(buffered),
        "source": f"provider:{provider.name}",
    }


def _merge_tool_calls_delta(accum: dict[int, dict[str, Any]], delta: list[dict[str, Any]]) -> None:
    """把 SSE 里 tool_calls 的增量 append 到 accum（按 index 聚合）。"""
    for d in delta:
        idx = int(d.get("index", 0))
        slot = accum.setdefault(idx, {"function": {"name": "", "arguments": ""}})
        if d.get("id"):
            slot["id"] = d["id"]
        if d.get("type"):
            slot["type"] = d["type"]
        fn = d.get("function") or {}
        if fn.get("name"):
            slot["function"]["name"] = (slot["function"].get("name") or "") + fn["name"]
        if fn.get("arguments"):
            slot["function"]["arguments"] = (slot["function"].get("arguments") or "") + fn["arguments"]


# ---------- helpers ----------


def _extract_usage(raw: Any) -> dict[str, int] | None:
    if not isinstance(raw, dict):
        return None
    prompt = int(raw.get("prompt_tokens") or 0)
    completion = int(raw.get("completion_tokens") or 0)
    total = int(raw.get("total_tokens") or (prompt + completion))
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    api_key: str,
    timeout: int,
    extra_headers: dict[str, str] | None = None,
    retries: int = 2,
) -> dict[str, Any]:
    """POST chat completions（非流式）。

    瞬时错误（429/500/502/503/504/超时）内建重试 2 次；
    4xx 客户端错误（如 400/401/403）直接抛出不重试。
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    try:
        body = network.request_json(
            url,
            method="POST",
            payload=payload,
            headers=headers,
            timeout=timeout,
            retries=retries,
        )
    except network.NetworkError as exc:
        raise RuntimeError(str(exc)) from exc
    if not isinstance(body, dict):
        raise RuntimeError("上游返回 JSON 不是对象")
    return body


def _post_sse(
    url: str,
    payload: dict[str, Any],
    *,
    api_key: str,
    timeout: int,
    extra_headers: dict[str, str] | None = None,
    retries: int = 2,
) -> Iterator[dict[str, Any]]:
    """拉上游 SSE，yield 结构化事件：
      {"delta": "...", "usage": None, "model": None}
      {"delta": None, "usage": {...}, "model": "xxx"}

    连接建立前失败（含 429/5xx/超时）会自动重试，最多 `retries` 次；
    一旦连上开始读流，就不再重试（避免双写）。
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    # 连接建立之前的 retry：open_request 内部会按状态码判定是否 retryable
    try:
        resp = network.open_request(req, timeout=timeout, retries=retries)
    except network.NetworkError as exc:
        raise RuntimeError(str(exc)) from exc

    try:
        while True:
            line = resp.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text or text.startswith(":"):
                continue
            if not text.startswith("data:"):
                continue
            data_part = text[len("data:"):].strip()
            if data_part == "[DONE]":
                break
            try:
                evt = json.loads(data_part)
            except json.JSONDecodeError:
                continue

            out: dict[str, Any] = {}
            if "model" in evt and evt["model"]:
                out["model"] = evt["model"]
            choices = evt.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    out["delta"] = content
                # Kimi K2.5 / DeepSeek R1 等 thinking 模型会在 delta 里输出
                # reasoning_content 增量。thinking 模型要求后续轮次把这个字段
                # 回传给它（否则会抛 "reasoning_content is missing"）。
                reasoning = delta.get("reasoning_content")
                if reasoning:
                    out["reasoning_delta"] = reasoning
                tc_delta = delta.get("tool_calls")
                if tc_delta:
                    out["tool_calls_delta"] = tc_delta
            if evt.get("usage"):
                out["usage"] = evt["usage"]
            if out:
                yield out
    finally:
        resp.close()


def _fake_stream_chunks(text: str, size: int = 24) -> Iterator[str]:
    for i in range(0, len(text), size):
        yield text[i : i + size]


def _mock_content(user_message: str, decision: RoutingDecision, *, error: str | None = None) -> str:
    # 上游失败 → 友好化错误提示，引导用户换模型或稍后重试
    if error:
        lines = [f"⚠️ **{decision.provider or '未知'}** / `{decision.model or '-'}` 调用失败"]
        err_lower = (error or "").lower()
        if "429" in error or "overload" in err_lower or "rate" in err_lower:
            lines.append("")
            lines.append("**原因**：上游瞬时过载或限流（内部已自动重试 2 次）。")
            lines.append("**建议**：")
            lines.append("- 等 10-30 秒再发，过载通常秒级恢复")
            lines.append("- 或从右上角「模型」下拉换另一家（Qwen/DeepSeek/Claude 都能接）")
        elif "401" in error or "403" in error or "认证" in error or "api key" in err_lower:
            lines.append("")
            lines.append("**原因**：API Key 无效或无权限。")
            lines.append("**建议**：打开 设置 → 重新填 key，或点 ↻ 刷 provider 列表")
        elif "404" in error:
            lines.append("")
            lines.append("**原因**：模型不存在或 base_url 错了。")
            lines.append("**建议**：点「⟲ 同步模型」拉一次最新列表再选")
        elif "timeout" in err_lower or "超时" in error:
            lines.append("")
            lines.append("**原因**：网络超时。")
            lines.append("**建议**：稍后重试，或检查网络 / VPN")
        else:
            lines.append("")
            lines.append(f"**详细**：{error}")
        return "\n".join(lines)

    # 没错误 → 完全没配 provider 的兜底话术
    lines = [
        "⚠️ **未配置任何 provider**",
        "",
        f"路由：{decision.layer} · {decision.reason}",
        "",
        "请在右上角 ↻ 刷新，或打开 设置 配一个 provider。",
    ]
    return "\n".join(lines)
