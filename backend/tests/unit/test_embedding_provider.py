"""embedding 客户端的单测：不真正发请求，验证分批、维度校验与重试。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.config import settings
from app.infra.llm import provider


class _Response:
    def __init__(self, body: dict[str, Any], status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom", request=httpx.Request("POST", "http://test"), response=None  # type: ignore[arg-type]
            )

    def json(self) -> dict[str, Any]:
        return self._body


def _embedding_body(vectors: list[list[float]]) -> dict[str, Any]:
    return {
        "data": [
            {"index": index, "embedding": vector} for index, vector in enumerate(vectors)
        ]
    }


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    monkeypatch.setattr(settings, "embedding_base_url", "https://example.com/v1")
    monkeypatch.setattr(settings, "embedding_dim", 1024)
    monkeypatch.setattr(settings, "embedding_batch_size", 10)


def test_embeds_in_batches_and_keeps_order(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_post(
        url: str, json: dict[str, Any], headers: dict[str, str], timeout: int
    ) -> _Response:
        calls.append(list(json["input"]))
        assert json["dimensions"] == 1024
        assert url == "https://example.com/v1/embeddings"
        return _Response(_embedding_body([[0.0] * 1024 for _ in json["input"]]))

    monkeypatch.setattr(provider.httpx, "post", fake_post)

    vectors = provider.embed_texts([f"text-{index}" for index in range(25)])

    assert len(vectors) == 25
    assert [len(batch) for batch in calls] == [10, 10, 5]
    assert calls[0] == [f"text-{index}" for index in range(10)]


def test_dimension_mismatch_raises_without_retry(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def fake_post(
        url: str, json: dict[str, Any], headers: dict[str, str], timeout: int
    ) -> _Response:
        calls.append(len(json["input"]))
        return _Response(_embedding_body([[0.0] * 8 for _ in json["input"]]))

    monkeypatch.setattr(provider.httpx, "post", fake_post)

    with pytest.raises(provider.EmbeddingDimensionError):
        provider.embed_texts(["only one"])

    assert len(calls) == 1


def test_transport_error_is_retried_then_raises(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(provider.time, "sleep", lambda _seconds: None)

    def fake_post(
        url: str, json: dict[str, Any], headers: dict[str, str], timeout: int
    ) -> _Response:
        calls.append(1)
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(provider.httpx, "post", fake_post)

    with pytest.raises(provider.EmbeddingError):
        provider.embed_texts(["only one"])

    assert len(calls) == 3


def test_unconfigured_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "embedding_api_key", "")
    monkeypatch.setattr(settings, "embedding_base_url", "")

    assert provider.is_embedding_configured() is False
    with pytest.raises(provider.EmbeddingError):
        provider.embed_texts(["anything"])
