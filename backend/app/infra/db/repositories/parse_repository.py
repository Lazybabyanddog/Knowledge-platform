"""parse_strategy / parse_task / parse_block 的查询封装。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.orm import Session

from app.domain.enums import ParseTaskStatus, StrategyStatus
from app.infra.db.models import ParseBlock, ParseStrategy, ParseTask, ParseTaskAttempt


class ParseStrategyRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, strategy_id: uuid.UUID) -> ParseStrategy | None:
        return self.db.get(ParseStrategy, strategy_id)

    def list_latest(
        self, workspace_id: uuid.UUID, doc_type: str | None = None
    ) -> list[ParseStrategy]:
        """每个 strategy_key 只返回最新版本，默认列表不暴露历史版本。"""
        latest = (
            select(
                ParseStrategy.strategy_key.label("strategy_key"),
                func.max(ParseStrategy.version).label("version"),
            )
            .where(ParseStrategy.workspace_id == workspace_id)
            .group_by(ParseStrategy.strategy_key)
            .subquery()
        )
        stmt = (
            select(ParseStrategy)
            .join(
                latest,
                and_(
                    ParseStrategy.strategy_key == latest.c.strategy_key,
                    ParseStrategy.version == latest.c.version,
                ),
            )
            .where(ParseStrategy.status == StrategyStatus.ACTIVE.value)
            .order_by(ParseStrategy.doc_type, ParseStrategy.name)
        )
        if doc_type:
            stmt = stmt.where(ParseStrategy.doc_type == doc_type)
        return list(self.db.scalars(stmt))

    def latest_of_key(self, workspace_id: uuid.UUID, strategy_key: str) -> ParseStrategy | None:
        return self.db.scalar(
            select(ParseStrategy)
            .where(
                ParseStrategy.workspace_id == workspace_id,
                ParseStrategy.strategy_key == strategy_key,
            )
            .order_by(ParseStrategy.version.desc())
            .limit(1)
        )

    def find_default(self, workspace_id: uuid.UUID, doc_type: str) -> ParseStrategy | None:
        return self.db.scalar(
            select(ParseStrategy).where(
                ParseStrategy.workspace_id == workspace_id,
                ParseStrategy.doc_type == doc_type,
                ParseStrategy.is_default.is_(True),
                ParseStrategy.status == StrategyStatus.ACTIVE.value,
            )
        )

    def clear_default(
        self,
        workspace_id: uuid.UUID,
        doc_type: str,
        *,
        exclude_key: str | None = None,
    ) -> None:
        stmt = (
            update(ParseStrategy)
            .where(
                ParseStrategy.workspace_id == workspace_id,
                ParseStrategy.doc_type == doc_type,
                ParseStrategy.is_default.is_(True),
            )
            .values(is_default=False)
        )
        if exclude_key:
            stmt = stmt.where(ParseStrategy.strategy_key != exclude_key)
        self.db.execute(stmt)

    def clear_key_default(self, workspace_id: uuid.UUID, strategy_key: str) -> None:
        """旧版本不再持有默认标记，避免与部分唯一索引冲突。"""
        self.db.execute(
            update(ParseStrategy)
            .where(
                ParseStrategy.workspace_id == workspace_id,
                ParseStrategy.strategy_key == strategy_key,
                ParseStrategy.is_default.is_(True),
            )
            .values(is_default=False)
        )

    def add(self, strategy: ParseStrategy) -> ParseStrategy:
        self.db.add(strategy)
        return strategy


class ParseTaskRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, task_id: uuid.UUID) -> ParseTask | None:
        return self.db.get(ParseTask, task_id)

    def add(self, task: ParseTask) -> ParseTask:
        self.db.add(task)
        return task

    def find_active(self, document_version_id: uuid.UUID) -> ParseTask | None:
        return self.db.scalar(
            select(ParseTask).where(
                ParseTask.document_version_id == document_version_id,
                ParseTask.status.in_(
                    [ParseTaskStatus.PENDING.value, ParseTaskStatus.RUNNING.value]
                ),
            )
        )

    def list_paginated(
        self,
        *,
        workspace_id: uuid.UUID,
        document_id: uuid.UUID | None = None,
        status: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ParseTask], int]:
        conditions = [ParseTask.workspace_id == workspace_id]
        if document_id:
            conditions.append(ParseTask.document_id == document_id)
        if status:
            conditions.append(ParseTask.status == status)
        if created_from:
            conditions.append(ParseTask.created_at >= created_from)
        if created_to:
            conditions.append(ParseTask.created_at <= created_to)

        total = self.db.scalar(
            select(func.count()).select_from(ParseTask).where(*conditions)
        )
        rows = list(
            self.db.scalars(
                select(ParseTask)
                .where(*conditions)
                .order_by(ParseTask.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, int(total or 0)

    def list_attempts(self, task_id: uuid.UUID) -> list[ParseTaskAttempt]:
        return list(
            self.db.scalars(
                select(ParseTaskAttempt)
                .where(ParseTaskAttempt.parse_task_id == task_id)
                .order_by(ParseTaskAttempt.attempt_no)
            )
        )

    def add_attempt(self, attempt: ParseTaskAttempt) -> ParseTaskAttempt:
        self.db.add(attempt)
        return attempt


class ParseBlockRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add_all(self, rows: list[ParseBlock]) -> None:
        self.db.add_all(rows)

    def delete_for_task(self, task_id: uuid.UUID) -> None:
        self.db.execute(delete(ParseBlock).where(ParseBlock.parse_task_id == task_id))

    def list_paginated(
        self,
        task_id: uuid.UUID,
        *,
        include_discarded: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[ParseBlock], int]:
        conditions: list[Any] = [ParseBlock.parse_task_id == task_id]
        if not include_discarded:
            conditions.append(ParseBlock.is_discarded.is_(False))
        total = self.db.scalar(
            select(func.count()).select_from(ParseBlock).where(*conditions)
        )
        rows = list(
            self.db.scalars(
                select(ParseBlock)
                .where(*conditions)
                .order_by(ParseBlock.seq)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, int(total or 0)

    def list_all(
        self, task_id: uuid.UUID, *, include_discarded: bool = False
    ) -> list[ParseBlock]:
        """抽取需要按顺序通读全部块，这里不做分页。"""
        conditions: list[Any] = [ParseBlock.parse_task_id == task_id]
        if not include_discarded:
            conditions.append(ParseBlock.is_discarded.is_(False))
        return list(
            self.db.scalars(
                select(ParseBlock).where(*conditions).order_by(ParseBlock.seq)
            )
        )

    def get_many(self, block_ids: list[uuid.UUID]) -> list[ParseBlock]:
        if not block_ids:
            return []
        return list(
            self.db.scalars(
                select(ParseBlock)
                .where(ParseBlock.id.in_(block_ids))
                .order_by(ParseBlock.seq)
            )
        )

    def count(self, task_id: uuid.UUID) -> int:
        return int(
            self.db.scalar(
                select(func.count())
                .select_from(ParseBlock)
                .where(ParseBlock.parse_task_id == task_id)
            )
            or 0
        )

    def counts_for_tasks(self, task_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        if not task_ids:
            return {}
        rows = self.db.execute(
            select(ParseBlock.parse_task_id, func.count())
            .where(ParseBlock.parse_task_id.in_(task_ids))
            .group_by(ParseBlock.parse_task_id)
        ).all()
        return {task_id: int(count) for task_id, count in rows}
