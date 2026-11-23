"""ontology attribute schema and entity identity

Revision ID: a7c3f1b90e42
Revises: d5b2f8e13c41
Create Date: 2026-11-22 18:10:00.000000

三件事：给关系类型补属性定义（与实体类对称）、给实体类补有序标识键、
建存量重抽批次表。

只加列/加表，不改列类型，也不改写存量 ``attributes`` JSON——
旧格式（仅 ``type``/``description``）在应用层读取时补默认值，
避免一次性数据迁移出错后难以回退。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7c3f1b90e42"
down_revision: str | Sequence[str] | None = "d5b2f8e13c41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ontology_relation",
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "ontology_class",
        sa.Column(
            "identity_keys",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    op.create_table(
        "extraction_backfill_batch",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("force", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "scope",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "report",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_extraction_backfill_workspace_created",
        "extraction_backfill_batch",
        ["workspace_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_extraction_backfill_workspace_created",
        table_name="extraction_backfill_batch",
    )
    op.drop_table("extraction_backfill_batch")
    op.drop_column("ontology_class", "identity_keys")
    op.drop_column("ontology_relation", "attributes")
