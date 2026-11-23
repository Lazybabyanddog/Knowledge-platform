"""本体管理集成测试（任务 5.1–5.3）。"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.infra.db.models import AuditLog, OntologyClass, OntologyRelation
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
    status: int = 0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "name": name,
        "attributes": {"owner": "string"},
        "status": status,
    }
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
    status: int = 0,
) -> dict[str, Any]:
    resp = client.post(
        RELATIONS,
        headers=headers,
        json={
            "code": code,
            "name": "测试关系",
            "source_class_id": source_id,
            "target_class_id": target_id,
            "status": status,
        },
    )
    assert resp.status_code == 201, resp.text
    cleanup.append(code)
    return resp.json()["data"]


def test_create_class_projects_to_neo4j(
    client: TestClient,
    db: Session,
    auth_headers: dict[str, str],
    ontology_cleanup: list[str],
) -> None:
    code = _code("person")
    request_id = f"ontology-create-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        CLASSES,
        headers={**auth_headers, "X-Request-Id": request_id},
        json={"code": code, "name": "人员", "attributes": {"owner": "string"}, "status": 0},
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()["data"]
    ontology_cleanup.append(code)

    assert created["code"] == code
    assert created["version"] >= 1
    assert created["status"] == 0

    audit = db.scalars(
        select(AuditLog).where(
            AuditLog.action == "ontology.class_create", AuditLog.request_id == request_id
        )
    ).one()
    assert audit.after["status"] == 0

    listed = client.get(CLASSES, headers=auth_headers).json()["data"]
    assert [item["code"] for item in listed["items"]].count(code) == 1
    assert listed["version"] == max(item["version"] for item in listed["items"])

    with graph_session() as graph:
        record = graph.run(
            "MATCH (c:Class {code: $code}) RETURN c", code=code
        ).single()
    assert record is not None
    assert record["c"]["name"] == "人员"
    assert record["c"]["status"] == 0


def test_class_code_unique_and_write_permissions(
    client: TestClient,
    auth_headers: dict[str, str],
    role_headers: Callable[[list[str]], dict[str, str]],
    ontology_cleanup: list[str],
) -> None:
    code = _code("org")
    _create_class(client, auth_headers, ontology_cleanup, code=code)

    duplicated = client.post(
        CLASSES, headers=auth_headers, json={"code": code, "name": "重复", "status": 0}
    )
    assert duplicated.status_code == 409
    assert duplicated.json()["data"]["code"] == code

    contributor = role_headers(["contributor"])
    assert (
        client.post(
            CLASSES,
            headers=contributor,
            json={"code": _code("x"), "name": "越权", "status": 0},
        )
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

    invalid = client.patch(
        f"{CLASSES}/{child['id']}", headers=auth_headers, json={"status": 1}
    )
    assert invalid.status_code == 200
    assert invalid.json()["data"]["status"] == 1

    visible = client.get(CLASSES, headers=auth_headers).json()["data"]
    assert child["code"] not in {item["code"] for item in visible["items"]}
    disabled_list = client.get(
        CLASSES, headers=auth_headers, params={"include_disabled": True}
    ).json()["data"]
    disabled_items = {item["code"]: item for item in disabled_list["items"]}
    assert child["code"] in disabled_items
    assert disabled_items[child["code"]]["status"] == 1

    bad_status = client.patch(
        f"{CLASSES}/{parent['id']}", headers=auth_headers, json={"status": 2}
    )
    assert bad_status.status_code == 400

    legacy_status = client.patch(
        f"{CLASSES}/{parent['id']}", headers=auth_headers, json={"status": "active"}
    )
    assert legacy_status.status_code == 400


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
            "status": 0,
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
            "status": 0,
        },
    )
    assert duplicated.status_code == 409

    listed = client.get(RELATIONS, headers=auth_headers).json()["data"]
    assert relation["code"] in {item["code"] for item in listed["items"]}


def test_relation_code_allows_mixed_case_and_stays_case_sensitive(
    client: TestClient,
    auth_headers: dict[str, str],
    ontology_cleanup: list[str],
) -> None:
    source = _create_class(client, auth_headers, ontology_cleanup, code=_code("person"))
    target = _create_class(client, auth_headers, ontology_cleanup, code=_code("case"))

    upper_code = f"HAS_PLAINTIFF_{uuid.uuid4().hex[:6].upper()}"
    lower_code = upper_code.lower()
    upper = _create_relation(
        client,
        auth_headers,
        ontology_cleanup,
        code=upper_code,
        source_id=source["id"],
        target_id=target["id"],
    )
    lower = _create_relation(
        client,
        auth_headers,
        ontology_cleanup,
        code=lower_code,
        source_id=source["id"],
        target_id=target["id"],
    )

    assert upper["code"] == upper_code
    assert lower["code"] == lower_code
    listed_codes = {
        item["code"] for item in client.get(RELATIONS, headers=auth_headers).json()["data"]["items"]
    }
    assert {upper_code, lower_code} <= listed_codes


def test_class_code_rejects_invalid_shapes(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    for bad_code in ("2party", "has-plaintiff", "_internal", "a"):
        resp = client.post(
            CLASSES,
            headers=auth_headers,
            json={"code": bad_code, "name": "非法编码", "status": 0},
        )
        assert resp.status_code == 400, bad_code
        fields = {item["field"] for item in resp.json()["data"]}
        assert "body.code" in fields, resp.text


def test_create_requires_status_within_zero_or_one(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    missing = client.post(
        CLASSES, headers=auth_headers, json={"code": _code("nostatus"), "name": "缺状态"}
    )
    assert missing.status_code == 400
    assert "body.status" in {item["field"] for item in missing.json()["data"]}

    for bad_status in (2, -1, "active"):
        resp = client.post(
            CLASSES,
            headers=auth_headers,
            json={"code": _code("badstatus"), "name": "状态越界", "status": bad_status},
        )
        assert resp.status_code == 400, bad_status
        fields = {item["field"] for item in resp.json()["data"]}
        assert "body.status" in fields, resp.text


def test_relation_delete_frees_class_and_removes_graph_node(
    client: TestClient,
    db: Session,
    auth_headers: dict[str, str],
    ontology_cleanup: list[str],
) -> None:
    source = _create_class(
        client, auth_headers, ontology_cleanup, code=_code("person"), name="人员"
    )
    target = _create_class(
        client, auth_headers, ontology_cleanup, code=_code("system"), name="系统"
    )
    relation = _create_relation(
        client,
        auth_headers,
        ontology_cleanup,
        code=_code("owns"),
        source_id=source["id"],
        target_id=target["id"],
    )

    # 被关系引用的实体类不允许删除
    blocked = client.delete(f"{CLASSES}/{source['id']}", headers=auth_headers)
    assert blocked.status_code == 409
    assert blocked.json()["data"]["relations"] == 1
    assert "关系类型" in blocked.json()["message"]

    deleted = client.delete(f"{RELATIONS}/{relation['id']}", headers=auth_headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"] == {"deleted": True}

    remaining = client.get(RELATIONS, headers=auth_headers).json()["data"]["items"]
    assert relation["code"] not in {item["code"] for item in remaining}

    audit = db.scalars(
        select(AuditLog).where(
            AuditLog.action == "ontology.relation_delete",
            AuditLog.object_id == relation["id"],
        )
    ).one()
    assert audit.before["code"] == relation["code"]
    assert audit.before["source"] == source["code"]

    # 关系释放后实体类可以删除，图库节点一并摘除
    removed = client.delete(f"{CLASSES}/{source['id']}", headers=auth_headers)
    assert removed.status_code == 200, removed.text
    classes = client.get(CLASSES, headers=auth_headers, params={"include_disabled": True})
    assert source["code"] not in {item["code"] for item in classes.json()["data"]["items"]}
    with graph_session() as graph:
        record = graph.run("MATCH (c:Class {code: $code}) RETURN c", code=source["code"]).single()
    assert record is None

    assert client.delete(f"{CLASSES}/{source['id']}", headers=auth_headers).status_code == 404


def test_class_delete_promotes_children_and_requires_manage(
    client: TestClient,
    auth_headers: dict[str, str],
    role_headers: Callable[[list[str]], dict[str, str]],
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

    viewer = role_headers(["viewer"])
    assert client.delete(f"{CLASSES}/{parent['id']}", headers=viewer).status_code == 403
    contributor = role_headers(["contributor"])
    assert client.delete(f"{CLASSES}/{parent['id']}", headers=contributor).status_code == 403

    resp = client.delete(f"{CLASSES}/{parent['id']}", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    listed = client.get(CLASSES, headers=auth_headers).json()["data"]["items"]
    survivors = {item["code"]: item for item in listed}
    assert child["code"] in survivors
    assert survivors[child["code"]]["parent_id"] is None


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


ATTR_USCC = {
    "code": "uscc",
    "name": "统一社会信用代码",
    "type": "string",
    "description": "营业执照上的 18 位登记号，需逐字抄写",
    "hint": "形如 91310000XXXXXXXXXX",
    "examples": ["91310000MA1K35QX8N"],
    "required": True,
}
ATTR_STAGE = {
    "code": "stage",
    "name": "融资轮次",
    "type": "enum",
    "description": "最近一轮融资轮次",
    "enum_values": ["天使轮", "A轮", "B轮"],
}
ATTR_TAGS = {
    "code": "tags",
    "name": "行业标签",
    "type": "array",
    "description": "主营行业标签",
    "items_type": "string",
}


def test_class_attribute_schema_and_identity_keys_roundtrip(
    client: TestClient,
    auth_headers: dict[str, str],
    ontology_cleanup: list[str],
) -> None:
    code = _code("company")
    resp = client.post(
        CLASSES,
        headers=auth_headers,
        json={
            "code": code,
            "name": "公司",
            "status": 0,
            "attributes": {
                "uscc": ATTR_USCC,
                "stage": ATTR_STAGE,
                "tags": ATTR_TAGS,
            },
            "identity_keys": ["uscc"],
        },
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()["data"]
    ontology_cleanup.append(code)

    assert created["identity_keys"] == ["uscc"]
    assert created["attributes"]["uscc"]["required"] is True
    assert created["attributes"]["uscc"]["examples"] == ["91310000MA1K35QX8N"]
    assert created["attributes"]["stage"]["enum_values"] == ["天使轮", "A轮", "B轮"]
    assert created["attributes"]["tags"]["items_type"] == "string"

    # 删掉仍被标识键引用的属性：直接 400，并指明修法
    blocked = client.patch(
        f"{CLASSES}/{created['id']}",
        headers=auth_headers,
        json={"attributes": {"stage": ATTR_STAGE, "tags": ATTR_TAGS}},
    )
    assert blocked.status_code == 400, blocked.text
    assert blocked.json()["data"]["identity_keys"] == ["uscc"]
    assert "标识键" in blocked.json()["message"]

    # 先清标识键再删属性：允许
    allowed = client.patch(
        f"{CLASSES}/{created['id']}",
        headers=auth_headers,
        json={"attributes": {"stage": ATTR_STAGE}, "identity_keys": []},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["data"]["identity_keys"] == []
    assert set(allowed.json()["data"]["attributes"]) == {"stage"}


def test_class_identity_keys_must_reference_defined_attributes(
    client: TestClient,
    auth_headers: dict[str, str],
    ontology_cleanup: list[str],
) -> None:
    code = _code("company")
    resp = client.post(
        CLASSES,
        headers=auth_headers,
        json={
            "code": code,
            "name": "公司",
            "status": 0,
            "attributes": {"uscc": ATTR_USCC},
            "identity_keys": ["uscc", "reg_no"],
        },
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["data"]["identity_keys"] == ["reg_no"]

    duplicated = client.post(
        CLASSES,
        headers=auth_headers,
        json={
            "code": _code("dup"),
            "name": "重复标识键",
            "status": 0,
            "attributes": {"uscc": ATTR_USCC},
            "identity_keys": ["uscc", "uscc"],
        },
    )
    assert duplicated.status_code == 400
    assert "重复" in duplicated.json()["message"]


def test_class_attribute_schema_rejects_invalid_definitions(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    for attribute in (
        {"code": "note", "name": "备注", "type": "text", "description": "未支持的类型"},
        {"code": "stage", "name": "轮次", "type": "enum", "description": "缺取值"},
        {"code": "tags", "name": "标签", "type": "array", "description": "缺元素类型"},
        {
            "code": "owner",
            "name": "负责人",
            "type": "string",
            "description": "非枚举却声明取值",
            "enum_values": ["a"],
        },
    ):
        resp = client.post(
            CLASSES,
            headers=auth_headers,
            json={
                "code": _code("badattr"),
                "name": "非法属性",
                "status": 0,
                "attributes": {attribute["code"]: attribute},
            },
        )
        assert resp.status_code == 400, resp.text
        fields = {item["field"] for item in resp.json()["data"]}
        assert fields, resp.text


def test_relation_attributes_roundtrip(
    client: TestClient,
    auth_headers: dict[str, str],
    ontology_cleanup: list[str],
) -> None:
    source = _create_class(client, auth_headers, ontology_cleanup, code=_code("company"))
    target = _create_class(client, auth_headers, ontology_cleanup, code=_code("companyb"))

    amount = {
        "code": "amount",
        "name": "投资金额",
        "type": "number",
        "description": "公告披露的投资金额（万元）",
    }
    created = client.post(
        RELATIONS,
        headers=auth_headers,
        json={
            "code": _code("invests"),
            "name": "投资",
            "source_class_id": source["id"],
            "target_class_id": target["id"],
            "status": 0,
            "attributes": {"amount": amount},
        },
    )
    assert created.status_code == 201, created.text
    relation = created.json()["data"]
    ontology_cleanup.append(relation["code"])
    assert relation["attributes"]["amount"]["type"] == "number"

    updated = client.patch(
        f"{RELATIONS}/{relation['id']}",
        headers=auth_headers,
        json={
            "attributes": {
                "amount": amount,
                "round": {
                    "code": "round",
                    "name": "对应轮次",
                    "type": "enum",
                    "description": "该笔投资对应的融资轮次",
                    "enum_values": ["A轮", "B轮"],
                },
            }
        },
    )
    assert updated.status_code == 200, updated.text
    data = updated.json()["data"]
    assert set(data["attributes"]) == {"amount", "round"}
    assert data["version"] > relation["version"]


def test_legacy_attribute_format_upgrades_to_schema(
    client: TestClient,
    auth_headers: dict[str, str],
    ontology_cleanup: list[str],
) -> None:
    """旧格式 {"type":"string"} 与字符串简写都能保存，读回来是完整定义。"""
    code = _code("legacy")
    created = client.post(
        CLASSES,
        headers=auth_headers,
        json={
            "code": code,
            "name": "存量实体类",
            "attributes": {
                "owner": {"type": "string"},
                "stage": "阶段",
            },
            "status": 0,
        },
    )
    assert created.status_code == 201, created.text
    ontology_cleanup.append(code)
    attributes = created.json()["data"]["attributes"]

    assert attributes["owner"]["name"] == "owner"
    assert attributes["owner"]["description"] == "owner"
    assert attributes["owner"]["required"] is False
    assert attributes["stage"] == {
        "code": "stage",
        "name": "stage",
        "type": "string",
        "description": "阶段",
        "examples": [],
        "required": False,
        "hint": None,
        "enum_values": None,
        "items_type": None,
    }

    # 列表里既有一批历史行，读取不能因为某条老定义不完整而整体 500
    listed = client.get(CLASSES, headers=auth_headers)
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json()["data"]["items"] if item["code"] == code)
    assert row["attributes"]["stage"]["description"] == "阶段"
