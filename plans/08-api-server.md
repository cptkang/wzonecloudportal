# 08. FastAPI 서버 및 연결 관리 API

> Wave: 4
> 계층: interface (`src/api/`) · application (`connection_service.py`)
> 담당 요건: FR-1xx(연결 관리), FR-11xx(외부 API), FR-4xx·6xx·8xx 노출
> 의존: 03, 06, 07, 09, 10, 12
> 관련 결정: D-005, D-007, D-008

## 1. 목적

포탈 UI와 외부 시스템에 REST API를 제공하고, 하이퍼바이저 연결을 관리한다.

**모든 API는 조회 전용이다** — 단, 포탈 자체의 데이터(연결 설정, 메타데이터, 사용자)는 변경할 수 있다.
**하이퍼바이저 자원을 변경하는 엔드포인트는 존재하지 않는다** (`spec.md` FR-11xx 주석, D-005).

## 2. 엔드포인트 구성

```
src/api/routes/
├── health.py         헬스체크
├── auth.py           로그인, 토큰
├── connections.py    연결 관리 (FR-1xx) — admin 전용
├── inventory.py      자원 조회·검색 (FR-4xx)
├── metadata.py       메타데이터 (FR-6xx)
├── history.py        변경 이력·수명주기 (FR-7xx)
├── quality.py        데이터 품질 (FR-5xx)
├── reports.py        리포트·내보내기 (FR-8xx) — 계획 13
└── dashboard.py      대시보드 집계 (FR-9xx)
```

### 2.1 주요 엔드포인트

| 메서드 | 경로 | 요건 | 권한 |
|---|---|---|---|
| GET | `/api/v1/health` | — | 없음 |
| POST | `/api/v1/auth/login` | FR-1001 | 없음 |
| GET | `/api/v1/connections` | FR-116 | admin |
| POST | `/api/v1/connections` | FR-101·103·104·105 | admin |
| PATCH | `/api/v1/connections/{id}` | FR-107·108 | admin |
| DELETE | `/api/v1/connections/{id}` | FR-109 | admin |
| POST | `/api/v1/connections/{id}/test` | FR-106 | admin |
| POST | `/api/v1/connections/test` | FR-106 (저장 전) | admin |
| POST | `/api/v1/connections/{id}/collect` | FR-202 | admin |
| GET | `/api/v1/connections/{id}/runs` | FR-205 | admin |
| GET | `/api/v1/virtual-machines` | FR-401·405·406 | viewer |
| GET | `/api/v1/virtual-machines/{id}` | FR-402 | viewer |
| GET | `/api/v1/virtual-machines/{id}/related` | FR-409 | viewer |
| GET | `/api/v1/virtual-machines/{id}/history` | FR-705 | viewer |
| GET | `/api/v1/search` | FR-403 | viewer |
| GET | `/api/v1/search/by-ip` | **FR-404** | viewer |
| GET | `/api/v1/hosts`, `/clusters`, `/datastores`, `/networks` | FR-401 | viewer |
| PATCH | `/api/v1/resources/{id}/metadata` | FR-601 | operator |
| POST | `/api/v1/resources/metadata/bulk` | FR-604 | operator |
| GET | `/api/v1/quality/*` | FR-503·504·505 | viewer |
| GET | `/api/v1/changes` | FR-703·704 | viewer |
| GET | `/api/v1/dashboard/summary` | FR-9xx | viewer |

## 3. 연결 관리 API (FR-1xx) — 이 계획의 핵심

### 3.1 등록 (FR-101·103·104·105)

```
POST /api/v1/connections
{
  "kind": "hyperv-cluster",
  "display_name": "본사 Hyper-V 클러스터",
  "address": "hvcluster01.corp.local",
  "port": 5986,
  "protocol": "https",
  "auth_method": "kerberos",          // vCenter는 불필요
  "username": "CORP\\svc-portal",
  "password": "…",
  "verify_tls": true,
  "collection_interval_minutes": 360
}
```

**연결 유형별 스키마 분기 (FR-103)**: Pydantic discriminated union으로 유형별 필수 필드를 강제한다.

