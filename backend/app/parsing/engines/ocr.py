"""OCR 引擎：按策略开关决定是否调用文字识别。

当前版本未接入 OCR 提供者时不会假装成功，而是返回图片块并给出明确告警，
质量评分据此判定为"低质量（未启用文字识别）"，前端可提示人工复核。
"""

from __future__ import annotations

from app.domain.enums import BlockType
from app.parsing.base import EngineResult, ParsedBlock, ParseEngine, ParseInput
from app.parsing.registry import register_engine

SUPPORTED_PROVIDERS = {"none", ""}


@register_engine
class OcrEngine(ParseEngine):
    name = "ocr"
    display_name = "图片文字识别"
    doc_types = ("image", "pdf")

    def parse(self, source: ParseInput) -> EngineResult:
        provider = str(source.config.get("ocr_provider", "none")).lower()
        page_count = max(int(source.config.get("page_count", 1) or 1), 1)
        blocks = [
            ParsedBlock(
                BlockType.IMAGE,
                "",
                page_no=page_no,
                metadata={"reason": "no_ocr_provider", "provider": provider},
                seq=page_no - 1,
            )
            for page_no in range(1, page_count + 1)
        ]

        if provider not in SUPPORTED_PROVIDERS:
            return EngineResult(
                blocks=blocks,
                warnings=[f"OCR 提供者 {provider} 尚未接入，已保留原图待人工处理"],
                stats={"page_count": page_count, "ocr_available": False, "ocr_provider": provider},
            )

        return EngineResult(
            blocks=blocks,
            warnings=["未启用文字识别，图片内容无法提取"],
            stats={"page_count": page_count, "ocr_available": False, "ocr_provider": "none"},
        )
