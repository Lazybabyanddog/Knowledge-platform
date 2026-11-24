"""查询理解。

规则负责"能被证据支持的部分"（指代线索、复合连词、口语词、专名片段），
模型只补意图、约束和候选策略。没有对话历史时不允许声称已消解指代。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.infra.llm import prompts
from app.infra.llm.provider import LLMError

logger = logging.getLogger(__name__)

REFERENCE_CUES = (
    "这个", "那个", "该", "上述", "前述", "它", "其", "此", "这些", "那些", "接着", "继续",
)
STRONG_COMPOSITE_CUES = ("并且", "同时", "以及", "分别", "各自", "还要", "另外", "对比")
COLLOQUIAL_CUES = (
    "咋", "怎么弄", "能不能", "帮我", "想问一下", "是多少来着", "搞一下", "看一下", "呗",
)
CONCEPT_CUES = ("什么是", "如何理解", "有哪些", "区别", "原理", "为什么", "怎么判断", "包括哪些")

_ENTITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"《([^》]{2,30})》"),
    re.compile(r"[“\"]([^”\"]{2,30})[”\"]"),
    re.compile(r"([\u4e00-\u9fa5]{2,12}(?:公司|集团|法院|委员会|银行|大学|医院|研究院))"),
    re.compile(r"([A-Z]{2,8})"),
    re.compile(r"(第?[0-9]{1,4}号)"),
)
_WHITESPACE = re.compile(r"\s+")


@dataclass(slots=True)
class Reference:
    cue: str
    resolved: bool
    reason: str


@dataclass(slots=True)
class Signals:
    references: list[str] = field(default_factory=list)
    composite_cues: list[str] = field(default_factory=list)
    colloquial_cues: list[str] = field(default_factory=list)
    concept_cues: list[str] = field(default_factory=list)
    entity_candidates: list[str] = field(default_factory=list)
    has_history: bool = False
    composite: bool = False


@dataclass(slots=True)
class Understanding:
    intent: str
    complexity: str
    entities: list[str]
    references: list[Reference]
    constraints: list[str]
    candidate_strategies: list[str]
    source: str
    degraded: bool
    signals: Signals
    length: int = 0


def extract_signals(query: str, history: list[dict[str, str]]) -> Signals:
    text = query.strip()
    references = [cue for cue in REFERENCE_CUES if cue in text]
    strong = [cue for cue in STRONG_COMPOSITE_CUES if cue in text]
    question_marks = text.count("?") + text.count("？")
    composite = bool(strong) or question_marks >= 2 or len(text) >= 45

    candidates: list[str] = []
    for pattern in _ENTITY_PATTERNS:
        for match in pattern.findall(text):
            value = match.strip()
            if value and value not in candidates:
                candidates.append(value)

    return Signals(
        references=references,
        composite_cues=strong,
        colloquial_cues=[cue for cue in COLLOQUIAL_CUES if cue in text],
        concept_cues=[cue for cue in CONCEPT_CUES if cue in text],
        entity_candidates=candidates[:6],
        has_history=bool(history),
        composite=composite,
    )


def rule_complexity(signals: Signals, query: str) -> str:
    if signals.composite:
        return "high"
    if len(query.strip()) >= 20 or signals.colloquial_cues or signals.concept_cues:
        return "medium"
    return "low"


def _rule_understanding(query: str, history: list[dict[str, str]]) -> Understanding:
    signals = extract_signals(query, history)
    references = [
        Reference(
            cue=cue,
            resolved=signals.has_history,
            reason="已按对话历史消解" if signals.has_history else "缺少对话历史，无法消解指代",
        )
        for cue in signals.references
    ]
    return Understanding(
        intent=_WHITESPACE.sub(" ", query.strip())[:60],
        complexity=rule_complexity(signals, query),
        entities=list(signals.entity_candidates),
        references=references,
        constraints=[],
        candidate_strategies=[],
        source="rules",
        degraded=False,
        signals=signals,
        length=len(query.strip()),
    )


def understand(
    query: str,
    history: list[dict[str, str]],
    *,
    call_llm: Callable[[str, str], dict[str, Any]] | None,
) -> Understanding:
    """规则打底，模型补充；模型不可用时退回纯规则结果并标记降级。"""
    base = _rule_understanding(query, history)
    if call_llm is None:
        base.degraded = True
        return base

    try:
        payload = call_llm(
            prompts.QUERY_UNDERSTAND_SYSTEM,
            prompts.query_understand_prompt(query, history),
        )
    except LLMError as exc:
        logger.info("查询理解模型不可用，退回规则特征: %s", exc)
        base.degraded = True
        base.source = "rules_fallback"
        return base

    base.intent = _first_text(payload.get("intent")) or base.intent
    complexity = _first_text(payload.get("complexity"))
    if complexity in {"low", "medium", "high"}:
        base.complexity = complexity
    base.constraints = _text_list(payload.get("constraints"), limit=4)
    base.candidate_strategies = _text_list(payload.get("candidate_strategies"), limit=7)
    for value in _text_list(payload.get("entities"), limit=6):
        if value not in base.entities:
            base.entities.append(value)
    for item in _raw_list(payload.get("references"))[:4]:
        cue = _first_text(item.get("cue")) if isinstance(item, dict) else ""
        if not cue:
            continue
        known = next((ref for ref in base.references if ref.cue == cue), None)
        if known is not None:
            continue
        base.references.append(
            Reference(
                cue=cue,
                resolved=base.signals.has_history,
                reason=_first_text(item.get("reason")) if isinstance(item, dict) else "",
            )
        )
    base.source = "llm"
    return base


def _first_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _raw_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text_list(value: Any, *, limit: int) -> list[str]:
    items: list[str] = []
    for entry in _raw_list(value):
        text = _first_text(entry)
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


__all__ = [
    "Reference",
    "Signals",
    "Understanding",
    "extract_signals",
    "rule_complexity",
    "understand",
]
