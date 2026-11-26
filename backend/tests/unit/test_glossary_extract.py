"""规则执行器：三种缩写写法、右边界收紧、正则补救入口、RELATION 只产候选。"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.glossary.config import parse_alias_config, resolve_rules
from app.services.glossary.extract import scan
from app.services.glossary.judge import judge

BLOCK = SimpleNamespace(id="block-1", seq=1, is_discarded=False)


def _scan(text: str, config: dict | None = None, block_id: str = "block-1"):
    block = SimpleNamespace(id=block_id, seq=1, content=text, is_discarded=False)
    rules = resolve_rules(parse_alias_config(config or {}))
    return scan([block], rules)


def _pairs(text: str, config: dict | None = None) -> set[tuple[str, str]]:
    return {(hit.surface, hit.canonical or "") for hit in _scan(text, config)}


def test_bare_trigger_extracts_surface_and_full_name() -> None:
    pairs = _pairs("江苏省苏州市盐务管理局以下简称苏州盐务局，负责盐业管理。")

    assert pairs == {("苏州盐务局", "江苏省苏州市盐务管理局")}


def test_parenthetical_trigger_binds_full_name() -> None:
    pairs = _pairs("江苏盐业集团（简称苏盐集团）成立于一九九八年。")

    assert pairs == {("苏盐集团", "江苏盐业集团")}


def test_nested_brackets_stay_inside_canonical() -> None:
    text = "《江苏省〈盐业管理条例〉实施办法》（以下简称《江苏盐业实施办法》）自发布之日起施行。"
    pairs = _pairs(text)

    assert pairs == {("江苏盐业实施办法", "《江苏省〈盐业管理条例〉实施办法》")}


def test_right_boundary_never_crosses_sentence_end() -> None:
    text = "江苏省盐业管理局（以下简称江苏省盐业管理办法）。下一句无关内容继续。"
    hits = _scan(text)

    assert hits, "应至少产出一条命中"
    for hit in hits:
        assert "。" not in hit.surface
        assert "下一句" not in hit.surface


def test_right_boundary_respects_max_len() -> None:
    config = {
        "alias": {
            "use_builtin": False,
            "rules": [
                {
                    "name": "短名",
                    "kind": "ALIAS",
                    "triggers": ["简称"],
                    "shapes": {"suffixes": ["公司"], "max_len": 6},
                }
            ],
        }
    }
    hits = _scan("（简称某某某某某某某某公司）", config)

    assert hits == [] or all(len(hit.surface) <= 6 for hit in hits)


def test_rules_without_suffix_do_not_invent_canonical() -> None:
    # 触发词前面只有标点、没有名称时不应凭空产出对照
    assert _pairs("，以下简称苏州盐务局。", {"alias": {"use_builtin": False, "rules": []}}) == set()


def test_regex_escape_hatch_still_works() -> None:
    config = {
        "alias": {
            "use_builtin": False,
            "rules": [
                {
                    "name": "甲方",
                    "kind": "ALIAS",
                    "triggers": ["甲方"],
                    "shapes": {"suffixes": ["公司"]},
                    "regex": r"(?P<canonical>[\u4e00-\u9fa5]{2,20}公司)（(?P<surface>甲方)）",
                }
            ],
        }
    }

    assert _pairs("华信科技有限公司（甲方）与乙方签约。", config) == {
        ("甲方", "华信科技有限公司")
    }


def test_zero_width_regex_terminates() -> None:
    config = {
        "alias": {
            "use_builtin": False,
            "rules": [
                {
                    "name": "零宽",
                    "kind": "ALIAS",
                    "triggers": ["简称"],
                    "shapes": {"suffixes": ["公司"]},
                    "regex": r"(?P<surface>)(?:公司)",
                }
            ],
        }
    }

    assert _scan("公司公司公司", config) == []


def test_relation_rules_only_produce_candidates() -> None:
    text = "子公司苏州某某科技有限公司，子公司无锡某某贸易有限公司，均纳入合并范围。"
    hits = _scan(text)
    entries, _ = judge(hits)

    relation = [entry for entry in entries if entry.surface == "子公司"]
    assert relation, "应识别出关系类称谓"
    assert relation[0].certainty == "CANDIDATE"
    assert relation[0].canonical is None
    assert set(relation[0].candidates) == {"苏州某某科技有限公司", "无锡某某贸易有限公司"}


def test_discarded_blocks_are_skipped() -> None:
    block = SimpleNamespace(id="b", seq=1, content="X（简称Y公司）", is_discarded=True)
    rules = resolve_rules(parse_alias_config({}))

    assert scan([block], rules) == []
