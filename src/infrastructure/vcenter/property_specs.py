"""수집 속성 목록 (계획 04 §5의 Step 1 축소판, ROADMAP §7.2).

**필요한 속성만 명시한다.** `all=True`나 넓은 `pathSet`은 응답을 폭증시킨다.

**`config.hardware.device`를 뺀 것이 핵심이다.** 이 속성이 응답 크기의 대부분을
차지한다. 디스크·NIC를 다루지 않는 Step 1에서는 제외하여 수집 시간을 실측하기 좋은
조건을 만든다. Step 4에서 추가할 때 같은 환경에서 전후 소요 시간을 비교하면
이 속성의 비용을 정량화할 수 있다.

> **[검증 필요]** 속성 경로는 vSphere 버전에 따라 존재 여부가 다르다. 지원 하한이
> 6.5이므로(CST-10) 6.5 환경에서 실측한다 (Step 2 §15.2-7). 누락 속성은 예외가 아니라
> `None`으로 처리된다 (`collector._props_to_dict`).
"""

from __future__ import annotations

VM_PROPERTIES_MVP: list[str] = [
    "name",
    "config.instanceUuid",  # native_id — CI 식별 1순위
    "config.uuid",  # bios_uuid — 2순위 (Step 5 대비 지금부터 저장)
    "config.guestFullName",  # 구성값 OS
    "config.hardware.numCPU",
    "config.hardware.memoryMB",
    "runtime.powerState",
    "runtime.connectionState",
    "runtime.host",
    "guest.guestFullName",  # 도구 감지값 OS
    "guest.hostName",
    "guest.toolsStatus",
    "guest.toolsRunningStatus",
]
