"""用户管理接口集成测试（任务 2.7）。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.exceptions import ErrorCode

BASE = "/api/v1/users"
PASSWORD = "Passw0rd!23"


@pytest.fixture
def created(client: TestClient, auth_headers: dict[str, str]) -> Iterator[list[str]]:
    """通过接口创建用户，并登记用户名以便清理。"""
    names: list[str] = []

    def _create(roles: list[str] | None = None, *, prefix: str = "pytest_u") -> dict:
        username = f"{prefix}_{uuid.uuid4().hex[:10]}"
        resp = client.post(
            BASE,
            headers=auth_headers,
            json={
                "username": username,
                "password": PASSWORD,
                "display_name": "接口创建用户",
                "roles": roles or ["viewer"],
            },
        )
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


def test_create_user_returns_roles(client: TestClient, created) -> None:
    data = created(["contributor", "reviewer"])

    assert data["username"].startswith("pytest_u")
    assert sorted(data["roles"]) == ["contributor", "reviewer"]
    assert data["status"] == "active"


def test_create_duplicate_username_conflict(
    client: TestClient, auth_headers: dict[str, str], created
) -> None:
    data = created()

    resp = client.post(
        BASE,
        headers=auth_headers,
        json={"username": data["username"], "password": PASSWORD, "display_name": "重复"},
    )

    assert resp.status_code == 409
    assert resp.json()["code"] == ErrorCode.CONFLICT


def test_create_with_unknown_role_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.post(
        BASE,
        headers=auth_headers,
        json={
            "username": f"pytest_unknown_{uuid.uuid4().hex[:8]}",
            "password": PASSWORD,
            "display_name": "未知角色",
            "roles": ["superman"],
        },
    )

    assert resp.status_code == 400
    assert "未知角色" in resp.json()["message"]


def test_list_users_filter_and_pagination(
    client: TestClient, auth_headers: dict[str, str], created
) -> None:
    data = created(["viewer"])
    keyword = data["username"][:16]

    resp = client.get(
        BASE, headers=auth_headers, params={"keyword": keyword, "page": 1, "page_size": 10}
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["total"] == 1
    assert body["items"][0]["username"] == data["username"]
    assert body["items"][0]["roles"] == ["viewer"]

    empty = client.get(
        BASE, headers=auth_headers, params={"keyword": "no-such-user-xyz", "page": 1}
    )
    assert empty.status_code == 200
    assert empty.json()["data"]["total"] == 0
    assert empty.json()["data"]["items"] == []


def test_list_users_filter_by_status(
    client: TestClient, auth_headers: dict[str, str], created
) -> None:
    data = created()
    client.patch(f"{BASE}/{data['id']}", headers=auth_headers, json={"status": "disabled"})

    resp = client.get(
        BASE,
        headers=auth_headers,
        params={"status": "disabled", "keyword": data["username"]},
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["total"] == 1


def test_update_user_profile_and_roles(
    client: TestClient, auth_headers: dict[str, str], created
) -> None:
    data = created(["viewer"])

    resp = client.patch(
        f"{BASE}/{data['id']}",
        headers=auth_headers,
        json={"display_name": "改名后", "roles": ["contributor"]},
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["display_name"] == "改名后"
    assert body["roles"] == ["contributor"]


def test_admin_cannot_disable_self(client: TestClient, auth_headers: dict[str, str]) -> None:
    me = client.get("/api/v1/auth/me", headers=auth_headers).json()["data"]

    resp = client.patch(
        f"{BASE}/{me['id']}", headers=auth_headers, json={"status": "disabled"}
    )

    assert resp.status_code == 400
    assert "不能禁用自己" in resp.json()["message"]


def test_admin_cannot_change_own_roles(client: TestClient, auth_headers: dict[str, str]) -> None:
    me = client.get("/api/v1/auth/me", headers=auth_headers).json()["data"]

    resp = client.patch(
        f"{BASE}/{me['id']}", headers=auth_headers, json={"roles": ["viewer"]}
    )

    assert resp.status_code == 400
    assert "角色" in resp.json()["message"]


def test_update_unknown_user_returns_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    resp = client.patch(
        f"{BASE}/{uuid.uuid4()}", headers=auth_headers, json={"display_name": "无此人"}
    )

    assert resp.status_code == 404
    assert resp.json()["code"] == ErrorCode.NOT_FOUND


def test_reset_password_revokes_tokens(
    client: TestClient, auth_headers: dict[str, str], created
) -> None:
    data = created()
    login = client.post(
        "/api/v1/auth/login", json={"username": data["username"], "password": PASSWORD}
    ).json()["data"]

    resp = client.post(
        f"{BASE}/{data['id']}/reset-password",
        headers=auth_headers,
        json={"new_password": "NewPassw0rd!"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["revoked_tokens"] == 1

    replay = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
    )
    assert replay.status_code == 401

    old = client.post(
        "/api/v1/auth/login", json={"username": data["username"], "password": PASSWORD}
    )
    assert old.status_code == 401

    new = client.post(
        "/api/v1/auth/login",
        json={"username": data["username"], "password": "NewPassw0rd!"},
    )
    assert new.status_code == 200


def test_disabling_user_blocks_login(
    client: TestClient, auth_headers: dict[str, str], created
) -> None:
    data = created()

    client.patch(f"{BASE}/{data['id']}", headers=auth_headers, json={"status": "disabled"})

    resp = client.post(
        "/api/v1/auth/login", json={"username": data["username"], "password": PASSWORD}
    )

    assert resp.status_code == 403
    assert resp.json()["code"] == ErrorCode.FORBIDDEN


def test_seed_admin_present_in_user_list(client: TestClient, auth_headers: dict[str, str]) -> None:
    resp = client.get(BASE, headers=auth_headers, params={"keyword": settings.seed_admin_username})

    assert resp.status_code == 200
    usernames = [item["username"] for item in resp.json()["data"]["items"]]
    assert settings.seed_admin_username in usernames
