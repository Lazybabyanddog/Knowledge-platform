"""抽取任务仓储。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.domain.enums import ExtractionTaskStatus
from app.infra.db.models import (
    ExtractionBatchCheckpoint,
    ExtractionTask,
    ExtractionTaskAttempt,
)


class ExtractionTaskRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, task_id: uuid.UUID) -> ExtractionTask | None:
        return self.db.get(ExtractionTask, task_id)

    def add(self, task: ExtractionTask) -> ExtractionTask:
        self.db.add(task)
        return task

    def find_active(self, parse_task_id: uuid.UUID) -> ExtractionTask | None:
        """同一解析任务的未完成任务，用于阻止重复投递。"""
        return self.db.scalar(
            select(ExtractionTask).where(
                ExtractionTask.parse_task_id == parse_task_id,
                ExtractionTask.status.in_(
                    [
                        ExtractionTaskStatus.PENDING.value,
                        ExtractionTaskStatus.RUNNING.value,
                    ]
                ),
            )
        )

    def list_paginated(
        self,
        *,
        workspace_id: uuid.UUID,
        document_id: uuid.UUID | None = None,
        parse_task_id: uuid.UUID | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ExtractionTask], int]:
        conditions = [ExtractionTask.workspace_id == workspace_id]
        if document_id:
            conditions.append(ExtractionTask.document_id == document_id)
        if parse_task_id:
            conditions.append(ExtractionTask.parse_task_id == parse_task_id)
        if status:
            conditions.append(ExtractionTask.status == status)

        total = self.db.scalar(
            select(func.count()).select_from(ExtractionTask).where(*conditions)
        )
        rows = list(
            self.db.scalars(
                select(ExtractionTask)
                .where(*conditions)
                .order_by(ExtractionTask.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, int(total or 0)

    def list_attempts(self, task_id: uuid.UUID) -> list[ExtractionTaskAttempt]:
        """按执行序号正序：调用方要倒序展示时自己反转，避免两套排序口径。"""
        return list(
            self.db.scalars(
                select(ExtractionTaskAttempt)
                .where(ExtractionTaskAttempt.extraction_task_id == task_id)
                .order_by(ExtractionTaskAttempt.attempt_no)
            )
        )

    def latest_attempt(self, task_id: uuid.UUID) -> ExtractionTaskAttempt | None:
        return self.db.scalar(
            select(ExtractionTaskAttempt)
            .where(ExtractionTaskAttempt.extraction_task_id == task_id)
            .order_by(ExtractionTaskAttempt.attempt_no.desc())
            .limit(1)
        )

    def add_attempt(self, attempt: ExtractionTaskAttempt) -> ExtractionTaskAttempt:
        self.db.add(attempt)
        return attempt

    def list_running(self) -> list[ExtractionTask]:
        """看护扫描的候选集：只有 RUNNING 才需要判活。"""
        return list(
            self.db.scalars(
                select(ExtractionTask).where(
                    ExtractionTask.status == ExtractionTaskStatus.RUNNING.value
                )
            )
        )


class ExtractionCheckpointRepository:
    """批次断点：写入只增不覆盖，恢复按批序号读回。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def upsert(
        self,
        *,
        task_id: uuid.UUID,
        batch_index: int,
        status: str,
        block_ids: list[uuid.UUID],
        payload: dict[str, Any],
        attempt_no: int,
    ) -> None:
        """同一批先到先得：ON CONFLICT DO NOTHING 既是幂等也是 fencing。"""
        stmt = (
            pg_insert(ExtractionBatchCheckpoint)
            .values(
                extraction_task_id=task_id,
                batch_index=batch_index,
                status=status,
                block_ids=block_ids,
                payload=payload,
                attempt_no=attempt_no,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    ExtractionBatchCheckpoint.extraction_task_id,
                    ExtractionBatchCheckpoint.batch_index,
                ]
            )
        )
        self.db.execute(stmt)

    def list_for_task(self, task_id: uuid.UUID) -> list[ExtractionBatchCheckpoint]:
        return list(
            self.db.scalars(
                select(ExtractionBatchCheckpoint)
                .where(ExtractionBatchCheckpoint.extraction_task_id == task_id)
                .order_by(ExtractionBatchCheckpoint.batch_index)
            )
        )

    def summarize(self, task_id: uuid.UUID) -> dict[str, Any]:
        """进度聚合：只返回计数与最大批序号，总批数由调用方按块重算。"""
        rows = self.db.execute(
            select(
                ExtractionBatchCheckpoint.status,
                func.count(),
                func.max(ExtractionBatchCheckpoint.batch_index),
            )
            .where(ExtractionBatchCheckpoint.extraction_task_id == task_id)
            .group_by(ExtractionBatchCheckpoint.status)
        ).all()
        summary: dict[str, Any] = {"committed": 0, "skipped": 0, "max_batch_index": None}
        for status, count, max_index in rows:
            if status in summary:
                summary[status] = int(count)
            if max_index is not None:
                current = summary["max_batch_index"]
                summary["max_batch_index"] = (
                    max_index if current is None else max(current, max_index)
                )
        return summary

    def delete_for_task(self, task_id: uuid.UUID) -> None:
        self.db.execute(
            delete(ExtractionBatchCheckpoint).where(
                ExtractionBatchCheckpoint.extraction_task_id == task_id
            )
        )

    def mark_consumed(self, task_id: uuid.UUID) -> None:
        """成功收尾：断点不再参与恢复，同时清掉 payload 只留计数。

        跳过原因要留着：任务成功但结果不完整时，页面还要回答"为什么少了这批"。
        """
        now = datetime.now(UTC)
        rows = list(
            self.db.scalars(
                select(ExtractionBatchCheckpoint).where(
                    ExtractionBatchCheckpoint.extraction_task_id == task_id
                )
            )
        )
        for row in rows:
            row.consumed_at = now
            payload = dict(row.payload or {})
            if row.status == "skipped" and payload.get("reason"):
                row.payload = {
                    "reason": payload["reason"],
                    "manual": bool(payload.get("manual")),
                }
            else:
                row.payload = {}
