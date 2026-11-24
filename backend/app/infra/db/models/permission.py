"""permission / role_permission：权限目录与角色-权限关系。

权限编码由 domain/policies.py 的 Permission 枚举定义，本表是它在数据库中的投影，
seed 负责幂等补齐；运行时判定只读本表，不再读代码里的矩阵。
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


class Permission(Base):
    __tablename__ = "permission"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    group_name: Mapped[str] = mapped_column(String(32), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


class RolePermission(Base):
    __tablename__ = "role_permission"

    role_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("role.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_code: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("permission.code", ondelete="RESTRICT"),
        primary_key=True,
    )
