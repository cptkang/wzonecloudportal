"""목 fabric을 로컬 PowerShell로 실행하는 러너 (docs/06 §5).

`PowerShellRunner`와 **같은 `invoke_json` 시그니처**를 가지므로 리더에 그대로 꽂을 수 있다.
WinRM/pypsrp 대신 로컬 `powershell.exe`를 쓰는 것만 다르다.

이 러너를 쓰면 `ScvmmInventoryReader`의 수집 경로 전체
(스크립트 → 실제 PowerShell → ConvertTo-Json → parse_ps_json → 제외 로직 → 매퍼 → outcome)를
실환경 없이 관통할 수 있다. 프로덕션 코드는 한 줄도 바꾸지 않는다 — 테스트에서
`reader._runner`를 이 객체로 교체한다.

**대체하지 못하는 것**: WinRM 전송·인증·세션 수명, JEA 제약 세션의 역직렬화.
그 계층은 실환경 실측에서만 확인된다 (연구 노트 §11-14·17·18).
"""

from __future__ import annotations

import asyncio
import base64
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from src.domain.exceptions import CollectionError
from src.infrastructure.hyperv.runner import parse_ps_json

POWERSHELL = shutil.which("powershell")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCVMM_FABRIC = REPO_ROOT / "tests" / "ps_mocks" / "scvmm_fabric.ps1"
HYPERV_MOCKS = REPO_ROOT / "tests" / "ps_mocks" / "hyperv_cmdlet_mocks.ps1"

#: 목 fabric 시나리오 이름 (scvmm_fabric.ps1과 동일해야 한다)
SCENARIOS = (
    "normal",
    "single",
    "empty",
    "large",
    "no_module",
    "connect_fail",
    "no_permission",
    "no_role",
)


class LocalPowerShellRunner:
    """목 cmdlet을 선로드한 뒤 수집 스크립트 원본을 로컬 PowerShell로 실행한다."""

    def __init__(
        self,
        *,
        mock_path: Path = SCVMM_FABRIC,
        scenario: str = "normal",
        timeout_seconds: int = 120,
    ) -> None:
        if POWERSHELL is None:  # pragma: no cover - Windows 외 환경은 테스트가 skip된다
            raise RuntimeError("Windows PowerShell 5.1이 필요합니다.")
        if scenario not in SCENARIOS:
            raise ValueError(f"알 수 없는 시나리오: {scenario} (가능: {', '.join(SCENARIOS)})")
        self._mock_path = mock_path
        self._scenario = scenario
        self._timeout = timeout_seconds
        #: 실행된 스크립트 원문. 쓰기 cmdlet 미호출 검증에 쓴다
        self.invoked_scripts: list[str] = []

    @property
    def scenario(self) -> str:
        return self._scenario

    def _run_sync(self, script: str) -> str:
        full = self._mock_path.read_text(encoding="utf-8") + "\n" + script
        encoded = base64.b64encode(full.encode("utf-16-le")).decode()
        env = dict(os.environ, WZONE_SCVMM_SCENARIO=self._scenario)
        proc = subprocess.run(  # noqa: S603 - 테스트 전용, 고정 인자
            [
                POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-EncodedCommand", encoded,
            ],
            capture_output=True,
            timeout=self._timeout,
            env=env,
        )
        if proc.returncode != 0:
            # PowerShellRunner가 `ps.had_errors`에서 던지는 것과 같은 도메인 예외로 맞춘다
            raise CollectionError(
                "PowerShell 실행 오류: " + proc.stderr.decode("utf-8", "replace").strip()
            )
        return proc.stdout.decode("utf-8", "replace")

    async def invoke_json(self, script: str, params: dict[str, Any] | None = None) -> list[dict]:
        """`PowerShellRunner.invoke_json`과 동일한 계약."""
        if params:  # 목 fabric은 파라미터를 쓰는 스크립트가 없다
            raise NotImplementedError("목 러너는 스크립트 파라미터를 지원하지 않습니다.")
        self.invoked_scripts.append(script)
        raw = await asyncio.to_thread(self._run_sync, script)
        return parse_ps_json(raw)


__all__ = ["HYPERV_MOCKS", "POWERSHELL", "SCENARIOS", "SCVMM_FABRIC", "LocalPowerShellRunner"]
