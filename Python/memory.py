"""
五层记忆：L2 用户画像 + L3 项目记忆
------------------------------------

- L1 soul.md：已在 server.py 处理
- L2 user.md：~/.steelg8/user.md   所有项目共享的"你"的偏好
- L3 project/steelg8.md：<project_root>/steelg8.md   当前项目独有的背景
- L4 会话：内存
- L5 知识库：vectordb（见 vectordb.py）

本模块负责 L2/L3 的读、写（追加）、注入。

设计决定：
- 不强制 Agent 学习。Agent 想写就调 remember() tool；用户也可以直接打开文件改
- 每次对话时把 L2/L3 全文拼到 system prompt 里（简单暴力，token 便宜）
- 首次调用会自动建模板文件，用户清楚"在哪里 / 怎么改"
"""

from __future__ import annotations

import os
from pathlib import Path


USER_PATH = Path(os.environ.get(
    "STEELG8_USER_MD",
    Path.home() / ".steelg8" / "user.md",
))


USER_TEMPLATE = """# steelg8 用户画像（L2）

> Agent 在对话里学到你的偏好，会追加到这里。你也可以手动改。
> 每次对话会把这份文件拼到 system prompt 里。

## 基本

（空）

## 写作口吻与偏好

（空）

## 常用流程 / 模板

（空）

## 禁忌 / 边界

（空）
"""


PROJECT_TEMPLATE = """# {project_name} · 项目记忆（L3）

> 这是 steelg8 为当前项目维护的记忆文件。
> Agent 在对话里遇到"这个项目的背景 / 干系人 / 决策"时会来读这里，
> 也可以通过 remember() 工具往这里追加新条目。
> 你可以随时手动编辑这份文件。

## 背景

（空）

## 干系人

（空）

## 术语表

（空）

## 历史决策

（空）
"""


# ---- L2: user.md ----


def user_path() -> Path:
    p = USER_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def ensure_user() -> str:
    p = user_path()
    if not p.exists():
        p.write_text(USER_TEMPLATE, encoding="utf-8")
    return p.read_text(encoding="utf-8")


def append_user(section: str, note: str) -> None:
    """向 user.md 指定 section 追加一条。section 不存在就新建。"""
    p = user_path()
    ensure_user()
    text = p.read_text(encoding="utf-8")
    text = _append_to_section(text, section, note)
    p.write_text(text, encoding="utf-8")


# ---- L3: <project>/steelg8.md ----


def project_memory_path(project_root: str) -> Path:
    return Path(project_root).expanduser() / "steelg8.md"


def ensure_project_memory(project_root: str, project_name: str = "") -> str:
    p = project_memory_path(project_root)
    if not p.exists():
        try:
            name = project_name or Path(project_root).name
            p.write_text(PROJECT_TEMPLATE.format(project_name=name), encoding="utf-8")
        except OSError:
            # 项目目录不可写（例如 readonly 挂载）→ 降级到 ~/.steelg8 下按 hash 命名
            return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def append_project_memory(project_root: str, section: str, note: str) -> None:
    p = project_memory_path(project_root)
    ensure_project_memory(project_root)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return
    text = _append_to_section(text, section, note)
    try:
        p.write_text(text, encoding="utf-8")
    except OSError:
        return


# ---- 通用：往 markdown 某个 "## Section" 下追加一条 bullet ----


def _append_to_section(text: str, section: str, note: str) -> str:
    """把 `- <note>` 追加到 '## {section}' 小节末尾；section 不存在就新建到文件末尾。
    会尝试清理小节下紧跟着的 '（空）' 占位符。"""
    note = note.strip()
    if not note:
        return text

    bullet = f"- {note}"
    lines = text.splitlines()
    heading_prefix = f"## {section.strip()}"

    # 找到目标 heading
    hit_idx = -1
    for i, line in enumerate(lines):
        if line.strip() == heading_prefix:
            hit_idx = i
            break

    if hit_idx < 0:
        # 追加新 section
        tail = "" if text.endswith("\n") else "\n"
        return text + f"{tail}\n{heading_prefix}\n\n{bullet}\n"

    # 找下一个 heading 或 EOF，作为插入区间终点
    end_idx = len(lines)
    for j in range(hit_idx + 1, len(lines)):
        if lines[j].startswith("## ") or lines[j].startswith("# "):
            end_idx = j
            break

    body = lines[hit_idx + 1 : end_idx]
    # 清理"（空）"占位符
    body = [b for b in body if b.strip() != "（空）"]
    # 去掉尾部空行
    while body and not body[-1].strip():
        body.pop()
    body.append(bullet)

    new_lines = lines[: hit_idx + 1] + body + [""] + lines[end_idx:]
    return "\n".join(new_lines)


# ---- 注入 ----


def compose_memory_block(
    *,
    include_user: bool = True,
    project_root: str | None = None,
    project_name: str = "",
    max_user_chars: int = 4000,
    max_project_chars: int = 6000,
) -> str:
    """拼出 L2+L3 的 memory 段落，给 system prompt 用。空字符串代表没什么可加的。"""
    parts: list[str] = []

    if include_user:
        try:
            u = ensure_user()
            u = u.strip()
            if _has_meaningful_content(u) and len(u) > 0:
                if len(u) > max_user_chars:
                    u = u[:max_user_chars] + "\n\n…（已截断）"
                parts.append(f"### L2 · 用户画像（来自 ~/.steelg8/user.md）\n\n{u}")
        except OSError:
            pass

    if project_root:
        try:
            pm = ensure_project_memory(project_root, project_name)
            pm = (pm or "").strip()
            if _has_meaningful_content(pm):
                if len(pm) > max_project_chars:
                    pm = pm[:max_project_chars] + "\n\n…（已截断）"
                parts.append(
                    f"### L3 · 当前项目记忆（来自 {project_root}/steelg8.md）\n\n{pm}"
                )
        except OSError:
            pass

    return "\n\n---\n\n".join(parts)


def _has_meaningful_content(text: str) -> bool:
    """检测 markdown 里是否有"实质"内容（不只是模板框架）。
    所有非空行里至少有一行不是：#/##/###/>/分隔符/（空）。"""
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        if s.startswith(">"):
            continue
        if s in {"（空）", "---"}:
            continue
        return True
    return False
