"""
steelg8 · Prompt Cache 标记
--------------------------

按 provider 决定怎么"让上游缓存前缀"：

| provider    | 机制                                    | 实现点                      |
|-------------|----------------------------------------|----------------------------|
| anthropic   | system content block 挂 cache_control  | 改 payload.system 结构      |
| openrouter  | 同 Anthropic（对 Claude 透传 cache_control）| 改 payload.messages system |
| kimi        | 显式 tag-based cache                    | 加 HTTP header             |
| bailian     | 隐式前缀缓存，上游自动识别                | 无操作                      |
| deepseek    | 隐式前缀缓存，上游自动识别                | 无操作                      |
| openai      | 隐式前缀缓存，上游自动识别                | 无操作                      |
| 其它        | 无操作                                  |                            |

Anthropic 的 cache_control 是挂在 content block 上的 marker：标记之前
的内容（system + 更早 messages）会被缓存。规则：
  - 最小缓存单元 1024 tokens
  - TTL 5 分钟；5 分钟内再次命中续命
  - 命中折扣约 10% 原价

Kimi 的 tag-based cache：发请求时加 header，上游按 tag 维护前缀缓存。
命中即自动折扣，tag 相同就共享缓存。TTL 默认 60 分钟。
"""

from __future__ import annotations

from typing import Any


def _is_anthropic_like(provider_name: str, model: str) -> bool:
    """provider 是 Anthropic 原生，或 openrouter 转发到 Claude。"""
    p = (provider_name or "").lower()
    m = (model or "").lower()
    if p == "anthropic":
        return True
    if p == "openrouter" and m.startswith("anthropic/"):
        return True
    return False


def _is_kimi(provider_name: str) -> bool:
    p = (provider_name or "").lower()
    return p in ("kimi", "moonshot")


def build_system_payload(
    system_text: str,
    *,
    provider_name: str,
    model: str,
) -> Any:
    """
    把 system prompt 按 provider 要求打好缓存标记。

    返回：
      - Anthropic-like: [{"type": "text", "text": ..., "cache_control": {...}}]
        （注意：OpenAI chat.completions 格式里 system message 的 content
         也允许是数组；Anthropic 原生接口用 system 字段。我们走 OpenAI
         兼容端点，system 就作为 messages[0]["content"]，这里返回数组，
         调用方据此构造 messages[0]）
      - 其它: 返回字符串原文
    """
    if not system_text:
        return ""

    if _is_anthropic_like(provider_name, model):
        return [
            {
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    return system_text


def extra_headers(
    provider_name: str,
    *,
    conversation_id: int | None,
    model: str = "",
) -> dict[str, str]:
    """按 provider 返回要附加到 /chat/completions 请求的额外 header。

    Kimi 走 tag-based cache：相同 tag 的请求共享前缀缓存。我们按 conversation_id
    建 tag，确保同一会话命中同一 cache；跨会话也能部分命中（前缀相同的话）。
    """
    headers: dict[str, str] = {}

    if _is_kimi(provider_name) and conversation_id is not None:
        headers["X-Msh-Context-Cache-Tag"] = f"steelg8_conv_{int(conversation_id)}"
        headers["X-Msh-Context-Cache-Reset-TTL"] = "3600"  # 1 小时

    return headers


def needs_content_block_system(provider_name: str, model: str) -> bool:
    """调用方用这个判断要不要把 system content 构造成数组。"""
    return _is_anthropic_like(provider_name, model)
