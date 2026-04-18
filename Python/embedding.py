"""
Embedding 封装：当前只支持阿里云百炼（DashScope）Qwen Embedding 系列，
通过 OpenAI 兼容模式调用。

默认走 **text-embedding-v4**（Qwen3-Embedding 系列）：
- 最大 dimensions 2048，这里默认 1024（精度够 + 存储砍半）
- 批大小 10（每次 input ≤10 条）
- 单条最长 8192 token
- 价格 ¥0.5/M token（比 v3 便宜 30%）
- 过渡期 v3 仅限免费额度；新项目直接用 v4

可通过环境变量 `STEELG8_EMBED_MODEL` 覆盖（v4 / v3 / v2）。

返回：list[list[float]]，每条对应 input 中一条文本的 embedding 向量。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib import request, error

from providers import ProviderRegistry


# ---- 默认配置 ----

DEFAULT_MODEL = os.environ.get("STEELG8_EMBED_MODEL", "text-embedding-v4")
DEFAULT_DIMS = int(os.environ.get("STEELG8_EMBED_DIMS", "1024"))
BATCH_SIZE = 10  # DashScope v4 推荐每批 ≤10 条


class EmbeddingError(RuntimeError):
    pass


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    usage: dict[str, int]  # {total_tokens, ...}
    model: str


def _pick_qwen(registry: ProviderRegistry):
    """从 registry 里找 qwen provider。"""
    prov = registry.providers.get("qwen")
    if prov and prov.is_ready():
        return prov
    return None


def embed(
    texts: list[str],
    registry: ProviderRegistry,
    *,
    model: str = DEFAULT_MODEL,
    dimensions: int = DEFAULT_DIMS,
    timeout: int = 60,
) -> EmbeddingResult:
    """批量 embedding。如果 Qwen 没配就抛 EmbeddingError。"""
    if not texts:
        return EmbeddingResult(vectors=[], usage={}, model=model)

    provider = _pick_qwen(registry)
    if provider is None:
        raise EmbeddingError(
            "没有就绪的 qwen provider。Phase 2 项目索引需要 Qwen Embedding（"
            f"{DEFAULT_MODEL}）。去 Settings 给 qwen 填 DashScope API Key，"
            "base_url 默认填好是 https://dashscope.aliyuncs.com/compatible-mode/v1"
        )

    all_vectors: list[list[float]] = []
    total_tokens = 0
    url = f"{provider.base_url}/embeddings"

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        payload: dict[str, Any] = {
            "model": model,
            "input": batch,
            "dimensions": dimensions,
            "encoding_format": "float",
        }
        req = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {provider.api_key()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400] if exc.fp else ""
            raise EmbeddingError(f"HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise EmbeddingError(f"网络错误：{exc}") from exc

        data = body.get("data") or []
        if len(data) != len(batch):
            raise EmbeddingError(
                f"embedding 返回条数不匹配：请求 {len(batch)}，返回 {len(data)}"
            )
        # 保证顺序和 input 一一对应（API 会返回 index 字段）
        data_sorted = sorted(data, key=lambda x: x.get("index", 0))
        for item in data_sorted:
            vec = item.get("embedding") or []
            if not vec:
                raise EmbeddingError("返回了空向量")
            all_vectors.append([float(x) for x in vec])

        u = body.get("usage") or {}
        total_tokens += int(u.get("total_tokens") or 0)

    return EmbeddingResult(
        vectors=all_vectors,
        usage={"total_tokens": total_tokens},
        model=model,
    )


def embed_one(text: str, registry: ProviderRegistry, **kwargs: Any) -> list[float]:
    """便捷：单条 embedding。"""
    res = embed([text], registry, **kwargs)
    if not res.vectors:
        raise EmbeddingError("embed_one 返回空")
    return res.vectors[0]
