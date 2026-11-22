"""review_flow / review_instance / review_record：文档审批的配置、实例与轨迹。

三张表各司其职：
- ``review_flow`` 存配置（步骤是 JSONB 数组，按 step_no 有序）；
- ``review_instance`` 是"一次提交"，提交时把流程快照进 ``flow_snapshot``，
  之后流程被改也不影响在途审批；
- ``review_record`` 是只追加的轨迹，每一步的审批人、动作、意见都在这里。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import ReviewAction, ReviewInstanceStatus
from app.infra.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ReviewFlow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "review_flow"
    __table_args__ = (
        Index("ix_review_flow_workspace_doc_type", "workspace_id", "doc_type"),
        # 每种文档类型至多一个默认流；部分唯一索引把约束交给数据库兜底
        Index(
            "uq_review_flow_default",
            "workspace_id",
            "doc_type",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    doc_type: Mapped[str] = mapped_column(String(16), nullable=False)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )


class ReviewInstance(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "review_instance"
    __table_args__ = (
        Index("ix_review_instance_status_step", "status", "current_step"),
        Index("ix_review_instance_workspace_status", "workspace_id", "status"),
        Index("ix_review_instance_document", "document_id", "submitted_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("document.id", ondelete="CASCADE"), nullable=False
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("document_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    flow_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("review_flow.id", ondelete="SET NULL")
    )
    flow_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # 提交瞬间的流程快照：流程后续被改/被删都不改变在途实例的走向
    flow_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ReviewInstanceStatus.PENDING.value,
        server_default=ReviewInstanceStatus.PENDING.value,
    )
    current_step: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewRecord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "review_record"
    __table_args__ = (
        Index("ix_review_record_instance", "instance_id", "created_at"),
        # 同一步骤同一个人同一动作只能留一条，重复批准由数据库兜底
        UniqueConstraint(
            "instance_id", "step_no", "actor_id", "action", name="uq_review_record_action"
        ),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("review_instance.id", ondelete="CASCADE"), nullable=False
    )
    step_no: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str | None] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ReviewAction.APPROVE.value
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    # 冗余审批人名字快照：用户改名或离职后轨迹仍然可读
    actor_name: Mapped[str | None] = mapped_column(String(128))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
