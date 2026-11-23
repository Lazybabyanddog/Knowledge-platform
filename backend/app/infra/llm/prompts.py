"""抽取提示词：系统约束 + 分批的用户内容模板。"""

from __future__ import annotations

import json
from typing import Any

ENTITY_SYSTEM = """你是企业知识图谱的信息抽取器。
只输出 JSON，不要解释。严格按给定本体抽取，禁止编造本体之外的类型。
JSON 结构：
{"entities":[{"ref":"本地编号","class_code":"本体类编码","name":"实体名称",
"aliases":["别名"],"description":"一句话说明","attributes":{"属性编码":"属性值"},"confidence":0.0}],
"relations":[{"source_ref":"源实体本地编号","target_ref":"目标实体本地编号",
"code":"关系类型编码","attributes":{"属性编码":"属性值"},"evidence_text":"支撑该关系的原文片段",
"confidence":0.0,"evidence_block_id":"证据块 id"}]}
置信度取 0~1；证据必须来自给定文本块，不得推测。
属性规则：
1. attributes 的键只能使用本体中已定义的属性编码，未定义的属性一律不要输出。
2. 原文没有写明的属性直接省略该键，不要填推测值，也不要填"未知""未提及"。
3. 属性的 description 说明该提什么，hint 说明判定口径，examples 是正例，请据此取值。
4. 标识类属性（如统一社会信用代码、身份证号、登记编号）在原文出现时必须逐字原样抄写，
   不要补全、不要改写、不要跨实体借用。
5. relations 的 evidence_text 必须是对应证据块中连续的一段原文，不得改写或概括。"""

REPAIR_SYSTEM = """你是严格的 JSON 修复器。用户会给你一段期望结构与一段不合规输出。
只输出修复后的 JSON 对象，字段与期望结构一致，不要新增解释。"""

def _project_attribute(code: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"code": code, "name": code, "type": "string", "description": str(raw)}
    projected: dict[str, Any] = {
        "code": raw.get("code") or code,
        "name": raw.get("name") or code,
        "type": raw.get("type") or "string",
        "description": raw.get("description") or raw.get("name") or code,
        "required": bool(raw.get("required", False)),
    }
    for field in ("hint", "examples", "enum_values", "items_type"):
        value = raw.get(field)
        if value:
            projected[field] = value
    return projected


def _project_attributes(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    return [_project_attribute(code, value) for code, value in raw.items()]


def prompt_projection(
    classes: list[dict[str, Any]], relations: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """裁剪成"只说抽取需要的话"的提示词视图。

    数据库里存的是完整定义（含版本、状态等管理字段），这里只保留模型真正要看的字段，
    以后加字段也不会无脑撑大提示词。
    """
    projected_classes = [
        {
            "code": row.get("code"),
            "name": row.get("name"),
            "description": row.get("description"),
            "attributes": _project_attributes(row.get("attributes")),
        }
        for row in classes
    ]
    projected_relations = [
        {
            "code": row.get("code"),
            "name": row.get("name"),
            "description": row.get("description"),
            "source_class_code": row.get("source_class_code"),
            "target_class_code": row.get("target_class_code"),
            "attributes": _project_attributes(row.get("attributes")),
        }
        for row in relations
    ]
    return projected_classes, projected_relations


def ontology_prompt(classes: list[dict[str, Any]], relations: list[dict[str, Any]]) -> str:
    classes, relations = prompt_projection(classes, relations)
    return (
        "可用实体类：\n"
        + json.dumps(classes, ensure_ascii=False)
        + "\n可用关系类型（source -> target）：\n"
        + json.dumps(relations, ensure_ascii=False)
    )


def blocks_prompt(blocks: list[dict[str, Any]]) -> str:
    return (
        "待抽取文本块（id 为证据编号）：\n"
        + json.dumps(blocks, ensure_ascii=False)
        + "\n请抽取出所有这些块中出现的实体与关系。"
    )


__all__ = [
    "ENTITY_SYSTEM",
    "REPAIR_SYSTEM",
    "blocks_prompt",
    "ontology_prompt",
    "prompt_projection",
]
