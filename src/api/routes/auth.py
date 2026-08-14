"""인증 라우터 (계획 08 §4.1)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from src.api.deps import (
    ActiveUser,
    AppSettings,
    CurrentUser,
    DbSession,
    client_ip,
    get_auth_service,
    get_connection_repo,
    get_registration_service,
    get_scope_repo,
    get_user_repo,
)
from src.api.schemas.auth import (
    ChangePasswordRequest,
    ConnectionRefResponse,
    LoginRequest,
    LoginResponse,
    MeResponse,
    RegisterRequest,
    to_user_response,
)
from src.application.auth_service import AuthService
from src.application.user_service import REGISTER_ACCEPTED_MESSAGE, RegistrationService
from src.config import Settings
from src.domain.auth import ROLE_PERMISSIONS, Role
from src.infrastructure.repository.connection_repo import ConnectionRepository
from src.infrastructure.repository.user_repo import ScopeRepository, UserRepository
from src.infrastructure.security.tokens import COOKIE_NAME

router = APIRouter(prefix="/auth", tags=["auth"])


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,  # JS에서 접근 불가 — XSS 시 토큰 유출 차단
        samesite="strict",  # CSRF 기본 방어
        secure=settings.cookie_secure,  # 운영 True. 개발 HTTP에서는 False
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )


@router.post("/register", status_code=202)
async def register(
    payload: RegisterRequest,
    request: Request,
    session: DbSession,
    svc: Annotated[RegistrationService, Depends(get_registration_service)],
) -> dict[str, str]:
    """가입 신청.

    **중복 여부를 노출하지 않기 위해 항상 202를 반환한다.** 다르면 가입 폼이 계정 열거
    수단이 된다 (계획 09 §4.5).
    """
    await svc.register(
        payload.username,
        payload.password,
        payload.display_name,
        payload.email,
        client_ip(request),
    )
    await session.commit()
    return {"message": REGISTER_ACCEPTED_MESSAGE}


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    svc: Annotated[AuthService, Depends(get_auth_service)],
    users: Annotated[UserRepository, Depends(get_user_repo)],
    scopes: Annotated[ScopeRepository, Depends(get_scope_repo)],
) -> LoginResponse:
    token, user = await svc.login(payload.username, payload.password, client_ip(request))
    await session.commit()
    set_session_cookie(response, token, settings)

    row = await users.get_row(user.user_id)
    scope_ids = None if user.role is Role.ADMIN else await scopes.list_for_user(user.user_id)
    return LoginResponse(
        user=to_user_response(
            user,
            email=row.email if row else None,
            scopes=scope_ids,
            last_login_at=row.last_login_at if row else None,
            created_at=row.created_at if row else None,
        )
    )


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    user: CurrentUser,
    session: DbSession,
    svc: Annotated[AuthService, Depends(get_auth_service)],
) -> None:
    await svc.record_logout(user, client_ip(request))
    await session.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", response_model=MeResponse)
async def me(
    user: CurrentUser,
    users: Annotated[UserRepository, Depends(get_user_repo)],
    scopes: Annotated[ScopeRepository, Depends(get_scope_repo)],
    connections: Annotated[ConnectionRepository, Depends(get_connection_repo)],
) -> MeResponse:
    """사용자 + `permissions[]` + 접근 가능한 연결 목록.

    `permissions[]`는 UI 메뉴 노출 판단용이다 (FR-1213). 실제 차단은 API가 한다.
    """
    row = await users.get_row(user.user_id)
    is_admin = user.role is Role.ADMIN
    scope_ids = None if is_admin else await scopes.list_for_user(user.user_id)

    # 자격증명·상태는 넣지 않는다 — 식별에 필요한 최소 정보만 (계획 09 §5).
    names = await connections.list_names(None if is_admin else scope_ids)

    return MeResponse(
        user=to_user_response(
            user,
            email=row.email if row else None,
            scopes=scope_ids,
            last_login_at=row.last_login_at if row else None,
            created_at=row.created_at if row else None,
        ),
        permissions=sorted(ROLE_PERMISSIONS[user.role]),
        accessible_connections=[
            ConnectionRefResponse(connection_id=cid, display_name=name) for cid, name in names
        ],
    )


@router.post("/change-password", status_code=204)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: ActiveUser,
    session: DbSession,
    svc: Annotated[AuthService, Depends(get_auth_service)],
) -> None:
    await svc.change_password(
        user, payload.current_password, payload.new_password, client_ip(request)
    )
    await session.commit()
