"""HTML 结构解析引擎的单元测试（任务 2.1 / 2.2 / 2.3 / 2.4）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.enums import BlockType
from app.parsing.base import EngineResult, ParseError, ParseInput
from app.parsing.engines.html_dom import HtmlDomEngine
from app.parsing.registry import get_engine, has_engine

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "html" / "court_gazette.html"


def _parse(config: dict | None = None, data: bytes | None = None) -> EngineResult:
    return HtmlDomEngine().parse(
        ParseInput(
            data=FIXTURE.read_bytes() if data is None else data,
            file_name="court_gazette.html",
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


def test_default_parse_is_single_page_without_ocr() -> None:
    result = _parse()

    assert result.blocks
    assert {block.page_no for block in result.blocks} == {1}
    assert result.stats["page_count"] == 1
    assert result.stats["html_parser"] in {"lxml", "html.parser"}
    assert result.needs_ocr is False


def test_scripts_styles_and_tags_never_reach_blocks() -> None:
    result = _parse()

    joined = "\n".join(_texts(result))
    assert "SHOULD_NOT_APPEAR" not in joined
    assert "font-size" not in joined
    assert all("<" not in content for content in _texts(result))


def test_block_type_mapping() -> None:
    result = _parse()

    types = {block.block_type for block in result.blocks}
    assert {
        BlockType.TITLE,
        BlockType.PARAGRAPH,
        BlockType.LIST,
        BlockType.TABLE,
        BlockType.CODE,
    } <= types

    titles = [block for block in result.blocks if block.block_type == BlockType.TITLE]
    assert titles and titles[0].level == 1
    assert len(_texts(result, BlockType.LIST)) == 3

    tables = [block for block in result.blocks if block.block_type == BlockType.TABLE]
    assert len(tables) == 1
    assert tables[0].metadata["columns"] == ["项目", "金额"]
    assert tables[0].metadata["row_count"] == 3


def test_table_rendered_as_pipe_rows() -> None:
    result = _parse()
    table = next(block for block in result.blocks if block.block_type == BlockType.TABLE)

    lines = table.content.splitlines()
    assert lines[0] == "| 项目 | 金额 |"
    assert len(lines) == 3


def test_whitespace_and_inline_tags_are_normalized() -> None:
    result = _parse()
    paragraphs = _texts(result, BlockType.PARAGRAPH)

    resolved = next(text for text in paragraphs if text.startswith("经审理查明"))
    assert "\xa0" not in resolved
    assert "  " not in resolved

    inline = next(text for text in paragraphs if "本院认为" in text)
    assert "合资合同系双方真实意思" in inline


def test_title_class_detected_and_merged() -> None:
    result = _parse()

    titles = _texts(result, BlockType.TITLE)
    assert len(titles) == 1
    assert "沛时投资公司诉天津市金属工具公司" in titles[0]
    assert "中华人民共和国最高人民法院公报" in titles[0]


def test_title_merge_can_be_disabled() -> None:
    result = _parse({"merge_titles": False})

    titles = _texts(result, BlockType.TITLE)
    assert len(titles) == 3


def test_title_selector_promotes_matching_node() -> None:
    result = _parse({"title_selector": ".doc-type"})

    assert "民事判决书" in _texts(result, BlockType.TITLE)


def test_content_selector_limits_scope() -> None:
    result = _parse({"content_selector": "#gb_content"})

    joined = "\n".join(_texts(result))
    assert "首页 公报" not in joined
    assert "广告位" not in joined
    assert "版权所有" not in joined
    assert "沛时投资公司" in joined


def test_content_selector_miss_warns_and_falls_back() -> None:
    result = _parse({"content_selector": "#missing"})

    assert any("正文选择器未命中" in warning for warning in result.warnings)
    assert result.blocks


def test_invalid_selector_raises_parse_error() -> None:
    with pytest.raises(ParseError) as exc_info:
        _parse({"content_selector": "p[data-x="})

    assert "正文选择器非法" in str(exc_info.value)


def test_keep_images_controls_image_blocks() -> None:
    assert not _texts(_parse(), BlockType.IMAGE)

    result = _parse({"keep_images": True})
    images = [block for block in result.blocks if block.block_type == BlockType.IMAGE]
    assert len(images) == 1
    assert str(images[0].metadata["src"]).endswith("gongbao.png")


def test_drop_selectors_remove_configured_noise() -> None:
    result = _parse({"drop_selectors": [".ad"]})

    assert all("广告位" not in content for content in _texts(result))


def test_empty_body_returns_no_blocks_with_warning() -> None:
    data = b"<html><body><script>var a = 1;</script><style>.a{}</style></body></html>"
    result = _parse(data=data)

    assert result.blocks == []
    assert any("未从 HTML 中提取到有效正文" in warning for warning in result.warnings)


def test_gb18030_content_is_decoded() -> None:
    html = '<html><body><p class="c_content">中文正文编码测试</p></body></html>'
    result = _parse(data=html.encode("gb18030"))

    assert any("中文正文编码测试" in content for content in _texts(result))
    assert all("\ufffd" not in content for content in _texts(result))


def test_engine_registered_for_html_only() -> None:
    assert has_engine("html_dom")

    engine = get_engine("html_dom")
    assert engine.supports("html") is True
    assert engine.supports("text") is False
