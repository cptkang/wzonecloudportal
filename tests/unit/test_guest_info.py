"""GuestInfo 3분법과 폴백 (계획 02 §5.1, ROADMAP §7.4).

"값 없음"과 "수집 불가"를 구분하는 설계가 살아 있는지 확인한다 (FR-501).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.domain.enums import GuestInfoAvailability, OsSource
from src.domain.values import GuestInfo, resolve_os_name, unavailable_reason


def _collected(observed_at: datetime) -> GuestInfo:
    return GuestInfo(
        availability=GuestInfoAvailability.AVAILABLE,
        os_name="Ubuntu 22.04 LTS",
        os_source=OsSource.GUEST_TOOLS,
        hostname="web-01.example.invalid",
        observed_at=observed_at,
    )


def test_is_collected_only_for_available() -> None:
    assert _collected(datetime.now(UTC)).is_collected
    for availability in (
        GuestInfoAvailability.TOOLS_NOT_INSTALLED,
        GuestInfoAvailability.TOOLS_NOT_RUNNING,
        GuestInfoAvailability.UNKNOWN,
    ):
        assert not GuestInfo(availability=availability).is_collected


def test_unavailable_reason_is_none_when_collected() -> None:
    assert unavailable_reason(GuestInfoAvailability.AVAILABLE) is None
    assert unavailable_reason(GuestInfoAvailability.TOOLS_NOT_INSTALLED) == "게스트 도구 미설치"
    assert unavailable_reason(GuestInfoAvailability.TOOLS_NOT_RUNNING) == "게스트 도구 미동작"


def test_with_fallback_keeps_previous_values_and_original_time() -> None:
    """도구가 멈춰도 이전 값과 **원래 관측 시각**이 유지되어야 한다."""
    earlier = datetime.now(UTC) - timedelta(days=3)
    previous = _collected(earlier)
    incoming = GuestInfo(availability=GuestInfoAvailability.TOOLS_NOT_RUNNING)

    merged = incoming.with_fallback(previous)

    assert merged.availability is GuestInfoAvailability.TOOLS_NOT_RUNNING
    assert merged.is_collected is False
    assert merged.os_name == "Ubuntu 22.04 LTS"
    assert merged.hostname == "web-01.example.invalid"
    # 현재 시각으로 갱신되면 "마지막 확인: 3일 전" 표시가 거짓말이 된다
    assert merged.observed_at == earlier


def test_with_fallback_does_not_overwrite_fresh_values() -> None:
    now = datetime.now(UTC)
    previous = GuestInfo(
        availability=GuestInfoAvailability.AVAILABLE,
        os_name="Old OS",
        observed_at=now - timedelta(days=10),
    )
    incoming = _collected(now)

    merged = incoming.with_fallback(previous)

    assert merged.os_name == "Ubuntu 22.04 LTS"
    assert merged.observed_at == now


def test_with_fallback_without_previous_returns_self() -> None:
    incoming = GuestInfo(availability=GuestInfoAvailability.TOOLS_NOT_INSTALLED)
    assert incoming.with_fallback(None) is incoming


def test_resolve_os_name_prefers_tools_value() -> None:
    assert resolve_os_name("Ubuntu 22.04", "Ubuntu Linux (64-bit)") == (
        "Ubuntu 22.04",
        OsSource.GUEST_TOOLS,
    )
    # 구성값만 있으면 출처를 표시해야 UI가 `(구성값)`을 병기한다 (FR-304)
    assert resolve_os_name(None, "Ubuntu Linux (64-bit)") == (
        "Ubuntu Linux (64-bit)",
        OsSource.VM_CONFIG,
    )
    assert resolve_os_name(None, None) == (None, None)
