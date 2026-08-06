# 13. 리포트 및 내보내기

> Wave: 4 · 계층: application (`report.py`) · interface (`routes/reports.py`)
> 담당 요건: FR-8xx, FR-9xx 집계, NFR-206
> 의존: 06, 07, 09, 12 · 관련 결정: D-005

## 1. 목적

인벤토리 현황 리포트를 생성하고 Excel/CSV로 내보낸다.

**포탈은 정리 대상을 식별해 보고할 뿐, 정리 작업은 수행하지 않는다** (CST-01).
`docs/00_research_notes.md` §8.4: 상용 도구(VMware Aria Operations)조차 고아 디스크의 자동 삭제를 제공하지 않고
수동 검증을 요구한다. 잘못 식별된 자원을 삭제하면 복구가 불가능하기 때문이다.

## 2. 리포트 목록

| ID | 리포트 | 요건 | 우선순위 |
|---|---|---|---|
| R-1 | 인벤토리 내보내기 | FR-801 | Must |
| R-2 | 자원 현황 집계 | FR-802 | Must |
| R-3 | 스냅샷 현황 | FR-803 | Must |
| R-4 | 유휴 자원 후보 | FR-804 | Should |
| R-5 | 용량 현황 | FR-805 | Should |
| R-6 | OS 분포 | FR-806 | Should |
| R-7 | 정기 발송 | FR-807 | Could |

---

## 3. R-1. 인벤토리 내보내기 (FR-801)

**화면의 조회 조건 그대로 내보낸다.** 필터링한 결과와 파일 내용이 일치해야 한다.

### 3.1 스트리밍 구조

수만 행을 메모리에 올리면 서버가 위험하다.

```python
EXPORT_ROW_LIMIT = 50_000
EXPORT_FETCH_SIZE = 1_000


class InventoryExporter:
    async def stream_rows(
        self, scope: AccessScope, criteria: SearchCriteria, columns: Sequence[str]
    ) -> AsyncIterator[ExportRow]:
        """저장소에서 서버 사이드 커서로 배치 조회한다."""
        offset = 0
        emitted = 0
        while emitted < EXPORT_ROW_LIMIT:
            page = Page(offset=offset, limit=EXPORT_FETCH_SIZE, sort_by="name")
            result = await self._repo.search_vms(scope, criteria, page)
            if not result.items:
                break
            for item in result.items:
                if emitted >= EXPORT_ROW_LIMIT:
                    break
                yield _to_export_row(item, columns)
                emitted += 1
            offset += len(result.items)
            if len(result.items) < EXPORT_FETCH_SIZE:
                break
```

### 3.2 CSV — 즉시 스트리밍

```python
async def stream_csv(self, rows: AsyncIterator[ExportRow], columns: Sequence[str]) -> AsyncIterator[bytes]:
    buf = io.StringIO()
    writer = csv.writer(buf)

    yield "﻿".encode("utf-8")                 # BOM — Excel에서 한글 깨짐 방지
    writer.writerow([COLUMN_LABELS[c] for c in columns])
    yield _flush(buf)

    async for row in rows:
        writer.writerow([row[c] for c in columns])
        if buf.tell() > 64 * 1024:
            yield _flush(buf)
    yield _flush(buf)
```

**BOM이 필요하다.** UTF-8 CSV를 Excel이 열 때 BOM이 없으면 한글이 깨진다.

### 3.3 Excel — write_only 모드

```python
async def write_xlsx(self, rows: AsyncIterator[ExportRow], columns: Sequence[str], path: Path) -> ExportSummary:
    """openpyxl write_only 모드로 메모리 사용을 억제한다."""
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("인벤토리")
    ws.append([COLUMN_LABELS[c] for c in columns])

    count = 0
    async for row in rows:
        ws.append([row[c] for c in columns])
        count += 1

    truncated = count >= EXPORT_ROW_LIMIT
    if truncated:
        note = wb.create_sheet("안내")
        note.append(["행 수 상한에 도달하여 결과가 잘렸습니다."])
        note.append([f"출력 행 수: {count:,}"])
        note.append(["필터를 좁혀 다시 내보내세요."])

    await asyncio.to_thread(wb.save, path)         # 저장은 블로킹이므로 오프로드
    return ExportSummary(row_count=count, truncated=truncated)
```

**절단을 반드시 명시한다.** 조용히 잘린 파일을 전체 목록으로 오해하면 잘못된 의사결정으로 이어진다.
파일 안(별도 시트)과 API 응답 헤더(`X-Export-Truncated: true`) 양쪽에 표시한다.

