"""수집 스크립트 실행 검증 — 실제 PowerShell 5.1 + 목 cmdlet (계획 05 §14, D-018).

SCVMM·Hyper-V는 vcsim 같은 시뮬레이터가 없고 컨테이너로도 실행할 수 없다 (D-018).
이 테스트가 그 공백을 메우는 로컬 최고 충실도 검증이다 — 스크립트 **원본을 실제
PowerShell로 실행**하여 다음을 확인한다:

- 스크립트 구문·파이프라인·KVP CIM-XML 파싱([xml] 캐스팅)이 실제로 동작한다
- 실제 `ConvertTo-Json` 출력(단일 객체/배열/null)이 `parse_ps_json` → 매퍼를 관통한다
- 생성된 JEA 역할 파일이 유효한 PowerShell 구문이며 수집 스크립트와 어긋나지 않았다

검증하지 **못하는** 것: WinRM 전송·인증, 실제 Hyper-V/SCVMM 객체 모델
(연구 노트 §11의 실환경 항목 — Step 2 실측에서 확인한다).

Windows가 아니면 전체 skip된다.
"""

from __future__ import annotations

import base64
import importlib.util
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from src.domain.enums import GuestInfoAvailability, OsSource, PowerState
from src.infrastructure.hyperv import host_mapper, scvmm_mapper
from src.infrastructure.hyperv.host_scripts import (
    SCRIPT_CLUSTER_NODES,
    SCRIPT_LIST_VMS,
    SCRIPT_PROBE_PERMISSIONS,
)
from src.infrastructure.hyperv.runner import parse_ps_json
from src.infrastructure.hyperv.scvmm_scripts import SCRIPT_PROBE_SCVMM, SCRIPT_SCVMM_LIST_VMS

