"""
steelg8 Provider Registry (stdlib-only)
---------------------------------------

12.1 起，配置三分（providers.json / secrets.json / model_catalog.json），
本模块负责把这三份合成 ProviderRegistry。

加载优先级（从高到低）：
  1. ~/.steelg8/providers.json (v2 schema：array form)
       + ~/.steelg8/secrets.json (api keys)
       + ~/.steelg8/model_catalog.json (selected models + pricing)
  2. 旧 STEELG8_OPENAI_* env vars（向后兼容快速起步）
  3. config/providers.example.json（兜底展示）
       + 同目录下 model_catalog.example.json（如有）

Provider 字段：
  - name: 路由 key（即原 id，如 "kimi"）
  - display_name: UI 展示用（如 "Kimi"）
  - kind: 协议类型，目前都是 "openai-compatible"
  - models: 来自 model_catalog.json 中 selected=true 的模型 id

api_key 解析顺序：
  1. secrets.json["keys"][provider_id]
  2. os.environ[api_key_env]
  3. providers.json 内 api_key_inline （**已弃用**，保留一个 cycle 兜底，
     并写一行 logger.warn；下个版本（12.2）将停止读取）
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


_LOG = logging.getLogger("steelg8.providers")
_INLINE_KEY_WARNED: set[str] = set()  # 仅每个 provider 第一次警告，避免日志被刷


DEFAULT_CONFIG_DIR = Path.home() / ".steelg8"
USER_PROVIDERS_PATH = Path(
    os.environ.get("STEELG8_PROVIDERS_PATH", DEFAULT_CONFIG_DIR / "providers.json")
).expanduser()
USER_SECRETS_PATH = Path(
    os.environ.get("STEELG8_SECRETS_PATH", DEFAULT_CONFIG_DIR / "secrets.json")
).expanduser()


@dataclass
class Provider:
    name: str          # 路由 / lookup key（旧字段名沿用，等价于新 schema 的 id）
    base_url: str
    display_name: str = ""
    api_key_env: str = ""
    api_key_inline: str = ""    # 弃用：来自 providers.json 中的 api_key 字段
    api_key_secret: str = ""    # 来自 secrets.json
    kind: str = "openai-compatible"
    models: list[str] = field(default_factory=list)

    def api_key(self) -> str:
        if self.api_key_secret:
            return self.api_key_secret.strip()
        if self.api_key_env:
            v = os.environ.get(self.api_key_env, "").strip()
            if v:
                return v
        if self.api_key_inline:
            if self.name not in _INLINE_KEY_WARNED:
                _INLINE_KEY_WARNED.add(self.name)
                _LOG.warning(
                    "providers.json 中 provider=%s 仍含 api_key_inline；"
                    "请迁移到 ~/.steelg8/secrets.json（12.2 将移除该兜底）。",
                    self.name,
                )
            return self.api_key_inline.strip()
        return ""

    def api_key_source(self) -> str:
        """给 /providers 端点用，说明 key 的来源（不泄露 key 本身）。"""
        if self.api_key_secret:
            return "secrets"
        if self.api_key_env and os.environ.get(self.api_key_env, ""):
            return f"env:{self.api_key_env}"
        if self.api_key_inline:
            return "inline-deprecated"
        return "missing"

    def is_ready(self) -> bool:
        if self.kind == "local-runtime":
            return bool(self.base_url)
        return bool(self.base_url) and bool(self.api_key())

    def owns_model(self, model: str) -> bool:
        if not model:
            return False
        if model in self.models:
            return True
        return model.startswith(f"{self.name}/") or model.startswith(f"{self.name}-")


@dataclass
class ProviderRegistry:
    providers: dict[str, Provider] = field(default_factory=dict)
    default_model: str = ""
    default_provider: str = ""
    source: str = "empty"

    def resolve(self, model: str | None) -> tuple[Provider, str] | None:
        target = (model or self.default_model or "").strip()
        if not target:
            return None

        if "/" in target:
            provider_name, _, remainder = target.partition("/")
            provider = self.providers.get(provider_name)
            if provider and provider.is_ready():
                return provider, remainder or ""

        if self.default_provider:
            provider = self.providers.get(self.default_provider)
            if provider and provider.owns_model(target) and provider.is_ready():
                return provider, target

        for provider in self.providers.values():
            if provider.owns_model(target) and provider.is_ready():
                canonical = target
                if target.startswith(f"{provider.name}/"):
                    canonical = target.split("/", 1)[1]
                return provider, canonical

        return None

    def first_ready(self) -> tuple[Provider, str] | None:
        for provider in self.providers.values():
            if provider.is_ready():
                fallback_model = provider.models[0] if provider.models else ""
                return provider, fallback_model or self.default_model
        return None

    def update_models(self, name: str, models: list[str]) -> bool:
        """更新 provider 的 selected models；同步落到 model_catalog.json。"""
        prov = self.providers.get(name)
        if not prov:
            return False
        prov.models = [str(m) for m in models if m]
        try:
            import model_catalog
            model_catalog.set_selected_models(name, prov.models, source="upstream")
        except OSError:
            return False
        return True

    def readiness_summary(self) -> list[dict[str, Any]]:
        try:
            import model_catalog
            pricing_lookup: dict[str, dict[str, dict[str, Any]]] = {}
            for provider in self.providers.values():
                pricing_lookup[provider.name] = model_catalog.model_pricing(provider.name)
        except Exception:  # noqa: BLE001
            pricing_lookup = {}

        return [
            {
                "name": provider.name,
                "displayName": provider.display_name or provider.name,
                "kind": provider.kind,
                "baseUrl": provider.base_url,
                "ready": provider.is_ready(),
                "apiKeyEnv": provider.api_key_env,
                "apiKeySource": provider.api_key_source(),
                "models": list(provider.models),
                "selected_models": list(provider.models),
                "pricing": pricing_lookup.get(provider.name, {}),
            }
            for provider in self.providers.values()
        ]

    def validation_summary(self) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        model_to_providers: dict[str, list[str]] = {}
        ready_providers: list[str] = []

        for provider in self.providers.values():
            prefix = f"providers.{provider.name}"
            parsed = urlparse(provider.base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                issues.append({
                    "level": "error",
                    "path": f"{prefix}.base_url",
                    "message": "base_url 必须是 http(s) URL",
                })
            if provider.base_url != provider.base_url.strip():
                issues.append({
                    "level": "warning",
                    "path": f"{prefix}.base_url",
                    "message": "base_url 前后有空白，建议清理",
                })
            if (
                not provider.api_key_secret
                and not provider.api_key_inline
                and not provider.api_key_env
            ):
                issues.append({
                    "level": "warning",
                    "path": f"{prefix}.api_key",
                    "message": "未配置 api_key 或 api_key_env，该 provider 不会就绪",
                })
            elif not provider.is_ready():
                issues.append({
                    "level": "warning",
                    "path": f"{prefix}.api_key_env",
                    "message": "secrets.json / 环境变量中无 key，当前内核看不到 key",
                })
            else:
                ready_providers.append(provider.name)

            seen_local: set[str] = set()
            for model in provider.models:
                model = model.strip()
                if not model:
                    continue
                if model in seen_local:
                    issues.append({
                        "level": "warning",
                        "path": f"{prefix}.models",
                        "message": f"模型重复：{model}",
                    })
                seen_local.add(model)
                model_to_providers.setdefault(model, []).append(provider.name)

        for model, owners in sorted(model_to_providers.items()):
            if len(owners) > 1:
                issues.append({
                    "level": "warning",
                    "path": "providers.*.models",
                    "message": f"模型 {model} 同时出现在 {', '.join(owners)}；自动解析会按 provider 顺序命中第一个",
                })

        if self.default_model:
            if _is_utility_model(self.default_model):
                issues.append({
                    "level": "error",
                    "path": "default_model",
                    "message": f"default_model={self.default_model} 是工具模型，不能用于对话",
                })
            resolved = self.resolve(self.default_model)
            if resolved is None:
                in_catalog = self.default_model in model_to_providers
                issues.append({
                    "level": "error" if not in_catalog else "warning",
                    "path": "default_model",
                    "message": (
                        f"default_model={self.default_model} 当前不可解析"
                        if in_catalog
                        else f"default_model={self.default_model} 不在任何 provider.models 中"
                    ),
                })

        try:
            import capabilities
            import pricing

            known_capability_models = {p.model for p in capabilities.all_profiles()}
            known_pricing_models = set(pricing.PRICING.keys())
            configured_models = {
                m for m in model_to_providers.keys()
                if not _is_utility_model(m)
            }
            capability_missing = sorted(configured_models - known_capability_models)
            pricing_missing = sorted(
                m for m in configured_models
                if m not in known_pricing_models and not m.endswith(":free")
            )
            for model in capability_missing[:20]:
                issues.append({
                    "level": "info",
                    "path": "capabilities",
                    "message": f"{model} 没有能力画像，将使用默认上下文预算",
                })
            for model in pricing_missing[:20]:
                issues.append({
                    "level": "info",
                    "path": "pricing",
                    "message": f"{model} 没有精确定价，将使用 provider 兜底价",
                })
        except Exception:  # noqa: BLE001
            pass

        return {
            "ok": not any(i["level"] == "error" for i in issues),
            "source": self.source,
            "defaultModel": self.default_model,
            "defaultProvider": self.default_provider,
            "readyProviders": ready_providers,
            "providerCount": len(self.providers),
            "readyProviderCount": len(ready_providers),
            "modelCount": len(model_to_providers),
            "issues": issues,
        }


# ---------- 加载逻辑 ----------


def _load_v2_doc(providers_path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(providers_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if not isinstance(raw.get("providers"), list):
        return None
    return raw


def _load_legacy_doc(providers_path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(providers_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if not isinstance(raw.get("providers"), dict):
        return None
    return raw


def _load_secrets(secrets_path: Path) -> dict[str, str]:
    if not secrets_path.exists():
        return {}
    try:
        raw = json.loads(secrets_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    keys = raw.get("keys")
    if not isinstance(keys, dict):
        return {}
    return {
        str(k): str(v).strip()
        for k, v in keys.items()
        if isinstance(v, str) and v.strip()
    }


def _build_registry_from_v2(
    doc: dict[str, Any],
    secrets: dict[str, str],
    catalog_doc: dict[str, Any],
    source: str,
) -> ProviderRegistry:
    catalog_providers = (catalog_doc or {}).get("providers") or {}
    if not isinstance(catalog_providers, dict):
        catalog_providers = {}

    providers: dict[str, Provider] = {}
    for entry in doc.get("providers") or []:
        if not isinstance(entry, dict):
            continue
        pid = str(entry.get("id") or "").strip()
        if not pid:
            continue
        base_url = str(entry.get("base_url", "")).rstrip("/")
        if not base_url:
            continue

        models: list[str] = []
        prov_cat = catalog_providers.get(pid) or {}
        for m in prov_cat.get("models") or []:
            if not isinstance(m, dict):
                continue
            if m.get("selected") is False:
                continue
            mid = m.get("id")
            if isinstance(mid, str) and mid:
                models.append(mid)

        providers[pid] = Provider(
            name=pid,
            display_name=str(entry.get("name") or pid),
            base_url=base_url,
            api_key_env=str(entry.get("api_key_env", "")),
            api_key_inline=str(entry.get("api_key", "") or "").strip(),
            api_key_secret=secrets.get(pid, ""),
            kind=str(entry.get("kind") or "openai-compatible"),
            models=models,
        )

    return ProviderRegistry(
        providers=providers,
        default_model=str(doc.get("default_model", "")),
        default_provider=str(doc.get("default_provider", "")),
        source=source,
    )


def _build_registry_from_legacy(
    doc: dict[str, Any], source: str
) -> ProviderRegistry:
    """旧 dict 形式 fallback。仅在迁移未运行 / example 仍是旧格式时走。"""
    providers: dict[str, Provider] = {}
    for name, cfg in (doc.get("providers") or {}).items():
        if not isinstance(cfg, dict):
            continue
        base_url = str(cfg.get("base_url", "")).rstrip("/")
        if not base_url:
            continue
        providers[name] = Provider(
            name=name,
            display_name=name,
            base_url=base_url,
            api_key_env=str(cfg.get("api_key_env", "")),
            api_key_inline=str(cfg.get("api_key", "") or "").strip(),
            kind="openai-compatible",
            models=[
                str(m) for m in (cfg.get("models") or [])
                if isinstance(m, str) and m
            ],
        )
    return ProviderRegistry(
        providers=providers,
        default_model=str(doc.get("default_model", "")),
        source=source,
    )


def _is_utility_model(model: str) -> bool:
    model = (model or "").lower()
    return "embedding" in model or "rerank" in model


def _load_from_legacy_env() -> ProviderRegistry:
    """向后兼容：用旧的 STEELG8_OPENAI_* 环境变量构造一个单 provider。"""
    base_url = os.environ.get("STEELG8_OPENAI_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("STEELG8_OPENAI_API_KEY", "")
    model = os.environ.get("STEELG8_OPENAI_MODEL", "")
    if not (base_url and api_key):
        return ProviderRegistry(source="legacy-empty")

    os.environ["STEELG8_LEGACY_API_KEY"] = api_key
    provider = Provider(
        name="legacy",
        display_name="legacy",
        base_url=base_url,
        api_key_env="STEELG8_LEGACY_API_KEY",
        models=[model] if model else [],
    )
    return ProviderRegistry(
        providers={"legacy": provider},
        default_model=model,
        source="legacy-env",
    )


def load_registry(example_candidates: Iterable[Path] = ()) -> ProviderRegistry:
    if USER_PROVIDERS_PATH.exists():
        v2 = _load_v2_doc(USER_PROVIDERS_PATH)
        if v2 is not None:
            import model_catalog
            secrets = _load_secrets(USER_SECRETS_PATH)
            return _build_registry_from_v2(
                v2, secrets, model_catalog.load(),
                source=str(USER_PROVIDERS_PATH),
            )
        legacy = _load_legacy_doc(USER_PROVIDERS_PATH)
        if legacy is not None:
            return _build_registry_from_legacy(legacy, source=str(USER_PROVIDERS_PATH))
        print(
            json.dumps(
                {"event": "provider_config_bad", "path": str(USER_PROVIDERS_PATH)},
                ensure_ascii=False,
            )
        )

    legacy_env = _load_from_legacy_env()
    if legacy_env.providers:
        return legacy_env

    for candidate in example_candidates:
        if not candidate.exists():
            continue
        v2 = _load_v2_doc(candidate)
        if v2 is not None:
            catalog_example = candidate.parent / "model_catalog.example.json"
            catalog_doc: dict[str, Any] = {}
            if catalog_example.exists():
                try:
                    catalog_doc = json.loads(catalog_example.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass
            return _build_registry_from_v2(
                v2, secrets={}, catalog_doc=catalog_doc,
                source=str(candidate),
            )
        legacy = _load_legacy_doc(candidate)
        if legacy is not None:
            return _build_registry_from_legacy(legacy, source=str(candidate))

    return legacy_env  # empty
