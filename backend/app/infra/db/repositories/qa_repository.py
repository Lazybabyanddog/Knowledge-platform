"""问答留痕与治理队列的数据访问。

查询条件里同时下推 workspace 与敏感级：治理端的计数也必须先过滤再统计，
否则"总共 12 条"本身就会泄露看不见的记录存在。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.infra.db.models.qa import QaFeedback, QaIssue, QaIssueEvent, QaIssueRecord, QaRecord


class QaRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ---------------- 记录 ----------------

    def add_record(self, record: QaRecord) -> QaRecord:
        self.db.add(record)
        self.db.flush()
        return record

    def get_record(self, record_id: uuid.UUID) -> QaRecord | None:
        return self.db.get(QaRecord, record_id)

    def get_record_for_update(self, record_id: uuid.UUID) -> QaRecord | None:
        stmt = select(QaRecord).where(QaRecord.id == record_id).with_for_update()
        return self.db.scalar(stmt)

    def list_records_for_reeval(
        self, workspace_id: uuid.UUID, sensitivities: list[str], *, limit: int = 5000
    ) -> list[QaRecord]:
        stmt = (
            select(QaRecord)
            .where(
                QaRecord.workspace_id == workspace_id,
                QaRecord.sensitivity.in_(sensitivities),
                QaRecord.answer_status == "completed",
            )
            .order_by(QaRecord.created_at.desc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt))

    # ---------------- 反馈 ----------------

    def get_feedback(self, record_id: uuid.UUID, user_id: uuid.UUID) -> QaFeedback | None:
        stmt = select(QaFeedback).where(
            QaFeedback.record_id == record_id, QaFeedback.user_id == user_id
        )
        return self.db.scalar(stmt)

    def add_feedback(self, feedback: QaFeedback) -> QaFeedback:
        self.db.add(feedback)
        self.db.flush()
        return feedback

    # ---------------- 簇 ----------------

    def add_issue(self, issue: QaIssue) -> QaIssue:
        self.db.add(issue)
        self.db.flush()
        return issue

    def get_issue(self, issue_id: uuid.UUID) -> QaIssue | None:
        return self.db.get(QaIssue, issue_id)

    def list_issues(
        self,
        *,
        workspace_id: uuid.UUID,
        sensitivities: list[str],
        status: str | None = None,
        disposition: str | None = None,
        reason: str | None = None,
        assignee_id: uuid.UUID | None = None,
        document_id: uuid.UUID | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[QaIssue], int]:
        stmt: Select = select(QaIssue).where(
            QaIssue.workspace_id == workspace_id,
            QaIssue.sensitivity.in_(sensitivities),
        )
        if status:
            stmt = stmt.where(QaIssue.status == status)
        if disposition:
            stmt = stmt.where(QaIssue.disposition == disposition)
        if reason:
            # JSONB 数组包含判断走 GIN 索引
            stmt = stmt.where(QaIssue.reasons.contains([reason]))
        if assignee_id:
            stmt = stmt.where(QaIssue.assignee_id == assignee_id)
        if document_id:
            stmt = stmt.where(QaIssue.primary_document_id == document_id)
        if created_from:
            stmt = stmt.where(QaIssue.last_seen_at >= created_from)
        if created_to:
            stmt = stmt.where(QaIssue.last_seen_at <= created_to)

        total = self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        # 影响面优先：有点踩的先看，再看被多少人问过，最后看最近发生
        rows = self.db.scalars(
            stmt.order_by(
                QaIssue.dislike_count.desc(),
                QaIssue.record_count.desc(),
                QaIssue.last_seen_at.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows), total

    def list_pending_issues_exact(
        self, workspace_id: uuid.UUID, sensitivity: str, document_id: uuid.UUID
    ) -> list[QaIssue]:
        stmt = select(QaIssue).where(
            QaIssue.workspace_id == workspace_id,
            QaIssue.sensitivity == sensitivity,
            QaIssue.status == "pending",
            QaIssue.primary_document_id == document_id,
        )
        return list(self.db.scalars(stmt))

    def find_nearest_pending_issue(
        self,
        workspace_id: uuid.UUID,
        sensitivity: str,
        vector: list[float],
        *,
        max_distance: float,
    ) -> QaIssue | None:
        distance = QaIssue.representative_vec.cosine_distance(vector).label("distance")
        stmt = (
            select(QaIssue, distance)
            .where(
                QaIssue.workspace_id == workspace_id,
                QaIssue.sensitivity == sensitivity,
                QaIssue.status == "pending",
                QaIssue.representative_vec.isnot(None),
            )
            .order_by(distance)
            .limit(1)
        )
        row = self.db.execute(stmt).first()
        if row is None:
            return None
        issue, dist = row
        return issue if float(dist) <= max_distance else None

    # ---------------- 簇成员 ----------------

    def get_membership(self, record_id: uuid.UUID) -> QaIssueRecord | None:
        stmt = select(QaIssueRecord).where(QaIssueRecord.record_id == record_id)
        return self.db.scalar(stmt)

    def add_membership(self, issue_id: uuid.UUID, record_id: uuid.UUID) -> QaIssueRecord:
        link = QaIssueRecord(issue_id=issue_id, record_id=record_id)
        self.db.add(link)
        self.db.flush()
        return link

    def delete_membership(self, link: QaIssueRecord) -> None:
        self.db.delete(link)
        self.db.flush()

    def list_member_records(
        self, issue_id: uuid.UUID, sensitivities: list[str], *, limit: int = 50
    ) -> list[QaRecord]:
        stmt = (
            select(QaRecord)
            .join(QaIssueRecord, QaIssueRecord.record_id == QaRecord.id)
            .where(
                QaIssueRecord.issue_id == issue_id,
                QaRecord.sensitivity.in_(sensitivities),
            )
            .order_by(QaRecord.created_at.desc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt))

    def count_member_records(
        self, issue_id: uuid.UUID, sensitivities: list[str]
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(QaIssueRecord)
            .join(QaRecord, QaRecord.id == QaIssueRecord.record_id)
            .where(
                QaIssueRecord.issue_id == issue_id,
                QaRecord.sensitivity.in_(sensitivities),
            )
        )
        return int(self.db.scalar(stmt) or 0)

    # ---------------- 轨迹 ----------------

    def add_event(self, event: QaIssueEvent) -> QaIssueEvent:
        self.db.add(event)
        self.db.flush()
        return event

    def list_events(self, issue_id: uuid.UUID) -> list[QaIssueEvent]:
        stmt = (
            select(QaIssueEvent)
            .where(QaIssueEvent.issue_id == issue_id)
            .order_by(QaIssueEvent.created_at.asc())
        )
        return list(self.db.scalars(stmt))
