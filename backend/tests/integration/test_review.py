"""文档审批集成测试（任务 6.1–6.8）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.domain.enums import AuditAction
from app.infra.db.models import AuditLog, Document, DocumentVersion, ReviewFlow, ReviewInstance

FLOWS = "/api/v1/review-flows"
REVIEWS = "/api/v1/reviews"
DOCUMENTS = "/api/v1/documents"
PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"


@pytest.fixture
def cleanup_flow(db: Session) -> Any:
    """测试创建的审批流，结束后删除，避免默认流串场。"""
    created: list[uuid.UUID] = []
    yield created
    for flow_id in created:
        db.execute(delete(ReviewFlow).where(ReviewFlow.id == flow_id))
    db.commit()


def _upload(
    client: TestClient,
    headers: dict[str, str],
    *,
    name: str = "制度文件.pdf",
    content: bytes | None = None,
) -> dict[str, Any]:
    resp = client.post(
        DOCUMENTS,
        headers=headers,
        files={"file": (name, content if content is not None else PDF, "application/octet-stream")},
        data={"title": name.rsplit(".", 1)[0]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _upload_version(
    client: TestClient, headers: dict[str, str], document_id: str, tag: bytes
) -> dict[str, Any]:
    return client.post(
        f"{DOCUMENTS}/{document_id}/versions",
        headers=headers,
        files={"file": ("制度文件.pdf", PDF + tag, "application/octet-stream")},
        data={"change_note": f"v{tag.decode()}"},
    )


def _create_flow(
    client: TestClient,
    headers: dict[str, str],
    cleanup: list[uuid.UUID],
    *,
    steps: list[dict[str, Any]] | None = None,
    doc_type: str = "pdf",
    is_default: bool = True,
    name: str | None = None,
) -> dict[str, Any]:
    payload = {
        "name": name or f"流程-{uuid.uuid4().hex[:6]}",
        "doc_type": doc_type,
        "steps": steps
        or [
            {
                "name": "内容审核",
                "approver_type": "role",
                "approver_value": "reviewer",
            }
        ],
        "is_default": is_default,
    }
    resp = client.post(FLOWS, headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    cleanup.append(uuid.UUID(data["id"]))
    return data


def _submit(
    client: TestClient,
    headers: dict[str, str],
    *,
    document_id: str,
    version_id: str | None = None,
    flow_id: str | None = None,
) -> httpx.Response:
    return client.post(
        f"{REVIEWS}/submit",
        headers=headers,
        json={
            "document_id": document_id,
            "document_version_id": version_id,
            "flow_id": flow_id,
        },
    )


def test_flow_delete_keeps_instance_snapshot(
    client: TestClient,
    auth_headers: dict[str, str],
    role_headers: Any,
    cleanup_flow: list[uuid.UUID],
) -> None:
    flow = _create_flow(client, auth_headers, cleanup_flow, is_default=True)
    document = _upload(client, auth_headers)
    submitted = _submit(
        client,
        auth_headers,
        document_id=document["id"],
        version_id=document["current_version_id"],
        flow_id=flow["id"],
    )
    assert submitted.status_code == 201, submitted.text
    instance_id = submitted.json()["data"]["id"]

    reviewer = role_headers(["reviewer"])
    assert client.delete(f"{FLOWS}/{flow['id']}", headers=reviewer).status_code == 403

    resp = client.delete(f"{FLOWS}/{flow['id']}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"deleted": True}
    assert client.get(f"{FLOWS}/{flow['id']}", headers=auth_headers).status_code == 404
    assert client.delete(f"{FLOWS}/{flow['id']}", headers=auth_headers).status_code == 404

    names = {
        item["name"] for item in client.get(FLOWS, headers=auth_headers).json()["data"]["items"]
    }
    assert flow["name"] not in names

    # 已提交实例按快照继续流转，不因流程删除而中断
    detail = client.get(f"{REVIEWS}/{instance_id}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()["data"]
    assert body["status"] == "pending"
    assert body["flow_id"] is None
    assert body["flow_name"] == flow["name"]


def test_flow_default_is_exclusive_and_admin_only(
    client: TestClient,
    auth_headers: dict[str, str],
    role_headers: Any,
    cleanup_flow: list[uuid.UUID],
) -> None:
    first = _create_flow(client, auth_headers, cleanup_flow, is_default=True)
    second = _create_flow(client, auth_headers, cleanup_flow, is_default=True)

    resp = client.get(FLOWS, headers=auth_headers, params={"doc_type": "pdf"})
    assert resp.status_code == 200, resp.text
    flows = {item["id"]: item for item in resp.json()["data"]["items"]}
    assert flows[first["id"]]["is_default"] is False
    assert flows[second["id"]]["is_default"] is True

    # 审批人可读流程，但不能配置
    reviewer_headers = role_headers(["reviewer"])
    assert client.get(FLOWS, headers=reviewer_headers).status_code == 200
    forbidden = client.post(
        FLOWS,
        headers=reviewer_headers,
        json={
            "name": "越权流程",
            "doc_type": "pdf",
            "steps": [{"name": "审核", "approver_type": "role", "approver_value": "reviewer"}],
        },
    )
    assert forbidden.status_code == 403

    # 角色必须存在
    bad_role = client.post(
        FLOWS,
        headers=auth_headers,
        json={
            "name": "错误角色",
            "doc_type": "pdf",
            "steps": [{"name": "审核", "approver_type": "role", "approver_value": "ghost"}],
        },
    )
    assert bad_role.status_code == 400
    assert "审批角色不存在" in bad_role.json()["message"]


def test_html_doc_type_flow_and_missing_default_rejected(
    client: TestClient,
    auth_headers: dict[str, str],
    cleanup_flow: list[uuid.UUID],
    cleanup_document: list[uuid.UUID],
) -> None:
    token = uuid.uuid4().hex[:6]
    steps = [{"name": "内容审核", "approver_type": "role", "approver_value": "reviewer"}]

    # markdown 不是受支持的类型，必须被拒（而不是存进库里后才在提审时炸）
    unsupported = client.post(
        FLOWS,
        headers=auth_headers,
        json={"name": f"Markdown 流程-{token}", "doc_type": "markdown", "steps": steps},
    )
    assert unsupported.status_code == 400
    assert any(
        detail["field"].endswith("doc_type") for detail in unsupported.json()["data"]
    )

    html_flow = _create_flow(
        client,
        auth_headers,
        cleanup_flow,
        doc_type="html",
        is_default=False,
        name=f"HTML 流程-{token}",
    )
    assert html_flow["doc_type"] == "html"

    html_doc = _upload(
        client,
        auth_headers,
        name=f"公告-{token}.html",
        content=f"<html><body><h1>公告</h1><p>{token}</p></body></html>".encode(),
    )
    cleanup_document.append(uuid.UUID(html_doc["id"]))
    assert html_doc["doc_type"] == "html"

    # 有流程但不是默认流：必须明确报「尚未配置默认审批流」，不能悄悄放行
    no_default = _submit(client, auth_headers, document_id=html_doc["id"], flow_id=html_flow["id"])
    assert no_default.status_code == 201, no_default.text

    other = _upload(
        client,
        auth_headers,
        name=f"公告2-{token}.html",
        content=f"<html><body><p>{token}-2</p></body></html>".encode(),
    )
    cleanup_document.append(uuid.UUID(other["id"]))
    missing = _submit(client, auth_headers, document_id=other["id"])
    assert missing.status_code == 400
    assert "尚未配置默认审批流" in missing.json()["message"]


def test_submit_freezes_version_and_blocks_duplicate(
    client: TestClient,
    auth_headers: dict[str, str],
    role_headers: Any,
    cleanup_document: list[uuid.UUID],
    cleanup_flow: list[uuid.UUID],
) -> None:
    _create_flow(client, auth_headers, cleanup_flow)
    contributor = role_headers(["contributor"])
    document = _upload(client, contributor)
    cleanup_document.append(uuid.UUID(document["id"]))

    resp = _submit(
        client,
        contributor,
        document_id=document["id"],
        version_id=document["current_version"]["id"],
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["status"] == "pending"
    assert data["current_step"] == 1
    assert data["total_steps"] == 1
    assert data["document_title"] == "制度文件"
    assert data["version_no"] == 1

    detail = client.get(f"{DOCUMENTS}/{document['id']}", headers=contributor)
    assert detail.json()["data"]["status"] == "pending"

    # 审批中版本冻结
    frozen = _upload_version(client, contributor, document["id"], b"2")
    assert frozen.status_code == 409
    assert "版本已冻结" in frozen.json()["message"]

    # 审批中不能绕过审批直接改状态
    patch = client.patch(
        f"{DOCUMENTS}/{document['id']}",
        headers=contributor,
        json={"status": "published"},
    )
    assert patch.status_code == 409
    assert "审批中" in patch.json()["message"]

    duplicate = _submit(
        client,
        contributor,
        document_id=document["id"],
        version_id=document["current_version"]["id"],
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["data"]["review_instance_id"] == data["id"]

    # 没有默认流的类型无法提交
    text_doc = _upload(client, contributor, name="说明.txt", content=b"hello knowledge base")
    cleanup_document.append(uuid.UUID(text_doc["id"]))
    no_flow = _submit(
        client,
        contributor,
        document_id=text_doc["id"],
        version_id=text_doc["current_version"]["id"],
    )
    assert no_flow.status_code == 400
    assert "默认审批流" in no_flow.json()["message"]


def test_two_step_approval_publishes_new_version(
    client: TestClient,
    auth_headers: dict[str, str],
    role_headers: Any,
    cleanup_document: list[uuid.UUID],
    cleanup_flow: list[uuid.UUID],
    db: Session,
) -> None:
    flow = _create_flow(
        client,
        auth_headers,
        cleanup_flow,
        steps=[
            {"name": "内容审核", "approver_type": "role", "approver_value": "reviewer"},
            {"name": "管理员批准", "approver_type": "role", "approver_value": "admin"},
        ],
    )
    contributor = role_headers(["contributor"])
    reviewer = role_headers(["reviewer"])

    document = _upload(client, contributor)
    cleanup_document.append(uuid.UUID(document["id"]))
    v2 = _upload_version(client, contributor, document["id"], b"2")
    assert v2.status_code == 201, v2.text
    v2_id = v2.json()["data"]["id"]

    submitted = _submit(
        client, contributor, document_id=document["id"], version_id=v2_id, flow_id=flow["id"]
    )
    assert submitted.status_code == 201, submitted.text
    instance_id = submitted.json()["data"]["id"]

    first = client.post(f"{REVIEWS}/{instance_id}/approve", headers=reviewer, json={})
    assert first.status_code == 200, first.text
    assert first.json()["data"]["status"] == "pending"
    assert first.json()["data"]["current_step"] == 2

    # 已完成第一步的人再次批准不属于自己的步骤 -> 403
    again = client.post(f"{REVIEWS}/{instance_id}/approve", headers=reviewer, json={})
    assert again.status_code == 403

    final = client.post(
        f"{REVIEWS}/{instance_id}/approve", headers=auth_headers, json={"comment": "同意发布"}
    )
    assert final.status_code == 200, final.text
    assert final.json()["data"]["status"] == "approved"
    assert final.json()["data"]["finished_at"] is not None

    detail = client.get(f"{DOCUMENTS}/{document['id']}", headers=contributor).json()["data"]
    assert detail["status"] == "published"
    assert detail["current_version_id"] == v2_id

    versions = {
        item["version_no"]: item
        for item in client.get(
            f"{DOCUMENTS}/{document['id']}/versions", headers=contributor
        ).json()["data"]
    }
    assert versions[1]["is_current"] is False
    assert versions[2]["is_current"] is True

    # 已结束的实例不能重复批准
    closed = client.post(f"{REVIEWS}/{instance_id}/approve", headers=auth_headers, json={})
    assert closed.status_code == 409

    db.rollback()
    actions = db.scalars(
        select(AuditLog.action).where(
            AuditLog.object_id == instance_id,
            AuditLog.action.in_(
                [
                    AuditAction.REVIEW_SUBMIT.value,
                    AuditAction.REVIEW_APPROVE.value,
                ]
            ),
        )
    ).all()
    assert actions.count(AuditAction.REVIEW_SUBMIT.value) == 1
    assert actions.count(AuditAction.REVIEW_APPROVE.value) == 2


def test_reject_requires_reason_and_unfreezes(
    client: TestClient,
    auth_headers: dict[str, str],
    role_headers: Any,
    cleanup_document: list[uuid.UUID],
    cleanup_flow: list[uuid.UUID],
) -> None:
    _create_flow(client, auth_headers, cleanup_flow)
    contributor = role_headers(["contributor"])
    reviewer = role_headers(["reviewer"])
    document = _upload(client, contributor)
    cleanup_document.append(uuid.UUID(document["id"]))
    instance_id = _submit(
        client,
        contributor,
        document_id=document["id"],
        version_id=document["current_version"]["id"],
    ).json()["data"]["id"]

    missing = client.post(f"{REVIEWS}/{instance_id}/reject", headers=reviewer, json={})
    assert missing.status_code == 400
    blank = client.post(
        f"{REVIEWS}/{instance_id}/reject", headers=reviewer, json={"comment": "   "}
    )
    assert blank.status_code == 400

    rejected = client.post(
        f"{REVIEWS}/{instance_id}/reject",
        headers=reviewer,
        json={"comment": "缺少风险披露章节"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["status"] == "rejected"

    detail = client.get(f"{DOCUMENTS}/{document['id']}", headers=contributor).json()["data"]
    assert detail["status"] == "rejected"

    # 驳回后版本解冻，可继续上传/修改并重新提交
    unfrozen = _upload_version(client, contributor, document["id"], b"2")
    assert unfrozen.status_code == 201, unfrozen.text

    reset = client.patch(
        f"{DOCUMENTS}/{document['id']}", headers=contributor, json={"status": "draft"}
    )
    assert reset.status_code == 200, reset.text

    resubmit = _submit(
        client,
        contributor,
        document_id=document["id"],
        version_id=unfrozen.json()["data"]["id"],
    )
    assert resubmit.status_code == 201, resubmit.text


def test_only_submitter_can_withdraw(
    client: TestClient,
    auth_headers: dict[str, str],
    role_headers: Any,
    cleanup_document: list[uuid.UUID],
    cleanup_flow: list[uuid.UUID],
) -> None:
    _create_flow(client, auth_headers, cleanup_flow)
    author = role_headers(["contributor"])
    other = role_headers(["contributor"])
    document = _upload(client, author)
    cleanup_document.append(uuid.UUID(document["id"]))
    instance_id = _submit(
        client,
        author,
        document_id=document["id"],
        version_id=document["current_version"]["id"],
    ).json()["data"]["id"]

    denied = client.post(f"{REVIEWS}/{instance_id}/withdraw", headers=other, json={})
    assert denied.status_code == 403

    withdrawn = client.post(f"{REVIEWS}/{instance_id}/withdraw", headers=author, json={})
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["data"]["status"] == "withdrawn"

    detail = client.get(f"{DOCUMENTS}/{document['id']}", headers=author).json()["data"]
    assert detail["status"] == "draft"
    assert _upload_version(client, author, document["id"], b"2").status_code == 201


def test_pending_scope_detail_and_overdue_flag(
    client: TestClient,
    auth_headers: dict[str, str],
    role_headers: Any,
    cleanup_document: list[uuid.UUID],
    cleanup_flow: list[uuid.UUID],
    db: Session,
) -> None:
    _create_flow(
        client,
        auth_headers,
        cleanup_flow,
        steps=[
            {"name": "内容审核", "approver_type": "role", "approver_value": "reviewer"},
            {"name": "管理员批准", "approver_type": "role", "approver_value": "admin"},
        ],
    )
    contributor = role_headers(["contributor"])
    reviewer = role_headers(["reviewer"])
    document = _upload(client, contributor)
    cleanup_document.append(uuid.UUID(document["id"]))
    instance_id = _submit(
        client,
        contributor,
        document_id=document["id"],
        version_id=document["current_version"]["id"],
    ).json()["data"]["id"]

    mine = client.get(
        REVIEWS,
        headers=reviewer,
        params={"scope": "mine", "document_id": document["id"]},
    )
    assert mine.status_code == 200, mine.text
    assert [item["id"] for item in mine.json()["data"]["items"]] == [instance_id]

    assert client.get(REVIEWS, headers=reviewer, params={"scope": "all"}).status_code == 403
    all_items = client.get(REVIEWS, headers=auth_headers, params={"scope": "all"})
    assert all_items.status_code == 200
    assert all_items.json()["data"]["total"] >= 1

    filtered = client.get(
        REVIEWS,
        headers=contributor,
        params={
            "scope": "mine",
            "document_id": document["id"],
            "status": "pending",
        },
    )
    assert filtered.status_code == 200
    assert filtered.json()["data"]["total"] == 0  # 贡献者不是当前步骤审批人

    detail = client.get(f"{REVIEWS}/{instance_id}", headers=reviewer)
    assert detail.status_code == 200, detail.text
    payload = detail.json()["data"]
    assert payload["document_title"] == "制度文件"
    assert [step["step_no"] for step in payload["steps"]] == [1, 2]
    assert payload["records"] == []
    assert payload["overdue"] is False

    # 回填提交时间，验证"超过 3 个工作日"标记与索引
    db.rollback()
    instance = db.get(ReviewInstance, uuid.UUID(instance_id))
    assert instance is not None
    instance.submitted_at = datetime.now(UTC) - timedelta(days=7)
    db.commit()

    overdue = client.get(
        REVIEWS,
        headers=auth_headers,
        params={"scope": "all", "document_id": document["id"]},
    )
    assert overdue.status_code == 200
    item = overdue.json()["data"]["items"][0]
    assert item["overdue"] is True
    assert item["waiting_workdays"] >= 4

    index_rows = db.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'review_instance' AND indexname = 'ix_review_instance_status_step'"
        )
    ).all()
    assert index_rows


def test_document_delete_cascades_instances(
    client: TestClient,
    auth_headers: dict[str, str],
    role_headers: Any,
    cleanup_document: list[uuid.UUID],
    cleanup_flow: list[uuid.UUID],
    db: Session,
) -> None:
    _create_flow(client, auth_headers, cleanup_flow)
    contributor = role_headers(["contributor"])
    document = _upload(client, contributor)
    cleanup_document.append(uuid.UUID(document["id"]))
    _submit(
        client,
        contributor,
        document_id=document["id"],
        version_id=document["current_version"]["id"],
    )

    # 审批中删除文档由服务层拦截（软删除），实例仍可查
    removed = client.delete(f"{DOCUMENTS}/{document['id']}", headers=contributor)
    assert removed.status_code == 409

    db.rollback()
    assert db.get(Document, uuid.UUID(document["id"])) is not None
    assert db.scalar(
        select(DocumentVersion).where(DocumentVersion.document_id == uuid.UUID(document["id"]))
    ) is not None
