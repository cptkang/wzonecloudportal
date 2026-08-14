"""감사 이벤트 (계획 10 §6, FR-1004).

**이력은 소급되지 않는다.** 조회 화면은 Step 8이지만 기록은 Step 1부터 남긴다
(ROADMAP §4.1).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal


class AuditAction(StrEnum):
    LOGIN = "login"
    LOGOUT = "logout"
    PASSWORD_CHANGE = "password.change"
    CONNECTION_CREATE = "connection.create"
    CONNECTION_DELETE = "connection.delete"
    CONNECTION_TEST = "connection.test"
    COLLECTION_TRIGGER = "collection.trigger"
    USER_REGISTER = "user.register"
    #: 중복 아이디로 가입 신청됨. 응답은 정상과 동일하지만 기록은 남긴다 (계획 09 §4.5).
    USER_REGISTER_DUPLICATE = "user.register_duplicate"
    USER_APPROVE = "user.approve"
    USER_REJECT = "user.reject"
    USER_DISABLE = "user.disable"
    USER_ENABLE = "user.enable"
    USER_ROLE_CHANGE = "user.role_change"
    USER_PASSWORD_RESET = "user.password_reset"
    SCOPE_UPDATE = "scope.update"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    actor: str | None
    action: AuditAction
    result: Literal["success", "failure"]
    actor_ip: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    occurred_at: datetime | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)


#: 액션별로 담을 수 있는 키 (계획 10 §6.2).
#: 요청 본문을 통째로 넣으면 자격증명이 섞이므로 화이트리스트로 관리한다.
ALLOWED_DETAIL_KEYS: dict[AuditAction, frozenset[str]] = {
    AuditAction.LOGIN: frozenset({"username", "reason"}),
    AuditAction.LOGOUT: frozenset({"username"}),
    AuditAction.PASSWORD_CHANGE: frozenset({"username"}),
    AuditAction.CONNECTION_CREATE: frozenset({"display_name", "kind", "address", "port"}),
    AuditAction.CONNECTION_DELETE: frozenset({"display_name", "impact"}),
    AuditAction.CONNECTION_TEST: frozenset({"is_usable", "failed_stage"}),
    AuditAction.COLLECTION_TRIGGER: frozenset({"display_name"}),
    AuditAction.USER_REGISTER: frozenset({"username"}),
    AuditAction.USER_REGISTER_DUPLICATE: frozenset({"username"}),
    AuditAction.USER_APPROVE: frozenset({"username", "role", "connection_count"}),
    AuditAction.USER_REJECT: frozenset({"username", "reason"}),
    AuditAction.USER_DISABLE: frozenset({"username"}),
    AuditAction.USER_ENABLE: frozenset({"username"}),
    AuditAction.USER_ROLE_CHANGE: frozenset({"username", "role"}),
    #: 값이 아니라 "발급됨" 사실만 남긴다. 평문·길이 모두 기록하지 않는다 (계획 09 §4.6.2).
    AuditAction.USER_PASSWORD_RESET: frozenset({"username"}),
    AuditAction.SCOPE_UPDATE: frozenset({"username", "connection_count"}),
}
