"""解析图节点：每个节点只做一件事，输入输出都是纯数据，方便单独测试。"""

from __future__ import annotations

import re
from typing import Any

from app.domain.enums import BlockType
from app.parsing.base import ParsedBlock, ParseInput
from app.parsing.quality import score_quality
from app.parsing.routing import resolve_effective_engine
from app.parsing.text_blocks import detect_doc_type

_EMPTY_CELL = re.compile(r"\s*\|\s*")


def _state_list(state: dict[str, Any], key: str) -> list[Any]:
    return list(state.get(key) or [])


def detect_type(state: dict[str, Any]) -> dict[str, Any]:
    doc_type = state.get("doc_type") or detect_doc_type(
        state.get("file_name", ""), state.get("mime_type", "")
    )
    return {"doc_type": doc_type, "blocks": [], "warnings": [], "stats": {}}


def make_engine_node(fallback: str):
    """策略指定的引擎优先；不支持当前文档类型时回落到按类型兜底的引擎。"""

    def _node(state: dict[str, Any]) -> dict[str, Any]:
        doc_type = state.get("doc_type", "text")
        engine = resolve_effective_engine(doc_type, state.get("engine"), fallback)

        result = engine.parse(
            ParseInput(
                data=state.get("data", b""),
                file_name=state.get("file_name", ""),
                mime_type=state.get("mime_type", ""),
                doc_type=doc_type,
                config=state.get("config") or {},
            )
        )
        stats = {**state.get("stats", {}), **result.stats}
        stats["engine"] = engine.name
        if result.needs_ocr:
            stats["needs_ocr"] = True
        return {
            "blocks": _state_list(state, "blocks") + result.blocks,
            "warnings": _state_list(state, "warnings") + result.warnings,
            "stats": stats,
        }

    return _node


def _table_rows(content: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("|"):
            line = line.strip("|")
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) >= 2:
            rows.append(cells)
    return rows


def _render_table(rows: list[list[str]]) -> str:
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    return "\n".join("| " + " | ".join(row) + " |" for row in padded)


def table_extract(state: dict[str, Any]) -> dict[str, Any]:
    """把表格块规整为等宽 markdown，并合并同页相邻的表格碎片。"""
    result: list[ParsedBlock] = []
    buffer: list[list[str]] = []
    buffer_page: int | None = None

    def flush() -> None:
        nonlocal buffer, buffer_page
        if not buffer:
            return
        result.append(
            ParsedBlock(
                BlockType.TABLE,
                _render_table(buffer),
                page_no=buffer_page,
                metadata={"row_count": len(buffer), "columns": buffer[0]},
                seq=len(result),
            )
        )
        buffer = []
        buffer_page = None

    for block in _state_list(state, "blocks"):
        if block.block_type != BlockType.TABLE:
            flush()
            result.append(block)
            continue
        rows = _table_rows(block.content)
        if not rows:
            continue
        if buffer and buffer_page == block.page_no:
            if rows and buffer[-1] == rows[0]:
                rows = rows[1:]
            buffer.extend(rows)
        else:
            flush()
            buffer = rows
            buffer_page = block.page_no
    flush()

    return {"blocks": result}


def normalize(state: dict[str, Any]) -> dict[str, Any]:
    """去空、去重、重排序号，并建立"块 -> 最近标题"的父子关系。"""
    remove_header = bool((state.get("config") or {}).get("remove_header", True))
    blocks: list[ParsedBlock] = []
    seen: set[tuple[str, str, int | None]] = set()
    last_title_index: int | None = None

    for block in _state_list(state, "blocks"):
        content = block.content.strip()
        if not content and block.block_type != BlockType.IMAGE:
            continue
        key = (block.block_type.value, content, block.page_no)
        if content and key in seen:
            continue
        seen.add(key)

        block.content = content
        if block.block_type in {BlockType.HEADER, BlockType.FOOTER}:
            block.is_discarded = remove_header
        if block.block_type == BlockType.TITLE:
            block.level = block.level or 1
            block.parent_index = None
            last_title_index = len(blocks)
        else:
            block.parent_index = last_title_index
        block.seq = len(blocks)
        blocks.append(block)

    stats = {**state.get("stats", {}), "block_count": len(blocks)}
    return {"blocks": blocks, "stats": stats}


def quality_score(state: dict[str, Any]) -> dict[str, Any]:
    result = score_quality(_state_list(state, "blocks"), state.get("stats") or {})
    quality = {
        "score": result.score,
        "reasons": result.reasons,
        "stats": result.stats,
        "low_quality": result.low_quality,
    }
    return {"quality": quality}


def persist_blocks(state: dict[str, Any]) -> dict[str, Any]:
    """定稿节点：只产出待落库的最终块集合，实际写库由服务层在同一事务完成。"""
    blocks = _state_list(state, "blocks")
    stats = {
        **state.get("stats", {}),
        "block_count": len(blocks),
        "effective_block_count": sum(1 for block in blocks if not block.is_discarded),
        "table_count": sum(1 for block in blocks if block.block_type == BlockType.TABLE),
        "title_count": sum(1 for block in blocks if block.block_type == BlockType.TITLE),
        "image_count": sum(1 for block in blocks if block.block_type == BlockType.IMAGE),
    }
    return {"blocks": blocks, "stats": stats}


def mark_low_quality(state: dict[str, Any]) -> dict[str, Any]:
    quality = dict(state.get("quality") or {})
    quality["low_quality"] = True
    reasons = list(quality.get("reasons") or [])
    if not reasons:
        reasons = ["解析质量低于阈值，建议人工复核"]
        quality["reasons"] = reasons
    warnings = _state_list(state, "warnings")
    for reason in reasons:
        if reason not in warnings:
            warnings.append(reason)
    return {"quality": quality, "warnings": warnings}
