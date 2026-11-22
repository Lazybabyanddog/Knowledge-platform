"""init: 迁移基线

表结构从后续迁移开始累积，本迁移只负责建立版本链路，
用于验证 upgrade/downgrade 的双向可用性。

Revision ID: 0001
Revises:
Create Date: 2026-11-21
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """基线迁移：暂无表结构。"""


def downgrade() -> None:
    """基线迁移：无需回退。"""