### 3.4 "수집 불가" 표현 — 화면과 일관 (FR-501)

```python
UNAVAILABLE_CELL = {
    GuestInfoAvailability.TOOLS_NOT_INSTALLED: "[수집 불가: 게스트 도구 미설치]",
    GuestInfoAvailability.TOOLS_NOT_RUNNING:   "[수집 불가: 게스트 도구 미동작]",
    GuestInfoAvailability.UNKNOWN:             "[수집 불가: 확인 필요]",
}


def _guest_cell(vm: VmSummary, field: str) -> str:
    if vm.guest_availability is GuestInfoAvailability.AVAILABLE:
        return getattr(vm, field) or ""
    return UNAVAILABLE_CELL[vm.guest_availability]
```

**빈 셀로 두면 파일을 받은 사람이 "IP 없음"으로 오해한다.**
추가로 `게스트정보수집상태` 컬럼을 제공하여 Excel에서 필터·집계가 가능하게 한다.

### 3.5 컬럼 구성

```python
COLUMN_LABELS: dict[str, str] = {
    "name": "이름", "connection_name": "연결", "hypervisor": "하이퍼바이저",
    "power_state": "전원 상태", "primary_ip": "IP 주소", "guest_os_name": "게스트 OS",
    "guest_availability": "게스트정보수집상태", "vcpu_count": "vCPU",
    "memory_mb": "메모리(MB)", "host_name": "호스트", "cluster_name": "클러스터",
    "total_provisioned_bytes": "프로비저닝 용량(GB)", "snapshot_count": "스냅샷 수",
    "owner": "소유자", "environment": "환경", "last_seen_at": "최종 수집",
    "lifecycle": "상태",
}
```

기본 컬럼은 `spec.md` §2.2의 필수(✔) 속성. 사용자가 선택 가능(FR-407 연계).
**영문 키 행을 옵션으로 제공**하여 시스템 연동에 쓸 수 있게 한다.

### 3.6 감사 (NFR-206)

인벤토리 정보(IP·호스트명·OS)는 그 자체로 공격 표면 정보다. 대량 내보내기는 유출 경로이므로 추적한다.

```python
await self._audit.record(AuditEvent(
    actor=scope.username, actor_ip=ip, action=AuditAction.EXPORT,
    target_type="inventory", target_id=None, result="success",
    detail=build_detail(AuditAction.EXPORT, {
        "criteria": _summarize_criteria(criteria),
        "row_count": summary.row_count,
        "format": fmt,
        "truncated": summary.truncated,
    }),
))
```

---

## 4. R-2. 자원 현황 집계 (FR-802)

대시보드(FR-902·903)와 **같은 집계 로직을 공유**한다. 두 곳에서 따로 계산하면 값이 어긋난다.

```sql
SELECT
    vm.connection_id,
    c.display_name                              AS connection_name,
    c.kind                                      AS connection_kind,
    vm.cluster_native_id,
    cl.name                                     AS cluster_name,
    md.environment,
    COUNT(*)                                    AS vm_count,
    COUNT(*) FILTER (WHERE vm.power_state = 'on')  AS running_count,
    SUM(vm.vcpu_count)                          AS total_vcpu,
    SUM(vm.memory_mb)                           AS total_memory_mb,
    SUM(vm.total_provisioned_bytes)             AS total_provisioned_bytes
FROM virtual_machines vm
JOIN connections c ON c.connection_id = vm.connection_id
LEFT JOIN clusters cl ON cl.connection_id = vm.connection_id
                      AND cl.native_id = vm.cluster_native_id
LEFT JOIN resource_metadata md ON md.resource_id = vm.resource_id
WHERE vm.lifecycle = 'active'
  AND (:scope_all OR vm.connection_id = ANY(:scope_connection_ids))
GROUP BY GROUPING SETS (
    (vm.connection_id, c.display_name, c.kind),
    (vm.cluster_native_id, cl.name),
    (md.environment),
    ()
);
```

`GROUPING SETS`로 연결별·클러스터별·환경별·전체 집계를 **한 번의 쿼리**로 얻는다.

**Redis 캐시 TTL 5분, 범위별 키 분리** (계획 07 §7.3).

```python
def _cache_key(scope: AccessScope, report: str) -> str:
    if scope.is_unrestricted:
        return f"report:{report}:all"
    h = hashlib.sha256(
        ",".join(sorted(str(c) for c in scope.allowed_connection_ids)).encode()
    ).hexdigest()[:16]
    return f"report:{report}:{h}"
```

---

