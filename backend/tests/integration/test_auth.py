"""认证接口集成测试（任务 2.4）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ErrorCode
from app.core.security import hash_password
from app.domain.enums import UserStatus
from app.infra.db.models import AppUser


def _create_user(db: Session, username: str, *, status: str = UserStatus.ACTIVE.value) -> AppUser:
    admin = db.query(AppUser).filter(AppUser.username == settings.seed_admin_username).one()
    user = AppUser(
        workspace_id=admin.workspace_id,
        username=username,
        password_hash=hash_password("Passw0rd!23"),
        display_name="测试用户",
        status=status,
    )
    db.add(user)
    db.commit()
    return user


def test_login_success(
    client: TestClient, admin_credentials: dict[str, str]
) -> None:
    resp = client.post("/api/v1/auth/login", json=admin_credentials)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == settings.access_token_ttl_seconds
    assert "admin" in data["user"]["roles"]


def test_login_wrong_password(client: TestClient, admin_credentials: dict[str, str]) -> None:
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": admin_credentials["username"], "password": "wrong-password"},
    )

    assert resp.status_code == 401
    assert resp.json()["code"] == ErrorCode.UNAUTHORIZED


def test_login_unknown_user(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/auth/login", json={"username": "no-such-user", "password": "whatever"}
    )

    assert resp.status_code == 401
    assert resp.json()["message"] == "用户名或密码错误"


def test_login_disabled_account(
    client: TestClient, db: Session, unique_username: str, cleanup_user: list[str]
) -> None:
    _create_user(db, unique_username, status=UserStatus.DISABLED.value)
    cleanup_user.append(unique_username)

    resp = client.post(
        "/api/v1/auth/login",
        json={"username": unique_username, "password": "Passw0rd!23"},
    )

    assert resp.status_code == 403
    assert resp.json()["code"] == ErrorCode.FORBIDDEN


def test_me_requires_token(client: TestClient) -> None:
    resp = client.get("/api/v1/auth/me")

    assert resp.status_code == 401
    assert resp.json()["code"] == ErrorCode.UNAUTHORIZED


def test_me_returns_roles(client: TestClient, auth_headers: dict[str, str]) -> None:
    resp = client.get("/api/v1/auth/me", headers=auth_headers)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["username"] == settings.seed_admin_username
    assert "admin" in data["roles"]
    assert data["workspace_id"]


def test_expired_access_token_rejected(
    client: TestClient, db: Session
) -> None:
    admin = db.query(AppUser).filter(AppUser.username == settings.seed_admin_username).one()
    expired = pyjwt.encode(
        {
            "sub": str(admin.id),
            "type": "access",
            "exp": datetime.now(UTC) - timedelta(minutes=5),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})

    assert resp.status_code == 401
    assert resp.json()["code"] == ErrorCode.TOKEN_EXPIRED


def test_refresh_token_replay_rejected(
    client: TestClient, admin_credentials: dict[str, str]
) -> None:
    login = client.post("/api/v1/auth/login", json=admin_credentials).json()["data"]
    refresh_token = login["refresh_token"]

    first = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert first.status_code == 200
    assert first.json()["data"]["refresh_token"] != refresh_token

    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert replay.status_code == 401
    assert replay.json()["code"] == ErrorCode.UNAUTHORIZED


def test_logout_revokes_refresh_token(
    client: TestClient, admin_credentials: dict[str, str]
) -> None:
    login = client.post("/api/v1/auth/login", json=admin_credentials).json()["data"]
    headers = {"Authorization": f"Bearer {login['access_token']}"}

    out = client.post(
        "/api/v1/auth/logout",
        headers=headers,
        json={"refresh_token": login["refresh_token"]},
    )
    assert out.status_code == 200
    assert out.json()["data"]["revoked"] == 1

    replay = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
    )
    assert replay.status_code == 401


@pytest.mark.parametrize("bad_token", ["", "not-a-jwt"])
def test_malformed_token_rejected(client: TestClient, bad_token: str) -> None:
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {bad_token}"})

    assert resp.status_code == 401
