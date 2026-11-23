"""知识层纯函数单测：名称归一、实体身份、关系证据键与候选去重。"""

from __future__ import annotations

from app.domain.knowledge import (
    IDENTITY_SEPARATOR,
    KIND_IDENTITY,
    KIND_NAME,
    compute_identity,
    dedupe_entities,
    dedupe_relations,
    entity_node_id,
    identity_key,
    normalize_identifier,
    normalize_name,
    relation_evidence_key,
    relation_instance_id,
)

ORG_CLASS = {"code": "company", "identity_keys": ["uscc"]}
PERSON_CLASS = {"code": "person", "identity_keys": []}


def test_normalize_name_unifies_width_case_and_punctuation() -> None:
    assert normalize_name("  ＡＢＣ 公司 ") == "abc公司"
    assert normalize_name("星海科技（集团）") == "星海科技集团"
    assert normalize_name("A-Team_01") == "ateam01"
    assert normalize_name("") == ""


def test_normalize_identifier_strips_separators_and_uppercases() -> None:
    assert normalize_identifier(" 9131 0000-xyzw ") == "91310000XYZW"
    assert normalize_identifier(None) == ""


def test_compute_identity_uses_identity_when_value_is_in_evidence() -> None:
    kind, value = compute_identity(
        ORG_CLASS,
        {"uscc": "9131 0000-XYZW"},
        "星海科技",
        ["统一社会信用代码：9131 0000-XYZW。"],
    )
    assert kind == KIND_IDENTITY
    assert value == "91310000XYZW"
    assert identity_key(kind, value) == f"identity:91310000XYZW"


def test_compute_identity_falls_back_to_name_when_value_is_hallucinated() -> None:
    """标识值不在原文里就不能当身份：否则幻觉一个证号会把两家公司并成一家。"""
    kind, value = compute_identity(
        ORG_CLASS,
        {"uscc": "91310000FAKE"},
        "星海科技",
        ["星海科技成立于 2019 年。"],
    )
    assert (kind, value) == (KIND_NAME, "星海科技")


def test_compute_identity_falls_back_when_keys_incomplete() -> None:
    class_def = {"code": "person", "identity_keys": ["id_no", "passport"]}
    kind, value = compute_identity(class_def, {"id_no": "110101199001011234"}, "张伟", ["张伟"])
    assert kind == KIND_NAME
    assert value == "张伟"


def test_compute_identity_joins_multiple_identity_values() -> None:
    class_def = {"code": "contract", "identity_keys": ["contract_no", "sign_date"]}
    kind, value = compute_identity(
        class_def,
        {"contract_no": "HT-001", "sign_date": "2024-03-01"},
        "采购合同",
        ["合同编号 HT-001，签订日期 2024-03-01"],
    )
    assert kind == KIND_IDENTITY
    assert value == f"HT001{IDENTITY_SEPARATOR}20240301"


def test_entity_node_id_is_scoped_by_workspace_class_and_identity() -> None:
    key = identity_key(KIND_IDENTITY, "91310000XYZW")
    first = entity_node_id("ws-1", "company", key)
    assert first == entity_node_id("ws-1", "company", key)
    assert first != entity_node_id("ws-2", "company", key)
    assert first != entity_node_id("ws-1", "person", key)


def test_relation_evidence_key_is_stable_and_instance_id_deterministic() -> None:
    first = relation_evidence_key("doc-1", "b1", "works_at", "a", "b", "张伟 任职于 星海科技")
    same = relation_evidence_key("doc-1", "b1", "works_at", "a", "b", " 张伟任职于星海科技。")
    other_block = relation_evidence_key("doc-1", "b2", "works_at", "a", "b", "张伟 任职于 星海科技")
    other_text = relation_evidence_key("doc-1", "b1", "works_at", "a", "b", "张伟 曾任 星海科技")

    assert first == same
    assert first != other_block
    assert first != other_text
    assert relation_instance_id(first) == relation_instance_id(same)


