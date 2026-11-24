"""集成测试公共夹具。

直接连开发库，测试创建的数据带 uuid 后缀并在结束时清理，
避免污染种子数据；不引入独立测试库是为了保证 schema 与迁移始终一致。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.domain.enums import UserStatus
from app.infra.cache.redis_client import get_redis
from app.infra.db.models import AppUser, Department, Document, DocumentVersion
from app.infra.db.repositories import UserRepository
from app.infra.db.session import SessionLocal
from app.infra.graph.driver import apply_schema, graph_session
from app.infra.storage import get_storage
from app.main import create_app


@pytest.fixture(scope="session")
def client() -> TestClient:
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def unique_username() -> str:
    return f"pytest_{uuid.uuid4().hex[:10]}"


@pytest.fixture
def admin_credentials() -> dict[str, str]:
    return {
        "username": settings.seed_admin_username,
        "password": settings.seed_admin_password,
    }


@pytest.fixture
def auth_headers(client: TestClient, admin_credentials: dict[str, str]) -> dict[str, str]:
    resp = client.post("/api/v1/auth/login", json=admin_credentials)
    assert resp.status_code == 200, resp.text
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def cleanup_user(db: Session) -> Iterator[list[str]]:
    """测试创建的用户名登记在此，结束后按用户名删除。"""
    created: list[str] = []
    yield created

    for username in created:
        user = db.query(AppUser).filter(AppUser.username == username).one_or_none()
        if user is not None:
            db.delete(user)
    db.commit()


@pytest.fixture
def make_user(
    db: Session, cleanup_user: list[str], unique_username: str
) -> Iterator[Callable[..., AppUser]]:
    """按角色建测试用户：make_user(["viewer", "reviewer"])，用户名自动唯一并登记清理。"""
    counter = {"n": 0}

    def _make(roles: list[str], username: str | None = None) -> AppUser:
        admin = db.query(AppUser).filter(AppUser.username == settings.seed_admin_username).one()
        name = username or f"{unique_username}_{counter['n']}"
        counter["n"] += 1
        user = AppUser(
            workspace_id=admin.workspace_id,
            username=name,
            password_hash=hash_password("Passw0rd!23"),
            display_name="权限测试用户",
            status=UserStatus.ACTIVE.value,
        )
        db.add(user)
        db.flush()
        repo = UserRepository(db)
        repo.set_roles(user.id, repo.role_ids_by_codes(roles))
        db.commit()
        cleanup_user.append(name)
        return user

    yield _make


@pytest.fixture
def department_tree(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> Iterator[dict[str, dict]]:
    """建两级测试部门（研发中心 / 文档组），结束后解绑人员并按子→父删除。"""
    suffix = uuid.uuid4().hex[:6]
    root_resp = client.post(
        "/api/v1/departments", headers=auth_headers, json={"name": f"研发中心{suffix}"}
    )
    assert root_resp.status_code == 201, root_resp.text
    root = root_resp.json()["data"]
    child_resp = client.post(
        "/api/v1/departments",
        headers=auth_headers,
        json={"name": f"文档组{suffix}", "parent_id": root["id"]},
    )
    assert child_resp.status_code == 201, child_resp.text
    child = child_resp.json()["data"]

    yield {"root": root, "child": child}

    root_id, child_id = uuid.UUID(root["id"]), uuid.UUID(child["id"])
    db.execute(
        update(AppUser)
        .where(AppUser.department_id.in_([root_id, child_id]))
        .values(department_id=None)
    )
    for department_id in (child_id, root_id):
        row = db.get(Department, department_id)
        if row is not None:
            db.delete(row)
            db.flush()
    db.commit()


@pytest.fixture
def cleanup_document(db: Session) -> Iterator[list[uuid.UUID]]:
    """登记测试创建的文档 id，结束后连同对象存储里的原件一起清理。"""
    created: list[uuid.UUID] = []
    yield created

    storage = get_storage()
    for document_id in created:
        versions = list(
            db.query(DocumentVersion).filter(DocumentVersion.document_id == document_id).all()
        )
        for version in versions:
            storage.delete(version.storage_key)
        document = db.get(Document, document_id)
        if document is not None:
            db.delete(document)
    db.commit()


@pytest.fixture
def role_headers(
    client: TestClient, make_user: Callable[..., AppUser]
) -> Callable[[list[str]], dict[str, str]]:
    """按角色登录并返回请求头：role_headers(["viewer"])。"""

    def _headers(roles: list[str]) -> dict[str, str]:
        user = make_user(roles)
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": user.username, "password": "Passw0rd!23"},
        )
        assert resp.status_code == 200, resp.text
        return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}

    return _headers


@pytest.fixture(scope="session")
def graph_db() -> Iterator[None]:
    """图相关用例的前置条件：Neo4j 可连且约束/索引已就绪。"""
    try:
        with graph_session() as session:
            session.run("RETURN 1 AS ok").consume()
    except Exception:  # noqa: BLE001 - 本地未起 Neo4j 时跳过而非失败
        pytest.skip("Neo4j 不可用，跳过图相关测试")
    apply_schema()
    yield


@pytest.fixture(autouse=True)
def clear_login_failures() -> Iterator[None]:
    """登录失败计数存在 Redis，测试间清空，避免上一个用例把账号锁死。"""
    client = get_redis()

    def _wipe() -> None:
        for key in client.scan_iter("login:fail:*"):
            client.delete(key)

    _wipe()
    yield
    _wipe()
