"""候选实体/关系的审核、合并撤销、子图与来源追溯。

图库是实体与关系的唯一事实来源（PostgreSQL 只存抽取任务与解析块），
因此这里所有写操作都先改 Neo4j，再补一条审计；实体名的归一化与别名合并
复用 ``app.domain.knowledge`` 里的纯函数，保证与抽取阶段口径一致。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import Principal
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.domain import state_machines
from app.domain.enums import AuditAction, EntityStatus
from app.domain.knowledge import normalize_name
from app.infra.db.models import Document, DocumentVersion
from app.infra.db.repositories import ParseBlockRepository
from app.infra.graph.entity_repository import EntityRepository
from app.schemas.knowledge import (
    EntityItem,
    EntityListResponse,
    EntityMergeRequest,
    EntityMergeResponse,
    EntityMergeUndoRequest,
    EntityQuery,
    EntityRejectRequest,
    EntitySourceBlock,
    EntitySourceResponse,
    EntityUpdateRequest,
    RelationItem,
    RelationListResponse,
    RelationQuery,
    SubgraphEdge,
    SubgraphResponse,
)
from app.services.audit_service import AuditService

SNIPPET_LIMIT = 200


def _ms_to_dt(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromtimestamp(int(value) / 1000, UTC)


def entity_item(row: dict[str, Any]) -> EntityItem:
    return EntityItem(
        id=str(row["id"]),
        class_code=str(row.get("class_code") or ""),
        name=str(row.get("name") or ""),
        aliases=list(row.get("aliases") or []),
        description=row.get("description"),
        status=row.get("status") or EntityStatus.PROPOSED.value,
        confidence=float(row.get("confidence") or 0.0),
        merged_into=row.get("merged_into"),
        reject_reason=row.get("reject_reason"),
        extraction_task_id=row.get("extraction_task_id"),
        document_id=row.get("document_id"),
        document_ids=list(row.get("document_ids") or []),
        evidence_block_ids=list(row.get("evidence_block_ids") or []),
        attributes=dict(row.get("attributes") or {}),
        created_at=_ms_to_dt(row.get("created_at_ms")),
        updated_at=_ms_to_dt(row.get("updated_at_ms")),
    )


def relation_item(row: dict[str, Any]) -> RelationItem:
    return RelationItem(
        id=str(row["id"]),
        code=str(row.get("code") or ""),
        source_id=str(row.get("source_id") or ""),
        target_id=str(row.get("target_id") or ""),
        confidence=float(row.get("confidence") or 0.0),
        status=row.get("status") or EntityStatus.PROPOSED.value,
        evidence_block_id=row.get("evidence_block_id"),
        document_id=row.get("document_id"),
        extraction_task_id=row.get("extraction_task_id"),
        reject_reason=row.get("reject_reason"),
        created_at=_ms_to_dt(row.get("created_at_ms")),
    )


class KnowledgeService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.graph = EntityRepository()
        self.blocks = ParseBlockRepository(db)
        self.audit = AuditService(db)

    # ------------------------------------------------------------------ #
    # 实体
    # ------------------------------------------------------------------ #
    def list_entities(
        self, principal: Principal, query: EntityQuery
    ) -> EntityListResponse:
        rows, total = self.graph.list_entities(
            str(principal.user.workspace_id),
            statuses=[query.status] if query.status else None,
            class_code=query.class_code,
            keyword=query.keyword,
            min_confidence=query.min_confidence,
            document_id=query.document_id,
            skip=(query.page - 1) * query.page_size,
            limit=query.page_size,
        )
        return EntityListResponse(
            items=[entity_item(row) for row in rows],
            total=total,
            page=query.page,
            page_size=query.page_size,
        )

    def get_entity(self, principal: Principal, entity_id: str) -> EntityItem:
        return entity_item(self._entity(principal, entity_id))

    def update_entity(
        self,
        principal: Principal,
        entity_id: str,
        payload: EntityUpdateRequest,
        *,
        ip: str | None = None,
    ) -> EntityItem:
        row = self._entity(principal, entity_id)
        props = self._metadata_props(row, payload)
        if not props:
            return entity_item(row)

        updated = self.graph.update_entity(
            str(principal.user.workspace_id), entity_id, props
        )
        if updated is None:
            raise NotFoundError("实体不存在")
        self.audit.record(
            AuditAction.ENTITY_UPDATE,
            actor=principal.user,
            object_type="entity",
            object_id=entity_id,
            before={"name": row["name"], "description": row.get("description")},
            after={key: props[key] for key in ("name", "description") if key in props},
            ip=ip,
        )
        self.db.commit()
        return entity_item(updated)

    def confirm_entity(
        self,
        principal: Principal,
        entity_id: str,
        payload: EntityUpdateRequest,
        *,
        ip: str | None = None,
    ) -> EntityItem:
        row = self._entity(principal, entity_id)
        state_machines.ENTITY.ensure(row["status"], EntityStatus.CONFIRMED.value)

        props = {"status": EntityStatus.CONFIRMED.value, **self._metadata_props(row, payload)}
        updated = self.graph.update_entity(
            str(principal.user.workspace_id), entity_id, props
        )
        if updated is None:
            raise NotFoundError("实体不存在")

        self.audit.record(
            AuditAction.ENTITY_CONFIRM,
            actor=principal.user,
            object_type="entity",
            object_id=entity_id,
            before={
                "status": row["status"],
                "name": row["name"],
                "aliases": row["aliases"],
                "confidence": row["confidence"],
            },
            after={
                "status": updated["status"],
                "name": updated["name"],
                "aliases": updated["aliases"],
                "confidence": updated["confidence"],
            },
            ip=ip,
        )
        self.db.commit()
        return entity_item(updated)

    def reject_entity(
        self,
        principal: Principal,
        entity_id: str,
        payload: EntityRejectRequest,
        *,
        ip: str | None = None,
    ) -> EntityItem:
        row = self._entity(principal, entity_id)
        state_machines.ENTITY.ensure(row["status"], EntityStatus.REJECTED.value)

        updated = self.graph.update_entity(
            str(principal.user.workspace_id),
            entity_id,
            {"status": EntityStatus.REJECTED.value, "reject_reason": payload.reason},
        )
        if updated is None:
            raise NotFoundError("实体不存在")

        self.audit.record(
            AuditAction.ENTITY_REJECT,
            actor=principal.user,
            object_type="entity",
            object_id=entity_id,
            before={"status": row["status"]},
            after={"status": EntityStatus.REJECTED.value, "reason": payload.reason},
            ip=ip,
        )
        self.db.commit()
        return entity_item(updated)

    # ------------------------------------------------------------------ #
    # 合并 / 撤销
    # ------------------------------------------------------------------ #
    def merge(
        self,
        principal: Principal,
        payload: EntityMergeRequest,
        *,
        ip: str | None = None,
    ) -> EntityMergeResponse:
        target_id = payload.target_id
        source_ids = [item for item in dict.fromkeys(payload.source_ids) if item != target_id]
        if not source_ids:
            raise BadRequestError("待合并实体不能为空或与目标实体相同")

        record, reason = self.graph.merge_entities(
            str(principal.user.workspace_id), target_id, source_ids
        )
        if record is None:
            if "不存在" in reason:
                raise NotFoundError(reason)
            raise ConflictError(reason, data={"target_id": target_id})

        self.audit.record(
            AuditAction.ENTITY_MERGE,
            actor=principal.user,
            object_type="entity",
            object_id=target_id,
            before={"sources": source_ids},
            after={
                "merge_id": record["id"],
                "target_id": target_id,
                "merged_count": record["merged_count"],
            },
            ip=ip,
        )
        self.db.commit()

        merged_at = datetime.fromtimestamp(int(record["created_at_ms"]) / 1000, UTC)
        return EntityMergeResponse(
            merge_id=record["id"],
            target_id=target_id,
            source_ids=list(record["source_ids"]),
            merged_count=int(record["merged_count"]),
            undo_deadline=merged_at + timedelta(days=settings.merge_undo_window_days),
        )

    def undo_merge(
        self,
        principal: Principal,
        payload: EntityMergeUndoRequest,
        *,
        ip: str | None = None,
    ) -> dict[str, Any]:
        ok, reason = self.graph.undo_merge(
            str(principal.user.workspace_id),
            payload.target_id,
            merge_id=payload.merge_id,
            window_days=settings.merge_undo_window_days,
        )
        if not ok:
            if "没有可撤销" in reason:
                raise NotFoundError(reason, data={"target_id": payload.target_id})
            raise ConflictError(reason, data={"target_id": payload.target_id})

        self.audit.record(
            AuditAction.ENTITY_MERGE_REVERT,
            actor=principal.user,
            object_type="entity",
            object_id=payload.target_id,
            before={"merge_id": payload.merge_id},
            after={"reverted": True},
            ip=ip,
        )
        self.db.commit()
        return {"target_id": payload.target_id, "reverted": True}

    # ------------------------------------------------------------------ #
    # 关系
    # ------------------------------------------------------------------ #
    def list_relations(
        self, principal: Principal, query: RelationQuery
    ) -> RelationListResponse:
        rows, total = self.graph.list_relations(
            str(principal.user.workspace_id),
            statuses=[query.status] if query.status else None,
            code=query.code,
            min_confidence=query.min_confidence,
            document_id=query.document_id,
            extraction_task_id=query.extraction_task_id,
            skip=(query.page - 1) * query.page_size,
            limit=query.page_size,
        )
        return RelationListResponse(
            items=[relation_item(row) for row in rows],
            total=total,
            page=query.page,
            page_size=query.page_size,
        )

    def get_relation(self, principal: Principal, relation_id: str) -> RelationItem:
        return relation_item(self._relation(principal, relation_id))

    def confirm_relation(
        self,
        principal: Principal,
        relation_id: str,
        *,
        ip: str | None = None,
    ) -> RelationItem:
        return self._review_relation(
            principal,
            relation_id,
            target=EntityStatus.CONFIRMED.value,
            reason=None,
            action=AuditAction.RELATION_CONFIRM,
            ip=ip,
        )

    def reject_relation(
        self,
        principal: Principal,
        relation_id: str,
        payload: EntityRejectRequest,
        *,
        ip: str | None = None,
    ) -> RelationItem:
        return self._review_relation(
            principal,
            relation_id,
            target=EntityStatus.REJECTED.value,
            reason=payload.reason,
            action=AuditAction.RELATION_REJECT,
            ip=ip,
        )

    # ------------------------------------------------------------------ #
    # 子图与来源
    # ------------------------------------------------------------------ #
    def subgraph(
        self,
        principal: Principal,
        entity_id: str,
        *,
        depth: int,
        include_proposed: bool,
    ) -> SubgraphResponse:
        nodes, edges, truncated = self.graph.subgraph(
            str(principal.user.workspace_id),
            entity_id,
            depth=depth,
            include_proposed=include_proposed,
            max_nodes=settings.graph_subgraph_max_nodes,
        )
        if not nodes:
            raise NotFoundError("实体不存在")

        return SubgraphResponse(
            nodes=[entity_item(row) for row in nodes],
            edges=[
                SubgraphEdge(
                    id=str(edge["id"]),
                    code=str(edge.get("code") or ""),
                    source_id=str(edge["source_id"]),
                    target_id=str(edge["target_id"]),
                    confidence=float(edge.get("confidence") or 0.0),
                    status=str(edge.get("status") or ""),
                )
                for edge in edges
            ],
            depth=depth,
            truncated=truncated,
        )

    def entity_sources(
        self, principal: Principal, entity_id: str
    ) -> EntitySourceResponse:
        row = self._entity(principal, entity_id)
        block_ids: list[uuid.UUID] = []
        for raw in row.get("evidence_block_ids") or []:
            try:
                block_ids.append(uuid.UUID(str(raw)))
            except ValueError:
                continue

        rows = self.blocks.get_many(block_ids)
        version_ids = {item.document_version_id for item in rows}
        document_by_version: dict[uuid.UUID, uuid.UUID] = {}
        if version_ids:
            document_by_version = {
                version_id: document_id
                for version_id, document_id in self.db.execute(
                    select(DocumentVersion.id, DocumentVersion.document_id).where(
                        DocumentVersion.id.in_(version_ids)
                    )
                )
            }

        blocks = [
            EntitySourceBlock(
                block_id=str(item.id),
                document_id=str(document_by_version.get(item.document_version_id, "")),
                page_no=item.page_no,
                snippet=" ".join(str(item.content or "").split())[:SNIPPET_LIMIT],
            )
            for item in rows
        ]
        documents = list(row.get("document_ids") or [])
        for item in blocks:
            if item.document_id and item.document_id not in documents:
                documents.append(item.document_id)
        return EntitySourceResponse(
            entity_id=entity_id,
            document_ids=[str(item) for item in documents if item],
            blocks=blocks,
        )

    def list_document_entities(
        self,
        principal: Principal,
        document_id: str,
        *,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> EntityListResponse:
        try:
            parsed_id = uuid.UUID(document_id)
        except ValueError as exc:
            raise NotFoundError("文档不存在") from exc

        document = self.db.get(Document, parsed_id)
        if (
            document is None
            or document.deleted_at is not None
            or document.workspace_id != principal.user.workspace_id
        ):
            raise NotFoundError("文档不存在")

        rows, total = self.graph.list_document_entities(
            str(principal.user.workspace_id),
            str(document.id),
            statuses=[status] if status else None,
            skip=(page - 1) * page_size,
            limit=page_size,
        )
        return EntityListResponse(
            items=[entity_item(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _metadata_props(
        self, row: dict[str, Any], payload: EntityUpdateRequest
    ) -> dict[str, Any]:
        """改名时把旧名沉进别名，保证历史提及仍能命中该实体。"""
        props: dict[str, Any] = {}
        new_name = (payload.name or "").strip()
        if new_name and new_name != row["name"]:
            aliases = list(row.get("aliases") or [])
            if row["name"] and row["name"] not in aliases:
                aliases.append(row["name"])
            if new_name in aliases:
                aliases.remove(new_name)
            props.update(
                name=new_name,
                normalized_name=normalize_name(new_name),
                aliases=aliases,
            )
        if payload.description is not None:
            props["description"] = payload.description
        if payload.attributes is not None:
            props["attributes_json"] = json.dumps(payload.attributes, ensure_ascii=False)
        return props

    def _review_relation(
        self,
        principal: Principal,
        relation_id: str,
        *,
        target: str,
        reason: str | None,
        action: AuditAction,
        ip: str | None,
    ) -> RelationItem:
        row = self._relation(principal, relation_id)
        state_machines.RELATION.ensure(row["status"], target)

        updated = self.graph.update_relation_status(
            str(principal.user.workspace_id),
            relation_id,
            status=target,
            reject_reason=reason,
        )
        if updated is None:
            raise NotFoundError("关系不存在")

        self.audit.record(
            action,
            actor=principal.user,
            object_type="relation",
            object_id=relation_id,
            before={"status": row["status"]},
            after={"status": target, "reason": reason},
            ip=ip,
        )
        self.db.commit()
        return relation_item(updated)

    def _entity(self, principal: Principal, entity_id: str) -> dict[str, Any]:
        row = self.graph.get_entity(str(principal.user.workspace_id), entity_id)
        if row is None:
            raise NotFoundError("实体不存在")
        row["status"] = row.get("status") or EntityStatus.PROPOSED.value
        return row

    def _relation(self, principal: Principal, relation_id: str) -> dict[str, Any]:
        row = self.graph.get_relation(str(principal.user.workspace_id), relation_id)
        if row is None:
            raise NotFoundError("关系不存在")
        row["status"] = row.get("status") or EntityStatus.PROPOSED.value
        return row


__all__ = ["KnowledgeService", "entity_item", "relation_item"]
