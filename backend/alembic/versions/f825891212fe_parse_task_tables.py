"""parse task tables

Revision ID: f825891212fe
Revises: bb10d05f4c15
Create Date: 2026-11-21 21:12:24.134622
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f825891212fe"
down_revision: str | Sequence[str] | None = "bb10d05f4c15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "parse_task",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("document_version_id", sa.UUID(), nullable=False),
        sa.Column("strategy_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("quality_score", sa.Integer(), nullable=True),
        sa.Column("low_quality", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("stats", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["document_version_id"], ["document_version.id"], ondelete="CASCADE"
        ),
        # 策略行不可删，历史任务必须能追溯到执行时的版本
        sa.ForeignKeyConstraint(["strategy_id"], ["parse_strategy.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_parse_task_document", "parse_task", ["document_id", "created_at"], unique=False
    )
    op.create_index(
        "ix_parse_task_version_status",
        "parse_task",
        ["document_version_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_parse_task_workspace_status", "parse_task", ["workspace_id", "status"], unique=False
    )

    op.create_table(
        "parse_block",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("parse_task_id", sa.UUID(), nullable=False),
        sa.Column("document_version_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("block_type", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=True),
        sa.Column("bbox", JSONB, nullable=True),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("parent_block_id", sa.UUID(), nullable=True),
        sa.Column("is_discarded", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_version_id"], ["document_version.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["parse_task_id"], ["parse_task.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parse_task_id", "seq", name="uq_parse_block_seq"),
    )
    op.create_index(
        "ix_parse_block_task_seq", "parse_block", ["parse_task_id", "seq"], unique=False
    )
    op.create_index(
        "ix_parse_block_version_seq", "parse_block", ["document_version_id", "seq"], unique=False
    )

    op.create_table(
        "parse_task_attempt",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("parse_task_id", sa.UUID(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("quality_score", sa.Integer(), nullable=True),
        sa.Column("stats", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["parse_task_id"], ["parse_task.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parse_task_id", "attempt_no", name="uq_parse_attempt_no"),
    )


def downgrade() -> None:
    op.drop_table("parse_task_attempt")
    op.drop_index("ix_parse_block_version_seq", table_name="parse_block")
    op.drop_index("ix_parse_block_task_seq", table_name="parse_block")
    op.drop_table("parse_block")
    op.drop_index("ix_parse_task_workspace_status", table_name="parse_task")
    op.drop_index("ix_parse_task_version_status", table_name="parse_task")
    op.drop_index("ix_parse_task_document", table_name="parse_task")
    op.drop_table("parse_task")
