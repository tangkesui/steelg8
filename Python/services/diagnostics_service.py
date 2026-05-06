from __future__ import annotations

import importlib.util
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import embedding
import extract
import logger
import project as project_mod
import rag_store
import vectordb
from providers import ProviderRegistry


@dataclass(frozen=True)
class DiagnosticContext:
    app_root: Path
    port: int
    auth_required: bool


def doctor(registry: ProviderRegistry, context: DiagnosticContext) -> dict[str, Any]:
    checks = [
        _kernel_check(context),
        _provider_check(registry),
        _embedding_check(registry),
        _dependency_check(),
        _rag_store_check(),
        _active_project_check(),
        _logs_check(),
    ]
    issues = _issues_from_checks(checks)
    level = _overall_level(checks)
    return {
        "ok": level != "error",
        "level": level,
        "checks": checks,
        "issues": issues,
        "context": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "appRoot": str(context.app_root),
            "port": context.port,
        },
    }


def index_inspector(*, sample_limit: int = 20) -> dict[str, Any]:
    active = project_mod.get_active()
    status = project_mod.status()
    if not active:
        return {
            "ok": False,
            "level": "warn",
            "activeProject": None,
            "indexStatus": status,
            "message": "没有激活项目",
            "manifest": {
                "count": 0,
                "totalChunks": 0,
                "staleFiles": [],
                "missingManifestFiles": [],
                "items": [],
            },
        }

    project_row = vectordb.get_project(active["path"])
    if project_row is None:
        return {
            "ok": False,
            "level": "error",
            "activeProject": active,
            "indexStatus": status,
            "message": "active_project.json 指向的项目不在向量库中",
            "manifest": {
                "count": 0,
                "totalChunks": 0,
                "staleFiles": [],
                "missingManifestFiles": [],
                "items": [],
            },
        }

    store = rag_store.default_store()
    manifest = store.list_manifest(project_row.id)
    current_files = _current_file_map(project_row.path)
    stale = sorted(rel for rel in manifest if rel not in current_files)
    missing = sorted(rel for rel in current_files if rel not in manifest)
    items = [
        {
            "relPath": record.rel_path,
            "size": record.size,
            "mtime": record.mtime,
            "contentHash": record.content_hash,
            "textHash": record.text_hash,
            "chunkCount": record.chunk_count,
            "embedModel": record.embed_model,
            "indexedAt": record.indexed_at,
            "exists": record.rel_path in current_files,
            "parserDiagnostics": record.parser_diagnostics or {},
        }
        for record in sorted(manifest.values(), key=lambda r: r.rel_path)[:sample_limit]
    ]
    parser_summary = _parser_summary(manifest)
    level = "ok"
    if status.get("state") == "error" or stale:
        level = "error"
    elif status.get("state") == "running" or missing:
        level = "warn"
    return {
        "ok": level != "error",
        "level": level,
        "activeProject": {
            "id": project_row.id,
            "path": project_row.path,
            "name": project_row.name,
            "indexedAt": project_row.indexed_at,
            "chunkCount": project_row.chunk_count,
            "embedModel": project_row.embed_model,
        },
        "indexStatus": status,
        "manifest": {
            "count": len(manifest),
            "totalChunks": sum(r.chunk_count for r in manifest.values()),
            "supportedFileCount": len(current_files),
            "staleFiles": stale[:sample_limit],
            "missingManifestFiles": missing[:sample_limit],
            "items": items,
            "parserSummary": parser_summary,
        },
        "storage": {
            "dbPath": str(vectordb.db_path()),
            "chunkCount": store.count_chunks(project_row.id),
        },
    }


def _kernel_check(context: DiagnosticContext) -> dict[str, Any]:
    app_root_ok = context.app_root.exists()
    return _check(
        "kernel",
        "ok" if app_root_ok and context.auth_required else "warn",
        "本地内核运行中" if app_root_ok else "appRoot 不存在",
        {
            "appRoot": str(context.app_root),
            "port": context.port,
            "authRequired": context.auth_required,
        },
    )


def _provider_check(registry: ProviderRegistry) -> dict[str, Any]:
    validation = registry.validation_summary()
    ready_count = int(validation.get("readyProviderCount") or 0)
    level = "ok" if validation.get("ok") and ready_count > 0 else "error"
    return _check(
        "providers",
        level,
        "provider 配置可用" if level == "ok" else "provider 配置不可用",
        validation,
    )


