"""僵尸任务看护：把"进程没了但状态卡在运行中"的任务收回来。

判定只看"存活信号"而不看任务跑了多久：worker 每完成一批会刷新执行记录的
``heartbeat_at``，超过 ``task_stale_threshold_seconds`` 没刷新才判死。这样
长任务不会被误杀，短任务也能尽快被发现。

处置三分支：
- 用户已取消 -> 保持取消（取消优先），只把执行记录收尾；
- 永久失败类别 -> 不重投，落失败终态；
- 自动重试预算未用尽 -> 回待执行并重投，来源记 ``watchdog``。

重排会占用一次自动重试预算，否则一个每次都被杀的批次会无限重排。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.enums import ExtractionTaskStatus, ParseTaskStatus
from app.domain.error_catalog import ErrorSpec, TaskErrorCode
from app.infra.cache.redis_client import get_redis
from app.infra.db.repositories import ParseTaskRepository
from app.infra.db.repositories.extraction_repository import ExtractionTaskRepository
from app.services.task_recovery import budget_of, error_detail

logger = logging.getLogger(__name__)

_WATCHDOG_SPEC = ErrorSpec(
    code=TaskErrorCode.TASK_TIMEOUT.value,
    message="执行超时",
    hint="执行者长时间没有上报存活信号，已由看护接管",
    retryable=True,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _is_stale(last_seen: datetime | None, now: datetime) -> bool:
    if last_seen is None:
        return True
    if last_seen.tzinfo is None:  # 防御：库里无时区列按 UTC 理解
        last_seen = last_seen.replace(tzinfo=UTC)
    return now - last_seen > timedelta(seconds=settings.task_stale_threshold_seconds)


def _release_lock(key: str) -> None:
    try:
        get_redis().delete(key)
    except Exception:  # noqa: BLE001 - 锁清理失败不影响重排，执行端还有 fencing
        logger.warning("看护清理运行锁失败 key=%s", key)


def _dispatch_parse(task_id: str) -> None:
    from app.workers.tasks import run_parse_task  # 延迟导入，避免循环

    run_parse_task.delay(task_id)


def _dispatch_extraction(task_id: str) -> None:
    from app.workers.tasks import run_extraction_task

    run_extraction_task.delay(task_id)


def _sweep_parse(db: Session, now: datetime) -> int:
    repo = ParseTaskRepository(db)
    recovered = 0
    for task in repo.list_running():
        attempt = repo.latest_attempt(task.id)
        last_seen = (attempt.heartbeat_at if attempt else None) or task.started_at
        if not _is_stale(last_seen, now):
            continue

        if attempt is not None and attempt.status == ParseTaskStatus.RUNNING.value:
            attempt.status = ParseTaskStatus.FAILED.value
            attempt.error_code = _WATCHDOG_SPEC.code
            attempt.error_message = "执行者存活信号超时，已由看护接管"
            attempt.error_detail = error_detail(
                None, stage="watchdog", spec=_WATCHDOG_SPEC
            )
            attempt.finished_at = now
            if attempt.started_at is not None:
                attempt.duration_ms = max(
                    0, int((now - attempt.started_at).total_seconds() * 1000)
                )
            attempt.heartbeat_at = None

        budget = budget_of(task)
        if task.status == ParseTaskStatus.CANCELLED.value:
            db.commit()
            continue
        if budget.auto_retries < settings.task_auto_retry_max_attempts:
            task.auto_retries = budget.auto_retries + 1
            task.status = ParseTaskStatus.PENDING.value
            task.next_source = "watchdog"
            task.finished_at = None
            db.commit()
            _release_lock(f"parse:run:{task.id}")
            _dispatch_parse(str(task.id))
            recovered += 1
            logger.warning("看护重排解析任务 task_id=%s", task.id)
        else:
            task.status = ParseTaskStatus.FAILED.value
            task.next_source = None
            task.error_code = _WATCHDOG_SPEC.code
            task.finished_at = now
            db.commit()
            logger.warning("看护判定解析任务失败（预算耗尽） task_id=%s", task.id)
    return recovered


def _sweep_extraction(db: Session, now: datetime) -> int:
    repo = ExtractionTaskRepository(db)
    recovered = 0
    for task in repo.list_running():
        attempt = repo.latest_attempt(task.id)
        last_seen = (attempt.heartbeat_at if attempt else None) or task.started_at
        if not _is_stale(last_seen, now):
            continue

        if attempt is not None and attempt.status == ExtractionTaskStatus.RUNNING.value:
            attempt.status = ExtractionTaskStatus.FAILED.value
            attempt.error_code = _WATCHDOG_SPEC.code
            attempt.error_message = "执行者存活信号超时，已由看护接管"
            attempt.error_detail = error_detail(
                None, stage="watchdog", spec=_WATCHDOG_SPEC
            )
            attempt.finished_at = now
            if attempt.started_at is not None:
                attempt.duration_ms = max(
                    0, int((now - attempt.started_at).total_seconds() * 1000)
                )
            attempt.heartbeat_at = None

        budget = budget_of(task)
        if task.status == ExtractionTaskStatus.CANCELLED.value:
            db.commit()
            continue
        if budget.auto_retries < settings.task_auto_retry_max_attempts:
            task.auto_retries = budget.auto_retries + 1
            task.status = ExtractionTaskStatus.PENDING.value
            task.next_source = "watchdog"
            task.finished_at = None
            db.commit()
            _release_lock(f"extract:run:{task.id}")
            _dispatch_extraction(str(task.id))
            recovered += 1
            logger.warning("看护重排抽取任务 task_id=%s", task.id)
        else:
            task.status = ExtractionTaskStatus.FAILED.value
            task.next_source = None
            task.error_code = _WATCHDOG_SPEC.code
            task.finished_at = now
            db.commit()
            logger.warning("看护判定抽取任务失败（预算耗尽） task_id=%s", task.id)
    return recovered


def sweep_stale_tasks(db: Session) -> dict[str, int]:
    """扫描一遍所有运行中的任务，返回本次处置计数（便于日志与测试断言）。"""
    if not settings.task_watchdog_enabled:
        return {"parse": 0, "extraction": 0}
    now = _now()
    return {
        "parse": _sweep_parse(db, now),
        "extraction": _sweep_extraction(db, now),
    }


__all__ = ["sweep_stale_tasks"]
