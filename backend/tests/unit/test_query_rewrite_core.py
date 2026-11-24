"""改写内核的单元测试：注册表、理解、路由、评估、融合、批量检索。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.query_rewrite import evaluation, fusion, router
from app.services.query_rewrite import specs as strategy_specs
from app.services.query_rewrite.understanding import understand
from app.services.retrieval_service import ChunkHit, RetrievalService


def _hit(chunk_id: str, *, document_id: str = "doc-1", score: float = 0.9) -> ChunkHit:
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


def _understanding(query: str, history: list[dict[str, str]] | None = None, llm=None):
    return understand(query, history or [], call_llm=llm)


def _decisions(understanding, *, allowlist=None, budget=5, llm_ready=True, graph_ready=True):
    default = strategy_specs.DEFAULT_ALLOWLIST
    return router.route(
        understanding,
        allowlist=list(allowlist) if allowlist is not None else list(default),
        budget=budget,
        llm_ready=llm_ready,
        graph_ready=graph_ready,
    )


def _decision(decisions, code: str):
    return next(item for item in decisions if item.strategy == code)


# ---- 注册表 ----


def test_registry_has_five_enabled_and_two_placeholders() -> None:
    assert len(strategy_specs.STRATEGIES) == 7
    enabled = [spec.code for spec in strategy_specs.STRATEGIES if spec.enabled]
    assert enabled == ["context", "entity", "semantic", "decompose", "multi"]
    assert strategy_specs.DEFAULT_ALLOWLIST == tuple(enabled)
    assert [spec.priority for spec in strategy_specs.STRATEGIES] == list(range(1, 8))


# ---- 理解 ----


def test_reference_not_resolved_without_history() -> None:
    result = _understanding("这个案例的处理流程是什么")
    assert result.references
    assert result.references[0].resolved is False

    context = _decision(_decisions(result), "context")
    assert context.decision == router.NOT_TRIGGERED
    assert "历史" in context.reason
    assert context.evidence


def test_reference_resolved_with_history() -> None:
    history = [
        {"role": "user", "content": "指导性案例15号的裁判要点"},
        {"role": "assistant", "content": "……"},
    ]
    result = _understanding("这个案例适用什么规则", history)
    assert all(ref.resolved for ref in result.references)
    assert _decision(_decisions(result), "context").decision == router.EXECUTE


def test_llm_failure_falls_back_to_rules() -> None:
    from app.infra.llm import provider

    def boom(_system: str, _user: str) -> dict[str, Any]:
        raise provider.LLMError("offline")

    result = _understanding("帮我看看年假怎么算", llm=boom)
    assert result.degraded is True
    assert result.source == "rules_fallback"
    assert result.candidate_strategies == []
    assert result.signals.colloquial_cues


# ---- 路由 ----


def test_model_candidates_never_trigger_execution() -> None:
    def llm(_system: str, _user: str) -> dict[str, Any]:
        return {"candidate_strategies": ["multi", "decompose"], "entities": [], "references": []}

    result = _understanding("年假多少天", llm=llm)
    assert "multi" in result.candidate_strategies

    decisions = _decisions(result)
    assert _decision(decisions, "multi").decision == router.NOT_TRIGGERED
    assert _decision(decisions, "decompose").decision == router.NOT_TRIGGERED


def test_allowlist_blocks_with_evidence() -> None:
    result = _understanding("帮我看看年假是怎么规定的")
    decisions = _decisions(result, allowlist=["multi"])
    semantic = _decision(decisions, "semantic")
    assert semantic.decision == router.BLOCKED_ALLOWLIST
    assert semantic.evidence
    assert "允许列表" in semantic.reason


def test_budget_truncates_by_priority() -> None:
    result = _understanding("《报销制度》里差旅和招待标准分别是多少，同时还要说明审批时限")
    # entity 优先级高于 decompose，预算只够两条时应当先保 entity、decompose，再拦 multi
    decisions = _decisions(result, budget=2)
    executed = [item.strategy for item in decisions if item.decision == router.EXECUTE]
    assert executed == ["entity", "decompose"]
    assert _decision(decisions, "multi").decision == router.BLOCKED_BUDGET


def test_disabled_strategy_is_unavailable_without_side_effects() -> None:
    decisions = _decisions(_understanding("年假怎么算"))
    for code in ("hyde", "stepback"):
        item = _decision(decisions, code)
        assert item.decision == router.UNAVAILABLE
        assert "未启用" in item.reason


def test_missing_llm_blocks_dependent_strategy() -> None:
    result = _understanding("帮我看看年假怎么算")
    semantic = _decision(_decisions(result, llm_ready=False), "semantic")
    assert semantic.decision == router.BLOCKED_DEPENDENCY
    assert "模型" in semantic.reason


def test_missing_graph_marks_entity_unavailable() -> None:
    result = _understanding("《员工手册》里年假怎么算")
    entity = _decision(_decisions(result, graph_ready=False), "entity")
    assert entity.decision == router.UNAVAILABLE
    assert "图谱" in entity.reason


def test_every_strategy_has_exactly_one_decision() -> None:
    decisions = _decisions(_understanding("年假怎么算"))
    assert len(decisions) == len(strategy_specs.STRATEGIES)
    assert len({item.strategy for item in decisions}) == len(decisions)


# ---- 评估 ----


def test_metrics_from_real_scores() -> None:
    hits = [_hit("a", score=0.81), _hit("b", document_id="doc-2", score=0.65), _hit("c", score=0.4)]
    arm = evaluation.metrics(hits, 0.6)
    assert arm.top1 == 0.81
    assert arm.above_tau == 2
    assert arm.docs == 2


def test_sufficient_requires_evidence_and_top1() -> None:
    baseline = evaluation.ArmMetrics(top1=0.5, above_tau=0, docs=0)
    just_enough = evaluation.ArmMetrics(top1=0.6, above_tau=2, docs=1)
    short_of_evidence = evaluation.ArmMetrics(top1=0.9, above_tau=1, docs=1)

    verdict = evaluation.evaluate(baseline, just_enough, threshold=0.6, min_evidence=2)
    assert verdict.verdict == evaluation.SUFFICIENT
    assert verdict.lift == pytest.approx(0.1)

    short = evaluation.evaluate(baseline, short_of_evidence, threshold=0.6, min_evidence=2)
    assert short.verdict == evaluation.INSUFFICIENT
    assert "片段" in short.reason


# ---- 融合 ----


def test_fusion_dedupes_and_keeps_sources() -> None:
    shared = _hit("shared", score=0.7)
    result = fusion.fuse(
        [
            ("baseline", [shared, _hit("base-only", score=0.6)]),
            ("rewritten", [_hit("shared", score=0.82), _hit("rewrite-only", score=0.9)]),
        ],
        k=60,
        top_k=6,
    )
    ids = [item.chunk_id for item in result.items]
    assert ids.count("shared") == 1
    assert result.before == 3
    assert result.after == 3

    shared_item = next(item for item in result.items if item.chunk_id == "shared")
    assert shared_item.sources == ["baseline", "rewritten"]
    assert shared_item.score == 0.82
    assert shared_item.rrf > 1 / 61

    # 多路命中应排在只被单路命中的片段之前
    assert result.items[0].chunk_id == "shared"


# ---- 批量检索 ----


class _EmptyResult:
    def all(self) -> list[Any]:
        return []


class _RecordingDb:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    def execute(self, statement: Any) -> _EmptyResult:
        self.statements.append(statement)
        return _EmptyResult()


def _principal() -> Any:
    return SimpleNamespace(
        user=SimpleNamespace(workspace_id="ws-1", id="user-1"),
        roles=["viewer"],
        has_role=lambda *codes: False,
    )


def test_search_many_embeds_once_and_keeps_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import retrieval_service as module

    calls: list[list[str]] = []

    def fake_embed(texts: list[str]) -> list[list[float]]:
        calls.append(list(texts))
        return [[0.1, 0.2] for _ in texts]

    monkeypatch.setattr(module.provider, "is_embedding_configured", lambda: True)
    monkeypatch.setattr(module.provider, "embed_texts", fake_embed)

    db = _RecordingDb()
    service = RetrievalService(db)  # type: ignore[arg-type]
    result = service.search_many(_principal(), ["问题一", "问题二", "问题一"], top_k=5)

    assert calls == [["问题一", "问题二"]]
    assert set(result) == {"问题一", "问题二"}

    selects = [item for item in db.statements if hasattr(item, "column_descriptions")]
    assert len(selects) == 2
    # 两次查询只有向量不同，过滤条件必须完全一致
    assert str(selects[0]) == str(selects[1])
    assert "workspace_id" in str(selects[0])


# ---- 请求契约与接口守卫 ----


def test_request_rejects_blank_query() -> None:
    from pydantic import ValidationError

    from app.schemas.query_rewrite import RewriteRunRequest

    with pytest.raises(ValidationError):
        RewriteRunRequest(query="   ")
    with pytest.raises(ValidationError):
        RewriteRunRequest(query="年" * 501)


def test_request_filters_unknown_strategy_codes() -> None:
    from app.schemas.query_rewrite import RewriteRunRequest

    payload = RewriteRunRequest(
        query="年假怎么算", allowlist=["semantic", "hyde", "not-a-strategy"]
    )
    assert payload.allowlist == ["semantic", "hyde"]


def test_endpoint_requires_embedding_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1.retrieval import rewrite_lab_run
    from app.core.exceptions import ServiceUnavailableError
    from app.schemas.query_rewrite import RewriteRunRequest
    from app.services import retrieval_service as module

    monkeypatch.setattr(module.provider, "is_embedding_configured", lambda: False)
    with pytest.raises(ServiceUnavailableError):
        rewrite_lab_run(RewriteRunRequest(query="年假怎么算"), _principal(), None)
