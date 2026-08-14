"""재시도 유틸 (계획 02 §4.1).

도메인이 아닌 utils에 둔다. 도메인 예외를 import하면 utils→domain 의존이 생기므로
**`retryable` 속성을 덕 타이핑으로 확인**한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> T:
    """지수 백오프 재시도. `retryable=False` 예외는 즉시 전파한다.

    기본값이 `False`인 것이 중요하다 — 알 수 없는 예외를 재시도하지 않는 쪽이 안전하다.
    인증 실패를 재시도하면 AD 계정이 잠긴다 (CST-05).
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 - retryable 판정을 위해 넓게 잡는다
            if not getattr(exc, "retryable", False):
                raise
            last_exc = exc
            if attempt >= max_attempts:
                break
            await asyncio.sleep(min(base_delay * (2 ** (attempt - 1)), max_delay))
    assert last_exc is not None
    raise last_exc
