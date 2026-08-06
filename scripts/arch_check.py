#!/usr/bin/env python3
"""아키텍처 규칙 위반 자동 탐지 스크립트.

두 종류의 규칙을 검사한다.

1. 계층 의존성 (Clean Architecture)
   각 Python 파일의 import 문을 분석하여 허용되지 않는 의존성 방향을 탐지한다.
   - 프로젝트 특화 규칙 A: 유스케이스가 특정 하이퍼바이저 어댑터를 직접 import 금지
   - 프로젝트 특화 규칙 B: 하이퍼바이저 어댑터 간 교차 참조 금지

2. 읽기 전용 범위 (spec.md CST-01 / NFR-202)
   이 포탈은 하이퍼바이저에 대해 어떠한 쓰기·제어 API도 호출하지 않는다.
   커넥터 Protocol과 하이퍼바이저 어댑터에 자원을 변경하는 메서드가 정의되면 위반으로 탐지한다.

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
    "domain",        # 핵심 도메인 (자원 엔티티, 커넥터 Protocol)
    "config",        # 설정 (모든 계층이 참조 가능)
    "utils",         # 공유 유틸리티 (모든 계층이 참조 가능)
    "infrastructure",# 하이퍼바이저 어댑터, DB, 캐시, 보안, 저장소
    "application",   # 유스케이스 (인벤토리 조회, 정규화, 메타데이터 관리)
    "orchestration", # 수집 스케줄러/워커
    "interface",     # FastAPI 어댑터
    "entry",         # 진입점 (main.py)
]

# 모듈 경로 → 계층 매핑
MODULE_LAYER_MAP: dict[str, Layer] = {
    # domain — 엔티티, 값 객체, 포트(Protocol)
    "src.domain":                        "domain",
    "src.domain.resource":               "domain",   # VM, Host, Cluster, Datastore, Network
    "src.domain.connection":             "domain",   # 하이퍼바이저 연결 정보
    "src.domain.metadata":               "domain",   # 소유자·환경 등 포탈 부여 메타데이터
    "src.domain.auth":                   "domain",   # 사용자, 역할, 권한
    "src.domain.history":                "domain",   # 변경 이력, 수집 이력
    "src.domain.audit":                  "domain",   # 감사 이벤트
    "src.domain.ports":                  "domain",   # HypervisorInventoryReader 등 Protocol 정의
    # config / utils
    "src.config":                        "config",
    "src.utils":                         "utils",
    # infrastructure — 하이퍼바이저 어댑터 및 외부 시스템 연동
    "src.infrastructure":                "infrastructure",
    "src.infrastructure.vcenter":        "infrastructure",   # pyVmomi 기반 vCenter 수집 어댑터
    "src.infrastructure.hyperv":         "infrastructure",   # WinRM/WMI 기반 Hyper-V 수집 어댑터
    "src.infrastructure.db":             "infrastructure",
    "src.infrastructure.cache":          "infrastructure",
    "src.infrastructure.security":       "infrastructure",   # 자격증명 암호화, 토큰
    "src.infrastructure.repository":     "infrastructure",
    # application — 유스케이스 서비스
    "src.application":                   "application",
    # orchestration — 수집 스케줄러/워커
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

# 커넥터 Protocol 정의 모듈 — 읽기 전용 검사 대상
CONNECTOR_PORT_MODULE = "src.domain.ports"

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
# 3. 읽기 전용 범위 규칙
#    하이퍼바이저 자원을 변경하는 동작을 나타내는 메서드명 접두사.
#    커넥터 Protocol과 어댑터의 public 메서드/함수에만 적용한다.
# ──────────────────────────────────────────────

FORBIDDEN_METHOD_PREFIXES: tuple[str, ...] = (
    "create_", "delete_", "destroy_", "remove_",
    "power_", "start_", "stop_", "restart_", "reboot_",
    "suspend_", "resume_", "reset_", "shutdown_",
    "modify_", "reconfigure_", "resize_", "rename_",
    "migrate_", "relocate_", "clone_", "deploy_", "provision_",
    "revert_", "attach_", "detach_", "mount_", "unmount_",
)

# 하이퍼바이저 자원을 변경하지 않는 정당한 메서드 (세션·수집 제어)
ALLOWED_METHOD_NAMES: frozenset[str] = frozenset({
    "start_session", "stop_session", "close_session", "reset_session",
    "start_collection", "stop_collection",
    "reset_connection", "remove_stale_cache",
})


# ──────────────────────────────────────────────
# 4. 파일 → 계층 결정
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


def is_readonly_scope(module_path: str) -> bool:
    """읽기 전용 메서드 검사 대상 모듈인지 판정한다."""
    if module_path == CONNECTOR_PORT_MODULE or module_path.startswith(CONNECTOR_PORT_MODULE + "."):
        return True
    return resolve_adapter(module_path) is not None


def file_to_module(file_path: Path, project_root: Path) -> str:
    """파일 경로를 모듈 경로로 변환한다."""
    rel = file_path.relative_to(project_root)
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


# ──────────────────────────────────────────────
# 5. AST 분석
# ──────────────────────────────────────────────

@dataclass
class ImportInfo:
    module: str
    line: int
    statement: str


@dataclass
class MethodInfo:
    name: str
    line: int
    statement: str


def _parse(file_path: Path) -> ast.Module | None:
    try:
        source = file_path.read_text(encoding="utf-8")
        return ast.parse(source, filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None


def extract_imports(file_path: Path) -> list[ImportInfo]:
    """파일에서 src.* import 문을 추출한다."""
    tree = _parse(file_path)
    if tree is None:
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


def extract_public_methods(file_path: Path) -> list[MethodInfo]:
    """파일의 public 함수·메서드를 추출한다 (언더스코어 시작 제외)."""
    tree = _parse(file_path)
    if tree is None:
        return []

    methods: list[MethodInfo] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        methods.append(MethodInfo(
            name=node.name,
            line=node.lineno,
            statement=f"{prefix} {node.name}(...)",
        ))
    return methods


# ──────────────────────────────────────────────
# 6. 위반 검사
# ──────────────────────────────────────────────

@dataclass
class Violation:
    file: str
    line: int
    severity: Literal["error", "warning"]
    reason: str
    statement: str
    kind: Literal["dependency", "readonly"] = "dependency"
    from_layer: Layer | None = None
    to_layer: Layer | None = None
    from_module: str = ""
    to_module: str = ""


@dataclass
class CheckResult:
    violations: list[Violation] = field(default_factory=list)
    checked_files: int = 0
    total_imports: int = 0
    allowed_imports: int = 0


def check_imports(file_path: Path, project_root: Path) -> list[Violation]:
    """단일 파일의 계층 의존성 규칙 위반을 검사한다."""
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
        common = dict(
            file=rel_path,
            line=imp.line,
            statement=imp.statement,
            kind="dependency",
            from_layer=from_layer,
            to_layer=to_layer,
            from_module=from_module,
            to_module=imp.module,
        )

        # 특화 규칙 A: 유스케이스가 특정 하이퍼바이저 어댑터를 직접 참조 금지
        if from_layer in ("application", "orchestration") and to_adapter is not None:
            violations.append(Violation(
                severity="error",
                reason=(
                    f"{from_layer}이 하이퍼바이저 어댑터({to_adapter})에 직접 결합. "
                    "src.domain.ports의 커넥터 Protocol을 통해 주입받을 것"
                ),
                **common,
            ))
            continue

        # 특화 규칙 B: 하이퍼바이저 어댑터 간 교차 참조 금지
        if from_adapter is not None and to_adapter is not None and from_adapter != to_adapter:
            violations.append(Violation(
                severity="error",
                reason=(
                    f"하이퍼바이저 어댑터 교차 참조 ({from_adapter} → {to_adapter}). "
                    "공통 로직은 src.domain 또는 src.utils로 추출할 것"
                ),
                **common,
            ))
            continue

        allowed = ALLOWED_DEPS.get(from_layer, set())
        if to_layer not in allowed and to_layer != from_layer:
            violations.append(Violation(
                severity="error",
                reason=f"{from_layer} → {to_layer} 의존은 Clean Architecture에서 금지",
                **common,
            ))

    return violations


def check_readonly_scope(file_path: Path, project_root: Path) -> list[Violation]:
    """커넥터 Protocol·어댑터에 자원 변경 메서드가 정의되었는지 검사한다."""
    module = file_to_module(file_path, project_root)
    if not is_readonly_scope(module):
        return []

    rel_path = str(file_path.relative_to(project_root))
    violations: list[Violation] = []

    for method in extract_public_methods(file_path):
        if method.name in ALLOWED_METHOD_NAMES:
            continue
        matched = next(
            (p for p in FORBIDDEN_METHOD_PREFIXES if method.name.startswith(p)),
            None,
        )
        if matched is None:
            continue
        violations.append(Violation(
            file=rel_path,
            line=method.line,
            severity="error",
            reason=(
                f"읽기 전용 포탈은 자원 변경·제어 메서드를 정의할 수 없음 "
                f"(금지 접두사 '{matched}', spec.md CST-01 / NFR-202). "
                "조회가 목적이면 get_/list_/fetch_/collect_ 등으로 명명할 것"
            ),
            statement=method.statement,
            kind="readonly",
            from_module=module,
        ))

    return violations


def check_project(project_root: Path) -> CheckResult:
    """프로젝트 전체의 아키텍처 규칙을 검사한다."""
    src_dir = project_root / "src"
    result = CheckResult()

    if not src_dir.is_dir():
        return result

    for py_file in sorted(src_dir.rglob("*.py")):
        # __init__.py는 re-export 목적이므로 의존성 검사에서 제외하되,
        # 읽기 전용 메서드 검사는 모든 파일에 적용한다.
        readonly_violations = check_readonly_scope(py_file, project_root)
        result.violations.extend(readonly_violations)

        if py_file.name == "__init__.py":
            continue

        result.checked_files += 1
        imports = extract_imports(py_file)
        result.total_imports += len(imports)
        import_violations = check_imports(py_file, project_root)
        result.violations.extend(import_violations)
        result.allowed_imports += len(imports) - len(import_violations)

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
# 7. 출력
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

KIND_LABEL = {"dependency": "계층 의존성", "readonly": "읽기 전용 범위"}


def _print_violation(v: Violation, color: str, tag: str) -> None:
    c = COLORS
    print(f"  {c[color]}{tag}{c['reset']} {v.file}:{v.line}  ({KIND_LABEL[v.kind]})")
    print(f"    {v.statement}")
    if v.kind == "dependency":
        print(f"    {c['cyan']}{v.from_layer}{c['reset']} -> {c['cyan']}{v.to_layer}{c['reset']}: {v.reason}")
    else:
        print(f"    {v.reason}")
    print()


def print_text_report(result: CheckResult, project_root: Path, verbose: bool = False) -> None:
    """텍스트 형식으로 결과를 출력한다."""
    c = COLORS

    print(f"\n{c['bold']}=== 아키텍처 규칙 검사 ==={c['reset']}\n")

    if not (project_root / "src").is_dir():
        print(f"  {c['yellow']}src/ 디렉토리가 없습니다. 구현 시작 전이면 정상입니다.{c['reset']}\n")
        return

    errors = [v for v in result.violations if v.severity == "error"]
    warnings = [v for v in result.violations if v.severity == "warning"]
    readonly_errors = [v for v in errors if v.kind == "readonly"]

    print(f"  검사 파일: {result.checked_files}개")
    print(f"  총 import: {result.total_imports}개")
    print(f"  허용 import: {result.allowed_imports}개")
    print(f"  {c['red']}위반 (error): {len(errors)}개{c['reset']}"
          f"  (읽기 전용 범위 위반 {len(readonly_errors)}개 포함)")
    print(f"  {c['yellow']}경고 (warning): {len(warnings)}개{c['reset']}")
    print()

    if errors:
        print(f"{c['red']}{c['bold']}--- ERRORS ---{c['reset']}\n")
        for v in errors:
            _print_violation(v, "red", "[ERROR]")

    if warnings:
        print(f"{c['yellow']}{c['bold']}--- WARNINGS ---{c['reset']}\n")
        for v in warnings:
            _print_violation(v, "yellow", "[WARN]")

    if not errors and not warnings:
        print(f"  {c['green']}모든 아키텍처 규칙을 준수합니다.{c['reset']}\n")

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
        print("  주의: 매트릭스는 계층 단위 집계이므로 어댑터 직접 참조·읽기 전용 위반은")
        print("        초록으로 보일 수 있습니다. 개별 판정은 ERRORS 목록을 기준으로 하세요.")
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
            "readonly_errors": len([
                v for v in result.violations if v.severity == "error" and v.kind == "readonly"
            ]),
        },
        "violations": [
            {
                "file": v.file,
                "line": v.line,
                "severity": v.severity,
                "kind": v.kind,
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
# 8. CLI
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="계층 의존성 + 읽기 전용 범위 규칙 위반 탐지",
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
