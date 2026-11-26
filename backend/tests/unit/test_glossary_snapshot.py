"""快照视图：详情页可查，但脏数据与超限数据都不会把接口打挂。"""

from __future__ import annotations

from app.services.glossary.snapshot import DETAIL_ENTRY_LIMIT, glossary_view


def _entry(index: int) -> dict[str, object]:
    return {"surface": f"名称{index}", "certainty": "CONFIRMED", "canonical": f"全称{index}"}


def test_missing_or_dirty_stats_return_empty_view() -> None:
    assert glossary_view(None) == {}
    assert glossary_view({}) == {}
    assert glossary_view({"glossary": "not-a-dict"}) == {}
    assert glossary_view({"glossary": []}) == {}


def test_view_reports_counts_and_status() -> None:
    view = glossary_view(
        {
            "glossary": {
                "status": "ok",
                "builtin_version": 1,
                "counts": {"raw": 3, "confirmed": 2, "candidate": 1, "dropped": 0},
                "rules": [{"name": "简称标记", "kind": "ALIAS", "priority": 60}],
                "entries": [_entry(0), _entry(1)],
            }
        }
    )

    assert view["status"] == "ok"
    assert view["builtin_version"] == 1
    assert view["counts"]["confirmed"] == 2
    assert len(view["entries"]) == 2
    assert view["truncated"] is False


def test_degraded_reason_is_visible() -> None:
    view = glossary_view(
        {
            "glossary": {
                "status": "degraded",
                "error": "RuntimeError: boom",
                "counts": {"raw": 0, "confirmed": 0, "candidate": 0, "dropped": 0},
                "entries": [],
            }
        }
    )

    assert view["status"] == "degraded"
    assert "boom" in view["error"]
    assert view["entries"] == []


def test_view_caps_entries_and_flags_truncation() -> None:
    entries = [_entry(index) for index in range(DETAIL_ENTRY_LIMIT + 20)]

    view = glossary_view({"glossary": {"status": "ok", "entries": entries}})

    assert len(view["entries"]) == DETAIL_ENTRY_LIMIT
    assert view["truncated"] is True


def test_non_mapping_entries_are_skipped() -> None:
    view = glossary_view(
        {"glossary": {"status": "ok", "entries": [_entry(0), "脏数据", None, _entry(1)]}}
    )

    assert len(view["entries"]) == 2
