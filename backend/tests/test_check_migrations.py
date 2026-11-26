"""迁移门禁脚本的行为：expand 放行、contract 拦截、可显式放行。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_migrations.py"

_EXPAND = '''
def upgrade() -> None:
    op.create_table("t_new", sa.Column("id", sa.Integer))
    op.add_column("document", sa.Column("release_tag", sa.String(32), nullable=True))
'''

_DROP = '''
def upgrade() -> None:
    op.drop_column("document", "legacy_flag")
'''

_TIGHTEN = '''
def upgrade() -> None:
    op.alter_column("document", "title", existing_type=sa.String(255), nullable=False)
'''


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_migrations_under_test", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checker() -> ModuleType:
    return _load()


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_expand_migration_passes(checker: ModuleType, tmp_path: Path) -> None:
    path = _write(tmp_path, "x_expand_add_tag.py", _EXPAND)

    assert checker.scan_file(path) == []
    assert checker.main(["--path", str(path)]) == 0


def test_drop_column_is_rejected(checker: ModuleType, tmp_path: Path) -> None:
    path = _write(tmp_path, "x_contract_drop.py", _DROP)

    findings = checker.scan_file(path)

    assert len(findings) == 1
    assert "drop_column" in findings[0]
    assert checker.main(["--path", str(path)]) == 1


def test_nullable_false_is_rejected(checker: ModuleType, tmp_path: Path) -> None:
    path = _write(tmp_path, "x_contract_tighten.py", _TIGHTEN)

    findings = checker.scan_file(path)

    assert any("nullable=False" in item for item in findings)
    assert checker.main(["--path", str(path)]) == 1


def test_allow_contract_flag_bypasses_gate(checker: ModuleType, tmp_path: Path) -> None:
    path = _write(tmp_path, "x_contract_drop.py", _DROP)

    # 放行不改变"有破坏性变更"的判定，只是让发布能继续（此后不可回滚）
    assert checker.main(["--path", str(path), "--allow-contract"]) == 0


def test_missing_file_reports_usage_error(checker: ModuleType, tmp_path: Path) -> None:
    assert checker.main(["--path", str(tmp_path / "nope.py")]) == 2
