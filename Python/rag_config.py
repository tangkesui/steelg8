"""
~/.steelg8/rag.json 数据访问层（stdlib-only）。

文件 schema (v1, 2026-05-08)：
{
  "version": 1,
  "embedding": {
    "provider": "bailian",
    "model": "text-embedding-v3",
    "dimensions": 1024,
    "endpoint_kind": "openai-compat"
  },
  "rerank": {
    "provider": "bailian",
    "model": "qwen3-rerank",
    "endpoint_kind": "dashscope-native",
    "endpoint_url_override": null
  },
  "strategy": {"id": "default", "params": {}},
  "backend":  {"id": "sqlite-brute-force", "params": {}}
}

任何字段缺失 / 类型不对 / 文件不存在都走兜底；不抛异常。
保存路径见 `path()`；env 变量覆盖见 `_apply_env_overrides()`。

向后兼容：保留 `STEELG8_EMBED_MODEL` / `STEELG8_RERANK_MODEL` /
`STEELG8_EMBED_DIMS` / `STEELG8_RAG_BACKEND` 等老 env 变量，启动期套到 config 上。
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---- 默认配置 ----

DEFAULT_EMBEDDING = {
    "provider": "bailian",
    "model": "text-embedding-v3",
    "dimensions": 1024,
    "endpoint_kind": "openai-compat",
}

DEFAULT_RERANK = {
    "provider": "bailian",
    "model": "qwen3-rerank",
    "endpoint_kind": "dashscope-native",
    "endpoint_url_override": None,
}

DEFAULT_STRATEGY = {"id": "default", "params": {}}
DEFAULT_BACKEND = {"id": "sqlite-brute-force", "params": {}}

VALID_ENDPOINT_KINDS = ("openai-compat", "dashscope-native")
# embedding 当前 embedding.py 只走 OpenAI 兼容 /embeddings 端点，dashscope-native
# 没真实现；先收口避免用户配了等于装饰。哪天真需要再开 plan。
VALID_EMBEDDING_ENDPOINT_KINDS = ("openai-compat",)
VALID_STRATEGY_IDS = ("default",)
VALID_BACKEND_IDS = ("sqlite-brute-force",)


def path() -> Path:
    return Path(
        os.environ.get(
            "STEELG8_RAG_CONFIG_PATH",
            Path.home() / ".steelg8" / "rag.json",
        )
    ).expanduser()


@dataclass
class EmbeddingConfig:
    provider: str = "bailian"
    model: str = "text-embedding-v3"
    dimensions: int = 1024
    endpoint_kind: str = "openai-compat"

    def fingerprint(self) -> str:
        return f"{self.provider}|{self.model}|{self.dimensions}|{self.endpoint_kind}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "dimensions": int(self.dimensions),
            "endpoint_kind": self.endpoint_kind,
        }


@dataclass
class RerankConfig:
    provider: str = "bailian"
    model: str = "qwen3-rerank"
    endpoint_kind: str = "dashscope-native"
    endpoint_url_override: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "endpoint_kind": self.endpoint_kind,
            "endpoint_url_override": self.endpoint_url_override,
        }


@dataclass
class StrategyConfig:
    id: str = "default"
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "params": dict(self.params or {})}


@dataclass
class BackendConfig:
    id: str = "sqlite-brute-force"
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "params": dict(self.params or {})}


@dataclass
class RagConfig:
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    rerank: RerankConfig = field(default_factory=RerankConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    backend: BackendConfig = field(default_factory=BackendConfig)
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "embedding": self.embedding.to_dict(),
            "rerank": self.rerank.to_dict(),
            "strategy": self.strategy.to_dict(),
            "backend": self.backend.to_dict(),
        }

    def embedding_fingerprint(self) -> str:
        return self.embedding.fingerprint()


# ---- 单例缓存（hot-reload 用） ----
_cached: RagConfig | None = None


def reload() -> RagConfig:
    """强制丢弃缓存重读。PUT /rag/config 后调一次。"""
    global _cached
    _cached = None
    return current()


def current() -> RagConfig:
    global _cached
    if _cached is None:
        _cached = _read_or_default()
    return _cached


def save(cfg: RagConfig) -> None:
    """写盘 + 失效缓存。"""
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        try:
            os.chmod(tmp, 0o644)
        except OSError:
            pass
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    global _cached
    _cached = cfg


# ---- 内部工具 ----


def _read_or_default() -> RagConfig:
    p = path()
    raw: dict[str, Any] = {}
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raw = {}
        except (OSError, json.JSONDecodeError):
            raw = {}

    cfg = RagConfig(
        embedding=_parse_embedding(raw.get("embedding")),
        rerank=_parse_rerank(raw.get("rerank")),
        strategy=_parse_strategy(raw.get("strategy")),
        backend=_parse_backend(raw.get("backend")),
        version=int(raw.get("version", 1) or 1),
    )
    _apply_env_overrides(cfg)

    # 缺文件就生成一份默认（不破坏既有：仅初次启动）
    if not p.exists():
        try:
            save(cfg)
        except OSError:
            # 写盘失败不阻塞读，下次启动再尝试
            pass
    return cfg


def _parse_embedding(raw: Any) -> EmbeddingConfig:
    raw = raw if isinstance(raw, dict) else {}
    e = EmbeddingConfig()
    if isinstance(raw.get("provider"), str) and raw["provider"].strip():
        e.provider = raw["provider"].strip()
    if isinstance(raw.get("model"), str) and raw["model"].strip():
        e.model = raw["model"].strip()
    dims = raw.get("dimensions")
    if isinstance(dims, (int, float)) and 0 < int(dims) <= 16384:
        e.dimensions = int(dims)
    kind = raw.get("endpoint_kind")
    if isinstance(kind, str) and kind in VALID_EMBEDDING_ENDPOINT_KINDS:
        e.endpoint_kind = kind
    # 非法值（含 dashscope-native）静默回退到默认 openai-compat —— EmbeddingConfig
    # 默认值就是 openai-compat，这里不动 e.endpoint_kind 即可。
    return e


def _parse_rerank(raw: Any) -> RerankConfig:
    raw = raw if isinstance(raw, dict) else {}
    r = RerankConfig()
    if isinstance(raw.get("provider"), str) and raw["provider"].strip():
        r.provider = raw["provider"].strip()
    if isinstance(raw.get("model"), str) and raw["model"].strip():
        r.model = raw["model"].strip()
    kind = raw.get("endpoint_kind")
    if isinstance(kind, str) and kind in VALID_ENDPOINT_KINDS:
        r.endpoint_kind = kind
    override = raw.get("endpoint_url_override")
    if isinstance(override, str) and override.strip():
        r.endpoint_url_override = override.strip()
    elif override is None:
        r.endpoint_url_override = None
    return r


def _parse_strategy(raw: Any) -> StrategyConfig:
    raw = raw if isinstance(raw, dict) else {}
    s = StrategyConfig()
    sid = raw.get("id")
    if isinstance(sid, str) and sid.strip():
        s.id = sid.strip()
    params = raw.get("params")
    if isinstance(params, dict):
        s.params = dict(params)
    return s


def _parse_backend(raw: Any) -> BackendConfig:
    raw = raw if isinstance(raw, dict) else {}
    b = BackendConfig()
    bid = raw.get("id")
    if isinstance(bid, str) and bid.strip():
        b.id = bid.strip()
    params = raw.get("params")
    if isinstance(params, dict):
        b.params = dict(params)
    return b


def _apply_env_overrides(cfg: RagConfig) -> None:
    """老 env 变量向后兼容。设置了 env → 覆盖 cfg。"""
    if v := os.environ.get("STEELG8_EMBED_MODEL", "").strip():
        cfg.embedding.model = v
    if v := os.environ.get("STEELG8_EMBED_DIMS", "").strip():
        try:
            d = int(v)
            if 0 < d <= 16384:
                cfg.embedding.dimensions = d
        except ValueError:
            pass
    if v := os.environ.get("STEELG8_RERANK_MODEL", "").strip():
        cfg.rerank.model = v
    if v := os.environ.get("STEELG8_RAG_BACKEND", "").strip():
        cfg.backend.id = v
