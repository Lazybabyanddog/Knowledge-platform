"""ontology status smallint

Revision ID: a7c4e2d91f60
Revises: b3f5c9d1a2e4
Create Date: 2026-11-22 10:40:00.000000

本体状态的取值从 ``active``/``disabled`` 字符串收敛为 ``0`` 有效 / ``1`` 失效：
- 先摘掉旧的 ``'active'`` 默认值，否则类型转换会尝试把默认值转成 smallint 而失败；
- ``USING 0`` 一步完成类型转换与存量置 0（存量定义一律视为有效）；
- 再补回默认值 ``0`` 与 ``CHECK (status IN (0, 1))``。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c4e2d91f60"
down_revision: str | Sequence[str] | None = "b3f5c9d1a2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("ontology_class", "ontology_relation")

_DOWNGRADE_STATUS = "CASE WHEN status = 1 THEN 'disabled' ELSE 'active' END"


def upgrade() -> None:
    for table in _TABLES:
        op.alter_column(
            table,
            "status",
            server_default=None,
            existing_type=sa.String(length=16),
            existing_nullable=False,
        )
        op.alter_column(
            table,
            "status",
            type_=sa.SmallInteger(),
            existing_type=sa.String(length=16),
            nullable=False,
            postgresql_using="0",
        )
        op.alter_column(table, "status", server_default="0")
        op.create_check_constraint(f"ck_{table}_status", table, "status IN (0, 1)")


def downgrade() -> None:
    for table in _TABLES:
        op.drop_constraint(f"ck_{table}_status", table, type_="check")
        op.alter_column(table, "status", server_default=None, existing_type=sa.SmallInteger())
        op.alter_column(
            table,
            "status",
            type_=sa.String(length=16),
            existing_type=sa.SmallInteger(),
            nullable=False,
            postgresql_using=_DOWNGRADE_STATUS,
        )
        op.alter_column(table, "status", server_default="active")
