"""解析策略服务：修改即生成新版本，历史任务始终指向执行时的旧版本行。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.exceptions import BadRequestError, NotFoundError
from app.domain.enums import AuditAction, StrategyStatus
from app.infra.db.models import ParseStrategy
from app.infra.db.repositories import ParseStrategyRepository
from app.parsing.registry import engine_names, get_engine, has_engine
from app.schemas.parse import (
    ParseEngineItem,
    ParseStrategyCreateRequest,
    ParseStrategyItem,
    ParseStrategyUpdateRequest,
)
from app.services.audit_service import AuditService
from app.services.chunk_config import chunk_config_view
from app.services.glossary.config import alias_config_view


def _item(strategy: ParseStrategy) -> ParseStrategyItem:
    config = dict(strategy.config or {})
    return ParseStrategyItem(
        id=str(strategy.id),
        strategy_key=strategy.strategy_key,
        version=strategy.version,
        name=strategy.name,
        doc_type=strategy.doc_type,
        engine=strategy.engine,
        config=config,
        chunk=chunk_config_view(config),
        alias=alias_config_view(config),
        is_default=strategy.is_default,
        status=strategy.status,
        created_at=strategy.created_at,
    )


def _ensure_engine_usable(engine_name: str, doc_type: str) -> None:
    """保存策略前就确认引擎可用，别把注定失败的任务留给 worker。

    三层校验：注册表存在 -> 支持该文档类型 -> 引擎自身配置完整
    （例如 MinerU 服务地址未配置时不该允许保存）。
    """
    if not has_engine(engine_name):
        raise BadRequestError(f"未注册的解析引擎: {engine_name}")
    engine = get_engine(engine_name)
    if not engine.supports(doc_type):
        raise BadRequestError(f"解析引擎 {engine_name} 不支持文档类型 {doc_type}")
    problem = engine.config_problem()
    if problem:
        raise BadRequestError(problem)


def _snapshot(strategy: ParseStrategy) -> dict[str, Any]:
    return {
        "strategy_key": strategy.strategy_key,
        "version": strategy.version,
        "name": strategy.name,
        "doc_type": strategy.doc_type,
        "engine": strategy.engine,
        "config": dict(strategy.config or {}),
        "is_default": strategy.is_default,
    }


class ParseStrategyService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.strategies = ParseStrategyRepository(db)
        self.audit = AuditService(db)

    def list_strategies(
        self, principal: Principal, doc_type: str | None = None
    ) -> list[ParseStrategyItem]:
        rows = self.strategies.list_latest(principal.user.workspace_id, doc_type)
        return [_item(row) for row in rows]

    def get_strategy(self, principal: Principal, strategy_id: uuid.UUID) -> ParseStrategyItem:
        return _item(self._get_or_404(principal, strategy_id))

    def list_engines(self) -> list[ParseEngineItem]:
        """引擎候选来自注册表，前端不再自己维护一份容易漂移的清单。"""
        items = []
        for name in engine_names():
            engine = get_engine(name)
            items.append(
                ParseEngineItem(
                    name=engine.name,
                    display_name=engine.display_name or engine.name,
                    doc_types=list(engine.doc_types),
                )
            )
        return items

    def create_strategy(
        self,
        principal: Principal,
        payload: ParseStrategyCreateRequest,
        *,
        ip: str | None = None,
    ) -> ParseStrategyItem:
        _ensure_engine_usable(payload.engine, payload.doc_type)
        if payload.is_default:
            # 默认策略按 doc_type 唯一，先让位再插入
            self.strategies.clear_default(principal.user.workspace_id, payload.doc_type)

        strategy = ParseStrategy(
            workspace_id=principal.user.workspace_id,
            strategy_key=uuid.uuid4().hex,
            version=1,
            name=payload.name,
            doc_type=payload.doc_type,
            engine=payload.engine,
            config=payload.config,
            is_default=payload.is_default,
            status=StrategyStatus.ACTIVE.value,
            created_by=principal.user_id,
        )
        self.strategies.add(strategy)
        self.db.flush()
        self.audit.record(
            AuditAction.PARSE_STRATEGY_CREATE,
            actor=principal.user,
            workspace_id=principal.user.workspace_id,
            object_type="parse_strategy",
            object_id=strategy.id,
            after=_snapshot(strategy),
            ip=ip,
        )
        self.db.commit()
        return _item(strategy)

    def update_strategy(
        self,
        principal: Principal,
        strategy_id: uuid.UUID,
        payload: ParseStrategyUpdateRequest,
        *,
        ip: str | None = None,
    ) -> ParseStrategyItem:
        current = self._get_or_404(principal, strategy_id)
        workspace_id = principal.user.workspace_id
        base = self.strategies.latest_of_key(workspace_id, current.strategy_key)
        if base is None:  # pragma: no cover - 数据完整性兜底
            raise NotFoundError("策略版本不存在")

        # 文档类型不可变，引擎可能被本次修改替换，按修改后的组合校验
        _ensure_engine_usable(
            payload.engine if payload.engine is not None else base.engine, base.doc_type
        )

        inherits_default = base.is_default
        new_version = ParseStrategy(
            workspace_id=workspace_id,
            strategy_key=base.strategy_key,
            version=base.version + 1,
            name=payload.name if payload.name is not None else base.name,
            doc_type=base.doc_type,
            engine=payload.engine if payload.engine is not None else base.engine,
            config=payload.config if payload.config is not None else dict(base.config or {}),
            is_default=False,
            status=StrategyStatus.ACTIVE.value,
            created_by=principal.user_id,
        )

        # 旧版本只让出"默认标记"，配置内容一律不动
        self.strategies.clear_key_default(workspace_id, base.strategy_key)
        keep_default = payload.is_default is True or (
            payload.is_default is None and inherits_default
        )
        if keep_default:
            self.strategies.clear_default(
                workspace_id, base.doc_type, exclude_key=base.strategy_key
            )
            new_version.is_default = True

        self.strategies.add(new_version)
        self.db.flush()
        self.audit.record(
            AuditAction.PARSE_STRATEGY_UPDATE,
            actor=principal.user,
            workspace_id=workspace_id,
            object_type="parse_strategy",
            object_id=new_version.id,
            before=_snapshot(base),
            after=_snapshot(new_version),
            ip=ip,
        )
        self.db.commit()
        return _item(new_version)

    def delete_strategy(
        self,
        principal: Principal,
        strategy_id: uuid.UUID,
        *,
        ip: str | None = None,
    ) -> int:
        """停用整组策略版本。

        ``parse_task.strategy_id`` 是 RESTRICT，历史任务必须能追溯执行时的策略，
        因此这里只做逻辑删除：全部版本置为 disabled 并让出默认标记，列表随之不可见。
        返回被停用的版本数。
        """
        current = self._get_or_404(principal, strategy_id)
        workspace_id = principal.user.workspace_id
        versions = self.strategies.list_by_key(workspace_id, current.strategy_key)
        if not versions:  # pragma: no cover - 数据完整性兜底
            raise NotFoundError("策略版本不存在")

        for row in versions:
            row.status = StrategyStatus.DISABLED.value
            row.is_default = False

        self.audit.record(
            AuditAction.PARSE_STRATEGY_DELETE,
            actor=principal.user,
            workspace_id=workspace_id,
            object_type="parse_strategy",
            object_id=current.id,
            before=_snapshot(current),
            after={
                "strategy_key": current.strategy_key,
                "disabled_versions": [row.version for row in versions],
            },
            ip=ip,
        )
        self.db.commit()
        return len(versions)

    def resolve_for_document(
        self, workspace_id: uuid.UUID, doc_type: str, strategy_id: str | None
    ) -> ParseStrategy:
        """触发解析时确定用哪个策略版本：显式指定优先，否则取该类型的默认策略。"""
        if strategy_id:
            try:
                target = uuid.UUID(strategy_id)
            except ValueError as exc:
                raise BadRequestError("strategy_id 不是合法的 UUID") from exc
            strategy = self.strategies.get(target)
            if strategy is None or strategy.workspace_id != workspace_id:
                raise NotFoundError("解析策略不存在")
            return strategy

        default = self.strategies.find_default(workspace_id, doc_type)
        if default is None:
            raise BadRequestError(f"文档类型 {doc_type} 未配置默认解析策略")
        return default

    def _get_or_404(self, principal: Principal, strategy_id: uuid.UUID) -> ParseStrategy:
        strategy = self.strategies.get(strategy_id)
        if strategy is None or strategy.workspace_id != principal.user.workspace_id:
            raise NotFoundError("解析策略不存在")
        return strategy
