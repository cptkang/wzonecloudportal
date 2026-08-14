"""Hyper-V 수집 어댑터 (계획 05, D-012).

한 패키지에 수집 경로가 둘 있다 — WinRM 세션·실행기·예외 변환을 공유한다 (§3):
- 경로 A: `HyperVHostInventoryReader` (hyperv-host · hyperv-cluster)
- 경로 B: `ScvmmInventoryReader` (scvmm — 주 경로)

`src.infrastructure.vcenter`를 import하지 않는다 (arch-check 특화 규칙 2).
"""

from src.infrastructure.hyperv.host_reader import HyperVHostInventoryReader
from src.infrastructure.hyperv.scvmm_reader import ScvmmInventoryReader

__all__ = ["HyperVHostInventoryReader", "ScvmmInventoryReader"]
