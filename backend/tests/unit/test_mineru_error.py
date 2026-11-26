"""MinerU 服务故障的错误码契约：可重试，且不改动既有 parse_error 行为。"""

from app.domain.error_catalog import (
    TaskErrorCode,
    classify_exception,
    describe_error,
    is_retryable,
)
from app.parsing.base import MineruUnavailableError, ParseError


def test_mineru_error_classifies_to_dedicated_code() -> None:
    spec = classify_exception(MineruUnavailableError("connect refused"))
    assert spec.code == TaskErrorCode.MINERU_UNAVAILABLE.value
    assert spec.retryable is True


def test_mineru_code_is_retryable() -> None:
    assert is_retryable(TaskErrorCode.MINERU_UNAVAILABLE.value) is True


def test_parse_error_stays_non_retryable() -> None:
    spec = classify_exception(ParseError("bad structure"))
    assert spec.code == TaskErrorCode.PARSE_ERROR.value
    assert spec.retryable is False
    assert is_retryable(TaskErrorCode.PARSE_ERROR.value) is False


def test_describe_error_exposes_hint() -> None:
    described = describe_error(TaskErrorCode.MINERU_UNAVAILABLE.value)
    assert described["message"]
    assert described["hint"]
    assert described["retryable"] is True
