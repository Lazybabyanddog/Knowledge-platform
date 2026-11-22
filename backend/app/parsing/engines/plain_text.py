"""纯文本引擎：txt / md / csv / html 等可以直接当文本读的文件。"""

from __future__ import annotations

from app.parsing.base import EngineResult, ParsedBlock, ParseEngine, ParseInput
from app.parsing.registry import register_engine
from app.parsing.text_blocks import blocks_from_lines, decode_text


@register_engine
class PlainTextEngine(ParseEngine):
    name = "plain_text"
    display_name = "纯文本"
    doc_types = ("text",)

    def parse(self, source: ParseInput) -> EngineResult:
        text = decode_text(source.data)
        pages = [page for page in text.split("\f")]
        blocks: list[ParsedBlock] = []
        for page_no, page in enumerate(pages, start=1):
            blocks.extend(
                blocks_from_lines(page.splitlines(), page_no=page_no, start_index=len(blocks))
            )
        return EngineResult(
            blocks=blocks,
            stats={
                "page_count": len(pages),
                "char_count": len(text),
                "encoding": "text",
            },
        )
