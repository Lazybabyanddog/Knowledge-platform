"""召回质量评估。

没有标注数据，任何"绝对质量分"都是编的。这里只报可观测量与相对对照组的增益：
改写有没有用，必须能被两路检索的实际相似度复算出来。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.retrieval_service import ChunkHit

SUFFICIENT = "sufficient"
INSUFFICIENT = "insufficient"


@dataclass(slots=True)
class ArmMetrics:
    top1: float
    above_tau: int
    docs: int


@dataclass(slots=True)
class Evaluation:
    verdict: str
    lift: float
    reason: str


def metrics(hits: list[ChunkHit], threshold: float) -> ArmMetrics:
    if not hits:
        return ArmMetrics(top1=0.0, above_tau=0, docs=0)
    return ArmMetrics(
        top1=round(max(hit.score for hit in hits), 6),
        above_tau=sum(1 for hit in hits if hit.score >= threshold),
        docs=len({hit.document_id for hit in hits}),
    )


def evaluate(
    baseline: ArmMetrics,
    rewritten: ArmMetrics,
    *,
    threshold: float,
    min_evidence: int,
) -> Evaluation:
    lift = round(rewritten.top1 - baseline.top1, 6)
    enough_evidence = rewritten.above_tau >= max(1, min_evidence)
    above_threshold = rewritten.top1 >= threshold
    if enough_evidence and above_threshold:
        return Evaluation(
            verdict=SUFFICIENT,
            lift=lift,
            reason=(
                f"改写组 {rewritten.above_tau} 条片段达到阈值 {threshold:g}，"
                f"最高分 {rewritten.top1:g}"
            ),
        )
    details: list[str] = []
    if not enough_evidence:
        details.append(
            f"达到阈值的片段只有 {rewritten.above_tau} 条（要求 {max(1, min_evidence)} 条）"
        )
    if not above_threshold:
        details.append(
            f"最高分 {rewritten.top1:g} 低于阈值 {threshold:g}"
        )
    return Evaluation(
        verdict=INSUFFICIENT,
        lift=lift,
        reason="；".join(details),
    )


__all__ = ["ArmMetrics", "Evaluation", "INSUFFICIENT", "SUFFICIENT", "evaluate", "metrics"]
