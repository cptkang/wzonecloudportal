"""CI 식별 키(계획 02 §7)와 페이징 검증(계획 07 §2)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.domain.enums import PowerState
from src.domain.exceptions import ValidationError
from src.domain.identity import IdentityRule, build_vm_identity_keys
from src.domain.query import Page
from tests.fakes.fake_reader import make_vm


def test_step1_generates_only_native_rule() -> None:
    """Step 1은 1순위만 만든다. 2·3순위는 Step 4·5에서 이 함수에 행을 더한다."""
    connection_id = uuid4()
    vm = make_vm(connection_id=connection_id, native_id="vm-instance-1", bios_uuid="4213abcd")

    keys = build_vm_identity_keys(vm)

    assert [k.rule for k in keys] == [IdentityRule.NATIVE]
    assert keys[0].value == f"{connection_id}:vm-instance-1"


def test_identity_key_includes_connection_id() -> None:
    """다른 연결의 같은 native_id가 같은 자원으로 병합되면 안 된다."""
    a = build_vm_identity_keys(make_vm(connection_id=uuid4(), native_id="same"))
    b = build_vm_identity_keys(make_vm(connection_id=uuid4(), native_id="same"))
    assert a[0].value != b[0].value


def test_page_rejects_unknown_sort_column() -> None:
    """허용 목록 밖의 정렬 컬럼은 인젝션 경로가 된다."""
    with pytest.raises(ValidationError) as ei:
        Page(sort_by="name; DROP TABLE users").validate()
    assert ei.value.field == "sort_by"


def test_page_limit_bounds() -> None:
    Page(limit=1).validate()
    Page(limit=Page.MAX_LIMIT).validate()
    with pytest.raises(ValidationError):
        Page(limit=0).validate()
    with pytest.raises(ValidationError):
        Page(limit=Page.MAX_LIMIT + 1).validate()
    with pytest.raises(ValidationError):
        Page(offset=-1).validate()


def test_make_vm_defaults_are_sane() -> None:
    vm = make_vm(connection_id=uuid4())
    assert vm.power_state is PowerState.ON
    assert vm.cpu.total_vcpu > 0
    assert vm.memory.assigned_mb > 0
    assert vm.guest.is_collected
