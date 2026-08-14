"""인증·계정 스키마 (계획 08 §4.1).

**응답에 `password_hash`·`locked_until`을 넣지 않는다.** `UserResponse`에 필드를
정의하지 않는 것으로 강제한다.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr

from src.domain.auth import AuthenticatedUser, Permission, Role, UserStatus


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: SecretStr = Field(min_length=10)
    display_name: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1)


class ChangePasswordRequest(BaseModel):
    current_password: SecretStr = Field(min_length=1)
    new_password: SecretStr = Field(min_length=10)


class UserResponse(BaseModel):
    user_id: UUID
    username: str
    display_name: str | None
    email: str | None
    role: Role
    status: UserStatus
    must_change_password: bool
    #: None = 무제한 (admin). 빈 목록 = 아무것도 보이지 않음 (기본 거부)
    scopes: list[UUID] | None
    last_login_at: datetime | None
    created_at: datetime | None


class ConnectionRefResponse(BaseModel):
    """조회 화면의 연결 필터용 최소 정보. 자격증명·상태는 담지 않는다."""

    connection_id: UUID
    display_name: str


class MeResponse(BaseModel):
    user: UserResponse
    #: UI 메뉴 노출 판단에 쓴다 (FR-1213). 실제 차단은 API가 한다.
    permissions: list[Permission]
    #: `/connections`는 admin 전용이라 조회 사용자의 필터 목록을 여기서 내려준다
    accessible_connections: list[ConnectionRefResponse]


class LoginResponse(BaseModel):
    """**토큰을 본문에 넣지 않는다.** `httpOnly` 쿠키로만 전달한다 (D-014)."""

    user: UserResponse


class ApproveRequest(BaseModel):
    role: Role = Role.VIEWER
    connection_ids: list[UUID] = []


class RejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class RoleUpdateRequest(BaseModel):
    role: Role


class ScopeUpdateRequest(BaseModel):
    connection_ids: list[UUID] = []


class TemporaryPasswordResponse(BaseModel):
    """임시 비밀번호는 응답에 1회만 실린다. 저장·로깅하지 않는다 (FR-1008)."""

    temporary_password: str


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int


def to_user_response(
    user: AuthenticatedUser,
    *,
    email: str | None,
    scopes: list[UUID] | None,
    last_login_at: datetime | None,
    created_at: datetime | None,
) -> UserResponse:
    return UserResponse(
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        email=email,
        role=user.role,
        status=user.status,
        must_change_password=user.must_change_password,
        scopes=scopes,
        last_login_at=last_login_at,
        created_at=created_at,
    )