def test_dedupe_entities_merges_same_identity_and_counts_attribute_conflicts() -> None:
    merged = dedupe_entities(
        [
            {
                "class_code": "company",
                "name": "星海科技",
                "identity_kind": KIND_IDENTITY,
                "identity_value": "91310000XYZW",
                "identity_key": "identity:91310000XYZW",
                "aliases": ["星海"],
                "confidence": 0.6,
                "attributes": {"uscc": "91310000XYZW", "city": "杭州"},
                "evidence_block_ids": ["b1"],
            },
            {
                "class_code": "company",
                "name": "星海科技集团",
                "identity_kind": KIND_IDENTITY,
                "identity_value": "91310000XYZW",
                "identity_key": "identity:91310000XYZW",
                "aliases": ["星海科技"],
                "confidence": 0.9,
                "attributes": {"city": "上海", "industry": "软件"},
                "evidence_block_ids": ["b2"],
            },
            {"class_code": "company", "name": "   ", "confidence": 1.0},
        ]
    )

    assert len(merged) == 1
    item = merged[0]
    assert item["identity_key"] == "identity:91310000XYZW"
    assert item["name"] == "星海科技"
    assert item["aliases"] == ["星海", "星海科技"]
    assert item["evidence_block_ids"] == ["b1", "b2"]
    assert item["confidence"] == 0.9
    # 既有非空值优先，冲突只计数不覆盖
    assert item["attributes"] == {"uscc": "91310000XYZW", "city": "杭州", "industry": "软件"}
    assert item["attribute_conflicts"] == 1


def test_dedupe_entities_keeps_same_name_with_different_identity() -> None:
    merged = dedupe_entities(
        [
            {
                "class_code": "person",
                "name": "张伟",
                "identity_key": "identity:110101199001011234",
                "evidence_block_ids": ["b1"],
            },
            {
                "class_code": "person",
                "name": "张伟",
                "identity_key": "identity:310101199202022345",
                "evidence_block_ids": ["b2"],
            },
        ]
    )
    assert len(merged) == 2
    assert {item["identity_key"] for item in merged} == {
        "identity:110101199001011234",
        "identity:310101199202022345",
    }


def test_dedupe_relations_keeps_multiple_instances_between_same_endpoints() -> None:
    rows = dedupe_relations(
        [
            {
                "id": "r1",
                "source_id": "p1",
                "target_id": "c1",
                "code": "works_at",
                "evidence_text": "2020 年至 2022 年任职",
                "confidence": 0.5,
            },
            {
                "id": "r1",
                "source_id": "p1",
                "target_id": "c1",
                "code": "works_at",
                "evidence_text": "2020 年至 2022 年任职",
                "confidence": 0.8,
            },
            {
                "id": "r2",
                "source_id": "p1",
                "target_id": "c1",
                "code": "works_at",
                "evidence_text": "2023 年起再次任职",
                "confidence": 0.6,
            },
        ]
    )
    by_id = {item["id"]: item for item in rows}
    assert set(by_id) == {"r1", "r2"}
    assert by_id["r1"]["confidence"] == 0.8


def test_dedupe_relations_builds_evidence_key_when_id_missing() -> None:
    rows = dedupe_relations(
        [
            {
                "document_id": "doc-1",
                "evidence_block_id": "b1",
                "source_id": "p1",
                "target_id": "c1",
                "code": "works_at",
                "evidence_text": "张伟任职于星海科技",
                "confidence": 0.7,
            },
            {
                "document_id": "doc-1",
                "evidence_block_id": "b1",
                "source_id": "p1",
                "target_id": "c1",
                "code": "works_at",
                "evidence_text": "张伟 任职于 星海科技。",
                "confidence": 0.4,
            },
        ]
    )
    assert len(rows) == 1
    assert rows[0]["confidence"] == 0.7
