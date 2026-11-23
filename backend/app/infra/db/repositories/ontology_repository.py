"""本体仓储：类、关系类型的读写与版本号推进。"""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.domain.enums import OntologyStatus
from app.infra.db.models import OntologyClass, OntologyRelation


class OntologyRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ---- 实体类 ----
    def get_class(self, class_id: uuid.UUID) -> OntologyClass | None:
        return self.db.get(OntologyClass, class_id)

    def class_by_code(self, workspace_id: uuid.UUID, code: str) -> OntologyClass | None:
        return self.db.scalar(
            select(OntologyClass).where(
                OntologyClass.workspace_id == workspace_id,
                OntologyClass.code == code,
            )
        )

    def list_classes(
        self, workspace_id: uuid.UUID, *, include_disabled: bool = False
    ) -> list[OntologyClass]:
        stmt = select(OntologyClass).where(OntologyClass.workspace_id == workspace_id)
        if not include_disabled:
            stmt = stmt.where(OntologyClass.status == OntologyStatus.VALID.value)
        return list(self.db.scalars(stmt.order_by(OntologyClass.code)))

    def add_class(self, row: OntologyClass) -> OntologyClass:
        self.db.add(row)
        return row

    def count_class_relations(self, class_id: uuid.UUID) -> int:
        """作为起点或终点被多少个关系类型引用（删除前必须为 0）。"""
        return int(
            self.db.scalar(
                select(func.count())
                .select_from(OntologyRelation)
                .where(
                    or_(
                        OntologyRelation.source_class_id == class_id,
                        OntologyRelation.target_class_id == class_id,
                    )
                )
            )
            or 0
        )

    def detach_children(self, class_id: uuid.UUID) -> None:
        """删除父类前把子类升为根类，与父类删除保持同一事务。"""
        self.db.execute(
            update(OntologyClass)
            .where(OntologyClass.parent_id == class_id)
            .values(parent_id=None)
        )

    def delete_class(self, row: OntologyClass) -> None:
        self.db.delete(row)

    # ---- 关系类型 ----
    def get_relation(self, relation_id: uuid.UUID) -> OntologyRelation | None:
        return self.db.get(OntologyRelation, relation_id)

    def relation_by_code(self, workspace_id: uuid.UUID, code: str) -> OntologyRelation | None:
        return self.db.scalar(
            select(OntologyRelation).where(
                OntologyRelation.workspace_id == workspace_id,
                OntologyRelation.code == code,
            )
        )

    def list_relations(
        self, workspace_id: uuid.UUID, *, include_disabled: bool = False
    ) -> list[OntologyRelation]:
        stmt = select(OntologyRelation).where(OntologyRelation.workspace_id == workspace_id)
        if not include_disabled:
            stmt = stmt.where(OntologyRelation.status == OntologyStatus.VALID.value)
        return list(self.db.scalars(stmt.order_by(OntologyRelation.code)))

    def add_relation(self, row: OntologyRelation) -> OntologyRelation:
        self.db.add(row)
        return row

    def delete_relation(self, row: OntologyRelation) -> None:
        self.db.delete(row)

    # ---- 版本 ----
    def current_version(self, workspace_id: uuid.UUID) -> int:
        """工作空间的本体版本 = 类与关系类型里出现过的最大版本号。"""
        class_max = self.db.scalar(
            select(func.max(OntologyClass.version)).where(
                OntologyClass.workspace_id == workspace_id
            )
        )
        relation_max = self.db.scalar(
            select(func.max(OntologyRelation.version)).where(
                OntologyRelation.workspace_id == workspace_id
            )
        )
        return int(max(class_max or 0, relation_max or 0))
