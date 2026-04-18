"""
steelg8 轻量 agent loop
------------------------

- 消息历史 / 系统 prompt 拼装
- 上游 OpenAI 兼容 HTTP，流式 + 非流式都支持
- 上游返回的 `usage` 字段会顺便带回（供 usage.py 记账）

流式协议（SSE event dict）：
  {"type": "meta",  "decision": {...}}
  {"type": "delta", "content": "部分文本"}
  {"type": "usage", "usage": {prompt_tokens, completion_tokens, total_tokens}, "cost_usd": ...}
  {"type": "done",  "full": "完整文本", "source": "provider:kimi"}
  {"type": "error", "error": "..."}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator
from urllib import request, error

from providers import Provider
from router import RoutingDecision
import pricing


# 某些"thinking"类模型只接受固定 temperature。清单按 model id 前缀匹配。
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
    tools: list[dict[str, Any]] = field(default_factory=list)

    def build_messages(self, user_message: str) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = []
        if self.system_prompt.strip():
            msgs.append({"role": "system", "content": self.system_prompt.strip()})
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

    messages = context.build_messages(user_message)
    accumulated_tool_calls: list[dict[str, Any]] = []
    total_prompt = total_completion = 0
    total_cost = 0.0
    resolved_model = decision.model or (provider.models[0] if provider.models else "")
    final_content = ""
    last_error: str | None = None

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
            # 追加 assistant turn（带 tool_calls）
            messages.append({
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": tcs,
            })
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
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": json.dumps(result, ensure_ascii=False),
                })
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

    messages = context.build_messages(user_message)
    buffered: list[str] = []                    # 跨所有 iter 的 assistant 文本
    iter_buffered: list[str]
    total_prompt = total_completion = 0
    total_cost = 0.0
    resolved_model: str | None = None

    for _ in range(MAX_TOOL_ITER):
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

        # 本轮 assistant 文本 / tool_calls 的聚合
        iter_buffered = []
        tc_accum: dict[int, dict[str, Any]] = {}   # index → {id, type, function.name, function.arguments}
        iter_usage: dict[str, int] | None = None
        iter_model: str | None = None

        try:
            for chunk in _post_sse(
                f"{provider.base_url}/chat/completions",
                payload,
                api_key=provider.api_key(),
                timeout=timeout,
            ):
                if chunk.get("model") and not iter_model:
                    iter_model = chunk["model"]
                    resolved_model = chunk["model"]
                if chunk.get("delta"):
                    text = chunk["delta"]
                    iter_buffered.append(text)
                    buffered.append(text)
                    yield {"type": "delta", "content": text}
                if chunk.get("tool_calls_delta"):
                    _merge_tool_calls_delta(tc_accum, chunk["tool_calls_delta"])
                if chunk.get("usage"):
                    iter_usage = _extract_usage(chunk["usage"])
        except Exception as exc:  # noqa: BLE001
            yield {"type": "error", "error": str(exc)}
            tail = _mock_content(user_message, decision, error=str(exc))
            yield {"type": "delta", "content": f"\n\n[stream 失败，降级 mock] {tail}"}
            yield {"type": "done", "full": "".join(buffered) + f"\n\n{tail}", "source": "mock-fallback"}
            return

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
            messages.append({
                "role": "assistant",
                "content": "".join(iter_buffered),
                "tool_calls": tool_calls_list,
            })

            for tc in tool_calls_list:
                fname = tc["function"].get("name") or ""
                raw_args = tc["function"].get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {}
                yield {"type": "tool_start", "id": tc.get("id"), "name": fname, "args": args}
                result = tool_dispatch(fname, args)
                yield {"type": "tool_result", "id": tc.get("id"), "name": fname, "result": result}
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": json.dumps(result, ensure_ascii=False),
                })
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


def _post_json(url: str, payload: dict[str, Any], *, api_key: str, timeout: int) -> dict[str, Any]:
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_sse(
    url: str,
    payload: dict[str, Any],
    *,
    api_key: str,
    timeout: int,
) -> Iterator[dict[str, Any]]:
    """拉上游 SSE，yield 结构化事件：
      {"delta": "...", "usage": None, "model": None}
      {"delta": None, "usage": {...}, "model": "xxx"}

    合并了 chunk 级 content 与最终 usage chunk，让调用方不必懂 SSE 细节。
    """
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )

    try:
        resp = request.urlopen(req, timeout=timeout)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500] if exc.fp else ""
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

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
    lines = [
        "steelg8 本地内核收到消息。",
        f"原始输入：{user_message[:120]}{'…' if len(user_message) > 120 else ''}",
        f"路由：layer={decision.layer} · reason={decision.reason}",
    ]
    if error:
        lines.append(f"上游调用失败：{error}")
    lines.append(
        "把 ~/.steelg8/providers.json 配齐任意一家 provider，就会切到真模型。"
    )
    return "\n".join(lines)
