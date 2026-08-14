"""가상 머신 조회 라우터 (계획 08 §6.1).

**모든 조회에 범위 필터가 적용된다.** 범위 밖 `connection_id`를 받으면 403이 아니라
빈 결과를 준다 — 403은 "그 연결이 존재한다"는 사실을 알려준다 (ROADMAP §9).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from src.api.deps import Scope, get_inventory_service
from src.api.schemas.common import PagedResponse
from src.api.schemas.inventory import VmSummaryResponse
from src.application.inventory_query import InventoryQueryService
from src.domain.query import Page

router = APIRouter(prefix="/virtual-machines", tags=["inventory"])


@router.get("", response_model=PagedResponse[VmSummaryResponse])
async def list_virtual_machines(
    scope: Scope,
    svc: Annotated[InventoryQueryService, Depends(get_inventory_service)],
    connection_id: UUID | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=Page.MAX_LIMIT),
    sort_by: str = Query(default="name"),
    sort_desc: bool = Query(default=False),
) -> PagedResponse[VmSummaryResponse]:
    # sort_by는 Page.validate()가 화이트리스트로 검증한다. 문자열을 그대로 SQL에 넣는
    # 구현을 한 번 만들면 인젝션 경로가 남는다 (ROADMAP §9.2).
    page = Page(offset=offset, limit=limit, sort_by=sort_by, sort_desc=sort_desc)
    result = await svc.list_virtual_machines(scope, connection_id, page)
    return PagedResponse[VmSummaryResponse](
        items=[VmSummaryResponse.from_summary(vm) for vm in result.items],
        total=result.total,
        offset=page.offset,
        limit=page.limit,
        total_is_estimate=result.total_is_estimate,
    )