def _embedding_check(registry: ProviderRegistry) -> dict[str, Any]:
    candidates = [
        name for name in ("bailian", "qwen")
        if name in registry.providers and registry.providers[name].is_ready()
    ]
    return _check(
        "embedding",
        "ok" if candidates else "warn",
        "embedding provider 可用" if candidates else "未发现就绪的 bailian/qwen embedding provider",
        {
            "model": embedding.DEFAULT_MODEL,
            "dims": embedding.DEFAULT_DIMS,
            "readyProviders": candidates,
        },
    )


def _dependency_check() -> dict[str, Any]:
    deps = {
        "docx": _module_available("docx"),
        "pypdf": _module_available("pypdf"),
        "pptx": _module_available("pptx"),
        "textutil": shutil.which("textutil") is not None,
    }
    missing = [name for name, ok in deps.items() if not ok]
    level = "ok" if not missing else "warn"
    return _check(
        "documentDependencies",
        level,
        "文档解析依赖完整" if level == "ok" else "部分文档格式会被跳过",
        {"available": deps, "missing": missing},
    )


def _rag_store_check() -> dict[str, Any]:
    db_path = vectordb.db_path()
    try:
        projects = vectordb.list_projects()
        stat = db_path.stat() if db_path.exists() else None
        store = rag_store.default_store()
        caps = store.capabilities().to_dict() if hasattr(store, "capabilities") else {}
        level = "ok"
        message = "RAG 数据库可访问"
        data = {
            "dbPath": str(db_path),
            "exists": db_path.exists(),
            "sizeBytes": stat.st_size if stat else 0,
            "projectCount": len(projects),
            "backend": caps,
        }
    except Exception as exc:  # noqa: BLE001
        level = "error"
        message = f"RAG 数据库不可访问：{exc.__class__.__name__}: {exc}"
        data = {"dbPath": str(db_path)}
    return _check("ragStore", level, message, data)


def _active_project_check() -> dict[str, Any]:
    active = project_mod.active_project_summary()
    status = project_mod.status()
    if active is None:
        return _check("activeProject", "warn", "没有激活项目", {"indexStatus": status})
    level = "error" if status.get("state") == "error" else "ok"
    return _check("activeProject", level, "当前项目可用", {"active": active, "indexStatus": status})


def _logs_check() -> dict[str, Any]:
    stats = logger.stats(days=1)
    errors = int(stats.get("errors") or 0)
    warns = int(stats.get("warns") or 0)
    level = "error" if errors else ("warn" if warns else "ok")
    return _check(
        "logs",
        level,
        "最近日志有错误" if errors else ("最近日志有警告" if warns else "最近日志正常"),
        stats,
    )


def _current_file_map(project_path: str) -> dict[str, extract.FileRef]:
    return {f.rel_path: f for f in extract.walk_project(project_path)}


def _parser_summary(manifest: dict[str, vectordb.FileManifest]) -> dict[str, Any]:
    parsers: dict[str, int] = {}
    totals = {
        "blockCount": 0,
        "tableCount": 0,
        "codeCount": 0,
        "emptyTextFiles": 0,
        "truncatedFiles": 0,
    }
    for record in manifest.values():
        diag = record.parser_diagnostics or {}
        parser = str(diag.get("parser") or "unknown")
        parsers[parser] = parsers.get(parser, 0) + 1
        totals["blockCount"] += int(diag.get("blockCount") or 0)
        totals["tableCount"] += int(diag.get("tableCount") or 0)
        totals["codeCount"] += int(diag.get("codeCount") or 0)
        if diag.get("emptyText"):
            totals["emptyTextFiles"] += 1
        if diag.get("truncated"):
            totals["truncatedFiles"] += 1
    return {
        "parsers": parsers,
        **totals,
    }


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _check(name: str, level: str, message: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "level": level,
        "ok": level != "error",
        "message": message,
        "data": data,
    }


def _issues_from_checks(checks: list[dict[str, Any]]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for check in checks:
        if check["level"] in {"warn", "error"}:
            issues.append({
                "level": check["level"],
                "check": check["name"],
                "message": check["message"],
            })
    return issues


def _overall_level(checks: list[dict[str, Any]]) -> str:
    if any(check["level"] == "error" for check in checks):
        return "error"
    if any(check["level"] == "warn" for check in checks):
        return "warn"
    return "ok"