## 5. R-3. 스냅샷 현황 (FR-803)

`docs/00_research_notes.md` §8.3: 스냅샷 delta 파일 누적은 대표적 공간 낭비 요인이다.

```sql
SELECT
    vm.resource_id, vm.name, c.display_name AS connection_name,
    vm.snapshot_count, vm.latest_snapshot_at,
    EXTRACT(DAY FROM (now() - vm.latest_snapshot_at))::int AS age_days,
    vm.snapshot_size_bytes,
    md.owner, md.environment
FROM virtual_machines vm
JOIN connections c ON c.connection_id = vm.connection_id
LEFT JOIN resource_metadata md ON md.resource_id = vm.resource_id
WHERE vm.lifecycle = 'active'
  AND vm.snapshot_count > 0
  AND vm.latest_snapshot_at < now() - make_interval(days => :older_than_days)
  AND (:scope_all OR vm.connection_id = ANY(:scope_connection_ids))
ORDER BY vm.latest_snapshot_at ASC
LIMIT :limit OFFSET :offset;
```

- 기본 기준 30일. 조회 시 조정 가능
- **소유자 정보를 함께 제공**한다. 담당자를 모르면 조치할 수 없다
- 정리 기능은 제공하지 않는다 (§1)

---

## 6. R-4. 유휴 자원 후보 (FR-804)

`docs/00_research_notes.md` §8.1: 좀비 VM은 사용되지 않으면서 자원을 점유한다.

**판정을 보수적으로 한다.** 잘못된 "유휴" 판정은 운영 중인 VM을 정리 대상으로 지목하는 사고를 부른다.

### 6.1 판정 기준

| 후보 유형 | 조건 | 근거 컬럼 |
|---|---|---|
| 장기 전원 오프 | `power_state='off'`가 N일 이상 | `power_state_changed_at` |
| 메타데이터 미등록 | 소유자·환경 없음 | `resource_metadata` |
| 도구 장기 미동작 | `guest_availability != available`가 N일 이상 | `guest_observed_at` |
| 스냅샷 방치 | 스냅샷 N일 초과 + 전원 오프 | 복합 |

```sql
SELECT vm.resource_id, vm.name, c.display_name AS connection_name,
       vm.power_state, vm.power_state_changed_at,
       EXTRACT(DAY FROM (now() - vm.power_state_changed_at))::int AS off_days,
       vm.vcpu_count, vm.memory_mb, vm.total_provisioned_bytes,
       md.owner, md.environment,
       -- 판정 근거를 함께 반환한다. 근거 없는 목록은 신뢰받지 못한다
       ARRAY_REMOVE(ARRAY[
           CASE WHEN vm.power_state = 'off'
                 AND vm.power_state_changed_at < now() - make_interval(days => :idle_days)
                THEN 'long_powered_off' END,
           CASE WHEN md.owner IS NULL THEN 'no_owner' END,
           CASE WHEN vm.guest_availability <> 'available'
                 AND vm.guest_observed_at < now() - make_interval(days => :idle_days)
                THEN 'tools_inactive' END
       ], NULL) AS reasons
FROM virtual_machines vm
JOIN connections c ON c.connection_id = vm.connection_id
LEFT JOIN resource_metadata md ON md.resource_id = vm.resource_id
WHERE vm.lifecycle = 'active'
  AND (:scope_all OR vm.connection_id = ANY(:scope_connection_ids))
  AND (
        (vm.power_state = 'off' AND vm.power_state_changed_at < now() - make_interval(days => :idle_days))
     OR (vm.guest_availability <> 'available' AND vm.guest_observed_at < now() - make_interval(days => :idle_days))
  )
ORDER BY vm.total_provisioned_bytes DESC NULLS LAST;
```

### 6.2 성능 고려

"N일 이상 전원 오프"를 변경 이력 조회로 판정하면 대량 데이터에서 느리다.
**`virtual_machines.power_state_changed_at` 컬럼을 두어 이력 조회 없이 판정**한다 (계획 06 §2.3에 포함됨).

이 컬럼은 저장소 upsert에서 전원 상태 변경 감지 시 갱신한다.

### 6.3 표현 규칙

- **"정리 대상"이 아니라 "확인 필요"로 표현**한다. 판정은 사람이 한다
- 각 후보에 판정 근거(`reasons`)를 함께 표시
- `[TODO]` 유휴 판정 기준일 확정 필요. 잠정 30일

---

## 7. R-5. 용량 현황 (FR-805)

