"""
~/.steelg8/model_catalog.json 数据访问层（stdlib-only）。

文件 schema：
{
  "version": 1,
  "providers": {
    "<provider_id>": {
      "fetched_at": null | "ISO 8601",
      "models": [
        {
          "id": "<model_id>",
          "selected": bool,
          "pricing_per_mtoken": {"input": float|null, "output": float|null},
          "source": "manual" | "upstream"
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
    return {"version": 1, "providers": {}}


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
        if isinstance(m, dict) and isinstance(m.get("id"), str):
            out.append(dict(m))
    return out


def model_pricing(provider_id: str) -> dict[str, dict[str, Any]]:
    """{model_id: {input, output}}；没数据返回空 dict。"""
    out: dict[str, dict[str, Any]] = {}
    for m in all_models(provider_id):
        pr = m.get("pricing_per_mtoken") or {}
        if not isinstance(pr, dict):
            continue
        out[m["id"]] = {
            "input": pr.get("input") if isinstance(pr.get("input"), (int, float)) else None,
            "output": pr.get("output") if isinstance(pr.get("output"), (int, float)) else None,
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
    provider_id: str, model_id: str, pricing: dict[str, Any]
) -> bool:
    doc = load()
    providers = doc.setdefault("providers", {})
    prov = providers.setdefault(provider_id, {"fetched_at": None, "models": []})
    models = prov.setdefault("models", [])
    if not isinstance(models, list):
        models = []
        prov["models"] = models
    for m in models:
        if isinstance(m, dict) and m.get("id") == model_id:
            m["pricing_per_mtoken"] = {
                "input": pricing.get("input"),
                "output": pricing.get("output"),
            }
            save(doc)
            return True
    models.append({
        "id": model_id,
        "selected": True,
        "pricing_per_mtoken": {
            "input": pricing.get("input"),
            "output": pricing.get("output"),
        },
        "source": "manual",
    })
    save(doc)
    return True


def mark_fetched(provider_id: str, when: str) -> None:
    doc = load()
    providers = doc.setdefault("providers", {})
    prov = providers.setdefault(provider_id, {"fetched_at": None, "models": []})
    prov["fetched_at"] = when
    save(doc)
