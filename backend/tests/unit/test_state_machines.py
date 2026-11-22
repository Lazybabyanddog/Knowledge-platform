"""状态机合法/非法迁移的单元测试（任务 2.1）。"""

from __future__ import annotations

import pytest

from app.core.exceptions import ConflictError
from app.domain import enums, state_machines


def test_user_state_machine() -> None:
    state_machines.USER.ensure(enums.UserStatus.ACTIVE, enums.UserStatus.DISABLED)
    state_machines.USER.ensure(enums.UserStatus.DISABLED, enums.UserStatus.ACTIVE)

    with pytest.raises(ConflictError):
        # 自身到自身是幂等，不算非法；这里用一个未声明的目标态
        state_machines.USER.ensure(enums.UserStatus.ACTIVE, "deleted")


def test_document_allows_declared_paths() -> None:
    machine = state_machines.DOCUMENT
    machine.ensure(enums.DocumentStatus.DRAFT, enums.DocumentStatus.PENDING)
    machine.ensure(enums.DocumentStatus.PENDING, enums.DocumentStatus.PUBLISHED)
    machine.ensure(enums.DocumentStatus.PUBLISHED, enums.DocumentStatus.ARCHIVED)
    machine.ensure(enums.DocumentStatus.ARCHIVED, enums.DocumentStatus.DRAFT)


def test_document_rejects_skipping_review() -> None:
    with pytest.raises(ConflictError) as excinfo:
        state_machines.DOCUMENT.ensure(enums.DocumentStatus.DRAFT, enums.DocumentStatus.PUBLISHED)

    assert excinfo.value.http_status == 409
    assert excinfo.value.data["current"] == "draft"


def test_parse_task_retry_cycle() -> None:
    machine = state_machines.PARSE_TASK
    machine.ensure(enums.ParseTaskStatus.PENDING, enums.ParseTaskStatus.RUNNING)
    machine.ensure(enums.ParseTaskStatus.RUNNING, enums.ParseTaskStatus.FAILED)
    machine.ensure(enums.ParseTaskStatus.FAILED, enums.ParseTaskStatus.PENDING)


def test_succeeded_is_terminal() -> None:
    machine = state_machines.PARSE_TASK
    assert machine.is_terminal(enums.ParseTaskStatus.SUCCEEDED)

    with pytest.raises(ConflictError):
        machine.ensure(enums.ParseTaskStatus.SUCCEEDED, enums.ParseTaskStatus.RUNNING)


def test_entity_merge_and_undo() -> None:
    machine = state_machines.ENTITY
    machine.ensure(enums.EntityStatus.PROPOSED, enums.EntityStatus.MERGED)
    machine.ensure(enums.EntityStatus.MERGED, enums.EntityStatus.PROPOSED)


def test_entity_cannot_merge_after_confirmed() -> None:
    with pytest.raises(ConflictError):
        state_machines.ENTITY.ensure(enums.EntityStatus.CONFIRMED, enums.EntityStatus.MERGED)


def test_review_instance_terminal_states() -> None:
    machine = state_machines.REVIEW_INSTANCE
    machine.ensure(enums.ReviewInstanceStatus.PENDING, enums.ReviewInstanceStatus.APPROVED)

    with pytest.raises(ConflictError):
        machine.ensure(enums.ReviewInstanceStatus.APPROVED, enums.ReviewInstanceStatus.REJECTED)


def test_idempotent_same_state_is_allowed() -> None:
    state_machines.DOCUMENT.ensure(enums.DocumentStatus.DRAFT, enums.DocumentStatus.DRAFT)


def test_every_machine_is_registered() -> None:
    assert set(state_machines.MACHINES) == {
        "user",
        "document",
        "parse_task",
        "extraction_task",
        "entity",
        "relation",
        "review_instance",
    }
