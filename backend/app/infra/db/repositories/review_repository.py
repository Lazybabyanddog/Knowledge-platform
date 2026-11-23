"""审批流、审批实例与审批轨迹的查询封装。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Select, String, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from app.infra.db.models import AppUser, Document, DocumentVersion, ReviewFlow
from app.infra.db.models.review import ReviewInstance, ReviewRecord


class ReviewRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ---- 审批流 ----
    def add_flow(self, flow: ReviewFlow) -> ReviewFlow:
        self.db.add(flow)
        return flow

    def delete_flow(self, flow: ReviewFlow) -> None:
        self.db.delete(flow)

    def get_flow(
        self, flow_id: uuid.UUID, *, workspace_id: uuid.UUID | None = None
    ) -> ReviewFlow | None:
        conditions = [ReviewFlow.id == flow_id]
        if workspace_id is not None:
            conditions.append(ReviewFlow.workspace_id == workspace_id)
        return self.db.scalar(select(ReviewFlow).where(*conditions))

    def list_flows(
        self,
        workspace_id: uuid.UUID,
        *,
        doc_type: str | None = None,
        include_disabled: bool = False,
    ) -> list[ReviewFlow]:
        conditions = [ReviewFlow.workspace_id == workspace_id]
        if doc_type:
            conditions.append(ReviewFlow.doc_type == doc_type)
        if not include_disabled:
            conditions.append(ReviewFlow.status == "active")
        return list(
            self.db.scalars(
                select(ReviewFlow)
                .where(*conditions)
                .order_by(ReviewFlow.doc_type, ReviewFlow.is_default.desc(), ReviewFlow.name)
            ).all()
        )

    def default_flow(self, workspace_id: uuid.UUID, doc_type: str) -> ReviewFlow | None:
        return self.db.scalar(
            select(ReviewFlow).where(
                ReviewFlow.workspace_id == workspace_id,
                ReviewFlow.doc_type == doc_type,
                ReviewFlow.is_default.is_(True),
                ReviewFlow.status == "active",
            )
        )

    def clear_default(
        self, workspace_id: uuid.UUID, doc_type: str, *, exclude_id: uuid.UUID | None = None
    ) -> None:
        """把同类型下其他默认流取消，保证"每类型至多一个默认"。"""
        conditions = [
            ReviewFlow.workspace_id == workspace_id,
            ReviewFlow.doc_type == doc_type,
            ReviewFlow.is_default.is_(True),
        ]
        if exclude_id is not None:
            conditions.append(ReviewFlow.id != exclude_id)
        for flow in self.db.scalars(select(ReviewFlow).where(*conditions)).all():
            flow.is_default = False

    # ---- 审批实例 ----
    def add_instance(self, instance: ReviewInstance) -> ReviewInstance:
        self.db.add(instance)
        return instance

    def get_instance(
        self,
        instance_id: uuid.UUID,
        *,
        workspace_id: uuid.UUID | None = None,
        for_update: bool = False,
    ) -> ReviewInstance | None:
        conditions = [ReviewInstance.id == instance_id]
        if workspace_id is not None:
            conditions.append(ReviewInstance.workspace_id == workspace_id)
        stmt: Select = select(ReviewInstance).where(*conditions)
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.scalar(stmt)

    def active_instance(self, document_id: uuid.UUID) -> ReviewInstance | None:
        return self.db.scalar(
            select(ReviewInstance).where(
                ReviewInstance.document_id == document_id,
                ReviewInstance.status == "pending",
            )
        )

    def list_instances(
        self,
        *,
        workspace_id: uuid.UUID,
        status: str | None = None,
        doc_type: str | None = None,
        submitted_by: uuid.UUID | None = None,
        document_id: uuid.UUID | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        approver_roles: list[str] | None = None,
        approver_user_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 20,
        newest_first: bool = True,
    ) -> tuple[list[tuple[ReviewInstance, str, str, int, str | None]], int]:
        """返回 (实例, 文档标题, 文档类型, 版本号, 提交人显示名) 的分页结果。"""
        conditions = [ReviewInstance.workspace_id == workspace_id]
        if status:
            conditions.append(ReviewInstance.status == status)
        if doc_type:
            conditions.append(Document.doc_type == doc_type)
        if submitted_by is not None:
            conditions.append(ReviewInstance.submitted_by == submitted_by)
        if document_id is not None:
            conditions.append(ReviewInstance.document_id == document_id)
        if created_from is not None:
            conditions.append(ReviewInstance.submitted_at >= created_from)
        if created_to is not None:
            conditions.append(ReviewInstance.submitted_at <= created_to)

        if approver_roles is not None or approver_user_id is not None:
            # 步骤数组 0 基、current_step 1 基，取当前步骤的审批人配置
            step_path = cast(ReviewInstance.current_step - 1, String)
            step_type = func.jsonb_extract_path_text(
                ReviewInstance.flow_snapshot, step_path, "approver_type"
            )
            step_value = func.jsonb_extract_path_text(
                ReviewInstance.flow_snapshot, step_path, "approver_value"
            )
            assignment = []
            if approver_roles:
                assignment.append(
                    and_(step_type == "role", step_value.in_(list(approver_roles)))
                )
            if approver_user_id is not None:
                assignment.append(
                    and_(step_type == "user", step_value == str(approver_user_id))
                )
            if assignment:
                conditions.append(or_(*assignment))

        base = (
            select(ReviewInstance.id)
            .join(Document, Document.id == ReviewInstance.document_id)
            .where(*conditions)
        )
        total = self.db.scalar(select(func.count()).select_from(base.subquery()))

        ordering = (
            ReviewInstance.submitted_at.desc()
            if newest_first
            else ReviewInstance.submitted_at.asc()
        )
        rows = self.db.execute(
            select(
                ReviewInstance,
                Document.title,
                Document.doc_type,
                DocumentVersion.version_no,
                AppUser.display_name,
            )
            .join(Document, Document.id == ReviewInstance.document_id)
            .join(DocumentVersion, DocumentVersion.id == ReviewInstance.document_version_id)
            .outerjoin(AppUser, AppUser.id == ReviewInstance.submitted_by)
            .where(*conditions)
            .order_by(ordering, ReviewInstance.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return [(row[0], row[1], row[2], row[3], row[4]) for row in rows], int(total or 0)

    # ---- 审批轨迹 ----
    def add_record(self, record: ReviewRecord) -> ReviewRecord:
        self.db.add(record)
        return record

    def list_records(self, instance_id: uuid.UUID) -> list[ReviewRecord]:
        return list(
            self.db.scalars(
                select(ReviewRecord)
                .where(ReviewRecord.instance_id == instance_id)
                .order_by(ReviewRecord.created_at, ReviewRecord.step_no)
            ).all()
        )

    def has_action(
        self,
        instance_id: uuid.UUID,
        *,
        step_no: int,
        actor_id: uuid.UUID,
        action: str,
    ) -> bool:
        return (
            self.db.scalar(
                select(ReviewRecord.id).where(
                    ReviewRecord.instance_id == instance_id,
                    ReviewRecord.step_no == step_no,
                    ReviewRecord.actor_id == actor_id,
                    ReviewRecord.action == action,
                )
            )
            is not None
        )

    def latest_record_times(self, instance_ids: list[uuid.UUID]) -> dict[uuid.UUID, datetime]:
        """每个实例最后一次动作的时间，用于计算当前步骤已等待多久。"""
        if not instance_ids:
            return {}
        rows = self.db.execute(
            select(ReviewRecord.instance_id, func.max(ReviewRecord.created_at))
            .where(ReviewRecord.instance_id.in_(instance_ids))
            .group_by(ReviewRecord.instance_id)
        ).all()
        return {row[0]: row[1] for row in rows}
