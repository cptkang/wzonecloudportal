"""VM 목록 응답 스키마 변환 (계획 08 §6.3).

`VmSummary`(도메인 읽기 모델) → `VmSummaryResponse`(API 계약) 변환에서
**값이 조용히 사라지지 않는지** 확인한다. 실제로 `configured_os`가 이 경계에서
누락되어 도구 미설치 VM의 OS가 화면에서 사라진 적이 있다 (2026-08-19).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from src.api.schemas.inventory import VmSummaryResponse
from src.domain.enums import (
    GuestInfoAvailability,
    HypervisorKind,
    OsSource,
    PowerState,
    ResourceLifecycle,
)
from src.domain.query import VmSummary


def _summary(
    *,
    availability: GuestInfoAvailability,
    guest_os_name: str | None,
    configured_os: str | None,
) -> VmSummary:
    return VmSummary(
        resource_id=uuid4(),
        name="vm-1",
        native_id="instance-1",
        connection_id=uuid4(),
        connection_name="vCenter A",
        hypervisor=HypervisorKind.VCENTER,
        power_state=PowerState.ON,
        vcpu_count=2,
        memory_mb=4096,
        configured_os=configured_os,
        guest_availability=availability,
        guest_os_name=guest_os_name,
        guest_os_source=OsSource.GUEST_TOOLS if guest_os_name else None,
        guest_hostname=None,
        guest_observed_at=None,
        host_native_id="host-1",
        lifecycle=ResourceLifecycle.ACTIVE,
        last_seen_at=datetime.now(UTC),
    )


def test_configured_os_survives_the_response_boundary() -> None:
    """도구 미설치 VM에서 구성값 OS가 응답에 남는다 (ROADMAP §5.3)."""
    res = VmSummaryResponse.from_summary(
        _summary(
            availability=GuestInfoAvailability.TOOLS_NOT_INSTALLED,
            guest_os_name=None,
            configured_os="Microsoft Windows Server 2012 (64-bit)",
        )
    )

    assert res.configured_os == "Microsoft Windows Server 2012 (64-bit)"
    # 구성값이 있다고 게스트 정보가 수집된 것은 아니다 — 두 개념을 섞지 않는다
    assert res.guest.is_collected is False
    assert res.guest.unavailable_reason == "게스트 도구 미설치"
    assert res.guest.os_name is None


def test_collected_guest_keeps_both_values() -> None:
    """도구가 동작해도 구성값은 별개로 유지된다 — 서로를 덮어쓰지 않는다."""
    res = VmSummaryResponse.from_summary(
        _summary(
            availability=GuestInfoAvailability.AVAILABLE,
            guest_os_name="Ubuntu 22.04 LTS",
            configured_os="Ubuntu Linux (64-bit)",
        )
    )

    assert res.guest.is_collected is True
    assert res.guest.os_name == "Ubuntu 22.04 LTS"
    assert res.configured_os == "Ubuntu Linux (64-bit)"
    assert res.guest.unavailable_reason is None


def test_configured_os_may_be_absent() -> None:
    """Hyper-V 경로는 구성값 OS가 없다 (`host_mapper.py` §8.2). None이 정상이다."""
    res = VmSummaryResponse.from_summary(
        _summary(
            availability=GuestInfoAvailability.TOOLS_NOT_INSTALLED,
            guest_os_name=None,
            configured_os=None,
        )
    )

    assert res.configured_os is None
    assert res.guest.unavailable_reason == "게스트 도구 미설치"
