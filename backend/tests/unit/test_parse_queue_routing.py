"""队列路由：MinerU 任务必须进 parse.mineru，其余回默认队列。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.infra.db.models import Document, ParseStrategy, ParseTask
from app.workers.queues import MINERU_QUEUE, PARSE_QUEUE, queue_for_engine, resolve_parse_queue


class _FakeSession:
    """按模型类型返回预置对象，避免为纯路由测试拉起数据库。"""

    def __init__(self, values: dict[Any, Any]) -> None:
        self._values = values

    def get(self, model: Any, _key: Any) -> Any:
        return self._values.get(model)


def _session(doc_type: str | None, engine: str | None) -> _FakeSession:
    values: dict[Any, Any] = {}
    if doc_type is not None:
        values[ParseTask] = SimpleNamespace(document_id=uuid.uuid4(), strategy_id=uuid.uuid4())
        values[Document] = SimpleNamespace(doc_type=doc_type)
    if engine is not None:
        values[ParseStrategy] = SimpleNamespace(engine=engine)
    return _FakeSession(values)


def test_queue_for_engine() -> None:
    assert queue_for_engine("mineru") == MINERU_QUEUE
    assert queue_for_engine("pdf_layout") == PARSE_QUEUE


def test_mineru_strategy_routes_to_dedicated_queue() -> None:
    assert resolve_parse_queue(_session("pdf", "mineru"), str(uuid.uuid4())) == MINERU_QUEUE


def test_other_engines_stay_on_default_queue() -> None:
    assert resolve_parse_queue(_session("pdf", "pdf_layout"), str(uuid.uuid4())) == PARSE_QUEUE


def test_unsupported_mineru_falls_back_to_default_queue() -> None:
    # office 文档用 mineru 策略时图里会回落到 office 引擎，队列必须一致
    assert resolve_parse_queue(_session("office", "mineru"), str(uuid.uuid4())) == PARSE_QUEUE


def test_unknown_task_falls_back_to_default_queue() -> None:
    assert resolve_parse_queue(_session(None, None), str(uuid.uuid4())) == PARSE_QUEUE
    assert resolve_parse_queue(_session(None, None), "not-a-uuid") == PARSE_QUEUE


def test_dispatch_uses_resolved_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import parse_task_service as service
    from app.workers import tasks as tasks_module

    captured: dict[str, Any] = {}

    def fake_apply_async(*, args: list[str], queue: str | None = None, **_: Any) -> None:
        captured["queue"] = queue
        captured["args"] = args

    monkeypatch.setattr(service, "_queue_of", lambda task_id: MINERU_QUEUE)
    monkeypatch.setattr(tasks_module.run_parse_task, "apply_async", fake_apply_async)

    service._default_dispatch("task-1")

    assert captured["queue"] == MINERU_QUEUE
    assert captured["args"] == ["task-1"]