```python
class VCenterConnectionCreate(BaseModel):
    kind: Literal[ConnectionKind.VCENTER]
    address: str
    port: int = 443
    username: str
    password: SecretStr
    verify_tls: bool = True

class HyperVConnectionCreate(BaseModel):
    kind: Literal[ConnectionKind.HYPERV_HOST, ConnectionKind.HYPERV_CLUSTER]
    address: str
    port: int = 5986
    protocol: Literal["http", "https"] = "https"
    auth_method: WinRmAuth                    # 필수 — Hyper-V만
    username: str
    password: SecretStr
    verify_tls: bool = True

ConnectionCreate = Annotated[
    VCenterConnectionCreate | HyperVConnectionCreate, Field(discriminator="kind")
]
```

**입력 검증 (FR-104)**: Pydantic validator로 저장 전 검증하고, 항목별 오류를 422로 반환한다.
- 주소: FQDN 또는 IP 형식
- 포트: 1~65535
- 계정: 유형별 형식 (`user@domain` / `DOMAIN\user`)

**중복 등록 방지 (FR-105)**: `(address, username)` 조합 중복 시 409와 기존 연결 ID를 반환한다.

### 3.2 수정 (FR-107·108·110)

```
PATCH /api/v1/connections/{id}
```

- **`id`는 변경할 수 없다** (FR-110). 경로 파라미터로만 받고 본문에서 무시한다
- **비밀번호 필드를 생략하면 기존 값 유지** (FR-108). `None`과 미지정을 구분한다 (계획 07 §5.2와 동일 패턴)
- 응답에 비밀번호를 포함하지 않는다. `has_password: bool`만 노출 (계획 10 §2.4)
- 저장 후 연결 테스트를 자동 실행하고, 실패해도 저장은 유지하되 경고를 응답에 포함

**주소 변경 시 안내 (FR-110)**: 응답에 경고를 포함한다.

```json
{ "warnings": ["주소가 변경되었습니다. 다른 하이퍼바이저를 가리키는 경우 다음 수집에서 기존 자원이 대량 미발견 처리됩니다."] }
```

### 3.3 삭제 (FR-109)

**2단계로 처리한다.** 수천 건의 자원이 딸려 있으므로 실수 삭제를 막아야 한다.

```
DELETE /api/v1/connections/{id}                    → 409 + 영향 범위 반환
DELETE /api/v1/connections/{id}?confirm=true       → 삭제 수행
```

```json
{
  "error": "confirmation_required",
  "impact": { "virtual_machines": 1243, "hosts": 18, "datastores": 42 },
  "policy": "자원은 삭제되지 않고 '연결 해제됨' 상태로 보존됩니다."
}
```

정책은 계획 06 §3.5의 권장안(보존)을 따른다. `[TODO]` 확정 시 조정.

### 3.4 연결 테스트 (FR-106)

저장 전(`POST /connections/test`)과 저장 후(`POST /connections/{id}/test`) 모두 지원한다.
응답은 계획 03 §4의 단계별 결과를 그대로 노출한다.

```json
{
  "stages": [
    {"stage": "reachable", "passed": true},
    {"stage": "tls_valid", "passed": true},
    {"stage": "authenticated", "passed": false, "detail": "인증에 실패했습니다. 계정 또는 인증 방식을 확인하세요."}
  ],
  "readable_types": [],
  "is_usable": false
}
```

**`detail`에 자격증명이 포함되지 않도록** 어댑터가 정제한 메시지만 전달한다 (계획 10 §2.4).

### 3.5 수동 수집 (FR-202)

```
POST /api/v1/connections/{id}/collect
```

- 이미 실행 중이면 409 + 진행 중인 `run_id` 반환
- 즉시 202를 반환하고 백그라운드 실행. 상태는 `/connections/{id}/runs`로 조회
- **자격증명 오류 상태의 연결은 거부한다** (FR-114). 재시도로 계정이 잠기는 것을 막는다

## 4. 외부 연동 API (FR-11xx)

| 요건 | 엔드포인트 |
|---|---|
| FR-1101 조회 API | `/api/v1/virtual-machines`, `/{id}` |
| FR-1102 검색 API | `/api/v1/search`, `/search/by-ip` |
| FR-1103 변경 이력 API | `/api/v1/changes?since=...` |
| FR-1104 인증·제한 | API 키 또는 JWT, 호출량 제한 |

- 외부 연동용 **API 키**를 사용자와 별도로 발급한다. 키에도 조회 범위를 부여한다
- 호출량 제한: Redis 기반 슬라이딩 윈도우 (기본 60 req/min)
- `/changes?since=` 는 외부 CMDB 동기화용이므로 **커서 기반 페이징**을 제공한다 (offset은 데이터 변동 시 누락 발생)

