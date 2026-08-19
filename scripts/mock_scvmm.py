"""목업 SCVMM fabric 실행기 — 실환경 없이 수집 결과를 눈으로 확인한다.

실제 SCVMM은 개발 PC에 세울 수 없다 (docs/06_scvmm_test_environment.md §3).
이 스크립트는 `tests/ps_mocks/scvmm_fabric.ps1`의 목 cmdlet 위에서 수집 스크립트
**원본**을 실제 PowerShell로 실행하고, 원시 JSON과 매핑된 도메인 모델을 함께 보여준다.

`scvmm_scripts.py`나 `scvmm_mapper.py`를 고친 뒤 결과가 어떻게 달라지는지 바로 확인하는 용도다.
테스트와 같은 경로를 쓰므로 여기서 이상하면 테스트도 깨진다.

사용법::

    python scripts/mock_scvmm.py                      # normal 시나리오, 요약 출력
    python scripts/mock_scvmm.py --scenario large     # 500대
    python scripts/mock_scvmm.py --scenario no_module --probe
    python scripts/mock_scvmm.py --raw                # 원시 JSON까지

Windows 전용이다 (Windows PowerShell 5.1 필요).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.domain.exceptions import PortalError  # noqa: E402
from src.infrastructure.hyperv.scvmm_mapper import map_scvmm_vm  # noqa: E402
from src.infrastructure.hyperv.scvmm_scripts import (  # noqa: E402
    SCRIPT_PROBE_SCVMM,
    SCRIPT_SCVMM_LIST_VMS,
)
from tests.fakes.local_ps_runner import (  # noqa: E402
    POWERSHELL,
    SCENARIOS,
    LocalPowerShellRunner,
)


def _print_vm_table(rows: list[dict]) -> None:
    connection_id = uuid4()
    observed_at = datetime.now(UTC)
    header = f"{'NAME':<24}{'STATE':<12}{'vCPU':>5}{'MEM(MB)':>9}  {'GUEST':<22}{'HOST'}"
    print(header)
    print("-" * len(header))

    excluded = 0
    for row in rows:
        if not row.get("Id"):
            # 리더와 같은 판정식 — 식별자 없는 자원은 수집하지 않는다 (계획 05 §8.4)
            excluded += 1
            print(f"{row.get('Name', '?'):<24}{'(제외: VMId 없음)'}")
            continue
        vm = map_scvmm_vm(connection_id, row, observed_at)
        guest = vm.guest.availability.value
        if vm.guest.primary_ipv4:
            guest = f"{guest} {vm.guest.primary_ipv4}"
        print(
            f"{vm.name:<24}{vm.power_state.value:<12}{vm.cpu.total_vcpu:>5}"
            f"{vm.memory.assigned_mb:>9}  {guest:<22}{vm.host_native_id or '-'}"
        )

    print()
    print(f"수집 {len(rows) - excluded}건 / 제외 {excluded}건 (원본 {len(rows)}행)")


async def main() -> int:
    parser = argparse.ArgumentParser(description="목업 SCVMM fabric 실행기")
    parser.add_argument("--scenario", choices=SCENARIOS, default="normal")
    parser.add_argument("--probe", action="store_true", help="권한 프로브 스크립트를 실행한다")
    parser.add_argument("--raw", action="store_true", help="원시 JSON도 출력한다")
    parser.add_argument("--limit", type=int, default=20, help="표에 표시할 최대 행 수")
    args = parser.parse_args()

    if POWERSHELL is None:
        print("Windows PowerShell 5.1이 필요합니다 (Windows 전용).", file=sys.stderr)
        return 2

    runner = LocalPowerShellRunner(scenario=args.scenario)
    script = SCRIPT_PROBE_SCVMM if args.probe else SCRIPT_SCVMM_LIST_VMS
    print(f"[시나리오] {args.scenario}   [스크립트] {'프로브' if args.probe else 'VM 목록'}\n")

    try:
        rows = await runner.invoke_json(script)
    except PortalError as exc:
        # 실패도 정상적인 관찰 대상이다 — 리더가 이 예외를 어떻게 다루는지가 §9의 부분 실패 규칙
        print(f"수집 실패 (도메인 예외): {exc.message}", file=sys.stderr)
        return 1

    if args.raw:
        print(json.dumps(rows, ensure_ascii=False, indent=2)[:4000])
        print()

    if args.probe:
        probe = rows[0] if rows else {}
        for key in ("module", "version", "role", "vm", "error"):
            if key in probe:
                print(f"  {key:<8}: {probe[key]}")
        return 0

    _print_vm_table(rows[: args.limit] if args.limit else rows)
    if args.limit and len(rows) > args.limit:
        print(f"(상위 {args.limit}행만 표시 — 전체 {len(rows)}행)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
