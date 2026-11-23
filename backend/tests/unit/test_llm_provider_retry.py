"""模型调用重试：只重试抖动，不重试"被拒绝"。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.config import settings
from app.infra.llm import provider
from app.infra.llm.provider import LLMPermanentError, LLMTransientError


def _ok(content: str = '{"entities": []}') -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
        request=httpx.Request("POST", "http://llm.local/v1/chat/completions"),
    )


def _status(code: int) -> httpx.Response:
    return httpx.Response(
        code,
        json={"error": "boom"},
        request=httpx.Request("POST", "http://llm.local/v1/chat/completions"),
    )


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_base_url", "http://llm.local/v1")
    monkeypatch.setattr(settings, "llm_max_attempts", 3)
    monkeypatch.setattr(settings, "llm_retry_base_delay_seconds", 0.0)
    monkeypatch.setattr(settings, "llm_retry_jitter_ratio", 0.0)
    monkeypatch.setattr(provider.time, "sleep", lambda _seconds: None)


def _install(monkeypatch: pytest.MonkeyPatch, results: list[Any]) -> list[int]:
    calls: list[int] = []

    def fake_post(*_args: Any, **_kwargs: Any) -> httpx.Response:
        calls.append(len(calls))
        item = results[min(len(calls) - 1, len(results) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(provider.httpx, "post", fake_post)
    return calls


def test_retries_server_errors_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, [_status(500), _status(502), _ok()])
    assert provider.chat_json("sys", "user") == {"entities": []}
    assert len(calls) == 3


def test_rate_limit_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, [_status(429), _ok()])
    assert provider.chat_json("sys", "user") == {"entities": []}
    assert len(calls) == 2


def test_permanent_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, [_status(401)])
    with pytest.raises(LLMPermanentError):
        provider.chat_json("sys", "user")
    assert len(calls) == 1


def test_exhausted_retries_raise_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, [_status(500)])
    with pytest.raises(LLMTransientError) as excinfo:
        provider.chat_json("sys", "user")
    assert excinfo.value.task_error_code == "llm_unavailable"
    assert len(calls) == 3


def test_timeout_surfaces_as_llm_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [httpx.ConnectTimeout("read timeout")])
    with pytest.raises(LLMTransientError) as excinfo:
        provider.chat_json("sys", "user")
    assert excinfo.value.task_error_code == "llm_timeout"


def test_unconfigured_llm_is_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_api_key", "")
    calls = _install(monkeypatch, [_ok()])
    with pytest.raises(LLMPermanentError):
        provider.chat_json("sys", "user")
    assert calls == []
