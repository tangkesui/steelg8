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
import knowledge as knowledge_mod
import memory
import project as project_mod
import templates as template_lib
import web as web_mod
from providers import ProviderRegistry
from skills import docx_diff, docx_fill, docx_grow


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
    {
        "type": "function",
        "function": {
            "name": "diff_documents",
            "description": "对比两个文档（.md / .txt / .docx / .pdf / .pptx / .doc）的文本差异，返回 unified diff。你拿到 diff 后用自然语言向用户解读'改了什么、加了什么、删了什么'。两份文件都要能读到。",
            "parameters": {
                "type": "object",
                "properties": {
                    "before": {"type": "string", "description": "旧版本的绝对路径"},
                    "after": {"type": "string", "description": "新版本的绝对路径"},
                    "context": {"type": "integer", "default": 2, "description": "diff 上下文行数"},
                },
                "required": ["before", "after"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "用 Tavily 搜索互联网，返回前 N 条结果（标题 / URL / 摘要）。用户问最新政策 / 查资料 / 对比产品等场景用。要看全文再调 web_fetch。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10},
                    "search_depth": {"type": "string", "enum": ["basic", "advanced"], "default": "basic"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "用 Jina Reader 拉取任意 URL 的正文（markdown 格式）。免费。读 web_search 拿到的链接用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "必须 http:// 或 https:// 开头"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_knowledge",
            "description": (
                "把一段重要信息存到知识库（~/.steelg8/knowledge/）。"
                "不像 remember() 是贴在某个文件里的一行，save_knowledge 是**独立的知识卡片**，"
                "每次对话都会被向量召回。适合：原创观点、客户典型反馈、值得反复引用的片段、"
                "研究结论。title 要能一眼看懂是什么，content 是一段完整的话（>30 字）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "source": {"type": "string", "description": "可选，内容来源，如 URL / 文件路径 / 对话 id"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "templates_list",
            "description": "列出用户模板库 ~/Documents/steelg8/templates/ 下的所有模板（.docx / .xlsx / .pptx），含每个模板的占位符清单。用户说'用模板 xxx'时先调这个看有什么可用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "把一条重要信息写入记忆文件，下次对话你能看到。"
                "scope='user' 写 ~/.steelg8/user.md（所有项目共享的你的偏好）；"
                "scope='project' 写当前激活项目的 steelg8.md（项目背景/术语/决策）。"
                "section 是要追加到的小节名（如 '写作口吻与偏好'、'干系人'），"
                "找不到就新建。"
                "用户明确说'记住 xxx' / 表达长期偏好 / 提到项目关键背景时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": ["user", "project"]},
                    "section": {"type": "string", "description": "目标 markdown 小节名，如 '写作口吻与偏好'"},
                    "note": {"type": "string", "description": "要记下的内容，一两句话即可"},
                },
                "required": ["scope", "note"],
            },
        },
    },
]


# ---- 调度 ----


def dispatch(name: str, args: dict[str, Any], registry: "ProviderRegistry | None" = None) -> dict[str, Any]:
    """执行一个 tool call；总是返回 dict，异常会被转成 {'error': str}。
    registry 用于需要调云 API 的 tool（如 save_knowledge 要 embed）。"""
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

        if name == "diff_documents":
            before = _safe_path(args.get("before"), must_exist=True)
            after = _safe_path(args.get("after"), must_exist=True)
            return docx_diff.diff_files(
                before, after,
                context=int(args.get("context", 2)),
            )

        if name == "web_search":
            if registry is None:
                return {"error": "dispatch: web_search 需要 registry"}
            try:
                results = web_mod.search(
                    str(args.get("query", "")).strip(),
                    registry,
                    max_results=int(args.get("max_results", 5)),
                    search_depth=str(args.get("search_depth", "basic")),
                )
            except web_mod.WebError as exc:
                return {"error": str(exc)}
            return {"results": results}

        if name == "web_fetch":
            url = str(args.get("url", "")).strip()
            if not url:
                return {"error": "url 不能为空"}
            try:
                return web_mod.fetch(url)
            except web_mod.WebError as exc:
                return {"error": str(exc)}

        if name == "save_knowledge":
            if registry is None:
                return {"error": "dispatch: save_knowledge 需要 registry"}
            title = str(args.get("title", "")).strip()
            content = str(args.get("content", "")).strip()
            if not content:
                return {"error": "content 不能为空"}
            tags = args.get("tags") or []
            source = args.get("source")
            try:
                r = knowledge_mod.save_card(
                    title=title,
                    content=content,
                    registry=registry,
                    source=source if isinstance(source, str) else None,
                    tags=[str(t) for t in tags] if isinstance(tags, list) else None,
                )
            except Exception as exc:  # noqa: BLE001
                return {"error": f"{exc.__class__.__name__}: {exc}"}
            return {"ok": True, **r}

        if name == "templates_list":
            items = template_lib.list_all()
            return {
                "dir": str(template_lib.default_dir()),
                "templates": [
                    {
                        "name": t.name,
                        "path": t.path,
                        "placeholders": t.placeholders,
                    }
                    for t in items
                ],
            }

        if name == "remember":
            scope = str(args.get("scope", "")).strip().lower()
            section = str(args.get("section", "")).strip() or "其它"
            note = str(args.get("note", "")).strip()
            if not note:
                return {"error": "note 不能为空"}
            if scope == "user":
                memory.append_user(section, note)
                return {"ok": True, "scope": "user", "section": section, "note": note}
            if scope == "project":
                active = project_mod.get_active()
                if not active:
                    return {"error": "当前没有激活的项目，无法写项目记忆"}
                memory.append_project_memory(active["path"], section, note)
                return {"ok": True, "scope": "project", "section": section, "note": note}
            return {"error": f"scope 必须是 'user' 或 'project'：{scope}"}

        return {"error": f"未知 tool: {name}"}
    except ValueError as exc:
        return {"error": f"参数错误：{exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{exc.__class__.__name__}: {exc}"}


def tool_schemas() -> list[dict[str, Any]]:
    """返回给 LLM 的 tools 数组副本。"""
    return [dict(t) for t in TOOLS]
