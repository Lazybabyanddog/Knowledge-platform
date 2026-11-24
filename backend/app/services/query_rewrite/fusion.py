"""多路命中融合：按片段去重 + RRF 排序。

对外展示的 ``score`` 仍是该片段在各路中的最大余弦相似度——
用户理解的是"有多相关"，融合分只用来决定顺序。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.retrieval_service import ChunkHit


@dataclass(slots=True)
class FusedChunk:
    chunk_id: str
    hit: ChunkHit
    sources: list[str] = field(default_factory=list)
    rrf: float = 0.0
    score: float = 0.0


@dataclass(slots=True)
class FusionResult:
    items: list[FusedChunk]
    before: int
    after: int


def fuse(
    arms: list[tuple[str, list[ChunkHit]]],
    *,
    k: int,
    top_k: int,
) -> FusionResult:
    merged: dict[str, FusedChunk] = {}
    for arm_name, hits in arms:
        # RRF 的 rank 在每条臂内部独立计算，重复命中就累加
        for rank, hit in enumerate(hits, start=1):
            item = merged.get(hit.chunk_id)
            if item is None:
                item = FusedChunk(chunk_id=hit.chunk_id, hit=hit, score=hit.score)
                merged[hit.chunk_id] = item
            if arm_name not in item.sources:
                item.sources.append(arm_name)
            item.rrf += 1.0 / (k + rank)
            if hit.score > item.score:
                item.score = hit.score
                item.hit = hit

    ordered = sorted(merged.values(), key=lambda item: item.rrf, reverse=True)
    for item in ordered:
        item.rrf = round(item.rrf, 6)
    before = len(merged)
    items = ordered[: max(1, top_k)]
    return FusionResult(items=items, before=before, after=len(items))


__all__ = ["FusedChunk", "FusionResult", "fuse"]
