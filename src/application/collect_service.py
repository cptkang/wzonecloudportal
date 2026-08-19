"""수집 실행 (계획 06 §7의 Step 1 축소판).

`src/orchestration/`은 Step 1에 없다. 수동 수집은 API 요청으로 시작하므로 이 서비스로
충분하다. 주기 실행 워커는 Step 3에서 만든다 (ROADMAP §6).

**어댑터를 직접 import하지 않고 `ReaderFactory`를 주입받는다** (계획 03 §7).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from src.config import Settings
from src.domain.connection import Connection
from src.domain.enums import ConnectionStatus
from src.domain.exceptions import (
    AuthenticationError,
    PortalError,
)
from src.domain.exceptions import (
    PermissionError as DomainPermissionError,
)
from src.domain.ports import ReaderFactory
from src.domain.resource import VirtualMachine
from src.infrastructure.repository.connection_repo import ConnectionRepository
from src.infrastructure.repository.vm_repo import VirtualMachineRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CollectSummary:
    connection_id: UUID
    created: int
    updated: int
    unchanged: int
    failed: bool
    error: str | None = None


class CollectService:
    def __init__(
        self,
        connections: ConnectionRepository,
        vms: VirtualMachineRepository,
        reader_factory: ReaderFactory,
        settings: Settings,
    ) -> None:
        self._connections = connections
        self._vms = vms
        self._reader_factory = reader_factory
        self._settings = settings

    async def collect(self, connection: Connection) -> CollectSummary:
        """단일 연결을 수집한다. 배치 단위로 upsert하며 세션은 반드시 해제한다."""
        observed_at = datetime.now(UTC)
        reader = self._reader_factory(connection)

        try:
            await reader.start_session()
        except AuthenticationError as exc:
            # **재시도하지 않는다.** 잘못된 비밀번호로 반복 시도하면 AD 계정이 잠기고
            # 그 계정을 쓰는 다른 시스템까지 연쇄 장애가 발생한다 (CST-05, FR-114).
            # Step 3에서 스케줄 해제(unschedule)가 여기에 추가된다.
            await self._connections.mark_failure(
                connection.connection_id, exc.message, ConnectionStatus.CREDENTIAL_ERROR
            )
            logger.error(
                "인증 실패 — 수집 중단",
                extra={"connection_id": str(connection.connection_id)},
            )
            return _failure(connection.connection_id, exc.message)
        except DomainPermissionError as exc:
            await self._connections.mark_failure(
                connection.connection_id, exc.message, ConnectionStatus.PERMISSION_ERROR
            )
            return _failure(connection.connection_id, exc.message)
        except PortalError as exc:
            await self._connections.mark_failure(
                connection.connection_id, exc.message, ConnectionStatus.UNREACHABLE
            )
            return _failure(connection.connection_id, exc.message)

        created = updated = unchanged = 0
        batch: list[VirtualMachine] = []
        # **어댑터 조회 시간과 DB 반영 시간을 분리해서 잰다.** 총 시간만 재면 Step 2 실측
        # (ROADMAP §15.3-13)에서 병목이 vCenter 왕복인지 DB인지 구분할 수 없고,
        # 무엇을 최적화할지 정할 근거가 없다 (D-020 후속, `tasks/plan.md` AD-1).
        adapter_ns = db_ns = 0
        batches = 0
        try:
            mark = time.perf_counter_ns()
            async for vm in reader.list_virtual_machines():
                adapter_ns += time.perf_counter_ns() - mark
                batch.append(vm)
                if len(batch) >= self._settings.collection_batch_size:
                    mark = time.perf_counter_ns()
                    c, u, n = await self._flush(connection.connection_id, batch, observed_at)
                    db_ns += time.perf_counter_ns() - mark
                    batches += 1
                    created, updated, unchanged = created + c, updated + u, unchanged + n
                    batch.clear()
                mark = time.perf_counter_ns()
            if batch:
                mark = time.perf_counter_ns()
                c, u, n = await self._flush(connection.connection_id, batch, observed_at)
                db_ns += time.perf_counter_ns() - mark
                batches += 1
                created, updated, unchanged = created + c, updated + u, unchanged + n
        except AuthenticationError as exc:
            await self._connections.mark_failure(
                connection.connection_id, exc.message, ConnectionStatus.CREDENTIAL_ERROR
            )
            return _failure(connection.connection_id, exc.message)
        finally:
            await reader.close_session()

        # **집계 수치만 남긴다.** 인벤토리 정보 자체가 민감 정보이므로 개별 VM 이름·ID를
        # 로그에 쓰지 않는다 (NFR-206).
        logger.info(
            "수집 구간별 소요",
            extra={
                "connection_id": str(connection.connection_id),
                "adapter_ms": adapter_ns // 1_000_000,
                "db_ms": db_ns // 1_000_000,
                "batch_count": batches,
                "vm_count": created + updated + unchanged,
            },
        )

        # 어댑터는 부분 실패를 예외가 아니라 outcome으로 보고한다 (FR-204).
        outcome = next(iter(reader.get_outcomes()), None)
        if outcome is not None and outcome.failed:
            await self._connections.mark_failure(
                connection.connection_id,
                outcome.error or "수집에 실패했습니다.",
                ConnectionStatus.UNREACHABLE,
            )
            return CollectSummary(
                connection_id=connection.connection_id,
                created=created,
                updated=updated,
                unchanged=unchanged,
                failed=True,
                error=outcome.error,
            )

        await self._connections.mark_success(connection.connection_id, observed_at)
        return CollectSummary(
            connection_id=connection.connection_id,
            created=created,
            updated=updated,
            unchanged=unchanged,
            failed=False,
        )

    async def _flush(
        self, connection_id: UUID, batch: list[VirtualMachine], observed_at: datetime
    ) -> tuple[int, int, int]:
        result = await self._vms.upsert_virtual_machines(connection_id, batch, observed_at)
        return result.created, result.updated, result.unchanged


def _failure(connection_id: UUID, message: str) -> CollectSummary:
    """실패해도 **기존 수집 데이터를 삭제하지 않는다** (NFR-302)."""
    return CollectSummary(
        connection_id=connection_id, created=0, updated=0, unchanged=0, failed=True, error=message
    )
