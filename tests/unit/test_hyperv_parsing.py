"""runner.parse_ps_json과 normalize 단위 테스트 (계획 05 §5.1·§8.1, §12-3·4)."""

from __future__ import annotations

import pytest

from src.domain.enums import PowerState
from src.domain.exceptions import CollectionError
from src.infrastructure.hyperv.normalize import (
    map_power_state,
    normalize_guid,
    split_kvp_addresses,
    to_mb,
)
from src.infrastructure.hyperv.runner import parse_ps_json


class TestParsePsJson:
    def test_single_object_is_normalized_to_list(self) -> None:
        """PowerShell은 항목이 1개면 배열이 아닌 객체를 반환한다 (§5.1)."""
        assert parse_ps_json('{"Name": "vm1"}') == [{"Name": "vm1"}]

    def test_array_passes_through(self) -> None:
        assert parse_ps_json('[{"a": 1}, {"b": 2}]') == [{"a": 1}, {"b": 2}]

    def test_empty_output_is_empty_list(self) -> None:
        assert parse_ps_json("") == []
        assert parse_ps_json("   \n  ") == []

    def test_broken_json_raises_collection_error(self) -> None:
        with pytest.raises(CollectionError):
            parse_ps_json("{not json")

    def test_scalar_output_is_ignored(self) -> None:
        assert parse_ps_json('"just a string"') == []

    def test_non_dict_items_are_dropped(self) -> None:
        assert parse_ps_json('[{"a": 1}, "noise", 3]') == [{"a": 1}]


class TestPowerState:
    """경로 A는 정수(EnabledState 계열), 경로 B는 열거형 이름 — 둘 다 로케일 무관 (§8.1)."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (2, PowerState.ON),
            (3, PowerState.OFF),
            (6, PowerState.SUSPENDED),
            (9, PowerState.SUSPENDED),
            ("Running", PowerState.ON),
            ("PowerOff", PowerState.OFF),
            ("Stopped", PowerState.OFF),
            ("Paused", PowerState.SUSPENDED),
            ("Saved", PowerState.SUSPENDED),
        ],
    )
    def test_known_states(self, raw: int | str, expected: PowerState) -> None:
        assert map_power_state(raw) is expected

    @pytest.mark.parametrize("raw", [None, 10, 32773, "Starting", "UnderMigration", True])
    def test_unknown_states_map_to_unknown(self, raw: object) -> None:
        """전이 상태·미지의 값은 추정하지 않는다 — UNKNOWN + 로그 (§8.1 [검증 필요])."""
        assert map_power_state(raw) is PowerState.UNKNOWN  # type: ignore[arg-type]


class TestNormalizeGuid:
    def test_wmi_braced_uppercase_matches_vcenter_format(self) -> None:
        """WMI BIOSGUID({대문자})를 vCenter config.uuid 표기로 맞춘다 — 교차 식별 (§8.4)."""
        assert (
            normalize_guid("{4C4C4544-004D-3510-8054-B4C04F435831}")
            == "4c4c4544-004d-3510-8054-b4c04f435831"
        )

    def test_plain_guid_is_lowered(self) -> None:
        assert (
            normalize_guid("4C4C4544-004D-3510-8054-B4C04F435831")
            == "4c4c4544-004d-3510-8054-b4c04f435831"
        )

    @pytest.mark.parametrize("raw", [None, "", "not-a-guid", "12345"])
    def test_invalid_returns_none(self, raw: str | None) -> None:
        assert normalize_guid(raw) is None


class TestUnits:
    def test_bytes_to_mb(self) -> None:
        assert to_mb(4 * 1024 * 1024 * 1024) == 4096
        assert to_mb(None) is None

    def test_kvp_semicolon_and_comma_split(self) -> None:
        """KVP NetworkAddressIPv4는 세미콜론 구분 문자열로 온다 (§8.2)."""
        assert split_kvp_addresses("10.0.0.1;10.0.0.2, 10.0.0.3") == [
            "10.0.0.1",
            "10.0.0.2",
            "10.0.0.3",
        ]
        assert split_kvp_addresses(None) == []
        assert split_kvp_addresses("  ") == []
