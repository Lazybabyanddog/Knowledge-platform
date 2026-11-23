"""task retry and recovery

Revision ID: c4a1e7d92b30
Revises: a7c4e2d91f60
Create Date: 2026-11-22 16:10:00.000000

把"重试预算"从 ``attempt`` 里拆出来，并给抽取补上与解析同构的执行历史：

- ``attempt`` 保留为执行序号（只增，兼作写入版本号），预算改由
  ``auto_retries`` / ``downgrade_retries`` / ``manual_revives`` 分别计数；
- ``parse_task_attempt`` 补 ``source`` / ``error_detail`` / ``duration_ms`` /
  ``heartbeat_at``，历史行统一视为人工触发，耗时由起止时间推导；
- 新增 ``extraction_task_attempt`` 与 ``extraction_batch_checkpoint``，
  后者用 ``(extraction_task_id, batch_index)`` 唯一约束做幂等与 fencing。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c4a1e7d92b30"
down_revision: str | Sequence[str] | None = "a7c4e2d91f60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BUDGET_COLUMNS = (
    sa.Column("auto_retries", sa.Integer(), server_default="0", nullable=False),
    sa.Column("downgrade_retries", sa.Integer(), server_default="0", nullable=False),
    sa.Column("manual_revives", sa.Integer(), server_default="0", nullable=False),
)

# server_default 一次性把存量行也填成 manual，避免历史记录出现空来源
_ATTEMPT_COLUMNS = (
    sa.Column("source", sa.String(length=24), server_default="manual", nullable=False),
    sa.Column("error_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column("duration_ms", sa.Integer(), nullable=True),
    sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
)


def upgrade() -> None:
    for table in ("parse_task", "extraction_task"):
        for column in _BUDGET_COLUMNS:
            op.add_column(table, column.copy())

    op.add_column(
        "extraction_task",
        sa.Column("checkpoint_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "extraction_task",
        sa.Column("incomplete", sa.Boolean(), server_default="false", nullable=False),
    )

    for column in _ATTEMPT_COLUMNS:
        op.add_column("parse_task_attempt", column.copy())

    # 存量历史没有耗时，能推的推出来，推不出的留空（不编造）
    op.execute(
        """
        UPDATE parse_task_attempt
        SET duration_ms = GREATEST(
            0,
            ROUND(EXTRACT(EPOCH FROM (finished_at - started_at)) * 1000)
        )::int
        WHERE duration_ms IS NULL
          AND started_at IS NOT NULL
          AND finished_at IS NOT NULL
        """
    )

    op.create_table(
        "extraction_task_attempt",
        sa.Column("extraction_task_id", sa.UUID(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=24), server_default="manual", nullable=False),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["extraction_task_id"], ["extraction_task.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "extraction_task_id", "attempt_no", name="uq_extraction_attempt_no"
        ),
    )
    op.create_index(
        "ix_extraction_attempt_heartbeat",
        "extraction_task_attempt",
        ["heartbeat_at"],
        unique=False,
    )

    op.create_table(
        "extraction_batch_checkpoint",
        sa.Column("extraction_task_id", sa.UUID(), nullable=False),
        sa.Column("batch_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "block_ids",
            postgresql.ARRAY(sa.UUID()),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer(), server_default="0", nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["extraction_task_id"], ["extraction_task.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "extraction_task_id", "batch_index", name="uq_extraction_checkpoint_batch"
        ),
    )
    op.create_index(
        "ix_extraction_checkpoint_task_status",
        "extraction_batch_checkpoint",
        ["extraction_task_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_extraction_checkpoint_task_status", table_name="extraction_batch_checkpoint"
    )
    op.drop_table("extraction_batch_checkpoint")
    op.drop_index("ix_extraction_attempt_heartbeat", table_name="extraction_task_attempt")
    op.drop_table("extraction_task_attempt")

    for column in _ATTEMPT_COLUMNS:
        op.drop_column("parse_task_attempt", column.name)

    op.drop_column("extraction_task", "incomplete")
    op.drop_column("extraction_task", "checkpoint_fingerprint")

    for table in ("parse_task", "extraction_task"):
        for column in _BUDGET_COLUMNS:
            op.drop_column(table, column.name)
