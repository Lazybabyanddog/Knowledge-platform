"""语义规则：随代码发布的内置规则词表。

内置规则只描述"中文文档里最常见的那几种写法"，不放任何具体业务词。
它们必须可停用（``config.alias.use_builtin = false``）：同一套词表在不同业务
文档上的误报率差别很大，用户能做的只有关掉它、再补自己的规则。

这里只存数据、不做解析，``app.services.glossary.config`` 负责把它们变成
``AliasRule``，避免 config <-> builtin 循环导入。
"""

from __future__ import annotations

from typing import Any

# 改了词表就 +1：快照记录该版本，事后能解释"当时按哪套内置规则抽的"
BUILTIN_VERSION = 1

_ORG_SUFFIXES: tuple[str, ...] = (
    "有限公司",
    "股份公司",
    "分公司",
    "子公司",
    "公司",
    "集团",
    "银行",
    "委员会",
    "管理局",
    "法院",
    "检察院",
    "研究院",
    "研究所",
    "事务所",
    "协会",
    "中心",
    "学院",
    "大学",
    "医院",
    "局",
    "厅",
    "部",
    "委",
    "办",
    "处",
)

_WRAPPERS: tuple[str, ...] = (
    "《",
    "》",
    "〈",
    "〉",
    "“",
    "”",
    "‘",
    "’",
    "（",
    "）",
    "(",
    ")",
    '"',
)

BUILTIN_RULE_DATA: tuple[dict[str, Any], ...] = (
    {
        "name": "简称标记",
        "kind": "ALIAS",
        "triggers": ("以下简称", "以下简称作", "简称为", "简称", "下称"),
        "shapes": {
            "suffixes": _ORG_SUFFIXES,
            "wrappers": _WRAPPERS,
            "stop_words": ("的规定", "的约定", "为准", "应当", "所"),
            "max_len": 40,
        },
        "target_cardinality": "ONE",
        "priority": 60,
    },
    {
        "name": "角色称谓",
        "kind": "ROLE",
        "triggers": ("原告", "被告", "第三人", "上诉人", "被上诉人", "申请执行人", "被执行人"),
        "shapes": {
            "suffixes": _ORG_SUFFIXES + ("某", "某公司"),
            "wrappers": _WRAPPERS,
            "stop_words": ("辩称", "诉称", "称", "认为", "主张", "请求", "提出", "表示", "系"),
            "max_len": 30,
        },
        "target_cardinality": "ONE",
        "priority": 40,
    },
    {
        "name": "关联方称谓",
        "kind": "RELATION",
        "triggers": ("母公司", "子公司", "关联方", "控股股东", "实际控制人"),
        "shapes": {
            "suffixes": _ORG_SUFFIXES,
            "wrappers": _WRAPPERS,
            "stop_words": ("及其", "之间", "名下"),
            "max_len": 30,
        },
        "target_cardinality": "MANY",
        "priority": 20,
    },
)

# 自定义规则不得与内置规则重名：重名意味着想覆盖内置规则，那会变成"悄悄改口径"
BUILTIN_RULE_NAMES: frozenset[str] = frozenset(
    str(item["name"]) for item in BUILTIN_RULE_DATA
)

__all__ = [
    "BUILTIN_RULE_DATA",
    "BUILTIN_RULE_NAMES",
    "BUILTIN_VERSION",
]
