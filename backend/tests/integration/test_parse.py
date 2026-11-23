"""解析流水线集成测试（任务 4.1–4.9）。

投递策略：默认把 Celery 投递替换成"同进程同步执行"，
既能覆盖真实执行路径，又不用起 Redis worker。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.infra.db.models import (
    AppUser,
    AuditLog,
    DocumentVersion,
    ParseBlock,
    ParseStrategy,
    ParseTask,
    ParseTaskAttempt,
)
from app.infra.db.repositories import ParseStrategyRepository
from app.infra.db.session import SessionLocal
from app.services.parse_task_service import ParseTaskService, set_dispatcher
from tests.helpers.pdf_factory import make_text_pdf

UPLOAD = "/api/v1/documents"
STRATEGIES = "/api/v1/parse-strategies"
TASKS = "/api/v1/parse-tasks"
HTML_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "html" / "court_gazette.html"

TEXT = (
    "第一章 总则\n\n"
    "第一条 为规范知识库的解析流程，特制定本办法。\n\n"
    "1. 所有上传文档统一走解析流水线；\n"
    "2. 解析产物按块存储并保留页码。\n\n"
    "第二章 责任\n\n"
    "第二条 各部门负责本部门文档内容的准确性。\n"
)
OLE_DOC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64


def _sync_execute(task_id: str) -> None:
    session = SessionLocal()
    try:
        ParseTaskService(session).execute(uuid.UUID(task_id))
    finally:
        session.close()


@pytest.fixture(autouse=True)
def sync_parse_dispatch() -> Iterator[None]:
    set_dispatcher(_sync_execute)
    yield
    set_dispatcher(None)


@pytest.fixture
def parse_cleanup() -> Iterator[dict[str, list[str]]]:
    """登记本次用到的策略 key 与任务 id，结束后按"任务 → 策略"顺序清理。

    创建默认策略会把种子策略的默认标记顶掉，因此先快照再还原，
    保证测试不会改变开发库的默认策略。
    """
    state: dict[str, list[str]] = {"strategy_keys": [], "task_ids": [], "default_ids": []}
    session = SessionLocal()
    try:
        state["default_ids"] = [
            str(item)
            for item in session.scalars(
                select(ParseStrategy.id).where(ParseStrategy.is_default.is_(True))
            )
        ]
    finally:
        session.close()

    yield state

    session = SessionLocal()
    try:
        if state["task_ids"]:
            session.execute(
                delete(ParseTask).where(
                    ParseTask.id.in_([uuid.UUID(item) for item in state["task_ids"]])
                )
            )
        if state["strategy_keys"]:
            session.execute(
                delete(ParseStrategy).where(
                    ParseStrategy.strategy_key.in_(state["strategy_keys"])
                )
            )
        session.flush()
        repo = ParseStrategyRepository(session)
        for raw_id in state["default_ids"]:
            row = session.get(ParseStrategy, uuid.UUID(raw_id))
            if row is None:
                continue
            repo.clear_default(row.workspace_id, row.doc_type)
            row.is_default = True
        session.commit()
    finally:
        session.close()


def _create_strategy(
    client: TestClient,
    headers: dict[str, str],
    cleanup: dict[str, list[str]],
    *,
    doc_type: str = "text",
    engine: str = "plain_text",
    is_default: bool = True,
    config: dict[str, Any] | None = None,
    name: str = "集成测试策略",
) -> dict[str, Any]:
    resp = client.post(
        STRATEGIES,
        headers=headers,
        json={
            "name": name,
            "doc_type": doc_type,
            "engine": engine,
            "config": config or {},
            "is_default": is_default,
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    cleanup["strategy_keys"].append(data["strategy_key"])
    return data


def _upload(
    client: TestClient,
    headers: dict[str, str],
    cleanup_document: list[uuid.UUID],
    *,
    name: str = "制度说明.txt",
    content: bytes | None = None,
    mime: str = "text/plain",
) -> dict[str, Any]:
    resp = client.post(
        UPLOAD,
        headers=headers,
        files={"file": (name, content if content is not None else TEXT.encode(), mime)},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    cleanup_document.append(uuid.UUID(data["id"]))
    return data


def _trigger(
    client: TestClient,
    headers: dict[str, str],
    document: dict[str, Any],
    cleanup: dict[str, list[str]],
    strategy_id: str | None = None,
) -> tuple[int, dict[str, Any]]:
    payload: dict[str, Any] = {"document_version_id": document["current_version_id"]}
    if strategy_id:
        payload["strategy_id"] = strategy_id
    resp = client.post(TASKS, headers=headers, json=payload)
    if resp.status_code == 202:
        cleanup["task_ids"].append(resp.json()["data"]["id"])
    return resp.status_code, resp.json()


def _get_task(client: TestClient, headers: dict[str, str], task_id: str) -> dict[str, Any]:
    resp = client.get(f"{TASKS}/{task_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_strategy_default_unique_and_versioning(
    client: TestClient,
    auth_headers: dict[str, str],
    role_headers: Callable[[list[str]], dict[str, str]],
    parse_cleanup: dict[str, list[str]],
) -> None:
    first = _create_strategy(client, auth_headers, parse_cleanup, name="纯文本 A")
    second = _create_strategy(client, auth_headers, parse_cleanup, name="纯文本 B")
    assert first["version"] == 1 and first["is_default"] is True

    listed = client.get(
        STRATEGIES, headers=auth_headers, params={"doc_type": "text"}
    ).json()["data"]["items"]
    by_id = {item["id"]: item for item in listed}
    assert by_id[second["id"]]["is_default"] is True
    assert by_id[first["id"]]["is_default"] is False

    patched = client.patch(
        f"{STRATEGIES}/{first['id']}",
        headers=auth_headers,
        json={"config": {"chunk_hint": 1200}, "is_default": True},
    )
    assert patched.status_code == 200, patched.text
    v2 = patched.json()["data"]
    assert (v2["version"], v2["is_default"]) == (2, True)
    assert v2["config"]["chunk_hint"] == 1200
    assert v2["strategy_key"] == first["strategy_key"]
    assert v2["name"] == "纯文本 A"

    # 旧版本内容不被改写，只让出默认标记
    db_session = SessionLocal()
    try:
        rows = list(
            db_session.scalars(
                select(ParseStrategy)
                .where(ParseStrategy.strategy_key == first["strategy_key"])
                .order_by(ParseStrategy.version)
            )
        )
        assert [(row.version, row.is_default) for row in rows] == [(1, False), (2, True)]
        assert rows[0].config == {}
    finally:
        db_session.close()

    # 已存在的版本行再次修改，仍以最新版为基线升版本
    again = client.patch(
        f"{STRATEGIES}/{first['id']}",
        headers=auth_headers,
        json={"name": "纯文本 A2"},
    )
    assert again.status_code == 200
    v3 = again.json()["data"]
    assert (v3["version"], v3["is_default"], v3["name"]) == (3, True, "纯文本 A2")
    assert v3["config"]["chunk_hint"] == 1200

    assert (
        client.post(
            STRATEGIES,
            headers=auth_headers,
            json={
                "name": "坏引擎",
                "doc_type": "text",
                "engine": "not_exists",
                "config": {},
                "is_default": False,
            },
        ).status_code
        == 400
    )
    contributor = role_headers(["contributor"])
    assert (
        client.post(
            STRATEGIES,
            headers=contributor,
            json={
                "name": "越权",
                "doc_type": "text",
                "engine": "plain_text",
                "config": {},
                "is_default": False,
            },
        ).status_code
        == 403
    )


def test_missing_default_strategy_blocks_trigger(
    client: TestClient,
    auth_headers: dict[str, str],
    cleanup_document: list[uuid.UUID],
    parse_cleanup: dict[str, list[str]],
) -> None:
    strategy = _create_strategy(client, auth_headers, parse_cleanup, name="临时默认")
    document = _upload(client, auth_headers, cleanup_document)

    resp = client.patch(
        f"{STRATEGIES}/{strategy['id']}", headers=auth_headers, json={"is_default": False}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["is_default"] is False

    status_code, body = _trigger(client, auth_headers, document, parse_cleanup)
    assert status_code == 400
    assert "未配置默认解析策略" in body["message"]


def test_trigger_then_execute_produces_blocks(
    client: TestClient,
    auth_headers: dict[str, str],
    cleanup_document: list[uuid.UUID],
    parse_cleanup: dict[str, list[str]],
) -> None:
    set_dispatcher(lambda _: None)  # 先停在 pending，模拟排队
    strategy = _create_strategy(client, auth_headers, parse_cleanup)
    document = _upload(client, auth_headers, cleanup_document)

    status_code, body = _trigger(
        client, auth_headers, document, parse_cleanup, strategy["id"]
    )
    assert status_code == 202
    task = body["data"]
    assert task["status"] == "pending"
    assert task["strategy_id"] == strategy["id"]

    duplicated = client.post(
        TASKS,
        headers=auth_headers,
        json={"document_version_id": document["current_version_id"]},
    )
    assert duplicated.status_code == 409
    assert duplicated.json()["data"]["task_id"] == task["id"]

    set_dispatcher(_sync_execute)
    session = SessionLocal()
    try:
        ParseTaskService(session).execute(uuid.UUID(task["id"]))
    finally:
        session.close()

    detail = _get_task(client, auth_headers, task["id"])
    assert detail["status"] == "succeeded"
    assert detail["attempt"] == 1
    assert detail["quality_score"] >= 60
    assert detail["low_quality"] is False
    assert detail["block_count"] > 0
    assert [item["attempt_no"] for item in detail["attempts"]] == [1]
    assert detail["attempts"][0]["status"] == "succeeded"
    assert detail["stats"]["engine"] == "plain_text"

    blocks = client.get(f"{TASKS}/{task['id']}/blocks", headers=auth_headers).json()["data"]
    assert blocks["total"] == detail["block_count"]
    assert [item["seq"] for item in blocks["items"]] == list(range(len(blocks["items"])))
    title = next(item for item in blocks["items"] if item["block_type"] == "title")
    assert title["content"] == "第一章 总则"
    child = next(
        item for item in blocks["items"] if item["block_type"] == "paragraph"
    )
    assert child["parent_block_id"] == title["id"]
    assert any(item["block_type"] == "list" for item in blocks["items"])

    # 策略修改生成新版本，但历史任务仍指向执行时的旧版本行
    patched = client.patch(
        f"{STRATEGIES}/{strategy['id']}",
        headers=auth_headers,
        json={"config": {"chunk_hint": 900}},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["version"] == 2
    assert _get_task(client, auth_headers, task["id"])["strategy_id"] == task["strategy_id"]

    # 重复投递 / 重复执行都只保留一份产物
    session = SessionLocal()
    try:
        ParseTaskService(session).execute(uuid.UUID(task["id"]))
    finally:
        session.close()
    again = _get_task(client, auth_headers, task["id"])
    assert again["attempt"] == 1
    assert again["block_count"] == detail["block_count"]


def test_failed_task_retry_limit_keeps_attempt_history(
    client: TestClient,
    auth_headers: dict[str, str],
    cleanup_document: list[uuid.UUID],
    parse_cleanup: dict[str, list[str]],
) -> None:
    strategy = _create_strategy(
        client,
        auth_headers,
        parse_cleanup,
        doc_type="office",
        engine="office",
        name="Office 解析",
    )
    document = _upload(
        client,
        auth_headers,
        cleanup_document,
        name="旧版合同.doc",
        content=OLE_DOC,
        mime="application/msword",
    )

    status_code, body = _trigger(
        client, auth_headers, document, parse_cleanup, strategy["id"]
    )
    assert status_code == 202
    task_id = body["data"]["id"]

    failed = _get_task(client, auth_headers, task_id)
    assert failed["status"] == "failed"
    assert failed["error_code"] == "parse_error"
    assert failed["error"]["message"] == "解析失败"
    assert "老格式" in failed["error_message"]
    # 解析失败属于不可重试类：不自动重排，直接等人工决定
    assert failed["auto_retries"] == 0
    assert failed["actions"]["can_revive"] is True
    assert failed["actions"]["can_skip_failed_batches"] is False

    for expected_attempt, expected_revives in ((2, 1), (3, 2)):
        retried = client.post(f"{TASKS}/{task_id}/retry", headers=auth_headers)
        assert retried.status_code == 200, retried.text
        detail = _get_task(client, auth_headers, task_id)
        assert detail["attempt"] == expected_attempt
        assert detail["manual_revives"] == expected_revives

    exhausted = client.post(f"{TASKS}/{task_id}/retry", headers=auth_headers)
    assert exhausted.status_code == 409
    assert exhausted.json()["data"]["attempt"] == 3
    assert exhausted.json()["data"]["manual_revives"] == 2

    detail = _get_task(client, auth_headers, task_id)
    # 展示按时间倒序：最近的执行在最前面
    assert [item["attempt_no"] for item in detail["attempts"]] == [3, 2, 1]
    assert all(item["status"] == "failed" for item in detail["attempts"])
    assert all(item["error_code"] == "parse_error" for item in detail["attempts"])
    assert [item["source"] for item in detail["attempts"]] == [
        "manual_revive",
        "manual_revive",
        "manual",
    ]
    assert detail["actions"]["can_revive"] is False

    db_session = SessionLocal()
    try:
        count = len(
            list(
                db_session.scalars(
                    select(ParseTaskAttempt).where(
                        ParseTaskAttempt.parse_task_id == uuid.UUID(task_id)
                    )
                )
            )
        )
        assert count == 3
    finally:
        db_session.close()


def test_cancel_pending_task_and_illegal_transitions(
    client: TestClient,
    auth_headers: dict[str, str],
    cleanup_document: list[uuid.UUID],
    parse_cleanup: dict[str, list[str]],
) -> None:
    set_dispatcher(lambda _: None)
    strategy = _create_strategy(client, auth_headers, parse_cleanup)
    document = _upload(client, auth_headers, cleanup_document)
    _, body = _trigger(client, auth_headers, document, parse_cleanup, strategy["id"])
    task_id = body["data"]["id"]

    cancelled = client.post(f"{TASKS}/{task_id}/cancel", headers=auth_headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["status"] == "cancelled"

    # 状态机允许同态重复（幂等）
    again = client.post(f"{TASKS}/{task_id}/cancel", headers=auth_headers)
    assert again.status_code == 200
    assert again.json()["data"]["status"] == "cancelled"

    # 取消是"可人工恢复的终态"：继续执行回到 pending，消耗人工预算而非自动预算
    revived = client.post(f"{TASKS}/{task_id}/retry", headers=auth_headers)
    assert revived.status_code == 200, revived.text
    assert revived.json()["data"]["status"] == "pending"
    assert revived.json()["data"]["manual_revives"] == 1
    assert revived.json()["data"]["auto_retries"] == 0

    # 再取消一次，已取消的任务被投递到 worker 时直接跳过，不产生 attempt
    client.post(f"{TASKS}/{task_id}/cancel", headers=auth_headers)
    session = SessionLocal()
    try:
        ParseTaskService(session).execute(uuid.UUID(task_id))
    finally:
        session.close()
    detail = _get_task(client, auth_headers, task_id)
    assert detail["status"] == "cancelled"
    assert detail["attempt"] == 0
    assert detail["attempts"] == []


def test_task_list_filters_and_pagination(
    client: TestClient,
    auth_headers: dict[str, str],
    cleanup_document: list[uuid.UUID],
    parse_cleanup: dict[str, list[str]],
) -> None:
    text_strategy = _create_strategy(client, auth_headers, parse_cleanup)
    pdf_strategy = _create_strategy(
        client,
        auth_headers,
        parse_cleanup,
        doc_type="pdf",
        engine="pdf_layout",
        name="PDF 版面",
    )
    text_doc = _upload(client, auth_headers, cleanup_document)
    pdf_doc = _upload(
        client,
        auth_headers,
        cleanup_document,
        name="年报.pdf",
        content=make_text_pdf(),
        mime="application/pdf",
    )
    _trigger(client, auth_headers, text_doc, parse_cleanup, text_strategy["id"])
    _trigger(client, auth_headers, pdf_doc, parse_cleanup, pdf_strategy["id"])

    by_document = client.get(
        TASKS, headers=auth_headers, params={"document_id": text_doc["id"]}
    ).json()["data"]
    assert by_document["total"] == 1
    assert by_document["items"][0]["document_id"] == text_doc["id"]

    succeeded = client.get(
        TASKS, headers=auth_headers, params={"status": "succeeded", "page_size": 1}
    ).json()["data"]
    assert succeeded["total"] >= 2
    assert len(succeeded["items"]) == 1

    paged = client.get(
        TASKS, headers=auth_headers, params={"page": 2, "page_size": 1}
    ).json()["data"]
    assert paged["page"] == 2
    assert len(paged["items"]) == 1
    assert paged["items"][0]["id"] != succeeded["items"][0]["id"]

    empty = client.get(
        TASKS, headers=auth_headers, params={"document_id": str(uuid.uuid4())}
    ).json()["data"]
    assert empty == {"items": [], "total": 0, "page": 1, "page_size": 20}


def test_blocks_pagination_and_discarded_filter(
    client: TestClient,
    auth_headers: dict[str, str],
    cleanup_document: list[uuid.UUID],
    parse_cleanup: dict[str, list[str]],
) -> None:
    strategy = _create_strategy(client, auth_headers, parse_cleanup)
    document = _upload(client, auth_headers, cleanup_document)
    _, body = _trigger(client, auth_headers, document, parse_cleanup, strategy["id"])
    task_id = body["data"]["id"]
    detail = _get_task(client, auth_headers, task_id)
    total = detail["block_count"]

    session = SessionLocal()
    try:
        session.add(
            ParseBlock(
                parse_task_id=uuid.UUID(task_id),
                document_version_id=uuid.UUID(document["current_version_id"]),
                seq=total,
                block_type="footer",
                content="内部资料 请勿外传",
                is_discarded=True,
            )
        )
        session.commit()
    finally:
        session.close()

    visible = client.get(f"{TASKS}/{task_id}/blocks", headers=auth_headers).json()["data"]
    assert visible["total"] == total
    assert all(item["is_discarded"] is False for item in visible["items"])

    full = client.get(
        f"{TASKS}/{task_id}/blocks",
        headers=auth_headers,
        params={"include_discarded": True},
    ).json()["data"]
    assert full["total"] == total + 1
    assert full["items"][-1]["is_discarded"] is True
    assert full["items"][-1]["content"] == "内部资料 请勿外传"

    paged = client.get(
        f"{TASKS}/{task_id}/blocks",
        headers=auth_headers,
        params={"page": 1, "page_size": 2},
    ).json()["data"]
    assert paged["page_size"] == 2
    assert len(paged["items"]) == 2
    assert [item["seq"] for item in paged["items"]] == [0, 1]


def test_parse_requires_read_permission_and_own_task(
    client: TestClient,
    auth_headers: dict[str, str],
    cleanup_document: list[uuid.UUID],
    parse_cleanup: dict[str, list[str]],
) -> None:
    strategy = _create_strategy(client, auth_headers, parse_cleanup)
    document = _upload(client, auth_headers, cleanup_document)
    _, body = _trigger(client, auth_headers, document, parse_cleanup, strategy["id"])
    task_id = body["data"]["id"]

    assert client.get(f"{TASKS}/{task_id}").status_code == 401
    assert client.get(f"{TASKS}/{str(uuid.uuid4())}", headers=auth_headers).status_code == 404


def test_task_query_rejects_bad_document_id(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.get(TASKS, headers=auth_headers, params={"document_id": "not-a-uuid"})

    assert resp.status_code == 400
    assert "UUID" in resp.json()["message"]


def test_delete_task_removes_products_and_resets_index(
    client: TestClient,
    db: Session,
    auth_headers: dict[str, str],
    cleanup_document: list[uuid.UUID],
    parse_cleanup: dict[str, list[str]],
) -> None:
    strategy = _create_strategy(client, auth_headers, parse_cleanup)
    document = _upload(client, auth_headers, cleanup_document)
    _, body = _trigger(client, auth_headers, document, parse_cleanup, strategy["id"])
    task_id = uuid.UUID(body["data"]["id"])
    detail = _get_task(client, auth_headers, str(task_id))
    assert detail["status"] == "succeeded"
    assert detail["block_count"] > 0

    resp = client.delete(f"{TASKS}/{task_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"deleted": True}

    assert client.get(f"{TASKS}/{task_id}", headers=auth_headers).status_code == 404
    assert client.delete(f"{TASKS}/{task_id}", headers=auth_headers).status_code == 404

    db.rollback()
    assert db.get(ParseTask, task_id) is None
    assert (
        db.scalar(
            select(func.count())
            .select_from(ParseBlock)
            .where(ParseBlock.parse_task_id == task_id)
        )
        == 0
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(ParseTaskAttempt)
            .where(ParseTaskAttempt.parse_task_id == task_id)
        )
        == 0
    )
    # 产物已删，版本索引失效
    version = db.get(DocumentVersion, uuid.UUID(document["current_version_id"]))
    assert version is not None
    assert version.index_status == "pending"
    assert version.chunk_count == 0

    audit = db.scalars(
        select(AuditLog).where(
            AuditLog.action == "parse.task_delete", AuditLog.object_id == str(task_id)
        )
    ).one()
    assert audit.before["status"] == "succeeded"


def test_delete_running_task_requires_cancel_first(
    client: TestClient,
    auth_headers: dict[str, str],
    cleanup_document: list[uuid.UUID],
    parse_cleanup: dict[str, list[str]],
) -> None:
    set_dispatcher(lambda _: None)
    strategy = _create_strategy(client, auth_headers, parse_cleanup)
    document = _upload(client, auth_headers, cleanup_document)
    _, body = _trigger(client, auth_headers, document, parse_cleanup, strategy["id"])
    task_id = body["data"]["id"]

    blocked = client.delete(f"{TASKS}/{task_id}", headers=auth_headers)
    assert blocked.status_code == 409
    assert "进行中" in blocked.json()["message"]

    client.post(f"{TASKS}/{task_id}/cancel", headers=auth_headers)
    allowed = client.delete(f"{TASKS}/{task_id}", headers=auth_headers)
    assert allowed.status_code == 200, allowed.text


def test_strategy_delete_disables_all_versions(
    client: TestClient,
    db: Session,
    auth_headers: dict[str, str],
    role_headers: Callable[[list[str]], dict[str, str]],
    parse_cleanup: dict[str, list[str]],
) -> None:
    strategy = _create_strategy(
        client, auth_headers, parse_cleanup, name="待删除策略", is_default=False
    )
    patched = client.patch(
        f"{STRATEGIES}/{strategy['id']}",
        headers=auth_headers,
        json={"config": {"chunk_hint": 800}},
    )
    assert patched.status_code == 200
    latest = patched.json()["data"]
    assert latest["version"] == 2

    contributor = role_headers(["contributor"])
    assert (
        client.delete(f"{STRATEGIES}/{strategy['id']}", headers=contributor).status_code == 403
    )

    resp = client.delete(f"{STRATEGIES}/{strategy['id']}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"deleted": True, "disabled_versions": 2}

    listed = client.get(STRATEGIES, headers=auth_headers).json()["data"]["items"]
    assert strategy["strategy_key"] not in {item["strategy_key"] for item in listed}

    # 历史版本行仍在，任务可继续追溯执行时的策略
    db.rollback()
    rows = list(
        db.scalars(
            select(ParseStrategy)
            .where(ParseStrategy.strategy_key == strategy["strategy_key"])
            .order_by(ParseStrategy.version)
        )
    )
    assert [row.version for row in rows] == [1, 2]
    assert all(row.status == "disabled" for row in rows)
    assert all(row.is_default is False for row in rows)

    # 幂等：再次删除不报错
    assert (
        client.delete(f"{STRATEGIES}/{strategy['id']}", headers=auth_headers).status_code == 200
    )


def test_task_rows_are_owned_by_workspace(
    client: TestClient,
    auth_headers: dict[str, str],
    cleanup_document: list[uuid.UUID],
    parse_cleanup: dict[str, list[str]],
    db: Session,
) -> None:
    strategy = _create_strategy(client, auth_headers, parse_cleanup)
    document = _upload(client, auth_headers, cleanup_document)
    _, body = _trigger(client, auth_headers, document, parse_cleanup, strategy["id"])
    task_id = body["data"]["id"]

    db.rollback()
    task = db.scalar(select(ParseTask).where(ParseTask.id == uuid.UUID(task_id)))
    admin = db.scalar(select(AppUser).where(AppUser.username == settings.seed_admin_username))
    assert task is not None
    assert admin is not None
    assert task.workspace_id == admin.workspace_id
    assert task.document_id == uuid.UUID(document["id"])


def test_html_strategy_creation_and_engine_validation(
    client: TestClient,
    auth_headers: dict[str, str],
    parse_cleanup: dict[str, list[str]],
) -> None:
    strategy = _create_strategy(
        client,
        auth_headers,
        parse_cleanup,
        doc_type="html",
        engine="html_dom",
        name="HTML 结构解析",
        config={"content_selector": "#gb_content", "merge_titles": True},
    )
    assert strategy["doc_type"] == "html"
    assert strategy["engine"] == "html_dom"
    assert strategy["is_default"] is True
    assert strategy["config"]["content_selector"] == "#gb_content"

    listed = client.get(STRATEGIES, headers=auth_headers, params={"doc_type": "html"})
    assert listed.status_code == 200
    assert any(item["id"] == strategy["id"] for item in listed.json()["data"]["items"])

    rejected = client.post(
        STRATEGIES,
        headers=auth_headers,
        json={
            "name": "坏引擎",
            "doc_type": "html",
            "engine": "not_registered",
            "config": {},
            "is_default": False,
        },
    )
    assert rejected.status_code == 400
    assert "未注册的解析引擎" in rejected.json()["message"]


def test_html_document_parse_produces_structured_blocks(
    client: TestClient,
    auth_headers: dict[str, str],
    cleanup_document: list[uuid.UUID],
    parse_cleanup: dict[str, list[str]],
) -> None:
    strategy = _create_strategy(
        client,
        auth_headers,
        parse_cleanup,
        doc_type="html",
        engine="html_dom",
        name="HTML 公报解析",
        config={"content_selector": "#gb_content"},
    )
    document = _upload(
        client,
        auth_headers,
        cleanup_document,
        name="公报案例.html",
        content=HTML_FIXTURE.read_bytes(),
        mime="text/html",
    )
    assert document["doc_type"] == "html"

    status_code, body = _trigger(
        client, auth_headers, document, parse_cleanup, strategy["id"]
    )
    assert status_code == 202

    detail = _get_task(client, auth_headers, body["data"]["id"])
    assert detail["status"] == "succeeded"
    assert detail["stats"]["engine"] == "html_dom"
    assert detail["stats"]["page_count"] == 1
    assert detail["quality_score"] >= 60
    assert detail["low_quality"] is False

    blocks = client.get(
        f"{TASKS}/{body['data']['id']}/blocks", headers=auth_headers
    ).json()["data"]
    assert blocks["total"] == detail["block_count"]
    assert any(item["block_type"] == "title" for item in blocks["items"])
    assert any(item["block_type"] == "table" for item in blocks["items"])
    assert not any("<" in item["content"] for item in blocks["items"])
    assert not any("版权所有" in item["content"] for item in blocks["items"])
