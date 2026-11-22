"""审计日志集成测试（任务 2.8）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.infra.db.models import AuditLog
from app.services.audit_service import partition_name

BASE = "/api/v1/users"
PASSWORD = "Passw0rd!23"


def _rows(db: Session, request_id: str) -> list[AuditLog]:
    # 测试会话可能处于旧事务快照，先结束再读
    db.rollback()
    return list(
        db.scalars(
            select(AuditLog).where(AuditLog.request_id == request_id).order_by(AuditLog.id)
        ).all()
    )


def test_login_success_is_audited(
    client: TestClient, db: Session, admin_credentials: dict[str, str]
) -> None:
    request_id = f"audit-ok-{uuid.uuid4().hex[:8]}"

    resp = client.post(
        "/api/v1/auth/login", headers={"X-Request-Id": request_id}, json=admin_credentials
    )
    assert resp.status_code == 200
    user_id = resp.json()["data"]["user"]["id"]

    rows = [row for row in _rows(db, request_id) if row.action == "login.success"]
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_name == settings.seed_admin_username
    assert row.object_type == "user"
    assert row.object_id == user_id
    assert row.ip, "登录审计必须记录来源 IP"
    assert row.request_id == request_id


def test_login_failure_is_audited_even_though_request_rolls_back(
    client: TestClient, db: Session, unique_username: str
) -> None:
    request_id = f"audit-bad-{uuid.uuid4().hex[:8]}"

    resp = client.post(
        "/api/v1/auth/login",
        headers={"X-Request-Id": request_id},
        json={"username": unique_username, "password": "wrong-password"},
    )

    assert resp.status_code == 401
    rows = [row for row in _rows(db, request_id) if row.action == "login.failure"]
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_name == unique_username
    assert row.ip
    assert row.after == {"reason": "bad_credentials"}


def test_user_change_is_audited_with_before_and_after(
    client: TestClient,
    db: Session,
    auth_headers: dict[str, str],
    cleanup_user: list[str],
) -> None:
    create_rid = f"audit-create-{uuid.uuid4().hex[:8]}"
    username = f"pytest_audit_{uuid.uuid4().hex[:8]}"

    created = client.post(
        BASE,
        headers={**auth_headers, "X-Request-Id": create_rid},
        json={
            "username": username,
            "password": PASSWORD,
            "display_name": "审计前",
            "roles": ["viewer"],
        },
    )
    assert created.status_code == 201, created.text
    cleanup_user.append(username)
    user_id = created.json()["data"]["id"]

    create_rows = [row for row in _rows(db, create_rid) if row.action == "user.create"]
    assert len(create_rows) == 1
    assert create_rows[0].after["username"] == username
    assert create_rows[0].after["roles"] == ["viewer"]
    assert create_rows[0].actor_name == settings.seed_admin_username

    update_rid = f"audit-update-{uuid.uuid4().hex[:8]}"
    updated = client.patch(
        f"{BASE}/{user_id}",
        headers={**auth_headers, "X-Request-Id": update_rid},
        json={"display_name": "审计后", "status": "disabled"},
    )
    assert updated.status_code == 200, updated.text

    update_rows = [row for row in _rows(db, update_rid) if row.action == "user.update"]
    assert len(update_rows) == 1
    row = update_rows[0]
    assert row.before["display_name"] == "审计前"
    assert row.after["display_name"] == "审计后"
    assert (row.before["status"], row.after["status"]) == ("active", "disabled")


def test_audit_month_partition_exists(db: Session) -> None:
    db.rollback()
    name = partition_name(datetime.now(UTC))
    exists = db.scalar(text("SELECT to_regclass(:name)"), {"name": name})

    assert exists == name
