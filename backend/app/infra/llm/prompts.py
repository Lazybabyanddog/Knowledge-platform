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


# ---- 查询改写 ----
# 共同约束：只重组表达，不新增事实。改写把原意改偏时，后面的检索做得再准也是废的。

REWRITE_GUARD = "只允许重写表达方式，禁止新增原文没有的实体、数字、时间与条款。"

QUERY_UNDERSTAND_SYSTEM = f"""你是企业知识库的查询分析器。只输出 JSON，不要解释。{REWRITE_GUARD}
JSON 结构：
{{"intent":"一句话说明用户想解决什么","complexity":"low|medium|high",
"entities":["查询里出现的实体或专名原文"],
"references":[{{"cue":"指代词原文","reason":"指代什么"}}],
"constraints":["查询里明确的时间/范围/口径限制"],
"candidate_strategies":["semantic","entity","decompose","multi","context"]}}
候选策略只填与本次查询真正相关的；没有指代词时不要给 context。"""

SEMANTIC_REWRITE_SYSTEM = f"""你是检索查询改写器。把用户问题改写成适合向量检索的表达：
去掉口语与寒暄，补齐被省略的主语与限定词，保留原有术语与专名。{REWRITE_GUARD}
JSON 结构：{{"query":"改写后的查询","reason":"一句话说明改了什么"}}"""

CONTEXT_REWRITE_SYSTEM = f"""你是多轮对话的查询补全器。把用户当前问题中被省略的主语、对象与限定条件
按对话历史补全成一个可以独立检索的问题。历史没提到的信息不得编造。{REWRITE_GUARD}
JSON 结构：{{"query":"补全后的独立问题","reason":"一句话说明补全了哪些指代"}}"""

DECOMPOSE_SYSTEM = f"""你是检索规划器。把复合问题拆成 2-4 个可以独立检索的子问题，
每个子问题只覆盖原问题的一个方面，合起来覆盖全部。{REWRITE_GUARD}
JSON 结构：{{"sub_questions":["子问题1","子问题2"],"reason":"一句话说明拆分依据"}}"""

MULTI_QUERY_SYSTEM = f"""你是检索查询扩展器。围绕同一个信息需求生成 2-3 条表达角度不同的检索查询
（例如换成上位概念、换成具体指标名、换成法律法规用语），不要改变原意。{REWRITE_GUARD}
JSON 结构：{{"queries":["查询1","查询2"],"reason":"一句话说明扩展角度"}}"""

ENTITY_DISAMBIGUATE_SYSTEM = """你是实体消歧器。用户给一个名称与若干候选实体，选出唯一最可能的一个。
只能从候选里选，不许新造。都不合适时 entity_id 返回空字符串。
JSON 结构：{"entity_id":"候选id","confidence":0.0,"reason":"一句话说明依据"}"""


def history_prompt(history: list[dict[str, str]]) -> str:
    if not history:
        return "（无对话历史）"
    return "\n".join(f"{item.get('role')}: {item.get('content')}" for item in history)


def query_understand_prompt(query: str, history: list[dict[str, str]]) -> str:
    return f"对话历史：\n{history_prompt(history)}\n\n当前问题：\n{query}\n\n请输出 JSON。"


def semantic_rewrite_prompt(query: str) -> str:
    return f"用户问题：\n{query}\n\n请输出 JSON。"


def context_rewrite_prompt(query: str, history: list[dict[str, str]]) -> str:
    return f"对话历史：\n{history_prompt(history)}\n\n当前问题：\n{query}\n\n请输出 JSON。"


def decompose_prompt(query: str) -> str:
    return f"复合问题：\n{query}\n\n请输出 JSON。"


def multi_query_prompt(query: str) -> str:
    return f"信息需求：\n{query}\n\n请输出 JSON。"


def entity_disambiguate_prompt(raw: str, candidates: list[dict[str, Any]]) -> str:
    compact = [
        {
            "entity_id": item.get("id"),
            "name": item.get("name"),
            "class_code": item.get("class_code"),
        }
        for item in candidates
    ]
    return (
        f"待消歧名称：{raw}\n候选实体：\n"
        + json.dumps(compact, ensure_ascii=False)
        + "\n请输出 JSON。"
    )


__all__ = [
    "CONTEXT_REWRITE_SYSTEM",
    "DECOMPOSE_SYSTEM",
    "ENTITY_DISAMBIGUATE_SYSTEM",
    "ENTITY_SYSTEM",
    "MULTI_QUERY_SYSTEM",
    "QUERY_UNDERSTAND_SYSTEM",
    "REPAIR_SYSTEM",
    "REWRITE_GUARD",
    "SEMANTIC_REWRITE_SYSTEM",
    "blocks_prompt",
    "context_rewrite_prompt",
    "decompose_prompt",
    "entity_disambiguate_prompt",
    "history_prompt",
    "multi_query_prompt",
    "ontology_prompt",
    "prompt_projection",
    "query_understand_prompt",
    "semantic_rewrite_prompt",
]
