"""抽取任务仓储。"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import ExtractionTaskStatus
from app.infra.db.models import ExtractionTask


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
