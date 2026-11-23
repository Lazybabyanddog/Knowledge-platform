"""本体管理：实体类与关系类型的增改查，版本单调递增并投影到图库。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.domain.enums import AuditAction
from app.infra.db.models import OntologyClass, OntologyRelation
from app.infra.db.repositories import OntologyRepository
from app.infra.graph.ontology_projection import (
    project_class_safe,
    project_workspace,
    unproject_class_safe,
)
from app.schemas.ontology import (
    OntologyClassCreateRequest,
    OntologyClassItem,
    OntologyClassListResponse,
    OntologyClassUpdateRequest,
    OntologyRelationCreateRequest,
    OntologyRelationItem,
    OntologyRelationListResponse,
    OntologyRelationUpdateRequest,
    normalize_attributes,
)
from app.services.audit_service import AuditService


def class_item(row: OntologyClass) -> OntologyClassItem:
    return OntologyClassItem(
        id=str(row.id),
        code=row.code,
        name=row.name,
        description=row.description,
        parent_id=str(row.parent_id) if row.parent_id else None,
        attributes=row.attributes or {},
        identity_keys=list(row.identity_keys or []),
        version=row.version,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def relation_item(
    row: OntologyRelation,
    source_code: str,
    target_code: str,
) -> OntologyRelationItem:
    return OntologyRelationItem(
        id=str(row.id),
        code=row.code,
        name=row.name,
        description=row.description,
        source_class_id=str(row.source_class_id),
        target_class_id=str(row.target_class_id),
        source_class_code=source_code,
        target_class_code=target_code,
        attributes=row.attributes or {},
        version=row.version,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _ensure_identity_keys(
    attributes: dict[str, Any],
    identity_keys: list[str],
    *,
    removing_attributes: bool,
) -> None:
    """标识键必须引用本类已定义的属性。

    ``removing_attributes`` 为真时说明本次更新只改了属性集合（没提交标识键），
    此时缺失的引用就是"删掉了仍被引用的属性"，错误信息要直接指向修复方式。
    """
    missing = [code for code in identity_keys if code not in attributes]
    if not missing:
        return
    if removing_attributes:
        raise BadRequestError(
            f"属性 {', '.join(missing)} 仍被标识键引用，请先移除标识键再删除该属性",
            data={"identity_keys": missing},
        )
    raise BadRequestError(
        f"标识键引用了未定义的属性：{', '.join(missing)}",
        data={"identity_keys": missing},
    )


class OntologyService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = OntologyRepository(db)
        self.audit = AuditService(db)

    # ---- 查询 ----
    def list_classes(
        self, principal: Principal, *, include_disabled: bool = False
    ) -> OntologyClassListResponse:
        rows = self.repo.list_classes(
            principal.user.workspace_id, include_disabled=include_disabled
        )
        return OntologyClassListResponse(
            items=[class_item(row) for row in rows],
            total=len(rows),
            version=self.repo.current_version(principal.user.workspace_id),
        )

    def list_relations(
        self, principal: Principal, *, include_disabled: bool = False
    ) -> OntologyRelationListResponse:
        relations = self.repo.list_relations(
            principal.user.workspace_id, include_disabled=include_disabled
        )
        codes = {
            row.id: row.code
            for row in self.repo.list_classes(
                principal.user.workspace_id, include_disabled=True
            )
        }
        return OntologyRelationListResponse(
            items=[
                relation_item(
                    row,
                    codes.get(row.source_class_id, ""),
                    codes.get(row.target_class_id, ""),
                )
                for row in relations
            ],
            total=len(relations),
            version=self.repo.current_version(principal.user.workspace_id),
        )

    # ---- 实体类 ----
    def create_class(
        self,
        principal: Principal,
        payload: OntologyClassCreateRequest,
        *,
        ip: str | None = None,
    ) -> OntologyClassItem:
        if self.repo.class_by_code(principal.user.workspace_id, payload.code):
            raise ConflictError("实体类编码已存在", data={"code": payload.code})
        parent = self._parent(principal, payload.parent_id)

        attributes = payload.attributes_payload()
        identity_keys = list(payload.identity_keys)
        _ensure_identity_keys(attributes, identity_keys, removing_attributes=False)

        row = OntologyClass(
            workspace_id=principal.user.workspace_id,
            code=payload.code,
            name=payload.name,
            description=payload.description,
            parent_id=parent.id if parent else None,
            attributes=attributes,
            identity_keys=identity_keys,
            version=self.repo.current_version(principal.user.workspace_id) + 1,
            status=payload.status,
            created_by=principal.user_id,
        )
        self.repo.add_class(row)
        self.audit.record(
            AuditAction.ONTOLOGY_CLASS_CREATE,
            actor_id=principal.user_id,
            actor_name=principal.user.username,
            workspace_id=principal.user.workspace_id,
            object_type="ontology_class",
            object_id=row.id,
            after={
                "code": row.code,
                "name": row.name,
                "version": row.version,
                "status": row.status,
            },
            ip=ip,
        )
        self.db.commit()
        project_class_safe(row, parent.code if parent else None)
        return class_item(row)

    def update_class(
        self,
        principal: Principal,
        class_id: uuid.UUID,
        payload: OntologyClassUpdateRequest,
        *,
        ip: str | None = None,
    ) -> OntologyClassItem:
        row = self._class(principal, class_id)
        before = {"name": row.name, "version": row.version, "status": row.status}

        attributes = payload.attributes_payload()
        identity_keys = (
            list(payload.identity_keys)
            if payload.identity_keys is not None
            else list(row.identity_keys or [])
        )
        _ensure_identity_keys(
            attributes
            if attributes is not None
            else normalize_attributes(row.attributes, lenient=True),
            identity_keys,
            removing_attributes=attributes is not None and payload.identity_keys is None,
        )

        if payload.status is not None:
            row.status = payload.status
        if payload.name is not None:
            row.name = payload.name
        if payload.description is not None:
            row.description = payload.description
        if attributes is not None:
            row.attributes = attributes
        if payload.identity_keys is not None:
            row.identity_keys = identity_keys
        if "parent_id" in payload.model_fields_set:
            parent = self._parent(principal, payload.parent_id)
            if parent is not None:
                self._ensure_no_cycle(principal, row, parent)
            row.parent_id = parent.id if parent else None

        row.version = self.repo.current_version(principal.user.workspace_id) + 1
        self.audit.record(
            AuditAction.ONTOLOGY_CLASS_UPDATE,
            actor_id=principal.user_id,
            actor_name=principal.user.username,
            workspace_id=principal.user.workspace_id,
            object_type="ontology_class",
            object_id=row.id,
            before=before,
            after={"name": row.name, "version": row.version, "status": row.status},
            ip=ip,
        )
        self.db.commit()

        parent_code = None
        if row.parent_id:
            parent_row = self.repo.get_class(row.parent_id)
            parent_code = parent_row.code if parent_row else None
        project_class_safe(row, parent_code)
        return class_item(row)

    def delete_class(
        self,
        principal: Principal,
        class_id: uuid.UUID,
        *,
        ip: str | None = None,
    ) -> None:
        """物理删除实体类：被关系类型引用时拒绝，子类升为根类，图库节点同步摘除。"""
        row = self._class(principal, class_id)
        references = self.repo.count_class_relations(row.id)
        if references:
            raise ConflictError(
                f"该实体类被 {references} 个关系类型引用，请先删除这些关系类型",
                data={"relations": references},
            )

        snapshot = {
            "code": row.code,
            "name": row.name,
            "version": row.version,
            "status": row.status,
            "parent_id": str(row.parent_id) if row.parent_id else None,
        }
        workspace_id = row.workspace_id
        code = row.code

        self.repo.detach_children(row.id)
        self.audit.record(
            AuditAction.ONTOLOGY_CLASS_DELETE,
            actor_id=principal.user_id,
            actor_name=principal.user.username,
            workspace_id=workspace_id,
            object_type="ontology_class",
            object_id=row.id,
            before=snapshot,
            ip=ip,
        )
        self.repo.delete_class(row)
        self.db.commit()
        unproject_class_safe(workspace_id, code)

    # ---- 关系类型 ----
    def create_relation(
        self,
        principal: Principal,
        payload: OntologyRelationCreateRequest,
        *,
        ip: str | None = None,
    ) -> OntologyRelationItem:
        if self.repo.relation_by_code(principal.user.workspace_id, payload.code):
            raise ConflictError("关系类型编码已存在", data={"code": payload.code})
        source = self._class(principal, payload.source_class_id)
        target = self._class(principal, payload.target_class_id)

        row = OntologyRelation(
            workspace_id=principal.user.workspace_id,
            code=payload.code,
            name=payload.name,
            description=payload.description,
            source_class_id=source.id,
            target_class_id=target.id,
            attributes=payload.attributes_payload(),
            version=self.repo.current_version(principal.user.workspace_id) + 1,
            status=payload.status,
            created_by=principal.user_id,
        )
        self.repo.add_relation(row)
        self.audit.record(
            AuditAction.ONTOLOGY_RELATION_CREATE,
            actor_id=principal.user_id,
            actor_name=principal.user.username,
            workspace_id=principal.user.workspace_id,
            object_type="ontology_relation",
            object_id=row.id,
            after={
                "code": row.code,
                "source": source.code,
                "target": target.code,
                "version": row.version,
                "status": row.status,
            },
            ip=ip,
        )
        self.db.commit()
        return relation_item(row, source.code, target.code)

    def update_relation(
        self,
        principal: Principal,
        relation_id: uuid.UUID,
        payload: OntologyRelationUpdateRequest,
        *,
        ip: str | None = None,
    ) -> OntologyRelationItem:
        row = self._relation(principal, relation_id)
        before = {"name": row.name, "version": row.version, "status": row.status}

        if payload.status is not None:
            row.status = payload.status
        if payload.name is not None:
            row.name = payload.name
        if payload.description is not None:
            row.description = payload.description
        if payload.attributes is not None:
            row.attributes = payload.attributes_payload()
        if payload.source_class_id is not None:
            row.source_class_id = self._class(principal, payload.source_class_id).id
        if payload.target_class_id is not None:
            row.target_class_id = self._class(principal, payload.target_class_id).id

        row.version = self.repo.current_version(principal.user.workspace_id) + 1
        source = self.repo.get_class(row.source_class_id)
        target = self.repo.get_class(row.target_class_id)
        self.audit.record(
            AuditAction.ONTOLOGY_RELATION_UPDATE,
            actor_id=principal.user_id,
            actor_name=principal.user.username,
            workspace_id=principal.user.workspace_id,
            object_type="ontology_relation",
            object_id=row.id,
            before=before,
            after={"name": row.name, "version": row.version, "status": row.status},
            ip=ip,
        )
        self.db.commit()
        return relation_item(
            row,
            source.code if source else "",
            target.code if target else "",
        )

    def delete_relation(
        self,
        principal: Principal,
        relation_id: uuid.UUID,
        *,
        ip: str | None = None,
    ) -> None:
        row = self._relation(principal, relation_id)
        source = self.repo.get_class(row.source_class_id) if row.source_class_id else None
        target = self.repo.get_class(row.target_class_id) if row.target_class_id else None
        self.audit.record(
            AuditAction.ONTOLOGY_RELATION_DELETE,
            actor_id=principal.user_id,
            actor_name=principal.user.username,
            workspace_id=row.workspace_id,
            object_type="ontology_relation",
            object_id=row.id,
            before={
                "code": row.code,
                "name": row.name,
                "source": source.code if source else None,
                "target": target.code if target else None,
                "version": row.version,
                "status": row.status,
            },
            ip=ip,
        )
        self.repo.delete_relation(row)
        self.db.commit()

    def resync(self, principal: Principal) -> int:
        """整体重放本体到图库，用于图库恢复后补齐 :Class 节点。"""
        rows = self.repo.list_classes(principal.user.workspace_id, include_disabled=True)
        return project_workspace(rows)

    # ---- 内部 ----
    def _class(self, principal: Principal, class_id: uuid.UUID) -> OntologyClass:
        row = self.repo.get_class(class_id)
        if row is None or row.workspace_id != principal.user.workspace_id:
            raise NotFoundError("实体类不存在")
        return row

    def _relation(self, principal: Principal, relation_id: uuid.UUID) -> OntologyRelation:
        row = self.repo.get_relation(relation_id)
        if row is None or row.workspace_id != principal.user.workspace_id:
            raise NotFoundError("关系类型不存在")
        return row

    def _parent(self, principal: Principal, parent_id: uuid.UUID | None) -> OntologyClass | None:
        if parent_id is None:
            return None
        return self._class(principal, parent_id)

    def _ensure_no_cycle(
        self, principal: Principal, row: OntologyClass, parent: OntologyClass
    ) -> None:
        """沿父链上溯，遇到自己说明把子孙挂成了自己的父节点。"""
        seen: set[uuid.UUID] = set()
        current: OntologyClass | None = parent
        while current is not None:
            if current.id == row.id:
                raise BadRequestError(
                    "父类不能是自身或自身的子孙", data={"parent_id": str(parent.id)}
                )
            if current.id in seen or current.parent_id is None:
                break
            seen.add(current.id)
            current = self.repo.get_class(current.parent_id)


def ontology_schema_snapshot(
    db: Session, workspace_id: uuid.UUID
) -> tuple[dict[str, OntologyClass], dict[str, OntologyRelation]]:
    """抽取时冻结的本体快照：code -> 定义。"""
    repo = OntologyRepository(db)
    classes = {row.code: row for row in repo.list_classes(workspace_id)}
    relations = {
        row.code: row
        for row in repo.list_relations(workspace_id)
        if row.source_class_id and row.target_class_id
    }
    return classes, relations
