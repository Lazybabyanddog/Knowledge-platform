"""文档级别名词典的集成链路：索引落库 → 详情可查 → 问答注入 → 引用不变。"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import Principal
from app.infra.db.models import AppUser, Document, DocumentVersion, ParseStrategy
from app.infra.db.models.parse_task import ParseBlock, ParseTask
from app.infra.db.models.qa import QaIssue, QaRecord
from app.infra.db.models.retrieval import DocumentChunk
from app.infra.llm import provider
from app.services import index_service
from app.services.index_service import build_version_index
from app.services.retrieval_service import RetrievalService

VECTOR = [0.01] * 1024
ALIAS_PREFIX = "本文简称："
DEFAULT_BLOCKS: tuple[tuple[str, str, int | None], ...] = (
    ("title", "民事起诉状", 1),
    (
        "paragraph",
        "原告山东鲁潍机械有限公司（以下简称鲁潍公司）诉称，双方签订的买卖合同真实有效。",
        None,
    ),
    ("paragraph", "被告鲁潍公司辩称，其已按约履行全部义务，不存在违约情形。", None),
)


@pytest.fixture(autouse=True)
def clean_qa_records(db: Session) -> Iterator[None]:
    """问答会落痕，用例结束后按新增行清理，避免污染开发库。"""
    record_ids = set(db.scalars(select(QaRecord.id)).all())
    issue_ids = set(db.scalars(select(QaIssue.id)).all())
    yield
    db.rollback()
    db.execute(delete(QaIssue).where(QaIssue.id.notin_(issue_ids)))
    db.execute(delete(QaRecord).where(QaRecord.id.notin_(record_ids)))
    db.commit()


@pytest.fixture
def fake_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider, "is_embedding_configured", lambda: True)
    monkeypatch.setattr(provider, "embed_texts", lambda texts: [list(VECTOR) for _ in texts])


@pytest.fixture
def principal(db: Session) -> Principal:
    admin = db.query(AppUser).filter(AppUser.username == settings.seed_admin_username).one()
    return Principal(user=admin, roles=["admin"], permissions=frozenset())


@pytest.fixture
def make_document(db: Session, fake_embedding: None) -> Iterator[Callable[..., SimpleNamespace]]:
    """造一篇已发布文档：独立策略 + 成功解析任务 + 可切分的解析块。"""
    admin = db.query(AppUser).filter(AppUser.username == settings.seed_admin_username).one()
    created: list[uuid.UUID] = []
    strategy_ids: list[uuid.UUID] = []

    def _make(
        title: str,
        *,
        alias: dict[str, Any] | None = None,
        blocks: tuple[tuple[str, str, int | None], ...] = DEFAULT_BLOCKS,
    ) -> SimpleNamespace:
        document = Document(
            workspace_id=admin.workspace_id,
            title=title,
            doc_type="text",
            status="published",
            sensitivity="internal",
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
            file_size=64,
            mime_type="text/plain",
            checksum=uuid.uuid4().hex,
            is_current=True,
            created_by=admin.id,
        )
        db.add(version)
        db.flush()
        document.current_version_id = version.id

        strategy = ParseStrategy(
            workspace_id=admin.workspace_id,
            strategy_key=uuid.uuid4().hex,
            version=1,
            name=f"词典策略-{uuid.uuid4().hex[:8]}",
            doc_type="text",
            engine="plain_text",
            config={"alias": alias} if alias is not None else {},
            is_default=False,
            status="active",
            created_by=admin.id,
        )
        db.add(strategy)
        db.flush()
        strategy_ids.append(strategy.id)

        task = ParseTask(
            workspace_id=admin.workspace_id,
            document_id=document.id,
            document_version_id=version.id,
            strategy_id=strategy.id,
            status="succeeded",
            created_by=admin.id,
        )
        db.add(task)
        db.flush()

        for seq, (block_type, content, level) in enumerate(blocks):
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
        return SimpleNamespace(
            document_id=document.id,
            version_id=version.id,
            task_id=task.id,
            title=title,
        )

    yield _make

    db.rollback()
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id.in_(created)))
    for document_id in created:
        document = db.get(Document, document_id)
        if document is not None:
            db.delete(document)
    db.commit()
    db.execute(delete(ParseStrategy).where(ParseStrategy.id.in_(strategy_ids)))
    db.commit()


def _index(db: Session, ctx: SimpleNamespace) -> None:
    build_version_index(db, ctx.version_id)
    db.commit()


def _glossary(db: Session, task_id: uuid.UUID) -> dict[str, Any]:
    db.rollback()
    task = db.get(ParseTask, task_id)
    assert task is not None
    return dict(task.stats or {})


def _fake_stream(chunks: list[str]) -> AsyncIterator[str]:
    async def _gen() -> AsyncIterator[str]:
        for chunk in chunks:
            yield chunk

    return _gen()


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
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
        events.append((event, json.loads("".join(data_lines))))
    return events


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider, "is_llm_configured", lambda: True)


def _ask(
    client: TestClient, headers: dict[str, str], question: str, *, top_k: int | None = None
) -> tuple[int, str]:
    payload: dict[str, Any] = {"question": question}
    if top_k is not None:
        payload["top_k"] = top_k
    with client.stream("POST", "/api/v1/retrieval/ask", json=payload, headers=headers) as resp:
        return resp.status_code, resp.read().decode()


def _pin_hits(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    principal: Principal,
    document_id: uuid.UUID,
    question: str,
) -> list[Any]:
    """用真实检索拿到带词典的命中，再钉住返回值，避免同向量下顺序不确定。"""
    hits, _vector = RetrievalService(db).search_with_vector(principal, question, top_k=50)
    mine = [hit for hit in hits if hit.document_id == str(document_id)]
    assert mine, "检索未命中目标文档，无法验证注入"
    monkeypatch.setattr(
        RetrievalService, "search_with_vector", lambda self, *args, **kwargs: (mine, VECTOR)
    )
    return mine


def test_index_persists_glossary_snapshot(
    db: Session, make_document: Callable[..., SimpleNamespace]
) -> None:
    ctx = make_document(f"词典落库-{uuid.uuid4().hex[:6]}")

    _index(db, ctx)

    stats = _glossary(db, ctx.task_id)
    snapshot = stats["glossary"]
    assert snapshot["status"] == "ok"
    assert snapshot["builtin_version"] == 1
    assert snapshot["counts"]["confirmed"] >= 1
    assert snapshot["counts"]["raw"] >= 1
    surfaces = {entry["surface"]: entry for entry in snapshot["entries"]}
    # 右侧简称由内置规则裁出，左侧全称可能带角色前缀（原告…），只断言指向正确机构
    assert surfaces["鲁潍公司"]["canonical"].endswith("山东鲁潍机械有限公司")
    assert surfaces["鲁潍公司"]["certainty"] == "CONFIRMED"

    version = db.get(DocumentVersion, ctx.version_id)
    assert version is not None
    assert version.index_status == "ready"


def test_parse_task_detail_exposes_glossary(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    make_document: Callable[..., SimpleNamespace],
) -> None:
    ctx = make_document(f"词典详情-{uuid.uuid4().hex[:6]}")
    _index(db, ctx)

    resp = client.get(f"/api/v1/parse-tasks/{ctx.task_id}", headers=auth_headers)

    assert resp.status_code == 200, resp.text
    glossary = resp.json()["data"]["glossary"]
    assert glossary["status"] == "ok"
    assert glossary["counts"]["confirmed"] >= 1
    assert len(glossary["entries"]) <= 50
    assert glossary["truncated"] is False


def test_ask_injects_glossary_and_reports_count(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    principal: Principal,
    make_document: Callable[..., SimpleNamespace],
    fake_llm: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = make_document(f"词典注入-{uuid.uuid4().hex[:6]}")
    _index(db, ctx)
    question = "被告鲁潍公司是否违约"
    _pin_hits(db, monkeypatch, principal, ctx.document_id, question)
    captured: dict[str, list[dict[str, str]]] = {}

    def _stream(messages: list[dict[str, str]]) -> AsyncIterator[str]:
        captured["messages"] = messages
        return _fake_stream(["[1]"])

    monkeypatch.setattr(provider, "astream_chat", _stream)

    status, body = _ask(client, auth_headers, question)

    assert status == 200, body
    events = _parse_sse(body)
    sources = events[0][1]["sources"]
    assert [item["title"] for item in sources] == [ctx.title]
    user = captured["messages"][1]["content"]
    lines = user.splitlines()
    # 编号与标题保持原样，对照行紧接其后，正文从下一行开始
    start = lines.index(f"[1] 《{ctx.title}》第 1 页")
    assert lines[start + 1].startswith(ALIAS_PREFIX)
    assert "鲁潍公司 = " in lines[start + 1]
    assert "山东鲁潍机械有限公司" in lines[start + 1]
    assert lines[start + 2] == "民事起诉状"
    # 引用片段不带对照行
    assert "本文简称" not in sources[0]["snippet"]
    assert events[-1][1]["glossary_lines"] >= 1


def test_glossary_failure_degrades_but_index_succeeds(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    principal: Principal,
    make_document: Callable[..., SimpleNamespace],
    fake_llm: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = make_document(f"词典降级-{uuid.uuid4().hex[:6]}")

    def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("抽取炸了")

    monkeypatch.setattr(index_service, "build_glossary", _boom)
    _index(db, ctx)

    stats = _glossary(db, ctx.task_id)
    assert stats["glossary"]["status"] == "degraded"
    assert "抽取炸了" in stats["glossary"]["error"]
    version = db.get(DocumentVersion, ctx.version_id)
    assert version is not None
    assert version.index_status == "ready"
    assert version.chunk_count > 0

    question = "被告鲁潍公司是否违约"
    _pin_hits(db, monkeypatch, principal, ctx.document_id, question)
    captured: dict[str, list[dict[str, str]]] = {}

    def _stream(messages: list[dict[str, str]]) -> AsyncIterator[str]:
        captured["messages"] = messages
        return _fake_stream(["[1]"])

    monkeypatch.setattr(provider, "astream_chat", _stream)

    status, body = _ask(client, auth_headers, question)

    assert status == 200, body
    events = _parse_sse(body)
    assert events[-1][1]["glossary_lines"] == 0
    assert "本文" not in captured["messages"][1]["content"]


def test_alias_disabled_keeps_output_unchanged(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    principal: Principal,
    make_document: Callable[..., SimpleNamespace],
    fake_llm: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = make_document(f"词典关闭-{uuid.uuid4().hex[:6]}", alias={"enabled": False})
    _index(db, ctx)

    assert "glossary" not in _glossary(db, ctx.task_id)

    question = "被告鲁潍公司是否违约"
    _pin_hits(db, monkeypatch, principal, ctx.document_id, question)
    captured: dict[str, list[dict[str, str]]] = {}

    def _stream(messages: list[dict[str, str]]) -> AsyncIterator[str]:
        captured["messages"] = messages
        return _fake_stream(["[1]"])

    monkeypatch.setattr(provider, "astream_chat", _stream)

    status, body = _ask(client, auth_headers, question)

    assert status == 200, body
    events = _parse_sse(body)
    assert events[-1][1]["glossary_lines"] == 0
    user = captured["messages"][1]["content"]
    assert "本文" not in user
    assert f"[1] 《{ctx.title}》第 1 页\n" in user


def test_rebuild_after_disabling_drops_old_glossary(
    db: Session, make_document: Callable[..., SimpleNamespace]
) -> None:
    ctx = make_document(f"词典重建-{uuid.uuid4().hex[:6]}")
    _index(db, ctx)
    assert _glossary(db, ctx.task_id)["glossary"]["status"] == "ok"

    db.rollback()
    task = db.get(ParseTask, ctx.task_id)
    assert task is not None
    strategy = db.get(ParseStrategy, task.strategy_id)
    assert strategy is not None
    strategy.config = {"alias": {"enabled": False}}
    db.commit()

    _index(db, ctx)

    assert "glossary" not in _glossary(db, ctx.task_id)
