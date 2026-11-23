"""extraction_task：一次知识抽取的执行记录。

只存业务状态，实体与关系落在 Neo4j（候选/已确认都在图里，用 status 区分）。
``ontology_version`` 冻结触发时的本体版本，``stats`` 记录批次、跳过块与修复次数。
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
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
    # 预算分层：attempt 是执行序号，下面三个各管一类来源，互不侵占
    auto_retries: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    downgrade_retries: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    manual_revives: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # 断点与当前输入是否匹配（批大小 + 块标识），不匹配则放弃断点从头执行
    checkpoint_fingerprint: Mapped[str | None] = mapped_column(String(64))
    # 下一次执行由谁排的：auto_retry / downgrade / manual_revive / skip_batches / watchdog
    next_source: Mapped[str | None] = mapped_column(String(24))
    # 跳过失败批次收尾时为真：任务成功但结果不完整
    incomplete: Mapped[bool] = mapped_column(
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ExtractionTaskAttempt(UUIDPrimaryKeyMixin, Base):
    """每次执行一条：谁跑的、跑出多少、为什么停。

    与 ``parse_task_attempt`` 同构，差别是抽取带批次进度与断点，因此多了
    ``error_detail``（可定位到批次）与 ``heartbeat_at``（看护判活用）。
    """

    __tablename__ = "extraction_task_attempt"
    __table_args__ = (
        UniqueConstraint("extraction_task_id", "attempt_no", name="uq_extraction_attempt_no"),
        Index("ix_extraction_attempt_heartbeat", "heartbeat_at"),
    )

    extraction_task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("extraction_task.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # manual / auto_retry / downgrade / manual_revive / skip_batches / watchdog
    source: Mapped[str] = mapped_column(
        String(24), nullable=False, default="manual", server_default="manual"
    )
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExtractionBatchCheckpoint(UUIDPrimaryKeyMixin, Base):
    """批次级断点：每批算完立即落一行，恢复时按行拼回。

    ``(extraction_task_id, batch_index)`` 唯一既是幂等也是 fencing —— 新旧执行
    同时写同一批时 ``ON CONFLICT DO NOTHING``，先到先得，谁也不会覆盖谁。
    """

    __tablename__ = "extraction_batch_checkpoint"
    __table_args__ = (
        UniqueConstraint(
            "extraction_task_id", "batch_index", name="uq_extraction_checkpoint_batch"
        ),
        Index("ix_extraction_checkpoint_task_status", "extraction_task_id", "status"),
    )

    extraction_task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("extraction_task.id", ondelete="CASCADE"),
        nullable=False,
    )
    batch_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    block_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    # {"entities": [...], "relation_rows": [...]}，成功后清空只留计数
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
