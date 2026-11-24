"""角色与权限接口集成测试：自定义角色、/auth/me 下发权限、内置保护与防锁死。"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.exceptions import ErrorCode
from app.infra.db.models import AppUser, Role

BASE = "/api/v1/roles"


@pytest.fixture
def custom_roles(db: Session) -> Iterator[list[uuid.UUID]]:
    ids: list[uuid.UUID] = []
    yield ids

    for role_id in ids:
        role = db.get(Role, role_id)
        if role is not None:
            db.delete(role)
            db.flush()
    db.commit()


def _create_role(
    client: TestClient,
    headers: dict[str, str],
    tracked: list[uuid.UUID],
    *,
    permissions: list[str] | None = None,
) -> dict:
    code = f"pytest_role_{uuid.uuid4().hex[:8]}"
    resp = client.post(
        BASE,
        headers=headers,
        json={
            "code": code,
            "name": f"测试角色{code[-4:]}",
            "description": "集成测试",
            "permissions": permissions or ["document:read"],
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    tracked.append(uuid.UUID(data["id"]))
    return data


def test_builtin_roles_expose_permissions(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.get(BASE, headers=auth_headers)

    assert resp.status_code == 200
    roles = {item["code"]: item for item in resp.json()["data"]["items"]}
    assert roles["admin"]["is_system"] is True
    assert "role:manage" in roles["admin"]["permissions"]
    assert "user:manage" in roles["admin"]["permissions"]
    assert "document:write" not in roles["viewer"]["permissions"]


def test_permission_catalog_groups(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.get(f"{BASE}/permissions", headers=auth_headers)

    assert resp.status_code == 200
    groups = resp.json()["data"]["groups"]
    names = [group["group"] for group in groups]
    assert "角色" in names and "文档" in names
    codes = {item["code"] for group in groups for item in group["items"]}
    assert {"department:manage", "role:manage"} <= codes


def test_create_custom_role_grants_permissions_to_members(
    client: TestClient,
    auth_headers: dict[str, str],
    custom_roles: list[uuid.UUID],
    make_user: Callable[..., AppUser],
) -> None:
    role = _create_role(
        client, auth_headers, custom_roles, permissions=["document:read", "graph:read"]
    )

    assert role["is_system"] is False
    assert sorted(role["permissions"]) == ["document:read", "graph:read"]

    user = make_user([role["code"]])
    login = client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": "Passw0rd!23"},
    )
    token = login.json()["data"]["access_token"]
    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    ).json()["data"]

    assert me["roles"] == [role["code"]]
    assert sorted(me["permissions"]) == ["document:read", "graph:read"]
    # 只读权限不应被放行写操作
    denied = client.post(
        "/api/v1/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("a.pdf", b"%PDF-1.7\n", "application/pdf")},
    )
    assert denied.status_code == 403


def test_update_role_permissions_reflected_in_me(
    client: TestClient,
    auth_headers: dict[str, str],
    custom_roles: list[uuid.UUID],
    make_user: Callable[..., AppUser],
) -> None:
    role = _create_role(client, auth_headers, custom_roles, permissions=["document:read"])
    user = make_user([role["code"]])

    resp = client.patch(
        f"{BASE}/{role['id']}",
        headers=auth_headers,
        json={"permissions": ["document:read", "document:write"]},
    )
    assert resp.status_code == 200, resp.text
    assert sorted(resp.json()["data"]["permissions"]) == [
        "document:read",
        "document:write",
    ]

    login = client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": "Passw0rd!23"},
    )
    token = login.json()["data"]["access_token"]
    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    ).json()["data"]
    assert sorted(me["permissions"]) == ["document:read", "document:write"]


def test_permission_takes_effect_without_relogin(
    client: TestClient,
    auth_headers: dict[str, str],
    custom_roles: list[uuid.UUID],
    make_user: Callable[..., AppUser],
    cleanup_document: list[uuid.UUID],
) -> None:
    """权限是每次请求现查库的，改完角色不必重新登录即生效。"""

    role = _create_role(client, auth_headers, custom_roles, permissions=["document:read"])
    user = make_user([role["code"]])
    login = client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": "Passw0rd!23"},
    )
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    denied = client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("a.pdf", b"%PDF-1.7\n", "application/pdf")},
    )
    assert denied.status_code == 403

    granted = client.patch(
        f"{BASE}/{role['id']}",
        headers=auth_headers,
        json={"permissions": ["document:read", "document:write"]},
    )
    assert granted.status_code == 200, granted.text

    uploaded = client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("a.pdf", b"%PDF-1.7\n", "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    cleanup_document.append(uuid.UUID(uploaded.json()["data"]["id"]))

    me = client.get("/api/v1/auth/me", headers=headers).json()["data"]
    assert sorted(me["permissions"]) == ["document:read", "document:write"]


def test_duplicate_code_and_unknown_permission_rejected(
    client: TestClient,
    auth_headers: dict[str, str],
    custom_roles: list[uuid.UUID],
) -> None:
    role = _create_role(client, auth_headers, custom_roles)

    duplicate = client.post(
        BASE,
        headers=auth_headers,
        json={"code": role["code"], "name": "重复编码", "permissions": []},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == ErrorCode.CONFLICT

    unknown = client.patch(
        f"{BASE}/{role['id']}", headers=auth_headers, json={"permissions": ["no:such"]}
    )
    assert unknown.status_code == 400
    assert "未知权限" in unknown.json()["message"]


def test_builtin_role_cannot_be_deleted(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    roles = client.get(BASE, headers=auth_headers).json()["data"]["items"]
    viewer = next(item for item in roles if item["code"] == "viewer")

    resp = client.delete(f"{BASE}/{viewer['id']}", headers=auth_headers)

    assert resp.status_code == 400
    assert "内置角色不可删除" in resp.json()["message"]


def test_cannot_remove_last_manageable_role(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """把 admin 角色的管理权限清空会让系统失去可管理入口，必须回滚。"""

    roles = client.get(BASE, headers=auth_headers).json()["data"]["items"]
    admin = next(item for item in roles if item["code"] == "admin")
    before = sorted(admin["permissions"])

    resp = client.patch(
        f"{BASE}/{admin['id']}", headers=auth_headers, json={"permissions": []}
    )

    assert resp.status_code == 400
    after = client.get(BASE, headers=auth_headers).json()["data"]["items"]
    restored = next(item for item in after if item["code"] == "admin")
    assert sorted(restored["permissions"]) == before


def test_unused_custom_role_can_be_deleted(
    client: TestClient,
    auth_headers: dict[str, str],
    custom_roles: list[uuid.UUID],
) -> None:
    role = _create_role(client, auth_headers, custom_roles)

    assert client.delete(f"{BASE}/{role['id']}", headers=auth_headers).status_code == 204

    remaining = client.get(BASE, headers=auth_headers).json()["data"]["items"]
    assert role["code"] not in {item["code"] for item in remaining}
