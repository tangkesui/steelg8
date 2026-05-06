from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from providers import Provider, ProviderRegistry, load_registry
from services.common import ServiceError, ServiceResponse


@dataclass
class RegistryReload:
    registry: ProviderRegistry
    payload: dict[str, Any]


def providers_summary(registry: ProviderRegistry) -> dict[str, Any]:
    return {
        "source": registry.source,
        "defaultModel": registry.default_model,
        "providers": registry.readiness_summary(),
    }


def validation_summary(registry: ProviderRegistry) -> dict[str, Any]:
    return registry.validation_summary()


def reload_registry(*, example_candidates: Iterable[Path]) -> RegistryReload:
    new_registry = load_registry(example_candidates=example_candidates)
    if os.environ.get("STEELG8_DEFAULT_MODEL"):
        new_registry.default_model = os.environ["STEELG8_DEFAULT_MODEL"]
    return RegistryReload(
        registry=new_registry,
        payload={
            "ok": True,
            "source": new_registry.source,
            "defaultModel": new_registry.default_model,
            "providers": new_registry.readiness_summary(),
            "readyProviders": [
                p.name for p in new_registry.providers.values() if p.is_ready()
            ],
            "validation": new_registry.validation_summary(),
        },
    )


def sync_models(registry: ProviderRegistry, name: str) -> ServiceResponse:
    prov = _require_ready_provider(registry, name)
    records = _fetch_upstream_records(prov)
    items = [r["id"] for r in records if isinstance(r.get("id"), str)]
    if not items:
        raise ServiceError(500, {"error": "上游返回空模型列表"})

    ok = registry.update_models(name, items)
    return ServiceResponse(
        status=200 if ok else 500,
        payload={
            "ok": ok,
            "name": name,
            "count": len(items),
            "models": items,
        },
    )


def catalog_refresh(registry: ProviderRegistry, name: str) -> ServiceResponse:
    """Phase 12.4：拉上游 /models → 注入定价 → 写 ~/.steelg8/model_catalog.json。

    定价来源：
    - OpenRouter：列表响应里 `pricing.prompt` / `pricing.completion`（USD/token）→ ×1e6 得 per Mtok
    - 其它 provider：`pricing.lookup(model_id, provider_id)` 兜底表
    - 兜底表也命中不到 → input/output 写 None，前端展示"未知"

    保留语义：之前用户手工 unselected 的模型，刷新后仍然 selected=False（即便上游仍含该 id）。
    上游已下架的模型直接从 catalog 移除。
    """
    import model_catalog

    prov = _require_ready_provider(registry, name)
    records = _fetch_upstream_records(prov)
    if not records:
        raise ServiceError(500, {"error": "上游返回空模型列表"})

    new_ids = [r["id"] for r in records if isinstance(r.get("id"), str)]
    if not new_ids:
        raise ServiceError(500, {"error": "上游返回的模型缺少 id 字段"})

    pricing_by_id: dict[str, dict[str, float | None]] = {}
    for rec in records:
        mid = rec.get("id")
        if not isinstance(mid, str):
            continue
        pricing_by_id[mid] = _resolve_pricing(rec, mid, name)

    old_unselected = {
        m["id"]
        for m in model_catalog.all_models(name)
        if m.get("selected") is False and isinstance(m.get("id"), str)
    }

    model_catalog.set_selected_models(name, new_ids, source="upstream")

    if old_unselected:
        doc = model_catalog.load()
        models_list = (
            doc.get("providers", {}).get(name, {}).get("models", [])
        )
        for m in models_list:
            if isinstance(m, dict) and m.get("id") in old_unselected:
                m["selected"] = False
        model_catalog.save(doc)

    for mid, pr in pricing_by_id.items():
        if pr.get("input") is not None or pr.get("output") is not None:
            model_catalog.record_pricing(name, mid, pr)

    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    model_catalog.mark_fetched(name, fetched_at)

    selected_ids = [mid for mid in new_ids if mid not in old_unselected]
    registry.update_models(name, selected_ids)

    return ServiceResponse(
        status=200,
        payload={
            "ok": True,
            "name": name,
            "count": len(new_ids),
            "fetched_at": fetched_at,
            "models": [
                {
                    "id": mid,
                    "selected": mid not in old_unselected,
                    "pricing_per_mtoken": pricing_by_id.get(
                        mid, {"input": None, "output": None}
                    ),
                }
                for mid in new_ids
            ],
        },
    )


