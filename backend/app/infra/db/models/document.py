"""document / document_version / document_acl：文档及版本元信息。

原件二进制只进对象存储，这里仅保存 storage_key 与可检索的元数据；
版本采用追加写，任何历史版本都不被覆盖。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import DocumentStatus, Sensitivity, SourceType
from app.infra.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document"
    __table_args__ = (
        Index(
            "ix_document_list",
            "workspace_id",
            "status",
            "deleted_at",
            text("created_at DESC"),
        ),
        Index("ix_document_tags", "tags", postgresql_using="gin"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SourceType.UPLOAD.value, server_default="upload"
    )
    doc_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=DocumentStatus.DRAFT.value,
        server_default=DocumentStatus.DRAFT.value,
    )
    # 指向当前生效版本；不建外键：document 与 document_version 互相引用会形成建表环
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    sensitivity: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=Sensitivity.INTERNAL.value,
        server_default=Sensitivity.INTERNAL.value,
    )
    tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    extra_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )


class DocumentVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "document_version"
    __table_args__ = (
        UniqueConstraint("document_id", "version_no", name="uq_document_version_no"),
        UniqueConstraint("document_id", "checksum", name="uq_document_version_checksum"),
        Index("ix_document_version_document", "document_id", "version_no"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("document.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    change_note: Mapped[str | None] = mapped_column(String(500))
    # 检索索引状态：pending / building / ready / failed / skipped
    index_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    index_error: Mapped[str | None] = mapped_column(Text)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class DocumentAcl(UUIDPrimaryKeyMixin, Base):
    """对象级权限的预留表：本期只建表，判定逻辑走角色权限矩阵。"""

    __tablename__ = "document_acl"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "subject_type",
            "subject_id",
            "permission",
            name="uq_document_acl_subject",
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("document.id", ondelete="CASCADE"), nullable=False
    )
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    permission: Mapped[str] = mapped_column(String(16), nullable=False)
