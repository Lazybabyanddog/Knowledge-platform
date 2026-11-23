"""存量重抽 CLI。

用法（在 ``backend/`` 目录下执行）::

    python -m scripts.backfill_extraction --workspace <id> --user <username> --dry-run
    python -m scripts.backfill_extraction --workspace <id> --user <username> --yes
    python -m scripts.backfill_extraction --workspace <id> --user <username> \
        --documents d1,d2 --classes company,person --force --yes

真实重抽会先清掉文档的旧图谱产出，默认要求显式 ``--yes``；``--dry-run``
只统计不写图，随时可跑。退出码：0 全部处理完，1 有文档失败，2 参数/前置检查不通过。
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime

from sqlalchemy import select

from app.core.deps import Principal
from app.infra.db.models import AppUser
from app.infra.db.repositories import UserRepository
from app.infra.db.session import session_scope
from app.schemas.backfill import BackfillRequest
from app.services.backfill_service import BackfillService


def _parse_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_dt(raw: str | None, field_name: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        raise SystemExit(f"{field_name} 不是合法的 ISO 时间：{raw}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按新规程重抽存量文档的实体与关系")
    parser.add_argument("--workspace", required=True, help="工作空间 ID")
    parser.add_argument("--user", required=True, help="执行人用户名（需存在于该工作空间）")
    parser.add_argument("--documents", help="文档 ID 列表，逗号分隔")
    parser.add_argument("--classes", help="实体类编码列表，逗号分隔")
    parser.add_argument("--since", help="只处理该时间之后创建的抽取任务（ISO 时间）")
    parser.add_argument("--until", help="只处理该时间之前创建的抽取任务（ISO 时间）")
    parser.add_argument(
        "--lagging-only", action="store_true", help="只处理本体版本落后的文档"
    )
    parser.add_argument("--limit", type=int, default=None, help="本次最多处理多少份文档")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不改图谱")
    parser.add_argument("--force", action="store_true", help="已重抽过的文档也再跑一遍")
    parser.add_argument("--yes", action="store_true", help="确认执行真实重抽")
    return parser


def _resolve_principal(session, workspace_id: uuid.UUID, username: str) -> Principal:
    user = session.scalar(
        select(AppUser).where(
            AppUser.workspace_id == workspace_id,
            AppUser.username == username,
        )
    )
    if user is None:
        raise SystemExit(f"工作空间内找不到用户：{username}")
    return Principal(user=user, roles=UserRepository(session).list_roles(user.id))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        workspace_id = uuid.UUID(args.workspace)
    except ValueError:
        print("--workspace 不是合法的 UUID", file=sys.stderr)
        return 2
    if not args.dry_run and not args.yes:
        print("真实重抽会先删掉旧图谱产出，请加 --yes 确认（或先跑 --dry-run）", file=sys.stderr)
        return 2

    payload = BackfillRequest(
        document_ids=_parse_ids(args.documents),
        class_codes=_parse_ids(args.classes),
        created_from=_parse_dt(args.since, "--since"),
        created_to=_parse_dt(args.until, "--until"),
        lagging_only=args.lagging_only,
        dry_run=args.dry_run,
        force=args.force,
        limit=args.limit,
    )

    with session_scope() as session:
        principal = _resolve_principal(session, workspace_id, args.user)
        batch = BackfillService(session).run(principal, payload)
        report = dict(batch.report or {})
        print(
            json.dumps(
                {"batch_id": batch.id, "status": batch.status, "report": report},
                ensure_ascii=False,
                indent=2,
            )
        )
        failures = report.get("failures") or []
        if failures:
            print(f"有 {len(failures)} 份文档重抽失败，见上报告", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