```sql
SELECT
    ds.resource_id, ds.name, ds.kind, c.display_name AS connection_name,
    ds.capacity_bytes, ds.free_bytes,
    (ds.capacity_bytes - ds.free_bytes)              AS used_bytes,
    ds.provisioned_bytes,
    CASE WHEN ds.capacity_bytes > 0
         THEN ROUND((ds.capacity_bytes - ds.free_bytes)::numeric / ds.capacity_bytes * 100, 1)
    END                                              AS used_percent,
    CASE WHEN ds.capacity_bytes > 0
         THEN ROUND(ds.provisioned_bytes::numeric / ds.capacity_bytes, 2)
    END                                              AS overcommit_ratio,
    (SELECT COUNT(DISTINCT d.resource_id) FROM vm_disks d
      WHERE d.datastore_name = ds.name)              AS vm_count
FROM datastores ds
JOIN connections c ON c.connection_id = ds.connection_id
WHERE ds.lifecycle = 'active'
  AND (:scope_all OR ds.connection_id = ANY(:scope_connection_ids))
ORDER BY used_percent DESC NULLS LAST;
```

- **오버커밋 비율** = 프로비저닝 ÷ 총 용량. 1을 넘으면 Thin 초과 할당 상태
- 사용률·오버커밋 임계 초과 항목을 강조
- vCenter Datastore와 Hyper-V CSV/SMB를 **동일 포맷**으로 표시 (NFR-401)

---

## 8. R-6. OS 분포 (FR-806)

**지원 종료(EOL) OS 파악이 주 목적**이다.

```sql
SELECT
    COALESCE(vm.guest_os_name, '(수집 불가)')       AS os_name,
    vm.guest_os_source,
    COUNT(*)                                        AS vm_count,
    COUNT(*) FILTER (WHERE vm.guest_os_source = 'vm_config') AS from_config_count,
    SUM(vm.vcpu_count)                              AS total_vcpu,
    SUM(vm.memory_mb)                               AS total_memory_mb
FROM virtual_machines vm
WHERE vm.lifecycle = 'active'
  AND (:scope_all OR vm.connection_id = ANY(:scope_connection_ids))
GROUP BY 1, 2
ORDER BY vm_count DESC;
```

### 8.1 반드시 지켜야 할 두 가지

1. **수집 불가 자원 수를 함께 표시한다.** 이를 빼고 집계하면 "OS 분포 100%"처럼 보여 실제 커버리지를 오해한다.

```
Windows Server 2019    412건  (48%)
Ubuntu 22.04 LTS       287건  (33%)
CentOS 7               104건  (12%)   ⚠ 지원 종료
수집 불가               58건  ( 7%)   ← 반드시 표시
```

2. **구성값 폴백 항목을 별도 표시한다** (FR-304). `os_source='vm_config'`는 정확도가 낮다.
   템플릿에서 생성된 VM은 구성값이 실제 OS와 다른 경우가 흔하다.

### 8.2 OS 문자열 정규화

```python
# src/utils/os_name.py — 어댑터 양쪽이 아닌 리포트 계층에서 사용
OS_NORMALIZE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"Microsoft Windows Server (\d{4})( R2)?.*", re.I), r"Windows Server \1\2"),
    (re.compile(r"Microsoft Windows (\d+|XP|Vista).*", re.I), r"Windows \1"),
    (re.compile(r"Ubuntu.*?(\d+\.\d+).*", re.I), r"Ubuntu \1"),
    (re.compile(r"(CentOS|Red Hat Enterprise Linux|Rocky|Alma).*?(\d+).*", re.I), r"\1 \2"),
)


def normalize_os_name(raw: str | None) -> str | None:
    if not raw:
        return None
    for pattern, repl in OS_NORMALIZE_RULES:
        if pattern.match(raw):
            return pattern.sub(repl, raw).strip()
    return raw.strip()
```

**원본 문자열도 함께 보관한다.** 정규화 규칙이 잘못돼도 원본으로 되돌릴 수 있어야 한다.

---

## 9. R-7. 정기 발송 (FR-807, Could)

- 스케줄러(계획 06)에 등록
- **수신자별 조회 범위를 적용한다.** 범위 밖 정보가 메일로 나가면 회수할 수 없다
- 발송도 감사 대상 (NFR-206)
- `[TODO]` 메일 발송 수단(SMTP)은 NFR-305 알림 수단과 함께 확정

```python
async def send_scheduled_report(self, subscription: ReportSubscription) -> None:
    user = await self._users.get(subscription.user_id)
    scope = build_scope(user, await self._scopes.list_for_user(user.user_id))   # 수신자 범위
    ...
```

