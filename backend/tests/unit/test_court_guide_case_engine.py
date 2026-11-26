"""法院指导案例解析引擎的单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.enums import BlockType
from app.parsing.base import EngineResult, ParseInput
from app.parsing.engines.court_guide_case import SECTION_LABELS, CourtGuideCaseEngine
from app.parsing.registry import engine_names, get_engine, has_engine

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "html" / "court_judgment.html"


def _parse(config: dict | None = None, data: bytes | None = None) -> EngineResult:
    return CourtGuideCaseEngine().parse(
        ParseInput(
            data=FIXTURE.read_bytes() if data is None else data,
            file_name="court_judgment.html",
            mime_type="text/html",
            doc_type="html",
            config=config or {},
        )
    )


def _texts(result: EngineResult, block_type: BlockType | None = None) -> list[str]:
    return [
        block.content
        for block in result.blocks
        if block_type is None or block.block_type == block_type
    ]


def test_engine_is_registered() -> None:
    assert has_engine("court_guide_case")
    assert "court_guide_case" in engine_names()
    engine = get_engine("court_guide_case")
    assert engine.display_name == "法院指导案例解析"
    assert engine.supports("html") is True
    assert engine.supports("pdf") is False


def test_case_metadata_extracted() -> None:
    result = _parse()

    assert result.stats["case_title"] == "沛时投资公司诉天津市金属工具公司中外合资合同纠纷上诉案"
    assert result.stats["case_no"] == "（2002）民四终字第3号"
    assert result.stats["court"] == "最高人民法院"
    assert result.stats["document_kind"] == "民事判决书"
    assert result.stats["judges"] == ["俞灵雨", "高晓力"]
    assert result.stats["clerks"] == ["杨弘磊"]
    assert result.stats["judgment_date"] == "二〇〇三年六月二十日"
    assert result.stats["page_count"] == 1
    assert result.needs_ocr is False


def test_title_is_merged_and_level_one() -> None:
    result = _parse()

    title = result.blocks[0]
    assert title.block_type == BlockType.TITLE
    assert title.level == 1
    assert title.metadata["section"] == "case_title"
    assert " " not in title.content
    assert "判决书" not in title.content


def test_sections_are_detected_in_order() -> None:
    result = _parse()

    assert result.stats["case_sections"] == [
        "parties",
        "procedure",
        "facts",
        "claims",
        "original",
        "appeal",
        "reply",
        "reasoning",
        "result",
        "signature",
        "profile",
    ]
    headings = [block.content for block in result.blocks if block.metadata.get("synthetic")]
    assert headings == [SECTION_LABELS[code] for code in result.stats["case_sections"]]
    assert result.stats["section_heading_count"] == len(headings)


def test_paragraphs_carry_section_metadata() -> None:
    result = _parse()

    by_section: dict[str, list[str]] = {}
    for block in result.blocks:
        if block.block_type == BlockType.PARAGRAPH:
            by_section.setdefault(block.metadata["section"], []).append(block.content)

    assert by_section["parties"][0].startswith("上诉人（原审被告）：")
    assert by_section["result"][0].endswith("判决如下：")
    assert by_section["result"][1:] == [
        "驳回上诉，维持原判。",
        "二审案件受理费30010元，由上诉人负担。",
        "本判决为终审判决。",
    ]
    assert by_section["signature"][0] == "审判长 俞灵雨"


def test_signature_roles_are_marked() -> None:
    result = _parse()

    roles = [
        block.metadata["role"]
        for block in result.blocks
        if block.block_type == BlockType.PARAGRAPH and "role" in block.metadata
    ]
    assert roles == ["judge", "judge", "judgment_date", "clerk"]


def test_page_noise_never_reaches_blocks() -> None:
    result = _parse()

    joined = "\n".join(_texts(result))
    assert "SHOULD_NOT_APPEAR" not in joined
    assert "京ICP" not in joined
    assert "版权" not in joined
    assert "首页" not in joined
    assert all(block.page_no == 1 for block in result.blocks)


def test_headings_can_be_disabled() -> None:
    result = _parse({"emit_section_headings": False})

    assert not any(block.metadata.get("synthetic") for block in result.blocks)
    assert result.stats["case_sections"][0] == "parties"
    paragraphs = [
        block.content for block in result.blocks if block.block_type == BlockType.PARAGRAPH
    ]
    assert paragraphs[0] == "（2002）民四终字第3号"
    assert any(text.startswith("上诉人（原审被告）：") for text in paragraphs)


def test_signature_can_be_dropped() -> None:
    result = _parse({"keep_signature": False})

    assert result.stats["case_sections"][-1] == "profile"
    assert not any(
        block.metadata.get("section") == "signature" for block in result.blocks
    )


def test_missing_container_falls_back_to_body() -> None:
    html = "<html><body><p>（2020）京01民终1号</p><p>本院认为，原判正确。</p></body></html>"
    result = _parse(data=html.encode())

    assert any("正文选择器未命中" in warning for warning in result.warnings)
    assert result.stats["case_no"] == "（2020）京01民终1号"
    assert result.stats["case_sections"] == ["reasoning"]


def test_empty_html_is_low_signal() -> None:
    result = _parse(data=b"<html><body></body></html>")

    assert result.blocks == []
    assert "未识别到案例标题" in result.warnings
    assert "未识别到案号" in result.warnings
    assert "未从 HTML 中提取到有效正文" in result.warnings


@pytest.mark.parametrize(
    ("raw", "title", "court", "kind"),
    [
        (
            "沛时投资公司诉 天津市金属工具公司中外合资合同纠纷上诉案 最高人民法院民事判决书",
            "沛时投资公司诉天津市金属工具公司中外合资合同纠纷上诉案",
            "最高人民法院",
            "民事判决书",
        ),
        (
            "甲公司诉乙公司买卖合同纠纷案 中华人民共和国最高人民法院公报",
            "甲公司诉乙公司买卖合同纠纷案",
            "最高人民法院",
            None,
        ),
    ],
)
def test_title_splitting(raw: str, title: str, court: str | None, kind: str | None) -> None:
    from app.parsing.engines.court_guide_case import _split_title

    parsed_title, parsed_court, parsed_kind, _source = _split_title(raw)

    assert parsed_title == title
    assert parsed_court == court
    assert parsed_kind == kind
