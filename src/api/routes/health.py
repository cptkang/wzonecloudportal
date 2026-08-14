"""헬스체크 (계획 08 §8.1)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from src.api.deps import DbSession

router = APIRouter(tags=["health"])


class CheckResult(BaseModel):
    ok: bool
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str
    checks: dict[str, CheckResult]


@router.get("/health", response_model=HealthResponse)
async def health(session: DbSession) -> HealthResponse:
    try:
        await session.execute(text("SELECT 1"))
        db = CheckResult(ok=True)
    except Exception as exc:  # noqa: BLE001 - 헬스체크는 실패도 결과로 보고한다
        db = CheckResult(ok=False, detail=type(exc).__name__)

    # Redis는 Step 3에서 도입한다. 없이도 조회가 동작해야 한다 (CLAUDE.md Tech Stack).
    checks = {"database": db}
    return HealthResponse(
        status="healthy" if all(c.ok for c in checks.values()) else "degraded", checks=checks
    )
