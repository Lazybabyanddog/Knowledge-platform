"""改写实验室端到端用例。

真实跑检索 SQL（过滤条件与双路查询都走同一套语句），只把模型与图谱换成假的：
- 模型按 system 提示词分派脚本，用来验证五个策略真的都执行了；
- 图谱只返回一个候选实体，避免消歧额外消耗预算。

断言重点：Trace 区块完整、决策数与注册表一致、totals 与实际调用次数可对账。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.infra.db.models import AppUser, Document, DocumentVersion, ParseStrategy
from app.infra.db.models.parse_task import ParseTask
from app.infra.db.models.retrieval import DocumentChunk
from app.infra.llm import prompts, provider
from app.services.query_rewrite import service as rewrite_service

QUERY = "它和《员工手册》里的年假规定有什么区别？同时调休怎么算？"
HISTORY = [{"role": "user", "content": "年假怎么算"}]


@pytest.fixture
def fake_runtime(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """假模型 + 假图谱；返回的列表按调用顺序记录命中的 system 提示词。"""
    calls: list[str] = []
    script = {
        prompts.QUERY_UNDERSTAND_SYSTEM: {
            "intent": "对比制度条款",
            "complexity": "high",
            "entities": ["员工手册"],
            "references": [{"cue": "它", "reason": "指上一轮的年假问题"}],
            "constraints": ["只依据已发布制度"],
            "candidate_strategies": ["context", "entity", "semantic", "decompose", "multi", "hyde"],
        },
        prompts.CONTEXT_REWRITE_SYSTEM: {
            "query": "《员工手册》里的年假规定与上一轮问的年假制度有什么区别？同时调休怎么算？",
            "reason": "按历史补全被省略的对象",
        },
        prompts.SEMANTIC_REWRITE_SYSTEM: {
            "query": "员工手册 年假规定 制度 区别 调休 计算 规则",
            "reason": "去掉口语，突出检索关键词",
        },
        prompts.DECOMPOSE_SYSTEM: {
            "sub_questions": [
                "员工手册的年假规定是什么",
                "年假制度与调休有什么区别",
                "调休怎么计算",
            ],
            "reason": "复合问题拆成三个子问题",
        },
        prompts.MULTI_QUERY_SYSTEM: {
            "queries": ["年假 计算 规则", "调休 申请 条件", "员工手册 年假 条款"],
            "reason": "从三个角度覆盖同一需求",
        },
    }

    def _chat_json(system: str, user: str) -> dict[str, object]:
        calls.append(system)
        payload = script.get(system)
        assert payload is not None, f"未脚本化的提示词：{system[:24]}"
        return dict(payload)

    def _lookup(workspace_id: uuid.UUID):
        def _query(keyword: str) -> list[dict[str, object]]:
            return [{"id": "entity-1", "name": "员工手册", "confidence": 0.98}]

        return _query

    monkeypatch.setattr(provider, "is_llm_configured", lambda: True)
    monkeypatch.setattr(provider, "is_embedding_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", _chat_json)
    vector = [0.01] * settings.embedding_dim
    monkeypatch.setattr(provider, "embed_texts", lambda texts: [list(vector) for _ in texts])
    monkeypatch.setattr(rewrite_service, "_graph_available", lambda: True)
    monkeypatch.setattr(rewrite_service, "_entity_lookup", _lookup)
    # 五个策略 + 一次理解 = 5 次调用，默认上限 4 会把靠后的策略拦掉
    monkeypatch.setattr(settings, "query_rewrite_max_llm_calls", 10)
    monkeypatch.setattr(settings, "query_rewrite_max_queries", 40)
    monkeypatch.setattr(settings, "query_rewrite_timeout_seconds", 30.0)
    return calls


@pytest.fixture
def seeded_document(db: Session) -> Iterator[Document]:
    """一篇已发布、带两个片段的文档：两条命中都超过阈值，保证首轮就充分。"""
    admin = db.query(AppUser).filter(AppUser.username == settings.seed_admin_username).one()
    strategy_id = db.scalar(select(ParseStrategy.id).limit(1))
    if strategy_id is None:
        pytest.skip("没有解析策略种子，跳过改写实验室端到端用例")

    document = Document(
        workspace_id=admin.workspace_id,
        title=f"员工手册-{uuid.uuid4().hex[:6]}",
        doc_type="text",
        status="published",
        sensitivity="internal",
        created_by=admin.id,
        owner_id=admin.id,
        deleted_at=None,
    )
    db.add(document)
    db.flush()

    version = DocumentVersion(
        document_id=document.id,
        version_no=1,
        storage_key=f"pytest/{uuid.uuid4().hex}.txt",
        file_name="handbook.txt",
        file_size=32,
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

    vector = [0.01] * settings.embedding_dim
    for seq, content in enumerate(
        ["员工手册：年假按工龄计算，满一年五天。", "调休需提前申请，未休年假可折算。"]
    ):
        db.add(
            DocumentChunk(
                workspace_id=document.workspace_id,
                document_id=document.id,
                document_version_id=version.id,
                parse_task_id=task.id,
                seq=seq,
                content=content,
                char_count=len(content),
                embedding=list(vector),
                embedding_model="test-embedding",
            )
        )
    db.commit()

    yield document

    db.rollback()
    stored = db.get(Document, document.id)
    if stored is not None:
        db.delete(stored)
    db.commit()


def _run(client: TestClient, headers: dict[str, str], **overrides: object) -> dict:
    payload: dict[str, object] = {
        "query": QUERY,
        "history": HISTORY,
        "mode": "auto",
        "budget": 5,
        "quality_threshold": 0.6,
        "top_k": 5,
    }
    payload.update(overrides)
    resp = client.post("/api/v1/retrieval/rewrite-lab/run", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_full_run_executes_five_strategies_and_reconciles_totals(
    client: TestClient,
    auth_headers: dict[str, str],
    fake_runtime: list[str],
    seeded_document: Document,
) -> None:
    data = _run(client, auth_headers)

    # 决策表：每个注册策略恰好一条，HyDE / Step-back 只登记未启用
    decisions = {item["strategy"]: item["decision"] for item in data["routing"]}
    assert len(data["routing"]) == 7
    assert decisions == {
        "context": "execute",
        "entity": "execute",
        "semantic": "execute",
        "decompose": "execute",
        "multi": "execute",
        "stepback": "unavailable",
        "hyde": "unavailable",
    }
    assert [item["strategy"] for item in data["rewrites"]] == [
        "context",
        "entity",
        "semantic",
        "decompose",
        "multi",
    ]
    assert all(item["status"] == "ok" for item in data["rewrites"])

    # 每次策略调用都命中一条脚本；理解也消耗一次
    assert len(fake_runtime) == 5
    assert fake_runtime.count(prompts.QUERY_UNDERSTAND_SYSTEM) == 1

    # 工作流只包含本次实际经过的节点，首尾固定
    node_ids = [node["id"] for node in data["workflow"]]
    assert node_ids[:2] == ["understand", "baseline"]
    assert node_ids[-2:] == ["evaluate", "fuse"]
    assert "second_stage" not in node_ids
    assert set(node_ids[2:7]) == {"context", "entity", "semantic", "decompose", "multi"}
    assert "rewritten" in node_ids

    # 双路检索都落在同一篇文档的两个片段上，首轮即充分
    arms = {item["arm"]: item for item in data["arms"]}
    assert set(arms) == {"baseline", "rewritten"}
    assert arms["baseline"]["hit_count"] == 2
    assert arms["rewritten"]["hit_count"] == 2
    assert arms["baseline"]["metrics"]["docs"] == 1
    assert arms["baseline"]["queries"] == [QUERY]
    assert arms["rewritten"]["metrics"]["top1"] >= 0.99
    assert data["evaluation"]["verdict"] == "sufficient"
    assert abs(data["evaluation"]["lift"]) < 1e-6
    assert data["second_stage"]["triggered"] is False

    # 融合：两条片段两路都命中，来源完整且编号连续
    fusion = data["fusion"]
    assert fusion["before"] == 2
    assert fusion["after"] == 2
    assert [item["index"] for item in fusion["items"]] == [1, 2]
    assert all(set(item["sources"]) == {"baseline", "rewritten"} for item in fusion["items"])
    assert all(item["content"] for item in fusion["items"])

    # 成本可对账：5 次模型调用；检索次数按查询条数计，向量化按批次计
    assert data["totals"]["llm_calls"] == len(fake_runtime) == 5
    assert data["totals"]["retrieval_calls"] == 1 + len(arms["rewritten"]["queries"])
    assert data["totals"]["embed_calls"] == 2
    assert data["totals"]["elapsed_ms"] >= 0
    assert data["degraded"] is False
    assert data["warnings"] == []

    # 提示词不得随 Trace 下发
    assert prompts.REWRITE_GUARD not in str(data)


def test_trace_echoes_request_and_rejects_blank_query(
    client: TestClient,
    auth_headers: dict[str, str],
    fake_runtime: list[str],
    seeded_document: Document,
) -> None:
    data = _run(client, auth_headers, budget=2, quality_threshold=0.75, top_k=3)

    echo = data["request"]
    assert echo["query"] == QUERY
    assert echo["mode"] == "auto"
    assert echo["budget"] == 2
    assert echo["quality_threshold"] == 0.75
    assert echo["top_k"] == 3
    assert echo["history_turns"] == 1

    blank = client.post(
        "/api/v1/retrieval/rewrite-lab/run",
        json={"query": "   "},
        headers=auth_headers,
    )
    assert blank.status_code == 400
    assert blank.json()["code"] == 40001

    too_long = client.post(
        "/api/v1/retrieval/rewrite-lab/run",
        json={"query": "年" * 501},
        headers=auth_headers,
    )
    assert too_long.status_code == 400
    assert too_long.json()["code"] == 40001
