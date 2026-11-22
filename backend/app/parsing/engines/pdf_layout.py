"""PDF 版面引擎：基于 pypdf 抽取文字层，按页还原标题/段落/表格，并识别页眉页脚。

扫描件没有文字层时不会静默返回空结果，而是标记 ``needs_ocr`` 并给出图片块，
由解析图决定是否改走 OCR 分支、由质量评分给出低质量原因。
"""

from __future__ import annotations

import io
from collections import Counter

from pypdf import PdfReader

from app.domain.enums import BlockType
from app.parsing.base import EngineResult, ParsedBlock, ParseEngine, ParseError, ParseInput
from app.parsing.registry import register_engine
from app.parsing.text_blocks import blocks_from_lines

MIN_TEXT_CHARS = 20
HEADER_FOOTER_RATIO = 0.5


def _repeated_edge_lines(pages: list[list[str]], *, edge: str) -> set[str]:
    """在多页首行/末行中重复出现的内容，视为页眉或页脚。"""
    if len(pages) < 3:
        return set()
    counter: Counter[str] = Counter()
    for lines in pages:
        if not lines:
            continue
        candidate = lines[0] if edge == "header" else lines[-1]
        candidate = candidate.strip()
        if candidate:
            counter[candidate] += 1
    threshold = max(2, int(len(pages) * HEADER_FOOTER_RATIO))
    return {text for text, count in counter.items() if count >= threshold}


@register_engine
class PdfLayoutEngine(ParseEngine):
    name = "pdf_layout"
    display_name = "PDF 版面"
    doc_types = ("pdf",)

    def parse(self, source: ParseInput) -> EngineResult:
        try:
            reader = PdfReader(io.BytesIO(source.data))
            raw_pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:  # pypdf 的异常类型很杂，统一转成解析失败
            raise ParseError(f"PDF 解析失败: {exc}") from exc

        remove_header = bool(source.config.get("remove_header", True))
        lines_per_page = [text.splitlines() for text in raw_pages]
        headers = _repeated_edge_lines(lines_per_page, edge="header")
        footers = _repeated_edge_lines(lines_per_page, edge="footer")

        blocks: list[ParsedBlock] = []
        total_chars = sum(len(text.strip()) for text in raw_pages)
        warnings: list[str] = []

        if total_chars < MIN_TEXT_CHARS:
            # 没有文字层：给出图片块占位，交给 OCR 分支或质量门禁
            for page_no in range(1, len(raw_pages) + 1):
                blocks.append(
                    ParsedBlock(
                        BlockType.IMAGE,
                        "",
                        page_no=page_no,
                        metadata={"reason": "no_text_layer"},
                        seq=len(blocks),
                    )
                )
            warnings.append("PDF 未检测到文字层，可能为扫描件")
            return EngineResult(
                blocks=blocks,
                warnings=warnings,
                stats={
                    "page_count": len(raw_pages),
                    "char_count": total_chars,
                    "needs_ocr": True,
                    "ocr_available": False,
                },
                needs_ocr=True,
            )

        for page_no, lines in enumerate(lines_per_page, start=1):
            if not lines:
                continue
            body = list(lines)
            first = body[0].strip() if body else ""
            last = body[-1].strip() if body else ""

            if first and first in headers:
                blocks.append(
                    ParsedBlock(
                        BlockType.HEADER,
                        first,
                        page_no=page_no,
                        is_discarded=remove_header,
                        seq=len(blocks),
                    )
                )
                body = body[1:]
            if body and last and last in footers:
                blocks.append(
                    ParsedBlock(
                        BlockType.FOOTER,
                        last,
                        page_no=page_no,
                        is_discarded=remove_header,
                        seq=len(blocks),
                    )
                )
                body = body[:-1]

            page_blocks = blocks_from_lines(body, page_no=page_no, start_index=len(blocks))
            # 每页开头若只有孤立的短行，PDF 里通常是页内小标题，保留原判
            blocks.extend(page_blocks)

        if not blocks:
            warnings.append("PDF 文字层存在但未提取到有效内容")

        return EngineResult(
            blocks=blocks,
            warnings=warnings,
            stats={
                "page_count": len(raw_pages),
                "char_count": total_chars,
                "needs_ocr": False,
                "header_footer_removed": remove_header,
            },
        )
