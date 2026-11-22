"""审批超时计算与流程快照规整（任务 6.7）。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.review_service import business_days_between, normalize_steps


def _at(year: int, month: int, day: int, hour: int = 10) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def test_business_days_same_day_is_zero() -> None:
    start = _at(2026, 11, 18, 9)
    assert business_days_between(start, _at(2026, 11, 18, 18)) == 0


def test_business_days_skips_weekend() -> None:
    friday = _at(2026, 11, 20)
    monday = _at(2026, 11, 23)
    assert business_days_between(friday, monday) == 1


def test_business_days_counts_weekdays_only() -> None:
    # 2026-11-16 是周一，跨到 2026-11-23 周一，中间 5 个工作日
    assert business_days_between(_at(2026, 11, 16), _at(2026, 11, 23)) == 5


def test_normalize_steps_fills_step_no_in_order() -> None:
    steps = normalize_steps(
        [
            {"name": "内容审核", "approver_type": "role", "approver_value": "reviewer"},
            {"name": "管理员批准", "approver_type": "role", "approver_value": "admin"},
        ]
    )
    assert [step["step_no"] for step in steps] == [1, 2]
    assert steps[1]["name"] == "管理员批准"
    assert steps[0]["skippable"] is False
