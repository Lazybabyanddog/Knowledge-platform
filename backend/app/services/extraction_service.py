"""知识抽取任务服务：触发、执行、重试/复活、跳过失败批次、取消与查询。

关键约定：

- 只有 ``succeeded`` 的解析任务可以触发抽取，避免从半成品内容里抽垃圾；
- 触发时把本体（类/关系编码）冻结进 ``config``，执行时按快照过滤；
- ``attempt`` 是**执行序号**（只增、兼作写入版本号），重试预算按来源拆成
  ``auto_retries`` / ``downgrade_retries`` / ``manual_revives``，互不侵占；
- 每批结果落 ``extraction_batch_checkpoint``，恢复时从第一个未完成批继续；
- 可重试失败按预算自动重排（含一次降参），耗尽后落 ``FAILED``，由人工选择
  "继续执行"或"跳过失败批次继续"；
- 候选实体/关系以 ``proposed`` 落 Neo4j，人工确认后才进入正式图谱。
"""

from __future__ import annotations

import hashlib
import logging
import math
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import Principal
from app.core.exceptions import (
    BadInputError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    OntologyMissingError,
)
from app.domain import state_machines
from app.domain.enums import AuditAction, ExtractionTaskStatus, ParseTaskStatus
from app.domain.error_catalog import TaskErrorCode, classify_exception, describe_error
from app.infra.cache.redis_client import get_redis
from app.infra.db.models import (
    ExtractionBatchCheckpoint,
    ExtractionTask,
    ExtractionTaskAttempt,
    OntologyClass,
    OntologyRelation,
    ParseTask,
)
from app.infra.db.repositories import (
    ExtractionCheckpointRepository,
    ExtractionTaskRepository,
    OntologyRepository,
    ParseBlockRepository,
)
from app.pipelines.extract.graph import run_extract_pipeline
from app.schemas.knowledge import (
    ExtractionAttemptItem,
    ExtractionTaskCreateRequest,
    ExtractionTaskDetail,
    ExtractionTaskItem,
    ExtractionTaskListResponse,
    ExtractionTaskQuery,
)
from app.schemas.task import TaskActions, TaskErrorInfo, TaskProgress, TaskSkipReason
from app.services.audit_service import AuditService
from app.services.ontology_service import ontology_schema_snapshot
from app.services.task_recovery import (
    auto_retry_allowed,
    budget_of,
    downgrade_allowed,
    downgraded_batch_size,
    error_detail,
    heartbeat_key,
    manual_revive_allowed,
    retry_delay,
    worker_name,
)

logger = logging.getLogger(__name__)

ERROR_MESSAGE_LIMIT = 2000
_SKIPPABLE_TYPES = {"footer", "header", "toc"}
_MIN_CONTENT = 4
MANUAL_SKIP_REASON = "batch_failed"
COMMITTED = "committed"
SKIPPED = "skipped"


def _now() -> datetime:
    return datetime.now(UTC)


def _fingerprint(batch_size: int, blocks: list[dict[str, Any]]) -> str:
    """批次划分指纹：批大小或块序列一变，旧断点的批序号就失去意义。"""
    digest = hashlib.sha256()
    digest.update(str(batch_size).encode())
    for block in blocks:
        digest.update(b"|")
        digest.update(str(block.get("id")).encode())
    return digest.hexdigest()[:32]


def _default_dispatch(task_id: str) -> None:
    from app.workers.tasks import run_extraction_task  # 延迟导入，避免 service <-> worker 循环

    run_extraction_task.delay(task_id)


def _default_dispatch_delayed(task_id: str, countdown: int) -> None:
    from app.workers.tasks import run_extraction_task

    run_extraction_task.apply_async(args=[task_id], countdown=countdown)


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


def extraction_task_item(
    task: ExtractionTask, *, progress: TaskProgress | None = None
) -> ExtractionTaskItem:
    stats = dict(task.stats or {})
    return ExtractionTaskItem(
        id=str(task.id),
        document_id=str(task.document_id),
        document_version_id=str(task.document_version_id),
        parse_task_id=str(task.parse_task_id),
        ontology_version=task.ontology_version,
        status=task.status,
        attempt=task.attempt,
        stats=stats,
        error_code=task.error_code,
        error_message=task.error_message,
        error=TaskErrorInfo(**describe_error(task.error_code)) if task.error_code else None,
        progress=progress or _progress_from_stats(stats),
        auto_retries=task.auto_retries,
        downgrade_retries=task.downgrade_retries,
        manual_revives=task.manual_revives,
        next_source=task.next_source,
        incomplete=bool(task.incomplete),
        started_at=task.started_at,
        finished_at=task.finished_at,
        created_at=task.created_at,
    )


