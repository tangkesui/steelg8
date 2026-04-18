"""
Embedding 封装：阿里云百炼（DashScope）Qwen Embedding 系列，走 OpenAI 兼容模式。

默认走 **text-embedding-v3**：不同账号在百炼看到的模型列表不一样，v3 是个人账号
最常见且开通即用的版本（500K token 免费额度，付费 ¥0.7/M）。

企业账号/特定 region 看得到 v4（Qwen3-Embedding，¥0.5/M，2048 dims）的话，改
环境变量：

    STEELG8_EMBED_MODEL=text-embedding-v4 open steelg8.app

维度默认 1024（v3 / v4 都支持），需要 768 / 512 压缩存储时可以：

    STEELG8_EMBED_DIMS=512

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

DEFAULT_MODEL = os.environ.get("STEELG8_EMBED_MODEL", "text-embedding-v3")
DEFAULT_DIMS = int(os.environ.get("STEELG8_EMBED_DIMS", "1024"))
BATCH_SIZE = 10  # DashScope 推荐每批 ≤10 条


class EmbeddingError(RuntimeError):
    pass


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    usage: dict[str, int]  # {total_tokens, ...}
    model: str


def _pick_bailian(registry: ProviderRegistry):
    """从 registry 里找百炼 provider。
    先 'bailian'，再向后兼容老命名 'qwen'。
    """
    for name in ("bailian", "qwen"):
        prov = registry.providers.get(name)
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

    provider = _pick_bailian(registry)
    if provider is None:
        raise EmbeddingError(
            "没有就绪的 bailian provider。项目索引需要阿里云百炼 Embedding（"
            f"{DEFAULT_MODEL}）。去 Settings 给 bailian 填 DashScope API Key，"
            "base_url 默认是 https://dashscope.aliyuncs.com/compatible-mode/v1"
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
