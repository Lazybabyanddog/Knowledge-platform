"""属性强转、证据原文校验与提示词投影的单测。

这些规则决定了"脏属性值被丢掉、但实体/关系本身必须留下"，
以及"属性类问题不该触发模型修复回合"。
"""

from __future__ import annotations

from app.infra.llm.prompts import ENTITY_SYSTEM, prompt_projection
from app.pipelines.extract.extractor import RuleBasedExtractor
from app.pipelines.extract.validators import (
    SOFT_ISSUE_CODES,
    clean_evidence_text,
    coerce_attributes,
    validate_extraction,
)

DEFS = {
    "uscc": {
        "code": "uscc",
        "name": "统一社会信用代码",
        "type": "string",
        "description": "18 位登记号",
        "required": True,
    },
    "employees": {
        "code": "employees",
        "name": "员工数",
        "type": "number",
        "description": "在册员工人数",
    },
    "founded_at": {
        "code": "founded_at",
        "name": "成立日期",
        "type": "date",
        "description": "营业执照登记日期",
    },
    "listed": {
        "code": "listed",
        "name": "是否上市",
        "type": "boolean",
        "description": "是否为上市公司",
    },
    "stage": {
        "code": "stage",
        "name": "轮次",
        "type": "enum",
        "description": "融资轮次",
        "enum_values": ["天使轮", "A轮"],
    },
    "tags": {
        "code": "tags",
        "name": "标签",
        "type": "array",
        "description": "行业标签",
        "items_type": "string",
    },
}


def test_coerce_attributes_converts_each_type() -> None:
    cleaned, problems = coerce_attributes(
        {
            "uscc": " 91310000XYZW ",
            "employees": "1,234",
            "founded_at": "2019年3月1日",
            "listed": "是",
            "stage": "a轮",
            "tags": ["软件", "", " 服务 "],
        },
        DEFS,
    )

    assert problems == []
    assert cleaned == {
        "uscc": "91310000XYZW",
        "employees": 1234,
        "founded_at": "2019-03-01",
        "listed": True,
        "stage": "A轮",
        "tags": ["软件", "服务"],
    }


def test_coerce_attributes_drops_bad_values_but_keeps_others() -> None:
    cleaned, problems = coerce_attributes(
        {
            "uscc": "91310000XYZW",
            "employees": "一千人",
            "founded_at": "去年",
            "listed": "maybe",
            "stage": "B轮",
            "tags": "软件",
            "ghost": "未定义属性",
        },
        DEFS,
    )

    assert cleaned == {"uscc": "91310000XYZW"}
    codes = {code for code, _ in problems}
    assert codes == {"employees", "founded_at", "listed", "stage", "tags", "ghost"}
    assert ("stage", "attr_enum_mismatch") in problems
    assert ("ghost", "attr_unknown") in problems


def test_coerce_attributes_parses_date_and_boolean_boundaries() -> None:
    cleaned, problems = coerce_attributes(
        {
            "uscc": "91310000XYZW",
            "founded_at": "2020/1/1",
            "listed": "否",
        },
        DEFS,
    )
    assert problems == []
    assert cleaned == {"uscc": "91310000XYZW", "founded_at": "2020-01-01", "listed": False}

    cleaned, problems = coerce_attributes(
        {"uscc": "91310000XYZW", "founded_at": "2020年1月1日", "listed": "是"},
        DEFS,
    )
    assert problems == []
    assert cleaned == {"uscc": "91310000XYZW", "founded_at": "2020-01-01", "listed": True}

    cleaned, problems = coerce_attributes(
        {"uscc": "91310000XYZW", "founded_at": "2020-13-40", "listed": "也许"},
        DEFS,
    )
    assert cleaned == {"uscc": "91310000XYZW"}
    assert {attr for attr, _ in problems} == {"founded_at", "listed"}
    assert {code for _, code in problems} == {"attr_type_mismatch"}


def test_coerce_attributes_flags_required_missing_without_dropping_entity() -> None:
    cleaned, problems = coerce_attributes({"employees": 12}, DEFS)
    assert cleaned == {"employees": 12}
    assert ("uscc", "attr_required_missing") in problems
    assert {code for _, code in problems} <= SOFT_ISSUE_CODES


def test_coerce_attributes_rejects_non_object_payload() -> None:
    cleaned, problems = coerce_attributes(["uscc"], DEFS)
    assert cleaned == {}
    assert problems == [("*", "attr_not_object")]


def test_clean_evidence_text_keeps_only_verbatim_fragments() -> None:
    block = "张伟自 2020 年起任职于星海科技，负责研发。"

    text, mismatch = clean_evidence_text("2020 年起任职于星海科技", block)
    assert text == "2020 年起任职于星海科技"
    assert mismatch is False

    text, mismatch = clean_evidence_text("张伟任职于星海科技", block)
    assert text is None
    assert mismatch is True

    text, mismatch = clean_evidence_text("2020 年起任职于星海科技", None)
    assert text is None
    assert mismatch is True

    text, mismatch = clean_evidence_text("   ", block)
    assert (text, mismatch) == (None, False)


def _classes() -> dict[str, dict]:
    return {"company": {"code": "company", "attributes": DEFS, "identity_keys": ["uscc"]}}