## 5. 공통 규약

### 5.1 응답 형식

```json
{ "items": [...], "total": 1243, "offset": 0, "limit": 50 }
```

에러:
```json
{ "error": "not_found", "message": "자원을 찾을 수 없습니다.", "detail": {...} }
```

### 5.2 예외 → HTTP 매핑 (`plans/README.md` §3.5)

| 도메인 예외 | HTTP |
|---|---|
| `ValidationError` | 422 |
| `AuthenticationError` (포탈 로그인) | 401 |
| 권한 부족 | 403 |
| `NotFoundError` | 404 |
| 중복 등록, 확인 필요, 수집 중 | 409 |
| `UnreachableError` (연결 테스트) | 200 + 실패 상세 (예외 아님) |
| 기타 | 500 (내부 상세 미노출) |

**연결 테스트 실패는 HTTP 에러가 아니다.** 테스트 자체는 성공했고 결과가 실패인 것이다.

### 5.3 인증·권한

- `Depends(get_access_scope)`로 범위 주입 (계획 09 §6)
- 라우터에 `require(Permission.X)` 적용 + **유스케이스에서 재검사** (이중 방어)
- 조회 범위는 유스케이스가 SQL에 반영. API가 결과를 후처리하지 않는다

### 5.4 감사 로그

연결 등록·수정·삭제·테스트, 메타데이터 변경, 내보내기 시 감사 이벤트 기록 (계획 10 §3.1).

## 6. 앱 구성 (`src/main.py`)

```
python -m src.main --mode api      → uvicorn FastAPI
python -m src.main --mode worker   → 수집 스케줄러 (계획 06)
```

- 어댑터 팩토리는 여기서 구성해 주입한다 (계획 03 §6). **`api`/`entry` 계층만 어댑터를 import할 수 있다**
- 시작 시: DB 연결 확인, 마이그레이션 상태 확인, Redis 연결 확인
- `/api/v1/health`는 DB·Redis 상태를 포함한다

## 7. 구현 순서

1. 앱 뼈대 + `/health` → 검증: 200 응답
2. 인증 라우터 + 의존성 → 검증: 로그인, 토큰 검증, 401/403
3. 연결 CRUD → 검증: 유형별 스키마 분기, 입력 검증, 중복 차단
4. 연결 테스트 → 검증: 단계별 결과, 자격증명 미노출
5. 삭제 2단계 확인 → 검증: `confirm` 없이 409 + 영향 범위
6. 자원 조회·검색 → 검증: 범위 필터, 페이징, IP 역조회
7. 메타데이터 → 검증: 부분 갱신, 권한
8. 이력·품질·대시보드
9. 외부 API 키 + 호출량 제한
10. OpenAPI 문서 정리 → 검증: `/docs` 확인

## 8. 완료 기준

- [ ] **하이퍼바이저 자원을 변경하는 엔드포인트가 없음** (전원·삭제·생성 경로 부재)
- [ ] 연결 관리 API가 admin에게만 허용됨
- [ ] Hyper-V 등록 시 인증 방식이 필수, vCenter는 불필요 (유형별 스키마)
- [ ] 비밀번호가 어떤 응답에도 포함되지 않음
- [ ] 수정 시 비밀번호 생략하면 기존 값 유지
- [ ] 삭제가 확인 없이는 수행되지 않고 영향 범위를 반환
- [ ] 자격증명 오류 연결의 수동 수집이 거부됨
- [ ] 모든 조회에 범위 필터 적용 (범위 밖 자원 미노출)
- [ ] `arch_check.py` 통과
- [ ] `/security-review` 지적 0건

## 9. 주의사항

- **연결 테스트 응답의 `detail`에 원본 예외 메시지를 그대로 넣지 않는다.** pyVmomi·WinRM 예외에 접속 정보가 섞일 수 있다.
- 자격증명 오류 상태에서 수동 수집을 허용하면 관리자가 반복 클릭해 계정을 잠글 수 있다 (CST-05).
- 대시보드 집계에도 조회 범위를 적용한다. 전체 건수 노출은 정보 누설이다 (계획 09 §9).
- 외부 API의 `since` 조회는 커서 기반으로 한다. offset 페이징은 데이터가 계속 변하는 이력 조회에서 누락을 만든다.
