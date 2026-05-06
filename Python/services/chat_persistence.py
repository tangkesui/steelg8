"""
chat 落库职责单独成模块。

chat_service 负责 prepare / orchestration；这里只关心：
- assistant tool_calls 与 role=tool 消息按 OpenAI 顺序入库
- 流式 partial / final 时把已生成的 assistant 文本与 tool transcript 持久化
- usage 记账（mock 路由不计费）
"""
from __future__ import annotations

from typing import Any

import conversations as conv_store
import history_manager
import usage

from services.chat_service import PreparedChat


def persist_transcript_messages(
    conversation_id: int,
    messages: list[dict[str, Any]],
) -> None:
    """按 OpenAI tool calling 顺序写入 assistant(tool_calls) / role=tool 消息。"""
    for msg in messages:
        role = msg.get("role")
        if role == "assistant":
            conv_store.append_message(
                conversation_id,
                role="assistant",
                content=msg.get("content") or "",
                tool_calls=msg.get("tool_calls") or [],
                tokens=history_manager.estimate_message_tokens(msg),
            )
        elif role == "tool":
            conv_store.append_message(
                conversation_id,
                role="tool",
                content=msg.get("content") or "",
                name=msg.get("name"),
                tool_call_id=msg.get("tool_call_id"),
                tokens=history_manager.estimate_message_tokens(msg),
            )


def persist_stream_partial(
    prepared: PreparedChat,
    *,
    transcript: list[dict[str, Any]],
    content: str,
    usage_payload: dict[str, int] | None,
) -> None:
    if not content and not transcript:
        return
    persist_transcript_messages(prepared.conversation_id, transcript)
    if content:
        conv_store.append_message(
            prepared.conversation_id,
            role="assistant",
            content=content,
            tokens=(usage_payload or {}).get("completion_tokens", 0)
            or history_manager.estimate_tokens(content),
        )
    conv_store.auto_title_from_first_user(prepared.conversation_id)


def persist_stream_final(
    prepared: PreparedChat,
    *,
    transcript: list[dict[str, Any]],
    content: str,
    usage_payload: dict[str, int] | None,
) -> None:
    persist_stream_partial(
        prepared,
        transcript=transcript,
        content=content,
        usage_payload=usage_payload,
    )


def record_stream_usage(
    prepared: PreparedChat,
    *,
    usage_payload: dict[str, int] | None,
    model: str | None,
) -> None:
    """流式结束后记账。mock 路由不计费。"""
    if (
        prepared.provider is not None
        and prepared.decision.layer != "mock"
        and usage_payload
        and (usage_payload.get("prompt_tokens") or usage_payload.get("completion_tokens"))
    ):
        usage.record(
            model=model or prepared.decision.model,
            provider=prepared.decision.provider,
            layer=prepared.decision.layer,
            prompt_tokens=usage_payload.get("prompt_tokens", 0),
            completion_tokens=usage_payload.get("completion_tokens", 0),
        )


def persist_run_once_result(prepared: PreparedChat, result: Any) -> None:
    """非流式 run_once 返回后落库（assistant transcript + 文本 + usage）。"""
    persist_transcript_messages(prepared.conversation_id, result.transcript_messages)
    if result.content:
        conv_store.append_message(
            prepared.conversation_id,
            role="assistant",
            content=result.content,
            tokens=(result.usage or {}).get("completion_tokens", 0)
            or history_manager.estimate_tokens(result.content),
        )
    conv_store.auto_title_from_first_user(prepared.conversation_id)

    if result.source.startswith("provider:") and result.usage:
        usage.record(
            model=result.decision.model,
            provider=result.decision.provider,
            layer=result.decision.layer,
            prompt_tokens=result.usage.get("prompt_tokens", 0),
            completion_tokens=result.usage.get("completion_tokens", 0),
        )
