"""认证接口：登录、刷新、登出、当前用户。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.deps import CurrentPrincipal, client_ip
from app.infra.db.session import get_db
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    MeResponse,
    RefreshRequest,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["认证"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post("/login", response_model=LoginResponse, summary="用户名密码登录")
def login(payload: LoginRequest, request: Request, db: DbSession) -> LoginResponse:
    return AuthService(db).login(
        payload.username,
        payload.password,
        user_agent=request.headers.get("user-agent"),
        ip=client_ip(request),
    )


@router.post("/refresh", response_model=LoginResponse, summary="刷新令牌换取新令牌")
def refresh(payload: RefreshRequest, request: Request, db: DbSession) -> LoginResponse:
    return AuthService(db).refresh(
        payload.refresh_token,
        user_agent=request.headers.get("user-agent"),
        ip=client_ip(request),
    )


@router.post("/logout", summary="登出并使刷新令牌失效")
def logout(
    request: Request,
    principal: CurrentPrincipal,
    db: DbSession,
    payload: LogoutRequest | None = None,
) -> dict[str, int]:
    revoked = AuthService(db).logout(
        principal.user, payload.refresh_token if payload else None
    )
    return {"revoked": revoked}


@router.get("/me", response_model=MeResponse, summary="当前用户与角色")
def me(principal: CurrentPrincipal) -> MeResponse:
    user = principal.user
    return MeResponse(
        id=str(user.id),
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        status=user.status,
        workspace_id=str(user.workspace_id),
        roles=principal.roles,
        last_login_at=user.last_login_at,
    )
