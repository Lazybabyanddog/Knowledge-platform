"""department：部门树（邻接表）。层级与成环校验在服务层完成。"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Department(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "department"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("department.id", ondelete="RESTRICT"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    __table_args__ = (
        # 同一父部门下不重名；parent_id 为 NULL 时依赖下方的部分唯一索引
        UniqueConstraint(
            "workspace_id", "parent_id", "name", name="uq_department_workspace_parent_name"
        ),
        Index(
            "uq_department_workspace_root_name",
            "workspace_id",
            "name",
            unique=True,
            postgresql_where=text("parent_id IS NULL"),
        ),
    )
