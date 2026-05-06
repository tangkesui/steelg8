"""
steelg8 · 历史压缩 / 预算调度
-----------------------------

职责：
1. `estimate_tokens(text)`：不依赖 tokenizer，按字符启发式估计 token。
2. `model_budget(model)`：查 capabilities 拿模型 context 窗口。
3. `maybe_compress(conv_id, registry, model, ...)`：检查是否超 60%，是就把
   最早一批 active 消息压进 summary，调 qwen-turbo 生成摘要并写回 DB。

压缩策略：
- 触发线：active 消息 tokens + system 预算 > budget * 0.6
- 保留最后 `KEEP_TAIL` 轮对话不压缩（默认 4 轮 = 8 条消息），防止 tool 链断裂
- 其余最早消息和旧 summary 一起喂给 qwen-turbo 生成新 summary
- 被压缩的消息 `compressed=1`，前端仍可看，但 LLM payload 不再带上

qwen-turbo 直接走 registry 里的 bailian provider（OpenAI 兼容）。
如果 bailian 没配 / 失败，降级为"截断保留头尾 + 机械摘要"，不阻塞主流程。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import capabilities
import conversations as conv_store
import network
import preferences


# ---------- 预算 ----------

# 默认触发压缩线，占模型窗口比例。用户可在 Settings 里覆盖。
COMPRESSION_TRIGGER_RATIO = 0.60

# 系统预留：soul + user.md + project.md + tools schema + 回复 buffer
# 约 8K token，保守估计
SYSTEM_RESERVED_TOKENS = 8_000

# 压缩时保留尾部 N 条消息（最近的对话不碰，保证工具链上下文完整）
KEEP_TAIL_MESSAGES = 8

# 压缩摘要字数预算（给 qwen-turbo 的 max_tokens）
SUMMARY_MAX_TOKENS = 2_000

# 未知模型的 fallback context 窗口
DEFAULT_CONTEXT_TOKENS = 32_000


# ---------- token 估算 ----------

_CJK_RE = re.compile(
    r"[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff"
    r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]"
)


def estimate_tokens(text: str | None) -> int:
    """粗糙但稳健的 token 估算。

    规则：
      - CJK 字符：1 字 ≈ 1 token
      - 其他字符：约 4 字符 ≈ 1 token

    这比调用 tokenizer 省事，且对我们场景（中英混合文案）误差在 ±10% 以内，
    对触发压缩完全够用。
    """
    if not text:
        return 0
    s = str(text)
    cjk = len(_CJK_RE.findall(s))
    rest = len(s) - cjk
    return cjk + max(1, rest // 4)


def estimate_message_tokens(msg: dict[str, Any]) -> int:
    """估算单条 OpenAI message 的 tokens（含 role / tool_calls 的结构开销）。"""
    base = 4  # role overhead
    content = msg.get("content") or ""
    base += estimate_tokens(content)
    tool_calls = msg.get("tool_calls") or []
    for tc in tool_calls:
        fn = tc.get("function") or {}
        base += estimate_tokens(fn.get("name") or "")
        base += estimate_tokens(fn.get("arguments") or "")
        base += 8
    if msg.get("name"):
        base += estimate_tokens(msg["name"]) + 2
    return base


# ---------- 模型预算 ----------

@dataclass
class BudgetPlan:
    model: str
    context_tokens: int          # 模型最大上下文
    reserved: int                # 系统 + 回复预留
    budget_for_history: int      # 留给对话历史的 token 预算
    trigger_tokens: int          # 超过这个数量就触发压缩


def model_budget(
    model: str,
    *,
    system_prompt_tokens: int = 0,
    response_reserve: int = 4_000,
) -> BudgetPlan:
    profile = capabilities.get(model or "")
    ctx = profile.context_tokens if profile else DEFAULT_CONTEXT_TOKENS
    reserved = system_prompt_tokens + response_reserve + SYSTEM_RESERVED_TOKENS
    budget_for_history = max(2_000, ctx - reserved)
    trigger = int(budget_for_history * compression_trigger_ratio())
    return BudgetPlan(
        model=model or "",
        context_tokens=ctx,
        reserved=reserved,
        budget_for_history=budget_for_history,
        trigger_tokens=trigger,
    )


def compression_trigger_ratio() -> float:
    """User-configurable compression threshold, clamped to a safe range."""
    raw = preferences.get("compression_trigger_ratio")
    try:
        ratio = float(raw)
    except (TypeError, ValueError):
        ratio = COMPRESSION_TRIGGER_RATIO
    if ratio > 1:
        ratio = ratio / 100.0
    return min(0.90, max(0.50, ratio))


# ---------- 压缩实现 ----------

_SUMMARY_SYSTEM_PROMPT = (
    "你是 steelg8 的上下文压缩器。任务：把这段对话历史压成一份简明"
    "「会话纪要」，供后续轮次续接使用。\n\n"
    "硬要求：\n"
    "1. 保留关键事实、决策、用户偏好、文件 / 路径 / 参数等具体信息\n"
    "2. 保留未完成的待办、悬而未决的问题\n"
    "3. 删除寒暄、重复、已被后续覆盖的早期方案\n"
    "4. 用 markdown 列表输出，不超过 ~600 字\n"
    "5. 不要写『我将…』『我建议…』这种继续对话的话术——只做纪要"
)


def _format_messages_for_summary(
    msgs: list[dict[str, Any]],
    prev_summary: str,
) -> str:
    parts: list[str] = []
    if prev_summary.strip():
        parts.append("## 已有纪要（前序）\n\n" + prev_summary.strip())
    parts.append("## 新增对话片段\n")
    for m in msgs:
        role = m.get("role", "?")
        content = (m.get("content") or "").strip()
        if m.get("tool_calls"):
            tc_desc = ", ".join(
                (tc.get("function") or {}).get("name", "?") for tc in m["tool_calls"]
            )
            content = (content + f"\n[调用工具: {tc_desc}]").strip()
        if role == "tool":
            # tool 返回只保留前 300 字，避免摘要爆炸
            content = content[:300] + ("…" if len(content) > 300 else "")
        if not content:
            continue
        parts.append(f"### {role}\n{content}")
    return "\n\n".join(parts)


def _call_qwen_turbo(
    system_prompt: str,
    user_content: str,
    registry: Any,
    *,
    timeout: int = 45,
) -> str | None:
    """调用 bailian provider 的 qwen-turbo 做摘要。失败返回 None。"""
    bailian = registry.providers.get("bailian") if registry else None
    if not bailian or not bailian.is_ready():
        return None

    payload = {
        "model": "qwen-turbo",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
        "max_tokens": SUMMARY_MAX_TOKENS,
        "stream": False,
    }
    try:
        body = network.request_json(
            f"{bailian.base_url}/chat/completions",
            method="POST",
            payload=payload,
            headers={"Authorization": f"Bearer {bailian.api_key()}"},
            timeout=timeout,
            retries=1,
        )
    except network.NetworkError:
        return None
    if not isinstance(body, dict):
        return None
    try:
        text = body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return None
    return text.strip() or None


def _fallback_mechanical_summary(
    msgs: list[dict[str, Any]],
    prev_summary: str,
) -> str:
    """qwen-turbo 不可用时的降级策略：机械截断 + 旧摘要拼接。"""
    parts: list[str] = []
    if prev_summary.strip():
        parts.append("（前序纪要）")
        parts.append(prev_summary.strip())
        parts.append("")
    parts.append("（以下为本段机械摘录，qwen-turbo 不可用）")
    for m in msgs[:8]:
        role = m.get("role", "?")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        snippet = content[:200] + ("…" if len(content) > 200 else "")
        parts.append(f"- [{role}] {snippet}")
    if len(msgs) > 8:
        parts.append(f"…（省略 {len(msgs) - 8} 条）")
    return "\n".join(parts)


# ---------- 对外 API ----------

@dataclass
class CompressionResult:
    compressed: bool
    compressed_count: int = 0
    new_summary_tokens: int = 0
    reason: str = ""


def maybe_compress(
    conv_id: int,
    registry: Any,
    *,
    model: str,
    system_prompt_tokens: int,
) -> CompressionResult:
    """核心入口：看情况压缩，返回是否真的压了。"""
    conv = conv_store.get_conversation(conv_id)
    if not conv:
        return CompressionResult(compressed=False, reason="conversation not found")

    active_msgs = conv_store.list_messages(conv_id, only_active=True)
    if len(active_msgs) <= KEEP_TAIL_MESSAGES + 2:
        return CompressionResult(compressed=False, reason="too few messages")

    # 计 tokens：已压缩的 summary + 当前 active 消息
    openai_msgs = [m.to_openai() for m in active_msgs]
    active_tokens = sum(estimate_message_tokens(m) for m in openai_msgs)
    summary_tokens = conv.summary_tokens or estimate_tokens(conv.summary)

    plan = model_budget(model, system_prompt_tokens=system_prompt_tokens + summary_tokens)
    total_history = active_tokens + summary_tokens

    if total_history < plan.trigger_tokens:
        percent = int(compression_trigger_ratio() * 100)
        return CompressionResult(
            compressed=False,
            reason=f"{total_history}/{plan.trigger_tokens} 未达 {percent}% 阈值",
        )

    # 要压：保留尾部 KEEP_TAIL_MESSAGES，但不能把 assistant tool_calls
    # 和紧随其后的 role=tool 结果切开。OpenAI-compatible API 对这段
    # 顺序很敏感，tail 不能以孤儿 tool 消息开头。
    keep_start = _safe_compression_keep_start(active_msgs, KEEP_TAIL_MESSAGES)
    to_compress = active_msgs[:keep_start]
    if not to_compress:
        return CompressionResult(compressed=False, reason="nothing safe to compress")

    to_compress_openai = [m.to_openai() for m in to_compress]
    summary_input = _format_messages_for_summary(to_compress_openai, conv.summary)

    new_summary = _call_qwen_turbo(_SUMMARY_SYSTEM_PROMPT, summary_input, registry)
    if not new_summary:
        new_summary = _fallback_mechanical_summary(to_compress_openai, conv.summary)

    new_summary_tokens = estimate_tokens(new_summary)
    conv_store.update_summary(conv_id, summary=new_summary, summary_tokens=new_summary_tokens)
    conv_store.mark_messages_compressed(conv_id, [m.id for m in to_compress])

    return CompressionResult(
        compressed=True,
        compressed_count=len(to_compress),
        new_summary_tokens=new_summary_tokens,
        reason=f"{total_history} tokens → 压缩 {len(to_compress)} 条",
    )


def build_history_for_llm(conv_id: int) -> list[dict[str, Any]]:
    """给 agent.py 用：从 DB 取出当前会话"未压缩"的消息，转 OpenAI 格式。"""
    active = conv_store.list_messages(conv_id, only_active=True)
    return _sanitize_openai_history([m.to_openai() for m in active])


def summary_block(conv_id: int) -> str:
    """返回当前会话的 summary，用于拼在 system prompt 末尾。空则返回 ""。"""
    conv = conv_store.get_conversation(conv_id)
    if not conv or not conv.summary.strip():
        return ""
    return (
        "## L4 · 历史会话纪要（已压缩早期对话，概述如下）\n\n"
        + conv.summary.strip()
    )


def _safe_compression_keep_start(
    msgs: list[conv_store.StoredMessage],
    keep_tail_messages: int,
) -> int:
    """Return a compression boundary that keeps tool-call groups intact."""
    if not msgs:
        return 0
    keep_start = max(0, len(msgs) - keep_tail_messages)
    # 如果 tail 从 tool result 开始，把对应的 assistant tool_calls 也留在 tail。
    while keep_start > 0 and msgs[keep_start].role == "tool":
        keep_start -= 1
    return keep_start


def _sanitize_openai_history(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop malformed tool fragments before sending history back to providers.

    Historical DB rows can be incomplete after app crashes, aborted streams, or
    older builds. Rather than let one orphan `role=tool` poison the whole next
    request, keep only complete assistant tool_calls → tool result groups.
    """
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(msgs):
        msg = msgs[i]
        role = msg.get("role")

        if role == "tool":
            # Orphan tool results are not valid OpenAI history.
            i += 1
            continue

        tool_calls = msg.get("tool_calls") or []
        if role == "assistant" and tool_calls:
            expected = [
                tc.get("id")
                for tc in tool_calls
                if isinstance(tc, dict) and tc.get("id")
            ]
            j = i + 1
            tool_by_id: dict[str, dict[str, Any]] = {}
            while j < len(msgs) and msgs[j].get("role") == "tool":
                tcid = msgs[j].get("tool_call_id")
                if isinstance(tcid, str) and tcid not in tool_by_id:
                    tool_by_id[tcid] = msgs[j]
                j += 1
            if expected and all(tcid in tool_by_id for tcid in expected):
                out.append(msg)
                out.extend(tool_by_id[tcid] for tcid in expected)
            elif (msg.get("content") or "").strip():
                # 保留有文字内容的 assistant，但移除不完整 tool_calls。
                downgraded = dict(msg)
                downgraded.pop("tool_calls", None)
                out.append(downgraded)
            i = j
            continue

        out.append(msg)
        i += 1

    return out
