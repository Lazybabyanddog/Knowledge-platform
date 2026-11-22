"""知识抽取集成测试（任务 5.7）。

补丁策略：
- 抽取器固定为 ``RuleBasedExtractor``，不依赖是否有 LLM 密钥；
- 投递默认替换为同进程同步执行，覆盖真实 worker 代码路径；
- 解析产物直接落库（parse_task + parse_block），避免把解析引擎拖进本组用例。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import settings
from app.infra.db.models import AppUser, ParseBlock, ParseStrategy, ParseTask
from app.infra.db.session import SessionLocal
from app.infra.graph.driver import graph_session
from app.pipelines.extract.extractor import RuleBasedExtractor, set_extractor
from app.services.extraction_service import ExtractionService, set_dispatcher

TASKS = "/api/v1/extraction-tasks"
ENTITIES = "/api/v1/entities"
GRAPH = "/api/v1/graph"
STRATEGIES = "/api/v1/parse-strategies"

BLOCKS = [
    ("paragraph", "张伟，星海科技，任职"),
    ("paragraph", "张伟，星海科技，任职"),
    ("footer", "版权所有"),
]


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
def manual_extract_dispatch() -> Iterator[None]:
    """只入队不执行，用来观察 pending / 去重 / 取消。"""
    set_dispatcher(lambda _task_id: None)
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
            "name": f"抽取测试策略{uuid.uuid4().hex[:6]}",
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
    """建一套最小本体：人员/组织 + 任职关系，用例结束按 code 清理 PG 与图库。"""
    suffix = uuid.uuid4().hex[:6]
    codes = {"person": f"person_{suffix}", "org": f"org_{suffix}", "works_at": f"works_{suffix}"}

    person = client.post(
        "/api/v1/ontology/classes",
        headers=auth_headers,
        json={"code": codes["person"], "name": "张伟"},
    )
    assert person.status_code == 201, person.text
    org = client.post(
        "/api/v1/ontology/classes",
        headers=auth_headers,
        json={"code": codes["org"], "name": "星海科技"},
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
        },
    )
    assert relation.status_code == 201, relation.text

    yield {
        "person": codes["person"],
        "org": codes["org"],
        "works_at": codes["works_at"],
        "person_id": person.json()["data"]["id"],
        "org_id": org.json()["data"]["id"],
    }

    from app.infra.db.models import OntologyClass, OntologyRelation

    db.execute(
        delete(OntologyRelation).where(OntologyRelation.code == codes["works_at"])
    )
    db.execute(
        delete(OntologyClass).where(
            OntologyClass.code.in_([codes["person"], codes["org"]])
        )
    )
    db.commit()
    with graph_session() as graph:
        graph.run(
            "MATCH (c:Class) WHERE c.code IN $codes DETACH DELETE c",
            codes=[codes["person"], codes["org"]],
        ).consume()


@pytest.fixture
def extraction_graph_cleanup(graph_db: None) -> Iterator[list[str]]:
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
        files={"file": ("抽取样例.txt", "张伟，星海科技，任职".encode(), "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    cleanup.append(uuid.UUID(data["id"]))
    return data


def _seed_parse_task(
    db: Session,
    document: dict[str, Any],
    strategy_id: str,
    *,
    status: str = "succeeded",
) -> ParseTask:
    admin = (
        db.query(AppUser)
        .filter(AppUser.username == settings.seed_admin_username)
        .one()
    )
    task = ParseTask(
        workspace_id=admin.workspace_id,
        document_id=uuid.UUID(document["id"]),
        document_version_id=uuid.UUID(document["current_version_id"]),
        strategy_id=uuid.UUID(strategy_id),
        status=status,
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


def test_extraction_end_to_end_produces_candidates(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    ontology_terms: dict[str, str],
    sync_extract_dispatch: None,
    extraction_graph_cleanup: list[str],
) -> None:
    document = _upload(client, auth_headers, cleanup_document)
    parse_task = _seed_parse_task(db, document, strategy_id)

    resp = client.post(
        TASKS, headers=auth_headers, json={"parse_task_id": str(parse_task.id)}
    )
    assert resp.status_code == 202, resp.text
    task_id = resp.json()["data"]["id"]
    extraction_graph_cleanup.append(task_id)

    detail = client.get(f"{TASKS}/{task_id}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    payload = detail.json()["data"]
    assert payload["status"] == "succeeded", payload
    assert payload["attempt"] == 1
    assert payload["stats"]["entities_persisted"] == 2
    assert payload["stats"]["relations_persisted"] == 1
    assert payload["stats"]["skipped_blocks"] >= 1

    listed = client.get(
        ENTITIES, headers=auth_headers, params={"status": "proposed"}
    ).json()["data"]
    ours = [item for item in listed["items"] if item["extraction_task_id"] == task_id]
    assert {item["name"] for item in ours} == {"张伟", "星海科技"}
    class_by_name = {
        "张伟": ontology_terms["person"],
        "星海科技": ontology_terms["org"],
    }
    for item in ours:
        assert item["class_code"] == class_by_name[item["name"]]

    relations = client.get(
        "/api/v1/relations",
        headers=auth_headers,
        params={"status": "proposed", "extraction_task_id": task_id},
    ).json()["data"]
    assert relations["total"] == 1
    assert relations["items"][0]["code"] == ontology_terms["works_at"]

    doc_entities = client.get(
        f"{GRAPH}/documents/{document['id']}/entities", headers=auth_headers
    ).json()["data"]
    assert {item["id"] for item in doc_entities["items"]} == {item["id"] for item in ours}

    tasks = client.get(
        TASKS, headers=auth_headers, params={"parse_task_id": str(parse_task.id)}
    ).json()["data"]
    assert tasks["total"] == 1


def test_trigger_requires_succeeded_parse_task(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    ontology_terms: dict[str, str],
    manual_extract_dispatch: None,
    extraction_graph_cleanup: list[str],
) -> None:
    document = _upload(client, auth_headers, cleanup_document)
    parse_task = _seed_parse_task(db, document, strategy_id, status="pending")

    resp = client.post(
        TASKS, headers=auth_headers, json={"parse_task_id": str(parse_task.id)}
    )
    assert resp.status_code == 409
    assert resp.json()["data"]["status"] == "pending"


def test_duplicate_trigger_cancel_and_permissions(
    client: TestClient,
    auth_headers: dict[str, str],
    role_headers: Callable[[list[str]], dict[str, str]],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    ontology_terms: dict[str, str],
    manual_extract_dispatch: None,
    extraction_graph_cleanup: list[str],
) -> None:
    document = _upload(client, auth_headers, cleanup_document)
    parse_task = _seed_parse_task(db, document, strategy_id)
    body = {"parse_task_id": str(parse_task.id)}

    first = client.post(TASKS, headers=auth_headers, json=body)
    assert first.status_code == 202, first.text
    task_id = first.json()["data"]["id"]
    extraction_graph_cleanup.append(task_id)
    assert first.json()["data"]["status"] == "pending"

    duplicated = client.post(TASKS, headers=auth_headers, json=body)
    assert duplicated.status_code == 409
    assert duplicated.json()["data"]["task_id"] == task_id

    viewer = role_headers(["viewer"])
    assert client.post(TASKS, headers=viewer, json=body).status_code == 403
    assert client.get(TASKS, headers=viewer).status_code == 200

    cancelled = client.post(f"{TASKS}/{task_id}/cancel", headers=auth_headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["status"] == "cancelled"

    again = client.post(TASKS, headers=auth_headers, json=body)
    assert again.status_code == 202, again.text
    extraction_graph_cleanup.append(again.json()["data"]["id"])


def test_trigger_requires_ontology_classes(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    manual_extract_dispatch: None,
    extraction_graph_cleanup: list[str],
) -> None:
    """没有可用本体时明确拒绝，避免抽出一堆无法归类的候选。"""
    admin = (
        db.query(AppUser)
        .filter(AppUser.username == settings.seed_admin_username)
        .one()
    )
    document = _upload(client, auth_headers, cleanup_document)
    parse_task = _seed_parse_task(db, document, strategy_id)

    # 临时把本空间的所有实体类置为 disabled，模拟"没有可用本体"
    from app.infra.db.models import OntologyClass

    rows = list(
        db.query(OntologyClass).filter(OntologyClass.workspace_id == admin.workspace_id)
    )
    original = {row.id: row.status for row in rows}
    for row in rows:
        row.status = "disabled"
    db.commit()
    try:
        resp = client.post(
            TASKS, headers=auth_headers, json={"parse_task_id": str(parse_task.id)}
        )
        assert resp.status_code == 400
        assert "本体" in resp.json()["message"]
    finally:
        for row in rows:
            row.status = original[row.id]
        db.commit()
