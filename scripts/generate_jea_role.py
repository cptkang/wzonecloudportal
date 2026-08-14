"""JEA 역할 기능 파일 생성기 (계획 05 §4.3.1).

경로 A의 수집 스크립트(`host_scripts.py`)를 **단일 출처**로 삼아 JEA 역할 기능
파일(.psrc)과 세션 구성 파일(.pssc)을 생성한다. 스크립트를 수정하면 이 생성기를
다시 실행해 대상 호스트에 재배포해야 한다 — 손으로 복사하면 두 실행 경로의 JSON이
어긋난다 (§12-13 등가 확인).

사용:
    python scripts/generate_jea_role.py          # scripts/jea/ 에 생성

배포 (대상 Hyper-V 호스트에서 관리자로):
    1. WzonePortalReadOnly.psrc →
       C:/Program Files/WindowsPowerShell/Modules/WzonePortalReadOnly/RoleCapabilities/
    2. WzonePortalReadOnly.pssc 의 RoleDefinitions 그룹명을 환경에 맞게 수정
    3. Register-PSSessionConfiguration -Name WzonePortalReadOnly -Path <pssc 경로>
    4. 포탈의 연결 등록에서 "JEA 세션 구성"에 WzonePortalReadOnly 입력

**JEA 구성 전에는 해당 호스트를 등록하지 않는다** (CLAUDE.md, 계획 05 §4.3.2).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.infrastructure.hyperv.host_scripts import (  # noqa: E402
    FUNCTION_CLUSTER_NODES,
    FUNCTION_LIST_VMS,
    FUNCTION_PROBE,
    SCRIPT_CLUSTER_NODES,
    SCRIPT_LIST_VMS,
    SCRIPT_PROBE_PERMISSIONS,
)

OUT_DIR = Path(__file__).resolve().parent / "jea"

FUNCTIONS: tuple[tuple[str, str], ...] = (
    (FUNCTION_LIST_VMS, SCRIPT_LIST_VMS),
    (FUNCTION_CLUSTER_NODES, SCRIPT_CLUSTER_NODES),
    (FUNCTION_PROBE, SCRIPT_PROBE_PERMISSIONS),
)


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else line for line in text.strip().splitlines())


def build_psrc() -> str:
    defs = []
    for name, body in FUNCTIONS:
        defs.append(
            "    @{\n"
            f"        Name = '{name}'\n"
            "        ScriptBlock = {\n"
            f"{_indent(body, 12)}\n"
            "        }\n"
            "    }"
        )
    joined = "\n".join(defs)
    return (
        "# WzonePortalReadOnly.psrc — wzoneportal 수집 전용 JEA 역할 기능\n"
        "# 자동 생성 파일. 수정하지 말 것 — python scripts/generate_jea_role.py 로 재생성한다.\n"
        "# 함수 본문은 src/infrastructure/hyperv/host_scripts.py 와 동일해야 한다 (계획 05 §12-13).\n"
        "@{\n"
        "    GUID = 'e3f1a7c2-5b1d-4c8e-9f30-2a6d84c1b7e5'\n"
        "    Author = 'wzoneportal'\n"
        "    Description = 'Read-only Hyper-V inventory collection functions'\n"
        "    FunctionDefinitions = @(\n"
        f"{joined}\n"
        "    )\n"
        "    VisibleFunctions = 'Get-Wzone*'\n"
        "}\n"
    )


def build_pssc() -> str:
    return (
        "# WzonePortalReadOnly.pssc — JEA 세션 구성 (계획 05 §4.3.1)\n"
        "# 자동 생성 파일. RoleDefinitions의 그룹명을 배포 환경에 맞게 수정한 뒤 등록한다.\n"
        "@{\n"
        "    SchemaVersion = '2.0.0.0'\n"
        "    GUID = 'b8d2c4e6-7a93-4f21-8d55-c09e13f6a2d4'\n"
        "    SessionType = 'RestrictedRemoteServer'\n"
        "    RunAsVirtualAccount = $true\n"
        "    RoleDefinitions = @{\n"
        "        'DOMAIN\\WzonePortalReaders' = @{ RoleCapabilities = 'WzonePortalReadOnly' }\n"
        "    }\n"
        "}\n"
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "WzonePortalReadOnly.psrc").write_text(build_psrc(), encoding="utf-8-sig")
    (OUT_DIR / "WzonePortalReadOnly.pssc").write_text(build_pssc(), encoding="utf-8-sig")
    print(f"생성 완료: {OUT_DIR}")


if __name__ == "__main__":
    main()
