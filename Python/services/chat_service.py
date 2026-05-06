from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import agent
import conversations as conv_store
import history_manager
import logger
import memory
import project as project_mod
import router
from providers import Provider, ProviderRegistry
from skills import registry as tool_registry


class ChatRequestError(ValueError):
    pass


@dataclass
class ChatRequest:
    message: str
    model: str | None
    history: list[dict[str, Any]]
    stream: bool
    conversation_id: int | None

    @classmethod
    def parse(cls, body: Any, *, stream_endpoint: bool) -> "ChatRequest | None":
        if not isinstance(body, dict):
            return None
        message = str(body.get("message", "")).strip()
        if not message:
            return None
        model = body.get("model") or None
        history = body.get("history") or []
        if not isinstance(history, list):
            history = []
        conv_raw = body.get("conversationId")
        try:
            conv_id = int(conv_raw) if conv_raw is not None and str(conv_raw).strip() else None
        except (TypeError, ValueError):
            conv_id = None
        return cls(
            message=message,
            model=model,
            history=history,
            stream=bool(body.get("stream")) or stream_endpoint,
            conversation_id=conv_id,
        )


@dataclass
class PreparedChat:
    request: ChatRequest
    soul_text: str
    context: agent.AgentContext
    provider: Provider | None
    decision: router.RoutingDecision
    rag_hits: list[Any]
    tools: list[dict[str, Any]]
    tool_dispatch: Callable[[str, Any], Any]
    conversation_id: int
    compression_result: history_manager.CompressionResult


def soul_summary(soul_text: str) -> str:
    for line in soul_text.splitlines():
        line = line.strip()
        if line.startswith("- "):
            return line[2:]
    return "方案不求人。"


_CONTEXT_PROBE_RE = re.compile(
    r"^(ping|pong|test|hi|hello|hey|你好|您好|在吗|测试|连通性测试)[.!?。！？\s]*$",
    re.IGNORECASE,
)


def is_context_probe(message: str) -> bool:
    """True for low-signal connectivity checks that should not drag in context."""
    return bool(_CONTEXT_PROBE_RE.match((message or "").strip()))


def build_system_prompt(
    soul_text: str,
    *,
    project_root: str | None = None,
    project_name: str = "",
) -> str:
    parts = [
        "## L1 · Soul",
        soul_text.strip(),
    ]

    mem_block = memory.compose_memory_block(
        include_user=True,
        project_root=project_root,
        project_name=project_name,
    )
    if mem_block:
        parts.append(mem_block)

    parts.append(
        "## 对话基调\n\n"
        "你是 steelg8 的本地内核。回答直接，根据当前请求挑合适的详略。"
        "遇到用户强调偏好 / 习惯 / 项目背景 / 重要决策时，可以用 remember() 工具"
        "把它记到 user.md 或 project/steelg8.md，之后的对话你会看到。"
    )
    return "\n\n".join(parts)


def prepare_chat(
    body: Any,
    registry: ProviderRegistry,
    *,
    soul_text: str,
    stream_endpoint: bool,
) -> PreparedChat:
    req = ChatRequest.parse(body, stream_endpoint=stream_endpoint)
    if req is None:
        raise ChatRequestError("message is required")

    base_system, project_root, project_name = _build_base_system(soul_text)

    decision = router.route(req.message, registry, explicit_model=req.model)
    provider: Provider | None = (
        registry.providers.get(decision.provider) if decision.provider else None
    )

    logger.info(
        "chat.start",
        conversation_id=req.conversation_id,
        stream=stream_endpoint,
        model_requested=req.model or None,
        model_resolved=decision.model,
        provider=decision.provider,
        routing_layer=decision.layer,
        routing_reason=decision.reason,
        message_len=len(req.message),
        provider_ready=(provider is not None and provider.is_ready()) if provider else False,
    )

    conv_id = req.conversation_id
    conv = conv_store.get_conversation(conv_id) if conv_id is not None else None
    if conv is None:
        conv = conv_store.get_or_create_project_conversation(
            project_root=project_root,
            title=project_name or "项目对话",
        )
        conv_id = conv.id
        logger.info("conversation.create", conversation_id=conv_id, project_root=project_root)

    isolate_context = is_context_probe(req.message)
    base_system_tokens = history_manager.estimate_tokens(base_system)
    if isolate_context:
        compression_result = history_manager.CompressionResult(
            compressed=False,
            reason="context probe isolated",
        )
    else:
        compression_result = history_manager.maybe_compress(
            conv_id,
            registry,
            model=decision.model or "",
            system_prompt_tokens=base_system_tokens,
        )
    if compression_result.compressed:
        logger.info(
            "compression.triggered",
            conversation_id=conv_id,
            count=compression_result.compressed_count,
            summary_tokens=compression_result.new_summary_tokens,
            reason=compression_result.reason,
        )

    summary_part = "" if isolate_context else history_manager.summary_block(conv_id)
    system_prompt = base_system + ("\n\n" + summary_part if summary_part else "")

    if isolate_context:
        system_prompt = (
            system_prompt
            + "\n\n## 本轮上下文隔离\n\n"
            + "这是一条连通性或上下文隔离探测消息。只按本轮用户消息回答；"
            + "如果用户只说 ping，请简短回复 pong。"
        )

    rag_hits = (
        []
        if isolate_context
        else project_mod.retrieve(req.message, registry, top_k=5)
    )
    if rag_hits:
        logger.info(
            "rag.retrieve",
            conversation_id=conv_id,
            count=len(rag_hits),
            top_score=rag_hits[0].score if rag_hits else 0,
            paths=[h.rel_path for h in rag_hits[:5]],
            retrieval=[getattr(h, "retrieval", "") for h in rag_hits[:5]],
        )
        rag_block = "\n\n".join(
            _rag_context_item(i + 1, h)
            for i, h in enumerate(rag_hits)
        )
        system_prompt = (
            system_prompt
            + "\n\n## 相关项目资料（混合召回，可引用）\n\n"
            + rag_block
        )

    history_dicts = [] if isolate_context else history_manager.build_history_for_llm(conv_id)
    conv_store.append_message(
        conv_id,
        role="user",
        content=req.message,
        tokens=history_manager.estimate_tokens(req.message),
    )

    context = agent.AgentContext(
        system_prompt=system_prompt,
        history_dicts=history_dicts,
        conversation_id=conv_id,
    )
    tools = tool_registry.tool_schemas()

    def dispatch_tool(name: str, args: Any) -> Any:
        return tool_registry.dispatch(name, args, registry=registry)

    return PreparedChat(
        request=req,
        soul_text=soul_text,
        context=context,
        provider=provider,
        decision=decision,
        rag_hits=rag_hits,
        tools=tools,
        tool_dispatch=dispatch_tool,
        conversation_id=conv_id,
        compression_result=compression_result,
    )


