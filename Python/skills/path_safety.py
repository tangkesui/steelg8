"""
Tool 路径沙箱
-------------

所有接收 path 参数的 skill 都必须经过 `safe_path()`。规则：
- resolve 后必须落在 `$HOME` 下，**或** preferences 里 `workspace_allowlist`
  显式列出的目录之一（例如用户主动允许 `/Volumes/Data/work` 这类盘外路径）。
- `must_exist=True` 用于读类工具，避免提前打开 sentinel 路径。
- `suffixes=` 限定文件后缀。
- `access="write"` 时再叠一层目录黑名单（.ssh / .git / .aws 等不允许覆盖）。
  **黑名单仅作用于落在 $HOME 下的路径**——用户已显式允许的 allowlist 目录不再
  二次拦截，避免和用户意图打架。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


DOCX_SUFFIXES: set[str] = {".docx"}

WRITE_DENY_DIRS: set[str] = {
    ".ssh",
    ".gnupg",
    ".aws",
    ".config",
    ".git",
    ".vscode",
    ".idea",
    ".claude",
}


def _load_workspace_allowlist() -> list[Path]:
    """从 preferences 加载用户显式允许的额外目录根。"""
    try:
        import preferences as prefs_mod
        prefs = prefs_mod.load()
    except Exception:  # noqa: BLE001
        return []
    raw = prefs.get("workspace_allowlist") or []
    if not isinstance(raw, list):
        return []
    out: list[Path] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        try:
            resolved = Path(item).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if resolved.is_dir():
            out.append(resolved)
    return out


def safe_path(
    raw: Any,
    *,
    must_exist: bool = False,
    suffixes: set[str] | None = None,
    access: str = "read",
    allowlist: list[str] | None = None,
) -> str:
    if not isinstance(raw, str) or not raw:
        raise ValueError("path 必须是非空字符串")
    p = Path(raw).expanduser().resolve()
    home = Path.home().resolve()

    # 允许根：home 总在 + allowlist（参数优先于 preferences，便于测试注入）
    extra_roots: list[Path]
    if allowlist is not None:
        extra_roots = []
        for item in allowlist:
            if not isinstance(item, str) or not item.strip():
                continue
            try:
                resolved = Path(item).expanduser().resolve()
            except (OSError, RuntimeError):
                continue
            if resolved.is_dir():
                extra_roots.append(resolved)
    else:
        extra_roots = _load_workspace_allowlist()

    matched_root: Path | None = None
    for root in [home, *extra_roots]:
        try:
            p.relative_to(root)
        except ValueError:
            continue
        matched_root = root
        break
    if matched_root is None:
        raise ValueError(f"path 必须在用户家目录或 workspace_allowlist 内：{p}")

    if must_exist and not p.exists():
        raise ValueError(f"文件不存在：{p}")
    if suffixes and p.suffix.lower() not in suffixes:
        allowed = ", ".join(sorted(suffixes))
        raise ValueError(f"path 后缀必须是 {allowed}：{p}")
    # 写黑名单仅作用于 $HOME 下；显式 allowlist 不再二次拦截。
    if access == "write" and matched_root == home:
        rel_parts = p.relative_to(home).parts
        blocked = next(
            (part for part in rel_parts if part.lower() in WRITE_DENY_DIRS), None
        )
        if blocked:
            raise ValueError(f"不允许写入敏感目录 {blocked}：{p}")
    return str(p)
