"""抽取流水线节点单测（任务 5.5–5.6）。

用注入式抽取器覆盖三条关键路径：
- 规则抽取跑通并稳定去重；
- 可修复问题（置信度越界、证据悬空）走一次修复后提交；
- 不可修复问题（本体里没有的类型）修复耗尽后整批跳过。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.pipelines.extract.extractor import BatchExtraction, set_extractor
from app.pipelines.extract.graph import run_extract_pipeline
from app.pipelines.extract.validators import repair_extraction
from app.pipelines.extract.writer import set_candidate_writer

CLASSES = {
    "person": {"code": "person", "name": "张伟", "aliases": ["张伟"], "attributes": {}},
    "org": {"code": "org", "name": "星海科技", "aliases": ["星海科技"], "attributes": {}},
}
RELATIONS = {
    "works_at": {
        "code": "works_at",
        "name": "任职",
        "source_class_code": "person",
        "target_class_code": "org",
        "keywords": ["任职"],
    }
}
BLOCKS = [
    {"id": "b1", "seq": 1, "block_type": "paragraph", "content": "张伟，星海科技，任职"},
    {"id": "b2", "seq": 2, "block_type": "paragraph", "content": "张伟，星海科技"},
    {"id": "b3", "seq": 3, "block_type": "footer", "content": "版权所有"},
]


class ScriptedExtractor:
    name = "scripted"

    def __init__(self, *outputs: dict[str, Any] | None) -> None:
        self.outputs = list(outputs)
        self.calls = 0

    def _next(self) -> BatchExtraction:
        index = min(self.calls, len(self.outputs) - 1)
        self.calls += 1
        return BatchExtraction(raw=self.outputs[index])

    def extract(
        self,
        blocks: list[dict[str, Any]],
        classes: dict[str, dict[str, Any]],
        relations: dict[str, dict[str, Any]],
    ) -> BatchExtraction:
        return self._next()

    def repair(
        self,
        raw: dict[str, Any] | None,
        issues: list[dict[str, Any]],
        blocks: list[dict[str, Any]],
        classes: dict[str, dict[str, Any]],
        relations: dict[str, dict[str, Any]],
    ) -> BatchExtraction:
        return self._next()


@pytest.fixture(autouse=True)
def restore_hooks() -> Iterator[None]:
    yield
    set_extractor(None)
    set_candidate_writer(None)


def _state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "workspace_id": "ws-test",
        "task_id": "task-1",
        "document_id": "doc-1",
        "blocks": BLOCKS,
        "classes": CLASSES,
        "relations": RELATIONS,
        "batch_size": 2,
        "max_repairs": 1,
    }
    state.update(overrides)
    return state


def _capture_writer() -> tuple[list[dict[str, Any]], Any]:
    calls: list[dict[str, Any]] = []

    def _writer(
        workspace_id: str,
        task_id: str,
        document_id: str,
        entities: list[dict[str, Any]],
        relations: list[dict[str, Any]],
    ) -> dict[str, int]:
        calls.append(
            {
                "workspace_id": workspace_id,
                "task_id": task_id,
                "document_id": document_id,
                "entities": entities,
                "relations": relations,
            }
        )
        return {"entities": len(entities), "relations": len(relations)}

    return calls, _writer


def test_rule_based_pipeline_produces_proposed_candidates() -> None:
    calls, writer = _capture_writer()
    set_candidate_writer(writer)

    result = run_extract_pipeline(_state())

    assert result["stats"]["entities"] == 2
    assert result["stats"]["relations"] == 1
    assert "b3" in result["skipped_block_ids"]

    assert len(calls) == 1
    entities = {item["name"]: item for item in calls[0]["entities"]}
    person = entities["张伟"]
    assert person["class_code"] == "person"
    assert person["evidence_block_ids"] == ["b1", "b2"]

    relation = calls[0]["relations"][0]
    assert relation["code"] == "works_at"
    assert relation["source_id"] == person["id"]


def test_rerun_same_task_is_idempotent() -> None:
    _, writer = _capture_writer()
    set_candidate_writer(writer)

    first = run_extract_pipeline(_state())
    second = run_extract_pipeline(_state())

    first_ids = sorted(item["id"] for item in first["entities"])
    second_ids = sorted(item["id"] for item in second["entities"])
    assert first_ids == second_ids
    assert [item["id"] for item in first["relation_rows"]] == [
        item["id"] for item in second["relation_rows"]
    ]


def test_fixable_issues_are_repaired_then_committed() -> None:
    scripted = ScriptedExtractor(
        {
            "entities": [
                {
                    "ref": "e1",
                    "class_code": "person",
                    "name": "张伟",
                    "aliases": "张伟",  # 类型不对：可修复
                    "confidence": 1.5,  # 越界：可修复
                    "evidence_block_ids": ["b9"],  # 不属于本批：可修复
                }
            ],
            "relations": [],
        },
        {
            "entities": [
                {
                    "ref": "e1",
                    "class_code": "person",
                    "name": "张伟",
                    "aliases": ["张伟"],
                    "confidence": 0.8,
                    "evidence_block_ids": ["b1"],
                }
            ],
            "relations": [],
        },
    )
    set_extractor(scripted)
    calls, writer = _capture_writer()
    set_candidate_writer(writer)

    result = run_extract_pipeline(_state())

    assert scripted.calls >= 2
    assert result["repair_total"] == 1
    # 只有被页眉页脚规则过滤掉的那一批算跳过，内容批正常提交
    assert result["stats"]["batches_skipped"] == 1
    entity = calls[0]["entities"][0]
    assert entity["confidence"] == 0.8
    assert entity["evidence_block_ids"] == ["b1"]


def test_blocking_issues_skip_batch_after_repairs() -> None:
    bad = {
        "entities": [
            {
                "ref": "e1",
                "class_code": "ghost",  # 未定义的实体类：不可修复
                "name": "不存在的类型",
                "confidence": 0.9,
                "evidence_block_ids": ["b1"],
            }
        ],
        "relations": [],
    }
    scripted = ScriptedExtractor(bad, bad)
    set_extractor(scripted)
    calls, writer = _capture_writer()
    set_candidate_writer(writer)

    result = run_extract_pipeline(_state())

    assert result["stats"]["batches_skipped"] == 2
    assert len(calls) == 1  # 落库节点仍会跑一次，但没有任何候选
    assert calls[0]["entities"] == []
    assert calls[0]["relations"] == []


def test_repair_extraction_drops_unknown_types_and_keeps_valid() -> None:
    raw = {
        "entities": [
            {"ref": "e1", "class_code": "person", "name": "张伟", "confidence": 0.7},
            {"ref": "e2", "class_code": "ghost", "name": "幽灵", "confidence": 0.9},
        ],
        "relations": [
            {"source_ref": "e1", "target_ref": "e2", "code": "works_at"},
        ],
    }

    fixed, dropped = repair_extraction(
        raw, classes=CLASSES, relations=RELATIONS, block_ids={"b1"}
    )

    assert dropped == 2  # 幽灵实体 + 端点悬空的关系
    assert [item["name"] for item in fixed["entities"]] == ["张伟"]
    assert fixed["relations"] == []
