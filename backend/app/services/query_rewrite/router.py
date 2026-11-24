"""策略路由：只做可解释的规则决策。

模型给的候选策略只进入解释与提示，不单独触发执行——否则"为什么这次改写了"
就退化成"模型这么说的"，调参时无从下手。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.query_rewrite.specs import STRATEGIES, StrategySpec
from app.services.query_rewrite.understanding import Understanding

EXECUTE = "execute"
NOT_TRIGGERED = "not_triggered"
BLOCKED_ALLOWLIST = "blocked_by_allowlist"
BLOCKED_BUDGET = "blocked_by_budget"
BLOCKED_DEPENDENCY = "blocked_by_dependency"
UNAVAILABLE = "unavailable"


@dataclass(slots=True)
class Decision:
    strategy: str
    label: str
    kind: str
    decision: str
    evidence: list[str] = field(default_factory=list)
    reason: str = ""


def route(
    understanding: Understanding,
    *,
    allowlist: list[str] | tuple[str, ...],
    budget: int,
    llm_ready: bool,
    graph_ready: bool,
) -> list[Decision]:
    allowed = set(allowlist)
    executed = 0
    decisions: list[Decision] = []

    for spec in STRATEGIES:
        hit, evidence, reason = _signal(spec, understanding)
        decision = _decide(
            spec,
            hit=hit,
            reason=reason,
            allowed=spec.code in allowed,
            budget_left=executed < budget,
            llm_ready=llm_ready,
            graph_ready=graph_ready,
        )
        if decision == EXECUTE:
            executed += 1
        decisions.append(
            Decision(
                strategy=spec.code,
                label=spec.label,
                kind=spec.kind,
                decision=decision,
                evidence=evidence,
                reason=_reason_text(spec, decision, reason),
            )
        )
    return decisions


def _decide(
    spec: StrategySpec,
    *,
    hit: bool,
    reason: str,
    allowed: bool,
    budget_left: bool,
    llm_ready: bool,
    graph_ready: bool,
) -> str:
    if not spec.enabled:
        return UNAVAILABLE
    if not hit:
        return NOT_TRIGGERED
    if spec.requires_graph and not graph_ready:
        return UNAVAILABLE
    if spec.requires_llm and not llm_ready:
        return BLOCKED_DEPENDENCY
    if not allowed:
        return BLOCKED_ALLOWLIST
    if not budget_left:
        return BLOCKED_BUDGET
    return EXECUTE


def _reason_text(spec: StrategySpec, decision: str, reason: str) -> str:
    if decision == UNAVAILABLE:
        if not spec.enabled:
            return "当前版本未启用该策略，不产生模型调用与检索"
        if spec.requires_graph:
            return "图谱不可用，无法做实体对齐"
        return reason or "该策略当前不可用"
    if decision == BLOCKED_DEPENDENCY:
        return "需要模型能力，当前未配置 LLM"
    if decision == BLOCKED_ALLOWLIST:
        return "命中规则信号，但不在本次允许列表内"
    if decision == BLOCKED_BUDGET:
        return "命中规则信号，但本次预算已用尽，按优先级靠后放弃"
    if decision == EXECUTE:
        return reason or "规则信号命中，进入执行集"
    return reason or "未命中规则信号"


def _signal(spec: StrategySpec, understanding: Understanding) -> tuple[bool, list[str], str]:
    signals = understanding.signals
    text_len = understanding.length
    if spec.code == "context":
        evidence = [f"指代线索：{cue}" for cue in signals.references]
        if not signals.references:
            return False, [], "未发现指代线索"
        if not signals.has_history:
            return False, evidence, "缺少对话历史，无法消解指代，不触发上下文改写"
        return True, evidence, "存在指代线索且提供了对话历史"
    if spec.code == "entity":
        evidence = [f"别名候选：{name}" for name in signals.entity_candidates]
        if not signals.entity_candidates:
            return False, [], "未发现需要标准化的别名"
        return True, evidence, "查询中出现疑似别名或专名"
    if spec.code == "semantic":
        cues = signals.colloquial_cues + signals.concept_cues
        evidence = [f"表达线索：{cue}" for cue in cues]
        if cues:
            return True, evidence, "查询偏口语或概念型表达"
        if text_len >= 20 and not signals.entity_candidates:
            return True, [f"长查询：{text_len} 字"], "长查询缺少检索友好的关键词密度"
        return False, evidence, "表达已足够检索友好"
    if spec.code == "decompose":
        if signals.composite:
            cues = signals.composite_cues or ["多处提问/长句"]
            return True, [f"复合线索：{cue}" for cue in cues], "查询包含多个可独立检索的问题"
        return False, [], "查询不是复合问题"
    if spec.code == "multi":
        if signals.composite or (signals.concept_cues and text_len >= 30):
            cues = signals.composite_cues or signals.concept_cues or ["多角度覆盖"]
            return True, [f"覆盖线索：{cue}" for cue in cues], "单一表述难以覆盖信息需求"
        return False, [], "单一查询已能覆盖需求"
    return False, [], "未启用策略不参与信号判定"


__all__ = [
    "BLOCKED_ALLOWLIST",
    "BLOCKED_BUDGET",
    "BLOCKED_DEPENDENCY",
    "Decision",
    "EXECUTE",
    "NOT_TRIGGERED",
    "UNAVAILABLE",
    "route",
]
