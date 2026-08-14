"""연결 테스트 단계 실행기 (계획 03 §4.1).

계획 03은 이 헬퍼를 `src/utils/`에 두라고 했으나, **utils는 domain을 import할 수 없다**
(D-002 계층 규칙, `ALLOWED_DEPS["utils"] = set()`). `StageResult`·`CheckStage`가 도메인
타입이고 `sanitize_error`가 infrastructure에 있으므로 infrastructure에 둔다 (D-016).

어댑터 공통 위치이지 특정 어댑터 소속이 아니므로 어댑터 교차 참조 규칙에도 걸리지 않는다.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from src.domain.enums import CheckStage
from src.domain.ports import StageResult
from src.infrastructure.security.masking import sanitize_error


class StageRunner:
    """단계를 순차 실행하고, 실패 시 이후 단계를 skipped로 기록한다."""

    def __init__(self, *, secrets: tuple[str, ...] = ()) -> None:
        self._results: list[StageResult] = []
        self._aborted = False
        self._secrets = secrets

    async def run(self, stage: CheckStage, fn: Callable[[], Awaitable[str | None]]) -> None:
        if self._aborted:
            self._results.append(StageResult(stage=stage, passed=False, skipped=True))
            return
        started = time.monotonic()
        try:
            detail = await fn()
        except Exception as exc:  # noqa: BLE001 - 모든 실패를 단계 결과로 환원한다
            self._results.append(
                StageResult(
                    stage=stage,
                    passed=False,
                    detail=sanitize_error(exc, secrets=self._secrets),
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
            )
            self._aborted = True
            return
        self._results.append(
            StageResult(
                stage=stage,
                passed=True,
                detail=detail,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        )

    @property
    def results(self) -> tuple[StageResult, ...]:
        return tuple(self._results)
