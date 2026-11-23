"""本体：实体类与关系类型的定义。

本体是抽取的约束来源，只存 PostgreSQL，再单向投影到 Neo4j 的 ``:Class`` 节点。
``(workspace_id, code)`` 唯一，``code`` 是跨版本稳定标识；``version`` 单调递增，
抽取任务记录触发时的本体版本，便于事后解释"当时按什么约束抽取"。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import OntologyStatus
from app.infra.db.base import Base, UUIDPrimaryKeyMixin


class OntologyClass(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ontology_class"
    __table_args__ = (
        UniqueConstraint("workspace_id", "code", name="uq_ontology_class_code"),
        CheckConstraint("status IN (0, 1)", name="ck_ontology_class_status"),
        Index("ix_ontology_class_parent", "parent_id"),
        Index("ix_ontology_class_workspace", "workspace_id", "status"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ontology_class.id", ondelete="SET NULL")
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    identity_keys: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=OntologyStatus.VALID.value,
        server_default="0",
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OntologyRelation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ontology_relation"
    __table_args__ = (
        UniqueConstraint("workspace_id", "code", name="uq_ontology_relation_code"),
        CheckConstraint("status IN (0, 1)", name="ck_ontology_relation_status"),
        Index("ix_ontology_relation_source", "workspace_id", "source_class_id"),
        Index("ix_ontology_relation_target", "workspace_id", "target_class_id"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_class_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ontology_class.id", ondelete="RESTRICT")
    )
    target_class_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ontology_class.id", ondelete="RESTRICT")
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=OntologyStatus.VALID.value,
        server_default="0",
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