def extraction_task_detail(
    task: ExtractionTask,
    *,
    attempts: list[ExtractionTaskAttempt],
    summary: dict[str, Any],
    checkpoints: list[ExtractionBatchCheckpoint] | None = None,
) -> ExtractionTaskDetail:
    base = extraction_task_item(task, progress=_progress_from_summary(task, summary))
    stats = dict(task.stats or {})
    skip_summary: dict[str, int] = {}
    for row in checkpoints or []:
        if row.status != SKIPPED:
            continue
        reason = str((row.payload or {}).get("reason") or "batch_skipped")
        skip_summary[reason] = skip_summary.get(reason, 0) + 1
    return ExtractionTaskDetail(
        **base.model_dump(),
        attempts=[_attempt_item(row) for row in reversed(attempts)],
        skip_summary=[
            TaskSkipReason(reason=reason, count=count)
            for reason, count in sorted(skip_summary.items(), key=lambda kv: -kv[1])
        ],
        actions=_actions(task, summary=summary, stats=stats),
    )


def _progress_from_stats(stats: dict[str, Any]) -> TaskProgress:
    return TaskProgress(
        batches_total=int(stats.get("batches") or 0),
        batches_done=int(stats.get("batches_done") or 0),
        batches_skipped=int(stats.get("batches_skipped") or 0),
        current_batch=stats.get("current_batch"),
    )


def _progress_from_summary(task: ExtractionTask, summary: dict[str, Any]) -> TaskProgress:
    stats = dict(task.stats or {})
    total = int(stats.get("batches") or 0)
    max_index = summary.get("max_batch_index")
    current = int(max_index) + 1 if max_index is not None else None
    if task.status == ExtractionTaskStatus.SUCCEEDED.value:
        current = None
    return TaskProgress(
        batches_total=total,
        batches_done=int(summary.get("committed") or 0),
        batches_skipped=int(summary.get("skipped") or 0),
        current_batch=current,
    )


def _actions(
    task: ExtractionTask, *, summary: dict[str, Any], stats: dict[str, Any]
) -> TaskActions:
    budget = budget_of(task)
    revive_remaining = max(0, settings.task_manual_revive_max_attempts - budget.manual_revives)
    unfinished = int(stats.get("batches") or 0) - (
        int(summary.get("committed") or 0) + int(summary.get("skipped") or 0)
    )
    can_revive = revive_remaining > 0 and task.status in {
        ExtractionTaskStatus.FAILED.value,
        ExtractionTaskStatus.CANCELLED.value,
    }
    return TaskActions(
        can_revive=can_revive,
        can_skip_failed_batches=(
            settings.task_skip_failed_batches_enabled
            and can_revive
            and task.status == ExtractionTaskStatus.FAILED.value
            and unfinished > 0
        ),
        revive_remaining=revive_remaining,
        auto_retry_remaining=max(
            0, settings.task_auto_retry_max_attempts - budget.auto_retries
        ),
    )


