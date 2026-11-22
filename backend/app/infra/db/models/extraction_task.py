"""extraction_task：一次知识抽取的执行记录。

只存业务状态，实体与关系落在 Neo4j（候选/已确认都在图里，用 status 区分）。
``ontology_version`` 冻结触发时的本体版本，``stats`` 记录批次、跳过块与修复次数。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import ExtractionTaskStatus
from app.infra.db.base import Base, UUIDPrimaryKeyMixin


class ExtractionTask(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "extraction_task"
    __table_args__ = (
        Index("ix_extraction_task_parse_status", "parse_task_id", "status"),
        Index("ix_extraction_task_workspace_status", "workspace_id", "status"),
        Index("ix_extraction_task_document", "document_id", "created_at"),
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
    parse_task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("parse_task.id", ondelete="CASCADE"), nullable=False
    )
    ontology_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ExtractionTaskStatus.PENDING.value,
        server_default=ExtractionTaskStatus.PENDING.value,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
