"""vCenter 세션 관리 (계획 04 §3).

pyVmomi는 동기 라이브러리이므로 모든 호출을 `asyncio.to_thread`로 오프로드한다.
없이 호출하면 이벤트 루프가 멈춘다.
"""

from __future__ import annotations

import asyncio
import logging
import ssl

from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim

from src.domain.connection import Connection
from src.domain.exceptions import CollectionError
from src.infrastructure.vcenter.errors import translate_error

logger = logging.getLogger(__name__)


class VCenterSession:
    def __init__(self, connection: Connection) -> None:
        self._conn = connection
        self._si: vim.ServiceInstance | None = None

    @property
    def is_open(self) -> bool:
        return self._si is not None

    @property
    def content(self) -> vim.ServiceInstanceContent:
        if self._si is None:
            raise CollectionError("세션이 열려 있지 않습니다.")
        return self._si.RetrieveContent()

    @property
    def server_version(self) -> str | None:
        if self._si is None:
            return None
        about = self.content.about
        return f"{about.fullName}"

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        """TLS 검증 정책에 따른 컨텍스트 (FR-115)."""
        if self._conn.verify_tls:
            return None  # 기본 검증 사용
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _connect_sync(self) -> vim.ServiceInstance:
        return SmartConnect(
            host=self._conn.address,
            port=self._conn.port,
            user=self._conn.username,
            # 평문화는 이 지점에서만 한다 (NFR-209)
            pwd=self._conn.password.get_secret_value(),
            sslContext=self._build_ssl_context(),
        )

    async def start_session(self) -> None:
        try:
            self._si = await asyncio.to_thread(self._connect_sync)
        except Exception as exc:
            # 원본 예외 체이닝을 끊는다 — 메시지에 접속 정보가 섞일 수 있다 (계획 10 §5.2)
            raise translate_error(
                exc, secrets=(self._conn.password.get_secret_value(),)
            ) from None

    async def close_session(self) -> None:
        """수집 실패 시에도 반드시 호출한다. vCenter는 유휴 세션을 일정 시간 유지한다."""
        if self._si is None:
            return
        try:
            await asyncio.to_thread(Disconnect, self._si)
        except Exception:  # noqa: BLE001 - 종료 실패가 수집 결과를 바꾸지 않는다
            logger.warning(
                "vCenter 세션 종료 실패",
                extra={"connection_id": str(self._conn.connection_id)},
            )
        finally:
            self._si = None

    async def __aenter__(self) -> "VCenterSession":
        await self.start_session()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close_session()
