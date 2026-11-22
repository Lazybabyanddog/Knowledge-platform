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
from app.parsing.registry import has_engine
from app.schemas.parse import (
    ParseStrategyCreateRequest,
    ParseStrategyItem,
    ParseStrategyUpdateRequest,
)
from app.services.audit_service import AuditService


def _item(strategy: ParseStrategy) -> ParseStrategyItem:
    return ParseStrategyItem(
        id=str(strategy.id),
        strategy_key=strategy.strategy_key,
        version=strategy.version,
        name=strategy.name,
        doc_type=strategy.doc_type,
        engine=strategy.engine,
        config=dict(strategy.config or {}),
        is_default=strategy.is_default,
        status=strategy.status,
        created_at=strategy.created_at,
    )


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

    def create_strategy(
        self,
        principal: Principal,
        payload: ParseStrategyCreateRequest,
        *,
        ip: str | None = None,
    ) -> ParseStrategyItem:
        if not has_engine(payload.engine):
            raise BadRequestError(f"未注册的解析引擎: {payload.engine}")
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

        if payload.engine is not None and not has_engine(payload.engine):
            raise BadRequestError(f"未注册的解析引擎: {payload.engine}")

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
