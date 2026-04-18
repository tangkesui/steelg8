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
        }


# ---------- 非流式 ----------


def run_once(
    user_message: str,
    context: AgentContext,
    provider: Provider | None,
    decision: RoutingDecision,
    *,
    temperature: float = 0.4,
    timeout: int = 30,
) -> AgentResult:
    if provider is None or decision.layer == "mock":
        return AgentResult(
            content=_mock_content(user_message, decision),
            decision=decision,
            source="mock-fallback",
        )

    payload = {
        "model": decision.model or (provider.models[0] if provider.models else ""),
        "messages": context.build_messages(user_message),
        "temperature": temperature,
        "stream": False,
    }

    try:
        body = _post_json(
            f"{provider.base_url}/chat/completions",
            payload,
            api_key=provider.api_key(),
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return AgentResult(
            content=_mock_content(user_message, decision, error=str(exc)),
            decision=decision,
            error=str(exc),
            source="mock-fallback",
        )

    content = (
        body.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )
    resolved_model = body.get("model") or payload["model"]
    usage = _extract_usage(body.get("usage"))
    cost = pricing.cost_usd(
        resolved_model,
        provider.name,
        usage.get("prompt_tokens", 0) if usage else 0,
        usage.get("completion_tokens", 0) if usage else 0,
    )

    final_decision = RoutingDecision(
        model=resolved_model,
        provider=decision.provider,
        layer=decision.layer,
        reason=decision.reason,
    )
    return AgentResult(
        content=content or "上游返回了空响应。",
        decision=final_decision,
        source=f"provider:{provider.name}",
        usage=usage,
        cost_usd=cost,
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

    payload = {
        "model": decision.model or (provider.models[0] if provider.models else ""),
        "messages": context.build_messages(user_message),
        "temperature": temperature,
        "stream": True,
        # OpenAI-compat：要 usage 必须显式请求；OpenRouter/DeepSeek/Qwen/Kimi 都认
        "stream_options": {"include_usage": True},
    }

    buffered: list[str] = []
    final_usage: dict[str, int] | None = None
    resolved_model: str | None = None

    try:
        for chunk in _post_sse(
            f"{provider.base_url}/chat/completions",
            payload,
            api_key=provider.api_key(),
            timeout=timeout,
        ):
            # _post_sse yield 结构：{"delta":..., "usage":..., "model":...}
            if chunk.get("model") and not resolved_model:
                resolved_model = chunk["model"]
            if chunk.get("delta"):
                text = chunk["delta"]
                buffered.append(text)
                yield {"type": "delta", "content": text}
            if chunk.get("usage"):
                final_usage = _extract_usage(chunk["usage"])
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "error": str(exc)}
        tail = _mock_content(user_message, decision, error=str(exc))
        yield {"type": "delta", "content": f"\n\n[stream 失败，降级 mock] {tail}"}
        yield {"type": "done", "full": "".join(buffered) + f"\n\n{tail}", "source": "mock-fallback"}
        return

    # usage 事件放在 done 之前，前端收到后可以即时更新金额
    model_id = resolved_model or payload["model"]
    prompt_tk = final_usage.get("prompt_tokens", 0) if final_usage else 0
    comp_tk = final_usage.get("completion_tokens", 0) if final_usage else 0
    cost = pricing.cost_usd(model_id, provider.name, prompt_tk, comp_tk)

    yield {
        "type": "usage",
        "usage": final_usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "costUsd": round(cost, 8),
        "model": model_id,
    }

    yield {
        "type": "done",
        "full": "".join(buffered),
        "source": f"provider:{provider.name}",
    }


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
