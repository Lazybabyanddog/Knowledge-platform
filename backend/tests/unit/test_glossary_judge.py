"""判定器：状态矩阵、不确定性默认弃置、快照截断。"""

from __future__ import annotations

import json

import pytest

from app.services.glossary.config import parse_alias_config, resolve_rules
from app.services.glossary.extract import RawHit
from app.services.glossary.judge import (
    CANDIDATE,
    CONFIRMED,
    PENDING,
    RESOLVED,
    Entry,
    is_legal_combination,
    judge,
)
from app.services.glossary.snapshot import MAX_BYTES, MAX_ENTRIES, degraded, serialize


def _hit(
    surface: str,
    *,
    canonical: str | None = None,
    target: str | None = None,
    kind: str = "ALIAS",
    cardinality: str = "ONE",
    priority: int = 50,
    block_id: str = "block-1",
) -> RawHit:
    return RawHit(
        surface=surface,
        canonical=canonical,
        target=target,
        kind=kind,
        cardinality=cardinality,
        priority=priority,
        rule="测试规则",
        block_id=block_id,
        block_seq=1,
    )


def _rules():
    return resolve_rules(parse_alias_config({}))


@pytest.mark.parametrize(
    ("certainty", "resolution", "cardinality", "expected"),
    [
        (CONFIRMED, RESOLVED, "ONE", True),
        (CANDIDATE, PENDING, "ONE", True),
        (CANDIDATE, PENDING, "MANY", True),
        (CONFIRMED, RESOLVED, "MANY", False),
        (CONFIRMED, PENDING, "ONE", False),
        (CONFIRMED, RESOLVED, "UNKNOWN", False),
        (CANDIDATE, PENDING, "UNKNOWN", False),
    ],
)
def test_combination_matrix(
    certainty: str, resolution: str, cardinality: str, expected: bool
) -> None:
    assert is_legal_combination(certainty, resolution, cardinality) is expected


def test_single_evidence_makes_confirmed() -> None:
    entries, dropped = judge([_hit("苏州盐务局", canonical="江苏省苏州市盐务管理局")])

    assert dropped == 0
    assert entries[0].certainty == CONFIRMED
    assert entries[0].resolution == RESOLVED
    assert entries[0].evidence_block_id == "block-1"


def test_two_canonicals_downgrade_to_candidate() -> None:
    entries, _ = judge(
        [
            _hit("盐务局", canonical="江苏省盐务管理局", block_id="block-1"),
            _hit("盐务局", canonical="苏州市盐务管理局", block_id="block-2"),
        ]
    )

    assert entries[0].certainty == CANDIDATE
    assert entries[0].canonical is None
    assert set(entries[0].candidates) == {"江苏省盐务管理局", "苏州市盐务管理局"}


def test_relation_cardinality_can_never_be_confirmed() -> None:
    entries, _ = judge(
        [_hit("子公司", target="苏州某某科技有限公司", kind="RELATION", cardinality="MANY")]
    )

    assert entries[0].certainty == CANDIDATE
    assert entries[0].canonical is None


def test_relation_with_single_target_is_still_candidate() -> None:
    entries, _ = judge(
        [_hit("子公司", target="苏州某某科技有限公司", kind="RELATION", cardinality="ONE")]
    )

    assert entries[0].certainty == CANDIDATE
    assert entries[0].candidates == ("苏州某某科技有限公司",)


def test_unknown_cardinality_falls_back_to_many() -> None:
    entries, _ = judge([_hit("关联方", canonical="某某公司", cardinality="UNKNOWN")])

    assert entries[0].certainty == CANDIDATE


def test_missing_evidence_is_not_confirmed() -> None:
    entries, _ = judge([_hit("苏州盐务局", canonical="江苏省苏州市盐务管理局", block_id="")])

    assert entries[0].certainty == CANDIDATE
    assert entries[0].evidence_block_id is None


def test_low_priority_conclusion_is_dropped_not_merged() -> None:
    entries, dropped = judge(
        [
            _hit("某某局", canonical="甲局", priority=80),
            _hit("某某局", canonical="乙局", priority=30),
        ]
    )

    assert entries[0].certainty == CONFIRMED
    assert entries[0].canonical == "甲局"
    assert dropped == 1


def test_hit_without_any_conclusion_is_dropped() -> None:
    entries, dropped = judge([_hit("以下简称", canonical=None)])

    assert entries == []
    assert dropped == 1


def test_counts_match_entries_after_truncation() -> None:
    hits = [
        _hit(f"名称{index}", canonical=f"全称{index}公司", block_id=f"block-{index}")
        for index in range(MAX_ENTRIES + 50)
    ]
    entries, dropped = judge(hits)
    payload = serialize(
        entries, _rules(), raw_hits=len(hits), dropped=dropped, builtin_version=1
    )

    assert len(payload["entries"]) == MAX_ENTRIES
    assert payload["truncated"] is True
    counts = payload["counts"]
    assert counts["confirmed"] + counts["candidate"] == len(payload["entries"])


def test_byte_limit_truncates_without_breaking_counts() -> None:
    entries = [
        Entry(
            surface=f"名称{index}" * 4,
            kind="ALIAS",
            certainty=CONFIRMED,
            resolution=RESOLVED,
            block_ids=(f"block-{index}",),
            rule="测试规则",
            priority=50,
            canonical=f"完整的规范名称{index}" * 6,
            evidence_block_id=f"block-{index}",
        )
        for index in range(MAX_ENTRIES)
    ]
    payload = serialize(entries, _rules(), raw_hits=len(entries), dropped=0, builtin_version=1)

    assert len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) <= MAX_BYTES
    assert payload["counts"]["confirmed"] == len(payload["entries"])
    assert payload["truncated"] is True


def test_degraded_snapshot_is_readable() -> None:
    payload = degraded("RuntimeError: boom")

    assert payload["status"] == "degraded"
    assert "boom" in payload["error"]
    assert payload["entries"] == []