---

## 10. API (계획 08 연계)

| 메서드 | 경로 | 비고 |
|---|---|---|
| GET | `/api/v1/reports/inventory/export?format=csv\|xlsx` | 스트리밍 응답 |
| GET | `/api/v1/reports/summary` | R-2 (대시보드와 공유) |
| GET | `/api/v1/reports/snapshots?older_than_days=30` | R-3 |
| GET | `/api/v1/reports/idle-candidates?idle_days=30` | R-4 |
| GET | `/api/v1/reports/capacity` | R-5 |
| GET | `/api/v1/reports/os-distribution` | R-6 |

```python
@router.get("/inventory/export")
async def export_inventory(...) -> StreamingResponse:
    summary_holder: list[ExportSummary] = []
    stream = exporter.stream_csv(...)
    headers = {
        "Content-Disposition": f'attachment; filename="inventory_{_ts()}.csv"',
        "X-Export-Truncated": "false",       # 스트리밍 중 확정되므로 트레일러 대신 응답 후 감사에 기록
    }
    return StreamingResponse(stream, media_type="text/csv; charset=utf-8", headers=headers)
```

권한: `EXPORT` (viewer 이상). **모든 응답에 조회 범위 적용.**

---

## 11. 구현 순서

| # | 작업 | 검증 |
|---|---|---|
| 1 | `stream_rows` 커서 조회 | 대량에서 메모리 안정 (5만 행 프로파일링) |
| 2 | CSV 스트리밍 | BOM, 한글 정상, 청크 플러시 |
| 3 | Excel `write_only` | **`xlsx` 스킬로 출력 파일 구조 확인** |
| 4 | 수집 불가 셀 표현 | 빈 셀과 구분, 상태 컬럼 |
| 5 | 행 수 상한·절단 표시 | 파일 내 안내 시트 + 감사 기록 |
| 6 | 내보내기 감사 | 조건·건수·형식·절단 기록 |
| 7 | R-2 집계 (`GROUPING SETS`) | 대시보드와 값 일치, 범위 반영 |
| 8 | 집계 캐시 | 범위별 키 분리 |
| 9 | R-3 스냅샷 | 기준일 필터, 소유자 포함 |
| 10 | R-4 유휴 후보 | 판정 근거 표시, `power_state_changed_at` 활용 |
| 11 | R-5 용량 | 오버커밋 계산, 하이퍼바이저 무관 동일 포맷 |
| 12 | R-6 OS 분포 | **수집 불가 건수 포함**, 구성값 폴백 구분 |

## 12. 완료 기준

- [ ] 화면 조회 조건과 내보내기 결과가 일치
- [ ] 5만 행 내보내기에서 메모리 사용이 안정적
- [ ] CSV가 Excel에서 한글 깨짐 없이 열림 (BOM)
- [ ] **행 수 상한 초과 시 절단 사실이 파일과 감사 로그에 명시됨**
- [ ] "수집 불가"가 빈 셀과 구분되고 상태 컬럼이 제공됨
- [ ] R-2 집계가 대시보드 값과 일치
- [ ] 모든 리포트에 조회 범위가 적용됨
- [ ] 집계 캐시가 범위별로 분리됨
- [ ] 유휴 후보에 판정 근거가 함께 표시됨
- [ ] 유휴 판정이 이력 조회 없이 `power_state_changed_at`으로 동작
- [ ] **OS 분포에 수집 불가 건수가 함께 표시됨**
- [ ] 구성값 폴백 OS가 별도 표시됨
- [ ] 내보내기가 감사 로그에 기록됨
- [ ] **자원을 정리·삭제하는 기능이 없음** (식별·보고만)
- [ ] `arch_check.py` 통과

## 13. 주의사항

- **조용한 절단이 가장 위험하다.** 상한에 걸려 잘린 파일을 전체 목록으로 오해하면 잘못된 의사결정으로 이어진다.
- 유휴 자원 판정은 보수적으로. "정리 대상"이라는 표현을 쓰지 않는다 (§6.3).
- OS 분포에서 수집 불가를 제외하고 집계하면 커버리지를 과대평가하게 된다 (§8.1).
- 리포트는 조회 범위 적용을 빠뜨리기 쉬운 경로다 (계획 09 §10). 새 리포트마다 확인한다.
- 정기 발송은 범위 밖 정보 유출 위험이 가장 큰 기능이다. 수신자 범위 적용을 반드시 검증한다.
- 대시보드와 리포트가 같은 집계를 따로 계산하면 값이 어긋난다. 로직을 공유한다.
