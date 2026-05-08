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
    """拉上游 /models → 注入定价 + created_at → 写 ~/.steelg8/model_catalog.json。

    定价来源（pricing_source 字段标记）：
    - OpenRouter 列表响应里 `pricing.prompt` / `pricing.completion`（USD/token） → verified
    - bailian/kimi/deepseek 走 pricing_scraper 抓官方文档 → verified（best-effort）
    - 其它走 `pricing.py` 静态表 → fallback
    - 都没命中 → input/output 写 None + fallback；UI 显示 "—"

    verified 优先级保护：catalog 里旧记录已是 `pricing_source: verified` 时，刷新不覆盖。

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

    # 主动 seed 已知的 embedding / rerank 模型（bailian 这种 /v1/models 不暴露
    # embedding/rerank 的上游需要这一步，否则 RAG 管理页永远列不出候选）。
    import known_capabilities

    seen_ids = set(new_ids)
    seeded_ids: list[str] = []
    for mid in (
        list(known_capabilities.EMBEDDING_MODELS.get(name, []))
        + list(known_capabilities.RERANK_MODELS.get(name, []))
    ):
        if mid not in seen_ids:
            seen_ids.add(mid)
            seeded_ids.append(mid)
    new_ids.extend(seeded_ids)

    # 1) 上游响应自带 pricing（OpenRouter）→ verified
    # 2) 没自带的 → _resolve_pricing 走 pricing.lookup() 静态表 → 命中 verified / 没命中 fallback
    pricing_by_id: dict[str, tuple[dict[str, float | None], str]] = {}
    created_by_id: dict[str, int | None] = {}
    for rec in records:
        mid = rec.get("id")
        if not isinstance(mid, str):
            continue
        pricing_by_id[mid] = _resolve_pricing(rec, mid, name)
        created = rec.get("created")
        if isinstance(created, (int, float)):
            created_by_id[mid] = int(created)
        else:
            created_by_id[mid] = None

    # seeded ids（known_capabilities 兜底进来的，上游 records 里没有）
    # 也走 _resolve_pricing —— 走的是空 record 路径，最终命中 pricing.lookup 静态表
    for mid in seeded_ids:
        pricing_by_id[mid] = _resolve_pricing({}, mid, name)
        created_by_id[mid] = None

    # 3) 官方文档爬虫 (best-effort)：bailian / kimi / deepseek 等
    try:
        from services import pricing_scraper

        scraped = pricing_scraper.scrape_pricing(name)
    except Exception:  # noqa: BLE001
        scraped = {}
    for mid, scraped_price in (scraped or {}).items():
        if mid in pricing_by_id:
            pricing_by_id[mid] = (scraped_price, "verified")
        elif mid in new_ids:
            pricing_by_id[mid] = (scraped_price, "verified")

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

    for mid, (pr, src) in pricing_by_id.items():
        # 写 pricing；verified 优先级保护：旧已是 verified → 跳过 fallback 写入
        respect = src == "fallback"
        if pr.get("input") is not None or pr.get("output") is not None or src == "fallback":
            model_catalog.record_pricing(
                name, mid, pr, pricing_source=src, respect_verified=respect
            )

    for mid, created in created_by_id.items():
        model_catalog.record_created_at(name, mid, created)

    # capability auto-tag：依据 known_capabilities 表给已知 embedding / rerank
    # 模型自动打标。规则：仅当当前 capabilities == ["chat"] 时打（用户手填过的
    # 不覆盖；用户在「模型管理」页右键 toggle 出非 chat 标签会落入跳过分支）。
    existing_caps = {
        m["id"]: list(m.get("capabilities") or ["chat"])
        for m in model_catalog.all_models(name)
        if isinstance(m.get("id"), str)
    }
    for mid in new_ids:
        inferred = known_capabilities.capabilities_for(name, mid)
        if inferred == ["chat"]:
            continue
        if existing_caps.get(mid, ["chat"]) == ["chat"]:
            model_catalog.set_capabilities(name, mid, inferred)

    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    model_catalog.mark_fetched(name, fetched_at)

    selected_ids = [mid for mid in new_ids if mid not in old_unselected]
    registry.update_models(name, selected_ids)

    # 重读 catalog 拿最新 capabilities（被 set_capabilities 覆写过）
    final_caps = {
        m["id"]: m.get("capabilities") or ["chat"]
        for m in model_catalog.all_models(name)
        if isinstance(m.get("id"), str)
    }

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
                        mid, ({"input": None, "output": None}, "fallback")
                    )[0],
                    "pricing_source": pricing_by_id.get(
                        mid, ({"input": None, "output": None}, "fallback")
                    )[1],
                    "created_at": created_by_id.get(mid),
                    "capabilities": final_caps.get(mid, ["chat"]),
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


def update_catalog_capability(
    name: str, payload: dict[str, Any]
) -> ServiceResponse:
    """模型管理页右键 toggle capability tag。

    body: {"model_id": "...", "capability": "embedding|rerank|chat|...", "enabled": bool}
    """
    import model_catalog

    mid = payload.get("model_id")
    cap = payload.get("capability")
    enabled = payload.get("enabled")
    if not isinstance(mid, str) or not mid.strip():
        raise ServiceError(400, {"error": "model_id is required"})
    if not isinstance(cap, str) or not cap.strip():
        raise ServiceError(400, {"error": "capability is required"})
    if not isinstance(enabled, bool):
        raise ServiceError(400, {"error": "enabled must be bool"})

    ok = model_catalog.toggle_capability(name, mid.strip(), cap.strip(), enabled)
    if not ok:
        raise ServiceError(404, {"error": f"model {mid} not in {name} catalog"})
    return read_catalog(name)


def resolve_model(
    registry: ProviderRegistry, payload: dict[str, Any]
) -> ServiceResponse:
    """模型管理页"实际通过 X 调用"的可视化端点。

    body: {"model": "..."}；返 {provider, model, layer, reason} —— 走和 router 一样的解析。
    """
    import router

    raw = payload.get("model") if isinstance(payload, dict) else None
    if not isinstance(raw, str):
        raw = ""
    # 用 preview 不写 _LAST_DECISION，避免覆盖 RouterPage 的最近命中
    decision = router.preview("", registry, explicit_model=raw or None)
    return ServiceResponse(
        status=200,
        payload={
            "ok": True,
            "provider": decision.provider,
            "model": decision.model,
            "layer": decision.layer,
            "reason": decision.reason,
        },
    )


def update_catalog_pricing(
    name: str, payload: dict[str, Any]
) -> ServiceResponse:
    """模型管理页"双击编辑价格"接口。

    body: {"model_id": "...", "input": float|null, "output": float|null, "reset": bool}
    - reset=True：把 pricing_source 改回 fallback、清空数字（"恢复 fallback"按钮）
    - 否则：写入 pricing_source=verified
    """
    import model_catalog

    mid = payload.get("model_id")
    if not isinstance(mid, str) or not mid.strip():
        raise ServiceError(400, {"error": "model_id is required"})
    mid = mid.strip()

    if payload.get("reset") is True:
        ok = model_catalog.reset_pricing_to_fallback(name, mid)
        if not ok:
            raise ServiceError(404, {"error": f"model {mid} not in catalog"})
        return read_catalog(name)

    pricing_dict = {
        "input": _coerce_optional_float(payload.get("input")),
        "output": _coerce_optional_float(payload.get("output")),
    }
    model_catalog.record_pricing(name, mid, pricing_dict, pricing_source="verified")
    return read_catalog(name)


def update_default_provider(
    registry: ProviderRegistry, payload: dict[str, Any]
) -> ServiceResponse:
    """路由设置页改默认供应商。写 providers.json 的 default_provider 字段。"""
    raw = payload.get("default_provider")
    if not isinstance(raw, str):
        raise ServiceError(400, {"error": "default_provider must be a string"})
    name = raw.strip()
    if name and name not in registry.providers:
        raise ServiceError(400, {"error": f"unknown provider: {name}"})
    _patch_providers_doc({"default_provider": name})
    registry.default_provider = name
    return ServiceResponse(status=200, payload={"ok": True, "default_provider": name})


def update_provider_order(
    registry: ProviderRegistry, payload: dict[str, Any]
) -> ServiceResponse:
    """路由设置页改 fallback 顺序。重排 providers.json providers[] 数组。

    body: {"order": ["bailian", "deepseek", ...]}
    未列出的 provider 追加到末尾，保持原顺序。
    """
    raw_order = payload.get("order")
    if not isinstance(raw_order, list):
        raise ServiceError(400, {"error": "order must be a list"})
    requested: list[str] = []
    seen: set[str] = set()
    for raw in raw_order:
        if not isinstance(raw, str):
            continue
        pid = raw.strip()
        if pid and pid not in seen and pid in registry.providers:
            seen.add(pid)
            requested.append(pid)

    doc = _read_providers_doc()
    providers_list = doc.get("providers")
    if not isinstance(providers_list, list):
        raise ServiceError(500, {"error": "providers.json schema unexpected"})

    by_id: dict[str, dict[str, Any]] = {}
    for entry in providers_list:
        if isinstance(entry, dict):
            pid = str(entry.get("id") or "").strip()
            if pid:
                by_id[pid] = entry

    new_list: list[dict[str, Any]] = []
    for pid in requested:
        if pid in by_id:
            new_list.append(by_id.pop(pid))
    # 未列出的保留原顺序追加
    for entry in providers_list:
        if not isinstance(entry, dict):
            continue
        pid = str(entry.get("id") or "").strip()
        if pid and pid in by_id:
            new_list.append(by_id.pop(pid))

    doc["providers"] = new_list
    _write_providers_doc(doc)

    # registry.providers 是 dict，重建顺序
    new_dict = {}
    for entry in new_list:
        pid = str(entry.get("id") or "").strip()
        if pid in registry.providers:
            new_dict[pid] = registry.providers[pid]
    registry.providers = new_dict
    return ServiceResponse(
        status=200, payload={"ok": True, "order": list(new_dict.keys())}
    )


def router_state() -> ServiceResponse:
    """GET /router/state：返回最近一次路由命中。路由设置页可视化用。"""
    import router

    last = router.last_decision()
    return ServiceResponse(
        status=200,
        payload={"ok": True, "last": last.to_dict() if last else None},
    )


def _coerce_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _read_providers_doc() -> dict[str, Any]:
    import json
    import os
    from pathlib import Path

    path = Path(os.environ.get(
        "STEELG8_PROVIDERS_PATH", Path.home() / ".steelg8" / "providers.json"
    )).expanduser()
    if not path.exists():
        raise ServiceError(404, {"error": "providers.json not found"})
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ServiceError(500, {"error": f"providers.json read failed: {exc}"}) from exc


def _patch_providers_doc(patch: dict[str, Any]) -> None:
    doc = _read_providers_doc()
    doc.update(patch)
    _write_providers_doc(doc)


def _write_providers_doc(doc: dict[str, Any]) -> None:
    import json
    import os
    import tempfile
    from pathlib import Path

    path = Path(os.environ.get(
        "STEELG8_PROVIDERS_PATH", Path.home() / ".steelg8" / "providers.json"
    )).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        try:
            os.chmod(tmp, 0o644)
        except OSError:
            pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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
    record: dict[str, Any], model_id: str, provider_name: str  # noqa: ARG001
) -> tuple[dict[str, float | None], str]:
    """返回 ({input, output}, source)。

    2026-05-08 起按用户偏好：catalog 不再写静态表 fallback 估值。
    - 上游响应自带 pricing → verified（OpenRouter 路径）
    - 其它情况 → (null, "fallback")，UI 显示 "—"
    后续由 pricing_scraper（LiteLLM 等可信源）+ 手填升级到 verified。
    """
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
            return (
                {
                    "input": prompt * 1_000_000 if prompt is not None else None,
                    "output": completion * 1_000_000 if completion is not None else None,
                },
                "verified",
            )

    # 上游 record.pricing 没拿到 → 查静态 pricing 表（embedding/rerank/官网定价）
    # 命中即 verified（来源是官网定价文档），未命中再 fallback null
    import pricing
    static_price = pricing.lookup(model_id, provider_name)
    if static_price is not None:
        return (
            {"input": static_price.input_per_1m, "output": static_price.output_per_1m},
            "verified",
        )

    return ({"input": None, "output": None}, "fallback")


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
