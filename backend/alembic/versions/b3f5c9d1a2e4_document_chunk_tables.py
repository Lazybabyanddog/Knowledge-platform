"""document chunk tables

Revision ID: b3f5c9d1a2e4
Revises: d667c77c8b2b
Create Date: 2026-11-22 10:12:00.000000

检索片段表与版本索引状态列。向量列为 1024 维，与 EMBEDDING_DIM 一致；
改维度需要新建迁移重建列并全量重算，不能原地 ALTER。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "b3f5c9d1a2e4"
down_revision: str | Sequence[str] | None = "d667c77c8b2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "document_chunk",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("document_version_id", sa.UUID(), nullable=False),
        sa.Column("parse_task_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("heading_path", sa.String(length=512), nullable=True),
        sa.Column(
            "block_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("first_page_no", sa.Integer(), nullable=True),
        sa.Column("last_page_no", sa.Integer(), nullable=True),
        sa.Column("char_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("embedding_model", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_version.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parse_task_id"], ["parse_task.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parse_task_id", "seq", name="uq_document_chunk_seq"),
    )
    op.create_index(
        "ix_document_chunk_version", "document_chunk", ["document_version_id", "seq"], unique=False
    )
    op.create_index(
        "ix_document_chunk_workspace_document",
        "document_chunk",
        ["workspace_id", "document_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_chunk_embedding",
        "document_chunk",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.add_column(
        "document_version",
        sa.Column("index_status", sa.String(length=16), server_default="pending", nullable=False),
    )
    op.add_column("document_version", sa.Column("index_error", sa.Text(), nullable=True))
    op.add_column(
        "document_version", sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "document_version",
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("document_version", "chunk_count")
    op.drop_column("document_version", "indexed_at")
    op.drop_column("document_version", "index_error")
    op.drop_column("document_version", "index_status")

    op.drop_index(
        "ix_document_chunk_embedding",
        table_name="document_chunk",
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_index("ix_document_chunk_workspace_document", table_name="document_chunk")
    op.drop_index("ix_document_chunk_version", table_name="document_chunk")
    op.drop_table("document_chunk")
