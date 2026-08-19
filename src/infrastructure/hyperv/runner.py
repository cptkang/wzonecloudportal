"""PowerShell 실행 + JSON 파싱 (계획 05 §5).

출력 형식 규칙 (§5.2):
- 스크립트는 `ConvertTo-Json -Depth N -Compress`로 끝나야 한다. 텍스트 테이블 파싱은
  로케일·컬럼 폭에 따라 깨진다.
- 날짜는 스크립트에서 `.ToString('o')`로 ISO 8601 문자열화한다. `/Date(...)/`를 파싱하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pypsrp.powershell import PowerShell

from src.domain.exceptions import CollectionError
from src.infrastructure.hyperv.errors import translate_error
from src.infrastructure.hyperv.session import HyperVSession
from src.infrastructure.security.masking import sanitize_message


class PowerShellRunner:
    def __init__(self, session: HyperVSession) -> None:
        self._session = session

    def _invoke_sync(self, script: str, params: dict[str, Any] | None = None) -> str:
        ps = PowerShell(self._session.pool)
        ps.add_script(script)
        for k, v in (params or {}).items():
            ps.add_parameter(k, v)
        output = ps.invoke()
        if ps.had_errors:
            errors = "; ".join(str(e) for e in ps.streams.error[:3])
            raise CollectionError(
                "PowerShell 실행 오류: "
                + sanitize_message(errors, secrets=self._session.secrets())
            )
        return "".join(str(o) for o in output)

    async def invoke_json(
        self, script: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """스크립트를 실행하고 JSON 출력을 리스트로 정규화한다."""
        try:
            raw = await asyncio.to_thread(self._invoke_sync, script, params)
        except Exception as exc:  # noqa: BLE001 - 하이퍼바이저 예외를 도메인 예외로 변환하는 경계다 (계획 03 §5)
            raise translate_error(exc, secrets=self._session.secrets()) from None
        return parse_ps_json(raw)


def parse_ps_json(raw: str) -> list[dict[str, Any]]:
    """ConvertTo-Json 출력을 파싱한다.

    PowerShell은 항목이 1개일 때 배열이 아닌 객체를 반환하므로 정규화가 필요하다 (§5.1).
    """
    text = raw.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CollectionError(f"PowerShell JSON 파싱 실패: {exc}") from None
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return []
