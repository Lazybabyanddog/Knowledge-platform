"""抽取提示词：系统约束 + 分批的用户内容模板。"""

from __future__ import annotations

import json
from typing import Any

ENTITY_SYSTEM = """你是企业知识图谱的信息抽取器。
只输出 JSON，不要解释。严格按给定本体抽取，禁止编造本体之外的类型。
JSON 结构：
{"entities":[{"ref":"本地编号","class_code":"本体类编码","name":"实体名称",
"aliases":["别名"],"description":"一句话说明","confidence":0.0}],
"relations":[{"source_ref":"源实体本地编号","target_ref":"目标实体本地编号",
"code":"关系类型编码","confidence":0.0,"evidence_block_id":"证据块 id"}]}
置信度取 0~1；证据必须来自给定文本块，不得推测。"""

REPAIR_SYSTEM = """你是严格的 JSON 修复器。用户会给你一段期望结构与一段不合规输出。
只输出修复后的 JSON 对象，字段与期望结构一致，不要新增解释。"""


def ontology_prompt(classes: list[dict[str, Any]], relations: list[dict[str, Any]]) -> str:
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


__all__ = ["ENTITY_SYSTEM", "REPAIR_SYSTEM", "blocks_prompt", "ontology_prompt"]
