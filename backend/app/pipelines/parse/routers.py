"""解析图的条件边：按文档类型分派引擎，按需改走 OCR，按质量分决定是否标记低质量。"""

from __future__ import annotations

from typing import Any

ROUTE_BY_TYPE = {
    "pdf": "layout_parse",
    "office": "office_parse",
    "image": "ocr_parse",
    "text": "plain_parse",
    "html": "html_parse",
}


def route_by_doc_type(state: dict[str, Any]) -> str:
    return ROUTE_BY_TYPE.get(state.get("doc_type", "text"), "plain_parse")


def route_needs_ocr(state: dict[str, Any]) -> str:
    """版面解析没有文字层且策略开启 OCR 时才走识别分支。"""
    stats = state.get("stats") or {}
    config = state.get("config") or {}
    if stats.get("needs_ocr") and config.get("ocr_enabled", False):
        return "ocr_parse"
    return "table_extract"


def route_quality(state: dict[str, Any]) -> str:
    quality = state.get("quality") or {}
    return "mark_low_quality" if quality.get("low_quality") else "persist_blocks"
