"""引擎选择规则：图节点与队列路由共用同一套判断。"""

from app.parsing import engines  # noqa: F401 - 导入即完成内置引擎注册
from app.parsing.routing import (
    fallback_engine_name,
    resolve_effective_engine,
    resolve_effective_engine_name,
)


def test_specified_and_supported_engine_wins() -> None:
    assert resolve_effective_engine_name("pdf", "pdf_layout") == "pdf_layout"


def test_specified_but_unsupported_engine_falls_back_to_type_default() -> None:
    # pdf_layout 只支持 pdf，用在 office 上必须回落 office
    assert resolve_effective_engine_name("office", "pdf_layout") == "office"


def test_unregistered_engine_falls_back_to_type_default() -> None:
    assert resolve_effective_engine_name("pdf", "not_registered") == "pdf_layout"


def test_missing_engine_uses_type_fallback() -> None:
    assert resolve_effective_engine_name("image", "") == "ocr"
    assert resolve_effective_engine_name("html", None) == "html_dom"
    assert resolve_effective_engine_name("text", None) == "plain_text"


def test_explicit_fallback_overrides_type_default() -> None:
    engine = resolve_effective_engine("pdf", "not_registered", "plain_text")
    assert engine.name == "plain_text"


def test_fallback_name_for_unknown_doc_type() -> None:
    assert fallback_engine_name("unknown") == "plain_text"
