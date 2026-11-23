"""存量重抽：把老规则抽出来的实体/关系按新规程重跑一遍。

为什么是"先清后抽"而不是"增量补字段"：属性与身份判定的正确性依赖抽取时的原文
与提示词，事后补只能靠猜；先清掉该文档的产出再整体重抽，才能得到与全量一致的图谱。

因此每次执行都必须能追溯：

- 只处理已成功抽取过的文档（否则等于首次抽取，不该混进运维批次）；
- 默认只挑"实体属性为空 / 身份为旧规则 / 本体版本落后"的文档，显式给文档集合时按用户指定；
- 先快照人工结论（confirmed / rejected），重抽后按身份回贴，贴不上的列进报告；
- 同一文档同一本体版本重复执行直接跳过（``force`` 才覆盖）；
- ``dry_run`` 只统计不动图，批次表照样留一条记录。

执行是同步的：CLI 需要"一批跑完再出报告"，而不是散给 worker 后无从对账。
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.exceptions import NotFoundError
from app.domain.enums import AuditAction, ExtractionTaskStatus, ParseTaskStatus
from app.infra.db.models import (
    ExtractionBackfillBatch,
    ExtractionTask,
    ParseTask,
)
from app.infra.db.repositories import (
    ExtractionBackfillRepository,
    ExtractionTaskRepository,
    OntologyRepository,
    ParseTaskRepository,
)
from app.infra.graph.entity_repository import EntityRepository
from app.schemas.backfill import BackfillBatchItem, BackfillRequest
from app.schemas.knowledge import ExtractionTaskCreateRequest
from app.services.audit_service import AuditService
from app.services.extraction_service import ExtractionService

logger = logging.getLogger(__name__)

STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"

SKIP_ALREADY_BACKFILLED = "already_backfilled"
SKIP_NO_PARSE = "no_succeeded_parse"

ENTITY_PAGE_SIZE = 500


def _now() -> datetime:
    return datetime.now(UTC)


def backfill_batch_item(batch: ExtractionBackfillBatch) -> BackfillBatchItem:
    return BackfillBatchItem(
        id=str(batch.id),
        status=batch.status,
        dry_run=bool(batch.dry_run),
        force=bool(batch.force),
        scope=dict(batch.scope or {}),
        report=dict(batch.report or {}),
        created_by=str(batch.created_by) if batch.created_by else None,
        created_at=batch.created_at,
        finished_at=batch.finished_at,
    )


class BackfillService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.batches = ExtractionBackfillRepository(db)
        self.tasks = ExtractionTaskRepository(db)
        self.parse_tasks = ParseTaskRepository(db)
        self.graph = EntityRepository()
        self.extraction = ExtractionService(db)
        self.audit = AuditService(db)

    # ------------------------------------------------------------------ #
    # 对外入口
    # ------------------------------------------------------------------ #
    def run(self, principal: Principal, payload: BackfillRequest) -> BackfillBatchItem:
        """执行一次重抽：先算范围 + 跳过原因，再逐文档清理、重抽、回贴结论。"""
        workspace_id = principal.user.workspace_id
        current_version = self._current_ontology_version(workspace_id)
        targets, skipped = self._resolve_targets(
            workspace_id, payload, current_version=current_version
        )

        batch = ExtractionBackfillBatch(
            workspace_id=workspace_id,
            status=STATUS_RUNNING,
            dry_run=payload.dry_run,
            force=payload.force,
            scope={
                "document_ids": list(payload.document_ids),
                "class_codes": list(payload.class_codes),
                "created_from": payload.created_from.isoformat()
                if payload.created_from
                else None,
                "created_to": payload.created_to.isoformat() if payload.created_to else None,
                "lagging_only": payload.lagging_only,
                "ontology_version": current_version,
                "targets": [item["document_id"] for item in targets],
            },
            report={},
            created_by=principal.user_id,
        )
        self.batches.add(batch)
        self.db.flush()
        self.audit.record(
            AuditAction.EXTRACTION_BACKFILL,
            actor=principal.user,
            workspace_id=workspace_id,
            object_type="extraction_backfill_batch",
            object_id=batch.id,
            after={
                "dry_run": payload.dry_run,
                "force": payload.force,
                "targets": len(targets),
                "ontology_version": current_version,
            },
        )
        self.db.commit()

        try:
            if payload.dry_run:
                report = self._preview(
                    workspace_id, targets, skipped, current_version=current_version
                )
            else:
                report = self._apply(principal, batch, targets, skipped)
            batch.status = STATUS_SUCCEEDED
        except Exception as exc:  # noqa: BLE001 - 批次级失败必须留痕再抛出
            self.db.rollback()
            batch.status = STATUS_FAILED
            batch.report = {
                "documents_total": len(targets),
                "error": str(exc)[:2000],
            }
            batch.finished_at = _now()
            self.db.commit()
            raise
        batch.report = report
        batch.finished_at = _now()
        self.db.commit()
        self.db.refresh(batch)
        return backfill_batch_item(batch)

    def get(self, principal: Principal, batch_id: str) -> BackfillBatchItem:
        try:
            parsed = uuid.UUID(batch_id)
        except ValueError as exc:
            raise NotFoundError("重抽批次不存在") from exc
        batch = self.batches.get(parsed)
        if batch is None or batch.workspace_id != principal.user.workspace_id:
            raise NotFoundError("重抽批次不存在")
        return backfill_batch_item(batch)

    def list_batches(self, principal: Principal, *, limit: int = 20) -> list[BackfillBatchItem]:
        rows = self.batches.list_recent(principal.user.workspace_id, limit=limit)
        return [backfill_batch_item(row) for row in rows]

    # ------------------------------------------------------------------ #
    # 范围解析
    # ------------------------------------------------------------------ #
    def _resolve_targets(
        self,
        workspace_id: uuid.UUID,
        payload: BackfillRequest,
        *,
        current_version: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        wanted = {str(item) for item in payload.document_ids if item}
        class_codes = {code for code in payload.class_codes if code}

        latest_tasks = self._latest_succeeded_tasks(workspace_id)
        targets: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []

        for task in latest_tasks:
            document_id = str(task.document_id)
            if wanted and document_id not in wanted:
                continue
            if class_codes and not class_codes & set((task.config or {}).get("class_codes") or []):
                continue
            if payload.created_from and task.created_at < payload.created_from:
                continue
            if payload.created_to and task.created_at > payload.created_to:
                continue
            if payload.lagging_only and task.ontology_version >= current_version:
                continue
            if not payload.document_ids and not payload.lagging_only:
                # 默认范围：只挑还带着旧规则产出的文档，避免把已经干净的文档全重跑
                if self.graph.count_document_legacy_entities(str(workspace_id), document_id) == 0:
                    continue
            if not payload.force and self._already_backfilled(task, current_version):
                skipped.append(
                    {
                        "document_id": document_id,
                        "reason": SKIP_ALREADY_BACKFILLED,
                        "detail": str((task.config or {}).get("backfill_batch_id")),
                    }
                )
                continue
            target = self._build_target(task)
            if target is None:
                skipped.append({"document_id": document_id, "reason": SKIP_NO_PARSE})
                continue
            targets.append(target)

        # 显式点了名但连成功抽取都没有的文档，也要在报告里说清楚
        found = {item["document_id"] for item in targets} | {
            item["document_id"] for item in skipped
        }
        for document_id in sorted(wanted - found):
            skipped.append(
                {
                    "document_id": document_id,
                    "reason": "no_succeeded_extraction",
                    "detail": "该文档没有成功的抽取任务，请直接触发抽取",
                }
            )
        if payload.limit:
            targets = targets[: payload.limit]
        return targets, skipped

    def _latest_succeeded_tasks(self, workspace_id: uuid.UUID) -> list[ExtractionTask]:
        """每份文档取最近一次成功的抽取任务：范围判定与跳过判定都以它为准。"""
        return list(
            self.db.scalars(
                select(ExtractionTask)
                .where(
                    ExtractionTask.workspace_id == workspace_id,
                    ExtractionTask.status == ExtractionTaskStatus.SUCCEEDED.value,
                )
                .distinct(ExtractionTask.document_id)
                .order_by(ExtractionTask.document_id, ExtractionTask.created_at.desc())
            )
        )

    def _build_target(self, task: ExtractionTask) -> dict[str, Any] | None:
        parse_task = self.db.scalar(
            select(ParseTask)
            .where(
                ParseTask.document_id == task.document_id,
                ParseTask.status == ParseTaskStatus.SUCCEEDED.value,
            )
            .order_by(ParseTask.created_at.desc())
            .limit(1)
        )
        if parse_task is None:
            return None
        return {
            "document_id": str(task.document_id),
            "document_version_id": str(parse_task.document_version_id),
            "parse_task_id": str(parse_task.id),
            "previous_task_id": str(task.id),
            "ontology_version": task.ontology_version,
        }

    @staticmethod
    def _already_backfilled(task: ExtractionTask, current_version: int) -> bool:
        """同一本体版本下重抽过就不再重跑：``force`` 由调用方决定是否覆盖。"""
        config = dict(task.config or {})
        return bool(config.get("backfill_batch_id")) and task.ontology_version >= current_version

    def _current_ontology_version(self, workspace_id: uuid.UUID) -> int:
        return OntologyRepository(self.db).current_version(workspace_id)

    # ------------------------------------------------------------------ #
    # 预演
    # ------------------------------------------------------------------ #
    def _preview(
        self,
        workspace_id: uuid.UUID,
        targets: list[dict[str, Any]],
        skipped: list[dict[str, str]],
        *,
        current_version: int,
    ) -> dict[str, Any]:
        """只读预演：统计"会清掉多少、会重抽几篇"，不动图也不建任务。"""
        workspace = str(workspace_id)
        report: dict[str, Any] = {
            "documents_total": len(targets),
            "documents_processed": 0,
            "documents_skipped": len(skipped),
            "documents_failed": 0,
            "extraction_tasks": 0,
            "entities": 0,
            "relations": 0,
            "reused_confirmed": 0,
            "reused_rejected": 0,
            "skipped": skipped,
            "failures": [],
            "unmatched": [],
            "preview": [],
        }
        for target in targets:
            document_id = target["document_id"]
            entity_rows = self._all_document_entities(workspace, document_id)
            relation_rows = self.graph.list_document_relations(workspace, document_id)
            report["entities"] += len(entity_rows)
            report["relations"] += len(relation_rows)
            report["preview"].append(
                {
                    "document_id": document_id,
                    "previous_task_id": target["previous_task_id"],
                    "entities": len(entity_rows),
                    "relations": len(relation_rows),
                    "ontology_version": target["ontology_version"],
                    "current_ontology_version": current_version,
                }
            )
        return report

    # ------------------------------------------------------------------ #
    # 正式执行
    # ------------------------------------------------------------------ #
    def _apply(
        self,
        principal: Principal,
        batch: ExtractionBackfillBatch,
        targets: list[dict[str, Any]],
        skipped: list[dict[str, str]],
    ) -> dict[str, Any]:
        workspace = str(principal.user.workspace_id)
        entity_snapshots, relation_snapshots = self.graph.review_snapshot(workspace)
        entities_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
        relations_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in entity_snapshots:
            for document_id in row.get("document_ids") or []:
                entities_by_doc[str(document_id)].append(row)
        for row in relation_snapshots:
            if row.get("document_id"):
                relations_by_doc[str(row["document_id"])].append(row)

        report: dict[str, Any] = {
            "documents_total": len(targets),
            "documents_processed": 0,
            "documents_skipped": len(skipped),
            "documents_failed": 0,
            "extraction_tasks": 0,
            "entities": 0,
            "relations": 0,
            "reused_confirmed": 0,
            "reused_rejected": 0,
            "skipped": skipped,
            "failures": [],
            "unmatched": [],
        }
        seen_entities: set[str] = set()
        seen_relations: set[str] = set()

        for target in targets:
            document_id = target["document_id"]
            try:
                result = self._process_document(
                    principal,
                    batch,
                    target,
                    entities_by_doc.get(document_id, []),
                    relations_by_doc.get(document_id, []),
                )
            except Exception as exc:  # noqa: BLE001 - 单文档失败不中断整批
                logger.exception("重抽文档失败 document_id=%s", document_id)
                self.db.rollback()
                report["documents_failed"] += 1
                report["failures"].append(
                    {
                        "document_id": document_id,
                        "error_code": getattr(exc, "code", type(exc).__name__),
                        "error_message": str(exc)[:500],
                    }
                )
                continue

            if result["status"] != STATUS_SUCCEEDED:
                report["documents_failed"] += 1
                report["failures"].append(
                    {
                        "document_id": document_id,
                        "error_code": result.get("error_code"),
                        "error_message": result.get("error_message"),
                    }
                )
                continue

            report["documents_processed"] += 1
            report["extraction_tasks"] += 1
            report["reused_confirmed"] += result["reused_confirmed"]
            report["reused_rejected"] += result["reused_rejected"]
            report["unmatched"].extend(result["unmatched"])
            seen_entities.update(result["entity_ids"])
            seen_relations.update(result["relation_ids"])

        report["entities"] = len(seen_entities)
        report["relations"] = len(seen_relations)
        return report

    def _process_document(
        self,
        principal: Principal,
        batch: ExtractionBackfillBatch,
        target: dict[str, Any],
        entity_snapshots: list[dict[str, Any]],
        relation_snapshots: list[dict[str, Any]],
    ) -> dict[str, Any]:
        workspace = str(principal.user.workspace_id)
        document_id = target["document_id"]

        # 顺序很重要：先清旧产出再抽，否则新旧实体按身份撞在一起，
        # 清理阶段会把刚写进去的新节点一起摘掉。
        self.graph.detach_document(workspace, document_id)
        self.graph.delete_document_relations(workspace, document_id)

        task = self.extraction.trigger(
            principal,
            ExtractionTaskCreateRequest(parse_task_id=target["parse_task_id"]),
            extra_config={"backfill_batch_id": str(batch.id)},
            dispatch=False,
        )
        self.extraction.execute(task.id)
        self.db.refresh(task)
        if task.status != ExtractionTaskStatus.SUCCEEDED.value:
            return {
                "status": task.status,
                "error_code": task.error_code,
                "error_message": task.error_message,
            }

        entities = self._all_document_entities(workspace, document_id)
        relations = self.graph.list_document_relations(workspace, document_id)
        applied_entities: set[str] = set()
        applied_relations: set[str] = set()
        reused_confirmed = 0
        reused_rejected = 0
        unmatched: list[dict[str, str]] = []

        entity_index_identity = {
            (row["class_code"], row["identity_key"]): row
            for row in entities
            if row.get("identity_key")
        }
        entity_index_name = {
            (row["class_code"], row["normalized_name"]): row
            for row in entities
            if row.get("normalized_name")
        }
        for snapshot in entity_snapshots:
            current = entity_index_identity.get(
                (snapshot["class_code"], snapshot.get("identity_key"))
            ) or entity_index_name.get(
                (snapshot["class_code"], snapshot.get("normalized_name"))
            )
            if current is None:
                unmatched.append(
                    {
                        "kind": "entity",
                        "id": str(snapshot["id"]),
                        "reason": "not_found_after_reextract",
                    }
                )
                continue
            # 多个旧节点按身份并到同一新节点时只贴一次，避免计数虚高
            if current["id"] in applied_entities:
                continue
            applied_entities.add(current["id"])
            self.graph.apply_review_status(
                workspace,
                entity_id=current["id"],
                status=snapshot["status"],
                reject_reason=snapshot.get("reject_reason"),
            )
            if snapshot["status"] == "confirmed":
                reused_confirmed += 1
            else:
                reused_rejected += 1

        relation_index_key = {
            row["evidence_key"]: row for row in relations if row.get("evidence_key")
        }
        relation_index_endpoints: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(
            list
        )
        for row in relations:
            relation_index_endpoints[
                (
                    str(row.get("code")),
                    str(row.get("source_identity_key")),
                    str(row.get("target_identity_key")),
                )
            ].append(row)
        for snapshot in relation_snapshots:
            current = relation_index_key.get(str(snapshot.get("evidence_key") or ""))
            if current is None:
                triple = (
                    str(snapshot.get("code")),
                    str(snapshot.get("source_identity_key")),
                    str(snapshot.get("target_identity_key")),
                )
                candidates = relation_index_endpoints.get(triple) or []
                # 端点相同但有多条证据实例时无法判断是哪一条，宁可不贴也不贴错
                current = candidates[0] if len(candidates) == 1 else None
            if current is None:
                unmatched.append(
                    {
                        "kind": "relation",
                        "id": str(snapshot["id"]),
                        "reason": "not_found_after_reextract",
                    }
                )
                continue
            if current["id"] in applied_relations:
                continue
            applied_relations.add(current["id"])
            self.graph.apply_relation_review_status(
                workspace,
                relation_id=current["id"],
                status=snapshot["status"],
                reject_reason=snapshot.get("reject_reason"),
            )
            if snapshot["status"] == "confirmed":
                reused_confirmed += 1
            else:
                reused_rejected += 1

        return {
            "status": STATUS_SUCCEEDED,
            "entity_ids": [row["id"] for row in entities],
            "relation_ids": [row["id"] for row in relations],
            "reused_confirmed": reused_confirmed,
            "reused_rejected": reused_rejected,
            "unmatched": unmatched,
        }

    def _all_document_entities(self, workspace: str, document_id: str) -> list[dict[str, Any]]:
        """分页读全某文档的实体：回贴要按身份建索引，只取一页会漏掉结论。"""
        rows: list[dict[str, Any]] = []
        skip = 0
        while True:
            page, total = self.graph.list_document_entities(
                workspace, document_id, skip=skip, limit=ENTITY_PAGE_SIZE
            )
            rows.extend(page)
            skip += len(page)
            if not page or skip >= total:
                break
        return rows


__all__ = ["BackfillService", "backfill_batch_item"]
