"""知识抽取任务服务：触发、执行、重试、取消与查询。

关键约定：
- 只有 ``succeeded`` 的解析任务可以触发抽取，避免从半成品内容里抽垃圾；
- 触发时把本体（类/关系编码）冻结进 ``config``，执行时按快照过滤，
  事后能解释"当时按哪一版本的本体抽取"；
- 执行前抢占 Redis 锁并再次确认 ``pending``，重复投递直接跳过（幂等）；
- 候选实体/关系以 ``proposed`` 落 Neo4j，人工确认后才进入正式图谱。
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
from app.core.exceptions import AppError, BadRequestError, ConflictError, NotFoundError
from app.domain import state_machines
from app.domain.enums import AuditAction, ExtractionTaskStatus, ParseTaskStatus
from app.infra.cache.redis_client import get_redis
from app.infra.db.models import ExtractionTask, OntologyClass, OntologyRelation, ParseTask
from app.infra.db.repositories import (
    ExtractionTaskRepository,
    OntologyRepository,
    ParseBlockRepository,
)
from app.pipelines.extract.graph import run_extract_pipeline
from app.schemas.knowledge import (
    ExtractionTaskCreateRequest,
    ExtractionTaskItem,
    ExtractionTaskListResponse,
    ExtractionTaskQuery,
)
from app.services.audit_service import AuditService
from app.services.ontology_service import ontology_schema_snapshot

logger = logging.getLogger(__name__)

LOCK_TTL_SECONDS = 1800
ERROR_MESSAGE_LIMIT = 2000


def _now() -> datetime:
    return datetime.now(UTC)


def _default_dispatch(task_id: str) -> None:
    from app.workers.tasks import run_extraction_task  # 延迟导入，避免 service <-> worker 循环

    run_extraction_task.delay(task_id)


_dispatcher: Callable[[str], None] = _default_dispatch


def set_dispatcher(dispatcher: Callable[[str], None] | None) -> None:
    """测试与运维可通过它替换投递方式（同步执行 / 其他队列）。"""
    global _dispatcher
    _dispatcher = dispatcher or _default_dispatch


def extraction_task_item(task: ExtractionTask) -> ExtractionTaskItem:
    return ExtractionTaskItem(
        id=str(task.id),
        document_id=str(task.document_id),
        document_version_id=str(task.document_version_id),
        parse_task_id=str(task.parse_task_id),
        ontology_version=task.ontology_version,
        status=task.status,
        attempt=task.attempt,
        stats=dict(task.stats or {}),
        error_code=task.error_code,
        error_message=task.error_message,
        started_at=task.started_at,
        finished_at=task.finished_at,
        created_at=task.created_at,
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
        self.audit = AuditService(db)

    # ------------------------------------------------------------------ #
    # 触发 / 重试 / 取消
    # ------------------------------------------------------------------ #
    def trigger(
        self,
        principal: Principal,
        payload: ExtractionTaskCreateRequest,
        *,
        ip: str | None = None,
    ) -> ExtractionTask:
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

        classes, relations = ontology_schema_snapshot(
            self.db, principal.user.workspace_id
        )
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
        self._dispatch(str(task.id))
        return task

    def retry(
        self, principal: Principal, task_id: uuid.UUID, *, ip: str | None = None
    ) -> ExtractionTask:
        task = self._get_or_404(principal, task_id)
        if task.attempt >= settings.extract_max_attempts:
            raise ConflictError(
                f"重试次数已达上限（{settings.extract_max_attempts} 次）",
                data={"attempt": task.attempt},
            )
        state_machines.EXTRACTION_TASK.ensure(
            task.status, ExtractionTaskStatus.PENDING.value
        )

        task.status = ExtractionTaskStatus.PENDING.value
        task.error_code = None
        task.error_message = None
        task.finished_at = None
        self.audit.record(
            AuditAction.EXTRACTION_RETRY,
            actor=principal.user,
            workspace_id=task.workspace_id,
            object_type="extraction_task",
            object_id=task.id,
            after={"attempt": task.attempt + 1},
            ip=ip,
        )
        self.db.commit()
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

    def get_detail(self, principal: Principal, task_id: uuid.UUID) -> ExtractionTaskItem:
        return extraction_task_item(self._get_or_404(principal, task_id))

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
            logger.info("抽取任务已被其他 worker 处理，跳过 task_id=%s", task_id)
            return

        try:
            state_machines.EXTRACTION_TASK.ensure(
                task.status, ExtractionTaskStatus.RUNNING.value
            )
            task.attempt += 1
            task.status = ExtractionTaskStatus.RUNNING.value
            task.started_at = _now()
            self.db.commit()

            blocks = self._load_blocks(task)
            if not blocks:
                raise BadRequestError("解析任务没有可抽取的内容块")

            classes, relations = self._frozen_ontology(task)
            config = dict(task.config or {})
            state: dict[str, Any] = {
                "workspace_id": str(task.workspace_id),
                "task_id": str(task.id),
                "document_id": str(task.document_id),
                "blocks": blocks,
                "classes": classes,
                "relations": relations,
                "batch_size": int(config.get("batch_size") or settings.extract_batch_size),
                "max_repairs": int(
                    config.get("max_repairs") or settings.extract_max_repair_attempts
                ),
            }
            result = run_extract_pipeline(state)

            self.db.refresh(task)
            if task.status == ExtractionTaskStatus.CANCELLED.value:
                return

            stats = dict(result.get("stats") or {})
            stats["blocks"] = len(blocks)
            stats["skipped_blocks"] = len(result.get("skipped_block_ids") or [])
            stats["skip_reasons"] = list(result.get("skip_reasons") or [])
            stats["repair_total"] = int(result.get("repair_total") or 0)

            state_machines.EXTRACTION_TASK.ensure(
                task.status, ExtractionTaskStatus.SUCCEEDED.value
            )
            task.status = ExtractionTaskStatus.SUCCEEDED.value
            task.stats = stats
            task.finished_at = _now()
            self.db.commit()
        except Exception as exc:  # noqa: BLE001 - worker 必须兜住全部异常并留痕
            self.db.rollback()
            self._mark_failed(task_id, exc)
        finally:
            self._release_lock(redis, lock_key)

    # ------------------------------------------------------------------ #
    # 内部
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
            classes = {
                code: row for code, row in classes.items() if code in class_codes
            }
        if relation_codes:
            relations = {
                code: row for code, row in relations.items() if code in relation_codes
            }
        class_dicts, relation_dicts = ontology_payload(classes, relations)
        if not class_dicts:
            raise BadRequestError("触发时的本体实体类已被禁用或删除，请重新触发抽取")
        # 关系类型的端点若不在冻结的类里，抽取时无法校验，直接剔除
        relation_dicts = {
            code: meta
            for code, meta in relation_dicts.items()
            if meta["source_class_code"] in class_dicts
            and meta["target_class_code"] in class_dicts
        }
        return class_dicts, relation_dicts

    def _mark_failed(self, task_id: uuid.UUID, exc: Exception) -> None:
        task = self.tasks.get(task_id)
        if task is None:
            return
        message = str(exc)[:ERROR_MESSAGE_LIMIT] or exc.__class__.__name__
        error_code = "extraction_error" if isinstance(exc, AppError) else "internal_error"

        if task.status == ExtractionTaskStatus.RUNNING.value:
            task.status = ExtractionTaskStatus.FAILED.value
            task.error_code = error_code
            task.error_message = message
            task.finished_at = _now()
            self.db.commit()
        logger.warning("抽取任务失败 task_id=%s attempt=%s: %s", task_id, task.attempt, message)

    def _dispatch(self, task_id: str) -> None:
        try:
            _dispatcher(task_id)
        except Exception:  # noqa: BLE001 - 消息队列不可用不应让 HTTP 请求失败
            logger.exception("抽取任务投递失败 task_id=%s", task_id)

    def _get_or_404(self, principal: Principal, task_id: uuid.UUID) -> ExtractionTask:
        task = self.tasks.get(task_id)
        if task is None or task.workspace_id != principal.user.workspace_id:
            raise NotFoundError("抽取任务不存在")
        return task

    @staticmethod
    def _acquire_lock(redis: Any, key: str) -> bool:
        try:
            return bool(redis.set(key, "1", nx=True, ex=LOCK_TTL_SECONDS))
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
    "extraction_task_item",
    "ontology_payload",
    "set_dispatcher",
]
