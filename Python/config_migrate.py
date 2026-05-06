"""
~/.steelg8 配置文件三分迁移：旧 providers.json 单文件 → 三份新文件
=================================================================

新 schema：
- providers.json (0644)：纯元数据（id / name / base_url / api_key_env / kind）
- secrets.json   (0600)：api keys（不进 git）
- model_catalog.json (0644)：每个 provider 的模型列表 + 选中态 + 定价

入口：`run_if_needed()` 由 server.py 启动早期调用，失败抛 ConfigMigrationError，
调用方应 fail-closed（不进入 serve_forever）。

幂等：三份新文件齐全且 providers.json 是 v2 → 立即返回；部分缺失 → 只补缺；
旧单文件存在 → 备份 + 拆分重写（atomic）。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


CONFIG_DIR = Path.home() / ".steelg8"
PROVIDERS_PATH = Path(
    os.environ.get("STEELG8_PROVIDERS_PATH", CONFIG_DIR / "providers.json")
).expanduser()
SECRETS_PATH = Path(
    os.environ.get("STEELG8_SECRETS_PATH", CONFIG_DIR / "secrets.json")
).expanduser()
CATALOG_PATH = Path(
    os.environ.get("STEELG8_CATALOG_PATH", CONFIG_DIR / "model_catalog.json")
).expanduser()


# 已知 provider 的中文展示名 / 协议类型；未知 provider 走 capitalize() 兜底
_PROVIDER_DISPLAY: dict[str, tuple[str, str]] = {
    "kimi": ("Kimi", "openai-compatible"),
    "deepseek": ("DeepSeek", "openai-compatible"),
    "bailian": ("百炼", "openai-compatible"),
    "qwen": ("百炼", "openai-compatible"),
    "openrouter": ("OpenRouter", "openai-compatible"),
    "zhipu": ("智谱", "openai-compatible"),
    "doubao": ("豆包", "openai-compatible"),
    "stepfun": ("阶跃", "openai-compatible"),
    "lingyi": ("零一", "openai-compatible"),
    "minimax": ("MiniMax", "openai-compatible"),
    "siliconflow": ("硅基流动", "openai-compatible"),
    "tavily": ("Tavily", "openai-compatible"),
}


class ConfigMigrationError(Exception):
    """迁移过程不可恢复的错误。调用方应 fail-closed。"""


def run_if_needed() -> dict[str, Any]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    state = _detect_state()

    if state["all_present"] and state["providers_is_v2"]:
        return {"action": "noop", "reason": "all three files present and v2"}

    if state["legacy_detected"]:
        result = _migrate_from_legacy()
        return {"action": "migrated", **result}

    created = _ensure_skeleton(state)
    if created:
        return {"action": "skeleton_created", "files": created}

    return {"action": "noop", "reason": "no migration needed"}


# ----- 内部 -----


def _detect_state() -> dict[str, Any]:
    providers_exists = PROVIDERS_PATH.exists()
    secrets_exists = SECRETS_PATH.exists()
    catalog_exists = CATALOG_PATH.exists()

    providers_is_v2 = False
    legacy_detected = False

    if providers_exists:
        try:
            raw = json.loads(PROVIDERS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigMigrationError(
                f"providers.json 损坏，无法读取：{exc}"
            ) from exc

        if isinstance(raw, dict):
            providers_field = raw.get("providers")
            version = raw.get("version")
            if isinstance(providers_field, list) and isinstance(version, int) and version >= 2:
                providers_is_v2 = True
            elif isinstance(providers_field, dict):
                # dict 形式即视为 legacy（无论是否有 inline key）
                legacy_detected = True

    return {
        "providers_exists": providers_exists,
        "secrets_exists": secrets_exists,
        "catalog_exists": catalog_exists,
        "all_present": providers_exists and secrets_exists and catalog_exists,
        "providers_is_v2": providers_is_v2,
        "legacy_detected": legacy_detected,
    }


def _migrate_from_legacy() -> dict[str, Any]:
    try:
        raw = json.loads(PROVIDERS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigMigrationError(f"读取 providers.json 失败：{exc}") from exc

    providers_raw = raw.get("providers") or {}
    if not isinstance(providers_raw, dict):
        raise ConfigMigrationError("providers 字段必须是 object（旧 schema）才可迁移")

    # 沿用旧 providers.py 的清理：把 "qwen" 重命名为 "bailian"（百炼是平台，
    # qwen 是模型家族，原命名混了两层）。仅在 bailian 不存在时执行。
    if "qwen" in providers_raw and "bailian" not in providers_raw:
        providers_raw["bailian"] = providers_raw.pop("qwen")

    default_model = str(raw.get("default_model", "")).strip()

    backup_path = _backup_legacy()

    new_providers: list[dict[str, Any]] = []
    secrets_keys: dict[str, str] = {}
    catalog_providers: dict[str, dict[str, Any]] = {}
    default_provider = ""

    for pid, cfg in providers_raw.items():
        if not isinstance(cfg, dict):
            continue
        base_url = str(cfg.get("base_url", "")).rstrip("/")
        api_key_env = str(cfg.get("api_key_env", ""))
        api_key_inline = str(cfg.get("api_key", "")).strip()
        models = [
            str(m) for m in (cfg.get("models") or [])
            if isinstance(m, str) and m.strip()
        ]

        display, kind = _PROVIDER_DISPLAY.get(
            pid, (pid.capitalize(), "openai-compatible")
        )
        new_providers.append({
            "id": pid,
            "name": display,
            "base_url": base_url,
            "api_key_env": api_key_env,
            "kind": kind,
        })

        if api_key_inline:
            secrets_keys[pid] = api_key_inline

        catalog_providers[pid] = {
            "fetched_at": None,
            "models": [
                {
                    "id": m,
                    "selected": True,
                    "pricing_per_mtoken": {"input": None, "output": None},
                    "source": "manual",
                }
                for m in models
            ],
        }

        if default_model and not default_provider and default_model in models:
            default_provider = pid

    if not default_provider:
        for entry in new_providers:
            pid = entry["id"]
            if catalog_providers.get(pid, {}).get("models"):
                default_provider = pid
                break

    new_providers_doc = {
        "version": 2,
        "default_provider": default_provider,
        "default_model": default_model,
        "providers": new_providers,
    }
    new_secrets_doc = {"version": 1, "keys": secrets_keys}
    new_catalog_doc = {"version": 1, "providers": catalog_providers}

    _atomic_write_json(PROVIDERS_PATH, new_providers_doc, mode=0o644)
    _atomic_write_json(SECRETS_PATH, new_secrets_doc, mode=0o600)
    _atomic_write_json(CATALOG_PATH, new_catalog_doc, mode=0o644)

    return {
        "backup": str(backup_path),
        "providers": str(PROVIDERS_PATH),
        "secrets": str(SECRETS_PATH),
        "catalog": str(CATALOG_PATH),
        "providers_count": len(new_providers),
        "secrets_count": len(secrets_keys),
    }


def _ensure_skeleton(state: dict[str, Any]) -> list[str]:
    """三份文件部分缺失：把缺的那几份补成空骨架；已存在的不动。"""
    created: list[str] = []
    if not state["secrets_exists"]:
        _atomic_write_json(SECRETS_PATH, {"version": 1, "keys": {}}, mode=0o600)
        created.append(str(SECRETS_PATH))
    if not state["catalog_exists"]:
        _atomic_write_json(
            CATALOG_PATH, {"version": 1, "providers": {}}, mode=0o644
        )
        created.append(str(CATALOG_PATH))
    if not state["providers_exists"]:
        _atomic_write_json(
            PROVIDERS_PATH,
            {
                "version": 2,
                "default_provider": "",
                "default_model": "",
                "providers": [],
            },
            mode=0o644,
        )
        created.append(str(PROVIDERS_PATH))
    return created


def _backup_legacy() -> Path:
    ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    backup_path = PROVIDERS_PATH.with_name(f"providers.json.bak.{ts}")
    shutil.copy2(PROVIDERS_PATH, backup_path)
    return backup_path


def _atomic_write_json(path: Path, payload: dict[str, Any], *, mode: int) -> None:
    """tempfile + os.replace，避免半写状态被读到。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        try:
            os.chmod(tmp_path, mode)
        except OSError:
            # NFS / 网盘 chmod 失败：不阻断，记录后继续
            pass
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
