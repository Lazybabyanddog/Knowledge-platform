"""改写实验室的请求与 Trace 契约。

Trace 是内核唯一出口：前端渲它，未来 `/ask` 只消费它末尾的融合结果。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.services.query_rewrite.specs import SPECS_BY_CODE


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class RewriteRunRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=10)
    mode: Literal["auto", "manual"] = "auto"
    allowlist: list[str] | None = None
    budget: int = Field(default=3, ge=1, le=7)
    quality_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=1, le=20)
    doc_type: str | None = Field(default=None, max_length=40)

    @field_validator("query")
    @classmethod
    def _require_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("查询不能为空")
        return text

    @field_validator("allowlist")
    @classmethod
    def _known_codes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [code for code in dict.fromkeys(value) if code in SPECS_BY_CODE]


class RequestEcho(BaseModel):
    query: str
    mode: str
    allowlist: list[str]
    budget: int
    quality_threshold: float
    top_k: int
    doc_type: str | None
    history_turns: int


class ReferenceOut(BaseModel):
    cue: str
    resolved: bool
    reason: str = ""


class UnderstandingOut(BaseModel):
    intent: str
    complexity: str
    entities: list[str] = Field(default_factory=list)
    references: list[ReferenceOut] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    candidate_strategies: list[str] = Field(default_factory=list)
    source: str
    degraded: bool = False


class DecisionOut(BaseModel):
    strategy: str
    label: str
    kind: str
    decision: str
    evidence: list[str] = Field(default_factory=list)
    reason: str = ""


class NodeOut(BaseModel):
    id: str
    label: str
    kind: str
    depends_on: list[str] = Field(default_factory=list)
    status: str
    elapsed_ms: int = 0


class RewriteOut(BaseModel):
    strategy: str
    label: str
    status: str
    text_in: str
    text_out: str
    reason: str = ""


class ArmMetricsOut(BaseModel):
    top1: float = 0.0
    above_tau: int = 0
    docs: int = 0


class ArmOut(BaseModel):
    arm: str
    queries: list[str] = Field(default_factory=list)
    metrics: ArmMetricsOut
    hit_count: int = 0


class EvaluationOut(BaseModel):
    verdict: str
    lift: float
    reason: str = ""


class SecondStageOut(BaseModel):
    triggered: bool = False
    actions: list[str] = Field(default_factory=list)
    new_chunks: int = 0
    reason: str = ""


class FusedChunkOut(BaseModel):
    index: int
    chunk_id: str
    document_id: str
    title: str
    version_no: int
    content: str
    truncated: bool = False
    heading_path: str | None = None
    score: float
    sources: list[str] = Field(default_factory=list)


class FusionOut(BaseModel):
    method: str = "rrf"
    k: int
    before: int = 0
    after: int = 0
    items: list[FusedChunkOut] = Field(default_factory=list)


class TotalsOut(BaseModel):
    llm_calls: int = 0
    embed_calls: int = 0
    retrieval_calls: int = 0
    elapsed_ms: int = 0


class RewriteTrace(BaseModel):
    request: RequestEcho
    understanding: UnderstandingOut
    routing: list[DecisionOut] = Field(default_factory=list)
    workflow: list[NodeOut] = Field(default_factory=list)
    rewrites: list[RewriteOut] = Field(default_factory=list)
    arms: list[ArmOut] = Field(default_factory=list)
    evaluation: EvaluationOut
    second_stage: SecondStageOut
    fusion: FusionOut
    totals: TotalsOut
    degraded: bool = False
    warnings: list[str] = Field(default_factory=list)


__all__ = [
    "ArmMetricsOut",
    "ArmOut",
    "DecisionOut",
    "EvaluationOut",
    "FusedChunkOut",
    "FusionOut",
    "HistoryTurn",
    "NodeOut",
    "RequestEcho",
    "RewriteOut",
    "RewriteRunRequest",
    "RewriteTrace",
    "SecondStageOut",
    "TotalsOut",
    "UnderstandingOut",
]
