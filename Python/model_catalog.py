"""
~/.steelg8/model_catalog.json 数据访问层（stdlib-only）。

文件 schema（v2，2026-05-08 新增 created_at + pricing_source）：
{
  "version": 2,
  "providers": {
    "<provider_id>": {
      "fetched_at": null | "ISO 8601",
      "models": [
        {
          "id": "<model_id>",
          "selected": bool,
          "pricing_per_mtoken": {"input": float|null, "output": float|null},
          "pricing_source": "verified" | "fallback",
            // verified = 上游 API 直接返回 / 用户手填 / 爬虫从官方文档拿到
            // fallback = pricing.py 静态表猜的（或没猜到为 null）
          "created_at": int|null,   // UNIX ts，OpenAI /v1/models 标准字段
          "source": "manual" | "upstream"  // 模型条目本身从何而来（不是定价的）
        }
      ]
    }
  }
}

任何字段缺失 / 类型不对 / 文件不存在都走兜底；不抛异常。
仅 save() 在 OSError 时往上抛。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(
    os.environ.get("STEELG8_CATALOG_PATH", Path.home() / ".steelg8" / "model_catalog.json")
).expanduser()


def _empty_doc() -> dict[str, Any]:
    return {"version": 2, "providers": {}}


_VALID_CAPABILITIES = ("chat", "embedding", "rerank", "vision", "tool-use")


def _normalize_model_entry(entry: Any) -> dict[str, Any] | None:
    """补全旧 catalog 缺的字段（向后兼容）。读时按需调用。"""
    if not isinstance(entry, dict):
        return None
    mid = entry.get("id")
    if not isinstance(mid, str) or not mid:
        return None
    out: dict[str, Any] = dict(entry)
    out["id"] = mid
    if not isinstance(out.get("selected"), bool):
        out["selected"] = True
    pr = out.get("pricing_per_mtoken")
    if not isinstance(pr, dict):
        pr = {"input": None, "output": None}
        out["pricing_per_mtoken"] = pr
    else:
        if not isinstance(pr.get("input"), (int, float)):
            pr["input"] = None
        if not isinstance(pr.get("output"), (int, float)):
            pr["output"] = None
    src = out.get("pricing_source")
    if src not in ("verified", "fallback"):
        out["pricing_source"] = "fallback"
    created = out.get("created_at")
    if not isinstance(created, (int, float)):
        out["created_at"] = None
    if out.get("source") not in ("manual", "upstream"):
        out["source"] = "upstream"
    caps = out.get("capabilities")
    if not isinstance(caps, list):
        out["capabilities"] = ["chat"]
    else:
        out["capabilities"] = [c for c in caps if isinstance(c, str) and c in _VALID_CAPABILITIES]
        if not out["capabilities"]:
            out["capabilities"] = ["chat"]
    return out


def load() -> dict[str, Any]:
    if not CATALOG_PATH.exists():
        return _empty_doc()
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_doc()
    if not isinstance(raw, dict):
        return _empty_doc()
    raw.setdefault("version", 1)
    providers = raw.get("providers")
    if not isinstance(providers, dict):
        raw["providers"] = {}
    return raw


def save(doc: dict[str, Any]) -> None:
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(
        prefix=CATALOG_PATH.name + ".", suffix=".tmp", dir=str(CATALOG_PATH.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        try:
            os.chmod(tmp, 0o644)
        except OSError:
            pass
        os.replace(tmp, CATALOG_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def selected_models(provider_id: str) -> list[str]:
    """返回某 provider 中 selected != False 的 model id 列表。"""
    doc = load()
    prov = (doc.get("providers") or {}).get(provider_id) or {}
    out: list[str] = []
    for m in prov.get("models") or []:
        if not isinstance(m, dict):
            continue
        if m.get("selected") is False:
            continue
        mid = m.get("id")
        if isinstance(mid, str) and mid:
            out.append(mid)
    return out


def all_models(provider_id: str) -> list[dict[str, Any]]:
    doc = load()
    prov = (doc.get("providers") or {}).get(provider_id) or {}
    out: list[dict[str, Any]] = []
    for m in prov.get("models") or []:
        normalized = _normalize_model_entry(m)
        if normalized is not None:
            out.append(normalized)
    return out


def all_model_ids(provider_id: str) -> list[str]:
    """全量模型 id（不论 selected）。给路由 fallback 池用。"""
    return [m["id"] for m in all_models(provider_id)]


def model_pricing(provider_id: str) -> dict[str, dict[str, Any]]:
    """{model_id: {input, output, source}}；没数据返回空 dict。"""
    out: dict[str, dict[str, Any]] = {}
    for m in all_models(provider_id):
        pr = m.get("pricing_per_mtoken") or {}
        out[m["id"]] = {
            "input": pr.get("input") if isinstance(pr.get("input"), (int, float)) else None,
            "output": pr.get("output") if isinstance(pr.get("output"), (int, float)) else None,
            "source": m.get("pricing_source", "fallback"),
        }
    return out


def set_selected_models(
    provider_id: str, model_ids: list[str], *, source: str = "manual"
) -> None:
    """全量替换某 provider 的 selected models 列表。
    保留已存在 model 的 pricing；新模型写 null pricing。

    source="upstream" 表示刷新 catalog 的模型全集：下架模型应被移除。
    source="manual" 表示用户勾选的子集：未勾选模型仍保留在 catalog 中。
    """
    doc = load()
    providers = doc.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        doc["providers"] = providers
    prov = providers.setdefault(provider_id, {"fetched_at": None, "models": []})
    if not isinstance(prov, dict):
        prov = {"fetched_at": None, "models": []}
        providers[provider_id] = prov

    old_models: list[dict[str, Any]] = []
    old_by_id: dict[str, dict[str, Any]] = {}
    for m in prov.get("models") or []:
        if isinstance(m, dict) and isinstance(m.get("id"), str):
            old_models.append(m)
            old_by_id[m["id"]] = m

    selected_ids = {
        mid for mid in model_ids
        if isinstance(mid, str) and mid
    }
    keep_unselected_catalog_entries = source != "upstream"

    new_models: list[dict[str, Any]] = []
    if keep_unselected_catalog_entries:
        for existing in old_models:
            entry = dict(existing)
            entry["selected"] = entry["id"] in selected_ids
            entry.setdefault("pricing_per_mtoken", {"input": None, "output": None})
            entry.setdefault("source", source)
            new_models.append(entry)

    emitted = {
        m["id"] for m in new_models
        if isinstance(m, dict) and isinstance(m.get("id"), str)
    }
    for mid in model_ids:
        if not isinstance(mid, str) or not mid or mid in emitted:
            continue
        existing = old_by_id.get(mid)
        if existing:
            entry = dict(existing)
            entry["selected"] = True
            entry.setdefault("pricing_per_mtoken", {"input": None, "output": None})
            entry.setdefault("source", source)
        else:
            entry = {
                "id": mid,
                "selected": True,
                "pricing_per_mtoken": {"input": None, "output": None},
                "source": source,
            }
        new_models.append(entry)
        emitted.add(mid)

    prov["models"] = new_models
    save(doc)


def record_pricing(
    provider_id: str,
    model_id: str,
    pricing: dict[str, Any],
    *,
    pricing_source: str = "fallback",
    respect_verified: bool = False,
) -> bool:
    """写某 model 的定价。

    Args:
        pricing_source: 这次写入的来源标记，"verified" / "fallback"
        respect_verified: True 时如果旧记录已是 verified，跳过本次写入（保护手填 / 爬虫值不被 fallback 覆盖）
    """
    if pricing_source not in ("verified", "fallback"):
        pricing_source = "fallback"
    doc = load()
    providers = doc.setdefault("providers", {})
    prov = providers.setdefault(provider_id, {"fetched_at": None, "models": []})
    models = prov.setdefault("models", [])
    if not isinstance(models, list):
        models = []
        prov["models"] = models
    for m in models:
        if isinstance(m, dict) and m.get("id") == model_id:
            if respect_verified and m.get("pricing_source") == "verified":
                return False
            m["pricing_per_mtoken"] = {
                "input": pricing.get("input"),
                "output": pricing.get("output"),
            }
            m["pricing_source"] = pricing_source
            save(doc)
            return True
    models.append({
        "id": model_id,
        "selected": True,
        "pricing_per_mtoken": {
            "input": pricing.get("input"),
            "output": pricing.get("output"),
        },
        "pricing_source": pricing_source,
        "created_at": None,
        "source": "manual",
    })
    save(doc)
    return True


def reset_pricing_to_fallback(provider_id: str, model_id: str) -> bool:
    """把某 model 的 pricing_source 改回 fallback 并清空数字。
    模型管理页"恢复 fallback"按钮的后端实现。"""
    doc = load()
    prov = (doc.get("providers") or {}).get(provider_id) or {}
    models = prov.get("models") or []
    for m in models:
        if isinstance(m, dict) and m.get("id") == model_id:
            m["pricing_per_mtoken"] = {"input": None, "output": None}
            m["pricing_source"] = "fallback"
            save(doc)
            return True
    return False


def record_created_at(provider_id: str, model_id: str, created_at: int | None) -> bool:
    """补 created_at 字段（catalog refresh 时用）。"""
    doc = load()
    prov = (doc.get("providers") or {}).get(provider_id) or {}
    models = prov.get("models") or []
    for m in models:
        if isinstance(m, dict) and m.get("id") == model_id:
            m["created_at"] = (
                int(created_at) if isinstance(created_at, (int, float)) else None
            )
            save(doc)
            return True
    return False


def set_capabilities(
    provider_id: str, model_id: str, capabilities: list[str]
) -> bool:
    """覆盖某 model 的 capabilities 标签（catalog refresh 写入 / 用户手动 toggle 都用）。"""
    cleaned = [c for c in capabilities if isinstance(c, str) and c in _VALID_CAPABILITIES]
    if not cleaned:
        cleaned = ["chat"]
    # 去重保留顺序
    seen: set[str] = set()
    unique: list[str] = []
    for c in cleaned:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    doc = load()
    prov = (doc.get("providers") or {}).get(provider_id) or {}
    models = prov.get("models") or []
    for m in models:
        if isinstance(m, dict) and m.get("id") == model_id:
            m["capabilities"] = unique
            save(doc)
            return True
    return False


def toggle_capability(
    provider_id: str, model_id: str, capability: str, enabled: bool
) -> bool:
    """启用 / 关闭某 model 的单个 capability tag。"""
    if capability not in _VALID_CAPABILITIES:
        return False
    doc = load()
    prov = (doc.get("providers") or {}).get(provider_id) or {}
    models = prov.get("models") or []
    for m in models:
        if isinstance(m, dict) and m.get("id") == model_id:
            caps = m.get("capabilities")
            if not isinstance(caps, list):
                caps = ["chat"]
            caps = [c for c in caps if isinstance(c, str)]
            if enabled:
                if capability not in caps:
                    caps.append(capability)
            else:
                caps = [c for c in caps if c != capability]
                if not caps:
                    caps = ["chat"]
            m["capabilities"] = caps
            save(doc)
            return True
    return False


def mark_fetched(provider_id: str, when: str) -> None:
    doc = load()
    providers = doc.setdefault("providers", {})
    prov = providers.setdefault(provider_id, {"fetched_at": None, "models": []})
    prov["fetched_at"] = when
    save(doc)