def read_catalog(name: str) -> ServiceResponse:
    """读 ~/.steelg8/model_catalog.json 中某 provider 的切片。"""
    import model_catalog

    doc = model_catalog.load()
    prov_doc = (doc.get("providers") or {}).get(name)
    if prov_doc is None:
        raise ServiceError(404, {"error": f"catalog has no entry for {name}"})
    return ServiceResponse(
        status=200,
        payload={
            "ok": True,
            "name": name,
            "fetched_at": prov_doc.get("fetched_at"),
            "models": prov_doc.get("models") or [],
        },
    )


def update_catalog_selection(name: str, payload: dict[str, Any]) -> ServiceResponse:
    """保存某 provider 的 selected 模型列表，并返回最新 catalog 切片。"""
    import model_catalog

    raw_ids = payload.get("model_ids")
    if not isinstance(raw_ids, list):
        raise ServiceError(400, {"error": "model_ids must be a list"})
    model_ids: list[str] = []
    seen: set[str] = set()
    for raw in raw_ids:
        if not isinstance(raw, str):
            continue
        mid = raw.strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        model_ids.append(mid)

    model_catalog.set_selected_models(name, model_ids, source="manual")
    return read_catalog(name)


def wallet_summary(registry: ProviderRegistry) -> dict[str, Any]:
    import wallet as wallet_mod

    return wallet_mod.summary(registry)


def capability_profiles() -> dict[str, Any]:
    import capabilities as caps

    return {
        "profiles": [
            {
                "model": p.model,
                "provider": p.provider,
                "chineseWriting": p.chinese_writing,
                "englishWriting": p.english_writing,
                "reasoning": p.reasoning,
                "contextTokens": p.context_tokens,
                "costTier": p.cost_tier,
                "latencyTier": p.latency_tier,
                "toolUse": p.tool_use,
                "tags": list(p.tags),
            }
            for p in caps.all_profiles()
        ]
    }


def _require_ready_provider(registry: ProviderRegistry, name: str) -> Provider:
    prov = registry.providers.get(name)
    if not prov:
        raise ServiceError(404, {"error": f"provider {name} not found"})
    if not prov.is_ready():
        raise ServiceError(400, {"error": f"provider {name} 未配 API key"})
    return prov


def _fetch_upstream_records(prov: Provider) -> list[dict[str, Any]]:
    """GET {base_url}/models，返回原始 model 记录列表（保留 pricing 等扩展字段）。

    上游异常 → ServiceError(502)。空列表不在这里抛，由调用方决定是否当成错误。
    """
    import network

    url = f"{prov.base_url.rstrip('/')}/models"
    headers = {}
    api_key = prov.api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        body = network.request_json(
            url,
            method="GET",
            headers=headers,
            timeout=15,
            retries=1,
        )
    except network.NetworkError as exc:
        raise ServiceError(502, {"error": f"上游请求失败：{exc}"}) from exc

    if not isinstance(body, dict):
        return []
    raw = body.get("data") or body.get("models") or []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            mid = item.get("id") or item.get("model") or item.get("name")
            if mid:
                rec = dict(item)
                rec["id"] = str(mid)
                out.append(rec)
        elif isinstance(item, str):
            out.append({"id": item})
    return out


def _resolve_pricing(
    record: dict[str, Any], model_id: str, provider_name: str
) -> dict[str, float | None]:
    """优先从上游响应解析 (OpenRouter 列表自带 pricing)；否则查 pricing.py 兜底表。"""
    pr = record.get("pricing")
    if isinstance(pr, dict):
        try:
            prompt_raw = pr.get("prompt")
            completion_raw = pr.get("completion")
            prompt = float(prompt_raw) if prompt_raw is not None else None
            completion = float(completion_raw) if completion_raw is not None else None
        except (TypeError, ValueError):
            prompt = completion = None
        if prompt is not None or completion is not None:
            return {
                "input": prompt * 1_000_000 if prompt is not None else None,
                "output": completion * 1_000_000 if completion is not None else None,
            }

    import pricing

    p = pricing.lookup(model_id, provider_name)
    return {
        "input": p.input_per_1m if p.input_per_1m > 0 else None,
        "output": p.output_per_1m if p.output_per_1m > 0 else None,
    }


def _extract_model_ids(body: Any) -> list[str]:
    """Legacy helper kept for backward compat with tests / 旧调用。"""
    items: list[str] = []
    if not isinstance(body, dict):
        return items
    raw = body.get("data") or body.get("models") or []
    for item in raw:
        if isinstance(item, dict):
            model_id = item.get("id") or item.get("model") or item.get("name")
            if model_id:
                items.append(str(model_id))
        elif isinstance(item, str):
            items.append(item)
    return items
