"""audit log partitioned table

Revision ID: b3f7c2e91a04
Revises: 0680f326afb4
Create Date: 2026-11-21 22:30:00.000000

按月 RANGE 分区：已发生的月份数据不随新月份增长而重写，过期归档可直接 DROP 分区。
建表时预建当月及未来 3 个月的分区，之后由 audit_service.ensure_partition 兜底。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3f7c2e91a04"
down_revision: str | Sequence[str] | None = "0680f326afb4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PARTITION_AHEAD = 3


def _shift_month(when: datetime, months: int) -> datetime:
    index = when.year * 12 + (when.month - 1) + months
    return datetime(index // 12, index % 12 + 1, 1, tzinfo=UTC)


def _partition_sql(when: datetime) -> str:
    name = f"audit_log_{when:%Y%m}"
    start = _shift_month(when, 0).strftime("%Y-%m-%d %H:%M:%S+00")
    end = _shift_month(when, 1).strftime("%Y-%m-%d %H:%M:%S+00")
    return (
        f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF audit_log "
        f"FOR VALUES FROM ('{start}') TO ('{end}')"
    )


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_name", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("object_type", sa.String(length=32), nullable=True),
        sa.Column("object_id", sa.String(length=64), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", "created_at", name="pk_audit_log"),
        postgresql_partition_by="RANGE (created_at)",
    )
    op.create_index("ix_audit_log_workspace_id", "audit_log", ["workspace_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_object_id", "audit_log", ["object_id"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])

    now = datetime.now(UTC)
    for offset in range(_PARTITION_AHEAD + 1):
        op.execute(_partition_sql(_shift_month(now, offset)))


def downgrade() -> None:
    # 父表删除时 PostgreSQL 会自动删除全部分区
    op.drop_table("audit_log")
