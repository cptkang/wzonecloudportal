"""감사 로그 기록 (계획 10 §6)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.audit import ALLOWED_DETAIL_KEYS, AuditAction, AuditEvent
from src.infrastructure.db.models import AuditEventRow
from src.infrastructure.security.masking import mask_text

logger = logging.getLogger(__name__)


def build_detail(action: AuditAction, raw: dict[str, Any]) -> dict[str, Any]:
    """액션별 화이트리스트만 남긴다.

    요청 본문을 통째로 넣으면 자격증명이 섞인다. `CREDENTIAL_*` 계열은 값·길이 모두
    기록하지 않는다 (계획 10 §6.2).
    """
    allowed = ALLOWED_DETAIL_KEYS.get(action, frozenset())
    return {k: _scrub(v) for k, v in raw.items() if k in allowed}


def _scrub(value: Any) -> Any:
    return mask_text(value) if isinstance(value, str) else value


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, event: AuditEvent) -> None:
        """감사 이벤트를 기록한다. 실패해도 본 작업을 막지 않는다.

        이 프로젝트는 조회 중심이므로 가용성을 우선한다. 규제 요구가 생기면
        `strict_audit` 설정으로 전환한다 (계획 10 §6.3).
        """
        try:
            self._session.add(
                AuditEventRow(
                    occurred_at=event.occurred_at or datetime.now(UTC),
                    actor=event.actor,
                    action=event.action.value,
                    target_type=event.target_type,
                    target_id=event.target_id,
                    result=event.result,
                    client_ip=event.actor_ip,
                    detail=dict(event.detail) or None,
                )
            )
            await self._session.flush()
        except Exception:  # noqa: BLE001 - 감사 실패로 조회·수정이 막히면 안 된다
            logger.exception(
                "감사 로그 기록 실패",
                extra={"action": event.action.value, "actor": event.actor},
            )
