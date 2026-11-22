"""解析图与引擎的单元测试（任务 4.3 / 4.4 / 4.5 / 4.9）。"""

from __future__ import annotations

from pathlib import Path

from app.domain.enums import BlockType
from app.parsing.base import EngineResult, ParsedBlock, ParseEngine, ParseInput
from app.parsing.registry import has_engine, register_engine
from app.parsing.text_blocks import detect_doc_type
from app.pipelines.parse.graph import run_parse_pipeline
from tests.helpers.pdf_factory import make_scanned_pdf, make_text_pdf

HTML_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "html" / "court_gazette.html"


def test_plain_text_pipeline_extracts_blocks() -> None:
    content = (
        "公司管理制度\n\n"
        "# 第一章 总则\n"
        "为规范公司内部管理，保障员工权益，特制定本制度。全体员工应当遵守。\n\n"
        "- 第一条 按时打卡\n"
        "- 第二条 请假提前申请\n"
    ).encode()

    state = run_parse_pipeline(data=content, file_name="制度.md", mime_type="text/markdown")

    blocks = state["blocks"]
    assert blocks, "纯文本应至少产出一个块"
    assert any(block.block_type == BlockType.TITLE for block in blocks)
    assert any(block.content.startswith("为规范公司") for block in blocks)
    assert [block.seq for block in blocks] == list(range(len(blocks)))
    assert state["quality"]["score"] >= 60
    assert state["quality"]["low_quality"] is False


def test_markdown_table_becomes_table_block() -> None:
    content = "人员统计\n\n| 部门 | 人数 |\n| 研发 | 12 |\n| 市场 | 5 |\n".encode()

    state = run_parse_pipeline(data=content, file_name="统计.md", mime_type="text/markdown")

    tables = [block for block in state["blocks"] if block.block_type == BlockType.TABLE]
    assert len(tables) == 1
    assert tables[0].content.splitlines()[0] == "| 部门 | 人数 |"
    assert tables[0].metadata["row_count"] == 3


def test_pdf_layout_pipeline_runs_per_page() -> None:
    state = run_parse_pipeline(
        data=make_text_pdf(),
        file_name="年报.pdf",
        mime_type="application/pdf",
        engine="pdf_layout",
    )

    blocks = state["blocks"]
    assert blocks
    assert {block.page_no for block in blocks} == {1, 2, 3}
    assert state["stats"]["page_count"] == 3
    assert any("Annual Report" in block.content for block in blocks)
    assert state["quality"]["score"] >= 60


def test_scanned_pdf_marks_ocr_needed_and_low_quality() -> None:
    state = run_parse_pipeline(
        data=make_scanned_pdf(),
        file_name="扫描件.pdf",
        mime_type="application/pdf",
        config={"ocr_enabled": False},
    )

    assert state["stats"]["needs_ocr"] is True
    assert all(block.block_type == BlockType.IMAGE for block in state["blocks"])
    assert state["quality"]["score"] < 60
    assert state["quality"]["low_quality"] is True
    assert any("未启用文字识别" in reason for reason in state["quality"]["reasons"])


def test_image_pipeline_without_ocr_is_low_quality() -> None:
    state = run_parse_pipeline(
        data=b"\x89PNG\r\n\x1a\n" + b"0" * 64,
        file_name="票据.png",
        mime_type="image/png",
        engine="ocr",
    )

    assert state["blocks"]
    assert state["quality"]["score"] < 60
    assert any("未启用文字识别" in warning for warning in state["warnings"])


def test_dummy_engine_can_be_registered_and_used() -> None:
    class DummyEngine(ParseEngine):
        name = "dummy"
        display_name = "测试引擎"
        doc_types = ("text",)

        def parse(self, source: ParseInput) -> EngineResult:
            return EngineResult(
                blocks=[ParsedBlock(BlockType.PARAGRAPH, "dummy engine output", seq=0)],
                stats={"page_count": 1, "char_count": 19},
            )

    register_engine(DummyEngine)
    assert has_engine("dummy")

    state = run_parse_pipeline(
        data=b"whatever",
        file_name="a.txt",
        mime_type="text/plain",
        engine="dummy",
    )

    assert [block.content for block in state["blocks"]] == ["dummy engine output"]
    assert state["stats"]["engine"] == "dummy"


def test_detect_doc_type_marks_html_separately() -> None:
    assert detect_doc_type("公报.html", "text/html") == "html"
    assert detect_doc_type("制度.txt", "text/plain") == "text"
    assert detect_doc_type("制度.md", "text/markdown") == "text"
    assert detect_doc_type("年报.pdf", "application/pdf") == "pdf"


def test_html_pipeline_routes_to_html_engine() -> None:
    state = run_parse_pipeline(
        data=HTML_FIXTURE.read_bytes(),
        file_name="court_gazette.html",
        mime_type="text/html",
    )

    assert state["doc_type"] == "html"
    assert state["stats"]["engine"] == "html_dom"
    assert state["stats"]["page_count"] == 1
    assert {block.page_no for block in state["blocks"]} == {1}
    assert any(block.block_type == BlockType.TITLE for block in state["blocks"])
    assert not any("<" in block.content for block in state["blocks"])
    assert state["quality"]["low_quality"] is False


def test_html_pipeline_does_not_fall_back_to_plain_text() -> None:
    state = run_parse_pipeline(
        data=HTML_FIXTURE.read_bytes(),
        file_name="court_gazette.html",
        mime_type="text/html",
        engine="plain_text",
        config={"content_selector": "#gb_content"},
    )

    assert state["stats"]["engine"] == "html_dom"
    assert all("首页 公报" not in block.content for block in state["blocks"])


def test_html_header_and_footer_are_discarded() -> None:
    state = run_parse_pipeline(
        data=HTML_FIXTURE.read_bytes(),
        file_name="court_gazette.html",
        mime_type="text/html",
        config={"remove_header": True},
    )

    frames = [
        block
        for block in state["blocks"]
        if block.block_type in {BlockType.HEADER, BlockType.FOOTER}
    ]
    assert frames
    assert all(block.is_discarded for block in frames)
    assert not any(
        "版权所有" in block.content for block in state["blocks"] if not block.is_discarded
    )
