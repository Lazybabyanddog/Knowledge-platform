"""部门管理接口集成测试：建树、层级上限、移动校验与删除保护。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.exceptions import ErrorCode
from app.infra.db.models import AppUser, Department

BASE = "/api/v1/departments"


@pytest.fixture
def created_departments(db: Session) -> Iterator[list[str]]:
    ids: list[str] = []
    yield ids

    uuids = [uuid.UUID(item) for item in ids]
    if uuids:
        # 先把测试人员从这些部门解绑，否则 department_id 的 RESTRICT 外键会阻止删除
        db.execute(
            update(AppUser)
            .where(AppUser.department_id.in_(uuids))
            .values(department_id=None)
        )
    for department_id in reversed(uuids):
        row = db.get(Department, department_id)
        if row is not None:
            db.delete(row)
            db.flush()
    db.commit()


def _create(
    client: TestClient,
    headers: dict[str, str],
    created: list[str],
    name: str,
    parent_id: str | None = None,
) -> dict:
    resp = client.post(
        BASE, headers=headers, json={"name": name, "parent_id": parent_id}
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    created.append(data["id"])
    return data


def test_create_and_list_flat_tree(
    client: TestClient,
    auth_headers: dict[str, str],
    created_departments: list[str],
) -> None:
    suffix = uuid.uuid4().hex[:6]
    root = _create(client, auth_headers, created_departments, f"技术中心{suffix}")
    child = _create(
        client, auth_headers, created_departments, f"平台组{suffix}", root["id"]
    )

    assert root["parent_id"] is None
    assert child["parent_id"] == root["id"]

    resp = client.get(BASE, headers=auth_headers)
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    by_id = {item["id"]: item for item in items}
    assert by_id[root["id"]]["user_count"] == 0
    assert by_id[child["id"]]["parent_id"] == root["id"]


def test_duplicate_name_under_same_parent_conflict(
    client: TestClient,
    auth_headers: dict[str, str],
    created_departments: list[str],
) -> None:
    suffix = uuid.uuid4().hex[:6]
    _create(client, auth_headers, created_departments, f"财务部{suffix}")

    resp = client.post(
        BASE, headers=auth_headers, json={"name": f"财务部{suffix}"}
    )

    assert resp.status_code == 409
    assert resp.json()["code"] == ErrorCode.CONFLICT


def test_move_into_own_subtree_rejected(
    client: TestClient,
    auth_headers: dict[str, str],
    created_departments: list[str],
) -> None:
    suffix = uuid.uuid4().hex[:6]
    root = _create(client, auth_headers, created_departments, f"总部{suffix}")
    child = _create(
        client, auth_headers, created_departments, f"分部{suffix}", root["id"]
    )

    to_self = client.patch(
        f"{BASE}/{root['id']}", headers=auth_headers, json={"parent_id": root["id"]}
    )
    assert to_self.status_code == 400

    to_child = client.patch(
        f"{BASE}/{root['id']}", headers=auth_headers, json={"parent_id": child["id"]}
    )
    assert to_child.status_code == 400
    assert "下级" in to_child.json()["message"]


def test_depth_limit_is_five(
    client: TestClient,
    auth_headers: dict[str, str],
    created_departments: list[str],
) -> None:
    suffix = uuid.uuid4().hex[:6]
    parent_id: str | None = None
    for level in range(1, 6):
        node = _create(
            client, auth_headers, created_departments, f"L{level}-{suffix}", parent_id
        )
        parent_id = node["id"]

    resp = client.post(
        BASE, headers=auth_headers, json={"name": f"L6-{suffix}", "parent_id": parent_id}
    )

    assert resp.status_code == 400
    assert "5 层" in resp.json()["message"]


def test_delete_blocked_by_children_and_users(
    client: TestClient,
    auth_headers: dict[str, str],
    created_departments: list[str],
) -> None:
    from app.infra.db.session import SessionLocal

    suffix = uuid.uuid4().hex[:6]
    root = _create(client, auth_headers, created_departments, f"市场部{suffix}")
    child = _create(
        client, auth_headers, created_departments, f"品牌组{suffix}", root["id"]
    )

    blocked = client.delete(f"{BASE}/{root['id']}", headers=auth_headers)
    assert blocked.status_code == 409

    assert client.delete(f"{BASE}/{child['id']}", headers=auth_headers).status_code == 204

    # 把当前管理员临时挂到根部门下，验证"有人员"也会阻止删除
    me = client.get("/api/v1/auth/me", headers=auth_headers).json()["data"]
    moved = client.patch(
        f"/api/v1/users/{me['id']}",
        headers=auth_headers,
        json={"department_id": root["id"]},
    )
    assert moved.status_code == 200, moved.text
    try:
        with_users = client.delete(f"{BASE}/{root['id']}", headers=auth_headers)
        assert with_users.status_code == 409
        assert "人员" in with_users.json()["message"]
    finally:
        session = SessionLocal()
        try:
            session.execute(
                update(AppUser)
                .where(AppUser.id == uuid.UUID(me["id"]))
                .values(department_id=None)
            )
            session.commit()
        finally:
            session.close()

    assert client.delete(f"{BASE}/{root['id']}", headers=auth_headers).status_code == 204


def test_viewer_cannot_manage_departments(
    client: TestClient,
    role_headers,
) -> None:
    headers = role_headers(["viewer"])

    assert client.get(BASE, headers=headers).status_code == 403
    assert (
        client.post(BASE, headers=headers, json={"name": f"越权{uuid.uuid4().hex[:6]}"}).status_code
        == 403
    )
