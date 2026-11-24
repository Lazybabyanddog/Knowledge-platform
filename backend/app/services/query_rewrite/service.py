"""运行编排。

顺序刻意如此：先做对照组检索，再花预算改写。这样超时或预算耗尽时总有真实结果可以返回，
而不是抛错或返回空列表。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import Principal
from app.infra.llm import provider
from app.schemas.query_rewrite import (
    ArmMetricsOut,
    ArmOut,
    DecisionOut,
    EvaluationOut,
    FusedChunkOut,
    FusionOut,
    NodeOut,
    ReferenceOut,
    RequestEcho,
    RewriteOut,
    RewriteRunRequest,
    RewriteTrace,
    SecondStageOut,
    TotalsOut,
    UnderstandingOut,
)
from app.services.query_rewrite.budget import Budget, BudgetExceeded
from app.services.query_rewrite.evaluation import (
    INSUFFICIENT,
    ArmMetrics,
    Evaluation,
    evaluate,
    metrics,
)
from app.services.query_rewrite.fusion import fuse
from app.services.query_rewrite.router import EXECUTE, Decision, route
from app.services.query_rewrite.specs import DEFAULT_ALLOWLIST, get_spec
from app.services.query_rewrite.strategies import (
    STATUS_DEGRADED,
    STATUS_OK,
    Outcome,
    StrategyRunner,
)
from app.services.query_rewrite.understanding import understand
from app.services.retrieval_service import ChunkHit, RetrievalService

logger = logging.getLogger(__name__)

CONTENT_LIMIT = 600
BASELINE_ARM = "baseline"
REWRITTEN_ARM = "rewritten"


class QueryRewriteService:
    def __init__(
        self,
        db: Session,
        *,
        retrieval: RetrievalService | None = None,
        graph_ready: Callable[[], bool] | None = None,
    ) -> None:
        self.db = db
        self.retrieval = retrieval or RetrievalService(db)
        self._graph_ready = graph_ready or _graph_available

    def run(self, principal: Principal, request: RewriteRunRequest) -> RewriteTrace:
        started = time.perf_counter()
        budget = Budget(
            timeout_seconds=settings.query_rewrite_timeout_seconds,
            max_llm_calls=settings.query_rewrite_max_llm_calls,
            max_queries=settings.query_rewrite_max_queries,
        )
        allowlist = (
            list(request.allowlist) if request.allowlist is not None else list(DEFAULT_ALLOWLIST)
        )
        top_k = request.top_k or settings.retrieval_top_k
        threshold = request.quality_threshold
        min_evidence = settings.query_rewrite_min_evidence
        history = [turn.model_dump() for turn in request.history]
        warnings: list[str] = []

        llm_ready = provider.is_llm_configured()
        graph_ready = self._graph_ready()
        # manual 只走规则，不花一次模型调用做理解
        use_llm_understanding = llm_ready and request.mode == "auto"
        understanding = understand(
            request.query,
            history,
            call_llm=(
                _budgeted(provider.chat_json, budget) if use_llm_understanding else None
            ),
        )
        if understanding.degraded and use_llm_understanding:
            warnings.append("查询理解模型不可用，已退回规则特征")

        decisions = route(
            understanding,
            allowlist=allowlist,
            budget=request.budget,
            llm_ready=llm_ready,
            graph_ready=graph_ready,
        )

        nodes: list[NodeOut] = [
            NodeOut(
                id="understand",
                label="查询理解",
                kind="understand",
                status="degraded" if understanding.degraded else "ok",
            )
        ]

        # 对照组先跑：它是超时/预算耗尽时唯一的兜底结果，因此不吃检索预算也不受截止时间限制
        try:
            budget.take_queries(1)
        except BudgetExceeded:
            pass
        baseline_map = self.retrieval.search_many(
            principal, [request.query], top_k=top_k, doc_type=request.doc_type
        )
        budget.mark_retrieval(1)
        baseline_hits = _flatten(baseline_map)
        nodes.append(
            NodeOut(
                id=BASELINE_ARM,
                label="对照组检索",
                kind="retrieve",
                depends_on=["understand"],
                status="ok",
            )
        )

        runner = StrategyRunner(
            budget=budget,
            call_llm=provider.chat_json if llm_ready else None,
            entity_lookup=_entity_lookup(principal.user.workspace_id) if graph_ready else None,
        )

        current = request.query
        rewrites: list[RewriteOut] = []
        executed: list[str] = []
        outcomes: list[Outcome] = []
        degraded = False
        timed_out = False
        prev_node = BASELINE_ARM

        for decision in decisions:
            if decision.decision != EXECUTE:
                continue
            if budget.expired():
                timed_out = True
                warnings.append(f"运行超时，{decision.label} 及后续策略未执行")
                break
            spec = get_spec(decision.strategy)
            outcome = runner.execute(
                spec, current, history=history, understanding=understanding
            )
            if spec.kind == "text" and outcome.status == STATUS_OK:
                current = outcome.text_out
            outcomes.append(outcome)
            executed.append(spec.code)
            rewrites.append(
                RewriteOut(
                    strategy=spec.code,
                    label=spec.label,
                    status=outcome.status,
                    text_in=outcome.text_in,
                    text_out=outcome.text_out,
                    reason=outcome.reason,
                )
            )
            nodes.append(
                NodeOut(
                    id=spec.code,
                    label=spec.label,
                    kind=spec.kind,
                    depends_on=[prev_node],
                    status=outcome.status,
                    elapsed_ms=outcome.elapsed_ms,
                )
            )
            prev_node = spec.code
            if outcome.status == STATUS_DEGRADED:
                degraded = True
                warnings.append(f"{spec.label} 降级：{outcome.reason}")

        rewrite_queries: list[str] = []
        if not timed_out:
            extras = [query for outcome in outcomes for query in outcome.extra_queries]
            rewrite_queries = _dedupe([current, *extras])
            try:
                budget.take_queries(len(rewrite_queries))
            except BudgetExceeded as exc:
                if budget.expired():
                    timed_out = True
                    rewrite_queries = []
                    warnings.append("运行超时，改写组检索已取消，返回对照组结果")
                else:
                    allowed = max(0, budget.max_queries - budget.queries_used)
                    rewrite_queries = rewrite_queries[:allowed]
                    warnings.append(f"{exc}，改写组只检索前 {len(rewrite_queries)} 条查询")
            if not rewrite_queries:
                timed_out = True
                warnings.append("改写组没有可用的检索查询，已回退到对照组结果")

        rewrite_map: dict[str, list[ChunkHit]] = {}
        if rewrite_queries:
            rewrite_started = time.perf_counter()
            rewrite_map = self.retrieval.search_many(
                principal, rewrite_queries, top_k=top_k, doc_type=request.doc_type
            )
            budget.mark_retrieval(len(rewrite_queries))
            nodes.append(
                NodeOut(
                    id=REWRITTEN_ARM,
                    label="改写组检索",
                    kind="retrieve",
                    depends_on=[prev_node],
                    status="ok",
                    elapsed_ms=int((time.perf_counter() - rewrite_started) * 1000),
                )
            )
        else:
            nodes.append(
                NodeOut(
                    id=REWRITTEN_ARM,
                    label="改写组检索",
                    kind="retrieve",
                    depends_on=[prev_node],
                    status="skipped",
                )
            )

        rewrite_hits = _flatten(rewrite_map)
        base_metrics = metrics(baseline_hits, threshold)
        rewrite_metrics = metrics(rewrite_hits, threshold)
        if rewrite_queries:
            evaluation = evaluate(
                base_metrics, rewrite_metrics, threshold=threshold, min_evidence=min_evidence
            )
        else:
            evaluation = Evaluation(
                verdict=INSUFFICIENT, lift=0.0, reason="改写阶段未完成，只有对照组结果"
            )
        nodes.append(
            NodeOut(
                id="evaluate",
                label="质量评估",
                kind="evaluate",
                depends_on=[BASELINE_ARM, REWRITTEN_ARM],
                status="ok",
            )
        )

        second_stage = SecondStageOut()
        if not timed_out and evaluation.verdict == INSUFFICIENT:
            second_stage, evaluation, rewrite_map, rewrite_metrics = self._second_stage(
                principal=principal,
                request=request,
                runner=runner,
                budget=budget,
                allowlist=allowlist,
                executed=executed,
                current=current,
                history=history,
                understanding=understanding,
                baseline=base_metrics,
                threshold=threshold,
                min_evidence=min_evidence,
                rewrite_map=rewrite_map,
                top_k=top_k,
            )
            if second_stage.triggered:
                nodes.append(
                    NodeOut(
                        id="second_stage",
                        label="二阶段补充",
                        kind="second_stage",
                        depends_on=["evaluate"],
                        status="ok",
                    )
                )
            if evaluation.verdict == INSUFFICIENT:
                warnings.append(f"补充检索后仍不足：{evaluation.reason}")
        elif evaluation.verdict == INSUFFICIENT:
            second_stage.reason = "改写阶段未完成，未做补充检索"

        rewrite_hits = _flatten(rewrite_map)
        fusion_result = fuse(
            [(BASELINE_ARM, baseline_hits), (REWRITTEN_ARM, rewrite_hits)],
            k=settings.query_rewrite_rrf_k,
            top_k=top_k,
        )
        nodes.append(
            NodeOut(
                id="fuse",
                label="融合去重",
                kind="fuse",
                depends_on=["evaluate"] + (["second_stage"] if second_stage.triggered else []),
                status="ok",
            )
        )

        if not llm_ready:
            warnings.append("未配置 LLM，只执行规则理解与对照组检索")

        return RewriteTrace(
            request=RequestEcho(
                query=request.query,
                mode=request.mode,
                allowlist=allowlist,
                budget=request.budget,
                quality_threshold=threshold,
                top_k=top_k,
                doc_type=request.doc_type,
                history_turns=len(history),
            ),
            understanding=UnderstandingOut(
                intent=understanding.intent,
                complexity=understanding.complexity,
                entities=understanding.entities,
                references=[
                    ReferenceOut(cue=ref.cue, resolved=ref.resolved, reason=ref.reason)
                    for ref in understanding.references
                ],
                constraints=understanding.constraints,
                candidate_strategies=understanding.candidate_strategies,
                source=understanding.source,
                degraded=understanding.degraded,
            ),
            routing=[_decision_out(item) for item in decisions],
            workflow=nodes,
            rewrites=rewrites,
            arms=[
                _arm_out(BASELINE_ARM, [request.query], baseline_hits, base_metrics),
                _arm_out(
                    REWRITTEN_ARM,
                    list(rewrite_map.keys()) or rewrite_queries,
                    rewrite_hits,
                    rewrite_metrics,
                ),
            ],
            evaluation=EvaluationOut(
                verdict=evaluation.verdict, lift=evaluation.lift, reason=evaluation.reason
            ),
            second_stage=second_stage,
            fusion=FusionOut(
                method="rrf",
                k=settings.query_rewrite_rrf_k,
                before=fusion_result.before,
                after=fusion_result.after,
                items=[
                    _fused_out(index, item)
                    for index, item in enumerate(fusion_result.items, start=1)
                ],
            ),
            totals=TotalsOut(
                llm_calls=budget.llm_calls,
                embed_calls=budget.embed_calls,
                retrieval_calls=budget.retrieval_calls,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            ),
            degraded=degraded or timed_out or (understanding.degraded and use_llm_understanding),
            warnings=warnings,
        )

    def _second_stage(
        self,
        *,
        principal: Principal,
        request: RewriteRunRequest,
        runner: StrategyRunner,
        budget: Budget,
        allowlist: list[str],
        executed: list[str],
        current: str,
        history: list[dict[str, str]],
        understanding: Any,
        baseline: ArmMetrics,
        threshold: float,
        min_evidence: int,
        rewrite_map: dict[str, list[ChunkHit]],
        top_k: int,
    ) -> tuple[SecondStageOut, Evaluation, dict[str, list[ChunkHit]], ArmMetrics]:
        stage = SecondStageOut()
        rewrite_hits = _flatten(rewrite_map)
        metrics_now = metrics(rewrite_hits, threshold)
        evaluation = evaluate(baseline, metrics_now, threshold=threshold, min_evidence=min_evidence)

        if "multi" not in allowlist:
            stage.reason = "Allowlist 未开放 Multi-Query，未做补充检索，结果按低置信度返回"
            return stage, evaluation, rewrite_map, metrics_now
        if "multi" in executed:
            stage.reason = "Multi-Query 已在首轮执行，为避免循环不再重复扩展"
            return stage, evaluation, rewrite_map, metrics_now
        if budget.expired():
            stage.reason = "运行已超时，未做补充检索"
            return stage, evaluation, rewrite_map, metrics_now

        spec = get_spec("multi")
        outcome = runner.execute(spec, current, history=history, understanding=understanding)
        if outcome.status != STATUS_OK or not outcome.extra_queries:
            stage.reason = f"补充策略未产出有效查询：{outcome.reason}"
            return stage, evaluation, rewrite_map, metrics_now

        new_queries = [query for query in outcome.extra_queries if query not in rewrite_map]
        if not new_queries:
            stage.reason = "扩展查询与首轮重复"
            return stage, evaluation, rewrite_map, metrics_now

        try:
            budget.take_queries(len(new_queries))
        except BudgetExceeded as exc:
            stage.reason = f"预算不足以补充检索：{exc}"
            return stage, evaluation, rewrite_map, metrics_now

        before_ids = {hit.chunk_id for hit in rewrite_hits}
        extra_map = self.retrieval.search_many(
            principal, new_queries, top_k=top_k, doc_type=request.doc_type
        )
        budget.mark_retrieval(len(new_queries))
        merged = dict(rewrite_map)
        for query, hits in extra_map.items():
            merged.setdefault(query, hits)

        new_hits = _flatten(extra_map)
        stage.triggered = True
        stage.actions = ["multi"]
        stage.new_chunks = len({hit.chunk_id for hit in new_hits} - before_ids)
        stage.reason = outcome.reason or "首轮不足，追加 Multi-Query 扩展"

        metrics_after = metrics(_flatten(merged), threshold)
        evaluation = evaluate(
            baseline, metrics_after, threshold=threshold, min_evidence=min_evidence
        )
        return stage, evaluation, merged, metrics_after


def _budgeted(
    call: Callable[[str, str], dict[str, Any]], budget: Budget
) -> Callable[[str, str], dict[str, Any]]:
    """让理解用的模型调用也计入预算；超预算降级为普通模型错误，由理解层退回规则。"""

    def invoke(system: str, user: str) -> dict[str, Any]:
        try:
            budget.take_llm()
        except BudgetExceeded as exc:
            raise provider.LLMError(str(exc)) from exc
        return call(system, user)

    return invoke


def _graph_available() -> bool:
    try:
        from app.infra.graph.driver import check_connectivity

        return bool(check_connectivity())
    except Exception:  # noqa: BLE001 - 图谱不可用只是少一个策略
        return False


def _entity_lookup(workspace_id: str) -> Callable[[str], list[dict[str, Any]]]:
    # 图谱驱动要求字符串参数：直接传 UUID 会被 Bolt 拒掉
    workspace_id = str(workspace_id)

    def lookup(keyword: str) -> list[dict[str, Any]]:
        from app.infra.graph.entity_repository import EntityRepository

        items, _total = EntityRepository().list_entities(
            workspace_id, keyword=keyword, limit=5
        )
        return items

    return lookup


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = (item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _flatten(mapping: dict[str, list[ChunkHit]]) -> list[ChunkHit]:
    best: dict[str, ChunkHit] = {}
    for hits in mapping.values():
        for hit in hits:
            existing = best.get(hit.chunk_id)
            if existing is None or hit.score > existing.score:
                best[hit.chunk_id] = hit
    return sorted(best.values(), key=lambda hit: hit.score, reverse=True)


def _arm_out(
    name: str, queries: list[str], hits: list[ChunkHit], arm_metrics: ArmMetrics
) -> ArmOut:
    return ArmOut(
        arm=name,
        queries=queries,
        metrics=ArmMetricsOut(
            top1=arm_metrics.top1,
            above_tau=arm_metrics.above_tau,
            docs=arm_metrics.docs,
        ),
        hit_count=len(hits),
    )


def _decision_out(decision: Decision) -> DecisionOut:
    return DecisionOut(
        strategy=decision.strategy,
        label=decision.label,
        kind=decision.kind,
        decision=decision.decision,
        evidence=decision.evidence,
        reason=decision.reason,
    )


def _fused_out(index: int, item: Any) -> FusedChunkOut:
    content = item.hit.content or ""
    truncated = len(content) > CONTENT_LIMIT
    return FusedChunkOut(
        index=index,
        chunk_id=item.chunk_id,
        document_id=item.hit.document_id,
        title=item.hit.title,
        version_no=item.hit.version_no,
        content=content[:CONTENT_LIMIT],
        truncated=truncated,
        heading_path=item.hit.heading_path,
        score=item.score,
        sources=item.sources,
    )


__all__ = ["QueryRewriteService"]
