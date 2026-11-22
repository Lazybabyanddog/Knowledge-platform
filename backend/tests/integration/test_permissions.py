"""权限矩阵验收（任务 2.6）：四种角色对上传/审批/管理接口的放行结果。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.deps import Principal, require_permission
from app.core.exceptions import register_exception_handlers
from app.domain.policies import Permission
from app.infra.db.models import AppUser

PASSWORD = "Passw0rd!23"


def _probe_app() -> FastAPI:
    """用真实依赖挂两个探针路由，代表后续的上传与审批接口。"""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/probe/upload")
    def upload(
        principal: Annotated[Principal, Depends(require_permission(Permission.DOCUMENT_WRITE))],
    ) -> dict[str, list[str]]:
        return {"roles": principal.roles}

    @app.get("/probe/approve")
    def approve(
        principal: Annotated[Principal, Depends(require_permission(Permission.REVIEW_APPROVE))],
    ) -> dict[str, list[str]]:
        return {"roles": principal.roles}

    return app


def _headers(client: TestClient, username: str) -> dict[str, str]:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


@pytest.mark.parametrize(
    ("role", "upload", "approve", "manage"),
    [
        ("admin", 200, 200, 200),
        ("contributor", 200, 403, 403),
        ("reviewer", 403, 200, 403),
        ("viewer", 403, 403, 403),
    ],
)
def test_role_permission_matrix(
    client: TestClient,
    make_user: Callable[[list[str]], AppUser],
    role: str,
    upload: int,
    approve: int,
    manage: int,
) -> None:
    user = make_user([role])
    headers = _headers(client, user.username)
    probe = TestClient(_probe_app(), raise_server_exceptions=False)

    assert probe.get("/probe/upload", headers=headers).status_code == upload
    assert probe.get("/probe/approve", headers=headers).status_code == approve
    assert client.get("/api/v1/users", headers=headers).status_code == manage


def test_unauthenticated_is_401(client: TestClient) -> None:
    assert client.get("/api/v1/users").status_code == 401
