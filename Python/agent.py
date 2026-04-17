"""
steelg8 轻量 agent loop
------------------------

MVP 阶段不 Fork Hermes（ADR: 决定自写），在这里维护一个最小的：

- 消息历史（system / user / assistant）
- 工具调用钩子（Phase 2 再接 docx 模板填充等 skill）
- 流式输出（通过 generator yield chunk）

设计哲学：
- **接口稳**：对外就一个 run_stream() / run_once()，后续替换底层不影响 server.py
- **不绑协议**：上游 API 调用屏蔽在 _chat_stream 里，走 OpenAI 兼容 HTTP
- **可降级**：任何一步失败都能优雅降级到 mock 回复，别让 UI 卡死

L1 soul 注入 + L2/L3 记忆层在这里拼 system prompt；实际 L2/L3 文件读取
由 server.py 按需加载后传进来，保持 agent.py 无 I/O 依赖便于单测。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator
from urllib import request, error

from providers import Provider
from router import RoutingDecision


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    name: str | None = None

    def to_openai(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            out["name"] = self.name
        return out


@dataclass
class AgentContext:
    """单次对话的运行上下文——系统 prompt、历史、工具列表等。"""

    system_prompt: str = ""
    history: list[ChatMessage] = field(default_factory=list)
    # Phase 2 再填：工具定义、skill registry、记忆层快照
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
    source: str = ""  # "provider:kimi" / "mock-fallback" / ...

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "model": self.decision.model,
            "provider": self.decision.provider,
            "routingLayer": self.decision.layer,
            "routingReason": self.decision.reason,
            "source": self.source,
            "error": self.error,
        }


# ---------- 非流式（/chat 普通响应） ----------


def run_once(
    user_message: str,
    context: AgentContext,
    provider: Provider | None,
    decision: RoutingDecision,
    *,
    temperature: float = 0.4,
    timeout: int = 30,
) -> AgentResult:
    """对上游发一次完整请求，返回 AgentResult。provider 为 None 时走 mock。"""

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
    # 如果上游返回了更精确的 model id，就更新 decision（对调试友好）
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
    )


# ---------- 流式（/chat SSE 响应） ----------


def run_stream(
    user_message: str,
    context: AgentContext,
    provider: Provider | None,
    decision: RoutingDecision,
    *,
    temperature: float = 0.4,
    timeout: int = 60,
) -> Iterator[dict[str, Any]]:
    """以 event dict 形式流式 yield。每个 dict 形如：

    - {"type": "meta", "decision": {...}}
    - {"type": "delta", "content": "部分文本"}
    - {"type": "done", "full": "完整文本", "source": "provider:kimi"}
    - {"type": "error", "error": "..."}

    server.py 会把每个 dict 转成一行 SSE 发给 WebView。
    """

    yield {"type": "meta", "decision": decision.to_dict()}

    if provider is None or decision.layer == "mock":
        full = _mock_content(user_message, decision)
        # 把 mock 也切成假 chunk，保证前端流式逻辑一致
        for chunk in _fake_stream_chunks(full):
            yield {"type": "delta", "content": chunk}
        yield {"type": "done", "full": full, "source": "mock-fallback"}
        return

    payload = {
        "model": decision.model or (provider.models[0] if provider.models else ""),
        "messages": context.build_messages(user_message),
        "temperature": temperature,
        "stream": True,
    }

    buffered: list[str] = []
    try:
        for chunk_text in _post_sse(
            f"{provider.base_url}/chat/completions",
            payload,
            api_key=provider.api_key(),
            timeout=timeout,
        ):
            if not chunk_text:
                continue
            buffered.append(chunk_text)
            yield {"type": "delta", "content": chunk_text}
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "error": str(exc)}
        # 流失败时补个 mock tail，前端才能收尾
        tail = _mock_content(user_message, decision, error=str(exc))
        yield {"type": "delta", "content": f"\n\n[stream 失败，降级 mock] {tail}"}
        yield {"type": "done", "full": "".join(buffered) + f"\n\n{tail}", "source": "mock-fallback"}
        return

    yield {"type": "done", "full": "".join(buffered), "source": f"provider:{provider.name}"}


# ---------- helpers ----------


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
) -> Iterator[str]:
    """拉 OpenAI 兼容的 `data: {...}` SSE 流，yield 每个 delta 的 content。"""

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
            choices = evt.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if content:
                yield content
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
