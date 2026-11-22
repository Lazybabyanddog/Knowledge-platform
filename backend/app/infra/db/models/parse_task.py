"""parse_task / parse_task_attempt / parse_block：解析任务与产物。

一次触发一条 parse_task；每次执行（含重试）一条 attempt，失败历史永不覆盖；
产物按 ``(parse_task_id, seq)`` 唯一，保证原文顺序稳定且重复投递不产生重复块。
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

from app.domain.enums import ParseTaskStatus
from app.infra.db.base import Base, UUIDPrimaryKeyMixin


class ParseTask(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "parse_task"
    __table_args__ = (
        Index("ix_parse_task_version_status", "document_version_id", "status"),
        Index("ix_parse_task_document", "document_id", "created_at"),
        Index("ix_parse_task_workspace_status", "workspace_id", "status"),
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
    # 策略版本不可变，这里固定引用执行时的具体版本行
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("parse_strategy.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ParseTaskStatus.PENDING.value,
        server_default=ParseTaskStatus.PENDING.value,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    quality_score: Mapped[int | None] = mapped_column(Integer)
    low_quality: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ParseTaskAttempt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "parse_task_attempt"
    __table_args__ = (
        UniqueConstraint("parse_task_id", "attempt_no", name="uq_parse_attempt_no"),
    )

    parse_task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("parse_task.id", ondelete="CASCADE"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    quality_score: Mapped[int | None] = mapped_column(Integer)
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ParseBlock(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "parse_block"
    __table_args__ = (
        UniqueConstraint("parse_task_id", "seq", name="uq_parse_block_seq"),
        Index("ix_parse_block_version_seq", "document_version_id", "seq"),
        Index("ix_parse_block_task_seq", "parse_task_id", "seq"),
    )

    parse_task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("parse_task.id", ondelete="CASCADE"), nullable=False
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("document_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    page_no: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    level: Mapped[int | None] = mapped_column(Integer)
    parent_block_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    is_discarded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    block_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