POWERSHELL = shutil.which("powershell")
pytestmark = pytest.mark.skipif(
    POWERSHELL is None, reason="Windows PowerShell 5.1이 필요한 스크립트 실행 검증"
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MOCKS_PATH = REPO_ROOT / "tests" / "ps_mocks" / "hyperv_cmdlet_mocks.ps1"
NOW = datetime(2026, 8, 14, 0, 0, tzinfo=UTC)
CONN = uuid4()


def _run_powershell(code: str, *, extra_env: dict[str, str] | None = None) -> str:
    encoded = base64.b64encode(code.encode("utf-16-le")).decode()
    env = dict(os.environ, **(extra_env or {}))
    proc = subprocess.run(  # noqa: S603 - 테스트 전용, 고정 인자
        [
            POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", encoded,
        ],
        capture_output=True,
        timeout=120,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return proc.stdout.decode("utf-8", "replace")


def _run_script(script_text: str, *, single_vm: bool = False) -> str:
    """목 cmdlet을 로드한 뒤 수집 스크립트 원본을 그대로 실행한다."""
    full = MOCKS_PATH.read_text(encoding="utf-8") + "\n" + script_text
    return _run_powershell(full, extra_env={"WZONE_MOCK_SINGLE": "1"} if single_vm else None)


@pytest.fixture(scope="module")
def host_rows() -> list[dict]:
    return parse_ps_json(_run_script(SCRIPT_LIST_VMS))


@pytest.fixture(scope="module")
def scvmm_rows() -> list[dict]:
    return parse_ps_json(_run_script(SCRIPT_SCVMM_LIST_VMS))


# ── 경로 A — 호스트 VM 스크립트 (§6.1) ───────────────────────


def test_host_script_emits_both_scenario_vms(host_rows: list[dict]) -> None:
    assert [r["Name"] for r in host_rows] == ["mock-vm-kvp", "mock-vm-nokvp"]


def test_kvp_xml_is_parsed_by_real_powershell(host_rows: list[dict]) -> None:
    """KVP 추출이 이 스크립트의 핵심이다 (§6.1) — 실제 [xml] 캐스팅 경로를 확인한다."""
    row = host_rows[0]
    assert row["IntegrationOk"] is True
    assert row["KvpOSName"] == "Windows Server 2022 Standard"
    assert row["KvpFQDN"] == "mock-vm-kvp.example.invalid"
    assert row["KvpIPv4"] == "10.10.0.5;169.254.10.5"  # 세미콜론 원문 그대로


def test_kvp_vm_maps_to_available_guest(host_rows: list[dict]) -> None:
    vm = host_mapper.map_virtual_machine(CONN, host_rows[0], NOW)
    assert vm.guest.availability is GuestInfoAvailability.AVAILABLE
    assert vm.guest.os_source is OsSource.GUEST_TOOLS
    assert vm.guest.ipv4_addresses == ("10.10.0.5",)  # 링크로컬 169.254 제거·중복 병합
    assert vm.guest.ipv6_addresses == ()  # fe80 제거
    assert vm.bios_uuid == "4c4c4544-004d-3510-8054-b4c04f435831"  # {대문자} → 정규화
    assert vm.power_state is PowerState.ON
    assert vm.memory.assigned_mb == 8192


def test_no_kvp_vm_maps_to_tools_not_installed(host_rows: list[dict]) -> None:
    vm = host_mapper.map_virtual_machine(CONN, host_rows[1], NOW)
    assert vm.guest.availability is GuestInfoAvailability.TOOLS_NOT_INSTALLED
    assert vm.power_state is PowerState.OFF
    assert vm.memory.assigned_mb == 2048  # MemoryAssigned=0 → MemoryStartup 폴백
    assert vm.bios_uuid is None


def test_single_vm_json_object_is_normalized_to_list() -> None:
    """PowerShell은 항목 1개면 배열이 아닌 객체를 낸다 — 실물 출력으로 §5.1을 확인한다."""
    raw = _run_script(SCRIPT_LIST_VMS, single_vm=True)
    assert raw.strip().startswith("{")  # 실제로 단일 객체가 나왔다
    rows = parse_ps_json(raw)
    assert len(rows) == 1 and rows[0]["Name"] == "mock-vm-kvp"


# ── 경로 A — 클러스터·프로브 (§6.3·§10) ──────────────────────


def test_cluster_nodes_script_shape() -> None:
    data = parse_ps_json(_run_script(SCRIPT_CLUSTER_NODES))[0]
    assert data["ClusterName"] == "MOCK-HVC01"
    assert [(n["Name"], n["State"]) for n in data["Nodes"]] == [
        ("mock-n1.example.invalid", "Up"),
        ("mock-n2.example.invalid", "Down"),
    ]


def test_host_probe_script_reports_vm_and_wmi() -> None:
    probe = parse_ps_json(_run_script(SCRIPT_PROBE_PERMISSIONS))[0]
    assert probe["vm"] is True
    assert probe["wmi"] is True
    assert "Windows Server" in probe["os"]


# ── 경로 B — SCVMM (§7.2·§10) ────────────────────────────────


def test_scvmm_script_marks_undeployed_vm_with_null_id(scvmm_rows: list[dict]) -> None:
    """VMId 없는 VM이 Id=null로 내려와야 리더의 제외 로직(§8.4)이 동작한다."""
    assert len(scvmm_rows) == 2
    assert scvmm_rows[0]["Id"] == "a1b2c3d4-0000-1111-2222-333344445555"
    assert scvmm_rows[1]["Id"] is None
    # 리더와 같은 판정식으로 정확히 1건이 제외된다
    assert sum(1 for r in scvmm_rows if not r.get("Id")) == 1


def test_scvmm_vm_maps_through_real_output(scvmm_rows: list[dict]) -> None:
    vm = scvmm_mapper.map_scvmm_vm(CONN, scvmm_rows[0], NOW)
    assert vm.native_id == "a1b2c3d4-0000-1111-2222-333344445555"
    assert vm.memory.assigned_mb == 8192  # SCVMM은 이미 MB
    assert vm.guest.os_source is OsSource.VM_CONFIG
    assert vm.guest.ipv4_addresses == ("10.10.0.5",)
    assert vm.host_native_id == "mock-hv01.example.invalid"


def test_scvmm_probe_script_shape() -> None:
    probe = parse_ps_json(_run_script(SCRIPT_PROBE_SCVMM))[0]
    assert probe["module"] is True
    assert probe["vm"] is True
    assert probe["version"] == "10.22.1287.0"
    assert probe["role"] == "ReadOnlyAdmin"


# ── JEA 역할 파일 (§4.3.1·§12-13) ────────────────────────────


def _parse_errors(path: Path) -> str:
    code = (
        "$tokens = $null; $errs = $null;"
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{path}',"
        "[ref]$tokens, [ref]$errs);"
        "if ($errs.Count -gt 0) { $errs | ForEach-Object { $_.Message } } else { 'OK' }"
    )
    return _run_powershell(code).strip()


@pytest.mark.parametrize("name", ["WzonePortalReadOnly.psrc", "WzonePortalReadOnly.pssc"])
def test_generated_jea_files_are_valid_powershell(name: str) -> None:
    path = REPO_ROOT / "scripts" / "jea" / name
    assert path.is_file(), "python scripts/generate_jea_role.py 로 생성한다"
    assert _parse_errors(path) == "OK"


def test_jea_role_file_matches_current_scripts() -> None:
    """JEA 함수 본문이 host_scripts.py와 어긋나면 두 실행 경로의 JSON이 갈린다 (§12-13)."""
    spec = importlib.util.spec_from_file_location(
        "generate_jea_role", REPO_ROOT / "scripts" / "generate_jea_role.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    current = (REPO_ROOT / "scripts" / "jea" / "WzonePortalReadOnly.psrc").read_text(
        encoding="utf-8-sig"
    )
    assert current == module.build_psrc(), (
        "host_scripts.py가 변경되었습니다 — python scripts/generate_jea_role.py 로 "
        "JEA 역할 파일을 재생성해 대상 호스트에 재배포하세요"
    )
