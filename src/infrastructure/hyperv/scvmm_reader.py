"""ScvmmInventoryReader — 경로 B (계획 05 §7·§9·§10).

SCVMM이 fabric 전체를 알고 있으므로 호출은 1회이며, 경로 A의 노드 순회가 없다.
부분 실패 단위는 노드가 아니라 **자원 유형**이다 (§9).

**쓰기 cmdlet을 호출하지 않는다** (D-005). 스크립트는 `scvmm_scripts.py`에만 두고
§14의 grep 검사를 통과해야 한다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from uuid import UUID

from src.config import Settings
from src.domain.connection import Connection
from src.domain.enums import CheckStage, ResourceType
from src.domain.exceptions import (
    AuthenticationError,
    PermissionError as DomainPermissionError,
    PortalError,
    ValidationError,
)
from src.domain.ports import CollectionOutcome, ConnectionCheckResult
from src.domain.resource import VirtualMachine
from src.infrastructure.checks import StageRunner
from src.infrastructure.hyperv.runner import PowerShellRunner
from src.infrastructure.hyperv.scvmm_mapper import map_scvmm_vm
from src.infrastructure.hyperv.scvmm_scripts import SCRIPT_PROBE_SCVMM, SCRIPT_SCVMM_LIST_VMS
from src.infrastructure.hyperv.session import HyperVSession

REACHABLE_TIMEOUT_SECONDS = 5


class ScvmmInventoryReader:
    def __init__(self, connection: Connection, settings: Settings) -> None:
        self._conn = connection
        self._session = HyperVSession(connection, settings)
        self._runner = PowerShellRunner(self._session)
        self._outcomes: list[CollectionOutcome] = []
        self._server_version: str | None = None

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

    async def __aenter__(self) -> "ScvmmInventoryReader":
        await self.start_session()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close_session()

    # ── 수집 ─────────────────────────────────────────────────

    async def list_virtual_machines(self) -> AsyncIterator[VirtualMachine]:
        observed_at = datetime.now(UTC)
        started = time.monotonic()
        count = 0
        excluded = 0
        try:
            rows = await self._runner.invoke_json(SCRIPT_SCVMM_LIST_VMS)
        except AuthenticationError:
            # 세션 자체가 무효 — 전파하여 연결을 자격증명 오류로 전환한다 (FR-114)
            raise
        except PortalError as exc:
            # 한 유형의 실패가 다른 유형의 수집을 중단시키면 안 된다 (FR-204)
            self._record(count, failed=True, error=str(exc.message))
            return

        for row in rows:
            if not row.get("Id"):
                # VMId 없는 VM(등록만 되고 미배포)은 제외한다 — 식별자 없는 자원을
                # 만들면 재수집마다 중복이 쌓인다 (계획 05 §8.4)
                excluded += 1
                continue
            count += 1
            yield map_scvmm_vm(self._conn.connection_id, row, observed_at)

        self._record(
            count,
            failed=False,
            error=f"VM GUID(VMId)가 없는 VM {excluded}건 제외" if excluded else None,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    def get_outcomes(self) -> Sequence[CollectionOutcome]:
        return tuple(self._outcomes)

    # ── 연결 테스트 (FR-106, 계획 05 §10) ────────────────────

    async def check_connection(self) -> ConnectionCheckResult:
        runner = StageRunner(secrets=self._session.secrets())
        readable: set[ResourceType] = set()

        await runner.run(CheckStage.REACHABLE, self._check_reachable)
        await runner.run(CheckStage.TLS_VALID, self._check_tls)
        await runner.run(CheckStage.AUTHENTICATED, self._check_auth)
        await runner.run(CheckStage.AUTHORIZED, lambda: self._check_authorized(readable))

        await self._session.close_session()
        return ConnectionCheckResult(
            stages=runner.results,
            readable_types=frozenset(readable),
            server_version=self._server_version,
        )

    async def _check_reachable(self) -> str | None:
        """TCP 연결만 확인한다. 짧은 타임아웃을 쓴다."""
        fut = asyncio.open_connection(self._conn.address, self._conn.port)
        _, writer = await asyncio.wait_for(fut, timeout=REACHABLE_TIMEOUT_SECONDS)
        writer.close()
        await writer.wait_closed()
        return f"{self._conn.address}:{self._conn.port}"

    async def _check_tls(self) -> str | None:
        if self._conn.protocol == "http":
            # WinRM HTTP도 메시지 암호화는 있으나 전송 계층 보호가 없다 — UI가 경고한다 (§4.1)
            return "HTTP — 전송 계층 암호화 없음"
        if not self._conn.verify_tls:
            return "검증 안 함 (verify_tls=false)"
        # 실제 검증은 다음 단계의 세션 수립이 수행한다 (계획 04 §3과 동일한 이유)
        return "인증서 검증 사용"

    async def _check_auth(self) -> str | None:
        await self._session.start_session()
        auth = self._conn.auth_method.value if self._conn.auth_method else "?"
        return f"{self._conn.username} ({auth})"

    async def _check_authorized(self, readable: set[ResourceType]) -> str | None:
        """SCVMM 모듈·서버 연결·VM 조회 권한을 프로브 스크립트 하나로 확인한다."""
        rows = await self._runner.invoke_json(SCRIPT_PROBE_SCVMM)
        probe = rows[0] if rows else {}

        if not probe.get("module"):
            # 인증 실패가 아니라 연결 유형/주소 오류다 — 명확히 구분한다 (계획 05 §10·§11)
            raise ValidationError(
                "대상 서버에서 SCVMM 모듈을 찾을 수 없습니다. "
                "SCVMM 관리 서버 주소가 맞는지, 연결 유형이 올바른지 확인하세요.",
                field="kind",
            )

        version = probe.get("version")
        self._server_version = f"SCVMM {version}" if version else "SCVMM"

        if probe.get("vm"):
            readable.add(ResourceType.VIRTUAL_MACHINE)
        if not readable:
            raise DomainPermissionError("조회 권한이 있는 자원 유형이 없습니다.")

        detail = "조회 가능: " + ", ".join(sorted(t.value for t in readable))
        role = probe.get("role")
        if role:
            detail += f" (VMM 역할: {role})"
        return detail

    # ── 내부 ─────────────────────────────────────────────────

    def _record(
        self,
        count: int,
        *,
        failed: bool,
        error: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        self._outcomes.append(
            CollectionOutcome(
                resource_type=ResourceType.VIRTUAL_MACHINE,
                collected_count=count,
                failed=failed,
                error=error,
                elapsed_ms=elapsed_ms,
            )
        )


__all__ = ["ScvmmInventoryReader"]
