"""任务恢复的公共算法：预算、退避、降参、错误快照、心跳。

解析与抽取的执行路径同构，把"该不该重试、退避多久、降到多小"集中在这里，
两个服务只负责搬运状态。所有函数都只读配置与任务字段，方便单测。
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.domain.error_catalog import ErrorSpec, TaskErrorCode, classify_exception, error_spec

HEARTBEAT_KEY_PREFIX = "aip:worker:"
ERROR_MESSAGE_LIMIT = 2000


@dataclass(frozen=True)
class Budget:
    """四类计数：attempt 是执行序号，其余三个各管一类来源。"""

    attempt: int
    auto_retries: int
    downgrade_retries: int
    manual_revives: int


def budget_of(task: Any) -> Budget:
    return Budget(
        attempt=int(getattr(task, "attempt", 0) or 0),
        auto_retries=int(getattr(task, "auto_retries", 0) or 0),
        downgrade_retries=int(getattr(task, "downgrade_retries", 0) or 0),
        manual_revives=int(getattr(task, "manual_revives", 0) or 0),
    )


def auto_retry_allowed(budget: Budget) -> bool:
    return budget.auto_retries < max(0, settings.task_auto_retry_max_attempts)


def downgrade_allowed(budget: Budget, *, supported: bool) -> bool:
    return supported and budget.downgrade_retries < max(
        0, settings.task_downgrade_max_attempts
    )


def manual_revive_allowed(budget: Budget) -> bool:
    return budget.manual_revives < max(0, settings.task_manual_revive_max_attempts)


def retry_delay(auto_retries: int) -> int:
    """按已用次数取退避秒数；超出配置长度时沿用最后一个值。"""
    backoff = list(settings.task_auto_retry_backoff_seconds or [30, 120, 300])
    if not backoff:
        return 30
    index = min(max(auto_retries, 0), len(backoff) - 1)
    return int(backoff[index])


def downgraded_batch_size(size: int) -> int:
    """降参只降低单批规模：下限保护避免退化成"一批一个块"把调用次数放大。"""
    target = int(size * settings.task_downgrade_batch_ratio)
    return max(settings.task_downgrade_batch_min, min(size, target))


def error_detail(
    exc: BaseException | None,
    *,
    stage: str,
    batch_index: int | None = None,
    block_seq: list[int] | None = None,
    call_attempts: int = 0,
    last_call_ms: int | None = None,
    spec: ErrorSpec | None = None,
) -> dict[str, Any]:
    """只放能定位问题的字段，不放堆栈；前端按这些字段渲染"卡在哪"。"""
    resolved = spec or (classify_exception(exc) if exc is not None else None)
    detail: dict[str, Any] = {
        "stage": stage,
        "cause": resolved.code if resolved else TaskErrorCode.INTERNAL_ERROR.value,
        "retryable": bool(resolved.retryable) if resolved else False,
        "call_attempts": int(call_attempts),
    }
    if batch_index is not None:
        detail["batch_index"] = int(batch_index)
    if block_seq:
        detail["block_seq"] = [int(item) for item in block_seq]
    if last_call_ms is not None:
        detail["last_call_ms"] = int(last_call_ms)
    if exc is not None:
        detail["raw"] = str(exc)[:ERROR_MESSAGE_LIMIT]
    return detail


def failure_spec(exc: BaseException) -> ErrorSpec:
    return classify_exception(exc)


def exhausted_spec() -> ErrorSpec:
    return error_spec(TaskErrorCode.ATTEMPTS_EXHAUSTED.value)


def timeout_spec() -> ErrorSpec:
    return error_spec(TaskErrorCode.TASK_TIMEOUT.value)


def worker_name() -> str:
    """worker 标识：进程名 + 主机名，够定位到具体容器。"""
    return f"{socket.gethostname()}:{os.getpid()}"


def heartbeat_key(name: str) -> str:
    return f"{HEARTBEAT_KEY_PREFIX}{name}"


__all__ = [
    "Budget",
    "auto_retry_allowed",
    "budget_of",
    "downgrade_allowed",
    "downgraded_batch_size",
    "error_detail",
    "exhausted_spec",
    "failure_spec",
    "heartbeat_key",
    "manual_revive_allowed",
    "retry_delay",
    "timeout_spec",
    "worker_name",
]
