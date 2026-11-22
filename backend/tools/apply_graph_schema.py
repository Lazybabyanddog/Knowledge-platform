"""执行 Neo4j 约束与索引初始化，并回显当前图库 schema。

对应 design.md「初始化顺序」第 3 步，可重复执行（幂等）。
"""

from __future__ import annotations

from app.infra.graph.driver import apply_schema, close_driver, graph_session


def main() -> int:
    statements = apply_schema()
    print(f"executed_statements={len(statements)}")

    with graph_session() as session:
        constraints = session.run(
            "SHOW CONSTRAINTS YIELD name, type RETURN name, type ORDER BY name"
        )
        for record in constraints:
            print(f"constraint\t{record['name']}\t{record['type']}")

        indexes = session.run(
            "SHOW INDEXES YIELD name, type, entityType "
            "RETURN name, type, entityType ORDER BY name"
        )
        for record in indexes:
            print(f"index\t{record['name']}\t{record['type']}\t{record['entityType']}")

    close_driver()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
