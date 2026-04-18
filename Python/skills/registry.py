"""
Tool 注册表：把 docx_fill / docx_grow 等 skill 函数包装成 OpenAI tool schema，
供 LLM tool calling 用。

设计要点：
- tool 名字、描述、参数都用中文/直白写，让国内 LLM 也能理解
- 每个 tool 对应一个 Python callable；dispatch() 负责解析 arguments 并执行
- 路径安全：所有 path 参数必须 (1) 绝对路径，(2) 以 $HOME 开头，(3) 不含 '..'
  —— 防止 LLM 一个抽风把 /etc 给写了
- 返回 tool_result 是 dict，会被 json.dumps 后作为 'role:tool' 消息回给 LLM
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import extract
from skills import docx_fill, docx_grow


# ---- 路径安全 ----

def _safe_path(raw: Any, *, must_exist: bool = False) -> str:
    if not isinstance(raw, str) or not raw:
        raise ValueError("path 必须是非空字符串")
    if ".." in raw:
        raise ValueError(f"path 不允许 '..'：{raw}")
    p = Path(raw).expanduser().resolve()
    home = Path.home().resolve()
    if not str(p).startswith(str(home)):
        raise ValueError(f"path 必须在用户家目录下：{p}")
    if must_exist and not p.exists():
        raise ValueError(f"文件不存在：{p}")
    return str(p)


# ---- tool schemas（OpenAI function-calling 格式） ----

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "docx_list_placeholders",
            "description": "读一份 .docx 模板里所有 {{占位符}} 的名字。用户没告诉你要填什么时，先用它看模板需要哪些字段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "模板 .docx 的绝对路径"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_fill",
            "description": "把数据填进 .docx 模板的 {{key}} 占位符，另存为新文件。数据里可以嵌套：{\"project\": {\"name\": \"xxx\"}} 对应模板里 {{project.name}}。",
            "parameters": {
                "type": "object",
                "properties": {
                    "template": {"type": "string", "description": "模板 .docx 绝对路径"},
                    "data": {"type": "object", "description": "key-value 数据，支持嵌套"},
                    "output": {"type": "string", "description": "输出 .docx 绝对路径；不填就在模板同目录生成 -filled.docx"},
                },
                "required": ["template", "data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_list_headings",
            "description": "列出一份 .docx 里所有 Heading 1/2/3 标题（带级别和顺序）。想给文档插入新章节前先用它确认锚点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标 .docx 的绝对路径"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_insert_section",
            "description": "在某个标题所在章节末尾插入一个新标题 + 若干段落。'章节末尾' = 遇到下一个同级或更高级标题之前的位置。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标 .docx 绝对路径"},
                    "afterHeading": {"type": "string", "description": "锚点标题的文本，必须和文档里的一致（标点、空格敏感）"},
                    "newHeading": {"type": "string", "description": "要插入的新标题文本"},
                    "newHeadingLevel": {"type": "integer", "description": "新标题级别 1-4，默认 2", "minimum": 1, "maximum": 4},
                    "paragraphs": {"type": "array", "items": {"type": "string"}, "description": "新标题下要追加的段落文本数组，可以为空"},
                    "output": {"type": "string", "description": "输出路径；不填就在原文件同目录生成 -edited.docx；填和 path 相同会原地写"},
                },
                "required": ["path", "afterHeading", "newHeading"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_append_paragraphs",
            "description": "在某个标题章节末尾追加若干段落（不新建子标题）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "afterHeading": {"type": "string"},
                    "paragraphs": {"type": "array", "items": {"type": "string"}},
                    "output": {"type": "string"},
                },
                "required": ["path", "afterHeading", "paragraphs"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_append_row",
            "description": "向 docx 里的第 N 个表格（从 0 计数）追加一行。cells 按列顺序给值，少于表格列数会补空，多了截断。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "tableIndex": {"type": "integer", "description": "表格下标，从 0 开始", "minimum": 0},
                    "cells": {"type": "array", "items": {"type": "string"}},
                    "output": {"type": "string"},
                },
                "required": ["path", "tableIndex", "cells"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_read",
            "description": "把一份 .docx 抽成纯文本（Markdown 结构：标题用 #，表格转管道表）。当你需要看一份 docx 里到底写了什么才能决定怎么改时用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要读的 .docx 绝对路径"},
                },
                "required": ["path"],
            },
        },
    },
]


# ---- 调度 ----


def dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """执行一个 tool call；总是返回 dict，异常会被转成 {'error': str}。"""
    try:
        if name == "docx_list_placeholders":
            path = _safe_path(args.get("path"), must_exist=True)
            return {"placeholders": docx_fill.list_placeholders(path)}

        if name == "docx_fill":
            template = _safe_path(args.get("template"), must_exist=True)
            output = _safe_path(args.get("output")) if args.get("output") else None
            data = args.get("data") or {}
            r = docx_fill.fill(template, data, output_path=output)
            return {
                "output": r.output_path,
                "replaced": r.replaced_count,
                "missing": r.missing_keys,
                "leftover": r.leftover_placeholders,
            }

        if name == "docx_list_headings":
            path = _safe_path(args.get("path"), must_exist=True)
            return {"headings": docx_grow.list_headings(path)}

        if name == "docx_insert_section":
            path = _safe_path(args.get("path"), must_exist=True)
            output = _safe_path(args.get("output")) if args.get("output") else None
            r = docx_grow.insert_section_after_heading(
                path,
                after_heading=str(args.get("afterHeading", "")),
                new_heading=str(args.get("newHeading", "")),
                new_heading_level=int(args.get("newHeadingLevel", 2)),
                paragraphs=list(args.get("paragraphs") or []),
                output_path=output,
            )
            return {"output": r.output_path, "inserted": r.inserted_elements, "notes": r.notes}

        if name == "docx_append_paragraphs":
            path = _safe_path(args.get("path"), must_exist=True)
            output = _safe_path(args.get("output")) if args.get("output") else None
            r = docx_grow.append_paragraphs_after_heading(
                path,
                after_heading=str(args.get("afterHeading", "")),
                paragraphs=list(args.get("paragraphs") or []),
                output_path=output,
            )
            return {"output": r.output_path, "inserted": r.inserted_elements}

        if name == "docx_append_row":
            path = _safe_path(args.get("path"), must_exist=True)
            output = _safe_path(args.get("output")) if args.get("output") else None
            r = docx_grow.append_table_row(
                path,
                table_index=int(args.get("tableIndex", 0)),
                cells=list(args.get("cells") or []),
                output_path=output,
            )
            return {"output": r.output_path, "inserted": r.inserted_elements}

        if name == "docx_read":
            path = _safe_path(args.get("path"), must_exist=True)
            txt = extract.read_docx(path)
            # 避免 tool response 超长；截断就好
            if len(txt) > 8000:
                txt = txt[:8000] + "\n\n…（已截断，完整内容超过 8000 字）"
            return {"text": txt}

        return {"error": f"未知 tool: {name}"}
    except ValueError as exc:
        return {"error": f"参数错误：{exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{exc.__class__.__name__}: {exc}"}


def tool_schemas() -> list[dict[str, Any]]:
    """返回给 LLM 的 tools 数组副本。"""
    return [dict(t) for t in TOOLS]
