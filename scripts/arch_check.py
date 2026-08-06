#!/usr/bin/env python3
"""Clean Architecture 계층 간 의존성 규칙 위반 자동 탐지 스크립트.

프로젝트의 계층 구조를 정의하고, 각 Python 파일의 import 문을 분석하여
허용되지 않는 의존성 방향을 위반 목록으로 출력한다.

이 프로젝트(클라우드 포탈)는 일반 계층 규칙에 더해 두 가지 특화 규칙을 적용한다.
  1. 유스케이스(application)가 특정 하이퍼바이저 어댑터를 직접 import 금지
     → domain의 HypervisorConnector Protocol을 통해서만 접근
  2. 하이퍼바이저 어댑터 간 교차 참조 금지 (vcenter ↔ hyperv)

사용법:
    python scripts/arch_check.py              # 전체 검사
    python scripts/arch_check.py --verbose    # 상세 출력 (의존성 매트릭스 포함)
    python scripts/arch_check.py --json       # JSON 형식 출력
    python scripts/arch_check.py --ci         # CI용: 위반 시 exit 1
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# ──────────────────────────────────────────────
# 1. 계층 정의
# ──────────────────────────────────────────────

Layer = Literal[
    "domain",        # 핵심 도메인 (VM/Host/Cluster 엔티티, 커넥터 Protocol)
    "config",        # 설정 (모든 계층이 참조 가능)
    "utils",         # 공유 유틸리티 (모든 계층이 참조 가능)
    "infrastructure",# 하이퍼바이저 어댑터, DB, 캐시, 보안, 외부 연동
    "application",   # 유스케이스 (프로비저닝, 전원 제어, 인벤토리 조회)
    "orchestration", # 워커/스케줄러 (인벤토리 동기화, 비동기 작업 실행)
    "interface",     # FastAPI 어댑터
    "entry",         # 진입점 (main.py)
]

# 모듈 경로 → 계층 매핑
MODULE_LAYER_MAP: dict[str, Layer] = {
    # domain — 엔티티, 값 객체, 포트(Protocol)
    "src.domain":                        "domain",
    "src.domain.resource":               "domain",   # VM, Host, Cluster, Datastore, Network
    "src.domain.tenant":                 "domain",   # 테넌트, 프로젝트, 할당량
    "src.domain.auth":                   "domain",   # 사용자, 역할, 권한
    "src.domain.task":                   "domain",   # 비동기 작업(Task) 상태 모델
    "src.domain.audit":                  "domain",   # 감사 이벤트
    "src.domain.ports":                  "domain",   # HypervisorConnector 등 Protocol 정의
    # config / utils
    "src.config":                        "config",
    "src.utils":                         "utils",
    # infrastructure — 하이퍼바이저 어댑터 및 외부 시스템 연동
    "src.infrastructure":                "infrastructure",
    "src.infrastructure.vcenter":        "infrastructure",   # pyVmomi 기반 vCenter 어댑터
    "src.infrastructure.hyperv":         "infrastructure",   # WinRM/PowerShell 기반 Hyper-V 어댑터
    "src.infrastructure.db":             "infrastructure",
    "src.infrastructure.cache":          "infrastructure",
    "src.infrastructure.security":       "infrastructure",   # 자격증명 암호화, 토큰
    "src.infrastructure.repository":     "infrastructure",
    # application — 유스케이스 서비스
    "src.application":                   "application",
    # orchestration — 워커/스케줄러
    "src.orchestration":                 "orchestration",
    # interface / entry
    "src.api":                           "interface",
    "src.main":                          "entry",
}

# 하이퍼바이저 어댑터 모듈 — 특화 규칙 대상
HYPERVISOR_ADAPTERS: tuple[str, ...] = (
    "src.infrastructure.vcenter",
    "src.infrastructure.hyperv",
)

# ──────────────────────────────────────────────
# 2. 허용 의존성 규칙
#    key 계층이 value 집합의 계층을 import할 수 있음
# ──────────────────────────────────────────────

ALLOWED_DEPS: dict[Layer, set[Layer]] = {
    "domain":         set(),                                                       # 어디에도 의존하지 않음
    "config":         set(),                                                       # 외부 패키지만
    "utils":          set(),                                                       # 외부 패키지만
    "infrastructure": {"domain", "config", "utils", "infrastructure"},             # 같은 레벨 + 하위
    "application":    {"domain", "config", "utils", "infrastructure"},             # 인프라까지 참조 가능
    "orchestration":  {"domain", "config", "utils", "application", "infrastructure"},
    "interface":      {"domain", "config", "utils", "orchestration", "application", "infrastructure"},
    "entry":          {"domain", "config", "utils", "orchestration", "interface", "infrastructure"},
}


# ──────────────────────────────────────────────
# 3. 파일 → 계층 결정
# ──────────────────────────────────────────────

def resolve_layer(module_path: str) -> Layer | None:
    """모듈 경로에서 계층을 결정한다. 가장 긴 prefix 매칭."""
    best: Layer | None = None
    best_len = 0
    for prefix, layer in MODULE_LAYER_MAP.items():
        if module_path == prefix or module_path.startswith(prefix + "."):
            if len(prefix) > best_len:
                best = layer
                best_len = len(prefix)
    return best


def resolve_adapter(module_path: str) -> str | None:
    """모듈 경로가 하이퍼바이저 어댑터에 속하면 그 어댑터 prefix를 반환한다."""
    for prefix in HYPERVISOR_ADAPTERS:
        if module_path == prefix or module_path.startswith(prefix + "."):
            return prefix
    return None


def file_to_module(file_path: Path, project_root: Path) -> str:
    """파일 경로를 모듈 경로로 변환한다."""
    rel = file_path.relative_to(project_root)
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


# ──────────────────────────────────────────────
# 4. Import 추출 (AST 기반)
# ──────────────────────────────────────────────

@dataclass
class ImportInfo:
    module: str
    line: int
    statement: str


def extract_imports(file_path: Path) -> list[ImportInfo]:
    """파일에서 src.* import 문을 추출한다."""
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError):
        return []

    imports: list[ImportInfo] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("src."):
                    imports.append(ImportInfo(
                        module=alias.name,
                        line=node.lineno,
                        statement=f"import {alias.name}",
                    ))
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("src."):
                imports.append(ImportInfo(
                    module=node.module,
                    line=node.lineno,
                    statement=f"from {node.module} import ...",
                ))
    return imports


# ──────────────────────────────────────────────
# 5. 위반 검사
# ──────────────────────────────────────────────

@dataclass
class Violation:
    file: str
    line: int
    from_layer: Layer
    to_layer: Layer
    from_module: str
    to_module: str
    statement: str
    severity: Literal["error", "warning"]
    reason: str


@dataclass
class CheckResult:
    violations: list[Violation] = field(default_factory=list)
    checked_files: int = 0
    total_imports: int = 0
    allowed_imports: int = 0


def check_file(file_path: Path, project_root: Path) -> list[Violation]:
    """단일 파일의 의존성 규칙 위반을 검사한다."""
    from_module = file_to_module(file_path, project_root)
    from_layer = resolve_layer(from_module)
    if from_layer is None:
        return []

    from_adapter = resolve_adapter(from_module)
    rel_path = str(file_path.relative_to(project_root))

    violations: list[Violation] = []
    for imp in extract_imports(file_path):
        to_layer = resolve_layer(imp.module)
        if to_layer is None:
            continue

        to_adapter = resolve_adapter(imp.module)

        # 특화 규칙 1: 유스케이스가 특정 하이퍼바이저 어댑터를 직접 참조 금지
        if from_layer in ("application", "orchestration") and to_adapter is not None:
            violations.append(Violation(
                file=rel_path,
                line=imp.line,
                from_layer=from_layer,
                to_layer=to_layer,
                from_module=from_module,
                to_module=imp.module,
                statement=imp.statement,
                severity="error",
                reason=(
                    f"{from_layer}이 하이퍼바이저 어댑터({to_adapter})에 직접 결합. "
                    "src.domain.ports의 HypervisorConnector Protocol을 통해 주입받을 것"
                ),
            ))
            continue

        # 특화 규칙 2: 하이퍼바이저 어댑터 간 교차 참조 금지
        if from_adapter is not None and to_adapter is not None and from_adapter != to_adapter:
            violations.append(Violation(
                file=rel_path,
                line=imp.line,
                from_layer=from_layer,
                to_layer=to_layer,
                from_module=from_module,
                to_module=imp.module,
                statement=imp.statement,
                severity="error",
                reason=(
                    f"하이퍼바이저 어댑터 교차 참조 ({from_adapter} → {to_adapter}). "
                    "공통 로직은 src.domain 또는 src.utils로 추출할 것"
                ),
            ))
            continue

        allowed = ALLOWED_DEPS.get(from_layer, set())
        if to_layer not in allowed and to_layer != from_layer:
            violations.append(Violation(
                file=rel_path,
                line=imp.line,
                from_layer=from_layer,
                to_layer=to_layer,
                from_module=from_module,
                to_module=imp.module,
                statement=imp.statement,
                severity="error",
                reason=f"{from_layer} → {to_layer} 의존은 Clean Architecture에서 금지",
            ))

    return violations


def check_project(project_root: Path) -> CheckResult:
    """프로젝트 전체의 의존성 규칙을 검사한다."""
    src_dir = project_root / "src"
    result = CheckResult()

    if not src_dir.is_dir():
        return result

    for py_file in sorted(src_dir.rglob("*.py")):
        if py_file.name == "__init__.py":
            # __init__.py는 re-export 목적이므로 같은 패키지 내 참조 허용
            continue
        result.checked_files += 1
        imports = extract_imports(py_file)
        result.total_imports += len(imports)
        file_violations = check_file(py_file, project_root)
        result.violations.extend(file_violations)
        result.allowed_imports += len(imports) - len(file_violations)

    return result


def collect_layer_deps(project_root: Path) -> dict[tuple[Layer, Layer], int]:
    """계층 간 실제 의존 횟수를 집계한다 (매트릭스 출력용)."""
    src_dir = project_root / "src"
    actual_deps: dict[tuple[Layer, Layer], int] = {}
    if not src_dir.is_dir():
        return actual_deps

    for py_file in sorted(src_dir.rglob("*.py")):
        from_layer = resolve_layer(file_to_module(py_file, project_root))
        if from_layer is None:
            continue
        for imp in extract_imports(py_file):
            to_layer = resolve_layer(imp.module)
            if to_layer is None or to_layer == from_layer:
                continue
            key = (from_layer, to_layer)
            actual_deps[key] = actual_deps.get(key, 0) + 1
    return actual_deps


# ──────────────────────────────────────────────
# 6. 출력
# ──────────────────────────────────────────────

COLORS = {
    "red": "\033[91m",
    "yellow": "\033[93m",
    "green": "\033[92m",
    "cyan": "\033[96m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}

LAYERS_ORDER: list[Layer] = [
    "domain", "config", "utils", "infrastructure",
    "application", "orchestration", "interface", "entry",
]


def print_text_report(result: CheckResult, project_root: Path, verbose: bool = False) -> None:
    """텍스트 형식으로 결과를 출력한다."""
    c = COLORS

    print(f"\n{c['bold']}=== Clean Architecture 의존성 규칙 검사 ==={c['reset']}\n")

    if not (project_root / "src").is_dir():
        print(f"  {c['yellow']}src/ 디렉토리가 없습니다. 구현 시작 전이면 정상입니다.{c['reset']}\n")
        return

    print(f"  검사 파일: {result.checked_files}개")
    print(f"  총 import: {result.total_imports}개")
    print(f"  허용 import: {result.allowed_imports}개")

    errors = [v for v in result.violations if v.severity == "error"]
    warnings = [v for v in result.violations if v.severity == "warning"]

    print(f"  {c['red']}위반 (error): {len(errors)}개{c['reset']}")
    print(f"  {c['yellow']}경고 (warning): {len(warnings)}개{c['reset']}")
    print()

    for title, group, color, tag in (
        ("--- ERRORS ---", errors, "red", "[ERROR]"),
        ("--- WARNINGS ---", warnings, "yellow", "[WARN]"),
    ):
        if not group:
            continue
        print(f"{c[color]}{c['bold']}{title}{c['reset']}\n")
        for v in group:
            print(f"  {c[color]}{tag}{c['reset']} {v.file}:{v.line}")
            print(f"    {v.statement}")
            print(f"    {c['cyan']}{v.from_layer}{c['reset']} -> {c['cyan']}{v.to_layer}{c['reset']}: {v.reason}")
            print()

    if not errors and not warnings:
        print(f"  {c['green']}모든 의존성이 Clean Architecture 규칙을 준수합니다.{c['reset']}\n")

    # 계층 의존성 요약 매트릭스
    if verbose:
        print(f"{c['bold']}--- 계층 의존성 매트릭스 ---{c['reset']}\n")
        actual_deps = collect_layer_deps(project_root)

        header = f"{'From \\ To':<16}" + "".join(f"{l:<16}" for l in LAYERS_ORDER)
        print(f"  {header}")
        print(f"  {'─' * (16 + 16 * len(LAYERS_ORDER))}")
        for from_l in LAYERS_ORDER:
            row = f"  {from_l:<16}"
            for to_l in LAYERS_ORDER:
                count = actual_deps.get((from_l, to_l), 0)
                if count == 0:
                    row += f"{'·':<16}"
                elif to_l in ALLOWED_DEPS.get(from_l, set()):
                    row += f"{c['green']}{count:<16}{c['reset']}"
                else:
                    row += f"{c['red']}{count:<16}{c['reset']}"
            print(row)
        print()


def print_json_report(result: CheckResult) -> None:
    """JSON 형식으로 결과를 출력한다."""
    output = {
        "summary": {
            "checked_files": result.checked_files,
            "total_imports": result.total_imports,
            "allowed_imports": result.allowed_imports,
            "errors": len([v for v in result.violations if v.severity == "error"]),
            "warnings": len([v for v in result.violations if v.severity == "warning"]),
        },
        "violations": [
            {
                "file": v.file,
                "line": v.line,
                "severity": v.severity,
                "from_layer": v.from_layer,
                "to_layer": v.to_layer,
                "from_module": v.from_module,
                "to_module": v.to_module,
                "statement": v.statement,
                "reason": v.reason,
            }
            for v in result.violations
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


# ──────────────────────────────────────────────
# 7. CLI
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean Architecture 계층 의존성 규칙 위반 탐지",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="상세 출력")
    parser.add_argument("--json", action="store_true", help="JSON 출력")
    parser.add_argument("--ci", action="store_true", help="CI 모드 (error 위반 시 exit 1)")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="프로젝트 루트 디렉토리",
    )
    args = parser.parse_args()

    project_root: Path = args.project_root.resolve()
    result = check_project(project_root)

    if args.json:
        print_json_report(result)
    else:
        print_text_report(result, project_root, verbose=args.verbose)

    if args.ci:
        errors = [v for v in result.violations if v.severity == "error"]
        sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
