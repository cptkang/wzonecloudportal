"""사용자 관리 라우터 (계획 08 §4.1) — 모두 admin 전용.

**`DELETE /users/{id}`를 만들지 않는다.** 계정은 비활성화만 한다 (D-014).
감사 로그의 행위자 참조가 끊기면 "누가 이 연결을 등록했는가"를 추적할 수 없다.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from src.api.deps import (
    DbSession,
    Scope,
    get_scope_repo,
    get_user_admin_service,
    get_user_repo,
    require,
)
from src.api.schemas.auth import (
    ApproveRequest,
    RejectRequest,
    RoleUpdateRequest,
    ScopeUpdateRequest,
    TemporaryPasswordResponse,
    UserListResponse,
    UserResponse,
)
from src.application.user_service import UserAdminService
from src.domain.auth import Permission, Role, UserStatus
from src.infrastructure.repository.user_repo import ScopeRepository, UserRepository

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(require(Permission.USER_MANAGE))],
)


@router.get("", response_model=UserListResponse)
async def list_users(
    users: Annotated[UserRepository, Depends(get_user_repo)],
    scopes: Annotated[ScopeRepository, Depends(get_scope_repo)],
    status: UserStatus | None = Query(default=None),
) -> UserListResponse:
    rows = await users.list_rows(status)
    scope_map = await scopes.counts_by_user([r.user_id for r in rows])
    items = [
        UserResponse(
            user_id=r.user_id,
            username=r.username,
            display_name=r.display_name,
            email=r.email,
            role=Role(r.role),
            status=UserStatus(r.status),
            must_change_password=r.must_change_password,
            # admin은 범위 테이블과 무관하게 전체를 본다
            scopes=None if Role(r.role) is Role.ADMIN else scope_map.get(r.user_id, []),
            last_login_at=r.last_login_at,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return UserListResponse(items=items, total=len(items))


@router.post("/{user_id}/approve", status_code=204)
async def approve_user(
    user_id: UUID,
    payload: ApproveRequest,
    scope: Scope,
    session: DbSession,
    svc: Annotated[UserAdminService, Depends(get_user_admin_service)],
) -> None:
    """승인하면서 **역할과 조회 범위를 함께 부여한다.**

    범위를 비워 두면 아무것도 보이지 않는다 (기본 거부). 이는 정상 동작이다.
    """
    await svc.approve(scope, user_id, payload.role, payload.connection_ids)
    await session.commit()


@router.post("/{user_id}/reject", status_code=204)
async def reject_user(
    user_id: UUID,
    payload: RejectRequest,
    scope: Scope,
    session: DbSession,
    svc: Annotated[UserAdminService, Depends(get_user_admin_service)],
) -> None:
    await svc.reject(scope, user_id, payload.reason)
    await session.commit()


@router.post("/{user_id}/disable", status_code=204)
async def disable_user(
    user_id: UUID,
    scope: Scope,
    session: DbSession,
    svc: Annotated[UserAdminService, Depends(get_user_admin_service)],
) -> None:
    await svc.disable(scope, user_id)
    await session.commit()


@router.post("/{user_id}/enable", status_code=204)
async def enable_user(
    user_id: UUID,
    scope: Scope,
    session: DbSession,
    svc: Annotated[UserAdminService, Depends(get_user_admin_service)],
) -> None:
    await svc.enable(scope, user_id)
    await session.commit()


@router.patch("/{user_id}", status_code=204)
async def update_user(
    user_id: UUID,
    payload: RoleUpdateRequest,
    scope: Scope,
    session: DbSession,
    svc: Annotated[UserAdminService, Depends(get_user_admin_service)],
) -> None:
    await svc.change_role(scope, user_id, payload.role)
    await session.commit()


@router.put("/{user_id}/scopes", status_code=204)
async def replace_scopes(
    user_id: UUID,
    payload: ScopeUpdateRequest,
    scope: Scope,
    session: DbSession,
    svc: Annotated[UserAdminService, Depends(get_user_admin_service)],
) -> None:
    """전체 교체(멱등)."""
    await svc.set_scopes(scope, user_id, payload.connection_ids)
    await session.commit()


@router.post("/{user_id}/reset-password", response_model=TemporaryPasswordResponse)
async def reset_password(
    user_id: UUID,
    scope: Scope,
    session: DbSession,
    svc: Annotated[UserAdminService, Depends(get_user_admin_service)],
) -> TemporaryPasswordResponse:
    """임시 비밀번호를 **1회만** 반환한다. 저장·로깅하지 않는다 (FR-1008)."""
    temporary = await svc.reset_password(scope, user_id)
    await session.commit()
    return TemporaryPasswordResponse(temporary_password=temporary.get_secret_value())
