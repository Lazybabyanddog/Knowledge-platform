"""人员部门归属：创建/更新/清除归属与按部门（含下级）筛选。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

BASE = "/api/v1/users"
PASSWORD = "Passw0rd!23"


@pytest.fixture
def created_users(client: TestClient, auth_headers: dict[str, str]) -> Iterator[list[str]]:
    names: list[str] = []

    def _create(*, department_id: str | None = None) -> dict:
        username = f"pytest_dept_{uuid.uuid4().hex[:10]}"
        payload: dict = {
            "username": username,
            "password": PASSWORD,
            "display_name": "部门测试用户",
            "roles": ["viewer"],
        }
        if department_id is not None:
            payload["department_id"] = department_id
        resp = client.post(BASE, headers=auth_headers, json=payload)
        assert resp.status_code == 201, resp.text
        names.append(username)
        return resp.json()["data"]

    yield _create

    from app.infra.db.models import AppUser
    from app.infra.db.session import SessionLocal

    session = SessionLocal()
    try:
        for name in names:
            user = session.query(AppUser).filter(AppUser.username == name).one_or_none()
            if user is not None:
                session.delete(user)
        session.commit()
    finally:
        session.close()


def test_create_user_with_department_returns_name(
    client: TestClient,
    auth_headers: dict[str, str],
    department_tree: dict[str, dict],
    created_users,
) -> None:
    data = created_users(department_id=department_tree["child"]["id"])

    assert data["department_id"] == department_tree["child"]["id"]
    assert data["department_name"] == department_tree["child"]["name"]

    listed = client.get(
        BASE, headers=auth_headers, params={"keyword": data["username"]}
    ).json()["data"]
    assert listed["items"][0]["department_name"] == department_tree["child"]["name"]


def test_list_filter_by_parent_department_includes_children(
    client: TestClient,
    auth_headers: dict[str, str],
    department_tree: dict[str, dict],
    created_users,
) -> None:
    in_child = created_users(department_id=department_tree["child"]["id"])
    no_department = created_users()

    by_root = client.get(
        BASE,
        headers=auth_headers,
        params={"department_id": department_tree["root"]["id"], "page_size": 50},
    ).json()["data"]
    ids = {item["id"] for item in by_root["items"]}
    assert in_child["id"] in ids
    assert no_department["id"] not in ids

    by_unknown = client.get(
        BASE, headers=auth_headers, params={"department_id": str(uuid.uuid4())}
    )
    assert by_unknown.status_code == 400
    assert "部门不存在" in by_unknown.json()["message"]


def test_update_and_clear_department(
    client: TestClient,
    auth_headers: dict[str, str],
    department_tree: dict[str, dict],
    created_users,
) -> None:
    data = created_users(department_id=department_tree["child"]["id"])

    moved = client.patch(
        f"{BASE}/{data['id']}",
        headers=auth_headers,
        json={"department_id": department_tree["root"]["id"]},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["data"]["department_name"] == department_tree["root"]["name"]

    cleared = client.patch(
        f"{BASE}/{data['id']}", headers=auth_headers, json={"department_id": None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["data"]["department_id"] is None
    assert cleared.json()["data"]["department_name"] is None


def test_unknown_department_rejected(
    client: TestClient, auth_headers: dict[str, str], created_users
) -> None:
    """创建时引用不存在的部门必须 400，不能落库成悬空引用。"""

    resp = client.post(
        BASE,
        headers=auth_headers,
        json={
            "username": f"pytest_dept_bad_{uuid.uuid4().hex[:8]}",
            "password": PASSWORD,
            "display_name": "错误部门",
            "roles": ["viewer"],
            "department_id": str(uuid.uuid4()),
        },
    )

    assert resp.status_code == 400
    assert "部门不存在" in resp.json()["message"]
