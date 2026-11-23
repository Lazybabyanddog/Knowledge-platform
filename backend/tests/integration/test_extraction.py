"""知识抽取集成测试（任务 5.7）。

补丁策略：
- 抽取器固定为 ``RuleBasedExtractor``，不依赖是否有 LLM 密钥；
- 投递默认替换为同进程同步执行，覆盖真实 worker 代码路径；
- 解析产物直接落库（parse_task + parse_block），避免把解析引擎拖进本组用例。
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.enums import OntologyStatus
from app.infra.db.models import (
    AppUser,
    ExtractionTask,
    ExtractionTaskAttempt,
    ParseBlock,
    ParseStrategy,
    ParseTask,
)
from app.infra.db.session import SessionLocal
from app.infra.graph.driver import graph_session
from app.infra.llm import LLMPermanentError, LLMTransientError
from app.pipelines.extract.extractor import (
    BatchExtraction,
    RuleBasedExtractor,
    set_extractor,
)
from app.services.extraction_service import ExtractionService, set_dispatcher
from app.services.task_watchdog import sweep_stale_tasks

TASKS = "/api/v1/extraction-tasks"
ENTITIES = "/api/v1/entities"
GRAPH = "/api/v1/graph"
STRATEGIES = "/api/v1/parse-strategies"

BLOCKS = [
    ("paragraph", "张伟，星海科技，任职"),
    ("paragraph", "张伟，星海科技，任职"),
    ("footer", "版权所有"),
]


def _sync_execute(task_id: str) -> None:
    session = SessionLocal()
    try:
        ExtractionService(session).execute(uuid.UUID(task_id))
    finally:
        session.close()


@pytest.fixture(autouse=True)
def deterministic_extractor() -> Iterator[None]:
    set_extractor(RuleBasedExtractor())
    yield
    set_extractor(None)


@pytest.fixture
def sync_extract_dispatch() -> Iterator[None]:
    set_dispatcher(_sync_execute)
    yield
    set_dispatcher(None)


@pytest.fixture
def manual_extract_dispatch() -> Iterator[None]:
    """只入队不执行，用来观察 pending / 去重 / 取消。"""
    set_dispatcher(lambda _task_id: None)
    yield
    set_dispatcher(None)


@pytest.fixture
def strategy_id(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> Iterator[str]:
    resp = client.post(
        STRATEGIES,
        headers=auth_headers,
        json={
            "name": f"抽取测试策略{uuid.uuid4().hex[:6]}",
            "doc_type": "text",
            "engine": "plain_text",
            "config": {},
            "is_default": False,
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    yield data["id"]

    db.rollback()
    db.execute(delete(ParseTask).where(ParseTask.strategy_id == uuid.UUID(data["id"])))
    db.execute(
        delete(ParseStrategy).where(ParseStrategy.strategy_key == data["strategy_key"])
    )
    db.commit()


@pytest.fixture
def ontology_terms(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> Iterator[dict[str, str]]:
    """建一套最小本体：人员/组织 + 任职关系，用例结束按 code 清理 PG 与图库。"""
    suffix = uuid.uuid4().hex[:6]
    codes = {"person": f"person_{suffix}", "org": f"org_{suffix}", "works_at": f"works_{suffix}"}

    person = client.post(
        "/api/v1/ontology/classes",
        headers=auth_headers,
        json={"code": codes["person"], "name": "张伟", "status": 0},
    )
    assert person.status_code == 201, person.text
    org = client.post(
        "/api/v1/ontology/classes",
        headers=auth_headers,
        json={"code": codes["org"], "name": "星海科技", "status": 0},
    )
    assert org.status_code == 201, org.text
    relation = client.post(
        "/api/v1/ontology/relations",
        headers=auth_headers,
        json={
            "code": codes["works_at"],
            "name": "任职",
            "source_class_id": person.json()["data"]["id"],
            "target_class_id": org.json()["data"]["id"],
            "status": 0,
        },
    )
    assert relation.status_code == 201, relation.text

    yield {
        "person": codes["person"],
        "org": codes["org"],
        "works_at": codes["works_at"],
        "person_id": person.json()["data"]["id"],
        "org_id": org.json()["data"]["id"],
    }

    from app.infra.db.models import OntologyClass, OntologyRelation

    db.execute(
        delete(OntologyRelation).where(OntologyRelation.code == codes["works_at"])
    )
    db.execute(
        delete(OntologyClass).where(
            OntologyClass.code.in_([codes["person"], codes["org"]])
        )
    )
    db.commit()
    with graph_session() as graph:
        graph.run(
            "MATCH (c:Class) WHERE c.code IN $codes DETACH DELETE c",
            codes=[codes["person"], codes["org"]],
        ).consume()


@pytest.fixture
def extraction_graph_cleanup(graph_db: None) -> Iterator[list[str]]:
    task_ids: list[str] = []
    yield task_ids
    if not task_ids:
        return
    with graph_session() as graph:
        graph.run(
            "MATCH (e:Entity) WHERE e.extraction_task_id IN $task_ids DETACH DELETE e",
            task_ids=task_ids,
        ).consume()


def _upload(
    client: TestClient, headers: dict[str, str], cleanup: list[uuid.UUID]
) -> dict[str, Any]:
    resp = client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("抽取样例.txt", "张伟，星海科技，任职".encode(), "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    cleanup.append(uuid.UUID(data["id"]))
    return data


def _seed_parse_task(
    db: Session,
    document: dict[str, Any],
    strategy_id: str,
    *,
    status: str = "succeeded",
    blocks: list[tuple[str, str]] | None = None,
) -> ParseTask:
    admin = (
        db.query(AppUser)
        .filter(AppUser.username == settings.seed_admin_username)
        .one()
    )
    task = ParseTask(
        workspace_id=admin.workspace_id,
        document_id=uuid.UUID(document["id"]),
        document_version_id=uuid.UUID(document["current_version_id"]),
        strategy_id=uuid.UUID(strategy_id),
        status=status,
        attempt=1,
        stats={},
    )
    db.add(task)
    db.flush()
    for seq, (block_type, content) in enumerate(blocks or BLOCKS):
        db.add(
            ParseBlock(
                parse_task_id=task.id,
                document_version_id=task.document_version_id,
                seq=seq,
                block_type=block_type,
                content=content,
                page_no=1,
            )
        )
    db.commit()
    return task


def test_extraction_end_to_end_produces_candidates(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    ontology_terms: dict[str, str],
    sync_extract_dispatch: None,
    extraction_graph_cleanup: list[str],
) -> None:
    document = _upload(client, auth_headers, cleanup_document)
    parse_task = _seed_parse_task(db, document, strategy_id)

    resp = client.post(
        TASKS, headers=auth_headers, json={"parse_task_id": str(parse_task.id)}
    )
    assert resp.status_code == 202, resp.text
    task_id = resp.json()["data"]["id"]
    extraction_graph_cleanup.append(task_id)

    detail = client.get(f"{TASKS}/{task_id}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    payload = detail.json()["data"]
    assert payload["status"] == "succeeded", payload
    assert payload["attempt"] == 1
    assert payload["stats"]["entities_persisted"] == 2
    # 关系按证据键去重：两段正文内容相同但属于不同内容块，是两条各自成立的任职记载
    assert payload["stats"]["relations_persisted"] == 2
    assert payload["stats"]["skipped_blocks"] >= 1

    def _ours(class_code: str) -> list[dict[str, Any]]:
        # 按本次用例独有的类编码过滤：工作区里可能残留其他数据，分页会把它们一起带出来
        listed = client.get(
            ENTITIES,
            headers=auth_headers,
            params={"status": "proposed", "class_code": class_code},
        ).json()["data"]
        return [item for item in listed["items"] if item["extraction_task_id"] == task_id]

    ours = _ours(ontology_terms["person"]) + _ours(ontology_terms["org"])
    assert {item["name"] for item in ours} == {"张伟", "星海科技"}
    class_by_name = {
        "张伟": ontology_terms["person"],
        "星海科技": ontology_terms["org"],
    }
    for item in ours:
        assert item["class_code"] == class_by_name[item["name"]]

    relations = client.get(
        "/api/v1/relations",
        headers=auth_headers,
        params={"status": "proposed", "extraction_task_id": task_id},
    ).json()["data"]
    assert relations["total"] == 2
    assert {item["code"] for item in relations["items"]} == {ontology_terms["works_at"]}
    assert len({item["evidence_key"] for item in relations["items"]}) == 2

    doc_entities = client.get(
        f"{GRAPH}/documents/{document['id']}/entities", headers=auth_headers
    ).json()["data"]
    assert {item["id"] for item in doc_entities["items"]} == {item["id"] for item in ours}

    tasks = client.get(
        TASKS, headers=auth_headers, params={"parse_task_id": str(parse_task.id)}
    ).json()["data"]
    assert tasks["total"] == 1


def test_trigger_requires_succeeded_parse_task(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    ontology_terms: dict[str, str],
    manual_extract_dispatch: None,
    extraction_graph_cleanup: list[str],
) -> None:
    document = _upload(client, auth_headers, cleanup_document)
    parse_task = _seed_parse_task(db, document, strategy_id, status="pending")

    resp = client.post(
        TASKS, headers=auth_headers, json={"parse_task_id": str(parse_task.id)}
    )
    assert resp.status_code == 409
    assert resp.json()["data"]["status"] == "pending"


def test_duplicate_trigger_cancel_and_permissions(
    client: TestClient,
    auth_headers: dict[str, str],
    role_headers: Callable[[list[str]], dict[str, str]],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    ontology_terms: dict[str, str],
    manual_extract_dispatch: None,
    extraction_graph_cleanup: list[str],
) -> None:
    document = _upload(client, auth_headers, cleanup_document)
    parse_task = _seed_parse_task(db, document, strategy_id)
    body = {"parse_task_id": str(parse_task.id)}

    first = client.post(TASKS, headers=auth_headers, json=body)
    assert first.status_code == 202, first.text
    task_id = first.json()["data"]["id"]
    extraction_graph_cleanup.append(task_id)
    assert first.json()["data"]["status"] == "pending"

    duplicated = client.post(TASKS, headers=auth_headers, json=body)
    assert duplicated.status_code == 409
    assert duplicated.json()["data"]["task_id"] == task_id

    viewer = role_headers(["viewer"])
    assert client.post(TASKS, headers=viewer, json=body).status_code == 403
    assert client.get(TASKS, headers=viewer).status_code == 200

    cancelled = client.post(f"{TASKS}/{task_id}/cancel", headers=auth_headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["status"] == "cancelled"

    again = client.post(TASKS, headers=auth_headers, json=body)
    assert again.status_code == 202, again.text
    extraction_graph_cleanup.append(again.json()["data"]["id"])


class FailingExtractor(RuleBasedExtractor):
    """按调用序号注入异常：用来验证"第几批失败"对应的预算与断点行为。"""

    name = "failing"

    def __init__(self, failures: dict[int, Exception]) -> None:
        self.failures = dict(failures)
        self.calls = 0

    def extract(
        self,
        blocks: list[dict[str, Any]],
        classes: dict[str, dict[str, Any]],
        relations: dict[str, dict[str, Any]],
    ) -> BatchExtraction:
        self.calls += 1
        exc = self.failures.get(self.calls)
        if exc is not None:
            raise exc
        return super().extract(blocks, classes, relations)


def _seed_blocks_task(
    db: Session,
    document: dict[str, Any],
    strategy_id: str,
    *,
    paragraphs: int,
) -> ParseTask:
    """按需造 N 个正文块：批次数由 N 与批大小共同决定，便于定位"第几批失败"。"""
    admin = (
        db.query(AppUser)
        .filter(AppUser.username == settings.seed_admin_username)
        .one()
    )
    task = ParseTask(
        workspace_id=admin.workspace_id,
        document_id=uuid.UUID(document["id"]),
        document_version_id=uuid.UUID(document["current_version_id"]),
        strategy_id=uuid.UUID(strategy_id),
        status="succeeded",
        attempt=1,
        stats={},
    )
    db.add(task)
    db.flush()
    for seq in range(paragraphs):
        db.add(
            ParseBlock(
                parse_task_id=task.id,
                document_version_id=task.document_version_id,
                seq=seq,
                block_type="paragraph",
                content=f"张伟，星海科技，任职（第{seq}段）",
                page_no=1,
            )
        )
    db.commit()
    return task


def _trigger(
    client: TestClient, headers: dict[str, str], parse_task_id: uuid.UUID
) -> str:
    resp = client.post(TASKS, headers=headers, json={"parse_task_id": str(parse_task_id)})
    assert resp.status_code == 202, resp.text
    return resp.json()["data"]["id"]


def _detail(client: TestClient, headers: dict[str, str], task_id: str) -> dict[str, Any]:
    resp = client.get(f"{TASKS}/{task_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_transient_failure_auto_retries_then_succeeds(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    ontology_terms: dict[str, str],
    manual_extract_dispatch: None,
    extraction_graph_cleanup: list[str],
) -> None:
    """可重试失败：任务短暂回 PENDING 并按退避重排，第二次跑成功。"""
    delayed: list[tuple[str, int]] = []
    set_dispatcher(lambda _task_id: None, delayed=lambda tid, cd: delayed.append((tid, cd)))
    set_extractor(
        FailingExtractor({1: LLMTransientError("read timeout", error_code="llm_timeout")})
    )
    try:
        document = _upload(client, auth_headers, cleanup_document)
        parse_task = _seed_parse_task(db, document, strategy_id)
        task_id = _trigger(client, auth_headers, parse_task.id)
        extraction_graph_cleanup.append(task_id)

        _sync_execute(task_id)
        detail = _detail(client, auth_headers, task_id)
        assert detail["status"] == "pending", detail
        assert detail["auto_retries"] == 1
        assert detail["error_code"] == "llm_timeout"
        assert delayed == [(task_id, settings.task_auto_retry_backoff_seconds[0])]
        first = detail["attempts"][0]
        assert first["source"] == "manual"
        assert first["status"] == "failed"
        assert first["error_detail"]["cause"] == "llm_timeout"
        assert first["error_detail"]["stage"] == "pipeline"
        assert detail["actions"]["auto_retry_remaining"] == (
            settings.task_auto_retry_max_attempts - 1
        )

        _sync_execute(task_id)  # 模拟退避到点后重投
        detail = _detail(client, auth_headers, task_id)
        assert detail["status"] == "succeeded", detail
        assert [item["source"] for item in detail["attempts"]] == ["auto_retry", "manual"]
        assert detail["attempts"][0]["duration_ms"] is not None
        assert detail["stats"]["entities_persisted"] == 2
    finally:
        set_dispatcher(None)


def test_permanent_failure_lands_failed_without_retry(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    ontology_terms: dict[str, str],
    manual_extract_dispatch: None,
    extraction_graph_cleanup: list[str],
) -> None:
    """凭据类失败重试只会浪费预算：直接失败，并给人工留出口。"""
    delayed: list[tuple[str, int]] = []
    set_dispatcher(lambda _task_id: None, delayed=lambda tid, cd: delayed.append((tid, cd)))
    set_extractor(FailingExtractor({1: LLMPermanentError("invalid api key")}))
    try:
        document = _upload(client, auth_headers, cleanup_document)
        parse_task = _seed_parse_task(db, document, strategy_id)
        task_id = _trigger(client, auth_headers, parse_task.id)
        extraction_graph_cleanup.append(task_id)

        _sync_execute(task_id)
        detail = _detail(client, auth_headers, task_id)
        assert detail["status"] == "failed", detail
        assert detail["error_code"] == "llm_rejected"
        assert detail["auto_retries"] == 0
        assert delayed == []
        assert len(detail["attempts"]) == 1
        assert detail["actions"]["can_revive"] is True
    finally:
        set_dispatcher(None)


def test_auto_budget_exhausted_then_downgrade_then_failed(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    ontology_terms: dict[str, str],
    manual_extract_dispatch: None,
    extraction_graph_cleanup: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """预算分层：自动重试用尽后降参一次，再失败才落终态，三类来源各自计数。"""
    monkeypatch.setattr(settings, "task_auto_retry_max_attempts", 1)
    monkeypatch.setattr(settings, "task_downgrade_max_attempts", 1)
    monkeypatch.setattr(settings, "task_auto_retry_backoff_seconds", [0])
    set_dispatcher(lambda _task_id: None, delayed=lambda _tid, _cd: None)
    set_extractor(
        FailingExtractor(
            {
                1: LLMTransientError("429"),
                2: LLMTransientError("429"),
                3: LLMTransientError("429"),
            }
        )
    )
    try:
        document = _upload(client, auth_headers, cleanup_document)
        parse_task = _seed_parse_task(db, document, strategy_id)
        task_id = _trigger(client, auth_headers, parse_task.id)
        extraction_graph_cleanup.append(task_id)

        _sync_execute(task_id)
        assert _detail(client, auth_headers, task_id)["auto_retries"] == 1

        _sync_execute(task_id)  # 自动预算用尽 -> 降参
        detail = _detail(client, auth_headers, task_id)
        assert detail["status"] == "pending", detail
        assert detail["downgrade_retries"] == 1
        task = db.get(ExtractionTask, uuid.UUID(task_id))
        db.refresh(task)
        assert task.config["batch_size"] < settings.extract_batch_size

        _sync_execute(task_id)  # 降参也失败 -> 终态
        detail = _detail(client, auth_headers, task_id)
        assert detail["status"] == "failed", detail
        assert detail["error_code"] == "attempts_exhausted"
        assert [item["source"] for item in detail["attempts"]] == [
            "downgrade",
            "auto_retry",
            "manual",
        ]
        assert detail["actions"]["can_skip_failed_batches"] is True
    finally:
        set_dispatcher(None)


def test_skip_failed_batches_finishes_partial(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    ontology_terms: dict[str, str],
    extraction_graph_cleanup: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跳过失败批次：已完成批次保留，缺的批次标跳过，结果是"部分完成"。"""
    monkeypatch.setattr(settings, "extract_batch_size", 2)
    monkeypatch.setattr(settings, "task_auto_retry_max_attempts", 0)
    set_dispatcher(lambda _task_id: None)
    set_extractor(FailingExtractor({2: LLMPermanentError("invalid api key")}))
    try:
        document = _upload(client, auth_headers, cleanup_document)
        parse_task = _seed_blocks_task(db, document, strategy_id, paragraphs=4)
        task_id = _trigger(client, auth_headers, parse_task.id)
        extraction_graph_cleanup.append(task_id)

        _sync_execute(task_id)  # 第一批成功、第二批失败
        detail = _detail(client, auth_headers, task_id)
        assert detail["status"] == "failed", detail
        assert detail["stats"]["batches_done"] == 1

        set_dispatcher(_sync_execute)  # 跳过接口自己会投递一次执行
        resp = client.post(f"{TASKS}/{task_id}/skip-failed-batches", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "pending"

        detail = _detail(client, auth_headers, task_id)
        assert detail["status"] == "succeeded", detail
        assert detail["incomplete"] is True
        assert detail["manual_revives"] == 1
        assert detail["stats"]["batches_skipped"] >= 1
        assert detail["skip_summary"] == [{"reason": "batch_failed", "count": 1}]
        assert detail["attempts"][0]["source"] == "skip_batches"
    finally:
        set_dispatcher(None)


def test_cancelled_task_can_be_revived(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    ontology_terms: dict[str, str],
    manual_extract_dispatch: None,
    extraction_graph_cleanup: list[str],
) -> None:
    """取消是可人工恢复的终态：复活走人工预算，执行来源记为 manual_revive。"""
    set_extractor(RuleBasedExtractor())
    document = _upload(client, auth_headers, cleanup_document)
    parse_task = _seed_parse_task(db, document, strategy_id)
    task_id = _trigger(client, auth_headers, parse_task.id)
    extraction_graph_cleanup.append(task_id)

    cancelled = client.post(f"{TASKS}/{task_id}/cancel", headers=auth_headers)
    assert cancelled.status_code == 200, cancelled.text

    revived = client.post(f"{TASKS}/{task_id}/retry", headers=auth_headers)
    assert revived.status_code == 200, revived.text
    assert revived.json()["data"]["status"] == "pending"
    assert revived.json()["data"]["manual_revives"] == 1

    _sync_execute(task_id)
    detail = _detail(client, auth_headers, task_id)
    assert detail["status"] == "succeeded", detail
    assert detail["attempts"][0]["source"] == "manual_revive"


def test_watchdog_recovers_stale_running_task(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    ontology_terms: dict[str, str],
    manual_extract_dispatch: None,
    extraction_graph_cleanup: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """worker 被杀：心跳停止超过阈值后看护送回 PENDING 并重投，占用一次自动预算。"""
    redispatched: list[str] = []
    monkeypatch.setattr(
        "app.services.task_watchdog._dispatch_extraction", redispatched.append
    )

    document = _upload(client, auth_headers, cleanup_document)
    parse_task = _seed_parse_task(db, document, strategy_id)
    task_id = _trigger(client, auth_headers, parse_task.id)
    extraction_graph_cleanup.append(task_id)

    from datetime import UTC, datetime, timedelta

    stale = datetime.now(UTC) - timedelta(seconds=settings.task_stale_threshold_seconds + 60)
    task = db.get(ExtractionTask, uuid.UUID(task_id))
    task.status = "running"
    task.attempt = 1
    task.started_at = stale
    db.add(
        ExtractionTaskAttempt(
            extraction_task_id=task.id,
            attempt_no=1,
            status="running",
            source="manual",
            stats={},
            started_at=stale,
            heartbeat_at=stale,
        )
    )
    db.commit()

    result = sweep_stale_tasks(db)
    assert result["extraction"] >= 1

    db.expire_all()
    task = db.get(ExtractionTask, uuid.UUID(task_id))
    assert task.status == "pending"
    assert task.auto_retries == 1
    assert task.next_source == "watchdog"
    attempt = (
        db.query(ExtractionTaskAttempt)
        .filter(ExtractionTaskAttempt.extraction_task_id == task.id)
        .order_by(ExtractionTaskAttempt.attempt_no.desc())
        .first()
    )
    assert attempt is not None and attempt.status == "failed"
    assert attempt.error_code == "task_timeout"
    assert task_id in redispatched


def test_trigger_requires_ontology_classes(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    manual_extract_dispatch: None,
    extraction_graph_cleanup: list[str],
) -> None:
    """没有可用本体时明确拒绝，避免抽出一堆无法归类的候选。"""
    admin = (
        db.query(AppUser)
        .filter(AppUser.username == settings.seed_admin_username)
        .one()
    )
    document = _upload(client, auth_headers, cleanup_document)
    parse_task = _seed_parse_task(db, document, strategy_id)

    # 临时把本空间的所有实体类置为失效（status=1），模拟"没有可用本体"
    from app.infra.db.models import OntologyClass

    rows = list(
        db.query(OntologyClass).filter(OntologyClass.workspace_id == admin.workspace_id)
    )
    original = {row.id: row.status for row in rows}
    for row in rows:
        row.status = OntologyStatus.INVALID.value
    db.commit()
    try:
        resp = client.post(
            TASKS, headers=auth_headers, json={"parse_task_id": str(parse_task.id)}
        )
        assert resp.status_code == 400
        assert "本体" in resp.json()["message"]
    finally:
        for row in rows:
            row.status = original[row.id]
        db.commit()


class _ScriptedExtractor:
    """按用例给的剧本返回固定 JSON，用来精确复现"属性 + 身份 + 多实例关系"。"""

    name = "scripted"

    def __init__(self, plan: Callable[[list[dict[str, Any]]], dict[str, Any]]) -> None:
        self.plan = plan
        self.calls = 0

    def extract(
        self,
        blocks: list[dict[str, Any]],
        classes: dict[str, dict[str, Any]],
        relations: dict[str, dict[str, Any]],
    ) -> BatchExtraction:
        self.calls += 1
        return BatchExtraction(raw=self.plan(blocks))

    def repair(
        self,
        raw: dict[str, Any] | None,
        issues: list[dict[str, Any]],
        blocks: list[dict[str, Any]],
        classes: dict[str, dict[str, Any]],
        relations: dict[str, dict[str, Any]],
    ) -> BatchExtraction:
        return BatchExtraction(raw=raw)


@pytest.fixture
def attribute_ontology(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> Iterator[dict[str, str]]:
    """带属性与标识键的本体：企业（uscc 为标识属性）+ 人员 + 任职关系（起止时间）。"""
    suffix = uuid.uuid4().hex[:6]
    codes = {
        "person": f"person_{suffix}",
        "org": f"org_{suffix}",
        "works_at": f"works_{suffix}",
    }

    person = client.post(
        "/api/v1/ontology/classes",
        headers=auth_headers,
        json={
            "code": codes["person"],
            "name": "张伟",
            "status": 0,
            "attributes": {
                "title": {
                    "code": "title",
                    "name": "职务",
                    "type": "string",
                    "description": "在组织中的职务",
                }
            },
        },
    )
    assert person.status_code == 201, person.text
    org = client.post(
        "/api/v1/ontology/classes",
        headers=auth_headers,
        json={
            "code": codes["org"],
            "name": "星海科技",
            "status": 0,
            "attributes": {
                "uscc": {
                    "code": "uscc",
                    "name": "统一社会信用代码",
                    "type": "string",
                    "description": "营业执照登记号，逐字抄写",
                    "required": True,
                },
                "city": {
                    "code": "city",
                    "name": "注册地",
                    "type": "string",
                    "description": "注册地城市",
                },
            },
            "identity_keys": ["uscc"],
        },
    )
    assert org.status_code == 201, org.text
    relation = client.post(
        "/api/v1/ontology/relations",
        headers=auth_headers,
        json={
            "code": codes["works_at"],
            "name": "任职",
            "source_class_id": person.json()["data"]["id"],
            "target_class_id": org.json()["data"]["id"],
            "status": 0,
            "attributes": {
                "start_date": {
                    "code": "start_date",
                    "name": "开始日期",
                    "type": "date",
                    "description": "任职开始日期",
                },
                "end_date": {
                    "code": "end_date",
                    "name": "结束日期",
                    "type": "date",
                    "description": "任职结束日期，仍在职留空",
                },
            },
        },
    )
    assert relation.status_code == 201, relation.text

    yield codes

    from app.infra.db.models import OntologyClass, OntologyRelation

    db.execute(delete(OntologyRelation).where(OntologyRelation.code == codes["works_at"]))
    db.execute(
        delete(OntologyClass).where(
            OntologyClass.code.in_([codes["person"], codes["org"]])
        )
    )
    db.commit()
    with graph_session() as graph:
        graph.run(
            "MATCH (c:Class) WHERE c.code IN $codes DETACH DELETE c",
            codes=[codes["person"], codes["org"]],
        ).consume()


def test_same_uscc_across_documents_merges_into_one_entity(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    attribute_ontology: dict[str, str],
    sync_extract_dispatch: None,
    extraction_graph_cleanup: list[str],
) -> None:
    """9.1 两份文档抽出同一信用代码的企业：图里只有一个节点，document_ids 含两份文档。"""
    uscc = "91310000MA1K35QX8N"
    org_code = attribute_ontology["org"]

    def _plan(city: str) -> Callable[[list[dict[str, Any]]], dict[str, Any]]:
        def plan(blocks: list[dict[str, Any]]) -> dict[str, Any]:
            block_id = str(blocks[0]["id"])
            return {
                "entities": [
                    {
                        "ref": "org",
                        "class_code": org_code,
                        "name": "星海科技",
                        "attributes": {"uscc": uscc, "city": city},
                        "confidence": 0.9,
                        "evidence_block_ids": [block_id],
                    }
                ],
                "relations": [],
            }

        return plan

    parsed: list[tuple[str, Any]] = []
    for city in ("杭州", "上海"):
        document = _upload(client, auth_headers, cleanup_document)
        parse_task = _seed_parse_task(
            db,
            document,
            strategy_id,
            blocks=[("paragraph", f"星海科技，统一社会信用代码 {uscc}，注册地{city}。")],
        )
        set_extractor(_ScriptedExtractor(_plan(city)))
        task_id = _trigger(client, auth_headers, parse_task.id)
        extraction_graph_cleanup.append(task_id)
        parsed.append((document["id"], task_id))

    listed = client.get(
        ENTITIES, headers=auth_headers, params={"status": "proposed", "class_code": org_code}
    ).json()["data"]
    ours = [item for item in listed["items"] if item["class_code"] == org_code]
    assert len(ours) == 1, ours
    entity = ours[0]
    assert entity["identity_kind"] == "identity"
    assert entity["identity_key"] == f"identity:{uscc}"
    assert {document_id for document_id in entity["document_ids"]} == {
        parsed[0][0],
        parsed[1][0],
    }
    # 既有非空值优先：第二份文档的"上海"不能覆盖第一份的"杭州"
    assert entity["attributes"]["uscc"] == uscc
    assert entity["attributes"]["city"] == "杭州"

    second = _detail(client, auth_headers, parsed[1][1])
    assert second["stats"]["entities_persisted"] == 1
    assert second["stats"]["attribute_conflicts"] >= 1


def test_two_employment_records_stay_two_relations(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    attribute_ontology: dict[str, str],
    sync_extract_dispatch: None,
    extraction_graph_cleanup: list[str],
) -> None:
    """9.2 同一人的两段任职：两条边各带起止时间；重跑同一文档数量不变。"""
    person_code = attribute_ontology["person"]
    org_code = attribute_ontology["org"]
    relation_code = attribute_ontology["works_at"]
    first_text = "张伟自 2020 年 3 月至 2022 年 6 月任职于星海科技。"
    second_text = "2023 年 9 月起，张伟再次任职于星海科技。"

    def _plan(blocks: list[dict[str, Any]]) -> dict[str, Any]:
        first_id, second_id = (str(item["id"]) for item in blocks)
        return {
            "entities": [
                {
                    "ref": "p",
                    "class_code": person_code,
                    "name": "张伟",
                    "confidence": 0.9,
                    "evidence_block_ids": [first_id, second_id],
                },
                {
                    "ref": "o",
                    "class_code": org_code,
                    "name": "星海科技",
                    "attributes": {"uscc": "91310000MA1K35QX8N"},
                    "confidence": 0.9,
                    "evidence_block_ids": [first_id, second_id],
                },
            ],
            "relations": [
                {
                    "source_ref": "p",
                    "target_ref": "o",
                    "code": relation_code,
                    "attributes": {"start_date": "2020-03-01", "end_date": "2022-06-01"},
                    "evidence_block_id": first_id,
                    "evidence_text": "张伟自 2020 年 3 月至 2022 年 6 月任职于星海科技",
                    "confidence": 0.8,
                },
                {
                    "source_ref": "p",
                    "target_ref": "o",
                    "code": relation_code,
                    "attributes": {"start_date": "2023-09-01"},
                    "evidence_block_id": second_id,
                    "evidence_text": "2023 年 9 月起，张伟再次任职于星海科技",
                    "confidence": 0.8,
                },
            ],
        }

    document = _upload(client, auth_headers, cleanup_document)
    parse_task = _seed_parse_task(
        db,
        document,
        strategy_id,
        blocks=[("paragraph", first_text), ("paragraph", second_text)],
    )
    set_extractor(_ScriptedExtractor(_plan))

    task_id = _trigger(client, auth_headers, parse_task.id)
    extraction_graph_cleanup.append(task_id)
    detail = _detail(client, auth_headers, task_id)
    assert detail["status"] == "succeeded", detail
    assert detail["stats"]["relations_persisted"] == 2

    def _relations() -> list[dict[str, Any]]:
        data = client.get(
            "/api/v1/relations",
            headers=auth_headers,
            params={"code": relation_code, "page_size": 20},
        ).json()["data"]
        return [item for item in data["items"] if item["code"] == relation_code]

    rows = _relations()
    assert len(rows) == 2, rows
    starts = sorted(item["attributes"]["start_date"] for item in rows)
    assert starts == ["2020-03-01", "2023-09-01"]
    assert {item["evidence_text"] for item in rows} == {
        "张伟自 2020 年 3 月至 2022 年 6 月任职于星海科技",
        "2023 年 9 月起，张伟再次任职于星海科技",
    }
    assert len({item["evidence_key"] for item in rows}) == 2

    # 重跑同一份解析产物：证据键不变，边不新增也不重复
    rerun_id = _trigger(client, auth_headers, parse_task.id)
    extraction_graph_cleanup.append(rerun_id)
    assert len(_relations()) == 2


def test_entity_attributes_survive_manual_edit_and_confirm(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    cleanup_document: list[uuid.UUID],
    strategy_id: str,
    attribute_ontology: dict[str, str],
    sync_extract_dispatch: None,
    extraction_graph_cleanup: list[str],
) -> None:
    """9.3 属性从抽取到审核页可见、可人工修正，确认后状态与属性都正确。"""
    org_code = attribute_ontology["org"]
    uscc = "91310000MA1K35QX8N"

    def _plan(blocks: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "entities": [
                {
                    "ref": "org",
                    "class_code": org_code,
                    "name": "星海科技",
                    "attributes": {"uscc": uscc, "city": "杭州"},
                    "confidence": 0.7,
                    "evidence_block_ids": [str(blocks[0]["id"])],
                }
            ],
            "relations": [],
        }

    document = _upload(client, auth_headers, cleanup_document)
    parse_task = _seed_parse_task(
        db,
        document,
        strategy_id,
        blocks=[("paragraph", f"星海科技，统一社会信用代码 {uscc}，注册地杭州。")],
    )
    set_extractor(_ScriptedExtractor(_plan))
    task_id = _trigger(client, auth_headers, parse_task.id)
    extraction_graph_cleanup.append(task_id)

    listed = client.get(
        ENTITIES, headers=auth_headers, params={"status": "proposed", "class_code": org_code}
    ).json()["data"]["items"]
    entity = next(item for item in listed if item["class_code"] == org_code)
    assert entity["attributes"] == {"uscc": uscc, "city": "杭州"}

    corrected = client.patch(
        f"{ENTITIES}/{entity['id']}",
        headers=auth_headers,
        json={"attributes": {"uscc": uscc, "city": "上海", "industry": "工业软件"}},
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["data"]["attributes"] == {
        "uscc": uscc,
        "city": "上海",
        "industry": "工业软件",
    }

    confirmed = client.post(f"{ENTITIES}/{entity['id']}/confirm", headers=auth_headers, json={})
    assert confirmed.status_code == 200, confirmed.text
    data = confirmed.json()["data"]
    assert data["status"] == "confirmed"
    assert data["attributes"]["city"] == "上海"
    assert data["identity_key"] == f"identity:{uscc}"


def test_concurrent_persist_keeps_document_ids_from_both_writes(
    extraction_graph_cleanup: list[str],
) -> None:
    """工作空间锁把"读既有 → 合并 → 写"串行化，并发写同一身份不丢文档归属。"""
    from app.pipelines.extract.writer import neo4j_writer

    workspace_id = f"ws-{uuid.uuid4().hex[:8]}"
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    extraction_graph_cleanup.append(task_id)
    identity_key = f"name:并发企业-{uuid.uuid4().hex[:6]}"
    documents = [f"doc-a-{uuid.uuid4().hex[:6]}", f"doc-b-{uuid.uuid4().hex[:6]}"]
    barrier = threading.Barrier(len(documents))

    def _candidates(document_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": f"e-{document_id}",
                "class_code": "person",
                "name": "并发企业",
                "normalized_name": "并发企业",
                "identity_key": identity_key,
                "identity_kind": "name",
                "aliases": [],
                "attributes": {},
                "confidence": 0.6,
                "evidence_block_ids": ["b1"],
            }
        ]

    errors: list[Exception] = []

    def _run(document_id: str) -> None:
        try:
            barrier.wait(timeout=5)
            neo4j_writer(workspace_id, task_id, document_id, _candidates(document_id), [])
        except Exception as exc:  # noqa: BLE001 - 线程内异常要带回主线程断言
            errors.append(exc)

    threads = [threading.Thread(target=_run, args=(doc,)) for doc in documents]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    with graph_session() as graph:
        records = list(
            graph.run(
                "MATCH (e:Entity {workspace_id: $ws, identity_key: $key}) "
                "RETURN e.document_ids AS ids",
                ws=workspace_id,
                key=identity_key,
            )
        )
    assert len(records) == 1
    assert set(records[0]["ids"]) == set(documents)
