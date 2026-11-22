"""检索接口的集成测试：直接连开发库，验证过滤口径。

向量由 mock 提供且完全相同，因此结果顺序无意义，断言只针对「哪些文档出现/不出现」。
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
from app.infra.db.models import (
    AppUser,
    AuditLog,
    Document,
    DocumentVersion,
    ParseStrategy,
    ReviewFlow,
    Workspace,
)
from app.infra.db.models.parse_task import ParseBlock, ParseTask
from app.infra.db.models.retrieval import DocumentChunk
from app.infra.llm import provider
from app.services import index_service
from app.services.index_service import build_version_index
from app.services.retrieval_service import NO_HIT_ANSWER, RetrievalService

VECTOR = [0.01] * 1024


@pytest.fixture
def fake_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider, "is_embedding_configured", lambda: True)
    monkeypatch.setattr(provider, "embed_texts", lambda texts: [list(VECTOR) for _ in texts])


@pytest.fixture
def other_workspace(db: Session) -> Iterator[Workspace]:
    token = uuid.uuid4().hex[:8]
    workspace = Workspace(name=f"pytest-ws-{token}", slug=f"pytest-ws-{token}")
    db.add(workspace)
    db.commit()
    yield workspace
    db.delete(db.get(Workspace, workspace.id))
    db.commit()


@pytest.fixture
def make_indexed_document(
    db: Session, fake_embedding: None
) -> Iterator[Callable[..., Document]]:
    """造一篇带片段与向量的文档，可按状态/密级/空间/是否当前版本控制。

    ``prebuilt_chunks=False`` 时只造解析块、不造片段，用于走真实建索引流程。
    """
    admin = db.query(AppUser).filter(AppUser.username == settings.seed_admin_username).one()
    strategy_id = db.scalar(select(ParseStrategy.id).limit(1))
    if strategy_id is None:
        pytest.skip("没有解析策略种子，跳过检索测试")

    created: list[uuid.UUID] = []

    def _make(
        title: str,
        *,
        status: str = "published",
        sensitivity: str = "internal",
        is_current: bool = True,
        deleted: bool = False,
        workspace_id: uuid.UUID | None = None,
        doc_type: str = "text",
        prebuilt_chunks: bool = True,
    ) -> Document:
        document = Document(
            workspace_id=workspace_id or admin.workspace_id,
            title=title,
            doc_type=doc_type,
            status=status,
            sensitivity=sensitivity,
            created_by=admin.id,
            owner_id=admin.id,
            deleted_at=datetime.now(UTC) if deleted else None,
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
            is_current=is_current,
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

        if prebuilt_chunks:
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
        else:
            for seq, (block_type, content, level) in enumerate(
                [
                    ("heading", "员工手册", 1),
                    ("paragraph", f"{title} 的正文段落，年假按工龄计算。", None),
                ]
            ):
                db.add(
                    ParseBlock(
                        parse_task_id=task.id,
                        document_version_id=version.id,
                        seq=seq,
                        block_type=block_type,
                        content=content,
                        page_no=1,
                        level=level,
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


def _titles(response: dict) -> set[str]:
    return {item["title"] for item in response["items"]}


def test_search_only_returns_published_current_in_workspace(
    client: TestClient,
    auth_headers: dict[str, str],
    make_indexed_document: Callable[..., Document],
    other_workspace: Workspace,
) -> None:
    token = uuid.uuid4().hex[:6]
    published = make_indexed_document(f"可见文档-{token}")
    make_indexed_document(f"草稿-{token}", status="draft")
    make_indexed_document(f"已下架-{token}", status="archived")
    make_indexed_document(f"已删除-{token}", deleted=True)
    make_indexed_document(f"旧版本-{token}", is_current=False)
    make_indexed_document(f"别的空间-{token}", workspace_id=other_workspace.id)

    resp = client.post(
        "/api/v1/retrieval/search",
        json={"query": "正文片段", "top_k": 20},
        headers=auth_headers,
    )

    assert resp.status_code == 200, resp.text
    titles = _titles(resp.json()["data"])
    assert published.title in titles
    assert f"草稿-{token}" not in titles
    assert f"已下架-{token}" not in titles
    assert f"已删除-{token}" not in titles
    assert f"旧版本-{token}" not in titles
    assert f"别的空间-{token}" not in titles


def test_search_enforces_sensitivity_for_non_admin(
    client: TestClient,
    auth_headers: dict[str, str],
    role_headers: Callable[[list[str]], dict[str, str]],
    make_indexed_document: Callable[..., Document],
) -> None:
    token = uuid.uuid4().hex[:6]
    secret = make_indexed_document(f"机密-{token}", sensitivity="confidential")
    public = make_indexed_document(f"公开-{token}", sensitivity="public")

    admin_titles = _titles(
        client.post(
            "/api/v1/retrieval/search",
            json={"query": "正文片段", "top_k": 20},
            headers=auth_headers,
        ).json()["data"]
    )
    user_titles = _titles(
        client.post(
            "/api/v1/retrieval/search",
            json={"query": "正文片段", "top_k": 20},
            headers=role_headers(["viewer"]),
        ).json()["data"]
    )

    assert secret.title in admin_titles
    assert secret.title not in user_titles
    assert public.title in user_titles


def test_search_returns_503_when_embedding_missing(
    client: TestClient,
    auth_headers: dict[str, str],
    make_indexed_document: Callable[..., Document],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_indexed_document(f"任意文档-{uuid.uuid4().hex[:6]}")
    monkeypatch.setattr(provider, "is_embedding_configured", lambda: False)

    resp = client.post(
        "/api/v1/retrieval/search", json={"query": "正文片段"}, headers=auth_headers
    )

    assert resp.status_code == 503
    assert resp.json()["code"] == 50301


# --- 流式问答 ---------------------------------------------------------------


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider, "is_llm_configured", lambda: True)


def _fake_stream(chunks: list[str]) -> AsyncIterator[str]:
    """按给定增量产出的假流；``astream_chat`` 是同步函数返回异步迭代器。"""

    async def _gen() -> AsyncIterator[str]:
        for chunk in chunks:
            yield chunk

    return _gen()


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    """把 SSE 文本拆成 (event, data) 列表，顺带校验每帧 data 是单行 JSON。"""
    events: list[tuple[str, dict]] = []
    for frame in body.split("\n\n"):
        if not frame.strip():
            continue
        event = ""
        data_lines: list[str] = []
        for line in frame.splitlines():
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
        assert event, f"缺少 event 字段: {frame!r}"
        events.append((event, json.loads("".join(data_lines))))
    return events


def _ask(
    client: TestClient,
    headers: dict[str, str],
    question: str,
    *,
    request_id: str | None = None,
    top_k: int | None = None,
) -> tuple[int, str]:
    extra = {"X-Request-Id": request_id} if request_id else {}
    payload: dict[str, object] = {"question": question}
    if top_k is not None:
        payload["top_k"] = top_k
    with client.stream(
        "POST",
        "/api/v1/retrieval/ask",
        json=payload,
        headers={**headers, **extra},
    ) as resp:
        return resp.status_code, resp.read().decode()


def test_ask_streams_sources_then_deltas_then_done(
    client: TestClient,
    auth_headers: dict[str, str],
    make_indexed_document: Callable[..., Document],
    fake_llm: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = uuid.uuid4().hex[:6]
    document = make_indexed_document(f"问答命中-{token}")
    captured: dict[str, list[dict[str, str]]] = {}

    def _stream(messages: list[dict[str, str]]) -> AsyncIterator[str]:
        captured["messages"] = messages
        return _fake_stream(["根据", "资料", "[1]"])

    monkeypatch.setattr(provider, "astream_chat", _stream)

    status, body = _ask(client, auth_headers, f"正文片段是什么-{token}")

    assert status == 200, body
    events = _parse_sse(body)
    assert [name for name, _ in events] == ["sources", "delta", "delta", "delta", "done"]

    sources = events[0][1]["sources"]
    assert [item["title"] for item in sources] == [document.title]
    assert sources[0]["index"] == 1

    assert "".join(data["text"] for name, data in events[1:4] if name == "delta") == "根据资料[1]"
    assert events[-1][1]["sources"] == 1

    # 提示词必须带编号资料与「只依据资料」的约束，编号与 sources 顺序一致
    system, user = captured["messages"][0]["content"], captured["messages"][1]["content"]
    assert "只能依据" in system
    assert "[1]" in user and document.title in user


def test_ask_without_hits_skips_model(
    client: TestClient,
    auth_headers: dict[str, str],
    make_indexed_document: Callable[..., Document],
    fake_llm: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_indexed_document(f"无关文档-{uuid.uuid4().hex[:6]}")
    monkeypatch.setattr(RetrievalService, "search", lambda self, *args, **kwargs: [])

    def _boom(messages: list[dict[str, str]]) -> AsyncIterator[str]:
        raise AssertionError("零命中时不应调用模型")

    monkeypatch.setattr(provider, "astream_chat", _boom)

    status, body = _ask(client, auth_headers, "库里不存在的问题")

    assert status == 200, body
    events = _parse_sse(body)
    assert [name for name, _ in events] == ["sources", "delta", "done"]
    assert events[0][1]["sources"] == []
    assert events[1][1]["text"] == NO_HIT_ANSWER
    assert events[2][1]["sources"] == 0


def test_ask_returns_503_when_llm_missing(
    client: TestClient,
    auth_headers: dict[str, str],
    make_indexed_document: Callable[..., Document],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_indexed_document(f"任意文档-{uuid.uuid4().hex[:6]}")
    monkeypatch.setattr(provider, "is_llm_configured", lambda: False)

    status, body = _ask(client, auth_headers, "正文片段")

    assert status == 503
    assert json.loads(body)["code"] == 50301


def test_ask_emits_error_event_when_upstream_fails(
    client: TestClient,
    auth_headers: dict[str, str],
    make_indexed_document: Callable[..., Document],
    fake_llm: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_indexed_document(f"上游失败-{uuid.uuid4().hex[:6]}")

    async def _stream(messages: list[dict[str, str]]) -> AsyncIterator[str]:
        yield "部分"
        raise provider.LLMError("上游连接中断")

    monkeypatch.setattr(provider, "astream_chat", _stream)

    status, body = _ask(client, auth_headers, "正文片段")

    assert status == 200, body
    events = _parse_sse(body)
    assert [name for name, _ in events] == ["sources", "delta", "error"]
    assert events[-1][1]["code"] == "LLM_CALL_FAILED"
    assert "上游连接中断" in events[-1][1]["message"]


def test_ask_audits_question_summary_without_answer(
    client: TestClient,
    db: Session,
    auth_headers: dict[str, str],
    make_indexed_document: Callable[..., Document],
    fake_llm: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_indexed_document(f"审计文档-{uuid.uuid4().hex[:6]}")
    monkeypatch.setattr(provider, "astream_chat", _fake_stream(["不得入库的答案"]))
    request_id = f"audit-ask-{uuid.uuid4().hex[:8]}"
    question = "审计问题：" + "长" * 120

    status, body = _ask(client, auth_headers, question, request_id=request_id)

    assert status == 200, body
    db.rollback()
    row = db.scalars(
        select(AuditLog).where(
            AuditLog.request_id == request_id, AuditLog.action == "retrieval.ask"
        )
    ).one()
    assert row.object_type == "retrieval"
    assert row.after is not None
    assert row.after["question"] == question[:100]
    assert row.after["hits"] >= 1
    assert "不得入库的答案" not in json.dumps(row.after, ensure_ascii=False)


def test_html_document_publish_index_and_ask(
    client: TestClient,
    db: Session,
    auth_headers: dict[str, str],
    role_headers: Callable[[list[str]], dict[str, str]],
    make_indexed_document: Callable[..., Document],
    fake_llm: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTML 文档全链路：配置默认审批流 → 提审 → 批准 → 建索引 → 问答引用页码。"""
    token = uuid.uuid4().hex[:6]
    # 发布时会投递 Celery 任务，测试里换成就地 no-op，随后手动建索引
    monkeypatch.setattr(index_service, "_index_dispatcher", lambda version_id: None)

    flow_resp = client.post(
        "/api/v1/review-flows",
        headers=auth_headers,
        json={
            "name": f"HTML 审批流-{token}",
            "doc_type": "html",
            "steps": [{"name": "内容审核", "approver_type": "role", "approver_value": "reviewer"}],
            "is_default": True,
        },
    )
    assert flow_resp.status_code == 201, flow_resp.text
    flow_id = uuid.UUID(flow_resp.json()["data"]["id"])

    try:
        document = make_indexed_document(
            f"HTML 制度-{token}",
            status="draft",
            doc_type="html",
            prebuilt_chunks=False,
        )

        submitted = client.post(
            "/api/v1/reviews/submit",
            headers=auth_headers,
            json={"document_id": str(document.id)},
        )
        assert submitted.status_code == 201, submitted.text
        instance_id = submitted.json()["data"]["id"]

        approved = client.post(
            f"/api/v1/reviews/{instance_id}/approve",
            headers=role_headers(["reviewer"]),
            json={"comment": "同意发布"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["data"]["status"] == "approved"

        published = client.get(f"/api/v1/documents/{document.id}", headers=auth_headers)
        assert published.json()["data"]["status"] == "published"

        db.rollback()
        version_id = db.scalar(
            select(DocumentVersion.id).where(
                DocumentVersion.document_id == document.id,
                DocumentVersion.is_current.is_(True),
            )
        )
        assert version_id is not None
        build_version_index(db, version_id)
        db.commit()

        db.rollback()
        version = db.get(DocumentVersion, version_id)
        assert version is not None
        assert version.index_status == "ready"
        assert version.chunk_count > 0

        monkeypatch.setattr(provider, "astream_chat", lambda messages: _fake_stream(["[1]"]))
        status, body = _ask(client, auth_headers, f"年假怎么算-{token}", top_k=20)

        assert status == 200, body
        sources = _parse_sse(body)[0][1]["sources"]
        matched = [item for item in sources if item["title"] == document.title]
        assert matched, f"问答未引用 HTML 文档，sources={sources}"
        assert matched[0]["page_no"] == 1
    finally:
        db.rollback()
        db.execute(delete(ReviewFlow).where(ReviewFlow.id == flow_id))
        db.commit()
