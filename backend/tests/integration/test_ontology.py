"""本体管理集成测试（任务 5.1–5.3）。"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.infra.db.models import OntologyClass, OntologyRelation
from app.infra.db.session import SessionLocal
from app.infra.graph.driver import graph_session

CLASSES = "/api/v1/ontology/classes"
RELATIONS = "/api/v1/ontology/relations"


@pytest.fixture
def ontology_cleanup(graph_db: None) -> Iterator[list[str]]:
    codes: list[str] = []
    yield codes

    session = SessionLocal()
    try:
        session.execute(delete(OntologyRelation).where(OntologyRelation.code.in_(codes)))
        session.execute(delete(OntologyClass).where(OntologyClass.code.in_(codes)))
        session.commit()
    finally:
        session.close()
    with graph_session() as graph:
        graph.run(
            "MATCH (c:Class) WHERE c.code IN $codes DETACH DELETE c", codes=codes
        ).consume()


def _code(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _create_class(
    client: TestClient,
    headers: dict[str, str],
    cleanup: list[str],
    *,
    code: str,
    name: str = "测试实体类",
    parent_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "name": name, "attributes": {"owner": "string"}}
    if parent_id:
        payload["parent_id"] = parent_id
    resp = client.post(CLASSES, headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    cleanup.append(code)
    return resp.json()["data"]


def _create_relation(
    client: TestClient,
    headers: dict[str, str],
    cleanup: list[str],
    *,
    code: str,
    source_id: str,
    target_id: str,
) -> dict[str, Any]:
    resp = client.post(
        RELATIONS,
        headers=headers,
        json={
            "code": code,
            "name": "测试关系",
            "source_class_id": source_id,
            "target_class_id": target_id,
        },
    )
    assert resp.status_code == 201, resp.text
    cleanup.append(code)
    return resp.json()["data"]


def test_create_class_projects_to_neo4j(
    client: TestClient,
    auth_headers: dict[str, str],
    ontology_cleanup: list[str],
) -> None:
    code = _code("person")
    created = _create_class(client, auth_headers, ontology_cleanup, code=code, name="人员")

    assert created["code"] == code
    assert created["version"] >= 1
    assert created["status"] == "active"

    listed = client.get(CLASSES, headers=auth_headers).json()["data"]
    assert [item["code"] for item in listed["items"]].count(code) == 1
    assert listed["version"] == max(item["version"] for item in listed["items"])

    with graph_session() as graph:
        record = graph.run(
            "MATCH (c:Class {code: $code}) RETURN c", code=code
        ).single()
    assert record is not None
    assert record["c"]["name"] == "人员"
    assert record["c"]["status"] == "active"


def test_class_code_unique_and_write_permissions(
    client: TestClient,
    auth_headers: dict[str, str],
    role_headers: Callable[[list[str]], dict[str, str]],
    ontology_cleanup: list[str],
) -> None:
    code = _code("org")
    _create_class(client, auth_headers, ontology_cleanup, code=code)

    duplicated = client.post(CLASSES, headers=auth_headers, json={"code": code, "name": "重复"})
    assert duplicated.status_code == 409
    assert duplicated.json()["data"]["code"] == code

    contributor = role_headers(["contributor"])
    assert (
        client.post(CLASSES, headers=contributor, json={"code": _code("x"), "name": "越权"})
    ).status_code == 403
    viewer = role_headers(["viewer"])
    assert client.get(CLASSES, headers=viewer).status_code == 200


def test_class_update_versions_and_rejects_cycle(
    client: TestClient,
    auth_headers: dict[str, str],
    ontology_cleanup: list[str],
) -> None:
    parent = _create_class(client, auth_headers, ontology_cleanup, code=_code("unit"), name="部门")
    child = _create_class(
        client,
        auth_headers,
        ontology_cleanup,
        code=_code("team"),
        name="小组",
        parent_id=parent["id"],
    )
    assert child["parent_id"] == parent["id"]

    cyclic = client.patch(
        f"{CLASSES}/{parent['id']}", headers=auth_headers, json={"parent_id": child["id"]}
    )
    assert cyclic.status_code == 400
    assert "父类" in cyclic.json()["message"]

    renamed = client.patch(
        f"{CLASSES}/{parent['id']}", headers=auth_headers, json={"name": "一级部门"}
    )
    assert renamed.status_code == 200
    updated = renamed.json()["data"]
    assert updated["name"] == "一级部门"
    assert updated["version"] > parent["version"]
    assert updated["parent_id"] is None

    disabled = client.patch(
        f"{CLASSES}/{child['id']}", headers=auth_headers, json={"status": "disabled"}
    )
    assert disabled.status_code == 200
    assert disabled.json()["data"]["status"] == "disabled"

    active = client.get(CLASSES, headers=auth_headers).json()["data"]
    assert child["code"] not in {item["code"] for item in active["items"]}
    disabled_list = client.get(
        CLASSES, headers=auth_headers, params={"include_disabled": True}
    ).json()["data"]
    all_codes = {item["code"] for item in disabled_list["items"]}
    assert child["code"] in all_codes

    bad_status = client.patch(
        f"{CLASSES}/{parent['id']}", headers=auth_headers, json={"status": "deleted"}
    )
    assert bad_status.status_code == 400


def test_relation_requires_existing_classes(
    client: TestClient,
    auth_headers: dict[str, str],
    ontology_cleanup: list[str],
) -> None:
    source = _create_class(client, auth_headers, ontology_cleanup, code=_code("person"))
    target = _create_class(client, auth_headers, ontology_cleanup, code=_code("system"))

    relation = _create_relation(
        client,
        auth_headers,
        ontology_cleanup,
        code=_code("owns"),
        source_id=source["id"],
        target_id=target["id"],
    )
    assert relation["source_class_code"] == source["code"]
    assert relation["target_class_code"] == target["code"]

    missing = client.post(
        RELATIONS,
        headers=auth_headers,
        json={
            "code": _code("bad"),
            "name": "悬空关系",
            "source_class_id": str(uuid.uuid4()),
            "target_class_id": target["id"],
        },
    )
    assert missing.status_code == 404

    duplicated = client.post(
        RELATIONS,
        headers=auth_headers,
        json={
            "code": relation["code"],
            "name": "重复关系",
            "source_class_id": source["id"],
            "target_class_id": target["id"],
        },
    )
    assert duplicated.status_code == 409

    listed = client.get(RELATIONS, headers=auth_headers).json()["data"]
    assert relation["code"] in {item["code"] for item in listed["items"]}


def test_ontology_resync_replays_classes(
    client: TestClient,
    auth_headers: dict[str, str],
    ontology_cleanup: list[str],
) -> None:
    first = _create_class(client, auth_headers, ontology_cleanup, code=_code("doc"), name="文档")
    second = _create_class(client, auth_headers, ontology_cleanup, code=_code("rule"), name="制度")

    with graph_session() as graph:
        graph.run(
            "MATCH (c:Class) WHERE c.code IN $codes DETACH DELETE c",
            codes=[first["code"], second["code"]],
        ).consume()
        gone = graph.run("MATCH (c:Class {code: $code}) RETURN c", code=first["code"]).single()
        assert gone is None

    resp = client.post("/api/v1/ontology/sync", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["projected"] >= 2

    with graph_session() as graph:
        codes = {
            record["c"]["code"]
            for record in graph.run(
                "MATCH (c:Class) WHERE c.code IN $codes RETURN c",
                codes=[first["code"], second["code"]],
            )
        }
    assert codes == {first["code"], second["code"]}
