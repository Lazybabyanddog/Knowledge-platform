"""语义规则配置：解析策略 ``config.alias`` 的唯一解析与校验入口。

沿用 ``chunk_config`` 的四件套：解析函数 + 保存期校验 + 读取视图 + 缺省回退。
缺省视为启用内置规则，但"抽不到任何条目就不注入任何内容"，因此未命中的路径
与关闭能力时完全等价，默认开启不会改变既有输出。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.services.glossary.builtin import BUILTIN_RULE_DATA, BUILTIN_RULE_NAMES

KIND_ALIAS = "ALIAS"
KIND_ROLE = "ROLE"
KIND_RELATION = "RELATION"
RULE_KINDS = (KIND_ALIAS, KIND_ROLE, KIND_RELATION)

CARDINALITY_ONE = "ONE"
CARDINALITY_MANY = "MANY"
CARDINALITY_UNKNOWN = "UNKNOWN"
CARDINALITIES = (CARDINALITY_ONE, CARDINALITY_MANY, CARDINALITY_UNKNOWN)

MAX_RULES = 20
MAX_TRIGGERS = 20
MAX_TRIGGER_LEN = 12
MAX_ITEMS_PER_SHAPE = 60
MAX_MAX_LEN = 60
MIN_MAX_LEN = 2
DEFAULT_MAX_LEN = 40
MAX_REGEX_LEN = 200
MIN_PRIORITY = 0
MAX_PRIORITY = 100

# 形如 (a+)+ / (a*)* 的嵌套量词会退化成指数级回溯，直接拒绝
_NESTED_QUANTIFIER = re.compile(r"\((?:\?:)?[^()]{0,60}[*+][^()]{0,60}\)\s*[*+{]")


class AliasConfigError(ValueError):
    """别名配置不合法；请求校验层据此返回 400 并指出规则与字段。"""


@dataclass(frozen=True, slots=True)
class AliasShapes:
    """名称形态：触发词只给左边界，右边界必须靠这些约束收住。"""

    suffixes: tuple[str, ...] = ()
    wrappers: tuple[str, ...] = ()
    stop_words: tuple[str, ...] = ()
    max_len: int = DEFAULT_MAX_LEN

    @property
    def is_empty(self) -> bool:
        return not (self.suffixes or self.wrappers or self.stop_words)


@dataclass(frozen=True, slots=True)
class AliasRule:
    name: str
    kind: str
    triggers: tuple[str, ...]
    shapes: AliasShapes
    target_cardinality: str
    priority: int
    regex: str | None = None
    builtin: bool = False


@dataclass(frozen=True, slots=True)
class AliasConfig:
    enabled: bool = True
    use_builtin: bool = True
    rules: tuple[AliasRule, ...] = ()


def parse_alias_config(config: Mapping[str, Any] | None) -> AliasConfig:
    """把策略的 ``config`` 解析成别名配置，非法输入抛 ``AliasConfigError``。"""
    data: Mapping[str, Any] = config if isinstance(config, Mapping) else {}
    raw = data.get("alias")
    if raw is None:
        return AliasConfig()
    if not isinstance(raw, Mapping):
        raise AliasConfigError(
            'alias 必须是对象，例如 {"enabled": true, "use_builtin": true, "rules": []}'
        )

    enabled = _require_bool(raw.get("enabled"), "alias.enabled", default=True)
    use_builtin = _require_bool(raw.get("use_builtin"), "alias.use_builtin", default=True)
    rules = _parse_rules(raw.get("rules"))
    return AliasConfig(enabled=enabled, use_builtin=use_builtin, rules=rules)


def alias_config_view(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """给接口用的生效配置（缺省值已补齐）；历史脏数据不报错，返回空对象。"""
    try:
        cfg = parse_alias_config(config)
    except AliasConfigError:
        return {}
    return {
        "enabled": cfg.enabled,
        "use_builtin": cfg.use_builtin,
        "rules": [_rule_view(rule) for rule in cfg.rules],
    }


def builtin_rules() -> tuple[AliasRule, ...]:
    return tuple(_rule_from_data(item, source_builtin=True) for item in BUILTIN_RULE_DATA)


def resolve_rules(config: AliasConfig) -> tuple[AliasRule, ...]:
    """内置规则与自定义规则叠加，按优先级从高到低排序；自定义不覆盖内置。"""
    rules = list(config.rules)
    if config.use_builtin:
        rules.extend(builtin_rules())
    return tuple(sorted(rules, key=lambda rule: -rule.priority))


def _parse_rules(raw: Any) -> tuple[AliasRule, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise AliasConfigError("alias.rules 必须是数组")
    if len(raw) > MAX_RULES:
        raise AliasConfigError(f"alias.rules 不得超过 {MAX_RULES} 条")

    rules: list[AliasRule] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, Mapping):
            raise AliasConfigError(f"alias.rules 第 {index} 条必须是对象")
        rule = _rule_from_data(item, source_builtin=False, position=index)
        if rule.name in seen:
            raise AliasConfigError(f"alias.rules 中规则名重复: {rule.name}")
        seen.add(rule.name)
        rules.append(rule)
    return tuple(rules)


def _rule_from_data(
    data: Mapping[str, Any], *, source_builtin: bool, position: int | None = None
) -> AliasRule:
    label = f"alias.rules 第 {position} 条" if position else "内置规则"
    name = str(data.get("name") or "").strip()
    if not name or len(name) > 64:
        raise AliasConfigError(f"{label} 缺少 name（1-64 字）")
    if not source_builtin and name in BUILTIN_RULE_NAMES:
        raise AliasConfigError(f"规则名与内置规则重名，无法覆盖内置规则: {name}")

    kind = str(data.get("kind") or KIND_ALIAS).strip().upper()
    if kind not in RULE_KINDS:
        raise AliasConfigError(
            f"规则「{name}」的 kind 非法: {kind}（可选 {', '.join(RULE_KINDS)}）"
        )

    triggers = _parse_str_list(data.get("triggers"), f"规则「{name}」的 triggers")
    if not triggers:
        raise AliasConfigError(f"规则「{name}」缺少 triggers")
    if len(triggers) > MAX_TRIGGERS:
        raise AliasConfigError(f"规则「{name}」的 triggers 不得超过 {MAX_TRIGGERS} 个")
    for trigger in triggers:
        if len(trigger) > MAX_TRIGGER_LEN:
            raise AliasConfigError(
                f"规则「{name}」的触发词过长（≤{MAX_TRIGGER_LEN} 字）: {trigger}"
            )

    shapes = _parse_shapes(data.get("shapes"), name)
    cardinality = _parse_cardinality(data.get("target_cardinality"), name, kind)
    priority = _parse_priority(data.get("priority"), name)
    regex = _parse_regex(data.get("regex"), name)
    return AliasRule(
        name=name,
        kind=kind,
        triggers=triggers,
        shapes=shapes,
        target_cardinality=cardinality,
        priority=priority,
        regex=regex,
        builtin=source_builtin,
    )


def _parse_shapes(raw: Any, rule_name: str) -> AliasShapes:
    """没有名称形态的规则一律拒绝：只有触发词时抽出的长度不受控。"""
    if not isinstance(raw, Mapping) or not raw:
        raise AliasConfigError(
            f"规则「{rule_name}」缺少名称形态 shapes（机构后缀 / 成对标记 / 停顿词 / 长度上限）"
        )
    suffixes = _parse_str_list(raw.get("suffixes"), f"规则「{rule_name}」的 shapes.suffixes")
    wrappers = _parse_str_list(raw.get("wrappers"), f"规则「{rule_name}」的 shapes.wrappers")
    stop_words = _parse_str_list(
        raw.get("stop_words"), f"规则「{rule_name}」的 shapes.stop_words"
    )
    max_len = (
        _require_int(raw.get("max_len"), f"规则「{rule_name}」的 shapes.max_len")
        if raw.get("max_len") is not None
        else DEFAULT_MAX_LEN
    )
    if not MIN_MAX_LEN <= max_len <= MAX_MAX_LEN:
        raise AliasConfigError(
            f"规则「{rule_name}」的 shapes.max_len 必须在 {MIN_MAX_LEN}-{MAX_MAX_LEN} 之间"
        )
    return AliasShapes(
        suffixes=suffixes, wrappers=wrappers, stop_words=stop_words, max_len=max_len
    )


def _parse_cardinality(raw: Any, rule_name: str, kind: str) -> str:
    if raw is None:
        return CARDINALITY_MANY if kind == KIND_RELATION else CARDINALITY_ONE
    value = str(raw).strip().upper()
    if value not in CARDINALITIES:
        raise AliasConfigError(
            f"规则「{rule_name}」的 target_cardinality 非法: {value}"
            f"（可选 {', '.join(CARDINALITIES)}）"
        )
    return value


def _parse_priority(raw: Any, rule_name: str) -> int:
    if raw is None:
        return 50
    value = _require_int(raw, f"规则「{rule_name}」的 priority")
    if not MIN_PRIORITY <= value <= MAX_PRIORITY:
        raise AliasConfigError(
            f"规则「{rule_name}」的 priority 必须在 {MIN_PRIORITY}-{MAX_PRIORITY} 之间"
        )
    return value


def _parse_regex(raw: Any, rule_name: str) -> str | None:
    """自由正则是补救入口，因此校验从严：长度、编译、嵌套量词、命名组。"""
    if raw is None:
        return None
    pattern = str(raw)
    if not pattern.strip():
        return None
    if len(pattern) > MAX_REGEX_LEN:
        raise AliasConfigError(f"规则「{rule_name}」的正则过长（≤{MAX_REGEX_LEN} 字符）")
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise AliasConfigError(f"规则「{rule_name}」的正则无法编译: {exc}") from exc
    if "surface" not in compiled.groupindex:
        raise AliasConfigError(f"规则「{rule_name}」的正则必须包含命名组 surface")
    if _NESTED_QUANTIFIER.search(pattern):
        raise AliasConfigError(f"规则「{rule_name}」的正则含嵌套量词，存在回溯爆炸风险")
    return pattern


def _parse_str_list(raw: Any, field: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
        raise AliasConfigError(f"{field} 必须是数组")
    if len(raw) > MAX_ITEMS_PER_SHAPE:
        raise AliasConfigError(f"{field} 不得超过 {MAX_ITEMS_PER_SHAPE} 项")
    values: list[str] = []
    for item in raw:
        text = str(item).strip()
        if not text:
            raise AliasConfigError(f"{field} 不能包含空字符串")
        if text not in values:
            values.append(text)
    return tuple(values)


def _require_bool(value: Any, field: str, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise AliasConfigError(f"{field} 必须是布尔值")


def _require_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise AliasConfigError(f"{field} 必须是整数")
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise AliasConfigError(f"{field} 必须是整数") from exc


def _rule_view(rule: AliasRule) -> dict[str, Any]:
    return {
        "name": rule.name,
        "kind": rule.kind,
        "triggers": list(rule.triggers),
        "shapes": {
            "suffixes": list(rule.shapes.suffixes),
            "wrappers": list(rule.shapes.wrappers),
            "stop_words": list(rule.shapes.stop_words),
            "max_len": rule.shapes.max_len,
        },
        "target_cardinality": rule.target_cardinality,
        "priority": rule.priority,
        "regex": rule.regex,
    }


__all__ = [
    "CARDINALITIES",
    "CARDINALITY_MANY",
    "CARDINALITY_ONE",
    "CARDINALITY_UNKNOWN",
    "KIND_ALIAS",
    "KIND_RELATION",
    "KIND_ROLE",
    "MAX_RULES",
    "RULE_KINDS",
    "AliasConfig",
    "AliasConfigError",
    "AliasRule",
    "AliasShapes",
    "alias_config_view",
    "builtin_rules",
    "parse_alias_config",
    "resolve_rules",
]
