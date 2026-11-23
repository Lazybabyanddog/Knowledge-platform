"""存量重抽（backfill）集成测试：范围选择、预演、幂等与人工结论回贴。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import settings
from app.infra.db.models import AppUser, ExtractionTask, ParseBlock, ParseStrategy, ParseTask
from app.infra.db.session import SessionLocal
from app.infra.graph.driver import graph_session
from app.infra.graph.entity_repository import EntityRepository
from app.pipelines.extract.extractor import RuleBasedExtractor, set_extractor
from app.services.backfill_service import BackfillService
from app.services.extraction_service import ExtractionService, set_dispatcher

BACKFILL = "/api/v1/extraction-tasks/backfill"
TASKS = "/api/v1/extraction-tasks"
ENTITIES = "/api/v1/entities"
RELATIONS = "/api/v1/relations"
STRATEGIES = "/api/v1/parse-strategies"
CLASSES = "/api/v1/ontology/classes"

BLOCKS = [("paragraph", "张伟，星海科技，任职")]


def _sync_execute(task_id: str) -> None:
    session = SessionLocal()
    try:
        ExtractionService(session).execute(uuid.UUID(task_id))
    finally:
        session.close()


@pytest.fixture(autouse=True)
def deterministic_extractor() -> Iterator[None]:
    set_extractor(RuleBasedExtractor())
    yield
    set_extractor(None)


@pytest.fixture
def sync_extract_dispatch() -> Iterator[None]:
    set_dispatcher(_sync_execute)
    yield
    set_dispatcher(None)


@pytest.fixture
def strategy_id(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> Iterator[str]:
    resp = client.post(
        STRATEGIES,
        headers=auth_headers,
        json={
            "name": f"重抽测试策略{uuid.uuid4().hex[:6]}",
            "doc_type": "text",
            "engine": "plain_text",
            "config": {},
            "is_default": False,
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    yield data["id"]

    db.rollback()
    db.execute(delete(ParseTask).where(ParseTask.strategy_id == uuid.UUID(data["id"])))
    db.execute(
        delete(ParseStrategy).where(ParseStrategy.strategy_key == data["strategy_key"])
    )
    db.commit()


@pytest.fixture
def ontology_terms(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> Iterator[dict[str, str]]:
    suffix = uuid.uuid4().hex[:6]
    codes = {"person": f"person_{suffix}", "org": f"org_{suffix}", "works_at": f"works_{suffix}"}
    person = client.post(
        CLASSES,
        headers=auth_headers,
        json={"code": codes["person"], "name": "张伟", "status": 0},
    )
    assert person.status_code == 201, person.text
    org = client.post(
        CLASSES,
        headers=auth_headers,
        json={
            "code": codes["org"],
            "name": "星海科技",
            "status": 0,
            "attributes": {
                "city": {
                    "code": "city",
                    "name": "注册地",
                    "type": "string",
                    "description": "注册地城市",
                }
            },
        },
    )
    assert org.status_code == 201, org.text
    relation = client.post(
        "/api/v1/ontology/relations",
        headers=auth_headers,
        json={
            "code": codes["works_at"],
            "name": "任职",
            "source_class_id": person.json()["data"]["id"],
            "target_class_id": org.json()["data"]["id"],
            "status": 0,
        },
    )
    assert relation.status_code == 201, relation.text

    yield codes

    from app.infra.db.models import OntologyClass, OntologyRelation

    db.execute(delete(OntologyRelation).where(OntologyRelation.code == codes["works_at"]))
    db.execute(
        delete(OntologyClass).where(OntologyClass.code.in_([codes["person"], codes["org"]]))
    )
    db.commit()
    with graph_session() as graph:
        graph.run(
            "MATCH (c:Class) WHERE c.code IN $codes DETACH DELETE c",
            codes=[codes["person"], codes["org"]],
        ).consume()


@pytest.fixture
def graph_cleanup(graph_db: None) -> Iterator[list[str]]:
    task_ids: list[str] = []
    yield task_ids
    if not task_ids:
        return
    with graph_session() as graph:
        graph.run(
            "MATCH (e:Entity) WHERE e.extraction_task_id IN $task_ids DETACH DELETE e",
            task_ids=task_ids,
        ).consume()


def _upload(
    client: TestClient, headers: dict[str, str], cleanup: list[uuid.UUID]
) -> dict[str, Any]:
    resp = client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("重抽样例.txt", "张伟，星海科技，任职".encode(), "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    cleanup.append(uuid.UUID(data["id"]))
    return data


def _seed_parse_task(
    db: Session, document: dict[str, Any], strategy_id: str
) -> ParseTask:
    admin = (
        db.query(AppUser).filter(AppUser.username == settings.seed_admin_username).one()
    )
    task = ParseTask(
        workspace_id=admin.workspace_id,
        document_id=uuid.UUID(document["id"]),
        document_version_id=uuid.UUID(document["current_version_id"]),
        strategy_id=uuid.UUID(strategy_id),
        status="succeeded",
        attempt=1,
        stats={},
    )
    db.add(task)
    db.flush()
    for seq, (block_type, content) in enumerate(BLOCKS):
        db.add(
            ParseBlock(
                parse_task_id=task.id,
                document_version_id=task.document_version_id,
                seq=seq,
                block_type=block_type,
                content=content,
                page_no=1,
            )
        )
    db.commit()
    return task


def _extract(
    client: TestClient, auth_headers: dict[str, str], parse_task_id: uuid.UUID
) -> str:
    resp = client.post(TASKS, headers=auth_headers, json={"parse_task_id": str(parse_task_id)})
    assert resp.status_code == 202, resp.text
    return resp.json()["data"]["id"]


def _document_entities(
    client: TestClient, auth_headers: dict[str, str], document_id: str
) -> list[dict[str, Any]]:
    resp = client.get(
        f"/api/v1/graph/documents/{document_id}/entities", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["items"]


def test_backfill_dry_run_reports_without_touching_graph(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    ontology_terms: dict[str, str],
    sync_extract_dispatch: None,
    graph_cleanup: list[str],
) -> None:
    document = _upload(client, auth_headers, cleanup_document)
    parse_task = _seed_parse_task(db, document, strategy_id)
    task_id = _extract(client, auth_headers, parse_task.id)
    graph_cleanup.append(task_id)

    before = _document_entities(client, auth_headers, document["id"])
    assert before, "前置抽取应产出实体"

    resp = client.post(
        BACKFILL,
        headers=auth_headers,
        json={"document_ids": [document["id"]], "dry_run": True},
    )
    assert resp.status_code == 200, resp.text
    batch = resp.json()["data"]
    assert batch["dry_run"] is True
    assert batch["report"]["documents_total"] == 1
    assert batch["report"]["documents_processed"] == 0
    assert batch["report"]["entities"] == len(before)
    assert batch["report"]["preview"][0]["document_id"] == document["id"]

    # 预演不改图、不建抽取任务
    assert {item["id"] for item in _document_entities(client, auth_headers, document["id"])} == {
        item["id"] for item in before
    }
    tasks = client.get(
        TASKS, headers=auth_headers, params={"parse_task_id": str(parse_task.id)}
    ).json()["data"]
    assert tasks["total"] == 1

    fetched = client.get(f"{BACKFILL}/{batch['id']}", headers=auth_headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["data"]["report"]["preview"][0]["document_id"] == document["id"]


def test_backfill_reuses_review_conclusion_and_is_idempotent(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    ontology_terms: dict[str, str],
    sync_extract_dispatch: None,
    graph_cleanup: list[str],
) -> None:
    document = _upload(client, auth_headers, cleanup_document)
    parse_task = _seed_parse_task(db, document, strategy_id)
    first_task = _extract(client, auth_headers, parse_task.id)
    graph_cleanup.append(first_task)

    person = next(
        item
        for item in _document_entities(client, auth_headers, document["id"])
        if item["class_code"] == ontology_terms["person"]
    )
    confirmed = client.post(f"{ENTITIES}/{person['id']}/confirm", headers=auth_headers, json={})
    assert confirmed.status_code == 200, confirmed.text

    body = {"document_ids": [document["id"]]}
    first = client.post(BACKFILL, headers=auth_headers, json=body)
    assert first.status_code == 200, first.text
    report = first.json()["data"]["report"]
    assert report["documents_processed"] == 1
    assert report["documents_failed"] == 0
    assert report["reused_confirmed"] >= 1

    # 结论按名称身份贴回：人工确认的实体重抽后仍是 confirmed
    rows = _document_entities(client, auth_headers, document["id"])
    mine = [item for item in rows if item["class_code"] == ontology_terms["person"]]
    assert len(mine) == 1
    assert mine[0]["status"] == "confirmed"
    assert mine[0]["identity_key"].startswith("name:")

    # 再跑一次：同一本体版本下已重抽过，直接跳过
    second = client.post(BACKFILL, headers=auth_headers, json=body)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["report"]["documents_processed"] == 0
    assert any(
        item["reason"] == "already_backfilled"
        for item in second.json()["data"]["report"]["skipped"]
    )

    # force 覆盖跳过
    forced = client.post(BACKFILL, headers=auth_headers, json={**body, "force": True})
    assert forced.status_code == 200, forced.text
    assert forced.json()["data"]["report"]["documents_processed"] == 1

    detail = client.get(f"{TASKS}/{first_task}", headers=auth_headers).json()["data"]
    assert detail["status"] == "succeeded"

    batches = client.get(BACKFILL, headers=auth_headers, params={"limit": 5}).json()["data"]
    assert len(batches) >= 3
    assert batches[0]["id"] == forced.json()["data"]["id"]

    # 抽取任务确实按重抽次数增加；最后一次带批次标记
    db.expire_all()
    tasks = (
        db.query(ExtractionTask)
        .filter(ExtractionTask.document_id == uuid.UUID(document["id"]))
        .order_by(ExtractionTask.created_at.desc())
        .all()
    )
    assert len(tasks) >= 3
    assert tasks[0].config.get("backfill_batch_id")


def test_detach_document_keeps_entity_shared_with_other_document(graph_db: None) -> None:
    """跨文档对齐的节点：剔除一份文档只摘归属，另一份文档的知识必须留下。"""
    repo = EntityRepository()
    workspace_id = f"ws-{uuid.uuid4().hex[:8]}"
    entity_id = f"e-{uuid.uuid4().hex[:8]}"
    document_a = f"doc-a-{uuid.uuid4().hex[:6]}"
    document_b = f"doc-b-{uuid.uuid4().hex[:6]}"
    repo.upsert_entities(
        [
            {
                "id": entity_id,
                "workspace_id": workspace_id,
                "class_code": "person",
                "name": "张伟",
                "normalized_name": "张伟",
                "aliases": [],
                "description": None,
                "confidence": 0.9,
                "extraction_task_id": "task-shared",
                "document_id": document_a,
                "document_ids": [document_a, document_b],
                "evidence_block_ids": ["b1"],
                "identity_key": "name:张伟",
                "identity_kind": "name",
                "attributes_json": "{}",
                "created_at_ms": 1,
                "updated_at_ms": 1,
            }
        ]
    )
    try:
        matched, removed = repo.detach_document(workspace_id, document_a)
        assert (matched, removed) == (1, 0)
        entity = repo.get_entity(workspace_id, entity_id)
        assert entity is not None
        assert entity["document_ids"] == [document_b]

        matched, removed = repo.detach_document(workspace_id, document_b)
        assert (matched, removed) == (1, 1)
        assert repo.get_entity(workspace_id, entity_id) is None
    finally:
        with graph_session() as graph:
            graph.run(
                "MATCH (e:Entity {workspace_id: $ws}) DETACH DELETE e",
                ws=workspace_id,
            ).consume()


def test_backfill_report_lists_failed_documents(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    ontology_terms: dict[str, str],
    sync_extract_dispatch: None,
    graph_cleanup: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单篇重抽失败不拖垮整批，报告里必须能定位是哪篇、为什么。"""
    document = _upload(client, auth_headers, cleanup_document)
    parse_task = _seed_parse_task(db, document, strategy_id)
    graph_cleanup.append(_extract(client, auth_headers, parse_task.id))

    def _explode(self: BackfillService, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("模拟重抽失败")

    monkeypatch.setattr(BackfillService, "_process_document", _explode)

    resp = client.post(
        BACKFILL,
        headers=auth_headers,
        json={"document_ids": [document["id"]], "dry_run": False},
    )
    assert resp.status_code == 200, resp.text
    batch = resp.json()["data"]
    assert batch["status"] == "succeeded"
    assert batch["report"]["documents_failed"] == 1
    assert batch["report"]["documents_processed"] == 0
    failure = batch["report"]["failures"][0]
    assert failure["document_id"] == document["id"]
    assert failure["error_code"] == "RuntimeError"
    assert "模拟重抽失败" in failure["error_message"]


def test_backfill_reuses_rejected_relation_reason(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    ontology_terms: dict[str, str],
    sync_extract_dispatch: None,
    graph_cleanup: list[str],
) -> None:
    """重抽后关系节点是新写入的，人工驳回状态与理由必须按证据键贴回去。"""
    document = _upload(client, auth_headers, cleanup_document)
    parse_task = _seed_parse_task(db, document, strategy_id)
    graph_cleanup.append(_extract(client, auth_headers, parse_task.id))

    before = client.get(
        RELATIONS,
        headers=auth_headers,
        params={"document_id": document["id"], "code": ontology_terms["works_at"]},
    ).json()["data"]["items"]
    assert len(before) == 1
    rejected = client.post(
        f"{RELATIONS}/{before[0]['id']}/reject",
        headers=auth_headers,
        json={"reason": "证据不足"},
    )
    assert rejected.status_code == 200, rejected.text

    resp = client.post(
        BACKFILL,
        headers=auth_headers,
        json={"document_ids": [document["id"]]},
    )
    assert resp.status_code == 200, resp.text
    report = resp.json()["data"]["report"]
    assert report["documents_processed"] == 1
    assert report["reused_rejected"] >= 1

    after = client.get(
        RELATIONS,
        headers=auth_headers,
        params={"document_id": document["id"], "code": ontology_terms["works_at"]},
    ).json()["data"]["items"]
    assert len(after) == 1
    assert after[0]["status"] == "rejected"
    assert after[0]["reject_reason"] == "证据不足"
