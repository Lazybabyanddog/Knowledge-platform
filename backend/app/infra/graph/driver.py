"""Neo4j 驱动单例与 schema 初始化。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from neo4j import Driver, GraphDatabase

from app.core.config import settings

_SCHEMA_FILE = Path(__file__).with_name("schema.cypher")
_driver: Driver | None = None


def get_driver() -> Driver:
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            max_connection_lifetime=settings.neo4j_max_connection_lifetime,
        )
    return _driver


def close_driver() -> None:
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


@contextmanager
def graph_session() -> Iterator[Any]:
    with get_driver().session(database=settings.neo4j_database) as session:
        yield session


def open_transaction():
    return get_driver().session(database=settings.neo4j_database)


def load_schema_statements() -> list[str]:
    """把 schema.cypher 按分号切成独立语句，忽略注释与空行。"""
    statements: list[str] = []
    buffer: list[str] = []
    for raw_line in _SCHEMA_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(buffer).strip().rstrip(";").strip())
            buffer.clear()

    remainder = "\n".join(buffer).strip().rstrip(";").strip()
    if remainder:
        statements.append(remainder)
    return statements


def apply_schema() -> list[str]:
    """执行 schema.cypher，返回已执行的语句列表（幂等）。"""
    statements = load_schema_statements()
    with graph_session() as session:
        for statement in statements:
            session.run(statement).consume()
    return statements


def check_connectivity() -> bool:
    try:
        get_driver().verify_connectivity()
        return True
    except Exception:  # noqa: BLE001 - 健康检查不应抛出
        return False


__all__ = [
    "apply_schema",
    "check_connectivity",
    "close_driver",
    "get_driver",
    "graph_session",
    "load_schema_statements",
    "open_transaction",
]
