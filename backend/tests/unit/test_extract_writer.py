"""抽取落库单测：persist 锁降级、属性冲突计数与关系实例幂等。

这几条规则决定"缓存故障不能挡住写图"以及"人工/先到的属性不被覆盖"。
"""

from __future__ import annotations

import json
from typing import Any

from app.pipelines.extract import writer


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    def delete(self, key: str) -> None:
        self.data.pop(key, None)


class _FakeRepo:
    def __init__(
        self,
        resolved: dict[tuple[str, str], dict[str, Any]] | None = None,
        merged: dict[str, str] | None = None,
        stored: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.resolved = resolved or {}
        self.merged = merged or {}
        self.stored = stored or {}
        self.entities: list[dict[str, Any]] = []
        self.relations: list[dict[str, Any]] = []

    def resolve_by_identity(
        self, workspace_id: str, class_codes: list[str], identity_keys: list[str]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        return {
            key: value
            for key, value in self.resolved.items()
            if key[0] in class_codes and key[1] in identity_keys
        }

    def resolve_merged_target(self, workspace_id: str, entity_id: str) -> str | None:
        return self.merged.get(entity_id)

    def get_entity(self, workspace_id: str, entity_id: str) -> dict[str, Any] | None:
        return self.stored.get(entity_id)

    def upsert_entities(self, rows: list[dict[str, Any]]) -> int:
        self.entities = rows
        return len(rows)

    def upsert_relations(self, rows: list[dict[str, Any]]) -> int:
        self.relations = rows
        return len(rows)


def _entity(**overrides: Any) -> dict[str, Any]:
    item = {
        "id": "e-new",
        "class_code": "person",
        "name": "张伟",
        "normalized_name": "张伟",
        "identity_key": "id:uscc:91310000XYZW",
        "identity_kind": "uscc",
        "aliases": [],
        "attributes": {"phone": "13900000000"},
        "confidence": 0.8,
        "evidence_block_ids": ["b1"],
    }
    item.update(overrides)
    return item


def _install(monkeypatch: Any, repo: _FakeRepo) -> None:
    monkeypatch.setattr(writer, "EntityRepository", lambda: repo)


def test_persist_degrades_without_failing_when_redis_unavailable(monkeypatch: Any) -> None:
    repo = _FakeRepo()
    _install(monkeypatch, repo)

    def _boom() -> Any:
        raise ConnectionError("redis down")

    monkeypatch.setattr(writer, "get_redis", _boom)

    counts = writer.neo4j_writer("ws-1", "task-1", "doc-1", [_entity()], [])

    assert counts["entities"] == 1
    assert counts["persist_lock_degraded"] == 1
    assert len(repo.entities) == 1


def test_persist_does_not_flag_degraded_when_lock_acquired(monkeypatch: Any) -> None:
    repo = _FakeRepo()
    _install(monkeypatch, repo)
    redis = _FakeRedis()
    monkeypatch.setattr(writer, "get_redis", lambda: redis)

    counts = writer.neo4j_writer("ws-1", "task-1", "doc-1", [_entity()], [])

    assert counts["persist_lock_degraded"] == 0
    assert redis.data == {}


def test_attribute_conflict_is_counted_without_overwriting_existing(monkeypatch: Any) -> None:
    existing = {
        "id": "e-existing",
        "class_code": "person",
        "name": "张伟",
        "normalized_name": "张伟",
        "aliases": ["张伟"],
        "description": None,
        "status": "proposed",
        "confidence": 0.9,
        "extraction_task_id": "task-0",
        "document_id": "doc-0",
        "document_ids": ["doc-0"],
        "evidence_block_ids": ["b0"],
        "identity_key": "id:uscc:91310000XYZW",
        "identity_kind": "uscc",
        "attributes": {"phone": "13800000000"},
        "created_at_ms": 1,
        "updated_at_ms": 1,
    }
    repo = _FakeRepo({("person", "id:uscc:91310000XYZW"): existing})
    _install(monkeypatch, repo)
    monkeypatch.setattr(writer, "get_redis", lambda: _FakeRedis())

    counts = writer.neo4j_writer("ws-1", "task-1", "doc-1", [_entity()], [])

    assert counts["attribute_conflicts"] == 1
    merged = json.loads(repo.entities[0]["attributes_json"])
    assert merged == {"phone": "13800000000"}
    assert repo.entities[0]["id"] == "e-existing"


def test_resolve_entity_rows_follows_merged_target(monkeypatch: Any) -> None:
    merged_row = {
        "id": "e-merged",
        "class_code": "person",
        "status": "merged",
        "attributes": {},
        "aliases": [],
        "name": "张伟",
        "document_ids": ["doc-0"],
        "evidence_block_ids": [],
        "confidence": 0.9,
        "description": None,
        "merged_into": "e-target",
        "created_at_ms": 1,
        "updated_at_ms": 1,
    }
    target_row = {
        "id": "e-target",
        "class_code": "person",
        "status": "confirmed",
        "attributes": {},
        "aliases": [],
        "name": "张伟",
        "document_ids": ["doc-0"],
        "evidence_block_ids": [],
        "confidence": 0.9,
        "description": None,
        "created_at_ms": 1,
        "updated_at_ms": 1,
    }
    repo = _FakeRepo(
        {("person", "name:张伟"): merged_row},
        merged={"e-merged": "e-target"},
        stored={"e-target": target_row},
    )

    rows, id_by_key, _ = writer._resolve_entity_rows(
        repo, "ws-1", "task-1", "doc-1", [_entity(identity_key="name:张伟")], 1000
    )

    assert len(rows) == 1
    # 命中已合并节点时把候选并到合并目标，避免在图里复活旧节点
    assert rows[0]["id"] == "e-target"
    assert id_by_key["person:name:张伟"] == "e-target"


def test_same_evidence_relation_stays_single_instance(monkeypatch: Any) -> None:
    repo = _FakeRepo()
    _install(monkeypatch, repo)
    monkeypatch.setattr(writer, "get_redis", lambda: _FakeRedis())

    entities = [
        _entity(id="e-a", name="张伟", identity_key="name:张伟"),
        _entity(id="e-b", class_code="org", name="星海科技", identity_key="name:星海科技"),
    ]
    relation = {
        "code": "works_at",
        "source_key": "person:name:张伟",
        "target_key": "org:name:星海科技",
        "evidence_block_id": "b1",
        "evidence_text": "张伟，星海科技，任职",
        "confidence": 0.8,
    }

    counts = writer.neo4j_writer("ws-1", "task-1", "doc-1", entities, [relation, relation])

    assert counts["relations"] == 1
    assert repo.relations[0]["evidence_key"]
