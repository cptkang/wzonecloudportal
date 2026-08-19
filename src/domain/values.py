"""값 객체 (계획 02 §5). 모두 불변(`frozen=True`)이다."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from src.domain.enums import Firmware, GuestInfoAvailability, OsSource


@dataclass(frozen=True, slots=True)
class GuestInfo:
    """게스트 OS가 보고하는 정보.

    VMware Tools / Hyper-V 통합 서비스가 동작할 때만 수집된다.
    도구가 없으면 OS·IP·FQDN이 **함께** 없어지므로 묶어서 상태를 부여한다.

    Step 1은 os_name/hostname까지만 쓰고, IP 목록은 Step 4에서 채운다.
    필드는 지금 두되 값이 비어 있는 것이 정상이다.
    """

    availability: GuestInfoAvailability
    os_name: str | None = None
    os_version: str | None = None
    os_source: OsSource | None = None
    hostname: str | None = None
    ipv4_addresses: tuple[str, ...] = ()
    ipv6_addresses: tuple[str, ...] = ()
    tool_version: str | None = None
    #: 이 값이 실제로 수집된 시각. 폴백 시에도 원래 시각을 유지한다 (ROADMAP §7.4).
    observed_at: datetime | None = None

    @property
    def is_collected(self) -> bool:
        return self.availability is GuestInfoAvailability.AVAILABLE

    @property
    def primary_ipv4(self) -> str | None:
        return self.ipv4_addresses[0] if self.ipv4_addresses else None

    def with_fallback(self, previous: GuestInfo | None) -> GuestInfo:
        """수집 불가 시 이전 값을 유지한 새 인스턴스를 반환한다.

        도구가 멈춘 것이지 VM의 OS·IP가 없어진 것이 아니다.
        이 폴백이 없으면 재수집 한 번으로 이전 값이 NULL로 덮어써지고 복구할 수 없다
        (ROADMAP §7.4).
        """
        if self.is_collected or previous is None:
            return self
        return replace(
            self,
            os_name=previous.os_name,
            os_version=previous.os_version,
            os_source=previous.os_source,
            hostname=previous.hostname,
            ipv4_addresses=previous.ipv4_addresses,
            ipv6_addresses=previous.ipv6_addresses,
            tool_version=previous.tool_version,
            observed_at=previous.observed_at,  # 원래 관측 시각 유지
        )


#: UI(계획 11)·내보내기(계획 13)가 같은 문구를 쓰도록 도메인에 둔다.
#: 서버가 이 문구를 응답에 실어 보내므로 화면마다 표현이 갈리지 않는다 (ROADMAP §9.2).
UNAVAILABLE_REASONS: dict[GuestInfoAvailability, str] = {
    GuestInfoAvailability.TOOLS_NOT_INSTALLED: "게스트 도구 미설치",
    GuestInfoAvailability.TOOLS_NOT_RUNNING: "게스트 도구 미동작",
    GuestInfoAvailability.UNKNOWN: "확인 필요",
}


def unavailable_reason(availability: GuestInfoAvailability) -> str | None:
    """수집 불가 사유 문구. 수집된 상태면 None."""
    return UNAVAILABLE_REASONS.get(availability)


@dataclass(frozen=True, slots=True)
class CpuSpec:
    total_vcpu: int
    socket_count: int | None = None
    cores_per_socket: int | None = None


@dataclass(frozen=True, slots=True)
class MemorySpec:
    assigned_mb: int


@dataclass(frozen=True, slots=True)
class PlatformSpec:
    hardware_version: str | None = None
    firmware: Firmware = Firmware.UNKNOWN
    #: VM 구성값 OS — 게스트 도구 없이도 조회 가능하다
    configured_os: str | None = None


def resolve_os_name(
    tools_value: str | None, config_value: str | None
) -> tuple[str | None, OsSource | None]:
    """게스트 OS 이름과 그 출처를 결정한다 (FR-304, 계획 02 §8).

    도구 감지값이 권위 있고, 없으면 구성값을 쓰되 출처를 표시한다.
    """
    if tools_value:
        return tools_value, OsSource.GUEST_TOOLS
    if config_value:
        return config_value, OsSource.VM_CONFIG
    return None, None
