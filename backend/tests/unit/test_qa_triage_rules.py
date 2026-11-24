"""低置信判定的纯逻辑测试：不连库，只校验信号到原因的映射。"""

from __future__ import annotations

from app.domain.enums import HARD_QA_REASONS, QaReason
from app.domain.qa_triage import (
    compose,
    evaluate,
    extract_citations,
    has_hard_reason,
    has_valid_citation,
    primary_source,
)

BASE = {
    "low_hit_floor": 2,
    "score_mark_threshold": 0.0,
    "score_gap_threshold": 0.0,
}


def test_no_hit_is_hard_signal() -> None:
    reasons = evaluate(hit_count=0, top_score=None, last_score=None, answer="没有找到相关资料", **BASE)
    assert reasons == [QaReason.NO_HIT.value]
    assert has_hard_reason(reasons)


def test_answer_without_citation_is_hard_signal() -> None:
    reasons = evaluate(
        hit_count=3, top_score=0.7, last_score=0.6, answer="这是一段没有任何编号的答案", **BASE
    )
    assert QaReason.NO_CITATION.value in reasons
    assert has_hard_reason(reasons)


def test_answer_with_valid_citation_passes() -> None:
    reasons = evaluate(
        hit_count=3, top_score=0.7, last_score=0.6, answer="根据制度[2]，年假为 10 天。", **BASE
    )
    assert reasons == []


def test_citation_beyond_hit_count_is_not_valid() -> None:
    # 引用了不存在的片段编号，等同于没有引用
    assert not has_valid_citation("参见[5]", hit_count=3)
    assert extract_citations("一[1]，二[2]，三[3]") == {1, 2, 3}


def test_soft_signals_only_mark_not_queue() -> None:
    reasons = evaluate(
        hit_count=2,
        top_score=0.31,
        last_score=0.28,
        answer="命中了两个片段[1][2]，但覆盖不足。",
        low_hit_floor=3,
        score_mark_threshold=0.5,
        score_gap_threshold=0.1,
    )
    assert set(reasons) == {
        QaReason.FEW_HITS.value,
        QaReason.LOW_SCORE.value,
        QaReason.SCORE_GAP.value,
    }
    # 三个都是软信号，不该进队列
    assert not has_hard_reason(reasons)


def test_score_thresholds_disabled_by_default() -> None:
    reasons = evaluate(
        hit_count=1,
        top_score=0.01,
        last_score=0.01,
        answer="答案[1]",
        low_hit_floor=0,
        score_mark_threshold=0.0,
        score_gap_threshold=0.0,
    )
    assert reasons == []


def test_compose_keeps_user_reason_across_reevaluation() -> None:
    auto = [QaReason.FEW_HITS.value]
    assert compose(auto, disliked=True) == [
        QaReason.USER_DISLIKE.value,
        QaReason.FEW_HITS.value,
    ]
    # 重算自动原因时，用户点踩必须留下来
    assert compose([QaReason.NO_HIT.value], disliked=True) == [
        QaReason.NO_HIT.value,
        QaReason.USER_DISLIKE.value,
    ]
    assert compose(auto, disliked=False) == [QaReason.FEW_HITS.value]


def test_hard_reasons_are_exactly_three() -> None:
    assert HARD_QA_REASONS == {
        QaReason.NO_HIT.value,
        QaReason.NO_CITATION.value,
        QaReason.USER_DISLIKE.value,
    }


def test_primary_source_skips_entries_without_document() -> None:
    sources = [
        {"index": 1, "document_id": None},
        {"index": 2, "document_id": "abc"},
    ]
    assert primary_source(sources) == {"index": 2, "document_id": "abc"}
    assert primary_source([]) is None
