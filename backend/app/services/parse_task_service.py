"""解析任务服务：触发、执行、重试、取消与产物查询。

执行路径上的关键约定：
- 只有 ``pending`` 才能被执行，重复投递同一任务直接跳过（幂等）；
- 执行前抢占 Redis 锁，避免同一任务被两个 worker 同时处理；
- 每次执行写一条 attempt，失败历史只增不改；
- 产物按 ``(task_id, seq)`` 唯一，重试前先清旧产物再写；
- 预算分层与抽取一致：``attempt`` 只作执行序号，自动/降参/人工三类计数独立；
- 解析没有可切分的模型调用，因此既不做批断点，也不占用降参预算。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import Principal
from app.core.exceptions import ConflictError, NotFoundError
from app.domain import state_machines
from app.domain.enums import AuditAction, ParseTaskStatus
from app.domain.error_catalog import TaskErrorCode, classify_exception, describe_error
from app.infra.cache.redis_client import get_redis
from app.infra.db.models import (
    Document,
    DocumentVersion,
    ParseBlock,
    ParseStrategy,
    ParseTask,
    ParseTaskAttempt,
)
from app.infra.db.repositories import (
    ParseBlockRepository,
    ParseTaskRepository,
)
from app.infra.storage import get_storage
from app.parsing.base import ParseError
from app.parsing.text_blocks import detect_doc_type
from app.pipelines.parse.graph import run_parse_pipeline
from app.schemas.parse import (
    ParseAttemptItem,
    ParseBlockItem,
    ParseBlockListResponse,
    ParseTaskCreateRequest,
    ParseTaskDetail,
    ParseTaskItem,
    ParseTaskListResponse,
    ParseTaskQuery,
)
from app.schemas.task import TaskActions, TaskErrorInfo
from app.services.audit_service import AuditService
from app.services.index_service import reset_version_index
from app.services.parse_strategy_service import ParseStrategyService
from app.services.task_recovery import (
    auto_retry_allowed,
    budget_of,
    error_detail,
    heartbeat_key,
    manual_revive_allowed,
    retry_delay,
    worker_name,
)

logger = logging.getLogger(__name__)

ERROR_MESSAGE_LIMIT = 2000


def _now() -> datetime:
    return datetime.now(UTC)


def _default_dispatch(task_id: str) -> None:
    from app.workers.tasks import run_parse_task  # 延迟导入，避免 service <-> worker 循环

    run_parse_task.delay(task_id)


def _default_dispatch_delayed(task_id: str, countdown: int) -> None:
    from app.workers.tasks import run_parse_task

    run_parse_task.apply_async(args=[task_id], countdown=countdown)


_dispatcher: Callable[[str], None] = _default_dispatch
_delayed_dispatcher: Callable[[str, int], None] = _default_dispatch_delayed


def set_dispatcher(
    dispatcher: Callable[[str], None] | None,
    *,
    delayed: Callable[[str, int], None] | None = None,
) -> None:
    """测试与运维可通过它替换投递方式（同步执行 / 其他队列 / 立即执行退避）。"""
    global _dispatcher, _delayed_dispatcher
    _dispatcher = dispatcher or _default_dispatch
    _delayed_dispatcher = delayed or (lambda task_id, _countdown: _dispatcher(task_id))


def task_item(task: ParseTask, block_count: int = 0) -> ParseTaskItem:
    return ParseTaskItem(
        id=str(task.id),
        document_id=str(task.document_id),
        document_version_id=str(task.document_version_id),
        strategy_id=str(task.strategy_id),
        status=task.status,
        attempt=task.attempt,
        quality_score=task.quality_score,
        low_quality=task.low_quality,
        stats=dict(task.stats or {}),
        error_code=task.error_code,
        error_message=task.error_message,
        error=TaskErrorInfo(**describe_error(task.error_code)) if task.error_code else None,
        auto_retries=task.auto_retries,
        downgrade_retries=task.downgrade_retries,
        manual_revives=task.manual_revives,
        next_source=task.next_source,
        started_at=task.started_at,
        finished_at=task.finished_at,
        created_at=task.created_at,
        block_count=block_count,
    )


def attempt_item(attempt: ParseTaskAttempt) -> ParseAttemptItem:
    return ParseAttemptItem(
        id=str(attempt.id),
        attempt_no=attempt.attempt_no,
        status=attempt.status,
        source=attempt.source,
        quality_score=attempt.quality_score,
        stats=dict(attempt.stats or {}),
        error_code=attempt.error_code,
        error_message=attempt.error_message,
        error_detail=attempt.error_detail,
        duration_ms=attempt.duration_ms,
        started_at=attempt.started_at,
        finished_at=attempt.finished_at,
    )


def block_item(block: ParseBlock) -> ParseBlockItem:
    return ParseBlockItem(
        id=str(block.id),
        seq=block.seq,
        block_type=block.block_type,
        content=block.content,
        page_no=block.page_no,
        bbox=block.bbox,
        level=block.level,
        parent_block_id=str(block.parent_block_id) if block.parent_block_id else None,
        is_discarded=block.is_discarded,
        metadata=dict(block.block_metadata or {}),
    )


def _actions(task: ParseTask) -> TaskActions:
    budget = budget_of(task)
    revive_remaining = max(
        0, settings.task_manual_revive_max_attempts - budget.manual_revives
    )
    return TaskActions(
        can_revive=revive_remaining > 0
        and task.status
        in {ParseTaskStatus.FAILED.value, ParseTaskStatus.CANCELLED.value},
        # 解析是单次确定性流水线，没有可跳过的批次
        can_skip_failed_batches=False,
        revive_remaining=revive_remaining,
        auto_retry_remaining=max(
            0, settings.task_auto_retry_max_attempts - budget.auto_retries
        ),
    )


class ParseTaskService:
    def __init__(self, db: Session, storage: Any | None = None) -> None:
        self.db = db
        self.tasks = ParseTaskRepository(db)
        self.blocks = ParseBlockRepository(db)
        self.strategies = ParseStrategyService(db)
        self.storage = storage or get_storage()
        self.audit = AuditService(db)

    # ------------------------------------------------------------------ #
    # 触发 / 重试 / 取消
    # ------------------------------------------------------------------ #
    def trigger(
        self,
        principal: Principal,
        payload: ParseTaskCreateRequest,
        *,
        ip: str | None = None,
    ) -> ParseTask:
        try:
            version_id = uuid.UUID(payload.document_version_id)
        except ValueError as exc:
            raise NotFoundError("文档版本不存在") from exc

        version = self.db.get(DocumentVersion, version_id)
        if version is None:
            raise NotFoundError("文档版本不存在")
        document = self.db.get(Document, version.document_id)
        if (
            document is None
            or document.deleted_at is not None
            or document.workspace_id != principal.user.workspace_id
        ):
            raise NotFoundError("文档版本不存在")

        active = self.tasks.find_active(version.id)
        if active is not None:
            raise ConflictError(
                "该文档版本已存在进行中的解析任务",
                data={"task_id": str(active.id), "status": active.status},
            )

        # 文档的 doc_type 记录的是扩展名（txt/md/docx…），策略使用归一后的四类
        strategy = self.strategies.resolve_for_document(
            document.workspace_id,
            detect_doc_type(version.file_name, version.mime_type),
            payload.strategy_id,
        )

        task = ParseTask(
            workspace_id=document.workspace_id,
            document_id=document.id,
            document_version_id=version.id,
            strategy_id=strategy.id,
            status=ParseTaskStatus.PENDING.value,
            attempt=0,
            stats={},
            created_by=principal.user_id,
        )
        self.tasks.add(task)
        self.db.flush()
        # 重新解析会替换块，旧片段必须同步失效，避免检索到过期内容
        reset_version_index(self.db, version)
        self.audit.record(
            AuditAction.PARSE_TRIGGER,
            actor=principal.user,
            workspace_id=document.workspace_id,
            object_type="parse_task",
            object_id=task.id,
            after={
                "document_id": str(document.id),
                "document_version_id": str(version.id),
                "strategy_id": str(strategy.id),
                "strategy_version": strategy.version,
            },
            ip=ip,
        )
        self.db.commit()
        self._dispatch(str(task.id))
        return task

    def retry(
        self, principal: Principal, task_id: uuid.UUID, *, ip: str | None = None
    ) -> ParseTask:
        """人工复活：失败或已取消的任务重新排队，不占用自动重试预算。"""
        task = self._get_or_404(principal, task_id)
        budget = budget_of(task)
        if not manual_revive_allowed(budget):
            raise ConflictError(
                f"人工继续次数已达上限（{settings.task_manual_revive_max_attempts} 次），"
                "请调整策略后重新触发",
                data={"attempt": task.attempt, "manual_revives": budget.manual_revives},
            )
        state_machines.PARSE_TASK.ensure(task.status, ParseTaskStatus.PENDING.value)

        task.manual_revives = budget.manual_revives + 1
        task.status = ParseTaskStatus.PENDING.value
        task.next_source = "manual_revive"
        task.error_code = None
        task.error_message = None
        task.finished_at = None
        self.audit.record(
            AuditAction.PARSE_RETRY,
            actor=principal.user,
            workspace_id=task.workspace_id,
            object_type="parse_task",
            object_id=task.id,
            after={"attempt": task.attempt + 1, "manual_revives": task.manual_revives},
            ip=ip,
        )
        self.db.commit()
        # 上一次执行可能被强杀，残留锁会让新执行直接跳过
        self._release_lock(get_redis(), f"parse:run:{task.id}")
        self._dispatch(str(task.id))
        return task

    def cancel(
        self, principal: Principal, task_id: uuid.UUID, *, ip: str | None = None
    ) -> ParseTask:
        task = self._get_or_404(principal, task_id)
        state_machines.PARSE_TASK.ensure(task.status, ParseTaskStatus.CANCELLED.value)

        task.status = ParseTaskStatus.CANCELLED.value
        task.finished_at = _now()
        self.audit.record(
            AuditAction.PARSE_CANCEL,
            actor=principal.user,
            workspace_id=task.workspace_id,
            object_type="parse_task",
            object_id=task.id,
            ip=ip,
        )
        self.db.commit()
        return task

    def delete(
        self, principal: Principal, task_id: uuid.UUID, *, ip: str | None = None
    ) -> None:
        """物理删除任务：产物、执行历史与片段一并清掉，进行中的任务必须先取消。"""
        task = self._get_or_404(principal, task_id)
        if task.status in {ParseTaskStatus.PENDING.value, ParseTaskStatus.RUNNING.value}:
            raise ConflictError(
                "任务进行中，请先取消再删除",
                data={"task_id": str(task.id), "status": task.status},
            )

        version = self.db.get(DocumentVersion, task.document_version_id)
        snapshot = {
            "document_id": str(task.document_id),
            "document_version_id": str(task.document_version_id),
            "strategy_id": str(task.strategy_id),
            "status": task.status,
            "attempt": task.attempt,
        }
        # 成功任务可能是该版本索引的唯一来源，删掉后索引必须一起失效
        drop_index = task.status == ParseTaskStatus.SUCCEEDED.value and (
            self.tasks.count_succeeded_for_version(
                task.document_version_id, exclude_task_id=task.id
            )
            == 0
        )

        # 显式清理子表，不依赖数据库级联是否生效
        self.blocks.delete_for_task(task.id)
        self.tasks.delete_attempts(task.id)
        self.db.delete(task)
        self.audit.record(
            AuditAction.PARSE_TASK_DELETE,
            actor=principal.user,
            workspace_id=task.workspace_id,
            object_type="parse_task",
            object_id=task.id,
            before=snapshot,
            ip=ip,
        )
        if drop_index and version is not None:
            reset_version_index(self.db, version)
        self.db.commit()

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def list_tasks(self, principal: Principal, query: ParseTaskQuery) -> ParseTaskListResponse:
        document_id = self._parse_uuid(query.document_id, "document_id")
        rows, total = self.tasks.list_paginated(
            workspace_id=principal.user.workspace_id,
            document_id=document_id,
            status=query.status.value if query.status else None,
            created_from=query.created_from,
            created_to=query.created_to,
            page=query.page,
            page_size=query.page_size,
        )
        counts = self.blocks.counts_for_tasks([row.id for row in rows])
        return ParseTaskListResponse(
            items=[task_item(row, counts.get(row.id, 0)) for row in rows],
            total=total,
            page=query.page,
            page_size=query.page_size,
        )

    def get_detail(self, principal: Principal, task_id: uuid.UUID) -> ParseTaskDetail:
        task = self._get_or_404(principal, task_id)
        base = task_item(task, self.blocks.count(task.id))
        warnings = [str(item) for item in (task.stats or {}).get("warnings", [])]
        return ParseTaskDetail(
            **base.model_dump(),
            warnings=warnings,
            attempts=[
                attempt_item(row)
                for row in reversed(self.tasks.list_attempts(task.id))
            ],
            actions=_actions(task),
        )

    def list_blocks(
        self,
        principal: Principal,
        task_id: uuid.UUID,
        *,
        include_discarded: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> ParseBlockListResponse:
        task = self._get_or_404(principal, task_id)
        rows, total = self.blocks.list_paginated(
            task.id,
            include_discarded=include_discarded,
            page=page,
            page_size=page_size,
        )
        return ParseBlockListResponse(
            items=[block_item(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    # ------------------------------------------------------------------ #
    # 执 行（Celery worker 调用）
    # ------------------------------------------------------------------ #
    def execute(self, task_id: uuid.UUID) -> None:
        task = self.tasks.get(task_id)
        if task is None or task.status != ParseTaskStatus.PENDING.value:
            return  # 幂等：重复投递 / 已取消 / 已结束一律跳过

        lock_key = f"parse:run:{task_id}"
        redis = get_redis()
        if not self._acquire_lock(redis, lock_key):
            logger.info("解析任务已被其他 worker 处理，跳过 task_id=%s", task_id)
            return

        attempt: ParseTaskAttempt | None = None
        attempt_no = 0
        try:
            state_machines.PARSE_TASK.ensure(task.status, ParseTaskStatus.RUNNING.value)
            # 投递时就决定了本次执行的来源（自动重试 / 人工复活 / 看护重排）
            source = task.next_source or "manual"
            task.next_source = None
            task.attempt += 1
            attempt_no = task.attempt
            task.status = ParseTaskStatus.RUNNING.value
            task.started_at = _now()
            task.finished_at = None
            task.error_code = None
            task.error_message = None
            attempt = ParseTaskAttempt(
                parse_task_id=task.id,
                attempt_no=attempt_no,
                status=ParseTaskStatus.RUNNING.value,
                source=source,
                started_at=task.started_at,
                heartbeat_at=_now(),
                stats={},
            )
            self.tasks.add_attempt(attempt)
            self.db.commit()
            self._touch_heartbeat(attempt)

            version, strategy = self._load_inputs(task)
            data = self.storage.get_bytes(version.storage_key)
            pipeline_state = run_parse_pipeline(
                data=data,
                file_name=version.file_name,
                mime_type=version.mime_type,
                config=dict(strategy.config or {}),
                engine=strategy.engine,
                task_id=str(task.id),
            )

            self.db.refresh(task)
            if task.status == ParseTaskStatus.CANCELLED.value:
                self._finish_attempt(attempt, ParseTaskStatus.CANCELLED.value)
                self.db.commit()
                return
            if task.attempt != attempt_no:
                # fencing：本次执行已被接管，迟到的结果一律丢弃
                logger.warning(
                    "解析执行已被接管，放弃提交 task_id=%s attempt=%s current=%s",
                    task_id,
                    attempt_no,
                    task.attempt,
                )
                self._finish_attempt(attempt, ParseTaskStatus.FAILED.value)
                attempt.error_code = TaskErrorCode.INTERNAL_ERROR.value
                attempt.error_message = "本次执行已被新的执行接管，写入已放弃"
                self.db.commit()
                return

            quality = pipeline_state.get("quality") or {}
            stats = dict(pipeline_state.get("stats") or {})
            stats["warnings"] = list(pipeline_state.get("warnings") or [])
            stats["quality"] = quality.get("stats") or {}
            stats["reasons"] = list(quality.get("reasons") or [])

            state_machines.PARSE_TASK.ensure(task.status, ParseTaskStatus.SUCCEEDED.value)
            task.status = ParseTaskStatus.SUCCEEDED.value
            task.quality_score = int(quality.get("score") or 0)
            task.low_quality = bool(quality.get("low_quality"))
            task.stats = stats
            task.finished_at = _now()
            self._write_blocks(task, version, pipeline_state.get("blocks") or [])

            attempt.quality_score = task.quality_score
            self._finish_attempt(attempt, ParseTaskStatus.SUCCEEDED.value, stats=stats)
            self.db.commit()
        except Exception as exc:  # noqa: BLE001 - worker 必须兜住全部异常并留痕
            self.db.rollback()
            self._mark_failed(task_id, exc)
        finally:
            self._release_lock(redis, lock_key)

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _load_inputs(self, task: ParseTask) -> tuple[DocumentVersion, ParseStrategy]:
        version = self.db.get(DocumentVersion, task.document_version_id)
        strategy = self.db.get(ParseStrategy, task.strategy_id)
        if version is None:
            raise ParseError("文档版本已被删除")
        if strategy is None:
            raise ParseError("解析策略已被删除")
        return version, strategy

    def _write_blocks(
        self, task: ParseTask, version: DocumentVersion, parsed_blocks: list[Any]
    ) -> None:
        self.blocks.delete_for_task(task.id)
        rows = [
            ParseBlock(
                parse_task_id=task.id,
                document_version_id=version.id,
                seq=block.seq,
                block_type=block.block_type.value,
                content=block.content,
                page_no=block.page_no,
                bbox=block.bbox,
                level=block.level,
                is_discarded=block.is_discarded,
                block_metadata=block.metadata,
            )
            for block in parsed_blocks
        ]
        self.db.add_all(rows)
        self.db.flush()
        for index, block in enumerate(parsed_blocks):
            parent_index = block.parent_index
            if parent_index is not None and 0 <= parent_index < len(rows):
                rows[index].parent_block_id = rows[parent_index].id

    def _mark_failed(self, task_id: uuid.UUID, exc: BaseException) -> None:
        """失败落库 + 预算决策：先自动重排，预算耗尽再落终态交给人工。"""
        self.db.rollback()
        task = self.tasks.get(task_id)
        if task is None:
            return
        if task.status != ParseTaskStatus.RUNNING.value:
            # 用户取消 / 看护已接管：不覆盖别人的结论
            logger.info(
                "解析任务状态已不是运行中，放弃写失败 task_id=%s status=%s",
                task_id,
                task.status,
            )
            return

        spec = classify_exception(exc)
        message = (str(exc) or type(exc).__name__)[:ERROR_MESSAGE_LIMIT]
        detail = error_detail(exc, stage="parse_pipeline", spec=spec)
        task.error_message = message

        attempt = self.tasks.latest_attempt(task.id)
        if attempt is not None and attempt.status == ParseTaskStatus.RUNNING.value:
            attempt.error_code = spec.code
            attempt.error_message = message
            attempt.error_detail = detail
            self._finish_attempt(attempt, ParseTaskStatus.FAILED.value)

        budget = budget_of(task)
        if spec.retryable and auto_retry_allowed(budget):
            delay = retry_delay(budget.auto_retries)
            task.auto_retries = budget.auto_retries + 1
            task.status = ParseTaskStatus.PENDING.value
            task.next_source = "auto_retry"
            task.error_code = spec.code
            task.finished_at = None
            self.db.commit()
            logger.info(
                "解析任务已排自动重试 task_id=%s delay=%ss attempt=%s",
                task_id,
                delay,
                task.attempt,
            )
            self._dispatch_delayed(str(task_id), delay)
            return

        task.status = ParseTaskStatus.FAILED.value
        task.next_source = None
        # 可重试类走到这里说明自动预算耗尽：对外给"预算耗尽"，真实原因留在 attempt
        task.error_code = (
            TaskErrorCode.ATTEMPTS_EXHAUSTED.value if spec.retryable else spec.code
        )
        task.finished_at = _now()
        self.db.commit()
        logger.warning(
            "解析任务失败 task_id=%s code=%s cause=%s",
            task_id,
            task.error_code,
            spec.code,
        )

    def _finish_attempt(
        self,
        attempt: ParseTaskAttempt,
        status: str,
        *,
        stats: dict[str, Any] | None = None,
    ) -> None:
        attempt.status = status
        attempt.finished_at = _now()
        if attempt.started_at is not None:
            attempt.duration_ms = max(
                0, int((attempt.finished_at - attempt.started_at).total_seconds() * 1000)
            )
        if stats is not None:
            attempt.stats = dict(stats)
        attempt.heartbeat_at = None

    def _touch_heartbeat(self, attempt: ParseTaskAttempt | None = None) -> None:
        """worker 级存活信号：看护靠它区分"在跑"与"进程没了"。"""
        try:
            get_redis().set(
                heartbeat_key(worker_name()),
                "1",
                ex=max(30, settings.task_heartbeat_ttl_seconds),
            )
        except Exception:  # noqa: BLE001 - Redis 故障不应让执行失败
            logger.warning("心跳写入失败，看护可能无法判活")
        if attempt is not None:
            self.db.execute(
                update(ParseTaskAttempt)
                .where(ParseTaskAttempt.id == attempt.id)
                .values(heartbeat_at=_now())
            )
            self.db.commit()

    def _dispatch(self, task_id: str) -> None:
        try:
            _dispatcher(task_id)
        except Exception:  # noqa: BLE001 - 消息队列不可用不应让 HTTP 请求失败
            logger.exception("解析任务投递失败 task_id=%s", task_id)

    def _dispatch_delayed(self, task_id: str, countdown: int) -> None:
        try:
            if countdown > 0:
                _delayed_dispatcher(task_id, countdown)
            else:
                _dispatcher(task_id)
        except Exception:  # noqa: BLE001
            logger.exception("解析任务延迟投递失败 task_id=%s", task_id)

    @staticmethod
    def _acquire_lock(redis: Any, key: str) -> bool:
        try:
            return bool(
                redis.set(
                    key, "1", nx=True, ex=max(60, settings.parse_run_lock_ttl_seconds)
                )
            )
        except Exception:  # noqa: BLE001 - Redis 故障时降级为无锁执行
            logger.warning("解析锁不可用，降级执行 key=%s", key)
            return True

    @staticmethod
    def _release_lock(redis: Any, key: str) -> None:
        try:
            redis.delete(key)
        except Exception:  # noqa: BLE001
            logger.warning("解析锁释放失败 key=%s", key)

    def _get_or_404(self, principal: Principal, task_id: uuid.UUID) -> ParseTask:
        task = self.tasks.get(task_id)
        if task is None or task.workspace_id != principal.user.workspace_id:
            raise NotFoundError("解析任务不存在")
        return task

    @staticmethod
    def _parse_uuid(value: str | None, field_name: str) -> uuid.UUID | None:
        if not value:
            return None
        try:
            return uuid.UUID(value)
        except ValueError:
            from app.core.exceptions import BadRequestError

            raise BadRequestError(f"{field_name} 不是合法的 UUID") from None
