"""文本行到内容块的启发式切分，供纯文本与 PDF 引擎复用。

规则尽量保守：只做"明显是标题/表格/列表/代码"的判定，其余一律归为段落，
宁可少标也不把正文误认成结构。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.domain.enums import BlockType
from app.parsing.base import ParsedBlock

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_ORDERED_ITEM = re.compile(r"^\s*(\d{1,3}[.)、]|[一二三四五六七八九十]+[、.)])\s+")
_BULLET_ITEM = re.compile(r"^\s*[-*•·]\s+")
_CODE_INDENT = re.compile(r"^( {4}|\t)")
_SENTENCE_END = ("。", "，", ",", "；", ";", "：", ":", "！", "!", "？", "?", "…")
_NUMBERED_TITLE = re.compile(
    r"^\s*(第[一二三四五六七八九十百]+[章节条]|[0-9]+(\.[0-9]+){0,2})\s*[、.]?\s*\S"
)
_TABLE_SEP = re.compile(r"\s{2,}|\t")


def detect_doc_type(file_name: str, mime_type: str = "") -> str:
    """按扩展名/后缀把文件归一到策略使用的 doc_type。"""
    suffix = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if suffix == "pdf" or mime_type == "application/pdf":
        return "pdf"
    if suffix in {"doc", "docx", "ppt", "pptx", "xls", "xlsx"}:
        return "office"
    if suffix in {"png", "jpg", "jpeg", "tiff", "bmp"}:
        return "image"
    if suffix == "html" or mime_type == "text/html":
        return "html"
    return "text"


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def table_columns(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped:
        return []
    if stripped.startswith("|") and stripped.endswith("|"):
        return [cell.strip() for cell in stripped.strip("|").split("|")]
    if "|" in stripped:
        return [cell.strip() for cell in stripped.split("|")]
    cells = [cell.strip() for cell in _TABLE_SEP.split(stripped) if cell.strip()]
    return cells if len(cells) >= 3 else []


def _is_table_row(line: str) -> bool:
    return len(table_columns(line)) >= 2


def _is_heading(line: str) -> bool:
    text = line.strip()
    if not text or len(text) > 40:
        return False
    if text.endswith(_SENTENCE_END):
        return False
    if _MD_HEADING.match(text) or _NUMBERED_TITLE.match(text):
        return True
    # 无标点且很短的一行，多半是小标题
    return len(text) <= 20 and not re.search(r"[，。；：,.;:!?！？]", text)


def _heading_level(line: str) -> int:
    match = _MD_HEADING.match(line.strip())
    if match:
        return len(match.group(1))
    return 2 if _NUMBERED_TITLE.match(line.strip()) else 1


def blocks_from_lines(
    lines: Sequence[str],
    *,
    page_no: int | None = None,
    start_index: int = 0,
    detect_tables: bool = True,
) -> list[ParsedBlock]:
    """把一段连续文本行切成块；返回块的 ``seq`` 为相对序号，后续统一重排。"""
    blocks: list[ParsedBlock] = []
    paragraph: list[str] = []
    table_rows: list[list[str]] = []
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            text = "\n".join(paragraph).strip()
            if text:
                blocks.append(
                    ParsedBlock(
                        BlockType.PARAGRAPH,
                        text,
                        page_no=page_no,
                        seq=start_index + len(blocks),
                    )
                )
            paragraph = []

    def flush_table() -> None:
        nonlocal table_rows
        if table_rows:
            header = table_rows[0]
            width = max(len(row) for row in table_rows)
            rows = [row + [""] * (width - len(row)) for row in table_rows]
            content = "\n".join("| " + " | ".join(row) + " |" for row in rows)
            blocks.append(
                ParsedBlock(
                    BlockType.TABLE,
                    content,
                    page_no=page_no,
                    metadata={"columns": header, "row_count": len(rows)},
                    seq=start_index + len(blocks),
                )
            )
            table_rows = []

    def flush_code() -> None:
        nonlocal code_lines
        if code_lines:
            blocks.append(
                ParsedBlock(
                    BlockType.CODE,
                    "\n".join(code_lines),
                    page_no=page_no,
                    metadata={"language": "unknown"},
                    seq=start_index + len(blocks),
                )
            )
            code_lines = []

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if _CODE_INDENT.match(line) and stripped:
            flush_paragraph()
            flush_table()
            code_lines.append(line[4:] if line.startswith("    ") else line.lstrip("\t"))
            continue
        flush_code()

        if not stripped:
            flush_paragraph()
            flush_table()
            continue

        if detect_tables and _is_table_row(stripped):
            flush_paragraph()
            table_rows.append(table_columns(stripped))
            continue
        flush_table()

        if _MD_HEADING.match(stripped):
            flush_paragraph()
            blocks.append(
                ParsedBlock(
                    BlockType.TITLE,
                    _MD_HEADING.match(stripped).group(2).strip(),  # type: ignore[union-attr]
                    page_no=page_no,
                    level=_heading_level(stripped),
                    seq=start_index + len(blocks),
                )
            )
            continue

        if _BULLET_ITEM.match(stripped) or _ORDERED_ITEM.match(stripped):
            flush_paragraph()
            blocks.append(
                ParsedBlock(
                    BlockType.LIST,
                    stripped,
                    page_no=page_no,
                    seq=start_index + len(blocks),
                )
            )
            continue

        if _is_heading(stripped):
            flush_paragraph()
            blocks.append(
                ParsedBlock(
                    BlockType.TITLE,
                    stripped,
                    page_no=page_no,
                    level=_heading_level(stripped),
                    seq=start_index + len(blocks),
                )
            )
            continue

        paragraph.append(stripped)

    flush_paragraph()
    flush_table()
    flush_code()
    return blocks


def split_pages(text: str) -> list[str]:
    """OCR/纯文本没有分页信息时按换页符切分。"""
    return [page for page in text.split("\f") if page.strip()]
