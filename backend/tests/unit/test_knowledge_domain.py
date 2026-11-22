"""知识抽取纯函数单测（任务 5.5 的去重/命名规则）。"""

from __future__ import annotations

from app.domain.knowledge import (
    dedupe_entities,
    dedupe_relations,
    entity_node_id,
    normalize_name,
)


def test_normalize_name_unifies_width_case_and_punctuation() -> None:
    assert normalize_name("  ＡＢＣ 公司 ") == "abc公司"
    assert normalize_name("星海科技（集团）") == "星海科技集团"
    assert normalize_name("A-Team_01") == "ateam01"
    assert normalize_name("") == ""


def test_entity_node_id_is_stable_per_task_class_and_name() -> None:
    first = entity_node_id("task-1", "person", "张伟")
    assert first == entity_node_id("task-1", "person", " 张伟 ")
    assert first != entity_node_id("task-2", "person", "张伟")
    assert first != entity_node_id("task-1", "org", "张伟")


def test_dedupe_entities_merges_aliases_evidence_and_confidence() -> None:
    merged = dedupe_entities(
        [
            {
                "class_code": "person",
                "name": "张伟",
                "aliases": ["小张"],
                "confidence": 0.6,
                "evidence_block_ids": ["b1"],
                "description": None,
            },
            {
                "class_code": "person",
                "name": "张 伟",
                "aliases": ["张总"],
                "confidence": 0.9,
                "evidence_block_ids": ["b1", "b2"],
                "description": "负责人",
            },
            {"class_code": "person", "name": "  ", "confidence": 1.0},
        ]
    )

    assert len(merged) == 1
    item = merged[0]
    assert item["name"] == "张伟"
    assert item["aliases"] == ["小张", "张总"]
    assert item["evidence_block_ids"] == ["b1", "b2"]
    assert item["confidence"] == 0.9
    assert item["description"] == "负责人"


def test_dedupe_relations_keeps_highest_confidence() -> None:
    rows = dedupe_relations(
        [
            {"id": "r1", "source_id": "a", "target_id": "b", "code": "works_at", "confidence": 0.4},
            {"id": "r2", "source_id": "a", "target_id": "b", "code": "works_at", "confidence": 0.8},
            {"id": "r3", "source_id": "a", "target_id": "c", "code": "works_at", "confidence": 0.5},
        ]
    )
    by_id = {item["id"]: item for item in rows}
    assert set(by_id) == {"r2", "r3"}
