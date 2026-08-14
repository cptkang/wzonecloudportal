"""공통 응답 스키마 (계획 08 §3.1).

**응답 계약은 Step 1부터 최종 형태로 만든다** (ROADMAP §9.2). 편의대로 만들면
Step 4에서 API와 UI를 동시에 고치게 되고, 그때는 화면이 더 늘어나 있다.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PagedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    #: page/size가 아니다 (계획 07 §2의 Page(offset, limit, sort_by, sort_desc))
    offset: int
    limit: int
    #: 대량에서 근사치로 전환할 때 True (Step 4)
    total_is_estimate: bool = False


class ErrorResponse(BaseModel):
    #: 기계 판독용 코드
    error: str
    #: 사용자 표시용 (한국어)
    message: str
    detail: dict[str, Any] = {}
