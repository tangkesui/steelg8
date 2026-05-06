from __future__ import annotations

from typing import Any

from services.common import ServiceError, require_dict
from skills.docx import fill as docx_fill
from skills.docx import grow as docx_grow


def placeholders(body: Any) -> dict[str, Any]:
    body = require_dict(body)
    path = str(body.get("path", "")).strip()
    if not path:
        raise ServiceError(400, {"error": "path is required"})
    try:
        names = docx_fill.list_placeholders(path)
    except docx_fill.DocxFillError as exc:
        raise ServiceError(400, {"error": str(exc)}) from exc
    return {"placeholders": names}


def fill(body: Any) -> dict[str, Any]:
    body = require_dict(body)
    template = str(body.get("template", "")).strip()
    data = body.get("data") or {}
    output = body.get("output") or None
    if not template:
        raise ServiceError(400, {"error": "template is required"})
    try:
        result = docx_fill.fill(template, data, output_path=output)
    except docx_fill.DocxFillError as exc:
        raise ServiceError(400, {"error": str(exc)}) from exc
    return {
        "output": result.output_path,
        "replaced": result.replaced_count,
        "missing": result.missing_keys,
        "leftover": result.leftover_placeholders,
    }


def headings(body: Any) -> dict[str, Any]:
    body = require_dict(body)
    path = str(body.get("path", "")).strip()
    if not path:
        raise ServiceError(400, {"error": "path is required"})
    try:
        headings_list = docx_grow.list_headings(path)
    except docx_grow.DocxGrowError as exc:
        raise ServiceError(400, {"error": str(exc)}) from exc
    return {"headings": headings_list}


def insert_section(body: Any) -> dict[str, Any]:
    body = require_dict(body)
    try:
        result = docx_grow.insert_section_after_heading(
            body["path"],
            after_heading=body["afterHeading"],
            new_heading=body["newHeading"],
            new_heading_level=int(body.get("newHeadingLevel", 2)),
            paragraphs=body.get("paragraphs") or [],
            anchor_level=body.get("anchorLevel"),
            output_path=body.get("output"),
        )
    except (KeyError, docx_grow.DocxGrowError) as exc:
        raise ServiceError(400, {"error": str(exc)}) from exc
    return {"output": result.output_path, "inserted": result.inserted_elements, "notes": result.notes}


def append_paragraphs(body: Any) -> dict[str, Any]:
    body = require_dict(body)
    try:
        result = docx_grow.append_paragraphs_after_heading(
            body["path"],
            after_heading=body["afterHeading"],
            paragraphs=body["paragraphs"],
            anchor_level=body.get("anchorLevel"),
            output_path=body.get("output"),
        )
    except (KeyError, docx_grow.DocxGrowError) as exc:
        raise ServiceError(400, {"error": str(exc)}) from exc
    return {"output": result.output_path, "inserted": result.inserted_elements}


def append_row(body: Any) -> dict[str, Any]:
    body = require_dict(body)
    try:
        result = docx_grow.append_table_row(
            body["path"],
            table_index=int(body.get("tableIndex", 0)),
            cells=body["cells"],
            output_path=body.get("output"),
        )
    except (KeyError, docx_grow.DocxGrowError) as exc:
        raise ServiceError(400, {"error": str(exc)}) from exc
    return {"output": result.output_path, "inserted": result.inserted_elements}
