"""전원 상태·GUID·용량 정규화 — 두 경로 공통 (계획 05 §7.1·§8.1).

경로 A는 CIM 정수값(`[int]$vm.State`), 경로 B는 .NET 열거형 이름을 반환한다.
**둘 다 로케일 영향을 받지 않는다.** 로케일에 취약한 것은 `Get-VM`의 `Status` 같은
표시 문자열이며, 그 값은 쓰지 않는다 (계획 02 §9.1).
"""

from __future__ import annotations

import logging
import re

from src.domain.enums import PowerState

logger = logging.getLogger(__name__)

#: 경로 A — Hyper-V VMState 정수값 (Msvm_ComputerSystem.EnabledState 계열).
#: 2=Running, 3=Off, 6=Saved, 9=Paused. 전이 상태(Starting=10, Stopping=4 등)는
#: 매핑하지 않고 UNKNOWN으로 둔다 — 실측 전 추정 매핑은 오판을 만든다 (§8.1 [검증 필요]).
HYPERV_ENABLED_STATE_MAP: dict[int, PowerState] = {
    2: PowerState.ON,
    3: PowerState.OFF,
    6: PowerState.SUSPENDED,
    9: PowerState.SUSPENDED,
}

#: 경로 B — VirtualMachineState 열거형 이름 (계획 05 §8.1)
SCVMM_STATE_MAP: dict[str, PowerState] = {
    "Running": PowerState.ON,
    "PowerOff": PowerState.OFF,
    "Stopped": PowerState.OFF,
    "Paused": PowerState.SUSPENDED,
    "Saved": PowerState.SUSPENDED,
}


def map_power_state(state: int | str | None) -> PowerState:
    """전원 상태를 공통 4값으로 매핑한다.

    매핑되지 않는 값은 UNKNOWN으로 두되 **로그에 원본 값을 남겨** 누락을 발견할 수 있게
    한다 (계획 05 §8.1).
    """
    if state is None:
        return PowerState.UNKNOWN
    if isinstance(state, bool):  # bool은 int의 하위 타입 — 잘못된 입력으로 취급한다
        return PowerState.UNKNOWN
    if isinstance(state, int):
        mapped = HYPERV_ENABLED_STATE_MAP.get(state)
    else:
        mapped = SCVMM_STATE_MAP.get(str(state))
    if mapped is None:
        logger.info("매핑되지 않은 전원 상태", extra={"raw_power_state": str(state)})
        return PowerState.UNKNOWN
    return mapped


_GUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def normalize_guid(raw: str | None) -> str | None:
    """GUID를 소문자·중괄호 없는 형식으로 정규화한다.

    WMI `BIOSGUID`는 `{4C4C4544-...}` 형태로 온다. vCenter `config.uuid`(소문자,
    중괄호 없음)와 같은 표기로 맞춰야 2순위 교차 식별(bios_uuid)이 동작한다 (계획 05 §8.4).
    """
    if not raw:
        return None
    cleaned = raw.strip().strip("{}").lower()
    return cleaned if _GUID_PATTERN.fullmatch(cleaned) else None


def to_mb(raw_bytes: int | float | None) -> int | None:
    """바이트 → MB. Hyper-V cmdlet은 메모리를 바이트로 반환한다."""
    if raw_bytes is None:
        return None
    return int(int(raw_bytes) // 1_048_576)


def split_kvp_addresses(raw: str | None) -> list[str]:
    """KVP의 `NetworkAddressIPv4`는 세미콜론(간혹 쉼표) 구분 문자열로 온다 (계획 05 §8.2)."""
    if not raw:
        return []
    return [p.strip() for p in re.split(r"[;,]", raw) if p.strip()]
