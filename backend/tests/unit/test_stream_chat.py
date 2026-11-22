"""流式对话的解析逻辑单测：不联网，验证增量顺序与异常行跳过。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.core.config import settings
from app.infra.llm import provider

LINES = [
    "",
    'data: {"choices":[{"delta":{"content":"根据"}}]}',
    ": keep-alive",
    'data: {"choices":[{"delta":{"content":"资料"}}]}',
    'data: {"choices":[{"delta":{"content":"[1]"}}]}',
    "data: not-json",
    "data: [DONE]",
]


class _FakeStream:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def stream(self, *args: Any, **kwargs: Any) -> _FakeStream:
        assert kwargs["json"]["stream"] is True
        return _FakeStream(LINES)


def test_astream_chat_yields_deltas_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_base_url", "https://example.com/v1")
    monkeypatch.setattr(provider.httpx, "AsyncClient", _FakeClient)

    async def collect() -> list[str]:
        return [chunk async for chunk in provider.astream_chat([{"role": "user", "content": "hi"}])]

    assert asyncio.run(collect()) == ["根据", "资料", "[1]"]


def test_astream_chat_requires_llm_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_base_url", "")

    async def run() -> None:
        async for _ in provider.astream_chat([{"role": "user", "content": "hi"}]):
            pass

    with pytest.raises(provider.LLMError):
        asyncio.run(run())


def test_sse_payload_is_single_line_json() -> None:
    payload = {"text": "第一行\n第二行"}
    assert "\n" not in json.dumps(payload, ensure_ascii=False)


def test_event_stream_stops_and_closes_upstream_when_client_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """客户端断开后：不再产出 delta，也不发 done，并且关闭上游生成器。"""
    from app.api.v1.retrieval import _event_stream
    from app.services.retrieval_service import ChunkHit

    closed = {"value": False}

    async def _upstream() -> Any:
        try:
            yield "第一段"
            yield "第二段"
        finally:
            closed["value"] = True

    monkeypatch.setattr(provider, "astream_chat", lambda messages: _upstream())

    class _DisconnectingRequest:
        calls = 0

        async def is_disconnected(self) -> bool:
            _DisconnectingRequest.calls += 1
            # 第一次仍算在线（首段能出去），之后模拟断开
            return _DisconnectingRequest.calls > 1

    hit = ChunkHit(
        chunk_id="c1",
        document_id="d1",
        title="文档",
        version_no=1,
        content="片段",
        score=0.9,
        heading_path=None,
        first_page_no=1,
        last_page_no=1,
        block_ids=[],
    )

    async def collect() -> list[str]:
        return [
            frame
            async for frame in _event_stream(
                "问题", [hit], [{"index": 1}], _DisconnectingRequest(), 0.0
            )
        ]

    frames = asyncio.run(collect())

    assert any("event: sources" in frame for frame in frames)
    assert sum("event: delta" in frame for frame in frames) == 1
    assert not any("event: done" in frame for frame in frames)
    assert closed["value"] is True, "客户端断开后必须关闭上游流"
