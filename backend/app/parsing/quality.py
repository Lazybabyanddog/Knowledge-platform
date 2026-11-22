"""解析质量评分：把"能不能用"量化成 0-100，并给出可读原因。

评分只依赖内容块与引擎统计，不做二次模型调用，保证 worker 里可解释、可复现。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.domain.enums import BlockType
from app.parsing.base import ParsedBlock

GARBLED_THRESHOLD = 0.05
MIN_CHARS_PER_PAGE = 20


@dataclass(slots=True)
class QualityResult:
    score: int
    reasons: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    low_quality: bool = False


def _garbled_ratio(blocks: list[ParsedBlock]) -> float:
    total = 0
    garbled = 0
    for block in blocks:
        for char in block.content:
            total += 1
            if char == "\ufffd":
                garbled += 1
            elif unicodedata.category(char) in {"Cc", "Co", "Cn", "Cs"} and char not in "\n\t\r":
                garbled += 1
    return garbled / total if total else 0.0


def score_quality(
    blocks: list[ParsedBlock],
    stats: dict[str, Any] | None = None,
    *,
    threshold: int | None = None,
) -> QualityResult:
    stats = stats or {}
    threshold = settings.parse_quality_threshold if threshold is None else threshold
    effective = [block for block in blocks if not block.is_discarded]
    page_count = max(int(stats.get("page_count") or 1), 1)
    ocr_available = bool(stats.get("ocr_available", False))

    text_blocks = [block for block in effective if block.block_type != BlockType.IMAGE]
    text_chars = sum(len(block.content.strip()) for block in text_blocks)
    image_blocks = [block for block in effective if block.block_type == BlockType.IMAGE]
    garbled = _garbled_ratio(effective)

    reasons: list[str] = []
    if not effective:
        score = 0
        reasons.append("未提取到有效内容")
        if not ocr_available:
            reasons.append("未启用文字识别，图片内容无法提取")
    else:
        score = 100
        if text_chars / page_count < MIN_CHARS_PER_PAGE:
            score -= 30
            reasons.append("文本内容过少，可能为扫描件或图片")
        if garbled > GARBLED_THRESHOLD:
            score -= 25
            reasons.append("文本存在大量乱码，编码可能不兼容")
        if image_blocks and len(image_blocks) / len(effective) >= 0.5:
            score -= 40
            reasons.append(
                "内容以图片为主且未启用文字识别"
                if not ocr_available
                else "内容以图片为主，识别结果可能不完整"
            )
        if not text_blocks and not image_blocks:
            score -= 20
            reasons.append("内容块缺少可用文本")

    score = max(0, min(100, score))
    return QualityResult(
        score=score,
        reasons=reasons,
        stats={
            "text_chars": text_chars,
            "page_count": page_count,
            "effective_blocks": len(effective),
            "image_blocks": len(image_blocks),
            "garbled_ratio": round(garbled, 4),
            "ocr_available": ocr_available,
        },
        low_quality=score < threshold,
    )
