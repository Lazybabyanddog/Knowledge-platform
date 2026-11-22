"""状态机：所有状态迁移的唯一校验入口。

service 层不得自己写 if 判断状态是否可迁移，一律调用 ``ensure()``，
保证非法迁移统一抛 409（CONFLICT），错误码与提示稳定可测。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from app.core.exceptions import ConflictError
from app.domain import enums


@dataclass(frozen=True, slots=True)
class StateMachine:
    """表驱动的状态机：transitions 描述 当前态 -> 允许的下一态集合。"""

    name: str
    initial: str
    transitions: Mapping[str, frozenset[str]] = field(default_factory=dict)

    def allowed(self, current: str) -> frozenset[str]:
        return self.transitions.get(current, frozenset())

    def can(self, current: str, target: str) -> bool:
        return target in self.allowed(current)

    def is_terminal(self, current: str) -> bool:
        return not self.allowed(current)

    def ensure(self, current: str, target: str) -> None:
        """允许原地重复（幂等），拒绝未声明的迁移。"""
        if current == target:
            return
        if not self.can(current, target):
            raise ConflictError(
                f"{self.name} 不允许从 {current} 迁移到 {target}",
                data={
                    "current": current,
                    "target": target,
                    "allowed": sorted(self.allowed(current)),
                },
            )


def _fs(*values: str) -> frozenset[str]:
    return frozenset(values)


USER = StateMachine(
    name="user",
    initial=enums.UserStatus.ACTIVE.value,
    transitions={
        enums.UserStatus.ACTIVE.value: _fs(enums.UserStatus.DISABLED.value),
        enums.UserStatus.DISABLED.value: _fs(enums.UserStatus.ACTIVE.value),
    },
)

DOCUMENT = StateMachine(
    name="document",
    initial=enums.DocumentStatus.DRAFT.value,
    transitions={
        enums.DocumentStatus.DRAFT.value: _fs(enums.DocumentStatus.PENDING.value),
        enums.DocumentStatus.PENDING.value: _fs(
            enums.DocumentStatus.PUBLISHED.value,
            enums.DocumentStatus.REJECTED.value,
            enums.DocumentStatus.DRAFT.value,
        ),
        enums.DocumentStatus.REJECTED.value: _fs(enums.DocumentStatus.DRAFT.value),
        enums.DocumentStatus.PUBLISHED.value: _fs(enums.DocumentStatus.ARCHIVED.value),
        enums.DocumentStatus.ARCHIVED.value: _fs(enums.DocumentStatus.DRAFT.value),
    },
)

PARSE_TASK = StateMachine(
    name="parse_task",
    initial=enums.ParseTaskStatus.PENDING.value,
    transitions={
        enums.ParseTaskStatus.PENDING.value: _fs(
            enums.ParseTaskStatus.RUNNING.value,
            enums.ParseTaskStatus.CANCELLED.value,
        ),
        enums.ParseTaskStatus.RUNNING.value: _fs(
            enums.ParseTaskStatus.SUCCEEDED.value,
            enums.ParseTaskStatus.FAILED.value,
            enums.ParseTaskStatus.CANCELLED.value,
        ),
        enums.ParseTaskStatus.FAILED.value: _fs(enums.ParseTaskStatus.PENDING.value),
        enums.ParseTaskStatus.SUCCEEDED.value: _fs(),
        enums.ParseTaskStatus.CANCELLED.value: _fs(),
    },
)

EXTRACTION_TASK = StateMachine(
    name="extraction_task",
    initial=enums.ExtractionTaskStatus.PENDING.value,
    transitions={
        enums.ExtractionTaskStatus.PENDING.value: _fs(
            enums.ExtractionTaskStatus.RUNNING.value,
            enums.ExtractionTaskStatus.CANCELLED.value,
        ),
        enums.ExtractionTaskStatus.RUNNING.value: _fs(
            enums.ExtractionTaskStatus.SUCCEEDED.value,
            enums.ExtractionTaskStatus.FAILED.value,
            enums.ExtractionTaskStatus.CANCELLED.value,
        ),
        enums.ExtractionTaskStatus.FAILED.value: _fs(enums.ExtractionTaskStatus.PENDING.value),
        enums.ExtractionTaskStatus.SUCCEEDED.value: _fs(),
        enums.ExtractionTaskStatus.CANCELLED.value: _fs(),
    },
)

ENTITY = StateMachine(
    name="entity",
    initial=enums.EntityStatus.PROPOSED.value,
    transitions={
        enums.EntityStatus.PROPOSED.value: _fs(
            enums.EntityStatus.CONFIRMED.value,
            enums.EntityStatus.REJECTED.value,
            enums.EntityStatus.MERGED.value,
        ),
        enums.EntityStatus.MERGED.value: _fs(enums.EntityStatus.PROPOSED.value),
        enums.EntityStatus.CONFIRMED.value: _fs(),
        enums.EntityStatus.REJECTED.value: _fs(),
    },
)

RELATION = StateMachine(
    name="relation",
    initial=enums.EntityStatus.PROPOSED.value,
    transitions={
        enums.EntityStatus.PROPOSED.value: _fs(
            enums.EntityStatus.CONFIRMED.value,
            enums.EntityStatus.REJECTED.value,
        ),
        enums.EntityStatus.CONFIRMED.value: _fs(),
        enums.EntityStatus.REJECTED.value: _fs(),
    },
)

REVIEW_INSTANCE = StateMachine(
    name="review_instance",
    initial=enums.ReviewInstanceStatus.PENDING.value,
    transitions={
        enums.ReviewInstanceStatus.PENDING.value: _fs(
            enums.ReviewInstanceStatus.APPROVED.value,
            enums.ReviewInstanceStatus.REJECTED.value,
            enums.ReviewInstanceStatus.WITHDRAWN.value,
        ),
        enums.ReviewInstanceStatus.APPROVED.value: _fs(),
        enums.ReviewInstanceStatus.REJECTED.value: _fs(),
        enums.ReviewInstanceStatus.WITHDRAWN.value: _fs(),
    },
)

MACHINES: dict[str, StateMachine] = {
    machine.name: machine
    for machine in (
        USER,
        DOCUMENT,
        PARSE_TASK,
        EXTRACTION_TASK,
        ENTITY,
        RELATION,
        REVIEW_INSTANCE,
    )
}
