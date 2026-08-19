"""HypervisorInventoryReader 구현 (vCenter) — 계획 04 §7·§9.

**쓰기 API를 호출하지 않는다** (D-005). `grep -rE "_Task\\(|SetField" src/infrastructure/vcenter/`로
확인한다 — arch_check는 메서드명만 보므로 호출부는 사람이 검사해야 한다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from uuid import UUID

from pyVmomi import vim

from src.config import Settings
from src.domain.connection import Connection
from src.domain.enums import CheckStage, ResourceType
from src.domain.exceptions import AuthenticationError
from src.domain.exceptions import PermissionError as DomainPermissionError
from src.domain.ports import CollectionOutcome, ConnectionCheckResult
from src.domain.resource import VirtualMachine
from src.infrastructure.checks import StageRunner
from src.infrastructure.vcenter.collector import PropertyCollectorReader
from src.infrastructure.vcenter.errors import translate_error
from src.infrastructure.vcenter.mapper import map_virtual_machine
from src.infrastructure.vcenter.property_specs import VM_PROPERTIES_MVP
from src.infrastructure.vcenter.session import VCenterSession

REACHABLE_TIMEOUT_SECONDS = 5


class VCenterInventoryReader:
    def __init__(self, connection: Connection, settings: Settings) -> None:
        self._conn = connection
        self._session = VCenterSession(connection)
        self._pc = PropertyCollectorReader(self._session, settings.collection_page_size)
        self._outcomes: list[CollectionOutcome] = []

    @property
    def connection_id(self) -> UUID:
        return self._conn.connection_id

    @property
    def is_session_closed(self) -> bool:
        return not self._session.is_open

    # ── 세션 ─────────────────────────────────────────────────

    async def start_session(self) -> None:
        await self._session.start_session()

    async def close_session(self) -> None:
        await self._session.close_session()

    async def __aenter__(self) -> VCenterInventoryReader:
        await self.start_session()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        # 실패 경로에서도 세션을 반드시 해제한다 (계획 04 §3.1)
        await self.close_session()

    # ── 수집 ─────────────────────────────────────────────────

    async def list_virtual_machines(self) -> AsyncIterator[VirtualMachine]:
        observed_at = datetime.now(UTC)
        started = time.monotonic()
        count = 0
        try:
            async for moid, props in self._pc.retrieve(vim.VirtualMachine, VM_PROPERTIES_MVP):
                count += 1
                yield map_virtual_machine(self._conn.connection_id, moid, props, observed_at)
        except AuthenticationError:
            # 세션 자체가 무효이므로 전파한다. 워커가 연결을 자격증명 오류로 전환한다.
            raise
        except Exception as exc:  # noqa: BLE001 - 하이퍼바이저 예외를 도메인 예외로 변환하는 경계다 (계획 03 §5)
            error = translate_error(exc, secrets=self._secrets())
            if isinstance(error, AuthenticationError):
                raise error from None
            # 한 유형의 실패가 다른 유형의 수집을 중단시키면 안 된다 (FR-204).
            # 예외를 전파하지 않고 outcome에 기록한 뒤 정상 종료한다.
            self._record(ResourceType.VIRTUAL_MACHINE, count, failed=True, error=str(error))
            return
        self._record(
            ResourceType.VIRTUAL_MACHINE,
            count,
            failed=False,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    def get_outcomes(self) -> Sequence[CollectionOutcome]:
        return tuple(self._outcomes)

    # ── 연결 테스트 (FR-106) ─────────────────────────────────

    async def check_connection(self) -> ConnectionCheckResult:
        runner = StageRunner(secrets=self._secrets())
        readable: set[ResourceType] = set()

        await runner.run(CheckStage.REACHABLE, self._check_reachable)
        await runner.run(CheckStage.TLS_VALID, self._check_tls)
        await runner.run(CheckStage.AUTHENTICATED, self._check_auth)
        await runner.run(CheckStage.AUTHORIZED, lambda: self._check_authorized(readable))

        server_version = self._session.server_version if self._session.is_open else None
        await self._session.close_session()

        return ConnectionCheckResult(
            stages=runner.results,
            readable_types=frozenset(readable),
            server_version=server_version,
        )

    async def _check_reachable(self) -> str | None:
        """TCP 연결만 확인한다. 짧은 타임아웃을 쓴다."""
        fut = asyncio.open_connection(self._conn.address, self._conn.port)
        _, writer = await asyncio.wait_for(fut, timeout=REACHABLE_TIMEOUT_SECONDS)
        writer.close()
        await writer.wait_closed()
        return f"{self._conn.address}:{self._conn.port}"

    async def _check_tls(self) -> str | None:
        if not self._conn.verify_tls:
            return "검증 안 함 (verify_tls=false)"
        # 실제 검증은 다음 단계의 SmartConnect가 수행한다. 여기서 별도 핸드셰이크를 하면
        # 세션을 두 번 여는 셈이라 vCenter 부하만 늘어난다.
        return "인증서 검증 사용"

    async def _check_auth(self) -> str | None:
        await self._session.start_session()
        return self._conn.username

    async def _check_authorized(self, readable: set[ResourceType]) -> str | None:
        """자원 유형별로 1건만 조회하여 권한을 확인한다. 전량 조회는 부적절하다."""
        probe = PropertyCollectorReader(self._session, page_size=1)
        try:
            async for _ in probe.retrieve(vim.VirtualMachine, ["name"]):
                break
            readable.add(ResourceType.VIRTUAL_MACHINE)
        except vim.fault.NoPermission:
            pass
        except Exception as exc:  # noqa: BLE001 - 하이퍼바이저 예외를 도메인 예외로 변환하는 경계다 (계획 03 §5)
            raise translate_error(exc, secrets=self._secrets()) from None

        if not readable:
            raise DomainPermissionError("조회 권한이 있는 자원 유형이 없습니다.")
        return f"조회 가능: {', '.join(sorted(t.value for t in readable))}"

    # ── 내부 ─────────────────────────────────────────────────

    def _secrets(self) -> tuple[str, ...]:
        secret = self._conn.password.get_secret_value()
        return (secret,) if secret else ()

    def _record(
        self,
        rtype: ResourceType,
        count: int,
        *,
        failed: bool,
        error: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        self._outcomes.append(
            CollectionOutcome(
                resource_type=rtype,
                collected_count=count,
                failed=failed,
                error=error,
                elapsed_ms=elapsed_ms,
            )
        )


__all__ = ["VCenterInventoryReader"]
