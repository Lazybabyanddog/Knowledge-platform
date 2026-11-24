"""问答留痕、用户反馈与低置信度治理的集成测试。

向量由 mock 提供且完全相同，因此"问题是否相似"在本文件里等价于"是否同空间同密级"：
这样断言不依赖真实 embedding，也能覆盖归簇的两条路径（精确条件与向量）。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import Principal
from app.core.exceptions import NotFoundError
from app.infra.db.models import (
    AppUser,
    AuditLog,
    Document,
    DocumentVersion,
    ParseTask,
    ParseStrategy,
)
from app.infra.db.models.qa import QaIssue, QaRecord
from app.infra.db.models.retrieval import DocumentChunk
from app.infra.llm import provider
from app.services.qa_service import QaTriageService

VECTOR = [0.01] * 1024
ASK = "/api/v1/retrieval/ask"
ISSUES = "/api/v1/qa-issues"


# ---------------- 夹具 ----------------


@pytest.fixture
def fake_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider, "is_embedding_configured", lambda: True)
    monkeypatch.setattr(provider, "embed_texts", lambda texts: [list(VECTOR) for _ in texts])
    monkeypatch.setattr(provider, "is_llm_configured", lambda: True)


@pytest.fixture
def clean_qa(db: Session) -> Iterator[None]:
    """只清理本用例新增的记录与簇，避免误删开发库里已有的数据。"""
    record_ids = set(db.scalars(select(QaRecord.id)).all())
    issue_ids = set(db.scalars(select(QaIssue.id)).all())
    yield
    db.rollback()
    db.execute(delete(QaIssue).where(QaIssue.id.notin_(issue_ids)))
    db.execute(delete(QaRecord).where(QaRecord.id.notin_(record_ids)))
    db.commit()


@pytest.fixture
def make_doc(
    db: Session, fake_embedding: None, clean_qa: None
) -> Iterator[Callable[..., Document]]:
    admin = db.query(AppUser).filter(AppUser.username == settings.seed_admin_username).one()
    strategy_id = db.scalar(select(ParseStrategy.id).limit(1))
    if strategy_id is None:
        pytest.skip("没有解析策略种子，跳过问答治理测试")
    created: list[uuid.UUID] = []

    def _make(sensitivity: str = "internal") -> Document:
        title = f"问答治理-{uuid.uuid4().hex[:8]}"
        document = Document(
            workspace_id=admin.workspace_id,
            title=title,
            doc_type="text",
            status="published",
            sensitivity=sensitivity,
            created_by=admin.id,
            owner_id=admin.id,
        )
        db.add(document)
        db.flush()
        version = DocumentVersion(
            document_id=document.id,
            version_no=1,
            storage_key=f"pytest/{uuid.uuid4().hex}.txt",
            file_name="sample.txt",
            file_size=16,
            mime_type="text/plain",
            checksum=uuid.uuid4().hex,
            is_current=True,
            created_by=admin.id,
        )
        db.add(version)
        db.flush()
        document.current_version_id = version.id
        task = ParseTask(
            workspace_id=document.workspace_id,
            document_id=document.id,
            document_version_id=version.id,
            strategy_id=strategy_id,
            status="succeeded",
            created_by=admin.id,
        )
        db.add(task)
        db.flush()
        content = f"{title} 的正文片段"
        db.add(
            DocumentChunk(
                workspace_id=document.workspace_id,
                document_id=document.id,
                document_version_id=version.id,
                parse_task_id=task.id,
                seq=0,
                content=content,
                char_count=len(content),
                embedding=list(VECTOR),
                embedding_model="test-embedding",
            )
        )
        db.commit()
        created.append(document.id)
        return document

    yield _make

    for document_id in created:
        document = db.get(Document, document_id)
        if document is not None:
            db.delete(document)
    db.commit()


@pytest.fixture
def answer_with(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """让模型返回固定文本，用来控制「有没有引用编号」。"""

    def _set(text: str) -> None:
        async def _stream(messages: list[dict[str, str]]) -> AsyncIterator[str]:
            yield text

        monkeypatch.setattr(provider, "astream_chat", _stream)

    return _set


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    name = ""
    for line in body.splitlines():
        if line.startswith("event: "):
            name = line[len("event: ") :]
        elif line.startswith("data: "):
            events.append((name, json.loads(line[len("data: ") :])))
    return events


def _ask(client: TestClient, headers: dict[str, str], question: str) -> tuple[int, list]:
    with client.stream("POST", ASK, json={"question": question, "top_k": 5}, headers=headers) as resp:
        return resp.status_code, _parse_sse(resp.read().decode())


def _record_id(events: list[tuple[str, dict]]) -> str:
    done = [data for name, data in events if name == "done"]
    assert done, events
    return done[-1]["qa_record_id"]


def _issues(client: TestClient, headers: dict[str, str], **params) -> dict:
    resp = client.get(ISSUES, headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# ---------------- 留痕与反馈 ----------------


def test_dislike_enqueues_record_and_can_be_read_back(
    client: TestClient,
    auth_headers: dict[str, str],
    make_doc: Callable[..., Document],
    answer_with: Callable[[str], None],
) -> None:
    make_doc()
    answer_with("根据制度[1]，年假为 10 天。")

    status, events = _ask(client, auth_headers, "年假几天")
    assert status == 200
    record_id = _record_id(events)

    listed = _issues(client, auth_headers)
    assert listed["total"] == 0  # 有引用、没点踩，不该进队列

    resp = client.put(
        f"/api/v1/qa-records/{record_id}/feedback",
        json={"rating": "dislike", "reason": "off_topic"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["feedback"]["rating"] == "dislike"
    assert resp.json()["data"]["feedback"]["reason"] == "off_topic"

    listed = _issues(client, auth_headers)
    assert listed["total"] == 1
    item = listed["items"][0]
    assert item["representative_question"] == "年假几天"
    assert item["record_count"] == 1
    assert "user_dislike" in item["reasons"]
    assert item["status"] == "pending"

    read_back = client.get(f"/api/v1/qa-records/{record_id}/feedback", headers=auth_headers)
    assert read_back.json()["data"]["feedback"]["rating"] == "dislike"


def test_dislike_requires_reason_but_like_clears_queue(
    client: TestClient,
    auth_headers: dict[str, str],
    make_doc: Callable[..., Document],
    answer_with: Callable[[str], None],
) -> None:
    make_doc()
    answer_with("答案[1]")
    _, events = _ask(client, auth_headers, "追问测试")
    record_id = _record_id(events)

    missing = client.put(
        f"/api/v1/qa-records/{record_id}/feedback",
        json={"rating": "dislike"},
        headers=auth_headers,
    )
    assert missing.status_code == 400

    other_needs_note = client.put(
        f"/api/v1/qa-records/{record_id}/feedback",
        json={"rating": "dislike", "reason": "other"},
        headers=auth_headers,
    )
    assert other_needs_note.status_code == 400

    client.put(
        f"/api/v1/qa-records/{record_id}/feedback",
        json={"rating": "dislike", "reason": "other", "note": "回答口径不对"},
        headers=auth_headers,
    )
    assert _issues(client, auth_headers)["total"] == 1

    # 改判为认可：记录失去唯一硬信号，簇应被摘空并删除
    like = client.put(
        f"/api/v1/qa-records/{record_id}/feedback",
        json={"rating": "like"},
        headers=auth_headers,
    )
    assert like.status_code == 200
    assert like.json()["data"]["feedback"]["reason"] is None
    assert _issues(client, auth_headers)["total"] == 0


def test_cannot_rate_someone_elses_record(
    client: TestClient,
    auth_headers: dict[str, str],
    role_headers: Callable[[list[str]], dict[str, str]],
    make_doc: Callable[..., Document],
    answer_with: Callable[[str], None],
) -> None:
    make_doc()
    answer_with("答案[1]")
    _, events = _ask(client, auth_headers, "别人的问答")
    record_id = _record_id(events)

    viewer = role_headers(["viewer"])
    resp = client.put(
        f"/api/v1/qa-records/{record_id}/feedback",
        json={"rating": "like"},
        headers=viewer,
    )
    assert resp.status_code == 404


def test_answer_without_citation_enqueues_without_feedback(
    client: TestClient,
    auth_headers: dict[str, str],
    make_doc: Callable[..., Document],
    answer_with: Callable[[str], None],
) -> None:
    make_doc(sensitivity="confidential")
    answer_with("这是一段完全没有出处编号的回答。")

    _, events = _ask(client, auth_headers, "无引用问答")
    assert _record_id(events)

    listed = _issues(client, auth_headers)
    assert listed["total"] == 1
    item = listed["items"][0]
    assert "no_citation" in item["reasons"]
    assert item["sensitivity"] == "confidential"
    assert item["primary_document_id"] is not None
    assert item["primary_document_title"]


# ---------------- 归簇 ----------------


def test_similar_questions_merge_into_one_cluster(
    client: TestClient,
    auth_headers: dict[str, str],
    make_doc: Callable[..., Document],
    answer_with: Callable[[str], None],
) -> None:
    make_doc()
    make_doc()
    answer_with("没有编号的回答。")

    _ask(client, auth_headers, "年假怎么算")
    _ask(client, auth_headers, "年假是怎么计算的")

    listed = _issues(client, auth_headers)
    assert listed["total"] == 1
    assert listed["items"][0]["record_count"] == 2


def test_different_sensitivity_never_merges(
    client: TestClient,
    auth_headers: dict[str, str],
    make_doc: Callable[..., Document],
    answer_with: Callable[[str], None],
) -> None:
    answer_with("没有编号的回答。")

    # 必须先建 internal 文档并提问，再建 secret 文档：
    # 两篇文档向量相同时，第二条记录会同时命中两篇并继承最高密级，
    # 若提前建好，两条记录都会是 secret，就测不到"密级不同不合并"的分支。
    make_doc(sensitivity="internal")
    _ask(client, auth_headers, "同一问题的内部版本")

    make_doc(sensitivity="secret")
    _ask(client, auth_headers, "同一问题的机密版本")

    listed = _issues(client, auth_headers)
    assert listed["total"] == 2
    assert {item["sensitivity"] for item in listed["items"]} == {"internal", "secret"}


def test_closed_cluster_does_not_take_new_records(
    client: TestClient,
    auth_headers: dict[str, str],
    make_doc: Callable[..., Document],
    answer_with: Callable[[str], None],
) -> None:
    make_doc()
    answer_with("没有编号的回答。")
    _, events = _ask(client, auth_headers, "关闭后是否收新记录")
    assert _record_id(events)

    issue_id = _issues(client, auth_headers)["items"][0]["id"]
    closed = client.post(
        f"{ISSUES}/{issue_id}/close",
        json={"disposition": "retrieval_failure"},
        headers=auth_headers,
    )
    assert closed.status_code == 200, closed.text

    _ask(client, auth_headers, "关闭后是否收新记录（又问一次）")
    listed = _issues(client, auth_headers)
    assert listed["total"] == 2
    old = next(item for item in listed["items"] if item["id"] == issue_id)
    assert old["status"] == "closed"
    assert old["record_count"] == 1


def test_disliked_cluster_sorts_before_bigger_cluster(
    client: TestClient,
    auth_headers: dict[str, str],
    make_doc: Callable[..., Document],
    answer_with: Callable[[str], None],
) -> None:
    # 两个簇靠密级分开：internal 的有两条自动信号，confidential 的只有一条点踩
    make_doc(sensitivity="internal")
    make_doc(sensitivity="internal")
    answer_with("没有编号的回答。")
    _ask(client, auth_headers, "自动信号一")
    _ask(client, auth_headers, "自动信号二")

    make_doc(sensitivity="confidential")
    answer_with("有出处[1]，但用户不满意。")
    _, events = _ask(client, auth_headers, "用户不满意的那条")
    record_id = _record_id(events)
    client.put(
        f"/api/v1/qa-records/{record_id}/feedback",
        json={"rating": "dislike", "reason": "incomplete"},
        headers=auth_headers,
    )

    items = _issues(client, auth_headers)["items"]
    assert items[0]["dislike_count"] == 1
    assert items[0]["sensitivity"] == "confidential"
    assert items[0]["record_count"] == 1
    assert items[1]["record_count"] == 2


# ---------------- 处置 ----------------


def test_close_requires_disposition(
    client: TestClient,
    auth_headers: dict[str, str],
    make_doc: Callable[..., Document],
    answer_with: Callable[[str], None],
) -> None:
    make_doc()
    answer_with("没有编号的回答。")
    _ask(client, auth_headers, "缺少结论的关闭")
    issue_id = _issues(client, auth_headers)["items"][0]["id"]

    resp = client.post(f"{ISSUES}/{issue_id}/close", json={}, headers=auth_headers)
    assert resp.status_code == 400
    detail = client.get(f"{ISSUES}/{issue_id}", headers=auth_headers).json()["data"]
    assert detail["status"] == "pending"


def test_triage_actions_append_events_and_audit(
    client: TestClient,
    db: Session,
    auth_headers: dict[str, str],
    make_doc: Callable[..., Document],
    answer_with: Callable[[str], None],
) -> None:
    make_doc()
    answer_with("没有编号的回答。")
    _ask(client, auth_headers, "完整处置流程")
    issue_id = _issues(client, auth_headers)["items"][0]["id"]
    admin_id = client.get("/api/v1/auth/me", headers=auth_headers).json()["data"]["id"]

    assigned = client.post(
        f"{ISSUES}/{issue_id}/assign",
        json={"assignee_id": admin_id, "note": "先给知识库负责人"},
        headers=auth_headers,
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["data"]["assignee_id"] == admin_id

    disposed = client.post(
        f"{ISSUES}/{issue_id}/dispose",
        json={"disposition": "knowledge_gap", "note": "缺员工手册补充版"},
        headers=auth_headers,
    )
    assert disposed.status_code == 200
    assert disposed.json()["data"]["disposition"] == "knowledge_gap"

    closed = client.post(
        f"{ISSUES}/{issue_id}/close",
        json={"disposition": "knowledge_gap"},
        headers=auth_headers,
    )
    assert closed.status_code == 200
    assert closed.json()["data"]["status"] == "closed"

    reopened = client.post(
        f"{ISSUES}/{issue_id}/reopen",
        json={"note": "文档补完再复核"},
        headers=auth_headers,
    )
    assert reopened.status_code == 200
    detail = reopened.json()["data"]
    assert detail["status"] == "pending"
    assert [event["action"] for event in detail["events"]] == [
        "assign",
        "dispose",
        "close",
        "reopen",
    ]
    # 重开不清空此前的结论，统计时不至于丢失历史判定
    assert detail["disposition"] == "knowledge_gap"

    # audit_log.object_id 是字符串列，写入时与 issue.id 的字符串形式一致
    audit_actions = set(
        db.scalars(select(AuditLog.action).where(AuditLog.object_id == issue_id)).all()
    )
    assert {"qa.issue_assign", "qa.issue_dispose", "qa.issue_close", "qa.issue_reopen"} <= audit_actions


def test_viewer_cannot_access_queue(
    client: TestClient,
    role_headers: Callable[[list[str]], dict[str, str]],
) -> None:
    viewer = role_headers(["viewer"])
    resp = client.get(ISSUES, headers=viewer)
    assert resp.status_code == 403


def test_sensitivity_filter_hides_cluster_from_manager(
    db: Session,
    client: TestClient,
    auth_headers: dict[str, str],
    make_user: Callable[..., AppUser],
    make_doc: Callable[..., Document],
    answer_with: Callable[[str], None],
) -> None:
    make_doc(sensitivity="secret")
    answer_with("没有编号的回答。")
    _ask(client, auth_headers, "机密问题")

    visible = client.get(ISSUES, headers=auth_headers)
    assert visible.json()["data"]["total"] == 1
    issue_id = visible.json()["data"]["items"][0]["id"]

    # 有治理权但可见密级只到 internal 的管理员：列表看不到，详情按不存在处理
    limited_user = make_user(["reviewer"])
    principal = Principal(
        user=limited_user, roles=["reviewer"], permissions=frozenset({"qa:manage"})
    )
    service = QaTriageService(db)
    items, total = service.list_issues(principal)
    assert total == 0
    assert items == []
    with pytest.raises(NotFoundError):
        service.get_detail(principal, uuid.UUID(issue_id))


def test_reevaluate_marks_existing_records(
    db: Session,
    client: TestClient,
    auth_headers: dict[str, str],
    make_doc: Callable[..., Document],
    answer_with: Callable[[str], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_doc()
    answer_with("答案[1]")
    _, events = _ask(client, auth_headers, "重算标记线")
    record_id = uuid.UUID(_record_id(events))

    db.rollback()
    record = db.get(QaRecord, record_id)
    assert record is not None
    assert "low_score" not in (record.reasons or [])

    # 把标记线抬到 1.5（分数不可能超过它），重算后应打上一档低分标记
    monkeypatch.setattr(settings, "qa_score_mark_threshold", 1.5)
    changed = QaTriageService(db).reevaluate(
        record.workspace_id, ["public", "internal", "confidential", "secret"]
    )
    assert changed >= 1
    db.commit()
    db.refresh(record)
    assert "low_score" in record.reasons
    # 软信号只打标，不进队列
    assert _issues(client, auth_headers)["total"] == 0
    assert record.finished_at is not None
    assert (datetime.now(UTC) - record.started_at).total_seconds() < 600


def test_triage_disabled_keeps_record_without_queueing(
    db: Session,
    client: TestClient,
    auth_headers: dict[str, str],
    make_doc: Callable[..., Document],
    answer_with: Callable[[str], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """灰度关闭判定时只留痕：问答照常记录，但不打原因、不进队列。"""
    monkeypatch.setattr(settings, "qa_triage_enabled", False)
    make_doc()
    answer_with("没有编号的回答。")

    status, events = _ask(client, auth_headers, "灰度关闭时不判定")
    assert status == 200
    record_id = uuid.UUID(_record_id(events))
    assert _issues(client, auth_headers)["total"] == 0

    # 用户点踩同样只留痕，不参与判定与入队
    resp = client.put(
        f"/api/v1/qa-records/{record_id}/feedback",
        json={"rating": "dislike", "reason": "off_topic"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert _issues(client, auth_headers)["total"] == 0

    db.rollback()
    record = db.get(QaRecord, record_id)
    assert record is not None
    assert not record.reasons
    assert record.answer_status == "completed"
    assert record.question == "灰度关闭时不判定"
