"""도메인 열거형 (계획 02 §3).

Step 1에 필요한 값만 정의한다. 나머지(Datastore/Network 종류 등)는 Step 4~6에서 추가한다.
"""

from __future__ import annotations

from enum import StrEnum


class HypervisorKind(StrEnum):
    VCENTER = "vcenter"
    HYPERV = "hyperv"


class ConnectionKind(StrEnum):
    """연결 단위.

    Microsoft 환경은 관리 콘솔이 두 계열이라 수집 경로가 갈린다 (D-012, 계획 05 §2).
    HYPERV_HOST/HYPERV_CLUSTER는 경로 A(Hyper-V 관리자, 호스트/클러스터 = 연결 1개),
    SCVMM은 경로 B(SCVMM 서버 = 연결 1개, fabric 전체)다.

    **여기에 값을 추가하면 리더 팩토리(`api/deps.py`)에 경로도 함께 구현해야 한다.**
    미구현 분기를 남기지 않는다 (CLAUDE.md Known Mistakes 5번, D-012).
    """

    VCENTER = "vcenter"
    HYPERV_HOST = "hyperv-host"
    HYPERV_CLUSTER = "hyperv-cluster"
    SCVMM = "scvmm"

    @property
    def hypervisor(self) -> HypervisorKind:
        if self is ConnectionKind.VCENTER:
            return HypervisorKind.VCENTER
        return HypervisorKind.HYPERV

    @property
    def uses_winrm(self) -> bool:
        """WinRM 기반 연결 여부. 인증 방식(auth_method)이 필수인 연결이다 (계획 05 §4)."""
        return self is not ConnectionKind.VCENTER


class WinRmAuth(StrEnum):
    """WinRM 인증 방식 (계획 05 §4.1). 환경에 따라 다르며 잘못 고르면 접속이 실패한다.

    CredSSP는 자격증명이 대상 서버로 위임되므로 UI에서 경고를 표시한다.
    """

    NTLM = "ntlm"
    KERBEROS = "kerberos"
    CREDSSP = "credssp"


class ConnectionStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    #: 인증 실패 — 자동 재시도를 중단한다 (FR-114, CST-05)
    CREDENTIAL_ERROR = "credential_error"
    PERMISSION_ERROR = "permission_error"
    UNREACHABLE = "unreachable"


class ResourceType(StrEnum):
    VIRTUAL_MACHINE = "virtual_machine"
    HOST = "host"
    CLUSTER = "cluster"
    DATASTORE = "datastore"
    NETWORK = "network"
    SNAPSHOT = "snapshot"


class PowerState(StrEnum):
    """vCenter/Hyper-V 전원 상태를 4값으로 통일한다.

    vCenter: poweredOn / poweredOff / suspended
    Hyper-V: Running / Off / Saved / Paused  (Saved·Paused 모두 SUSPENDED)
    """

    ON = "on"
    OFF = "off"
    SUSPENDED = "suspended"
    UNKNOWN = "unknown"


class ConnectionState(StrEnum):
    """자원 자체의 연결 상태 (하이퍼바이저가 자원을 인지하는 상태)."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    INACCESSIBLE = "inaccessible"
    ORPHANED = "orphaned"
    UNKNOWN = "unknown"


class GuestInfoAvailability(StrEnum):
    """게스트 정보 수집 가능 여부 (FR-501).

    VMware Tools / Hyper-V 통합 서비스가 동작해야 게스트 OS·IP·FQDN을 얻을 수 있다.
    "값 없음"과 "수집 불가"를 구분하기 위한 핵심 열거형이다.
    """

    AVAILABLE = "available"
    TOOLS_NOT_INSTALLED = "tools_not_installed"
    TOOLS_NOT_RUNNING = "tools_not_running"
    UNKNOWN = "unknown"


class OsSource(StrEnum):
    """게스트 OS 값의 출처 (FR-304)."""

    #: 게스트 도구가 감지한 값 — 권위 있음
    GUEST_TOOLS = "guest_tools"
    #: VM 구성값 — 실제와 다를 수 있으므로 UI에서 병기한다
    VM_CONFIG = "vm_config"


class Firmware(StrEnum):
    BIOS = "bios"
    UEFI = "uefi"
    UNKNOWN = "unknown"


class ResourceLifecycle(StrEnum):
    ACTIVE = "active"
    #: 수집에서 사라짐 — 유예 중 (FR-307, Step 3)
    MISSING = "missing"
    RETIRED = "retired"
    #: 연결 삭제됨 (FR-109, Step 3)
    DISCONNECTED = "disconnected"


class CheckStage(StrEnum):
    """연결 테스트 단계 (FR-106). 조치가 단계마다 다르므로 개별 보고한다."""

    REACHABLE = "reachable"
    TLS_VALID = "tls_valid"
    AUTHENTICATED = "authenticated"
    AUTHORIZED = "authorized"