def run_once(prepared: PreparedChat) -> dict[str, Any]:
    # 延迟 import 避免循环：chat_persistence 依赖本模块的 PreparedChat。
    from services import chat_persistence

    result = agent.run_once(
        prepared.request.message,
        prepared.context,
        prepared.provider,
        prepared.decision,
        tools=prepared.tools,
        tool_dispatch=prepared.tool_dispatch,
    )
    chat_persistence.persist_run_once_result(prepared, result)

    payload = result.to_dict()
    payload["soulSummary"] = soul_summary(prepared.soul_text)
    payload["conversationId"] = prepared.conversation_id
    if prepared.compression_result.compressed:
        payload["compression"] = compression_payload(prepared.compression_result)
    if prepared.rag_hits:
        payload["ragHits"] = rag_hits_payload(prepared.rag_hits)
        payload["citations"] = citations_payload(prepared.rag_hits)
    return payload


def stream_events(prepared: PreparedChat) -> Iterator[dict[str, Any]]:
    return agent.run_stream(
        prepared.request.message,
        prepared.context,
        prepared.provider,
        prepared.decision,
        tools=prepared.tools,
        tool_dispatch=prepared.tool_dispatch,
    )


def conversation_event(prepared: PreparedChat) -> dict[str, Any]:
    return {
        "type": "conversation",
        "conversationId": prepared.conversation_id,
        "compression": compression_payload(prepared.compression_result),
    }


def rag_event(prepared: PreparedChat) -> dict[str, Any] | None:
    if not prepared.rag_hits:
        return None
    return {
        "type": "rag",
        "hits": rag_hits_payload(prepared.rag_hits),
        "citations": citations_payload(prepared.rag_hits),
    }


def compression_payload(result: history_manager.CompressionResult) -> dict[str, Any]:
    return {
        "compressed": bool(result and result.compressed),
        "count": getattr(result, "compressed_count", 0),
        "summaryTokens": getattr(result, "new_summary_tokens", 0),
        "reason": getattr(result, "reason", ""),
    }


def rag_hits_payload(rag_hits: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "relPath": h.rel_path,
            "chunkIdx": h.chunk_idx,
            "score": h.score,
            "preview": h.text[:240],
            "sourceType": getattr(h, "source_type", "project"),
            "retrieval": getattr(h, "retrieval", "vector"),
            "page": getattr(h, "page", None),
            "heading": getattr(h, "heading", ""),
            "paragraphIndex": getattr(h, "paragraph_idx", 0),
            "charStart": getattr(h, "start_char", 0),
            "charEnd": getattr(h, "end_char", 0),
            "contentHash": getattr(h, "content_hash", ""),
            "citation": h.citation() if hasattr(h, "citation") else {},
        }
        for h in rag_hits
    ]


def citations_payload(rag_hits: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for h in rag_hits:
        if hasattr(h, "citation"):
            out.append(h.citation())
    return out


def _rag_context_item(index: int, hit: Any) -> str:
    parts = [
        f"[{index}] {hit.rel_path}",
        f"score={hit.score}",
        f"via={getattr(hit, 'retrieval', 'vector')}",
        f"source={getattr(hit, 'source_type', 'project')}",
    ]
    page = getattr(hit, "page", None)
    heading = getattr(hit, "heading", "")
    if page is not None:
        parts.append(f"page={page}")
    if heading:
        parts.append(f"heading={heading}")
    parts.append(
        f"chars={getattr(hit, 'start_char', 0)}-{getattr(hit, 'end_char', 0)}"
    )
    return f"{' · '.join(parts)}\n{hit.text}"


def _build_base_system(soul_text: str) -> tuple[str, str | None, str]:
    active = project_mod.get_active()
    project_root = active.get("path") if active else None
    project_name = active.get("name", "") if active else ""
    sys_prompt = build_system_prompt(
        soul_text,
        project_root=project_root,
        project_name=project_name,
    )
    return sys_prompt, project_root, project_name
