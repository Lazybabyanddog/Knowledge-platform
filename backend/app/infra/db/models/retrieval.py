"""document_chunk：检索片段及其向量。

片段是检索与引用的最小单位，由同一解析结果中相邻的块合并而来。
``workspace_id`` 冗余存一份，检索时无需为了权限过滤再 join 一次 document。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
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

from app.infra.db.base import Base, UUIDPrimaryKeyMixin

EMBEDDING_DIM = 1024


class DocumentChunk(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "document_chunk"
    __table_args__ = (
        # 同一解析结果内的片段顺序唯一，重复索引不会产生重复片段
        UniqueConstraint("parse_task_id", "seq", name="uq_document_chunk_seq"),
        Index("ix_document_chunk_version", "document_version_id", "seq"),
        Index("ix_document_chunk_workspace_document", "workspace_id", "document_id"),
        Index(
            "ix_document_chunk_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("document.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("document_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    parse_task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("parse_task.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    heading_path: Mapped[str | None] = mapped_column(String(512))
    # 覆盖的块 ID，保序；引用回到原文时按此取 bbox
    block_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    first_page_no: Mapped[int | None] = mapped_column(Integer)
    last_page_no: Mapped[int | None] = mapped_column(Integer)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    @property
    def page_no(self) -> int | None:
        return self.first_page_no

    def metadata_dict(self) -> dict[str, Any]:
        """给接口层的轻量摘要，避免把 embedding 带进响应。"""
        return {
            "heading_path": self.heading_path,
            "block_ids": self.block_ids,
        }
