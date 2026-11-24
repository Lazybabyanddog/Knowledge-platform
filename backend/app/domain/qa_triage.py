"""低置信判定的纯逻辑：只吃信号与阈值，不碰数据库。

抽出这一层是为了两件事：

- 阈值重算：判定结果 = f(信号快照, 当前阈值)，历史记录不必重新采集
- 单元可测：硬信号与软打标的分界不依赖真实检索结果

两个维度刻意不合成一个分数：检索侧决定"是不是没找到"，答案侧决定
"找到了但答得不对"，合成之后就分不清该补文档还是该改提示词。
"""

from __future__ import annotations

import re

from app.domain.enums import HARD_QA_REASONS, QaReason

CITATION_PATTERN = re.compile(r"\[(\d+)\]")

# 展示顺序固定：硬信号在前，软标记在后，界面不用再排一次
REASON_ORDER: list[str] = [
    QaReason.NO_HIT.value,
    QaReason.NO_CITATION.value,
    QaReason.USER_DISLIKE.value,
    QaReason.FEW_HITS.value,
    QaReason.LOW_SCORE.value,
    QaReason.SCORE_GAP.value,
]

AUTO_REASONS: frozenset[str] = frozenset(
    {
        QaReason.NO_HIT.value,
        QaReason.NO_CITATION.value,
        QaReason.FEW_HITS.value,
        QaReason.LOW_SCORE.value,
        QaReason.SCORE_GAP.value,
    }
)


def extract_citations(answer: str) -> set[int]:
    """取出答案里出现的引用编号，容忍 ``[1,2]`` 之外的常见写法。"""
    return {int(match) for match in CITATION_PATTERN.findall(answer or "")}


def has_valid_citation(answer: str, hit_count: int) -> bool:
    """答案是否至少引用了一个真实存在的片段编号。"""
    if hit_count <= 0:
        return False
    return any(1 <= no <= hit_count for no in extract_citations(answer))


def evaluate(
    *,
    hit_count: int,
    top_score: float | None,
    last_score: float | None,
    answer: str,
    low_hit_floor: int,
    score_mark_threshold: float,
    score_gap_threshold: float,
) -> list[str]:
    """算出自动判定原因；用户侧原因由反馈接口单独追加。"""
    reasons: set[str] = set()

    if hit_count <= 0:
        reasons.add(QaReason.NO_HIT.value)
    else:
        if low_hit_floor > 0 and hit_count < low_hit_floor:
            reasons.add(QaReason.FEW_HITS.value)
        if (
            score_mark_threshold > 0
            and top_score is not None
            and top_score < score_mark_threshold
        ):
            reasons.add(QaReason.LOW_SCORE.value)
        if (
            score_gap_threshold > 0
            and hit_count >= 2
            and top_score is not None
            and last_score is not None
            and (top_score - last_score) < score_gap_threshold
        ):
            reasons.add(QaReason.SCORE_GAP.value)
        if not has_valid_citation(answer, hit_count):
            reasons.add(QaReason.NO_CITATION.value)

    return order_reasons(reasons)


def compose(auto_reasons: list[str], *, disliked: bool) -> list[str]:
    """把自动原因与用户原因合并成记录上的最终原因列表。"""
    reasons = {r for r in auto_reasons if r in AUTO_REASONS}
    if disliked:
        reasons.add(QaReason.USER_DISLIKE.value)
    return order_reasons(reasons)


def order_reasons(reasons: set[str] | list[str]) -> list[str]:
    known = [r for r in REASON_ORDER if r in reasons]
    unknown = sorted(r for r in reasons if r not in REASON_ORDER)
    return known + unknown


def has_hard_reason(reasons: list[str] | set[str]) -> bool:
    return bool(HARD_QA_REASONS & set(reasons))


def primary_source(sources: list[dict] | None) -> dict | None:
    """来源快照里排在最前、且带文档标识的那一条。"""
    for source in sources or []:
        if isinstance(source, dict) and source.get("document_id"):
            return source
    return None
