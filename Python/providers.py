"""
steelg8 Provider Registry (stdlib-only)
---------------------------------------

一个极简的多 provider 注册表，为 Phase 0 "LiteLLM 网关" 的最小实现：
不引入 litellm 依赖，直接在 Python stdlib 上封装 OpenAI-compatible HTTP 调用。

配置来源（按优先级从高到低）：
  1. 文件：~/.steelg8/providers.json （或 STEELG8_PROVIDERS_PATH 指向的路径）
  2. 默认模板：Python/../config/providers.example.json
  3. 旧 ENV 兼容：STEELG8_OPENAI_BASE_URL / _API_KEY / _MODEL

配置文件示例：
{
  "default_model": "deepseek-chat",
  "providers": {
    "kimi": {
      "base_url": "https://api.moonshot.cn/v1",
      "api_key_env": "KIMI_API_KEY",
      "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k", "kimi-k2"]
    },
    "deepseek": {
      "base_url": "https://api.deepseek.com",
      "api_key_env": "DEEPSEEK_API_KEY",
      "models": ["deepseek-chat", "deepseek-reasoner"]
    }
  }
}

设计原则：
- 模型名就是路由 key。如 model="kimi-k2" → 找到 provider "kimi"。
- 不存储明文 api_key，只存环境变量名 `api_key_env`，运行时解析。
- 找不到对应 provider 时优雅降级到旧 ENV 单 provider 路径。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


DEFAULT_CONFIG_DIR = Path.home() / ".steelg8"
USER_PROVIDERS_PATH = Path(
    os.environ.get("STEELG8_PROVIDERS_PATH", DEFAULT_CONFIG_DIR / "providers.json")
).expanduser()


@dataclass
class Provider:
    name: str
    base_url: str
    api_key_env: str
    models: list[str] = field(default_factory=list)

    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "").strip()

    def is_ready(self) -> bool:
        return bool(self.base_url) and bool(self.api_key())

    def owns_model(self, model: str) -> bool:
        if not model:
            return False
        if model in self.models:
            return True
        # 允许 provider 前缀匹配（如 "kimi/moonshot-v1-8k"）
        return model.startswith(f"{self.name}/") or model.startswith(f"{self.name}-")


@dataclass
class ProviderRegistry:
    providers: dict[str, Provider] = field(default_factory=dict)
    default_model: str = ""
    source: str = "empty"

    def resolve(self, model: str | None) -> tuple[Provider, str] | None:
        """按模型名找 provider；返回 (provider, 实际模型名)。未命中返回 None。"""
        target = (model or self.default_model or "").strip()
        if not target:
            return None

        # 支持 "kimi/moonshot-v1-8k" 这种显式前缀写法
        if "/" in target:
            provider_name, _, remainder = target.partition("/")
            provider = self.providers.get(provider_name)
            if provider and provider.is_ready():
                return provider, remainder or ""

        for provider in self.providers.values():
            if provider.owns_model(target) and provider.is_ready():
                # 去掉 provider 前缀再传给上游
                canonical = target
                if target.startswith(f"{provider.name}/"):
                    canonical = target.split("/", 1)[1]
                return provider, canonical

        return None

    def first_ready(self) -> tuple[Provider, str] | None:
        """找第一个配置齐全的 provider（用作兜底）。"""
        for provider in self.providers.values():
            if provider.is_ready():
                fallback_model = provider.models[0] if provider.models else ""
                return provider, fallback_model or self.default_model
        return None

    def readiness_summary(self) -> list[dict[str, Any]]:
        return [
            {
                "name": provider.name,
                "baseUrl": provider.base_url,
                "ready": provider.is_ready(),
                "apiKeyEnv": provider.api_key_env,
                "models": list(provider.models),
            }
            for provider in self.providers.values()
        ]


def _load_providers_from_json(path: Path) -> ProviderRegistry:
    raw = json.loads(path.read_text(encoding="utf-8"))
    providers_raw = raw.get("providers", {})
    providers: dict[str, Provider] = {}
    for name, cfg in providers_raw.items():
        base_url = str(cfg.get("base_url", "")).rstrip("/")
        if not base_url:
            continue
        providers[name] = Provider(
            name=name,
            base_url=base_url,
            api_key_env=str(cfg.get("api_key_env", "")),
            models=[str(m) for m in cfg.get("models", [])],
        )
    return ProviderRegistry(
        providers=providers,
        default_model=str(raw.get("default_model", "")),
        source=str(path),
    )


def _load_from_legacy_env() -> ProviderRegistry:
    """向后兼容：用旧的 STEELG8_OPENAI_* 环境变量构造一个单 provider。"""
    base_url = os.environ.get("STEELG8_OPENAI_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("STEELG8_OPENAI_API_KEY", "")
    model = os.environ.get("STEELG8_OPENAI_MODEL", "")
    if not (base_url and api_key):
        return ProviderRegistry(source="legacy-empty")

    # 创建一个合成的 provider，沿用旧的 env var 名做 api_key 来源
    os.environ["STEELG8_LEGACY_API_KEY"] = api_key
    provider = Provider(
        name="legacy",
        base_url=base_url,
        api_key_env="STEELG8_LEGACY_API_KEY",
        models=[model] if model else [],
    )
    return ProviderRegistry(
        providers={"legacy": provider},
        default_model=model,
        source="legacy-env",
    )


def load_registry(
    example_candidates: Iterable[Path] = (),
) -> ProviderRegistry:
    """按优先级查找 providers 配置。

    顺序：
      1. ~/.steelg8/providers.json（用户真实配置，显式意图最强）
      2. 旧 STEELG8_OPENAI_* env（向后兼容，quick-start 用）
      3. example_candidates 传入的默认模板路径（兜底展示空注册表）
    """
    if USER_PROVIDERS_PATH.exists():
        try:
            return _load_providers_from_json(USER_PROVIDERS_PATH)
        except (OSError, json.JSONDecodeError) as exc:
            # 坏掉的配置文件不应阻塞启动；记录错误但继续降级
            print(
                json.dumps(
                    {
                        "event": "provider_config_bad",
                        "path": str(USER_PROVIDERS_PATH),
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                )
            )

    legacy = _load_from_legacy_env()
    if legacy.providers:
        return legacy

    for candidate in example_candidates:
        if candidate.exists():
            try:
                return _load_providers_from_json(candidate)
            except (OSError, json.JSONDecodeError):
                continue

    return legacy  # empty registry
