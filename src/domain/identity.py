"""CI 식별 키 생성 (계획 02 §7, FR-302).

Step 1은 **1순위만 생성**하지만 함수 형태와 저장 구조는 최종형을 쓴다.
`ON CONFLICT` 기반으로 만들면 2·3순위를 추가할 때 upsert를 전면 재작성해야 한다
(ROADMAP §7.3, D-011).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from src.domain.resource import ResourceBase, VirtualMachine


class IdentityRule(IntEnum):
    #: connection_id + native_id — 가장 신뢰
    NATIVE = 1
    #: BIOS UUID — Step 5(교차 식별)에서 사용
    BIOS_UUID = 2
    #: MAC들 + 이름 — Step 4(어댑터 수집)에서 사용
    MAC_AND_NAME = 3


@dataclass(frozen=True, slots=True)
class IdentityKey:
    rule: IdentityRule
    value: str


def build_vm_identity_keys(vm: VirtualMachine) -> list[IdentityKey]:
    """우선순위 순으로 식별 키를 생성한다.

    Step 1은 1순위만 반환한다. 2순위(bios_uuid)는 Step 5,
    3순위(MAC+이름)는 Step 4에서 이 함수에 행을 더한다 —
    호출부(`upsert`)는 바뀌지 않는다.
    """
    return [IdentityKey(IdentityRule.NATIVE, f"{vm.connection_id}:{vm.native_id}")]


def build_generic_identity_keys(res: ResourceBase) -> list[IdentityKey]:
    """VM 외 자원은 1순위만 사용한다. 대체 식별자가 마땅치 않다."""
    return [IdentityKey(IdentityRule.NATIVE, f"{res.connection_id}:{res.native_id}")]
