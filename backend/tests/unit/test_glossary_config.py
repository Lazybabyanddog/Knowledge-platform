"""别名规则的配置契约：缺省值、非法输入、脏数据兼容。"""

from __future__ import annotations

import pytest

from app.services.glossary.config import (
    AliasConfigError,
    alias_config_view,
    parse_alias_config,
    resolve_rules,
)

SHAPES = {"suffixes": ["公司"], "max_len": 20}


def _rule(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "name": "自定义",
        "kind": "ALIAS",
        "triggers": ["以下简称"],
        "shapes": dict(SHAPES),
        "target_cardinality": "ONE",
        "priority": 50,
    }
    data.update(overrides)
    return data


def test_missing_alias_uses_defaults() -> None:
    config = parse_alias_config({})

    assert config.enabled is True
    assert config.use_builtin is True
    assert config.rules == ()


def test_alias_must_be_object_for_writes() -> None:
    with pytest.raises(AliasConfigError):
        parse_alias_config({"alias": "以下简称=简称"})


def test_dirty_alias_returns_empty_view() -> None:
    assert alias_config_view({"alias": ["oops"]}) == {}


def test_view_fills_defaults() -> None:
    view = alias_config_view({"alias": {"rules": [_rule()]}})

    assert view["enabled"] is True
    assert view["use_builtin"] is True
    assert view["rules"][0]["triggers"] == ["以下简称"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"kind": "ENTITY"}, "kind"),
        ({"triggers": []}, "triggers"),
        ({"triggers": ["这个触发词明显太长了超过上限"]}, "触发词过长"),
        ({"target_cardinality": "SOME"}, "target_cardinality"),
        ({"priority": 200}, "priority"),
        ({"name": ""}, "name"),
        ({"shapes": {}}, "名称形态"),
        ({"name": "简称标记"}, "内置规则重名"),
    ],
)
def test_illegal_rules_are_rejected(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(AliasConfigError) as excinfo:
        parse_alias_config({"alias": {"rules": [_rule(**overrides)]}})

    assert message in str(excinfo.value)


def test_duplicate_rule_names_are_rejected() -> None:
    with pytest.raises(AliasConfigError):
        parse_alias_config({"alias": {"rules": [_rule(), _rule()]}})


def test_rule_count_is_capped() -> None:
    rules = [_rule(name=f"规则{index}") for index in range(21)]

    with pytest.raises(AliasConfigError):
        parse_alias_config({"alias": {"rules": rules}})


def test_regex_must_compile_and_expose_surface() -> None:
    with pytest.raises(AliasConfigError, match="无法编译"):
        parse_alias_config({"alias": {"rules": [_rule(regex="(unclosed")]}})
    with pytest.raises(AliasConfigError, match="surface"):
        parse_alias_config({"alias": {"rules": [_rule(regex="(?P<full>.+)")]}})


def test_nested_quantifier_is_rejected() -> None:
    with pytest.raises(AliasConfigError, match="嵌套量词"):
        parse_alias_config({"alias": {"rules": [_rule(regex="(?P<surface>(a+)+)")]}})


def test_unknown_cardinality_defaults_by_kind() -> None:
    config = parse_alias_config({"alias": {"rules": [_rule(target_cardinality=None)]}})
    relation = parse_alias_config(
        {"alias": {"rules": [_rule(kind="RELATION", target_cardinality=None)]}}
    )

    assert config.rules[0].target_cardinality == "ONE"
    assert relation.rules[0].target_cardinality == "MANY"


def test_resolve_rules_stacks_builtin_and_sorts_by_priority() -> None:
    config = parse_alias_config({"alias": {"rules": [_rule(priority=90)]}})
    rules = resolve_rules(config)

    assert [rule.priority for rule in rules] == sorted(
        (rule.priority for rule in rules), reverse=True
    )
    assert any(rule.builtin for rule in rules)
    assert any(not rule.builtin for rule in rules)


def test_builtin_can_be_disabled() -> None:
    config = parse_alias_config({"alias": {"use_builtin": False, "rules": [_rule()]}})

    assert [rule.builtin for rule in resolve_rules(config)] == [False]
