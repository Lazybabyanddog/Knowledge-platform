"""发布前的迁移阶段门禁。

蓝绿期间新旧两版共用同一个库，所以本次发布只能包含向后兼容（expand）的变更；
删列、收紧非空、重命名这类 contract 动作必须留到下一次发布。

用法::

    python scripts/check_migrations.py                 # 只看 git 里未提交的迁移
    python scripts/check_migrations.py --all           # 扫描全部（CI 兜底）
    python scripts/check_migrations.py --allow-contract # 明确知道自己要跑 contract

退出码：0 通过；1 命中破坏性变更；2 用法/环境错误。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

VERSIONS_DIR = Path("backend/alembic/versions")

SIMPLE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bop\.drop_column\s*\("), "drop_column"),
    (re.compile(r"\bop\.drop_table\s*\("), "drop_table"),
    (re.compile(r"\bop\.drop_constraint\s*\("), "drop_constraint"),
    (re.compile(r"\bop\.rename_table\s*\("), "rename_table"),
    (re.compile(r"\bop\.execute\s*\([^)]*\bDROP\b", re.IGNORECASE | re.DOTALL), "raw_drop"),
)

# alter_column 的参数里常有嵌套调用（sa.String(255)），正则截不准，改用括号配平
ALTER_COLUMN = re.compile(r"\bop\.alter_column\s*(?P<open>\()")


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _call_body(text: str, open_index: int) -> str:
    """返回从 open_index 处的 '(' 到配对 ')' 之间的内容。"""
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[open_index + 1 : index]
    return text[open_index + 1 :]


def scan_file(path: Path) -> list[str]:
    """返回该文件里的破坏性变更描述，形如 ``file:行: 规则``。"""
    text = path.read_text(encoding="utf-8")
    findings: list[str] = []

    for pattern, rule in SIMPLE_RULES:
        for match in pattern.finditer(text):
            findings.append(f"{path.as_posix()}:{_line_of(text, match.start())}: {rule}")

    for match in ALTER_COLUMN.finditer(text):
        body = _call_body(text, match.start("open"))
        if re.search(r"nullable\s*=\s*False", body):
            findings.append(
                f"{path.as_posix()}:{_line_of(text, match.start())}: alter_column(nullable=False)"
            )

    return findings


def changed_migration_files() -> list[Path]:
    """git 工作区里新增/修改的迁移文件（未提交也算）。"""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", VERSIONS_DIR.as_posix()],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"无法读取 git 状态（{exc}）；请改用 --all 或 --path") from exc

    files: list[Path] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        status, raw_path = line[:2], line[3:].strip().strip('"')
        if status.strip() in {"A", "AM", "M", "??", "R", "RM"} and raw_path.endswith(".py"):
            files.append(Path(raw_path))
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查迁移是否只包含 expand 变更")
    parser.add_argument("--all", action="store_true", help="扫描全部迁移文件")
    parser.add_argument("--path", action="append", default=[], help="指定迁移文件，可重复")
    parser.add_argument("--allow-contract", action="store_true", help="已知本次要执行 contract")
    args = parser.parse_args(argv)

    if args.all:
        targets = sorted(VERSIONS_DIR.glob("*.py"))
    elif args.path:
        targets = [Path(item) for item in args.path]
    else:
        targets = changed_migration_files()

    if not targets:
        print("未发现新增/修改的迁移文件，按 expand 通过")
        return 0

    missing = [str(path) for path in targets if not path.exists()]
    if missing:
        print(f"迁移文件不存在: {', '.join(missing)}", file=sys.stderr)
        return 2

    findings: list[str] = []
    for path in targets:
        findings.extend(scan_file(path))

    if not findings:
        print(f"迁移检查通过（{len(targets)} 个文件）")
        return 0

    print("检测到破坏性变更（属于 contract 阶段，不能在蓝绿发布中执行）：", file=sys.stderr)
    for finding in findings:
        print(f"  {finding}", file=sys.stderr)

    if args.allow_contract:
        print("已指定 --allow-contract，继续（本次发布后不可回滚）")
        return 0

    print("如果本次确实要执行 contract，请显式加 --allow-contract", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
