"""候选审核与图谱查询集成测试（任务 5.8–5.10）。

实体直接写进 Neo4j 造数：审核/合并/子图只依赖图库，不需要先跑一遍抽取；
涉及来源追溯的用例再补最小 PostgreSQL 数据（文档 + 解析块）。
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.knowledge import normalize_name
from app.infra.db.models import AppUser, AuditLog, ParseBlock, ParseStrategy, ParseTask
from app.infra.graph.driver import graph_session
from app.infra.graph.entity_repository import EntityRepository

ENTITIES = "/api/v1/entities"
RELATIONS = "/api/v1/relations"
GRAPH = "/api/v1/graph"
STRATEGIES = "/api/v1/parse-strategies"


def _workspace_id(db: Session) -> str:
    admin = (
        db.query(AppUser)
        .filter(AppUser.username == settings.seed_admin_username)
        .one()
    )
    return str(admin.workspace_id)


def _entity_row(
    workspace_id: str,
    entity_id: str,
    name: str,
    *,
    status: str = "proposed",
    class_code: str = "person",
    evidence: list[str] | None = None,
    documents: list[str] | None = None,
    confidence: float = 0.7,
    attributes: dict[str, Any] | None = None,
    identity_kind: str = "name",
    identity_key: str | None = None,
) -> dict[str, Any]:
    now = int(time.time() * 1000)
    return {
        "id": entity_id,
        "workspace_id": workspace_id,
        "class_code": class_code,
        "name": name,
        "normalized_name": normalize_name(name),
        "aliases": [],
        "description": None,
        "status": status,
        "confidence": confidence,
        "extraction_task_id": f"task-{uuid.uuid4().hex[:8]}",
        "document_id": (documents or [None])[0],
        "document_ids": documents or [],
        "evidence_block_ids": evidence or [],
        "attributes_json": json.dumps(attributes or {}, ensure_ascii=False),
        "identity_kind": identity_kind,
        "identity_key": identity_key or f"{identity_kind}:{normalize_name(name)}",
        "created_at_ms": now,
        "updated_at_ms": now,
    }


def _relation_row(
    workspace_id: str,
    relation_id: str,
    source_id: str,
    target_id: str,
    *,
    status: str = "confirmed",
    code: str = "works_at",
    confidence: float = 0.6,
    attributes: dict[str, Any] | None = None,
    evidence_text: str | None = None,
    evidence_key: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    now = int(time.time() * 1000)
    return {
        "id": relation_id,
        "workspace_id": workspace_id,
        "source_id": source_id,
        "target_id": target_id,
        "code": code,
        "status": status,
        "confidence": confidence,
        "evidence_block_id": None,
        "evidence_text": evidence_text,
        "evidence_key": evidence_key or f"ev_{relation_id}",
        "attributes_json": json.dumps(attributes or {}, ensure_ascii=False),
        "description": description,
        "document_id": None,
        "extraction_task_id": None,
        "created_at_ms": now,
        "updated_at_ms": now,
    }


@pytest.fixture
def seed_graph(graph_db: None) -> Iterator[Callable[..., list[str]]]:
    """登记本次写入的实体/关系，用例结束按 id 删除。"""
    repository = EntityRepository()
    entity_ids: list[str] = []
    relation_ids: list[str] = []

    def _seed(
        rows: list[dict[str, Any]] | None = None,
        relations: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        if rows:
            repository.upsert_entities(rows)
            entity_ids.extend(row["id"] for row in rows)
        if relations:
            repository.upsert_relations(relations)
            relation_ids.extend(row["id"] for row in relations)
        return [row["id"] for row in (rows or [])]

    yield _seed

    with graph_session() as graph:
        if relation_ids:
            graph.run(
                "MATCH ()-[r:REL]->() WHERE r.id IN $ids DELETE r", ids=relation_ids
            ).consume()
        if entity_ids:
            graph.run(
                "MATCH (m:MergeRecord) WHERE m.target_id IN $ids DETACH DELETE m",
                ids=entity_ids,
            ).consume()
            graph.run(
                "MATCH (e:Entity) WHERE e.id IN $ids DETACH DELETE e", ids=entity_ids
            ).consume()


def test_confirm_entity_renames_and_writes_audit(
    client: TestClient, auth_headers: dict[str, str], db: Session, seed_graph: Any
) -> None:
    workspace = _workspace_id(db)
    entity_id = f"t_{uuid.uuid4().hex}"
    seed_graph([_entity_row(workspace, entity_id, "星海科技", class_code="org")])

    resp = client.post(
        f"{ENTITIES}/{entity_id}/confirm",
        headers=auth_headers,
        json={"name": "星海科技集团", "description": "重点客户"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "confirmed"
    assert data["name"] == "星海科技集团"
    assert "星海科技" in data["aliases"]
    assert data["description"] == "重点客户"

    detail = client.get(f"{ENTITIES}/{entity_id}", headers=auth_headers)
    assert detail.json()["data"]["status"] == "confirmed"

    confirmed = client.get(
        ENTITIES, headers=auth_headers, params={"status": "confirmed", "keyword": "星海科技集团"}
    ).json()["data"]
    assert entity_id in {item["id"] for item in confirmed["items"]}

    logs = list(
        db.scalars(
            select(AuditLog).where(
                AuditLog.action == "entity.confirm", AuditLog.object_id == entity_id
            )
        )
    )
    assert len(logs) == 1
    assert logs[0].before["name"] == "星海科技"
    assert logs[0].after["name"] == "星海科技集团"

    # 状态机对同态重复操作是幂等的，不会重复写审计
    again = client.post(
        f"{ENTITIES}/{entity_id}/confirm", headers=auth_headers, json={}
    )
    assert again.status_code == 200
    assert again.json()["data"]["status"] == "confirmed"


def test_reject_entity_requires_reason(
    client: TestClient, auth_headers: dict[str, str], db: Session, seed_graph: Any
) -> None:
    workspace = _workspace_id(db)
    entity_id = f"t_{uuid.uuid4().hex}"
    seed_graph([_entity_row(workspace, entity_id, "临时实体")])

    missing = client.post(f"{ENTITIES}/{entity_id}/reject", headers=auth_headers, json={})
    assert missing.status_code == 400
    assert missing.json()["data"][0]["field"].endswith("reason")

    rejected = client.post(
        f"{ENTITIES}/{entity_id}/reject",
        headers=auth_headers,
        json={"reason": "抽取片段不是实体"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["status"] == "rejected"
    assert rejected.json()["data"]["reject_reason"] == "抽取片段不是实体"

    assert (
        client.post(
            f"{ENTITIES}/{entity_id}/confirm", headers=auth_headers, json={}
        ).status_code
        == 409
    )


def test_merge_moves_relations_and_undo_restores(
    client: TestClient, auth_headers: dict[str, str], db: Session, seed_graph: Any
) -> None:
    workspace = _workspace_id(db)
    target = f"t_{uuid.uuid4().hex}"
    source = f"t_{uuid.uuid4().hex}"
    other = f"t_{uuid.uuid4().hex}"
    relation_id = f"r_{uuid.uuid4().hex}"
    seed_graph(
        [
            _entity_row(
                workspace,
                target,
                "星海科技",
                status="confirmed",
                class_code="org",
                evidence=["b1"],
            ),
            _entity_row(workspace, source, "星海科技公司", class_code="org", evidence=["b2"]),
            _entity_row(workspace, other, "张伟", status="confirmed"),
        ],
        [_relation_row(workspace, relation_id, source, other)],
    )

    merged = client.post(
        ENTITIES + "/merge",
        headers=auth_headers,
        json={"target_id": target, "source_ids": [source]},
    )
    assert merged.status_code == 200, merged.text
    payload = merged.json()["data"]
    assert payload["merged_count"] == 1
    assert payload["source_ids"] == [source]
    assert datetime.fromisoformat(payload["undo_deadline"]) > datetime.now(UTC)

    target_item = client.get(f"{ENTITIES}/{target}", headers=auth_headers).json()["data"]
    assert "星海科技公司" in target_item["aliases"]
    assert set(target_item["evidence_block_ids"]) == {"b1", "b2"}

    source_item = client.get(f"{ENTITIES}/{source}", headers=auth_headers).json()["data"]
    assert source_item["status"] == "merged"
    assert source_item["merged_into"] == target

    moved = client.get(f"{RELATIONS}/{relation_id}", headers=auth_headers).json()["data"]
    assert moved["source_id"] == target
    assert moved["target_id"] == other

    undo = client.post(
        ENTITIES + "/merge/undo",
        headers=auth_headers,
        json={"target_id": target, "merge_id": payload["merge_id"]},
    )
    assert undo.status_code == 200, undo.text

    restored = client.get(f"{ENTITIES}/{source}", headers=auth_headers).json()["data"]
    assert restored["status"] == "proposed"
    assert restored["merged_into"] is None

    reverted_target = client.get(f"{ENTITIES}/{target}", headers=auth_headers).json()["data"]
    assert reverted_target["aliases"] == []

    restored_relation = client.get(
        f"{RELATIONS}/{relation_id}", headers=auth_headers
    ).json()["data"]
    assert restored_relation["source_id"] == source

    second_undo = client.post(
        ENTITIES + "/merge/undo", headers=auth_headers, json={"target_id": target}
    )
    assert second_undo.status_code == 404


def test_undo_after_window_returns_conflict(
    client: TestClient, auth_headers: dict[str, str], db: Session, seed_graph: Any
) -> None:
    workspace = _workspace_id(db)
    target = f"t_{uuid.uuid4().hex}"
    source = f"t_{uuid.uuid4().hex}"
    seed_graph(
        [
            _entity_row(workspace, target, "合并目标", class_code="org"),
            _entity_row(workspace, source, "合并来源", class_code="org"),
        ]
    )

    merged = client.post(
        ENTITIES + "/merge",
        headers=auth_headers,
        json={"target_id": target, "source_ids": [source]},
    )
    assert merged.status_code == 200, merged.text
    payload = merged.json()["data"]

    expired = int(time.time() * 1000) - 8 * 86_400_000
    with graph_session() as graph:
        graph.run(
            "MATCH (m:MergeRecord {id: $id}) SET m.created_at_ms = $expired",
            id=payload["merge_id"],
            expired=expired,
        ).consume()

    undo = client.post(
        ENTITIES + "/merge/undo",
        headers=auth_headers,
        json={"target_id": target, "merge_id": payload["merge_id"]},
    )
    assert undo.status_code == 409
    assert "期限" in undo.json()["message"]


def test_relation_review_state_machine(
    client: TestClient, auth_headers: dict[str, str], db: Session, seed_graph: Any
) -> None:
    workspace = _workspace_id(db)
    left = f"t_{uuid.uuid4().hex}"
    right = f"t_{uuid.uuid4().hex}"
    relation_id = f"r_{uuid.uuid4().hex}"
    seed_graph(
        [
            _entity_row(workspace, left, "甲", status="confirmed"),
            _entity_row(workspace, right, "乙", status="confirmed"),
        ],
        [_relation_row(workspace, relation_id, left, right, status="proposed")],
    )

    rejected = client.post(
        f"{RELATIONS}/{relation_id}/reject",
        headers=auth_headers,
        json={"reason": "关系方向错误"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["status"] == "rejected"

    conflict = client.post(f"{RELATIONS}/{relation_id}/confirm", headers=auth_headers)
    assert conflict.status_code == 409

    other_relation = f"r_{uuid.uuid4().hex}"
    seed_graph(relations=[_relation_row(workspace, other_relation, left, right, status="proposed")])
    confirmed = client.post(f"{RELATIONS}/{other_relation}/confirm", headers=auth_headers)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["data"]["status"] == "confirmed"


def test_subgraph_respects_depth_and_include_proposed(
    client: TestClient, auth_headers: dict[str, str], db: Session, seed_graph: Any
) -> None:
    workspace = _workspace_id(db)
    root = f"t_{uuid.uuid4().hex}"
    child = f"t_{uuid.uuid4().hex}"
    grandchild = f"t_{uuid.uuid4().hex}"
    seed_graph(
        [
            _entity_row(workspace, root, "根实体", status="confirmed"),
            _entity_row(workspace, child, "子实体", status="confirmed"),
            _entity_row(workspace, grandchild, "孙实体", status="proposed"),
        ],
        [
            _relation_row(workspace, f"r_{uuid.uuid4().hex}", root, child),
            _relation_row(workspace, f"r_{uuid.uuid4().hex}", child, grandchild),
        ],
    )

    shallow = client.get(
        f"{GRAPH}/entities/{root}/subgraph", headers=auth_headers, params={"depth": 1}
    ).json()["data"]
    assert {item["id"] for item in shallow["nodes"]} == {root, child}
    assert shallow["truncated"] is False

    deep = client.get(
        f"{GRAPH}/entities/{root}/subgraph",
        headers=auth_headers,
        params={"depth": 2, "include_proposed": True},
    ).json()["data"]
    assert {item["id"] for item in deep["nodes"]} == {root, child, grandchild}
    assert len(deep["edges"]) == 2

    without_proposed = client.get(
        f"{GRAPH}/entities/{root}/subgraph", headers=auth_headers, params={"depth": 2}
    ).json()["data"]
    assert grandchild not in {item["id"] for item in without_proposed["nodes"]}

    missing = client.get(f"{GRAPH}/entities/t_missing/subgraph", headers=auth_headers)
    assert missing.status_code == 404


def test_subgraph_truncates_at_max_nodes(
    client: TestClient, auth_headers: dict[str, str], db: Session, seed_graph: Any
) -> None:
    workspace = _workspace_id(db)
    root = f"t_{uuid.uuid4().hex}"
    children = [f"t_{uuid.uuid4().hex}" for _ in range(250)]
    neighbors = [
        _entity_row(workspace, item, f"邻居{item[-6:]}", status="confirmed")
        for item in children
    ]
    seed_graph(
        [_entity_row(workspace, root, "中心实体", status="confirmed"), *neighbors],
        [
            _relation_row(workspace, f"r_{uuid.uuid4().hex}", root, item)
            for item in children
        ],
    )

    resp = client.get(
        f"{GRAPH}/entities/{root}/subgraph", headers=auth_headers, params={"depth": 1}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert len(data["nodes"]) == settings.graph_subgraph_max_nodes
    assert data["truncated"] is True


@pytest.fixture
def parse_artifacts(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
) -> Iterator[dict[str, Any]]:
    """造一份"已解析"的最小数据：文档 + 解析任务 + 两个块。"""
    resp = client.post(
        "/api/v1/documents",
        headers=auth_headers,
        files={"file": ("来源样例.txt", "张伟，星海科技".encode(), "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    document = resp.json()["data"]
    cleanup_document.append(uuid.UUID(document["id"]))

    strategy = client.post(
        STRATEGIES,
        headers=auth_headers,
        json={
            "name": f"来源测试策略{uuid.uuid4().hex[:6]}",
            "doc_type": "text",
            "engine": "plain_text",
            "config": {},
            "is_default": False,
        },
    )
    assert strategy.status_code == 201, strategy.text
    strategy_key = strategy.json()["data"]["strategy_key"]
    strategy_id_value = strategy.json()["data"]["id"]

    task = ParseTask(
        workspace_id=uuid.UUID(_workspace_id(db)),
        document_id=uuid.UUID(document["id"]),
        document_version_id=uuid.UUID(document["current_version_id"]),
        strategy_id=uuid.UUID(strategy_id_value),
        status="succeeded",
        attempt=1,
        stats={},
    )
    db.add(task)
    db.flush()

    block_ids = [uuid.uuid4(), uuid.uuid4()]
    for seq, block_id in enumerate(block_ids):
        db.add(
            ParseBlock(
                id=block_id,
                parse_task_id=task.id,
                document_version_id=task.document_version_id,
                seq=seq,
                block_type="paragraph",
                content="张伟，星海科技，任职",
                page_no=seq + 1,
            )
        )
    db.commit()

    yield {
        "document_id": document["id"],
        "block_ids": [str(item) for item in block_ids],
        "strategy_key": strategy_key,
    }

    db.execute(
        delete(ParseTask).where(ParseTask.strategy_id == uuid.UUID(strategy_id_value))
    )
    db.execute(delete(ParseStrategy).where(ParseStrategy.strategy_key == strategy_key))
    db.commit()


def test_entity_sources_and_document_entities(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    seed_graph: Any,
    parse_artifacts: dict[str, Any],
) -> None:
    workspace = _workspace_id(db)
    entity_id = f"t_{uuid.uuid4().hex}"
    seed_graph(
        [
            _entity_row(
                workspace,
                entity_id,
                "张伟",
                status="confirmed",
                evidence=parse_artifacts["block_ids"],
                documents=[parse_artifacts["document_id"]],
            )
        ]
    )

    sources = client.get(f"{ENTITIES}/{entity_id}/sources", headers=auth_headers)
    assert sources.status_code == 200, sources.text
    data = sources.json()["data"]
    assert data["entity_id"] == entity_id
    assert data["document_ids"] == [parse_artifacts["document_id"]]
    assert [item["block_id"] for item in data["blocks"]] == parse_artifacts["block_ids"]
    assert [item["page_no"] for item in data["blocks"]] == [1, 2]
    assert data["blocks"][0]["document_id"] == parse_artifacts["document_id"]
    assert data["blocks"][0]["snippet"] == "张伟，星海科技，任职"

    listed = client.get(
        f"{GRAPH}/documents/{parse_artifacts['document_id']}/entities",
        headers=auth_headers,
        params={"status": "confirmed"},
    ).json()["data"]
    assert entity_id in {item["id"] for item in listed["items"]}

    assert (
        client.get(
            f"{GRAPH}/documents/{uuid.uuid4()}/entities", headers=auth_headers
        ).status_code
        == 404
    )


def test_review_permissions(
    client: TestClient,
    role_headers: Callable[[list[str]], dict[str, str]],
    db: Session,
    seed_graph: Any,
) -> None:
    """reviewer 只有审批权，确认/合并属于贡献者职责。"""
    workspace = _workspace_id(db)
    entity_id = f"t_{uuid.uuid4().hex}"
    seed_graph([_entity_row(workspace, entity_id, "权限实体")])

    reviewer = role_headers(["reviewer"])
    assert (
        client.post(
            f"{ENTITIES}/{entity_id}/confirm", headers=reviewer, json={}
        ).status_code
        == 403
    )

    viewer = role_headers(["viewer"])
    assert client.get(f"{ENTITIES}/{entity_id}", headers=viewer).status_code == 200
    assert (
        client.post(
            f"{ENTITIES}/{entity_id}/reject", headers=viewer, json={"reason": "x"}
        ).status_code
        == 403
    )

    contributor = role_headers(["contributor"])
    assert (
        client.post(
            f"{ENTITIES}/{entity_id}/confirm", headers=contributor, json={}
        ).status_code
        == 200
    )


def test_relation_attribute_update_writes_audit(
    client: TestClient,
    auth_headers: dict[str, str],
    role_headers: Callable[[list[str]], dict[str, str]],
    db: Session,
    seed_graph: Any,
) -> None:
    workspace = _workspace_id(db)
    source_id = f"t_{uuid.uuid4().hex}"
    target_id = f"t_{uuid.uuid4().hex}"
    relation_id = f"t_{uuid.uuid4().hex}"
    code = f"rel_{uuid.uuid4().hex[:8]}"
    seed_graph(
        [
            _entity_row(workspace, source_id, "张伟", status="confirmed"),
            _entity_row(workspace, target_id, "星海科技", class_code="org", status="confirmed"),
        ],
        [
            _relation_row(
                workspace,
                relation_id,
                source_id,
                target_id,
                status="proposed",
                code=code,
                evidence_text="张伟自 2020 年起任职于星海科技",
            )
        ],
    )

    resp = client.patch(
        f"{RELATIONS}/{relation_id}",
        headers=auth_headers,
        json={
            "attributes": {"start_date": "2020-03-01", "title": "研发负责人"},
            "description": "2020 年起任职",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["attributes"] == {"start_date": "2020-03-01", "title": "研发负责人"}
    assert data["description"] == "2020 年起任职"
    # 端点与类型不接受修改：请求里带了也不会生效
    assert data["source_id"] == source_id
    assert data["target_id"] == target_id
    assert data["code"] == code

    audit = (
        db.query(AuditLog)
        .filter(AuditLog.object_id == relation_id, AuditLog.action == "relation.update")
        .one()
    )
    assert audit.before["description"] is None
    assert audit.after["description"] == "2020 年起任职"

    viewer = role_headers(["viewer"])
    forbidden = client.patch(
        f"{RELATIONS}/{relation_id}",
        headers=viewer,
        json={"attributes": {"title": "越权修改"}},
    )
    assert forbidden.status_code == 403


def test_relation_attribute_update_rejected_conflicts(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    seed_graph: Any,
) -> None:
    workspace = _workspace_id(db)
    source_id = f"t_{uuid.uuid4().hex}"
    target_id = f"t_{uuid.uuid4().hex}"
    relation_id = f"t_{uuid.uuid4().hex}"
    seed_graph(
        [
            _entity_row(workspace, source_id, "张伟", status="confirmed"),
            _entity_row(workspace, target_id, "星海科技", class_code="org", status="confirmed"),
        ],
        [
            _relation_row(
                workspace,
                relation_id,
                source_id,
                target_id,
                status="rejected",
                code=f"rel_{uuid.uuid4().hex[:8]}",
            )
        ],
    )

    resp = client.patch(
        f"{RELATIONS}/{relation_id}",
        headers=auth_headers,
        json={"attributes": {"title": "研发负责人"}},
    )
    assert resp.status_code == 409, resp.text


def test_rename_updates_name_identity_only(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    seed_graph: Any,
) -> None:
    """名称身份要跟着新名字走；证件身份与名称无关，改名不该动它。"""
    workspace = _workspace_id(db)
    name_entity = f"t_{uuid.uuid4().hex}"
    identity_entity = f"t_{uuid.uuid4().hex}"
    seed_graph(
        [
            _entity_row(
                workspace,
                name_entity,
                "星海科技",
                identity_kind="name",
                identity_key="name:星海科技",
            ),
            _entity_row(
                workspace,
                identity_entity,
                "云图科技",
                identity_kind="identity",
                identity_key="identity:91310000XYZW",
                attributes={"uscc": "91310000XYZW"},
            ),
        ]
    )

    renamed = client.post(
        f"{ENTITIES}/{name_entity}/confirm",
        headers=auth_headers,
        json={"name": "星海科技集团"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["data"]["identity_kind"] == "name"
    assert renamed.json()["data"]["identity_key"] == "name:星海科技集团"

    kept = client.post(
        f"{ENTITIES}/{identity_entity}/confirm",
        headers=auth_headers,
        json={"name": "云图科技股份有限公司"},
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["data"]["identity_kind"] == "identity"
    assert kept.json()["data"]["identity_key"] == "identity:91310000XYZW"