def _attempt_item(row: ExtractionTaskAttempt) -> ExtractionAttemptItem:
    return ExtractionAttemptItem(
        id=str(row.id),
        attempt_no=row.attempt_no,
        status=row.status,
        source=row.source,
        stats=dict(row.stats or {}),
        error_code=row.error_code,
        error_message=row.error_message,
        error_detail=row.error_detail,
        duration_ms=row.duration_ms,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def ontology_payload(
    classes: dict[str, OntologyClass],
    relations: dict[str, OntologyRelation],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """把本体 ORM 行转成抽取器与校验器都接受的普通字典。"""
    code_by_id = {row.id: row.code for row in classes.values()}
    class_dicts = {
        row.code: {
            "code": row.code,
            "name": row.name,
            "description": row.description,
            "attributes": row.attributes or {},
            "identity_keys": list(row.identity_keys or []),
            "aliases": [row.name],
        }
        for row in classes.values()
    }
    relation_dicts = {
        row.code: {
            "code": row.code,
            "name": row.name,
            "description": row.description,
            "source_class_code": code_by_id.get(row.source_class_id),
            "target_class_code": code_by_id.get(row.target_class_id),
            "attributes": row.attributes or {},
            "keywords": [row.name],
        }
        for row in relations.values()
    }
    return class_dicts, relation_dicts


class ExtractionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.tasks = ExtractionTaskRepository(db)
        self.blocks = ParseBlockRepository(db)
        self.ontology = OntologyRepository(db)
        self.checkpoints = ExtractionCheckpointRepository(db)
        self.audit = AuditService(db)

    # ------------------------------------------------------------------ #
    # 触发 / 重试 / 跳过失败批次 / 取消
    # ------------------------------------------------------------------ #
    def trigger(
        self,
        principal: Principal,
        payload: ExtractionTaskCreateRequest,
        *,
        ip: str | None = None,
        extra_config: dict[str, Any] | None = None,
        dispatch: bool = True,
    ) -> ExtractionTask:
        """建任务并入队。

        ``extra_config`` 给运维场景（存量重抽）留一个标记位，``dispatch=False``
        让调用方自己串行执行（CLI 需要一批跑完再统计，而不是散给 worker）。
        """
        try:
            parse_task_id = uuid.UUID(payload.parse_task_id)
        except ValueError as exc:
            raise NotFoundError("解析任务不存在") from exc

        parse_task = self.db.get(ParseTask, parse_task_id)
        if parse_task is None or parse_task.workspace_id != principal.user.workspace_id:
            raise NotFoundError("解析任务不存在")
        if parse_task.status != ParseTaskStatus.SUCCEEDED.value:
            raise ConflictError(
                "解析任务尚未成功，无法抽取",
                data={"parse_task_id": str(parse_task.id), "status": parse_task.status},
            )

        active = self.tasks.find_active(parse_task.id)
        if active is not None:
            raise ConflictError(
                "该解析任务已存在进行中的抽取任务",
                data={"task_id": str(active.id), "status": active.status},
            )

        classes, relations = ontology_schema_snapshot(self.db, principal.user.workspace_id)
        if not classes:
            raise BadRequestError(
                "尚未定义本体实体类，无法启动抽取",
                data={"hint": "先通过 /api/v1/ontology/classes 定义实体类"},
            )

        task = ExtractionTask(
            workspace_id=principal.user.workspace_id,
            document_id=parse_task.document_id,
            document_version_id=parse_task.document_version_id,
            parse_task_id=parse_task.id,
            ontology_version=self.ontology.current_version(principal.user.workspace_id),
            config={
                "batch_size": settings.extract_batch_size,
                "limit_blocks": payload.limit_blocks,
                "class_codes": sorted(classes),
                "relation_codes": sorted(relations),
                **(extra_config or {}),
            },
            status=ExtractionTaskStatus.PENDING.value,
            attempt=0,
            stats={},
            created_by=principal.user_id,
        )
        self.tasks.add(task)
        self.db.flush()
        self.audit.record(
            AuditAction.EXTRACTION_TRIGGER,
            actor=principal.user,
            workspace_id=task.workspace_id,
            object_type="extraction_task",
            object_id=task.id,
            after={
                "parse_task_id": str(parse_task.id),
                "ontology_version": task.ontology_version,
                "class_codes": sorted(classes),
            },
            ip=ip,
        )
        self.db.commit()
        if dispatch:
            self._dispatch(str(task.id))
        return task

    def retry(
        self,
        principal: Principal,
        task_id: uuid.UUID,
        *,
        degrade: bool = False,
        ip: str | None = None,
    ) -> ExtractionTask:
        """人工复活：失败或已取消的任务从断点继续，不占用自动重试预算。"""
        task = self._get_or_404(principal, task_id)
        budget = budget_of(task)
        if not manual_revive_allowed(budget):
            raise ConflictError(
                f"人工继续次数已达上限（{settings.task_manual_revive_max_attempts} 次）",
                data={
                    "attempt": task.attempt,
                    "manual_revives": budget.manual_revives,
                    "hint": "修改输入后重新触发抽取",
                },
            )
        state_machines.EXTRACTION_TASK.ensure(
            task.status, ExtractionTaskStatus.PENDING.value
        )

        task.manual_revives = budget.manual_revives + 1
        task.status = ExtractionTaskStatus.PENDING.value
        task.next_source = "manual_revive"
        task.error_code = None
        task.error_message = None
        task.finished_at = None
        if degrade:
            config = dict(task.config or {})
            current = int(config.get("batch_size") or settings.extract_batch_size)
            config["downgraded_from"] = current
            config["batch_size"] = downgraded_batch_size(current)
            task.config = config
        self.audit.record(
            AuditAction.EXTRACTION_RETRY,
            actor=principal.user,
            workspace_id=task.workspace_id,
            object_type="extraction_task",
            object_id=task.id,
            after={
                "attempt": task.attempt + 1,
                "manual_revives": task.manual_revives,
                "degrade": degrade,
            },
            ip=ip,
        )
        self.db.commit()
        # 复活前必须清锁：上一次执行可能被强杀，残留标记会让新执行直接跳过
        self._release_lock(get_redis(), f"extract:run:{task.id}")
        self._dispatch(str(task.id))
        return task

    def skip_failed_batches(
        self,
        principal: Principal,
        task_id: uuid.UUID,
        *,
        ip: str | None = None,
    ) -> ExtractionTask:
        """跳过卡住的批次继续：只标"连续未完成的那一段"，剩余批次仍要正常抽。"""
        if not settings.task_skip_failed_batches_enabled:
            raise ConflictError("当前未开启跳过失败批次")
        task = self._get_or_404(principal, task_id)
        if task.status != ExtractionTaskStatus.FAILED.value:
            raise ConflictError(
                "只有失败的任务可以跳过批次继续", data={"status": task.status}
            )
        budget = budget_of(task)
        if not manual_revive_allowed(budget):
            raise ConflictError(
                f"人工继续次数已达上限（{settings.task_manual_revive_max_attempts} 次）",
                data={"attempt": task.attempt, "manual_revives": budget.manual_revives},
            )
        state_machines.EXTRACTION_TASK.ensure(
            task.status, ExtractionTaskStatus.PENDING.value
        )

        blocks = self._load_blocks(task)
        if not blocks:
            raise BadInputError("解析任务没有可抽取的内容块")
        config = dict(task.config or {})
        batch_size = int(config.get("batch_size") or settings.extract_batch_size)
        offset = max(0, int(config.get("resume_block_index") or 0))
        sliced = blocks[offset:]
        total_batches = math.ceil(len(sliced) / batch_size) if sliced else 0
        rows = self.checkpoints.list_for_task(task.id)
        done = {row.batch_index for row in rows}
        missing = [index for index in range(total_batches) if index not in done]
        if not missing:
            raise ConflictError("没有未完成的批次，直接继续执行即可")

        # 只跳过"从第一个未完成批开始的连续一段"，不能把后面的好批次一起跳掉
        stuck: list[int] = []
        for index in range(missing[0], total_batches):
            if index in done:
                break
            stuck.append(index)

        for index in stuck:
            batch_blocks = sliced[index * batch_size : (index + 1) * batch_size]
            self.checkpoints.upsert(
                task_id=task.id,
                batch_index=index,
                status=SKIPPED,
                block_ids=[uuid.UUID(str(block["id"])) for block in batch_blocks],
                payload={"reason": MANUAL_SKIP_REASON, "manual": True},
                attempt_no=task.attempt,
            )

        task.manual_revives = budget.manual_revives + 1
        task.status = ExtractionTaskStatus.PENDING.value
        task.next_source = "skip_batches"
        task.incomplete = True
        task.error_code = None
        task.error_message = None
        task.finished_at = None
        self.audit.record(
            AuditAction.EXTRACTION_RETRY,
            actor=principal.user,
            workspace_id=task.workspace_id,
            object_type="extraction_task",
            object_id=task.id,
            after={"skipped_batches": stuck, "manual_revives": task.manual_revives},
            ip=ip,
        )
        self.db.commit()
        self._release_lock(get_redis(), f"extract:run:{task.id}")
        self._dispatch(str(task.id))
        return task

    def cancel(
        self, principal: Principal, task_id: uuid.UUID, *, ip: str | None = None
    ) -> ExtractionTask:
        task = self._get_or_404(principal, task_id)
        state_machines.EXTRACTION_TASK.ensure(
            task.status, ExtractionTaskStatus.CANCELLED.value
        )

        task.status = ExtractionTaskStatus.CANCELLED.value
        task.finished_at = _now()
        self.audit.record(
            AuditAction.EXTRACTION_CANCEL,
            actor=principal.user,
            workspace_id=task.workspace_id,
            object_type="extraction_task",
            object_id=task.id,
            ip=ip,
        )
        self.db.commit()
        return task

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def list_tasks(
        self, principal: Principal, query: ExtractionTaskQuery
    ) -> ExtractionTaskListResponse:
        rows, total = self.tasks.list_paginated(
            workspace_id=principal.user.workspace_id,
            document_id=self._parse_uuid(query.document_id, "document_id"),
            parse_task_id=self._parse_uuid(query.parse_task_id, "parse_task_id"),
            status=query.status,
            page=query.page,
            page_size=query.page_size,
        )
        return ExtractionTaskListResponse(
            items=[extraction_task_item(row) for row in rows],
            total=total,
            page=query.page,
            page_size=query.page_size,
        )

    def get_detail(self, principal: Principal, task_id: uuid.UUID) -> ExtractionTaskDetail:
        task = self._get_or_404(principal, task_id)
        return extraction_task_detail(
            task,
            attempts=self.tasks.list_attempts(task.id),
            summary=self.checkpoints.summarize(task.id),
            checkpoints=self.checkpoints.list_for_task(task.id),
        )

    # ------------------------------------------------------------------ #
    # 执 行（Celery worker 调用）
    # ------------------------------------------------------------------ #
    def execute(self, task_id: uuid.UUID) -> None:
        task = self.tasks.get(task_id)
        if task is None or task.status != ExtractionTaskStatus.PENDING.value:
            return  # 幂等：重复投递 / 已取消 / 已结束一律跳过

        lock_key = f"extract:run:{task_id}"
        redis = get_redis()
        if not self._acquire_lock(redis, lock_key):
            logger.info("抽取任务已有执行者持有锁，跳过 task_id=%s", task_id)
            return

        attempt_row: ExtractionTaskAttempt | None = None
        try:
            state_machines.EXTRACTION_TASK.ensure(
                task.status, ExtractionTaskStatus.RUNNING.value
            )
            # 投递时就决定了本次执行的来源（自动重试 / 降参 / 复活 / 跳过后续跑）
            source = task.next_source or "manual"
            task.next_source = None
            task.attempt += 1
            attempt_no = task.attempt
            task.status = ExtractionTaskStatus.RUNNING.value
            task.started_at = _now()
            task.finished_at = None
            task.error_code = None
            task.error_message = None
            attempt_row = ExtractionTaskAttempt(
                extraction_task_id=task.id,
                attempt_no=attempt_no,
                status=ExtractionTaskStatus.RUNNING.value,
                source=source,
                stats={},
                started_at=task.started_at,
                heartbeat_at=_now(),
            )
            self.tasks.add_attempt(attempt_row)
            self.db.commit()

            all_blocks = self._load_blocks(task)
            if not all_blocks:
                raise BadInputError("解析任务没有可抽取的内容块")
            classes, relations = self._frozen_ontology(task)

            config = dict(task.config or {})
            batch_size = int(config.get("batch_size") or settings.extract_batch_size)
            blocks, resume_index, restored = self._resolve_resume(
                task, all_blocks, batch_size
            )
            total_batches = math.ceil(len(blocks) / batch_size) if blocks else 0
            task.stats = {
                **(task.stats or {}),
                "batches": total_batches,
                "blocks": len(blocks),
            }
            self.db.commit()
            self._touch_heartbeat()

            state: dict[str, Any] = {
                "workspace_id": str(task.workspace_id),
                "task_id": str(task.id),
                "document_id": str(task.document_id),
                "blocks": blocks,
                "classes": classes,
                "relations": relations,
                "batch_size": batch_size,
                "max_repairs": int(
                    config.get("max_repairs") or settings.extract_max_repair_attempts
                ),
                "resume_index": resume_index,
                "attempt_no": attempt_no,
                # 每批提交后由服务层落断点并刷新心跳（图节点保持纯函数）
                "checkpoint_writer": partial(
                    self._write_checkpoint, task.id, attempt_row.id, attempt_no
                ),
                **restored,
            }
            result = run_extract_pipeline(state)

            self.db.refresh(task)
            if task.status == ExtractionTaskStatus.CANCELLED.value:
                # 取消优先：跑完的批次落库，但任务保持取消态
                self._finish_attempt(
                    attempt_row, ExtractionTaskStatus.CANCELLED.value, stats=task.stats
                )
                self.db.commit()
                return
            if task.attempt != attempt_no:
                # fencing：本次执行已被接管（看护重排 / 人工复活），迟到的结果一律丢弃
                logger.warning(
                    "执行已被接管，放弃提交 task_id=%s attempt=%s current=%s",
                    task_id,
                    attempt_no,
                    task.attempt,
                )
                self._finish_attempt(
                    attempt_row,
                    ExtractionTaskStatus.FAILED.value,
                    stats={"superseded": True},
                )
                attempt_row.error_code = TaskErrorCode.INTERNAL_ERROR.value
                attempt_row.error_message = "本次执行已被新的执行接管，写入已放弃"
                self.db.commit()
                return

            summary = self.checkpoints.summarize(task.id)
            manual_skips = self._count_manual_skips(task.id)
            stats = dict(result.get("stats") or {})
            stats.update(
                {
                    "blocks": len(blocks),
                    "batches": total_batches,
                    "batches_done": int(summary.get("committed") or 0),
                    "batches_skipped": int(summary.get("skipped") or 0),
                    "skipped_blocks": len(result.get("skipped_block_ids") or []),
                    "skip_reasons": list(result.get("skip_reasons") or []),
                    "repair_total": int(result.get("repair_total") or 0),
                    "resumed_from_batch": resume_index,
                    "unprocessed_batches": manual_skips,
                    "incomplete": manual_skips > 0,
                }
            )
            stats.pop("current_batch", None)

            state_machines.EXTRACTION_TASK.ensure(
                task.status, ExtractionTaskStatus.SUCCEEDED.value
            )
            task.status = ExtractionTaskStatus.SUCCEEDED.value
            task.stats = stats
            task.incomplete = manual_skips > 0
            task.finished_at = _now()
            config.pop("resume_block_index", None)
            task.config = config
            self._finish_attempt(
                attempt_row, ExtractionTaskStatus.SUCCEEDED.value, stats=stats
            )
            # 断点保留但标记已消费：不再参与恢复，只用于回溯"哪些块产出了什么"
            self.checkpoints.mark_consumed(task.id)
            self.db.commit()
        except Exception as exc:  # noqa: BLE001 - 失败必须落库，再由预算决定去向
            self.db.rollback()
            self._mark_failed(task_id, exc)
        finally:
            self._release_lock(redis, lock_key)

    def _mark_failed(self, task_id: uuid.UUID, exc: BaseException) -> None:
        """失败落库 + 预算决策：自动重排、降参重排，或落终态交给人工。"""
        self.db.rollback()
        task = self.tasks.get(task_id)
        if task is None:
            return
        if task.status != ExtractionTaskStatus.RUNNING.value:
            # 用户取消 / 看护已接管：不覆盖别人的结论
            logger.info(
                "任务状态已不是运行中，放弃写失败 task_id=%s status=%s",
                task_id,
                task.status,
            )
            return

        spec = classify_exception(exc)
        message = (str(exc) or type(exc).__name__)[:ERROR_MESSAGE_LIMIT]
        summary = self.checkpoints.summarize(task.id)
        max_index = summary.get("max_batch_index")
        stall_batch = None if max_index is None else int(max_index) + 1
        detail = error_detail(
            exc,
            stage=getattr(exc, "task_stage", "pipeline"),
            batch_index=stall_batch,
            spec=spec,
        )
        detail["resume_from"] = stall_batch or 0

        stats = dict(task.stats or {})
        stats.update(
            {
                "batches_done": int(summary.get("committed") or 0),
                "batches_skipped": int(summary.get("skipped") or 0),
                "resume_from": stall_batch or 0,
            }
        )
        if stall_batch is not None:
            stats["current_batch"] = stall_batch
        task.stats = stats
        task.error_message = message

        attempt = self.tasks.latest_attempt(task.id)
        if attempt is not None and attempt.status == ExtractionTaskStatus.RUNNING.value:
            attempt.error_code = spec.code
            attempt.error_message = message
            attempt.error_detail = detail
            self._finish_attempt(attempt, ExtractionTaskStatus.FAILED.value, stats=stats)

        budget = budget_of(task)
        config = dict(task.config or {})
        next_source: str | None = None
        delay = 0
        if spec.retryable and auto_retry_allowed(budget):
            next_source = "auto_retry"
            delay = retry_delay(budget.auto_retries)
            task.auto_retries = budget.auto_retries + 1
        elif spec.retryable and downgrade_allowed(budget, supported=True):
            current = int(config.get("batch_size") or settings.extract_batch_size)
            config["downgraded_from"] = current
            config["batch_size"] = downgraded_batch_size(current)
            task.config = config
            task.downgrade_retries = budget.downgrade_retries + 1
            next_source = "downgrade"

        if next_source is None:
            task.status = ExtractionTaskStatus.FAILED.value
            task.next_source = None
            # 可重试类失败走到这里说明预算耗尽：对外给"预算耗尽"，
            # 真实原因留在自己的 error_message 与执行记录的 error_detail.cause 里
            task.error_code = (
                TaskErrorCode.ATTEMPTS_EXHAUSTED.value
                if spec.retryable
                else spec.code
            )
            task.finished_at = _now()
            self.db.commit()
            logger.warning(
                "抽取任务失败 task_id=%s code=%s cause=%s", task_id, task.error_code, spec.code
            )
            return

        task.status = ExtractionTaskStatus.PENDING.value
        task.next_source = next_source
        task.error_code = spec.code
        task.finished_at = None
        self.db.commit()
        logger.info(
            "抽取任务已排下一次执行 task_id=%s source=%s delay=%ss",
            task_id,
            next_source,
            delay,
        )
        self._dispatch_delayed(str(task_id), delay)

    # ------------------------------------------------------------------ #
    # 断点：解析恢复位置、写断点、统计未处理批次
    # ------------------------------------------------------------------ #
    def _resolve_resume(
        self,
        task: ExtractionTask,
        all_blocks: list[dict[str, Any]],
        batch_size: int,
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
        """决定这次从第几批开始，并把已完成批次的产出拼回 state。

        批大小变化（降参）会让旧批序号失去意义，此时按"已覆盖的块前缀"推进
        offset，既不从第一批重来，也不会把旧断点混进新划分。
        """
        config = dict(task.config or {})
        offset = max(0, int(config.get("resume_block_index") or 0))
        sliced = all_blocks[offset:]
        fingerprint = _fingerprint(batch_size, sliced)
        rows = self.checkpoints.list_for_task(task.id)

        if (
            rows
            and task.checkpoint_fingerprint
            and task.checkpoint_fingerprint != fingerprint
        ):
            advanced = offset + self._covered_prefix(sliced, rows)
            self.checkpoints.delete_for_task(task.id)
            config["resume_block_index"] = advanced
            task.config = config
            task.checkpoint_fingerprint = None
            self.db.commit()
            logger.info(
                "输入或批大小已变化，放弃旧断点 task_id=%s resume_block=%s",
                task.id,
                advanced,
            )
            offset = advanced
            sliced = all_blocks[offset:]
            fingerprint = _fingerprint(batch_size, sliced)
            rows = []

        if task.checkpoint_fingerprint != fingerprint:
            task.checkpoint_fingerprint = fingerprint
            self.db.commit()

        return sliced, self._first_missing(rows), self._restore(rows)

    @staticmethod
    def _covered_prefix(
        sliced: list[dict[str, Any]], rows: list[ExtractionBatchCheckpoint]
    ) -> int:
        """按块顺序找第一个没被任何断点覆盖的位置。"""
        covered = {str(block_id) for row in rows for block_id in row.block_ids or []}
        for index, block in enumerate(sliced):
            if str(block.get("id")) not in covered:
                return index
        return len(sliced)

    @staticmethod
    def _first_missing(rows: list[ExtractionBatchCheckpoint]) -> int:
        done = {row.batch_index for row in rows}
        index = 0
        while index in done:
            index += 1
        return index

    @staticmethod
    def _restore(rows: list[ExtractionBatchCheckpoint]) -> dict[str, Any]:
        """从断点行拼回 state：实体/关系参与全局去重，跳过记录与计数还原。"""
        entities: list[dict[str, Any]] = []
        relation_rows: list[dict[str, Any]] = []
        skipped_block_ids: list[str] = []
        skip_reasons: list[str] = []
        batches_skipped = 0
        for row in rows:
            payload = dict(row.payload or {})
            entities.extend(payload.get("entities") or [])
            relation_rows.extend(payload.get("relation_rows") or [])
            if row.status == SKIPPED:
                batches_skipped += 1
                skipped_block_ids.extend(str(item) for item in row.block_ids or [])
                reason = payload.get("reason")
                if reason and reason not in skip_reasons:
                    skip_reasons.append(str(reason))
        return {
            "entities": entities,
            "relation_rows": relation_rows,
            "skipped_block_ids": skipped_block_ids,
            "skip_reasons": skip_reasons,
            "stats": {"batches_skipped": batches_skipped},
        }

    def _write_checkpoint(
        self,
        task_id: uuid.UUID,
        attempt_id: uuid.UUID,
        attempt_no: int,
        *,
        batch_index: int,
        status: str,
        block_ids: list[str],
        payload: dict[str, Any],
        reason: str | None = None,
    ) -> None:
        """图节点每提交（或跳过）一批就调这里：独立提交 + 刷新心跳。"""
        parsed: list[uuid.UUID] = []
        for item in block_ids:
            try:
                parsed.append(uuid.UUID(str(item)))
            except ValueError:
                continue
        body = payload if status == COMMITTED else {"reason": reason or "batch_skipped"}
        self.checkpoints.upsert(
            task_id=task_id,
            batch_index=int(batch_index),
            status=status,
            block_ids=parsed,
            payload=body,
            attempt_no=attempt_no,
        )
        self.db.execute(
            update(ExtractionTaskAttempt)
            .where(ExtractionTaskAttempt.id == attempt_id)
            .values(heartbeat_at=_now())
        )
        self.db.commit()
        self._touch_heartbeat()

    def _count_manual_skips(self, task_id: uuid.UUID) -> int:
        return sum(
            1
            for row in self.checkpoints.list_for_task(task_id)
            if (row.payload or {}).get("manual")
        )

    def _finish_attempt(
        self,
        row: ExtractionTaskAttempt,
        status: str,
        *,
        stats: dict[str, Any] | None = None,
    ) -> None:
        row.status = status
        row.finished_at = _now()
        if row.started_at is not None:
            row.duration_ms = max(
                0, int((row.finished_at - row.started_at).total_seconds() * 1000)
            )
        if stats is not None:
            row.stats = dict(stats)
        row.heartbeat_at = None

    # ------------------------------------------------------------------ #
    # 数据装载与基础设施
    # ------------------------------------------------------------------ #
    def _load_blocks(self, task: ExtractionTask) -> list[dict[str, Any]]:
        rows = self.blocks.list_all(task.parse_task_id)
        limit = (task.config or {}).get("limit_blocks")
        if limit:
            rows = rows[: int(limit)]
        else:
            rows = rows[: settings.extract_max_blocks]
        return [
            {
                "id": str(row.id),
                "seq": row.seq,
                "block_type": row.block_type,
                "content": row.content,
            }
            for row in rows
        ]

    def _frozen_ontology(
        self, task: ExtractionTask
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        config = dict(task.config or {})
        classes, relations = ontology_schema_snapshot(self.db, task.workspace_id)
        class_codes = set(config.get("class_codes") or [])
        relation_codes = set(config.get("relation_codes") or [])
        if class_codes:
            classes = {code: row for code, row in classes.items() if code in class_codes}
        if relation_codes:
            relations = {
                code: row for code, row in relations.items() if code in relation_codes
            }
        class_dicts, relation_dicts = ontology_payload(classes, relations)
        if not class_dicts:
            raise OntologyMissingError("触发时的本体实体类已被禁用或删除，请重新触发抽取")
        # 关系类型的端点若不在冻结的类里，抽取时无法校验，直接剔除
        relation_dicts = {
            code: meta
            for code, meta in relation_dicts.items()
            if meta["source_class_code"] in class_dicts
            and meta["target_class_code"] in class_dicts
        }
        return class_dicts, relation_dicts

    def _touch_heartbeat(self) -> None:
        """worker 级存活信号：看护靠它区分"在跑"与"进程没了"。"""
        try:
            get_redis().set(
                heartbeat_key(worker_name()),
                "1",
                ex=max(30, settings.task_heartbeat_ttl_seconds),
            )
        except Exception:  # noqa: BLE001 - Redis 故障不应让执行失败
            logger.warning("心跳写入失败，看护可能无法判活")

    def _dispatch(self, task_id: str) -> None:
        try:
            _dispatcher(task_id)
        except Exception:  # noqa: BLE001 - 消息队列不可用不应让 HTTP 请求失败
            logger.exception("抽取任务投递失败 task_id=%s", task_id)

    def _dispatch_delayed(self, task_id: str, countdown: int) -> None:
        try:
            if countdown > 0:
                _delayed_dispatcher(task_id, countdown)
            else:
                _dispatcher(task_id)
        except Exception:  # noqa: BLE001
            logger.exception("抽取任务延迟投递失败 task_id=%s", task_id)

    def _get_or_404(self, principal: Principal, task_id: uuid.UUID) -> ExtractionTask:
        task = self.tasks.get(task_id)
        if task is None or task.workspace_id != principal.user.workspace_id:
            raise NotFoundError("抽取任务不存在")
        return task

    @staticmethod
    def _acquire_lock(redis: Any, key: str) -> bool:
        try:
            return bool(
                redis.set(
                    key, "1", nx=True, ex=max(60, settings.extract_run_lock_ttl_seconds)
                )
            )
        except Exception:  # noqa: BLE001 - Redis 故障时降级为无锁执行
            logger.warning("抽取锁不可用，降级执行 key=%s", key)
            return True

    @staticmethod
    def _release_lock(redis: Any, key: str) -> None:
        try:
            redis.delete(key)
        except Exception:  # noqa: BLE001
            logger.warning("抽取锁释放失败 key=%s", key)

    @staticmethod
    def _parse_uuid(value: str | None, field_name: str) -> uuid.UUID | None:
        if not value:
            return None
        try:
            return uuid.UUID(value)
        except ValueError:
            raise BadRequestError(f"{field_name} 不是合法的 UUID") from None


__all__ = [
    "ExtractionService",
    "extraction_task_detail",
    "extraction_task_item",
    "ontology_payload",
    "set_dispatcher",
]
