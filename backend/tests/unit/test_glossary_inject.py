"""读侧注入：只注入片段里出现的别名，不改正文、不进引用。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from app.services.glossary.inject import (
    MAX_LINES_PER_HIT,
    PREFIX_ALIAS,
    PREFIX_GENERAL,
    attach_glossary,
    render_glossary,
)
from app.services.retrieval_service import ChunkHit, build_ask_messages, hit_to_source


class _FakeSession:
    """按 id 返回预置解析任务，并记录查询次数以断言只查一次。"""

    def __init__(self, tasks: list[Any]) -> None:
        self._tasks = tasks
        self.queries = 0

    def scalars(self, _stmt: Any) -> Any:
        self.queries += 1
        return SimpleNamespace(all=lambda: self._tasks)


def _task(stats: dict[str, Any] | None) -> Any:
    return SimpleNamespace(id=uuid.uuid4(), stats=stats or {})


def _snapshot(entries: list[dict[str, Any]], *, status: str = "ok") -> dict[str, Any]:
    return {"glossary": {"status": status, "entries": entries, "counts": {}}}


def _entry(
    surface: str,
    *,
    canonical: str | None = None,
    candidates: tuple[str, ...] = (),
    certainty: str = "CONFIRMED",
    kind: str = "ALIAS",
    priority: int = 50,
    block_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "surface": surface,
        "canonical": canonical,
        "candidates": list(candidates),
        "certainty": certainty,
        "kind": kind,
        "priority": priority,
        "block_ids": list(block_ids),
    }


def _hit(
    content: str,
    *,
    task_id: str | None = None,
    block_ids: list[str] | None = None,
    index: int = 1,
) -> ChunkHit:
    return ChunkHit(
        chunk_id=f"chunk-{index}",
        document_id="doc-1",
        title="起诉状",
        version_no=1,
        content=content,
        score=0.9,
        heading_path=None,
        first_page_no=index,
        last_page_no=index,
        block_ids=list(block_ids or []),
        parse_task_id=task_id,
    )


def test_only_aliases_present_in_hit_are_injected() -> None:
    task = _task(
        _snapshot(
            [
                _entry("鲁潍公司", canonical="山东鲁潍机械有限公司", block_ids=("b2",)),
                _entry("盐务局", canonical="江苏省苏州市盐务管理局", block_ids=("b9",)),
            ]
        )
    )
    session = _FakeSession([task])
    hit = _hit("原告鲁潍公司诉称……", task_id=str(task.id), block_ids=["b2"])

    [result] = attach_glossary(session, [hit])

    assert [line.surface for line in result.glossary] == ["鲁潍公司"]


def test_text_containment_is_the_fallback_when_block_ids_miss() -> None:
    task = _task(_snapshot([_entry("盐务局", canonical="苏州市盐务管理局", block_ids=("b9",))]))
    session = _FakeSession([task])
    hit = _hit("被告盐务局辩称……", task_id=str(task.id), block_ids=["b1"])

    [result] = attach_glossary(session, [hit])

    assert result.glossary[0].text == "盐务局 = 苏州市盐务管理局"


def test_candidate_wording_is_weakened() -> None:
    task = _task(
        _snapshot(
            [
                _entry(
                    "子公司",
                    candidates=("苏州某某科技有限公司", "无锡某某有限公司"),
                    certainty="CANDIDATE",
                    kind="RELATION",
                )
            ]
        )
    )
    session = _FakeSession([task])
    hit = _hit("其子公司承担连带责任。", task_id=str(task.id))

    [result] = attach_glossary(session, [hit])

    assert render_glossary(result) == (
        f"{PREFIX_GENERAL}子公司 可能指 苏州某某科技有限公司 / 无锡某某有限公司。"
    )


def test_confirmed_line_uses_alias_prefix() -> None:
    task = _task(_snapshot([_entry("鲁潍公司", canonical="山东鲁潍机械有限公司")]))
    session = _FakeSession([task])

    [result] = attach_glossary(session, [_hit("原告鲁潍公司诉称。", task_id=str(task.id))])

    assert render_glossary(result) == f"{PREFIX_ALIAS}鲁潍公司 = 山东鲁潍机械有限公司。"


def test_line_limit_truncates_by_priority() -> None:
    entries = [
        _entry(f"别名{index}", canonical=f"全称{index}公司", priority=index)
        for index in range(8)
    ]
    task = _task(_snapshot(entries))
    session = _FakeSession([task])
    content = "".join(f"别名{index} " for index in range(8))

    [result] = attach_glossary(session, [_hit(content, task_id=str(task.id))])

    assert len(result.glossary) == MAX_LINES_PER_HIT
    assert [line.surface for line in result.glossary] == [
        "别名7",
        "别名6",
        "别名5",
        "别名4",
        "别名3",
    ]


def test_confirmed_entries_outrank_higher_priority_candidates() -> None:
    task = _task(
        _snapshot(
            [
                _entry("甲公司", candidates=("乙公司",), certainty="CANDIDATE", priority=90),
                _entry("丙公司", canonical="丙股份有限公司", priority=10),
            ]
        )
    )
    session = _FakeSession([task])

    [result] = attach_glossary(session, [_hit("甲公司 与 丙公司", task_id=str(task.id))])

    assert [line.surface for line in result.glossary] == ["丙公司", "甲公司"]


def test_single_query_serves_many_hits() -> None:
    task = _task(_snapshot([_entry("鲁潍公司", canonical="山东鲁潍机械有限公司")]))
    session = _FakeSession([task])
    hits = [
        _hit("原告鲁潍公司诉称。", task_id=str(task.id), index=index) for index in range(1, 4)
    ]

    results = attach_glossary(session, hits)

    assert session.queries == 1
    assert all(result.glossary for result in results)


def test_hits_without_parse_task_keep_empty_glossary() -> None:
    session = _FakeSession([])

    [result] = attach_glossary(session, [_hit("没有任何词典的片段。")])

    assert result.glossary == ()
    assert session.queries == 0


def test_degraded_snapshot_is_not_injected() -> None:
    task = _task({"glossary": {"status": "degraded", "error": "boom", "entries": []}})
    session = _FakeSession([task])
    hit = _hit("原告鲁潍公司诉称。", task_id=str(task.id))

    [result] = attach_glossary(session, [hit])

    assert result.glossary == ()
    assert render_glossary(result) == ""


def test_messages_are_byte_identical_without_glossary() -> None:
    hit = _hit("原告鲁潍公司诉称……", task_id=None)
    injected = attach_glossary(_FakeSession([]), [hit])[0]

    messages = build_ask_messages("原告是谁？", [injected])

    assert messages[1]["content"] == (
        "【资料】\n[1] 《起诉状》第 1 页\n原告鲁潍公司诉称……\n\n"
        "【问题】\n原告是谁？\n\n"
        "请用中文回答，并在引用处标注 [编号]；资料没有覆盖的部分请明确说明。"
    )


def test_messages_carry_glossary_line_before_content() -> None:
    task = _task(_snapshot([_entry("鲁潍公司", canonical="山东鲁潍机械有限公司")]))
    session = _FakeSession([task])
    [hit] = attach_glossary(session, [_hit("原告鲁潍公司诉称……", task_id=str(task.id))])

    messages = build_ask_messages("原告是谁？", [hit])
    user = messages[1]["content"]

    expected = (
        "[1] 《起诉状》第 1 页\n"
        "本文简称：鲁潍公司 = 山东鲁潍机械有限公司。\n"
        "原告鲁潍公司诉称……"
    )
    assert expected in user
    assert "[1]" in user


def test_glossary_never_leaks_into_source_snippet() -> None:
    task = _task(_snapshot([_entry("鲁潍公司", canonical="山东鲁潍机械有限公司")]))
    session = _FakeSession([task])
    content = "原告鲁潍公司诉称……" * 80
    [hit] = attach_glossary(session, [_hit(content, task_id=str(task.id))])

    source = hit_to_source(hit, 1)

    assert source["snippet"] == content[:400]
    assert "本文简称" not in source["snippet"]
    assert source["index"] == 1
    assert source["page_no"] == 1
