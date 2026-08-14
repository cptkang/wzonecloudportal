"""IP·MAC 정규화 (계획 02 §9.3).

어댑터 양쪽이 쓰므로 utils에 둔다. **어댑터끼리 참조하면 arch-check 위반**이다.
Step 1은 IP를 수집하지 않지만, 게스트 매퍼가 값 유무를 판정할 때 이미 필요하다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from ipaddress import ip_address


def normalize_mac(raw: str | None) -> str | None:
    """MAC 주소를 소문자 콜론 구분 형식으로 정규화한다.

    ``00-15-5D-01-02-03`` / ``00155D010203`` / ``00:15:5d:01:02:03``
    → ``00:15:5d:01:02:03``
    """
    if not raw:
        return None
    hex_only = re.sub(r"[^0-9a-fA-F]", "", raw)
    if len(hex_only) != 12:
        return None
    return ":".join(hex_only[i : i + 2] for i in range(0, 12, 2)).lower()


def normalize_ip(raw: str | None) -> str | None:
    """IP 주소를 정규화한다. 유효하지 않으면 None."""
    if not raw:
        return None
    try:
        return str(ip_address(raw.strip()))
    except ValueError:
        return None


def is_reportable_ip(addr: str) -> bool:
    """인벤토리에 표시할 가치가 있는 IP인지 판정한다.

    링크로컬(169.254.x, fe80::)·루프백은 제외한다.
    게스트 도구가 이런 주소까지 보고하므로 필터링하지 않으면 목록과 검색이 오염된다.
    """
    try:
        ip = ip_address(addr)
    except ValueError:
        return False
    return not (ip.is_loopback or ip.is_link_local or ip.is_unspecified)


def split_ip_families(addrs: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """IPv4/IPv6로 분리하고 정규화·필터링한다. 중복은 순서를 유지한 채 제거한다."""
    v4: list[str] = []
    v6: list[str] = []
    for raw in addrs:
        norm = normalize_ip(raw)
        if norm is None or not is_reportable_ip(norm):
            continue
        (v4 if ip_address(norm).version == 4 else v6).append(norm)
    return tuple(dict.fromkeys(v4)), tuple(dict.fromkeys(v6))
