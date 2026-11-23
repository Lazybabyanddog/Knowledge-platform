"""task next source

Revision ID: d5b2f8e13c41
Revises: c4a1e7d92b30
Create Date: 2026-11-22 17:05:00.000000

补一个 ``next_source``：任务被排进 ``PENDING`` 后，worker 需要知道这次执行
是自动重试、降参、人工复活还是跳过批次触发的，才能写对执行历史里的 ``source``。
存在任务行上而不是随消息传递，是因为消息可能重投或丢失，而任务状态不会。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d5b2f8e13c41"
down_revision: str | Sequence[str] | None = "c4a1e7d92b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("parse_task", "extraction_task")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("next_source", sa.String(length=24), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "next_source")
