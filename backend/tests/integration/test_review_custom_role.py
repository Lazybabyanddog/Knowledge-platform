"""自定义角色参与审批：流程保存与审批人匹配都按数据库里的角色编码。"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.infra.db.models import AppUser, ReviewFlow, Role

DOCS = "/api/v1/documents"
FLOWS = "/api/v1/review-flows"
REVIEWS = "/api/v1/reviews"
PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"


@pytest.fixture
def custom_role(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> Iterator[dict]:
    code = f"pytest_auditor_{uuid.uuid4().hex[:6]}"
    resp = client.post(
        "/api/v1/roles",
        headers=auth_headers,
        json={
            "code": code,
            "name": "自定义审核员",
            "permissions": ["document:read", "review:approve"],
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]

    yield data

    role = db.get(Role, uuid.UUID(data["id"]))
    if role is not None:
        db.delete(role)
        db.commit()


@pytest.fixture
def cleanup_flow(db: Session) -> Iterator[list[uuid.UUID]]:
    created: list[uuid.UUID] = []
    yield created
    for flow_id in created:
        db.execute(delete(ReviewFlow).where(ReviewFlow.id == flow_id))
    db.commit()


def test_custom_role_can_approve_and_viewer_cannot(
    client: TestClient,
    auth_headers: dict[str, str],
    custom_role: dict,
    cleanup_flow: list[uuid.UUID],
    cleanup_document: list[uuid.UUID],
    make_user: Callable[..., AppUser],
) -> None:
    flow = client.post(
        FLOWS,
        headers=auth_headers,
        json={
            "name": f"自定义角色流程-{uuid.uuid4().hex[:6]}",
            "doc_type": "pdf",
            "steps": [
                {
                    "name": "内容审核",
                    "approver_type": "role",
                    "approver_value": custom_role["code"],
                }
            ],
            "is_default": True,
        },
    )
    assert flow.status_code == 201, flow.text
    cleanup_flow.append(uuid.UUID(flow.json()["data"]["id"]))

    document = client.post(
        DOCS, headers=auth_headers, files={"file": ("制度文件.pdf", PDF, "application/pdf")}
    ).json()["data"]
    cleanup_document.append(uuid.UUID(document["id"]))

    submitted = client.post(
        f"{REVIEWS}/submit",
        headers=auth_headers,
        json={"document_id": document["id"], "document_version_id": document["current_version_id"]},
    )
    assert submitted.status_code == 201, submitted.text
    instance_id = submitted.json()["data"]["id"]

    auditor = make_user([custom_role["code"]])
    login = client.post(
        "/api/v1/auth/login",
        json={"username": auditor.username, "password": "Passw0rd!23"},
    )
    auditor_headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    viewer = make_user(["viewer"])
    viewer_login = client.post(
        "/api/v1/auth/login",
        json={"username": viewer.username, "password": "Passw0rd!23"},
    )
    viewer_headers = {"Authorization": f"Bearer {viewer_login.json()['data']['access_token']}"}

    denied = client.post(f"{REVIEWS}/{instance_id}/approve", headers=viewer_headers, json={})
    assert denied.status_code == 403

    approved = client.post(f"{REVIEWS}/{instance_id}/approve", headers=auditor_headers, json={})
    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["status"] == "approved"

    detail = client.get(f"{DOCS}/{document['id']}", headers=auth_headers).json()["data"]
    assert detail["status"] == "published"
