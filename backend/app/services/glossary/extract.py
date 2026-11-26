"""规则执行器：在解析块上定位触发词并裁出名称边界。

纯函数、无 IO、无模型调用，输入是块列表与已解析的规则，输出是未经判定的原始命中。
"触发词只给左边界"，右边界由机构后缀 / 成对标记 / 停顿词 / 长度上限共同收住；
收不住就不产出，宁缺勿错。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from app.services.glossary.config import (
    KIND_ALIAS,
    KIND_RELATION,
    KIND_ROLE,
    AliasRule,
    AliasShapes,
)

HARD_STOPS = "。；;，,、！？!?：:\n\r\t 　"
_OPENERS = "（(《〈“‘"
_CLOSERS = "）)》〉”’"
_BRACKETS = {"（": "）", "(": ")", "《": "》", "〈": "〉", "“": "”", "‘": "’"}
_TRIM = " \t\r\n　。；;，,、！？!?：:\"'“”‘’"

MAX_STEPS = 2000
MAX_HITS = 200


@dataclass(frozen=True, slots=True)
class RawHit:
    """未经判定的命中；canonical 与 target 二选一（ALIAS/ROLE 用前者）。"""

    surface: str
    kind: str
    cardinality: str
    priority: int
    rule: str
    block_id: str
    block_seq: int
    canonical: str | None = None
    target: str | None = None


def scan(
    blocks: Iterable[Any],
    rules: Iterable[AliasRule],
    *,
    max_steps: int = MAX_STEPS,
    max_hits: int = MAX_HITS,
) -> list[RawHit]:
    """按优先级从高到低执行规则；被更高优先级占用的位置不再重复产出。"""
    ordered = list(rules)
    hits: list[RawHit] = []
    steps = 0

    for block in blocks:
        if getattr(block, "is_discarded", False):
            continue
        text = str(getattr(block, "content", "") or "")
        if not text.strip():
            continue
        block_id = str(getattr(block, "id", "") or "")
        block_seq = int(getattr(block, "seq", 0) or 0)
        used: list[tuple[int, int]] = []

        for rule in ordered:
            for span, raw in _rule_hits(text, rule, shapes=rule.shapes):
                steps += 1
                if steps > max_steps or len(hits) >= max_hits:
                    return hits
                if _covered(used, span[0]):
                    continue
                used.append(span)
                hits.append(
                    RawHit(
                        surface=raw[0],
                        canonical=raw[1],
                        target=raw[2],
                        kind=rule.kind,
                        cardinality=rule.target_cardinality,
                        priority=rule.priority,
                        rule=rule.name,
                        block_id=block_id,
                        block_seq=block_seq,
                    )
                )
    return hits


def _rule_hits(
    text: str, rule: AliasRule, *, shapes: AliasShapes
) -> Iterator[tuple[tuple[int, int], tuple[str, str | None, str | None]]]:
    if rule.regex:
        yield from _regex_hits(text, rule)
        return
    for trigger in sorted(rule.triggers, key=len, reverse=True):
        start = text.find(trigger)
        while start >= 0:
            hit = _trigger_hit(text, start, trigger, rule, shapes)
            if hit is not None:
                yield hit
            start = text.find(trigger, start + len(trigger))


def _trigger_hit(
    text: str, start: int, trigger: str, rule: AliasRule, shapes: AliasShapes
) -> tuple[tuple[int, int], tuple[str, str | None, str | None]] | None:
    """按规则类型决定谁是 surface、谁是规范名。

    - ALIAS：触发词后是简称，触发词前（可带括号）是全称；
    - ROLE：触发词本身就是 surface（"原告"），后面是本案当事人；
    - RELATION：触发词是类别词，后面只能给出候选实体，不能给出确定性对照。
    """
    after = start + len(trigger)
    name, end = _right_name(text, after, shapes)

    if rule.kind == KIND_RELATION:
        if not name:
            return None
        return (start, end), (trigger, None, name)

    if rule.kind == KIND_ROLE:
        if not name or not _name_like(name, shapes):
            return None
        return (start, end), (trigger, name, None)

    if rule.kind == KIND_ALIAS:
        if not name:
            return None
        canonical = _left_name(text, start, shapes)
        if not canonical or not _name_like(canonical, shapes):
            return None
        if canonical == name:
            return None
        return (start, end), (name, canonical, None)

    return None


def _regex_hits(
    text: str, rule: AliasRule
) -> Iterator[tuple[tuple[int, int], tuple[str, str | None, str | None]]]:
    pattern = re.compile(rule.regex or "")
    for match in pattern.finditer(text):
        if match.start() == match.end():
            # 零宽匹配会让循环原地打转，显式跳过
            continue
        surface = (match.groupdict().get("surface") or "").strip()
        canonical = (match.groupdict().get("canonical") or "").strip() or None
        if not surface:
            continue
        if rule.kind == KIND_RELATION:
            yield (match.start(), match.end()), (surface, None, canonical)
        elif rule.kind == KIND_ROLE:
            yield (match.start(), match.end()), (surface, canonical, None)
        else:
            yield (match.start(), match.end()), (surface, canonical, None)


def _right_name(text: str, start: int, shapes: AliasShapes) -> tuple[str | None, int]:
    """从触发词右侧裁出名称：成对标记优先，其次按停顿词与后缀词收尾。"""
    length = len(text)
    index = start
    while index < length and text[index] in " 　":
        index += 1
    if index >= length:
        return None, length

    opener = text[index]
    closer = _BRACKETS.get(opener)
    if closer:
        end = text.find(closer, index + 1)
        if end == -1:
            return None, index
        inner = text[index + 1 : end].strip(_TRIM)
        return (inner or None), end + 1

    limit = min(length, index + shapes.max_len)
    end = index
    while end < limit:
        if text[end] in HARD_STOPS or text[end] in _CLOSERS:
            break
        if _starts_with(text, end, shapes.stop_words):
            break
        end += 1
    segment = text[index:end]
    if shapes.suffixes:
        cut = _first_suffix_end(segment, shapes.suffixes)
        if cut:
            segment = segment[:cut]
    segment = segment.strip(_TRIM)
    return (segment or None), end


def _left_name(text: str, stop: int, shapes: AliasShapes) -> str | None:
    """从触发词左侧向左裁出规范名；括号成对出现时按深度跳过。"""
    index = stop - 1
    while index >= 0 and text[index] in " 　":
        index -= 1
    if index < 0:
        return None
    # "全称（以下简称简称）"：先跨过左括号这个分隔符
    if text[index] in _OPENERS:
        index -= 1

    depth = 0
    chars: list[str] = []
    while index >= 0 and len(chars) < shapes.max_len:
        char = text[index]
        if char in _CLOSERS:
            depth += 1
        elif char in _OPENERS:
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and char in HARD_STOPS:
            break
        chars.append(char)
        index -= 1
    name = "".join(reversed(chars)).strip(_TRIM)
    return name or None


def _name_like(name: str, shapes: AliasShapes) -> bool:
    """规范名必须"像名字"：以成对标记收尾，或以已配置的机构后缀收尾。"""
    if len(name) < 2:
        return False
    if name[-1] in _CLOSERS:
        return True
    if shapes.suffixes:
        return any(name.endswith(suffix) for suffix in shapes.suffixes)
    return True


def _first_suffix_end(segment: str, suffixes: tuple[str, ...]) -> int | None:
    for end in range(1, len(segment) + 1):
        head = segment[:end]
        if any(head.endswith(suffix) for suffix in suffixes):
            return end
    return None


def _starts_with(text: str, index: int, words: tuple[str, ...]) -> bool:
    return any(text.startswith(word, index) for word in words)


def _covered(used: list[tuple[int, int]], position: int) -> bool:
    return any(start <= position < end for start, end in used)


__all__ = ["MAX_HITS", "MAX_STEPS", "RawHit", "scan"]
