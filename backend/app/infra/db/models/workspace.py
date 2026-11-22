"""workspace：知识空间。单租户部署也保留此层，为多空间留路。"""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import WorkspaceStatus
from app.infra.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Workspace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=WorkspaceStatus.ACTIVE.value, server_default="active"
    )
