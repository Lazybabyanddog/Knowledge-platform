"""运行编排的单元测试：全链路、超时回退、二阶段、降级与只读性。

模型与检索全部替身，不连数据库、不打外部接口。
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import settings
from app.infra.llm import provider
from app.schemas.query_rewrite import HistoryTurn, RewriteRunRequest
from app.services.query_rewrite import service as service_module
from app.services.query_rewrite.service import QueryRewriteService
from app.services.retrieval_service import ChunkHit

ALL_FIVE_QUERY = "帮我看看这个《员工手册》里年假咋规定的，并且同时说明病假怎么算"
PLAIN_QUERY = "年假的天数标准"
HISTORY = [
    HistoryTurn(role="user", content="我想先了解《员工手册》的适用范围"),
    HistoryTurn(role="assistant", content="《员工手册》适用于全体正式员工。"),
]


def _hit(chunk_id: str, *, document_id: str, score: float) -> ChunkHit:
    return ChunkHit(
        chunk_id=chunk_id,
        document_id=document_id,
        title="员工手册",
        version_no=1,
        content=f"片段 {chunk_id}",
        score=score,
        heading_path=None,
        first_page_no=1,
        last_page_no=1,
        block_ids=[],
    )


class FakeRetrieval:
    def __init__(
        self,
        *,
        default: list[ChunkHit] | None = None,
        overrides=None,
        delay: float = 0.0,
    ) -> None:
        self.default = default or []
        self.overrides: dict[str, list[ChunkHit]] = overrides or {}
        self.delay = delay
        self.calls: list[list[str]] = []

    def search_many(self, principal: Any, queries: list[str], *, top_k=None, doc_type=None):
        if self.delay:
            time.sleep(self.delay)
        self.calls.append(list(queries))
        return {query: self.overrides.get(query, self.default) for query in queries}


class ExplodingDb:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"运行期间不应触碰数据库：{name}")


def _scripted(mapping: dict[str, dict[str, Any]]):
    def call(system: str, user: str) -> dict[str, Any]:
        for marker, payload in mapping.items():
            if marker in system:
                return payload
        raise provider.LLMError(f"未编排的提示词: {system[:24]}")

    return call


FULL_SCRIPT = {
    "查询分析器": {
        "intent": "确认年假与病假的规定",
        "complexity": "high",
        "entities": ["员工手册"],
        "references": [{"cue": "这个", "reason": "指代《员工手册》"}],
        "constraints": [],
        "candidate_strategies": ["context", "semantic", "decompose"],
    },
    "多轮对话的查询补全器": {"query": "《员工手册》里年假是怎么规定的", "reason": "补全指代"},
    "检索查询改写器": {"query": "员工手册 年假 规定", "reason": "去掉口语"},
    "检索规划器": {
        "sub_questions": ["年假天数标准", "病假计算方式"],
        "reason": "拆成两个假期口径",
    },
    "检索查询扩展器": {"queries": ["年假 工龄 折算", "病假 工资 比例"], "reason": "换指标口径"},
}


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "query_rewrite_max_llm_calls", 10)
    monkeypatch.setattr(settings, "query_rewrite_max_queries", 20)
    monkeypatch.setattr(settings, "query_rewrite_timeout_seconds", 20.0)
    monkeypatch.setattr(provider, "is_llm_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", _scripted(FULL_SCRIPT))
    monkeypatch.setattr(
        service_module,
        "_entity_lookup",
        lambda workspace_id: lambda keyword: [
            {"id": "entity-1", "name": "员工手册（2024 版）", "class_code": "Document"}
        ],
    )


def _service(
    retrieval: FakeRetrieval, *, graph_ready: bool = True, db: Any = None
) -> QueryRewriteService:
    return QueryRewriteService(
        db if db is not None else ExplodingDb(),
        retrieval=retrieval,  # type: ignore[arg-type]
        graph_ready=lambda: graph_ready,
    )


def _request(**kwargs: Any) -> RewriteRunRequest:
    payload: dict[str, Any] = {
        "query": ALL_FIVE_QUERY,
        "history": HISTORY,
        "budget": 5,
        "quality_threshold": 0.6,
    }
    payload.update(kwargs)
    return RewriteRunRequest(**payload)


def _principal() -> Any:
    return SimpleNamespace(user=SimpleNamespace(workspace_id="ws-1", id="user-1"), roles=[])


def test_full_run_executes_five_strategies() -> None:
    retrieval = FakeRetrieval(
        default=[
            _hit("r1", document_id="doc-1", score=0.88),
            _hit("r2", document_id="doc-2", score=0.72),
            _hit("r3", document_id="doc-3", score=0.66),
        ],
        overrides={ALL_FIVE_QUERY: [_hit("b1", document_id="doc-9", score=0.4)]},
    )
    trace = _service(retrieval).run(_principal(), _request())

    executed = [item.strategy for item in trace.routing if item.decision == "execute"]
    assert executed == ["context", "entity", "semantic", "decompose", "multi"]
    assert len(trace.routing) == 7
    assert trace.degraded is False
    assert trace.evaluation.verdict == "sufficient"
    assert trace.evaluation.lift > 0
    assert trace.second_stage.triggered is False

    # 串行改写：后一个的输入是前一个的输出
    context, entity, semantic = trace.rewrites[0], trace.rewrites[1], trace.rewrites[2]
    assert context.text_in == ALL_FIVE_QUERY
    assert entity.text_in == context.text_out
    assert semantic.text_in == entity.text_out
    assert "员工手册（2024 版）" in entity.text_out

    # enhance 类策略只产出额外查询，不改主查询
    assert trace.rewrites[3].text_out == trace.rewrites[3].text_in
    assert trace.rewrites[4].text_out == trace.rewrites[4].text_in

    assert trace.totals.llm_calls == 5
    assert trace.totals.retrieval_calls >= 2
    assert [item.index for item in trace.fusion.items] == list(
        range(1, len(trace.fusion.items) + 1)
    )
    assert {source for item in trace.fusion.items for source in item.sources} == {
        "baseline",
        "rewritten",
    }


def test_timeout_falls_back_to_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "query_rewrite_timeout_seconds", 0.05)
    # 对照组检索本身就把时间用光：预算最小时限 0.1s，这里 0.15s 必定越过
    retrieval = FakeRetrieval(default=[_hit("b1", document_id="doc-1", score=0.5)], delay=0.15)

    trace = _service(retrieval).run(_principal(), _request())

    assert trace.degraded is True
    assert any("超时" in warning for warning in trace.warnings)
    assert trace.arms[0].arm == "baseline"
    assert trace.arms[1].queries == []
    assert trace.evaluation.verdict == "insufficient"
    assert any(node.id == "rewritten" and node.status == "skipped" for node in trace.workflow)
    assert trace.fusion.items  # 对照组结果照常返回


def test_baseline_runs_even_after_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """理解阶段把时间用光也不能挡住对照组：否则超时直接 500，连兜底结果都没有。"""
    monkeypatch.setattr(settings, "query_rewrite_timeout_seconds", 0.05)

    def _slow(system: str, user: str) -> dict[str, Any]:
        time.sleep(0.15)
        return FULL_SCRIPT["查询分析器"]

    monkeypatch.setattr(provider, "chat_json", _slow)
    retrieval = FakeRetrieval(default=[_hit("b1", document_id="doc-1", score=0.5)])

    trace = _service(retrieval).run(_principal(), _request())

    assert retrieval.calls[0] == [ALL_FIVE_QUERY]
    assert trace.arms[0].metrics.top1 == 0.5
    assert trace.arms[1].queries == []
    assert trace.degraded is True
    assert any("超时" in warning for warning in trace.warnings)


def test_second_stage_triggers_multi_query() -> None:
    retrieval = FakeRetrieval(
        default=[
            _hit("r1", document_id="doc-1", score=0.91),
            _hit("r2", document_id="doc-2", score=0.77),
        ],
        overrides={PLAIN_QUERY: [_hit("b1", document_id="doc-9", score=0.5)]},
    )
    trace = _service(retrieval).run(_principal(), _request(query=PLAIN_QUERY, history=[], budget=1))

    assert trace.second_stage.triggered is True
    assert trace.second_stage.actions == ["multi"]
    assert trace.second_stage.new_chunks >= 1
    assert trace.evaluation.verdict == "sufficient"
    assert trace.warnings == []


def test_second_stage_respects_allowlist() -> None:
    retrieval = FakeRetrieval(default=[_hit("b1", document_id="doc-9", score=0.5)])
    trace = _service(retrieval).run(
        _principal(),
        _request(query=PLAIN_QUERY, history=[], budget=1, allowlist=["semantic"]),
    )

    assert trace.second_stage.triggered is False
    assert "Allowlist" in trace.second_stage.reason
    # understanding 1 次，其余策略都没开
    assert trace.totals.llm_calls == 1


def test_without_llm_only_rules_and_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider, "is_llm_configured", lambda: False)
    retrieval = FakeRetrieval(default=[_hit("b1", document_id="doc-1", score=0.5)])

    trace = _service(retrieval).run(_principal(), _request())

    assert trace.totals.llm_calls == 0
    assert any("未配置 LLM" in warning for warning in trace.warnings)
    assert all(
        item.decision in {"blocked_by_dependency", "not_triggered", "unavailable"}
        for item in trace.routing
    )
    assert trace.arms[0].metrics.top1 == 0.5


def test_trace_never_contains_raw_prompts() -> None:
    retrieval = FakeRetrieval(default=[_hit("b1", document_id="doc-1", score=0.5)])
    trace = _service(retrieval).run(_principal(), _request())
    payload = trace.model_dump_json()

    assert "禁止新增" not in payload
    assert "只输出 JSON" not in payload


def test_equivalent_rewrite_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    query = "帮我看看年假的天数标准"
    script = dict(FULL_SCRIPT)
    script["检索查询改写器"] = {"query": query, "reason": "无改动"}
    monkeypatch.setattr(provider, "chat_json", _scripted(script))

    retrieval = FakeRetrieval(default=[_hit("b1", document_id="doc-1", score=0.5)])
    trace = _service(retrieval).run(
        _principal(), _request(query=query, history=[], budget=2, allowlist=["semantic"])
    )

    semantic = next(item for item in trace.rewrites if item.strategy == "semantic")
    assert semantic.status == "skipped"
    assert semantic.text_out == semantic.text_in == query
    # 等价输出不产生额外检索：只有对照组 + 原查询那一次
    assert trace.arms[1].queries == [query]


def test_graph_unavailable_marks_entity_unavailable() -> None:
    retrieval = FakeRetrieval(default=[_hit("b1", document_id="doc-1", score=0.5)])
    trace = _service(retrieval, graph_ready=False).run(_principal(), _request())

    entity = next(item for item in trace.routing if item.strategy == "entity")
    assert entity.decision == "unavailable"
    assert "图谱" in entity.reason
    assert trace.totals.llm_calls >= 1


def test_run_keeps_database_untouched() -> None:
    retrieval = FakeRetrieval(default=[_hit("b1", document_id="doc-1", score=0.5)])
    # ExplodingDb 一旦被访问就抛错：能跑完即证明运行没有写入
    trace = _service(retrieval, db=ExplodingDb()).run(_principal(), _request())
    assert trace.request.query == ALL_FIVE_QUERY
