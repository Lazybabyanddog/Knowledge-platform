"""解析任务服务：触发、执行、重试、取消与产物查询。

执行路径上的关键约定：
- 只有 ``pending`` 才能被执行，重复投递同一任务直接跳过（幂等）；
- 执行前抢占 Redis 锁，避免同一任务被两个 worker 同时处理；
- 每次执行写一条 attempt，失败历史只增不改；
- 产物按 ``(task_id, seq)`` 唯一，重试前先清旧产物再写。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import Principal
from app.core.exceptions import ConflictError, NotFoundError
from app.domain import state_machines
from app.domain.enums import AuditAction, ParseTaskStatus
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
from app.services.audit_service import AuditService
from app.services.index_service import reset_version_index
from app.services.parse_strategy_service import ParseStrategyService

logger = logging.getLogger(__name__)

LOCK_TTL_SECONDS = 1800
ERROR_MESSAGE_LIMIT = 2000


def _now() -> datetime:
    return datetime.now(UTC)


def _default_dispatch(task_id: str) -> None:
    from app.workers.tasks import run_parse_task  # 延迟导入，避免 service <-> worker 循环

    run_parse_task.delay(task_id)


_dispatcher: Callable[[str], None] = _default_dispatch


def set_dispatcher(dispatcher: Callable[[str], None] | None) -> None:
    """测试与运维可通过它替换投递方式（同步执行 / 其他队列）。"""
    global _dispatcher
    _dispatcher = dispatcher or _default_dispatch


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
        quality_score=attempt.quality_score,
        error_code=attempt.error_code,
        error_message=attempt.error_message,
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
        task = self._get_or_404(principal, task_id)
        if task.attempt >= settings.parse_max_attempts:
            raise ConflictError(
                f"重试次数已达上限（{settings.parse_max_attempts} 次），请调整策略后重新触发",
                data={"attempt": task.attempt},
            )
        state_machines.PARSE_TASK.ensure(task.status, ParseTaskStatus.PENDING.value)

        task.status = ParseTaskStatus.PENDING.value
        task.error_code = None
        task.error_message = None
        task.finished_at = None
        self.audit.record(
            AuditAction.PARSE_RETRY,
            actor=principal.user,
            workspace_id=task.workspace_id,
            object_type="parse_task",
            object_id=task.id,
            after={"attempt": task.attempt + 1},
            ip=ip,
        )
        self.db.commit()
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
            attempts=[attempt_item(row) for row in self.tasks.list_attempts(task.id)],
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
        try:
            state_machines.PARSE_TASK.ensure(task.status, ParseTaskStatus.RUNNING.value)
            task.attempt += 1
            task.status = ParseTaskStatus.RUNNING.value
            task.started_at = _now()
            attempt = ParseTaskAttempt(
                parse_task_id=task.id,
                attempt_no=task.attempt,
                status=ParseTaskStatus.RUNNING.value,
                started_at=task.started_at,
                stats={},
            )
            self.tasks.add_attempt(attempt)
            self.db.commit()

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
                attempt.status = ParseTaskStatus.CANCELLED.value
                attempt.finished_at = _now()
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

            attempt.status = ParseTaskStatus.SUCCEEDED.value
            attempt.quality_score = task.quality_score
            attempt.stats = stats
            attempt.finished_at = task.finished_at
            self.db.commit()
        except Exception as exc:  # noqa: BLE001 - worker 必须兜住全部异常并留痕
            self.db.rollback()
            self._mark_failed(task_id, attempt, exc)
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

    def _mark_failed(
        self,
        task_id: uuid.UUID,
        attempt: ParseTaskAttempt | None,
        exc: Exception,
    ) -> None:
        task = self.tasks.get(task_id)
        if task is None:
            return
        message = str(exc)[:ERROR_MESSAGE_LIMIT] or exc.__class__.__name__
        error_code = "parse_error" if isinstance(exc, ParseError) else "internal_error"

        if task.status == ParseTaskStatus.RUNNING.value:
            task.status = ParseTaskStatus.FAILED.value
            task.error_code = error_code
            task.error_message = message
            task.finished_at = _now()

        current_attempt = attempt
        if current_attempt is None:
            attempts = self.tasks.list_attempts(task.id)
            current_attempt = attempts[-1] if attempts else None
        if current_attempt is not None:
            current_attempt.status = ParseTaskStatus.FAILED.value
            current_attempt.error_code = error_code
            current_attempt.error_message = message
            current_attempt.finished_at = task.finished_at or _now()
        self.db.commit()
        logger.warning("解析任务失败 task_id=%s attempt=%s: %s", task_id, task.attempt, message)

    def _dispatch(self, task_id: str) -> None:
        try:
            _dispatcher(task_id)
        except Exception:  # noqa: BLE001 - 消息队列不可用不应让 HTTP 请求失败
            logger.exception("解析任务投递失败 task_id=%s", task_id)

    @staticmethod
    def _acquire_lock(redis: Any, key: str) -> bool:
        try:
            return bool(redis.set(key, "1", nx=True, ex=LOCK_TTL_SECONDS))
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
