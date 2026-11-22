"""本体到 Neo4j 的单向投影。

PostgreSQL 是本体的唯一真相，图库里的 ``:Class`` 只用于图查询时按类型过滤。
投影失败不回滚业务事务：记录告警，可随时按工作空间整体重放。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.infra.db.models import OntologyClass
from app.infra.graph.driver import graph_session

logger = logging.getLogger(__name__)

_UPSERT_CLASSES = """
UNWIND $rows AS row
MERGE (c:Class {workspace_id: row.workspace_id, code: row.code})
SET c.name = row.name,
    c.parent_code = row.parent_code,
    c.status = row.status,
    c.version = row.version
"""


def _row(row: OntologyClass, parent_codes: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {
        "workspace_id": str(row.workspace_id),
        "code": row.code,
        "name": row.name,
        "parent_code": parent_codes.get(row.parent_id) if row.parent_id else None,
        "status": row.status,
        "version": row.version,
    }


def project_classes(rows: list[OntologyClass], parent_codes: dict[uuid.UUID, str]) -> int:
    if not rows:
        return 0
    payload = [_row(row, parent_codes) for row in rows]
    with graph_session() as session:
        session.run(_UPSERT_CLASSES, rows=payload).consume()
    return len(payload)


def project_class_safe(row: OntologyClass, parent_code: str | None = None) -> None:
    """单条投影，失败只告警：本体写入不应被图库可用性拖垮。"""
    try:
        project_classes([row], {row.parent_id: parent_code} if row.parent_id else {})
    except Exception:  # noqa: BLE001 - 图库故障降级为告警
        logger.warning("本体类投影失败 code=%s", row.code, exc_info=True)


def project_workspace(classes: list[OntologyClass]) -> int:
    """整体重放：先清掉该空间的 Class 节点再全量写入。"""
    if not classes:
        return 0
    workspace_id = str(classes[0].workspace_id)
    parent_codes = {row.id: row.code for row in classes}
    with graph_session() as session:
        session.run(
            "MATCH (c:Class {workspace_id: $workspace_id}) DETACH DELETE c",
            workspace_id=workspace_id,
        ).consume()
    return project_classes(classes, parent_codes)


__all__ = ["project_class_safe", "project_classes", "project_workspace"]
