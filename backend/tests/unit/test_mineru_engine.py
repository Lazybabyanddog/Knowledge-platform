"""MinerU 引擎的调用与映射行为：错误分类、产物映射、部分页失败。"""

from __future__ import annotations

from typing import Any, Callable

import httpx
import pytest

from app.domain.enums import BlockType
from app.parsing.base import MineruUnavailableError, ParseError, ParseInput
from app.parsing.engines import mineru as mineru_module
from app.parsing.engines.mineru import MineruEngine

SOURCE = ParseInput(
    data=b"%PDF-1.4 fake",
    file_name="policy.pdf",
    mime_type="application/pdf",
    doc_type="pdf",
)

CONTENT_LIST: list[dict[str, Any]] = [
    {"type": "title", "text": "第三章 归档", "text_level": 2, "page_idx": 2, "bbox": [1, 2, 3, 4]},
    {"type": "text", "text": "正文第一段。", "page_idx": 2},
    {"type": "list", "text": "- 项目一\n- 项目二", "page_idx": 2},
    {
        "type": "table",
        "table_body": "<table><tr><th>字段</th><th>要求</th></tr>"
        "<tr><td>期限</td><td>30 天</td></tr></table>",
        "page_idx": 3,
    },
    {"type": "interline_equation", "text": "$$E=mc^2$$", "page_idx": 3},
    {"type": "image", "img_path": "images/abc.jpg", "page_idx": 3},
    {"type": "text", "text": "   ", "page_idx": 3},
]

RESPONSE = {"policy.pdf": {"content_list": CONTENT_LIST, "failed_pages": [7]}}


def _install(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]) -> dict:
    """把引擎的 httpx 调用接到 MockTransport 上，并返回捕获的客户端参数。"""
    real_client = httpx.Client
    captured: dict[str, Any] = {}

    def _client(*args: Any, **kwargs: Any) -> httpx.Client:
        captured.update(kwargs)
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(mineru_module.httpx, "Client", _client)
    monkeypatch.setattr(mineru_module.settings, "mineru_base_url", "http://mineru.test")
    return captured


def _json_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=RESPONSE)


def test_missing_base_url_reports_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mineru_module.settings, "mineru_base_url", "")
    assert MineruEngine().config_problem()

    with pytest.raises(MineruUnavailableError):
        MineruEngine().parse(SOURCE)


def test_connect_error_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _install(monkeypatch, handler)
    with pytest.raises(MineruUnavailableError):
        MineruEngine().parse(SOURCE)


def test_timeout_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    _install(monkeypatch, handler)
    with pytest.raises(MineruUnavailableError):
        MineruEngine().parse(SOURCE)


def test_server_error_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda request: httpx.Response(503))
    with pytest.raises(MineruUnavailableError):
        MineruEngine().parse(SOURCE)


def test_rejected_file_is_not_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda request: httpx.Response(415))
    with pytest.raises(ParseError) as excinfo:
        MineruEngine().parse(SOURCE)
    assert not isinstance(excinfo.value, MineruUnavailableError)


def test_missing_content_list_is_parse_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda request: httpx.Response(200, json={"policy.pdf": {"md": "x"}}))
    with pytest.raises(ParseError):
        MineruEngine().parse(SOURCE)


def test_timeout_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mineru_module.settings, "mineru_timeout_seconds", 321)
    captured = _install(monkeypatch, _json_ok)

    MineruEngine().parse(SOURCE)

    assert captured["timeout"].read == 321


def test_maps_blocks_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _json_ok)
    result = MineruEngine().parse(SOURCE)

    types = [block.block_type for block in result.blocks]
    assert types == [
        BlockType.TITLE,
        BlockType.PARAGRAPH,
        BlockType.LIST,
        BlockType.TABLE,
        BlockType.PARAGRAPH,  # 公式以段落承载
        BlockType.IMAGE,
    ]
    assert [block.seq for block in result.blocks] == list(range(len(result.blocks)))

    title = result.blocks[0]
    assert title.level == 2
    assert title.page_no == 3
    assert title.bbox == {"x0": 1.0, "y0": 2.0, "x1": 3.0, "y1": 4.0}

    table = result.blocks[3]
    assert "| 字段 | 要求 |" in table.content
    assert "| --- | --- |" in table.content

    formula = result.blocks[4]
    assert formula.content == "$$E=mc^2$$"
    assert formula.metadata["kind"] == "formula"

    image = result.blocks[5]
    assert image.content == ""
    assert image.metadata["resource"] == "images/abc.jpg"


def test_partial_page_failure_keeps_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _json_ok)
    result = MineruEngine().parse(SOURCE)

    assert result.blocks
    assert any("7" in warning for warning in result.warnings)
    assert result.stats["failed_pages"] == 1
    assert result.stats["page_count"] == 4


def test_engine_registered_and_discoverable() -> None:
    from app.parsing.registry import engine_names, engines_for

    assert "mineru" in engine_names()
    assert "mineru" in [engine.name for engine in engines_for("pdf")]
    # 只声明了 pdf，别让 office 的策略误选到它
    assert "mineru" not in [engine.name for engine in engines_for("office")]
    assert MineruEngine.display_name


def test_settings_expose_mineru_fields() -> None:
    assert isinstance(mineru_module.settings.mineru_base_url, str)
    assert mineru_module.settings.mineru_timeout_seconds > 0
    assert mineru_module.settings.mineru_lang
    assert mineru_module.settings.mineru_backend

