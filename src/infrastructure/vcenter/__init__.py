"""vCenter 수집 어댑터 (계획 04).

**`src/infrastructure/hyperv`를 절대 import하지 않는다** (arch-check 특화 규칙 B).
공통 로직이 필요하면 `src/utils/` 또는 `src/domain/`에 두고 양쪽이 각각 참조한다.

**HTTP 클라이언트를 이 패키지에 들이지 않는다.** `httpx`·`requests`로 vCenter REST를
직접 호출하면 D-010을 우회하는 것이고, arch_check의 읽기 전용 검사(메서드명 기반)가
HTTP 동사를 잡지 못한다.
"""

from src.infrastructure.vcenter.reader import VCenterInventoryReader

__all__ = ["VCenterInventoryReader"]
