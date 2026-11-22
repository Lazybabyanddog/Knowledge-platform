"""Alembic 运行环境：数据库地址与元数据都从应用配置读取。"""

from __future__ import annotations

import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.infra.db import models  # noqa: F401  导入以注册全部 ORM 模型
from app.infra.db.base import Base

# audit_log 的月份分区是运行期 DDL，不写进迁移文件，也不参与 autogenerate 比对
_AUDIT_PARTITION = re.compile(r"^audit_log_\d{6}$")


def include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001, ANN201
    if type_ == "table" and _AUDIT_PARTITION.match(name):
        return False
    table = getattr(obj, "table", None)
    if type_ == "index" and table is not None and _AUDIT_PARTITION.match(table.name):
        return False
    return True

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 密码里可能出现 %，ConfigParser 会当成插值符号，必须转义
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
