"""
Tool 注册表：把 docx_fill / docx_grow 等 skill 函数包装成 OpenAI tool schema，
供 LLM tool calling 用。

设计要点：
- tool schema 集中在 `skills.schemas`，本文件不再混入纯数据；
- 路径安全在 `skills.path_safety`：所有 path 参数必须 resolve 后仍位于 $HOME 下；
- dispatch() 解析 arguments → 调用 skill 函数 → 用 _enrich_tool_result 给失败结果补 hint。
- 返回 tool_result 是 dict，会被 json.dumps 后作为 'role:tool' 消息回给 LLM。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import extract
import knowledge as knowledge_mod
import logger
import memory
import project as project_mod
import templates as template_lib
import time as _time
import web as web_mod
from providers import ProviderRegistry
from skills.docx import comments as docx_comments
from skills.docx import convert as docx_convert
from skills.docx import diff as docx_diff
from skills.docx import edit as docx_edit
from skills.docx import fill as docx_fill
from skills.docx import grow as docx_grow
from skills.docx import media as docx_media
from skills.docx import page as docx_page
from skills.docx import xml_io as docx_xml_io
from skills.path_safety import DOCX_SUFFIXES as _DOCX_SUFFIXES
from skills.path_safety import safe_path as _safe_path
from skills.schemas import TOOLS



# ---- 调度 ----


def dispatch(name: str, args: dict[str, Any], registry: "ProviderRegistry | None" = None) -> dict[str, Any]:
    """执行一个 tool call；总是返回 dict，异常会被转成 {'error': str}。
    registry 用于需要调云 API 的 tool（如 save_knowledge 要 embed）。"""
    result = _dispatch_inner(name, args, registry)
    return _enrich_tool_result(name, args, result)


def _dispatch_inner(name: str, args: dict[str, Any], registry: "ProviderRegistry | None") -> dict[str, Any]:
    started = _time.time()
    args_preview = json.dumps(args, ensure_ascii=False)[:200]
    try:
        if name == "docx_list_placeholders":
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            return {"placeholders": docx_fill.list_placeholders(path)}

        if name == "docx_fill":
            template = _safe_path(args.get("template"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            output = (
                _safe_path(args.get("output"), suffixes=_DOCX_SUFFIXES, access="write")
                if args.get("output") else None
            )
            data = args.get("data") or {}
            r = docx_fill.fill(template, data, output_path=output)
            return {
                "output": r.output_path,
                "replaced": r.replaced_count,
                "missing": r.missing_keys,
                "leftover": r.leftover_placeholders,
            }

        if name == "docx_list_headings":
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            return {"headings": docx_grow.list_headings(path)}

        if name == "docx_build_outline":
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            output = (
                _safe_path(args.get("output"), suffixes=_DOCX_SUFFIXES, access="write")
                if args.get("output") else None
            )
            sections_raw = args.get("sections") or []
            if not isinstance(sections_raw, list) or not sections_raw:
                return {"error": "sections 不能为空，至少传一个章节"}
            after = args.get("after") or None
            r = docx_grow.build_outline(
                path,
                sections=sections_raw,
                after=str(after) if after else None,
                output_path=output,
            )
            return r  # 已经是 dict，含 output_path / total_inserted / sections / final_headings

        if name == "docx_insert_section":
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            output = (
                _safe_path(args.get("output"), suffixes=_DOCX_SUFFIXES, access="write")
                if args.get("output") else None
            )
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
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            output = (
                _safe_path(args.get("output"), suffixes=_DOCX_SUFFIXES, access="write")
                if args.get("output") else None
            )
            r = docx_grow.append_paragraphs_after_heading(
                path,
                after_heading=str(args.get("afterHeading", "")),
                paragraphs=list(args.get("paragraphs") or []),
                output_path=output,
            )
            return {"output": r.output_path, "inserted": r.inserted_elements}

        if name == "docx_append_row":
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            output = (
                _safe_path(args.get("output"), suffixes=_DOCX_SUFFIXES, access="write")
                if args.get("output") else None
            )
            r = docx_grow.append_table_row(
                path,
                table_index=int(args.get("tableIndex", 0)),
                cells=list(args.get("cells") or []),
                output_path=output,
            )
            return {"output": r.output_path, "inserted": r.inserted_elements}

        if name == "docx_insert_table":
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            output = (
                _safe_path(args.get("output"), suffixes=_DOCX_SUFFIXES, access="write")
                if args.get("output") else None
            )
            return docx_edit.insert_table(
                path,
                after_heading=str(args.get("afterHeading", "")),
                headers=list(args.get("headers") or []),
                rows=list(args.get("rows") or []),
                caption=str(args["caption"]) if args.get("caption") else None,
                font_size=int(args.get("fontSize", 10)),
                output_path=output,
            )

        if name == "docx_replace_text":
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            output = (
                _safe_path(args.get("output"), suffixes=_DOCX_SUFFIXES, access="write")
                if args.get("output") else None
            )
            replacements = args.get("replacements") or {}
            if not isinstance(replacements, dict) or not replacements:
                return {"error": "replacements 必须是非空 dict"}
            return docx_edit.replace_text(
                path,
                replacements={str(k): str(v) for k, v in replacements.items()},
                scope=str(args.get("scope", "all")),
                output_path=output,
            )

        if name == "docx_rename_heading":
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            output = (
                _safe_path(args.get("output"), suffixes=_DOCX_SUFFIXES, access="write")
                if args.get("output") else None
            )
            return docx_edit.rename_heading(
                path,
                old_title=str(args.get("oldTitle", "")),
                new_title=str(args.get("newTitle", "")),
                level=int(args["level"]) if args.get("level") is not None else None,
                output_path=output,
            )

        if name == "docx_delete_section":
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            output = (
                _safe_path(args.get("output"), suffixes=_DOCX_SUFFIXES, access="write")
                if args.get("output") else None
            )
            return docx_edit.delete_section(
                path,
                heading=str(args.get("heading", "")),
                level=int(args["level"]) if args.get("level") is not None else None,
                delete_range=str(args.get("deleteRange", "heading_only")),
                output_path=output,
            )

        if name == "docx_check_compliance":
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            required_headings = args.get("requiredHeadings") or None
            required_tables = args.get("requiredTables") or None
            if required_headings and not isinstance(required_headings, list):
                return {"error": "requiredHeadings 必须是数组"}
            if required_tables and not isinstance(required_tables, dict):
                return {"error": "requiredTables 必须是 object"}
            return docx_edit.check_compliance(
                path,
                required_headings=list(required_headings) if required_headings else None,
                required_tables={
                    str(k): list(v) for k, v in (required_tables or {}).items()
                } if required_tables else None,
            )

        if name == "docx_validate":
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            return docx_xml_io.validate(path)

        if name == "docx_list_tracked_changes":
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            changes = docx_xml_io.iter_tracked_changes(path)
            return {
                "count": len(changes),
                "changes": changes,
                "authors": sorted({c["author"] for c in changes if c.get("author")}),
            }

        if name == "docx_resolve_tracked_changes":
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            mode = str(args.get("mode", "accept")).lower()
            if mode not in ("accept", "reject"):
                return {"error": "mode 必须是 'accept' 或 'reject'"}
            output = (
                _safe_path(args.get("output"), suffixes=_DOCX_SUFFIXES, access="write")
                if args.get("output") else None
            )
            # 默认输出走项目版本路径
            if not output:
                active = project_mod.get_active()
                if active:
                    out = project_mod.next_version_path(Path(path).stem, ext=".docx",
                                                        label=f"{mode}-changes")
                    output = str(out) if out else None

            before_changes = docx_xml_io.iter_tracked_changes(path)
            if mode == "accept":
                out_path = docx_xml_io.accept_all_changes(path, output_path=output)
            else:
                out_path = docx_xml_io.reject_all_changes(path, output_path=output)
            return {
                "output_path": str(out_path),
                "mode": mode,
                "resolved_count": len(before_changes),
                "by_author": {
                    a: sum(1 for c in before_changes if c.get("author") == a)
                    for a in sorted({c.get("author", "") for c in before_changes})
                },
            }

        if name == "docx_convert_to_docx":
            # 允许多种输入格式，不走 _DOCX_SUFFIXES
            path = _safe_path(args.get("path"), must_exist=True,
                              suffixes={".doc", ".docx", ".rtf", ".odt", ".wps",
                                        ".html", ".htm", ".txt", ".md"})
            output_dir = args.get("output_dir")
            if output_dir:
                output_dir = _safe_path(output_dir, access="write")
            try:
                return docx_convert.convert_to_docx(path, output_dir=output_dir)
            except docx_convert.DocxConvertError as exc:
                return {"error": str(exc)}

        if name == "docx_insert_image":
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            image_path = _safe_path(args.get("imagePath"), must_exist=True,
                                    suffixes={".png", ".jpg", ".jpeg", ".gif",
                                              ".bmp", ".tiff", ".webp"})
            output = (
                _safe_path(args.get("output"), suffixes=_DOCX_SUFFIXES, access="write")
                if args.get("output") else None
            )
            return docx_media.insert_image(
                path,
                image_path=image_path,
                after_heading=str(args.get("afterHeading", "")),
                width_cm=float(args["widthCm"]) if args.get("widthCm") is not None else None,
                height_cm=float(args["heightCm"]) if args.get("heightCm") is not None else None,
                caption=str(args["caption"]) if args.get("caption") else None,
                output_path=output,
            )

        if name == "docx_set_header_footer":
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            output = (
                _safe_path(args.get("output"), suffixes=_DOCX_SUFFIXES, access="write")
                if args.get("output") else None
            )
            return docx_page.set_header_footer(
                path,
                header_text=str(args["headerText"]) if args.get("headerText") is not None else None,
                footer_text=str(args["footerText"]) if args.get("footerText") is not None else None,
                footer_with_page_number=bool(args.get("footerWithPageNumber", False)),
                section_index=int(args.get("sectionIndex", 0)),
                output_path=output,
            )

        if name == "docx_insert_toc":
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            output = (
                _safe_path(args.get("output"), suffixes=_DOCX_SUFFIXES, access="write")
                if args.get("output") else None
            )
            return docx_page.insert_toc(
                path,
                title=str(args.get("title", "目录")),
                levels=str(args.get("levels", "1-3")),
                after_heading=str(args["afterHeading"]) if args.get("afterHeading") else None,
                output_path=output,
            )

        if name == "docx_list_comments":
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            items = docx_comments.list_comments(path)
            return {
                "count": len(items),
                "comments": items,
                "authors": sorted({c.get("author", "") for c in items if c.get("author")}),
            }

        if name == "docx_add_comment":
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            output = (
                _safe_path(args.get("output"), suffixes=_DOCX_SUFFIXES, access="write")
                if args.get("output") else None
            )
            return docx_comments.add_comment(
                path,
                target_text=str(args.get("targetText", "")),
                comment_text=str(args.get("commentText", "")),
                author=str(args.get("author", "steelg8")),
                initials=str(args.get("initials", "s8")),
                output_path=output,
            )

        if name == "docx_read":
            path = _safe_path(args.get("path"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            txt = extract.read_docx(path)
            # 避免 tool response 超长；截断就好
            if len(txt) > 8000:
                txt = txt[:8000] + "\n\n…（已截断，完整内容超过 8000 字）"
            return {"text": txt}

        if name == "diff_documents":
            before = _safe_path(args.get("before"), must_exist=True, suffixes=_DOCX_SUFFIXES)
            after = _safe_path(args.get("after"), must_exist=True, suffixes=_DOCX_SUFFIXES)
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

        if name == "project_find_references":
            active = project_mod.get_active()
            if not active:
                return {"error": "当前没有激活的项目。先在侧栏「打开…」选一个项目目录。"}
            root = Path(active["path"]).expanduser()
            suffix_filter = (args.get("suffix_filter") or "").lower().strip()
            allowed = {".docx", ".xlsx", ".pptx", ".pdf", ".md"}
            if suffix_filter:
                allowed = {suffix_filter if suffix_filter.startswith(".") else "." + suffix_filter}

            items: list[dict[str, Any]] = []
            for p in root.rglob("*"):
                # 跳过输出目录自身，避免自闭环
                if project_mod.OUTPUT_DIR_NAME in p.parts:
                    continue
                if not p.is_file():
                    continue
                if p.suffix.lower() not in allowed:
                    continue
                try:
                    stat = p.stat()
                except OSError:
                    continue
                items.append({
                    "path": str(p),
                    "rel": str(p.relative_to(root)),
                    "suffix": p.suffix.lower(),
                    "size_bytes": stat.st_size,
                    "mtime": int(stat.st_mtime),
                })
            items.sort(key=lambda x: (-x["mtime"], x["rel"]))
            out_dir = project_mod.output_dir(ensure=True)
            return {
                "project": {
                    "name": active.get("name", ""),
                    "path": str(root),
                },
                "output_dir": str(out_dir) if out_dir else "",
                "count": len(items),
                "files": items[:40],  # 截断避免返回太大
                "truncated": len(items) > 40,
            }

        if name == "project_output_path":
            active = project_mod.get_active()
            if not active:
                return {"error": "当前没有激活的项目，无法分配输出路径"}
            task_name = str(args.get("task_name") or "").strip()
            if not task_name:
                return {"error": "task_name 不能为空"}
            suffix = str(args.get("suffix") or ".docx").strip()
            if not suffix.startswith("."):
                suffix = "." + suffix
            label = str(args.get("label") or "").strip()
            p = project_mod.next_version_path(task_name, ext=suffix, label=label)
            if not p:
                return {"error": "分配输出路径失败"}
            return {
                "output_path": str(p),
                "task_dir": str(p.parent),
                "version": p.stem,
            }

        return {"error": f"未知 tool: {name}"}
    except ValueError as exc:
        logger.warn("tool.arg_error", tool=name, args=args_preview, error=str(exc),
                    duration_ms=int((_time.time() - started) * 1000))
        return {"error": f"参数错误：{exc}"}
    except Exception as exc:  # noqa: BLE001
        logger.error("tool.exception", exc=exc, tool=name, args=args_preview,
                     duration_ms=int((_time.time() - started) * 1000))
        return {"error": f"{exc.__class__.__name__}: {exc}"}
    finally:
        pass  # 成功路径不记录（太吵），出错才记


def _enrich_tool_result(name: str, args: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """给 tool 结果增强：失败时附 hint 引导 LLM 正确恢复。"""
    if not isinstance(result, dict):
        return result
    err = result.get("error", "")

    # docx_insert_section / docx_append_paragraphs 找不到锚点标题 → 给 AI 明确指引
    if err and name in ("docx_insert_section", "docx_append_paragraphs") and "找不到标题" in err:
        path = args.get("path")
        try:
            if path:
                headings = docx_grow.list_headings(path)
                heading_names = [h.get("text") for h in headings if h.get("text")]
                result["hint"] = (
                    "锚点标题不存在。可能原因：(1) 你用的 afterHeading 是还没插入的章节；"
                    "(2) 一轮里发了多个 docx_insert_section，但锚点依赖上一步的结果——"
                    "**请务必一次只插一个章节**，等返回后再决定下一个。"
                )
                result["available_headings"] = heading_names[:50]
        except Exception:  # noqa: BLE001
            pass

    # 工具执行失败日志
    if err:
        logger.warn("tool.result_error", tool=name, error=err[:200])
    return result


def tool_schemas() -> list[dict[str, Any]]:
    """返回给 LLM 的 tools 数组副本。"""
    return [dict(t) for t in TOOLS]
