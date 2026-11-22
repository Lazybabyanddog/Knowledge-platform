"""role / user_role：内置角色与用户角色关联。"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, UUIDPrimaryKeyMixin


class Role(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "role"

    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class UserRole(Base):
    __tablename__ = "user_role"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("role.id", ondelete="CASCADE"),
        primary_key=True,
    )
