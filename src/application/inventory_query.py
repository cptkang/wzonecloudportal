"""인벤토리 조회 (계획 07 §3의 Step 1 축소판).

- **하이퍼바이저를 직접 호출하지 않는다** (D-007). 모든 조회는 저장소 경유다.
- **하이퍼바이저 분기(`if kind == VCENTER`)를 이 계층에 두지 않는다.**
- **모든 조회 메서드는 `AccessScope`를 첫 필수 인자로 받는다** (계획 09 §3.1).
"""

from __future__ import annotations

from uuid import UUID

from src.domain.auth import AccessScope, Permission
from src.domain.query import Page, PagedResult, VmSummary
from src.domain.repositories import InventoryReadRepository


class InventoryQueryService:
    def __init__(self, repo: InventoryReadRepository) -> None:
        self._repo = repo

    async def list_virtual_machines(
        self, scope: AccessScope, connection_id: UUID | None, page: Page
    ) -> PagedResult[VmSummary]:
        # API 라우터에서도 검사하지만 여기서 재검사한다 (이중 방어, 계획 09 §6.1).
        # 워커·스크립트 경로가 API를 거치지 않기 때문이다.
        scope.require(Permission.RESOURCE_READ)
        page.validate()

        # 범위 밖 connection_id는 403이 아니라 빈 결과를 준다 —
        # 403은 "그 연결이 존재한다"는 사실을 알려준다 (ROADMAP §9).
        if connection_id is not None and not scope.can_access(connection_id):
            return PagedResult(items=(), total=0, page=page)

        return await self._repo.search_vms(scope, connection_id, page)
