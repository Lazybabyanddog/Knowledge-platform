"""document tables

Revision ID: bb10d05f4c15
Revises: b3f7c2e91a04
Create Date: 2026-11-21 21:03:16.613394

document 列表走 (workspace_id, status, deleted_at, created_at DESC) 复合索引，
标签筛选走 GIN；版本表用两条唯一约束同时保证"版本号唯一"和"内容不重复"。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "bb10d05f4c15"
down_revision: str | Sequence[str] | None = "b3f7c2e91a04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(length=16), server_default="upload", nullable=False),
        sa.Column("doc_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("current_version_id", sa.UUID(), nullable=True),
        sa.Column("owner_id", sa.UUID(), nullable=True),
        sa.Column(
            "sensitivity", sa.String(length=16), server_default="internal", nullable=False
        ),
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "extra_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_document_list",
        "document",
        ["workspace_id", "status", "deleted_at", sa.text("created_at DESC")],
    )
    op.create_index("ix_document_tags", "document", ["tags"], postgresql_using="gin")

    op.create_table(
        "document_version",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("is_current", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("change_note", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "checksum", name="uq_document_version_checksum"),
        sa.UniqueConstraint("document_id", "version_no", name="uq_document_version_no"),
    )
    op.create_index(
        "ix_document_version_document", "document_version", ["document_id", "version_no"]
    )

    op.create_table(
        "document_acl",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("permission", sa.String(length=16), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "subject_type",
            "subject_id",
            "permission",
            name="uq_document_acl_subject",
        ),
    )


def downgrade() -> None:
    op.drop_table("document_acl")
    op.drop_index("ix_document_version_document", table_name="document_version")
    op.drop_table("document_version")
    op.drop_index("ix_document_tags", table_name="document", postgresql_using="gin")
    op.drop_index("ix_document_list", table_name="document")
    op.drop_table("document")
