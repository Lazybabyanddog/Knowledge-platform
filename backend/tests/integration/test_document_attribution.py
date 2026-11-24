"""文档归属集成测试：上传人姓名、所属部门与按部门（含下级）筛选。"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.infra.db.models import AppUser

DOCS = "/api/v1/documents"
PASSWORD = "Passw0rd!23"
PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"


@pytest.fixture
def contributor(
    client: TestClient,
    auth_headers: dict[str, str],
    department_tree: dict[str, dict],
    make_user: Callable[..., AppUser],
) -> dict[str, str]:
    """把贡献者挂到子部门，并返回其登录头。"""

    user = make_user(["contributor"])
    moved = client.patch(
        f"/api/v1/users/{user.id}",
        headers=auth_headers,
        json={"department_id": department_tree["child"]["id"]},
    )
    assert moved.status_code == 200, moved.text

    login = client.post(
        "/api/v1/auth/login", json={"username": user.username, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def test_upload_returns_uploader_name_and_department(
    client: TestClient,
    contributor: dict[str, str],
    department_tree: dict[str, dict],
    cleanup_document: list[uuid.UUID],
) -> None:
    resp = client.post(
        DOCS,
        headers=contributor,
        files={"file": ("归属测试.pdf", PDF, "application/pdf")},
    )

    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    cleanup_document.append(uuid.UUID(data["id"]))

    assert data["created_by_name"] == "权限测试用户"
    assert data["created_by_department_id"] == department_tree["child"]["id"]
    assert data["created_by_department_name"] == department_tree["child"]["name"]
    assert data["current_version"]["created_by_name"] == "权限测试用户"

    detail = client.get(f"{DOCS}/{data['id']}", headers=contributor).json()["data"]
    assert detail["created_by_name"] == "权限测试用户"
    assert detail["created_by_department_name"] == department_tree["child"]["name"]

    versions = client.get(f"{DOCS}/{data['id']}/versions", headers=contributor).json()["data"]
    assert versions[0]["created_by_name"] == "权限测试用户"


def test_list_filter_by_department_includes_descendants(
    client: TestClient,
    auth_headers: dict[str, str],
    contributor: dict[str, str],
    department_tree: dict[str, dict],
    cleanup_document: list[uuid.UUID],
) -> None:
    created = client.post(
        DOCS,
        headers=contributor,
        files={"file": ("筛选测试.pdf", PDF, "application/pdf")},
    ).json()["data"]
    cleanup_document.append(uuid.UUID(created["id"]))

    by_child = client.get(
        DOCS, headers=auth_headers, params={"department_id": department_tree["child"]["id"]}
    ).json()["data"]
    assert created["id"] in {item["id"] for item in by_child["items"]}

    by_root = client.get(
        DOCS, headers=auth_headers, params={"department_id": department_tree["root"]["id"]}
    ).json()["data"]
    assert created["id"] in {item["id"] for item in by_root["items"]}

    by_unknown = client.get(
        DOCS, headers=auth_headers, params={"department_id": str(uuid.uuid4())}
    )
    assert by_unknown.status_code == 400
    assert "部门不存在" in by_unknown.json()["message"]


def test_attribution_follows_current_department_and_survives_disable(
    client: TestClient,
    auth_headers: dict[str, str],
    contributor: dict[str, str],
    department_tree: dict[str, dict],
    cleanup_document: list[uuid.UUID],
) -> None:
    """归属取人员当前档案：换部门即更新，停用后仍有姓名。"""

    created = client.post(
        DOCS,
        headers=contributor,
        files={"file": ("变更测试.pdf", PDF, "application/pdf")},
    ).json()["data"]
    cleanup_document.append(uuid.UUID(created["id"]))

    uploader_id = created["created_by"]
    moved = client.patch(
        f"/api/v1/users/{uploader_id}",
        headers=auth_headers,
        json={"department_id": department_tree["root"]["id"]},
    )
    assert moved.status_code == 200, moved.text

    detail = client.get(f"{DOCS}/{created['id']}", headers=auth_headers).json()["data"]
    assert detail["created_by_department_name"] == department_tree["root"]["name"]

    disabled = client.patch(
        f"/api/v1/users/{uploader_id}", headers=auth_headers, json={"status": "disabled"}
    )
    assert disabled.status_code == 200, disabled.text

    listed = client.get(
        DOCS, headers=auth_headers, params={"keyword": created["title"]}
    ).json()["data"]
    row = next(item for item in listed["items"] if item["id"] == created["id"])
    assert row["created_by_name"] == "权限测试用户"


def test_user_without_department_shows_unassigned(
    client: TestClient,
    auth_headers: dict[str, str],
    cleanup_document: list[uuid.UUID],
) -> None:
    resp = client.post(
        DOCS,
        headers=auth_headers,
        files={"file": ("无部门.pdf", PDF, "application/pdf")},
    )

    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    cleanup_document.append(uuid.UUID(data["id"]))

    assert data["created_by_name"] == settings.seed_admin_display_name
    assert data["created_by_department_id"] is None
    assert data["created_by_department_name"] is None