def _relations() -> dict[str, dict]:
    return {
        "invests": {
            "code": "invests",
            "source_class_code": "company",
            "target_class_code": "company",
            "attributes": {"amount": {"code": "amount", "name": "金额", "type": "number"}},
        }
    }


def test_validate_extraction_reports_attribute_issues_as_soft() -> None:
    issues = validate_extraction(
        {
            "entities": [
                {
                    "ref": "e1",
                    "class_code": "company",
                    "name": "星海科技",
                    "confidence": 0.9,
                    "evidence_block_ids": ["b1"],
                    "attributes": {"employees": "很多", "ghost": 1},
                }
            ],
            "relations": [
                {
                    "source_ref": "e1",
                    "target_ref": "e1",
                    "code": "invests",
                    "attributes": {"amount": "巨额"},
                }
            ],
        },
        classes=_classes(),
        relations=_relations(),
        block_ids={"b1"},
    )

    attr_issues = [item for item in issues if item.code in SOFT_ISSUE_CODES]
    assert {item.code for item in attr_issues} >= {
        "attr_type_mismatch",
        "attr_unknown",
        "attr_required_missing",
    }
    assert all(not item.blocking for item in attr_issues)


def test_validate_extraction_flags_evidence_text_outside_blocks() -> None:
    issues = validate_extraction(
        {
            "entities": [
                {"ref": "e1", "class_code": "company", "name": "星海科技", "confidence": 0.9},
                {"ref": "e2", "class_code": "company", "name": "云图科技", "confidence": 0.9},
            ],
            "relations": [
                {
                    "source_ref": "e1",
                    "target_ref": "e2",
                    "code": "invests",
                    "evidence_block_id": "b1",
                    "evidence_text": "星海科技收购了云图科技",
                }
            ],
        },
        classes=_classes(),
        relations=_relations(),
        block_ids={"b1"},
        block_texts={"b1": "星海科技投资云图科技，金额未披露。"},
    )

    codes = {item.code for item in issues}
    assert "evidence_text_mismatch" in codes


def test_prompt_projection_keeps_only_extraction_fields() -> None:
    classes, relations = prompt_projection(
        [
            {
                "code": "company",
                "name": "公司",
                "description": "企业法人",
                "version": 7,
                "status": 0,
                "identity_keys": ["uscc"],
                "attributes": {
                    "uscc": {
                        "code": "uscc",
                        "name": "统一社会信用代码",
                        "type": "string",
                        "description": "18 位登记号",
                        "examples": ["91310000XYZW"],
                        "hint": "逐字抄写",
                        "required": True,
                        "enum_values": None,
                        "items_type": None,
                    },
                    "legacy": "string",
                },
            }
        ],
        [
            {
                "code": "invests",
                "name": "投资",
                "source_class_code": "company",
                "target_class_code": "company",
                "version": 3,
                "attributes": {},
            }
        ],
    )

    assert set(classes[0]) == {"code", "name", "description", "attributes"}
    assert "version" not in classes[0]
    uscc = classes[0]["attributes"][0]
    assert uscc["hint"] == "逐字抄写"
    assert uscc["examples"] == ["91310000XYZW"]
    assert uscc["required"] is True
    # 空值字段不塞进提示词，避免把 enum_values=null 也发给模型
    assert "enum_values" not in uscc
    legacy = classes[0]["attributes"][1]
    assert legacy == {
        "code": "legacy",
        "name": "legacy",
        "type": "string",
        "description": "string",
    }
    assert relations[0]["attributes"] == []
    assert relations[0]["source_class_code"] == "company"


def test_entity_system_declares_attribute_and_evidence_rules() -> None:
    # 提示词快照：关键约束被删掉会让模型开始编属性、改证据原文，这里钉住它们。
    assert '"attributes":{"属性编码":"属性值"}' in ENTITY_SYSTEM
    assert '"evidence_text":"支撑该关系的原文片段"' in ENTITY_SYSTEM
    assert "attributes 的键只能使用本体中已定义的属性编码" in ENTITY_SYSTEM
    assert "标识类属性" in ENTITY_SYSTEM and "逐字原样抄写" in ENTITY_SYSTEM
    assert "不得改写或概括" in ENTITY_SYSTEM


def test_rule_based_extractor_keeps_attribute_and_evidence_shape() -> None:
    extractor = RuleBasedExtractor()
    result = extractor.extract(
        [{"id": "b1", "content": "张伟，星海科技，任职"}],
        {
            "person": {
                "code": "person",
                "name": "张伟",
                "attributes": {"uscc": {"code": "uscc", "type": "string"}},
            },
            "org": {"code": "org", "name": "星海科技", "attributes": {}},
        },
        {
            "works_at": {
                "code": "works_at",
                "name": "任职",
                "source_class_code": "person",
                "target_class_code": "org",
                "attributes": {"title": {"code": "title", "type": "string"}},
            }
        },
    )

    assert result.raw is not None
    assert all(item["attributes"] == {} for item in result.raw["entities"])
    relation = result.raw["relations"][0]
    assert relation["attributes"] == {}
    assert relation["evidence_text"] == "任职"
    assert relation["evidence_block_id"] == "b1"
