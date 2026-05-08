"""
已知稳定可用的 embedding / rerank 模型 id 表。

catalog refresh 时按 (provider id, model id) 命中这两张表，自动给
catalog 条目打 capability tag（chat / embedding / rerank）。
命不中的模型默认只标 ['chat']。

维护方式：随用户实际跑的本地模型补；非自动同步。
RAG 管理页右键模型行 toggle capability 是另一条手填路径。
"""
from __future__ import annotations


# OpenAI-compatible embedding 接口可调通的模型清单。
# 主流 / 稳定 / 用户最常碰到的一批；其余靠手动 toggle。
EMBEDDING_MODELS: dict[str, list[str]] = {
    "bailian": [
        "text-embedding-v3",
        "text-embedding-v4",
    ],
    "ollama": [
        "nomic-embed-text",
        "mxbai-embed-large",
        "bge-m3",
        "snowflake-arctic-embed",
    ],
    "lmstudio": [
        "nomic-embed-text-v1.5",
        "bge-m3",
    ],
    "openai": [
        "text-embedding-3-small",
        "text-embedding-3-large",
        "text-embedding-ada-002",
    ],
    # OpenRouter / kimi / deepseek 不主动 host embedding，本期空——用户自填。
}


# Rerank 接口可调通的模型清单。
# 业内 rerank API 标准没成型，多数本地 runtime 还没 expose；先列已知最稳的。
RERANK_MODELS: dict[str, list[str]] = {
    "bailian": [
        "qwen3-rerank",
        "gte-rerank",
        "gte-rerank-v2",
    ],
    # 其它本期空，按用户实际部署再加。
}


def capabilities_for(provider_id: str, model_id: str) -> list[str]:
    """根据 provider/model 推断 capabilities。
    catalog 写入时调用，命不中默认 ['chat']。
    """
    caps = ["chat"]
    if model_id in (EMBEDDING_MODELS.get(provider_id) or []):
        caps.append("embedding")
        # 纯 embedding 模型不该走 chat 路径——但 catalog 不能凭空判定，
        # 保留 chat 标签供 picker 全列；实际 chat 调用失败时上游会报错。
    if model_id in (RERANK_MODELS.get(provider_id) or []):
        caps.append("rerank")
    return caps
