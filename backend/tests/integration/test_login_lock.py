"""登录失败锁定集成测试（任务 2.5）。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ErrorCode
from app.core.security import hash_password
from app.domain.enums import UserStatus
from app.infra.cache.redis_client import get_redis
from app.infra.db.models import AppUser

PASSWORD = "Passw0rd!23"


def _create_user(db: Session, username: str) -> None:
    admin = db.query(AppUser).filter(AppUser.username == settings.seed_admin_username).one()
    db.add(
        AppUser(
            workspace_id=admin.workspace_id,
            username=username,
            password_hash=hash_password(PASSWORD),
            display_name="锁定测试用户",
            status=UserStatus.ACTIVE.value,
        )
    )
    db.commit()


def test_sixth_attempt_is_locked(
    client: TestClient, db: Session, unique_username: str, cleanup_user: list[str]
) -> None:
    _create_user(db, unique_username)
    cleanup_user.append(unique_username)

    for attempt in range(settings.login_max_failures):
        resp = client.post(
            "/api/v1/auth/login", json={"username": unique_username, "password": "wrong"}
        )
        assert resp.status_code == 401, f"第 {attempt + 1} 次应返回 401"

    # 第 6 次即使密码正确也被拦截
    blocked = client.post(
        "/api/v1/auth/login", json={"username": unique_username, "password": PASSWORD}
    )

    assert blocked.status_code == 429
    body = blocked.json()
    assert body["code"] == ErrorCode.TOO_MANY_REQUESTS
    assert body["data"]["retry_after_seconds"] > 0
    assert body["data"]["failures"] >= settings.login_max_failures


def test_success_resets_counter(
    client: TestClient, db: Session, unique_username: str, cleanup_user: list[str]
) -> None:
    _create_user(db, unique_username)
    cleanup_user.append(unique_username)

    for _ in range(settings.login_max_failures - 1):
        client.post("/api/v1/auth/login", json={"username": unique_username, "password": "wrong"})

    ok = client.post("/api/v1/auth/login", json={"username": unique_username, "password": PASSWORD})
    assert ok.status_code == 200

    key = f"login:fail:{unique_username.lower()}"
    assert get_redis().get(key) is None


def test_lock_is_scoped_per_account(
    client: TestClient, db: Session, unique_username: str, cleanup_user: list[str]
) -> None:
    _create_user(db, unique_username)
    cleanup_user.append(unique_username)

    for _ in range(settings.login_max_failures):
        client.post("/api/v1/auth/login", json={"username": unique_username, "password": "wrong"})

    # 另一个账号不受影响
    other = client.post(
        "/api/v1/auth/login",
        json={
            "username": settings.seed_admin_username,
            "password": settings.seed_admin_password,
        },
    )
    assert other.status_code == 200
