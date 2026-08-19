"""HyperVHostInventoryReader — 경로 A (계획 05 §6·§9·§10).

`hyperv-host`(호스트 1대)와 `hyperv-cluster`(장애 조치 클러스터) 연결을 처리한다.

클러스터 이름 접속은 임의 노드로 라우팅되므로(§6.3), 클러스터 연결은 노드 목록을 얻은 뒤
**노드별로 개별 접속**해 수집한다. 일부 노드가 실패해도 나머지는 계속한다 (FR-204, §9).

JEA 제약 세션(`session_configuration` 지정)에서는 스크립트 블록이 실행되지 않으므로
역할 기능 함수 이름을 호출한다 (§4.3.1). 두 실행 경로는 같은 JSON을 반환해야 한다 (§12-13).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from src.config import Settings
from src.domain.connection import Connection
from src.domain.enums import CheckStage, ConnectionKind, ResourceType
from src.domain.exceptions import (
    AuthenticationError,
    CollectionError,
    PortalError,
)
from src.domain.exceptions import (
    PermissionError as DomainPermissionError,
)
from src.domain.ports import CollectionOutcome, ConnectionCheckResult
from src.domain.resource import VirtualMachine
from src.infrastructure.checks import StageRunner
from src.infrastructure.hyperv.host_mapper import map_virtual_machine
from src.infrastructure.hyperv.host_scripts import (
    FUNCTION_CLUSTER_NODES,
    FUNCTION_LIST_VMS,
    FUNCTION_PROBE,
    SCRIPT_CLUSTER_NODES,
    SCRIPT_LIST_VMS,
    SCRIPT_PROBE_PERMISSIONS,
)
from src.infrastructure.hyperv.runner import PowerShellRunner
from src.infrastructure.hyperv.session import HyperVSession

logger = logging.getLogger(__name__)

REACHABLE_TIMEOUT_SECONDS = 5


class HyperVHostInventoryReader:
    def __init__(self, connection: Connection, settings: Settings) -> None:
        self._conn = connection
        self._settings = settings
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

    async def __aenter__(self) -> HyperVHostInventoryReader:
        await self.start_session()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close_session()

    # ── 수집 (§9 — 클러스터 노드 순회·부분 실패) ─────────────

    async def list_virtual_machines(self) -> AsyncIterator[VirtualMachine]:
        observed_at = datetime.now(UTC)
        started = time.monotonic()
        vm_script = FUNCTION_LIST_VMS if self._session.uses_jea else SCRIPT_LIST_VMS
        total = 0
        failed_nodes: list[tuple[str, str]] = []

        if self._conn.kind is ConnectionKind.HYPERV_CLUSTER:
            try:
                nodes = await self._resolve_nodes()
            except AuthenticationError:
                raise
            except PortalError as exc:
                self._record(0, failed=True, error=exc.message)
                return

            for node, state in nodes:
                if state.lower() != "up":
                    # 다운 노드는 접속 타임아웃만 낭비한다. 실패 목록에 남겨 관리자가 인지한다
                    failed_nodes.append((node, f"노드 상태 {state}"))
                    continue
                node_session = HyperVSession(replace(self._conn, address=node), self._settings)
                try:
                    await node_session.start_session()
                    rows = await PowerShellRunner(node_session).invoke_json(vm_script)
                except AuthenticationError:
                    # 같은 자격증명이 모든 노드에서 실패한다 — 반복 시도는 계정 잠금이다 (CST-05)
                    raise
                except PortalError as exc:
                    failed_nodes.append((node, exc.message))
                    logger.warning(
                        "노드 수집 실패", extra={"node": node, "error": exc.message}
                    )
                    continue
                finally:
                    await node_session.close_session()
                for row in rows:
                    total += 1
                    yield map_virtual_machine(self._conn.connection_id, row, observed_at)
        else:
            try:
                rows = await self._runner.invoke_json(vm_script)
            except AuthenticationError:
                raise
            except PortalError as exc:
                self._record(0, failed=True, error=exc.message)
                return
            for row in rows:
                total += 1
                yield map_virtual_machine(self._conn.connection_id, row, observed_at)

        # 일부 노드만 실패하면 failed=False — 수집된 VM은 저장되고 error로 관리자가 인지한다 (§9).
        # 워커의 미발견 처리(Step 3)는 error가 있으면 건너뛰어야 한다 (계획 06 §11).
        self._record(
            total,
            failed=bool(failed_nodes) and total == 0,
            error=(
                f"{len(failed_nodes)}개 노드 수집 실패: "
                + ", ".join(f"{n} ({e})" for n, e in failed_nodes)
                if failed_nodes
                else None
            ),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    async def _resolve_nodes(self) -> list[tuple[str, str]]:
        """클러스터 노드 (이름, 상태) 목록. 클러스터 주소의 세션으로 1회 조회한다 (§6.3)."""
        script = FUNCTION_CLUSTER_NODES if self._session.uses_jea else SCRIPT_CLUSTER_NODES
        rows = await self._runner.invoke_json(script)
        data = rows[0] if rows else {}
        nodes = [
            (str(n["Name"]), str(n.get("State") or "Unknown"))
            for n in data.get("Nodes") or []
            if n.get("Name")
        ]
        if not nodes:
            raise CollectionError("클러스터 노드 목록을 얻지 못했습니다.")
        return nodes

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
        fut = asyncio.open_connection(self._conn.address, self._conn.port)
        _, writer = await asyncio.wait_for(fut, timeout=REACHABLE_TIMEOUT_SECONDS)
        writer.close()
        await writer.wait_closed()
        return f"{self._conn.address}:{self._conn.port}"

    async def _check_tls(self) -> str | None:
        if self._conn.protocol == "http":
            return "HTTP — 전송 계층 암호화 없음"
        if not self._conn.verify_tls:
            return "검증 안 함 (verify_tls=false)"
        return "인증서 검증 사용"

    async def _check_auth(self) -> str | None:
        await self._session.start_session()
        auth = self._conn.auth_method.value if self._conn.auth_method else "?"
        jea = f", JEA: {self._conn.session_configuration}" if self._session.uses_jea else ""
        return f"{self._conn.username} ({auth}{jea})"

    async def _check_authorized(self, readable: set[ResourceType]) -> str | None:
        """Hyper-V 모듈과 WMI(KVP) 접근을 각각 확인한다 — §4.3의 권한 문제를 드러낸다."""
        script = FUNCTION_PROBE if self._session.uses_jea else SCRIPT_PROBE_PERMISSIONS
        rows = await self._runner.invoke_json(script)
        probe = rows[0] if rows else {}

        self._server_version = probe.get("os") or None
        if probe.get("vm"):
            readable.add(ResourceType.VIRTUAL_MACHINE)
        if not readable:
            raise DomainPermissionError(
                "Hyper-V VM 조회 권한이 없습니다. JEA 역할 또는 계정 권한을 확인하세요."
            )

        detail = "조회 가능: " + ", ".join(sorted(t.value for t in readable))
        if not probe.get("wmi"):
            detail += " · KVP(WMI) 접근 불가 — 게스트 OS·IP를 수집할 수 없습니다"
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


__all__ = ["HyperVHostInventoryReader"]
