"""认证服务：登录、刷新、登出。

刷新令牌一次性使用：每次刷新都会吊销旧记录，重复使用同一令牌会被判定为
重放并拒绝，从而在令牌泄漏时留下可观测的失败信号。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import (
    ForbiddenError,
    TokenExpiredError,
    TooManyRequestsError,
    UnauthorizedError,
)
from app.core.security import (
    create_access_token,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.domain.enums import AuditAction, UserStatus
from app.infra.db.models import AppUser
from app.infra.db.repositories import RefreshTokenRepository, UserRepository
from app.schemas.auth import LoginResponse, TokenUser
from app.services.audit_service import AuditService
from app.services.login_guard import LoginGuard


class AuthService:
    def __init__(self, db: Session, guard: LoginGuard | None = None) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.tokens = RefreshTokenRepository(db)
        self.guard = guard or LoginGuard()

    def login(
        self,
        username: str,
        password: str,
        *,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> LoginResponse:
        # 先判定锁定，再校验密码：锁定期间不消耗校验成本，也不泄露账号是否存在
        try:
            self.guard.ensure_not_locked(username)
        except TooManyRequestsError:
            AuditService.record_isolated(
                AuditAction.LOGIN_FAILURE,
                actor_name=username,
                object_type="user",
                after={"reason": "locked"},
                ip=ip,
            )
            raise

        user = self.users.get_by_username(username)
        # 用户不存在与密码错误返回同一提示，避免账号枚举
        if user is None or not verify_password(password, user.password_hash):
            self.guard.record_failure(username)
            AuditService.record_isolated(
                AuditAction.LOGIN_FAILURE,
                actor_id=user.id if user else None,
                actor_name=username,
                workspace_id=user.workspace_id if user else None,
                object_type="user",
                object_id=user.id if user else None,
                after={"reason": "bad_credentials"},
                ip=ip,
            )
            raise UnauthorizedError("用户名或密码错误")
        if user.status != UserStatus.ACTIVE.value:
            AuditService.record_isolated(
                AuditAction.LOGIN_FAILURE,
                actor=user,
                object_type="user",
                object_id=user.id,
                after={"reason": "disabled"},
                ip=ip,
            )
            raise ForbiddenError("账号已被禁用")

        self.guard.reset(username)
        user.last_login_at = datetime.now(UTC)
        # 与登录状态一起提交：审计写入成功即视为登录成功留痕
        AuditService(self.db).record(
            AuditAction.LOGIN_SUCCESS,
            actor=user,
            object_type="user",
            object_id=user.id,
            ip=ip,
        )
        return self._issue(user, user_agent=user_agent, ip=ip)

    def refresh(
        self,
        raw_refresh_token: str,
        *,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> LoginResponse:
        record = self.tokens.get_by_hash(hash_refresh_token(raw_refresh_token))
        if record is None:
            raise UnauthorizedError("刷新令牌无效")
        if record.revoked_at is not None:
            raise UnauthorizedError("刷新令牌已失效，请重新登录")
        if record.expires_at <= datetime.now(UTC):
            raise TokenExpiredError("刷新令牌已过期")

        user = self.users.get(record.user_id)
        if user is None:
            raise UnauthorizedError("用户不存在")
        if user.status != UserStatus.ACTIVE.value:
            raise ForbiddenError("账号已被禁用")

        self.tokens.revoke(record)
        return self._issue(user, user_agent=user_agent, ip=ip)

    def logout(self, user: AppUser, raw_refresh_token: str | None = None) -> int:
        """返回被吊销的令牌数。不指定令牌则吊销该用户全部会话。"""
        if raw_refresh_token:
            record = self.tokens.get_by_hash(hash_refresh_token(raw_refresh_token))
            if record is not None and record.user_id == user.id:
                self.tokens.revoke(record)
                self.db.commit()
                return 1

        revoked = self.tokens.revoke_all_for_user(user.id)
        self.db.commit()
        return revoked

    def _issue(
        self,
        user: AppUser,
        *,
        user_agent: str | None,
        ip: str | None,
    ) -> LoginResponse:
        roles = self.users.list_roles(user.id)
        access_token, _ = create_access_token(
            subject=str(user.id),
            username=user.username,
            roles=roles,
            workspace_id=str(user.workspace_id),
        )
        raw_refresh, digest = new_refresh_token()
        self.tokens.create(
            user_id=user.id,
            token_hash=digest,
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds),
            user_agent=user_agent,
            ip=ip,
        )
        self.db.commit()

        return LoginResponse(
            access_token=access_token,
            refresh_token=raw_refresh,
            expires_in=settings.access_token_ttl_seconds,
            user=TokenUser(id=str(user.id), display_name=user.display_name, roles=roles),
        )
