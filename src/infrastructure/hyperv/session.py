"""WinRM 세션 관리 (계획 05 §4).

pypsrp는 동기 라이브러리이므로(0.9 기준 비동기 API 없음 — §11 검증) 모든 호출을
`asyncio.to_thread`로 오프로드한다.

JEA 제약 세션(경로 A)은 `RunspacePool(configuration_name=...)`으로 붙는다 —
pypsrp 0.9가 이 파라미터를 지원함을 확인했다 (계획 05 §4.3.1의 [검증 필요] 해소).
"""

from __future__ import annotations

import asyncio
import logging

from pypsrp.powershell import RunspacePool
from pypsrp.wsman import WSMan

from src.config import Settings
from src.domain.connection import Connection
from src.domain.enums import WinRmAuth
from src.domain.exceptions import CollectionError, ValidationError
from src.infrastructure.hyperv.errors import translate_error

logger = logging.getLogger(__name__)

AUTH_MAP: dict[WinRmAuth, str] = {
    WinRmAuth.NTLM: "ntlm",
    WinRmAuth.KERBEROS: "kerberos",
    WinRmAuth.CREDSSP: "credssp",
}

#: JEA 미사용 시 WinRM 기본 엔드포인트
DEFAULT_CONFIGURATION = "Microsoft.PowerShell"


class HyperVSession:
    """WinRM 세션. RunspacePool을 재사용하여 연결 비용을 줄인다."""

    def __init__(self, connection: Connection, settings: Settings) -> None:
        self._conn = connection
        self._settings = settings
        self._wsman: WSMan | None = None
        self._pool: RunspacePool | None = None

    @property
    def is_open(self) -> bool:
        return self._pool is not None

    @property
    def pool(self) -> RunspacePool:
        if self._pool is None:
            raise CollectionError("세션이 열려 있지 않습니다.")
        return self._pool

    @property
    def uses_jea(self) -> bool:
        """JEA 제약 세션 여부. 스크립트 대신 역할 기능 함수를 호출해야 한다 (계획 05 §4.3.1)."""
        return self._conn.session_configuration is not None

    def secrets(self) -> tuple[str, ...]:
        secret = self._conn.password.get_secret_value()
        return (secret,) if secret else ()

    def _open_sync(self) -> RunspacePool:
        if self._conn.auth_method is None:
            raise ValidationError("Hyper-V 연결은 인증 방식이 필요합니다.", field="auth_method")

        timeout = self._settings.collection_timeout_seconds
        self._wsman = WSMan(
            server=self._conn.address,
            port=self._conn.port,
            username=self._conn.username,
            # 평문화는 이 지점에서만 한다 (NFR-209)
            password=self._conn.password.get_secret_value(),
            ssl=(self._conn.protocol == "https"),
            auth=AUTH_MAP[self._conn.auth_method],
            cert_validation=self._conn.verify_tls,
            connection_timeout=timeout,
            # read_timeout은 operation_timeout보다 커야 한다 (pypsrp 제약).
            # 원격 PowerShell은 느리므로 수집 타임아웃을 그대로 상한으로 쓴다.
            operation_timeout=timeout,
            read_timeout=timeout + 10,
        )
        pool = RunspacePool(
            self._wsman,
            configuration_name=self._conn.session_configuration or DEFAULT_CONFIGURATION,
        )
        pool.open()
        return pool

    async def start_session(self) -> None:
        try:
            self._pool = await asyncio.to_thread(self._open_sync)
        except Exception as exc:  # noqa: BLE001 - 하이퍼바이저 예외를 도메인 예외로 변환하는 경계다 (계획 03 §5)
            await self._dispose_wsman()
            # 원본 예외 체이닝을 끊는다 — 메시지에 접속 정보가 섞일 수 있다 (계획 10 §5.2)
            raise translate_error(exc, secrets=self.secrets()) from None

    async def close_session(self) -> None:
        """수집 실패 시에도 반드시 호출한다."""
        if self._pool is not None:
            try:
                await asyncio.to_thread(self._pool.close)
            except Exception:  # noqa: BLE001 - 종료 실패가 수집 결과를 바꾸지 않는다
                logger.warning(
                    "WinRM 세션 종료 실패",
                    extra={"connection_id": str(self._conn.connection_id)},
                )
            finally:
                self._pool = None
        await self._dispose_wsman()

    async def _dispose_wsman(self) -> None:
        if self._wsman is not None:
            try:
                await asyncio.to_thread(self._wsman.close)
            except Exception:  # noqa: BLE001 - 정리 실패는 수집 결과를 바꾸지 않는다
                logger.debug("WSMan 종료 실패 (무시)")
            finally:
                self._wsman = None

    async def __aenter__(self) -> HyperVSession:
        await self.start_session()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close_session()
