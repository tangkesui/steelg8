"""
RagStrategy 最小边界（2026-05-08）。

只迁移现行 default pipeline `embed → coarse topK → rerank → top_n`，不引入
新策略、不暴露新 UI；为未来 TreeRAG / GraphRAG / Hybrid 留接口。

设计：
- `RagStrategy` protocol：`retrieve(query, registry, *, top_k, ...) -> list[Hit]`
- `register_strategy(name, factory)` + `default_strategy()`：参考 `rag_store.register_backend`
- 当前唯一注册的 strategy 是 `DefaultRagStrategy`（包装 `project.retrieve`）
- `default_strategy()` 按 `rag_config.current().strategy.id` 选；找不到回退到 'default'
"""
from __future__ import annotations

from typing import Any, Callable, Protocol


class RagStrategy(Protocol):
    """所有策略实现必须满足。"""

    def retrieve(
        self,
        query: str,
        registry: Any,
        *,
        top_k: int = 5,
        **kwargs: Any,
    ) -> list[Any]: ...


_STRATEGIES: dict[str, Callable[[], RagStrategy]] = {}


def register_strategy(name: str, factory: Callable[[], RagStrategy]) -> None:
    """注册一个策略工厂。
    示例：
        rag_strategy.register_strategy('tree', make_tree_strategy)
        # 然后在 ~/.steelg8/rag.json 改 strategy.id='tree'
    """
    if not name or not callable(factory):
        return
    _STRATEGIES[name] = factory


def list_strategies() -> list[str]:
    return sorted(_STRATEGIES.keys())


def default_strategy() -> RagStrategy:
    """按 rag_config.strategy.id 选；缺失回退 'default'。"""
    try:
        import rag_config
        sid = rag_config.current().strategy.id or "default"
    except Exception:  # noqa: BLE001
        sid = "default"
    factory = _STRATEGIES.get(sid) or _STRATEGIES.get("default")
    if factory is None:
        raise RuntimeError("RAG strategy 'default' 未注册——project.py 启动期应该 register")
    return factory()


# ---- 内置 default 策略 ----


class DefaultRagStrategy:
    """包装现行 `project.retrieve` 的瀑布检索：embed → topK → rerank → top_n。
    本期仅做边界——未来策略不动这块。
    """

    def retrieve(
        self,
        query: str,
        registry: Any,
        *,
        top_k: int = 5,
        **kwargs: Any,
    ) -> list[Any]:
        # 延迟导入避免循环
        import project
        return project.retrieve(query, registry, top_k=top_k, **kwargs)


def _bootstrap_default() -> None:
    register_strategy("default", lambda: DefaultRagStrategy())


_bootstrap_default()
