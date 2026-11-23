"""任务恢复算法的单测：预算、退避、降参、错误分类与断点游标。

这些都只读配置与普通字段，不连库；集成测试覆盖"落到状态机上的效果"。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import settings
from app.core.exceptions import BadInputError
from app.domain.error_catalog import TaskErrorCode, classify_exception, describe_error
from app.infra.db.models import ExtractionBatchCheckpoint
from app.infra.llm import LLMPermanentError, LLMTransientError
from app.services.extraction_service import (
    ExtractionService,
    _fingerprint,
)
from app.services.task_recovery import (
    auto_retry_allowed,
    budget_of,
    downgrade_allowed,
    downgraded_batch_size,
    exhausted_spec,
    manual_revive_allowed,
    retry_delay,
)


def _task(**kwargs: Any) -> SimpleNamespace:
    base = {"attempt": 0, "auto_retries": 0, "downgrade_retries": 0, "manual_revives": 0}
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_budget_of_tolerates_null_columns() -> None:
    """老数据/回填缺失时按 0 计，不能因为 None 让整个恢复逻辑抛 TypeError。"""
    budget = budget_of(_task(attempt=None, auto_retries=None, manual_revives=None))
    assert (budget.attempt, budget.auto_retries, budget.manual_revives) == (0, 0, 0)


def test_budgets_are_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "task_auto_retry_max_attempts", 1)
    monkeypatch.setattr(settings, "task_downgrade_max_attempts", 1)
    monkeypatch.setattr(settings, "task_manual_revive_max_attempts", 1)

    budget = budget_of(_task(attempt=5, auto_retries=1, downgrade_retries=1, manual_revives=1))
    assert not auto_retry_allowed(budget)
    assert not downgrade_allowed(budget, supported=True)
    assert not manual_revive_allowed(budget)
    # 解析没有可降参数：不支持降参时即便预算为 0 也不允许
    assert not downgrade_allowed(budget_of(_task()), supported=False)


def test_auto_retry_zero_disables_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "task_auto_retry_max_attempts", 0)
    assert not auto_retry_allowed(budget_of(_task()))


def test_retry_delay_follows_backoff_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "task_auto_retry_backoff_seconds", [10, 20])
    assert retry_delay(0) == 10
    assert retry_delay(1) == 20
    assert retry_delay(9) == 20  # 超出配置长度沿用最后一个值


def test_retry_delay_empty_list_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "task_auto_retry_backoff_seconds", [])
    assert retry_delay(0) >= 1


def test_downgraded_batch_size_has_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "task_downgrade_batch_ratio", 0.5)
    monkeypatch.setattr(settings, "task_downgrade_batch_min", 2)
    assert downgraded_batch_size(8) == 4
    assert downgraded_batch_size(3) == 2
    assert downgraded_batch_size(2) == 2  # 不会因为降参把批大小放大


def test_classify_exception_maps_known_failures() -> None:
    assert classify_exception(LLMTransientError("read timeout")).code == "llm_unavailable"
    assert (
        classify_exception(LLMTransientError("timeout", error_code="llm_timeout")).code
        == "llm_timeout"
    )
    assert classify_exception(LLMPermanentError("401 unauthorized")).code == "llm_rejected"
    assert classify_exception(BadInputError("文件损坏")).code == "bad_input"
    assert classify_exception(RuntimeError("boom")).code == "internal_error"


def test_classify_exception_recognises_time_limit() -> None:
    """Celery 软超时的类名是 SoftTimeLimitExceeded，不含 timeout 字样。"""

    class SoftTimeLimitExceeded(Exception):
        pass

    assert classify_exception(SoftTimeLimitExceeded()).code == TaskErrorCode.TASK_TIMEOUT.value


def test_describe_error_keeps_unknown_code() -> None:
    payload = describe_error("vendor_specific_code")
    assert payload["code"] == "vendor_specific_code"
    assert payload["message"]  # 前端永远不会拿到空文案


def test_exhausted_spec_is_not_retryable() -> None:
    assert exhausted_spec().code == TaskErrorCode.ATTEMPTS_EXHAUSTED.value
    assert not exhausted_spec().retryable


def test_fingerprint_changes_with_batch_size_and_blocks() -> None:
    blocks = [{"id": "b1"}, {"id": "b2"}]
    base = _fingerprint(2, blocks)
    assert base == _fingerprint(2, blocks)  # 确定性
    assert base != _fingerprint(4, blocks)  # 批大小变
    assert base != _fingerprint(2, list(reversed(blocks)))  # 块序变


def _checkpoint(
    batch_index: int, block_ids: list[str] | None = None, status: str = "committed"
) -> Any:
    return ExtractionBatchCheckpoint(
        extraction_task_id=uuid.uuid4(),
        batch_index=batch_index,
        status=status,
        block_ids=[uuid.uuid5(uuid.NAMESPACE_OID, item) for item in (block_ids or [])],
        payload={},
        attempt_no=1,
    )


def test_covered_prefix_stops_at_first_uncovered_block() -> None:
    sliced = [{"id": str(uuid.uuid5(uuid.NAMESPACE_OID, key))} for key in ("a", "b", "c")]
    assert ExtractionService._covered_prefix(sliced, [_checkpoint(0, ["a", "b"])]) == 2
    # 只覆盖了第二批：第一批没做，前缀为 0
    assert ExtractionService._covered_prefix(sliced, [_checkpoint(1, ["b"])]) == 0


def test_first_missing_finds_first_gap() -> None:
    rows = [_checkpoint(0), _checkpoint(1), _checkpoint(3)]
    assert ExtractionService._first_missing(rows) == 2
    assert ExtractionService._first_missing([]) == 0
