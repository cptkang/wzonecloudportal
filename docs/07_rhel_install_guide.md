# 07. RHEL 9 폐쇄망 설치 가이드

인터넷이 차단된 RHEL / Rocky Linux 9 서버에 포탈을 설치하고, 스키마를 만들고,
데이터를 넣기까지의 전 과정이다.

| 항목 | 값 |
|---|---|
| 대상 OS | RHEL 9.x / Rocky Linux 9.x (x86_64) |
| Python | 3.11 (`python3.11` — 시스템 기본 3.9는 쓰지 않는다) |
| PostgreSQL | 17 표준 / 16 하한 (D-013) |
| 네트워크 | **폐쇄망** — dnf·pip이 외부에 나가지 않는다 |
| 기준 커밋 | Step 1 + Hyper-V 어댑터 (D-018). 마이그레이션 head = `0002_hyperv_connections` |

## 이 문서와 `05_deployment.md`의 관계

`docs/05_deployment.md`가 **배포판 공통 레퍼런스**다 — systemd 유닛 전문, nginx 설정 전문,
운영(백업·키 교체·로그), 문제 해결표가 거기에 있다. 이 문서는 그 위에
**RHEL 9 + 폐쇄망**에서만 달라지는 것을 처음부터 끝까지 한 흐름으로 정리한다.
겹치는 부분은 옮겨 적지 않고 `05 §N`으로 가리킨다 — 두 벌로 두면 갈라진다.

> **먼저 확인할 것**: `spec.md` CST-08(배포 환경·폐쇄망 여부)은 아직 `[TODO]`다.
> 이 문서는 폐쇄망을 전제로 한다 (D-021). 인터넷이 되는 서버라면 05를 그대로 따르는 편이 짧다.

---

## 1. 반입 물자

폐쇄망에서 가장 흔한 실패는 **설치를 시작한 뒤에 빠진 것을 발견하는 것**이다.
반입 전에 아래 4종을 모두 확보한다.

| # | 물자 | 크기 | 만드는 곳 |
|---|---|---|---|
| 1 | 소스 아카이브 (`wzoneportal.tar.gz`) | ~2MB | 개발 장비 (§1.1) |
| 2 | Python wheel 번들 | 55MB | 인터넷 가능 장비 (§1.2) |
| 3 | RPM 세트 (python3.11 · PostgreSQL · nginx) | 200~400MB | **동일 버전 RHEL 9** (§1.3) |
| 4 | TLS 인증서 + 키 | — | 사내 인증기관 |

### 1.1 소스 아카이브

`.git`·캐시·`.env`를 제외하고 만든다. **`.env`가 들어가면 개발용 키가 운영에 섞인다.**

```bash
git archive --format=tar.gz --prefix=wzoneportal/ -o wzoneportal.tar.gz HEAD
```

`git archive`는 `.gitignore` 대상과 미추적 파일을 자동으로 뺀다. 수동 `tar`를 쓴다면
`--exclude=.git --exclude=.env --exclude='*.pyc' --exclude=__pycache__ --exclude=.venv`를 붙인다.

> **wheel 번들은 `.gitignore` 대상이라 `git archive`에 포함되지 않는다** (D-021).
> §1.2에서 따로 만들어 함께 반입한다.

### 1.2 Python wheel 번들

`deploy/wheels/rhel9-py311-x86_64/README.md`의 재생성 절차를 따른다.
이미 만들어 두었다면 그 디렉토리를 그대로 반입한다 (runtime 42 · dev 21 · buildtools 4).

```bash
tar -czf wzoneportal-wheels.tar.gz deploy/wheels/rhel9-py311-x86_64/
```

> **`pip download`는 `--platform`을 줘도 의존성의 환경 마커를 *실행 중인* OS로 평가한다.**
> Windows에서 수집하면 Linux 전용 `uvloop`이 **빠진다** — 번들 README의 보정 절차를 반드시 거친다.
> 빠진 채로 반입하면 폐쇄망 안에서야 발견된다.

### 1.3 RPM 세트

**인터넷이 되는 동일 마이너 버전 RHEL 9 장비**에서 받는다. 9.4에서 받은 RPM을 9.2에 넣으면
의존성이 어긋난다. `cat /etc/redhat-release`로 양쪽을 먼저 맞춘다.

```bash
sudo dnf install -y dnf-plugins-core createrepo_c
mkdir -p ~/wzoneportal-rpms && cd ~/wzoneportal-rpms

# ① Python 3.11 — 시스템 기본 3.9와 별개 패키지다
dnf download --resolve --alldeps python3.11 python3.11-pip

# ② PostgreSQL — 둘 중 하나를 고른다 (§3.1)
#    (a) AppStream 모듈 16 — 폐쇄망에서는 이쪽이 간단하다.
#        module enable을 "먼저" 해야 한다. 안 하면 기본 스트림인 13이 받아진다 (검증 완료)
sudo dnf -y module enable postgresql:16
dnf download --resolve --alldeps postgresql-server postgresql-contrib
#    (b) PGDG 17 — 저장소를 먼저 추가한 장비에서
# sudo dnf install -y https://download.postgresql.org/pub/repos/yum/reporpms/EL-9-x86_64/pgdg-redhat-repo-latest.noarch.rpm
# sudo dnf -qy module disable postgresql
# dnf download --resolve --alldeps postgresql17-server postgresql17-contrib

# ③ nginx + 정책 도구
dnf download --resolve --alldeps nginx policycoreutils-python-utils

# 로컬 저장소로 묶는다 — 개별 rpm을 순서대로 설치하는 것보다 안전하다
createrepo_c .
cd .. && tar -czf wzoneportal-rpms.tar.gz wzoneportal-rpms/
```

`--resolve --alldeps`가 핵심이다. 이미 설치된 의존성까지 포함해야 대상 서버에서 빠지지 않는다.

> `postgresql-contrib`가 **`pg_trgm`을 담고 있다.** 이것이 빠지면 §5의
> `CREATE EXTENSION pg_trgm`에서 막히고, 폐쇄망에서는 즉시 보충할 수 없다.

> **`dnf -y module enable postgresql:16`을 다운로드 전에 실행한다.** 이것을 빠뜨리면
> RHEL 9의 기본 스트림인 **13**이 받아져 D-013의 하한(16)에 미달한다.
> 받은 파일 이름으로 확인한다 — `postgresql-server-16.x-...module+el9...` 여야 한다.
> AppStream이 제공하는 스트림은 **15 / 16 / 18**이며 **17은 없다.** 17이 필요하면 (b) PGDG다.

### 1.4 반입 후 검수

```bash
sha256sum -c checksums.txt        # 반입 전 생성한 것과 대조
tar -tzf wzoneportal.tar.gz | grep -c ''
ls deploy/wheels/rhel9-py311-x86_64/runtime/*.whl | wc -l    # 42
ls deploy/wheels/rhel9-py311-x86_64/buildtools/*.whl | wc -l # 4
```

---

## 2. 서버 요건과 사전 승인

서버 사양·신청 항목은 **05 §2·§3**과 같다. 폐쇄망에서 추가로 확인할 것:

| 확인 | 이유 |
|---|---|
| 포탈 서버 → **vCenter 443** 아웃바운드 | 수집 경로다. 막히면 연결 테스트가 1단계에서 실패한다 |
| 포탈 서버 → **SCVMM/Hyper-V 5986** 아웃바운드 | Hyper-V 연결을 쓸 때만 (계획 05) |
| 포탈 서버 → **DB 5432** | DB가 별도 서버인 경우 |
| NTP 동기화 | 수집 시각·감사 로그·JWT 만료가 전부 시각 기준이다 |
| DNS 또는 `/etc/hosts` | vCenter를 FQDN으로 등록하려면 이름 해석이 되어야 한다 |

---

## 3. 로컬 저장소 구성과 OS 패키지 설치

```bash
sudo tar -xzf wzoneportal-rpms.tar.gz -C /opt
sudo tee /etc/yum.repos.d/wzoneportal-local.repo >/dev/null <<'REPO'
[wzoneportal-local]
name=wzoneportal offline packages
baseurl=file:///opt/wzoneportal-rpms
enabled=1
gpgcheck=0
# PostgreSQL 16은 modular 패키지다. createrepo_c로 만든 로컬 저장소에는
# 모듈 메타데이터가 없어, 이 줄이 없으면 설치가 거부된다 (§3.2)
module_hotfixes=1
REPO

sudo dnf --disablerepo='*' --enablerepo=wzoneportal-local install -y \
    python3.11 python3.11-pip nginx policycoreutils-python-utils
python3.11 -V      # Python 3.11.x
```

`gpgcheck=0`은 사내에서 직접 만든 저장소이기 때문이다. 서명 검증이 필요하면
반입 전에 `rpm --import`로 RHEL GPG 키를 넣고 `gpgcheck=1`로 둔다.

### 3.1 PostgreSQL 설치 — 버전 선택

| 선택 | 명령 | 판단 기준 |
|---|---|---|
| **AppStream 16** | `dnf install postgresql-server postgresql-contrib` → `postgresql-setup --initdb` | 폐쇄망에서 **간단하다.** D-013의 하한(16)을 만족한다 |
| **PGDG 17** | `dnf install postgresql17-server postgresql17-contrib` → `/usr/pgsql-17/bin/postgresql-17-setup initdb` | D-013 표준. 저장소 RPM까지 반입해야 한다 |

```bash
# AppStream 16을 쓰는 경우
sudo dnf --disablerepo='*' --enablerepo=wzoneportal-local install -y postgresql-server postgresql-contrib
psql --version                              # psql (PostgreSQL) 16.x  ← 13.x면 §1.3을 다시 본다
sudo postgresql-setup --initdb
sudo systemctl enable --now postgresql
```

사내 DB 서버를 쓴다면 이 절을 건너뛰고 §5로 간다. 단 **`pg_trgm` 확장 설치 가능 여부**를
DBA와 먼저 확인한다 (05 §4.2).

### 3.2 modular 패키지 — 폐쇄망에서 반드시 걸리는 지점

RHEL 9의 PostgreSQL 15/16/18은 **modular 패키지**다 (파일 이름에 `.module+el9...`가 붙는다).
`createrepo_c`로 만든 로컬 저장소에는 **모듈 메타데이터가 없어서**, 그대로 설치하면 거부된다.

```
Error: No available modular metadata for modular package
No available modular metadata for modular package 'postgresql-server-16.14-1.module+el9...',
it cannot be installed on the system
```

원인이 드러나지 않는 메시지라 폐쇄망에서 시간을 많이 잡아먹는다.
**저장소 정의에 `module_hotfixes=1`을 넣으면 해결된다** (§3의 repo 파일에 이미 있다).

> Rocky Linux 9.8 컨테이너에서 확인했다 — `module_hotfixes` 없이는 위 오류로 실패하고,
> 넣으면 `postgresql-server-16.14`가 정상 설치된다. AppStream 13(non-modular)을 쓸 때는
> 이 설정이 필요 없지만, **13은 D-013의 하한에 미달한다.**

---

## 4. 서비스 계정과 소스 배치

```bash
sudo useradd --system --home-dir /opt/wzoneportal --shell /sbin/nologin wzoneportal
sudo mkdir -p /opt/wzoneportal
sudo tar -xzf wzoneportal.tar.gz -C /opt/wzoneportal --strip-components=1
sudo tar -xzf wzoneportal-wheels.tar.gz -C /opt/wzoneportal
sudo chown -R wzoneportal:wzoneportal /opt/wzoneportal
```

---

## 5. DB 계정과 데이터베이스

**superuser가 필요 없다.** 레시피와 근거(검증 결과 포함)는 **05 §4.2**에 있다. 요약:

```sql
-- postgres 계정으로 1회
CREATE ROLE portal LOGIN PASSWORD '<강한-비밀번호>' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE wzoneportal OWNER portal ENCODING 'UTF8';
```

접속 허용(`pg_hba.conf`)은 **05 §4.3**을 따른다 — `0.0.0.0/0`을 쓰지 않는다.

---

## 6. 가상환경과 의존성 (오프라인)

```bash
W=/opt/wzoneportal/deploy/wheels/rhel9-py311-x86_64

sudo -u wzoneportal python3.11 -m venv /opt/wzoneportal/.venv
sudo -u wzoneportal /opt/wzoneportal/.venv/bin/pip install \
    --no-index --find-links=$W/buildtools --upgrade pip setuptools wheel

cd /opt/wzoneportal
sudo -u wzoneportal /opt/wzoneportal/.venv/bin/pip install \
    --no-index --find-links=$W/buildtools --find-links=$W/runtime -e /opt/wzoneportal
```

> **`-e`(editable)를 반드시 쓴다.** 일반 설치는 `static/`을 못 찾아 **오류 없이 UI만 404**가 된다
> (05 §5.3). `migrations/`·`alembic.ini`도 소스 트리에 그대로 있어야 한다.
>
> **`--find-links=$W/buildtools`가 두 번째 명령에도 필요하다.** `pip install -e .`는 PEP 517
> 빌드 격리에서 setuptools를 새로 받으려 하며, 폐쇄망에서는 그 시점에 실패한다.

확인:

```bash
sudo -u wzoneportal /opt/wzoneportal/.venv/bin/python -c \
  "from src.main import STATIC_DIR; print(STATIC_DIR, STATIC_DIR.is_dir())"
# /opt/wzoneportal/static True   ← .env가 아직 없으면 ValidationError가 먼저 난다. §7 뒤에 다시 실행한다
```

서버에서 테스트까지 돌릴 경우에만:

```bash
sudo -u wzoneportal /opt/wzoneportal/.venv/bin/pip install \
    --no-index --find-links=$W/dev -r $W/requirements-dev.txt
```

---

## 7. 설정 (`.env`)

키 생성·항목표·주의사항은 **05 §6** 그대로다. 폐쇄망에서 특히 중요한 것만 옮긴다.

```bash
sudo -u wzoneportal cp /opt/wzoneportal/.env.example /opt/wzoneportal/.env
sudo -u wzoneportal vi /opt/wzoneportal/.env
sudo chmod 600 /opt/wzoneportal/.env
```

| 키 | 운영값 |
|---|---|
| `PORTAL_DATABASE_URL` | `postgresql+asyncpg://portal:<pw>@127.0.0.1:5432/wzoneportal` — **`+asyncpg` 필수** |
| `PORTAL_COOKIE_SECURE` | `true` (HTTPS 전제) |
| `PORTAL_API_HOST` / `PORTAL_API_PORT` | `127.0.0.1` / `8080` — 외부에 직접 노출하지 않는다 |
| `PORTAL_CREDENTIAL_ENCRYPTION_KEY` | 05 §6.1로 생성. **분실 시 복구 불가** |

> **`PORTAL_CREDENTIAL_ENCRYPTION_KEY`를 DB 백업과 다른 곳에 보관한다.**
> 이 키가 없으면 등록된 연결의 비밀번호를 영원히 풀 수 없고, 전 연결을 재등록해야 한다.
> 폐쇄망에서는 외부 비밀 관리 서비스를 못 쓰므로 **보관 위치를 사람이 정해 문서로 남긴다.**

`.env`는 **작업 디렉토리 기준**으로 읽힌다 — systemd 유닛에 `WorkingDirectory=`가 없으면
기동이 `ValidationError: database_url Field required`로 실패한다 (05 §6.3).


---

## 8. 스키마 생성

```bash
cd /opt/wzoneportal      # alembic.ini가 상대 경로를 쓴다
sudo -u wzoneportal /opt/wzoneportal/.venv/bin/python -m alembic upgrade head
```

**접속 URL은 `alembic.ini`가 아니라 `.env`에서 읽는다** (05 §7.1). 따라서 §7이 끝난 뒤에만
실행할 수 있고, URL을 인자로 넘기는 옵션은 없다.

확인:

```bash
psql "postgresql://portal:<pw>@127.0.0.1/wzoneportal" -c "\dt"
# alembic_version, audit_events, connections, resource_identities,
# user_connection_scopes, users, virtual_machines   ← 7개

psql "postgresql://portal:<pw>@127.0.0.1/wzoneportal" -c "SELECT extname FROM pg_extension"
# plpgsql, pg_trgm   ← pg_trgm이 없으면 §5의 권한 문제이거나 postgresql-contrib 누락(§1.3)이다

cd /opt/wzoneportal
sudo -u wzoneportal .venv/bin/python -m alembic current   # 0002_hyperv_connections (head)
```

### 8.1 테이블 구성

| 테이블 | 내용 | 채워지는 경로 |
|---|---|---|
| `connections` | 하이퍼바이저 연결 + **암호화된 자격증명** | API 등록만 (§9.3) |
| `virtual_machines` | 수집된 VM 인벤토리 | **수집만** — 직접 넣지 않는다 (§9.3) |
| `resource_identities` | CI 식별 키 (FR-303) | 수집이 파생 생성 |
| `users` | 계정 (bcrypt 해시) | API 가입/승인 (§9.3) |
| `user_connection_scopes` | 조회 범위 (기본 거부) | API 부여 (§9.3) |
| `audit_events` | 감사 기록 (FR-1004) | 애플리케이션이 append |
| `alembic_version` | 적용된 리비전 | alembic |

### 8.2 DBA가 대행하는 경우

DB에 접속하지 않고 실행할 SQL만 만들어 전달한다 (05 §7.3).

```bash
cd /opt/wzoneportal
sudo -u wzoneportal .venv/bin/python -m alembic upgrade head --sql > /tmp/wzoneportal_schema.sql
```

`.env`는 여전히 필요하다 — URL로 SQL 방언을 정한다. 접속은 하지 않는다.
생성된 SQL에는 `CREATE EXTENSION pg_trgm`과 `alembic_version` 기록까지 들어 있어,
DBA 실행 후 `alembic current`로 상태를 확인할 수 있다.

---

## 9. 데이터 마이그레이션

세 가지가 서로 다른 작업이다. 필요한 것만 수행한다.

| 구분 | 상황 | 절 |
|---|---|---|
| **A. 스키마 마이그레이션** | 포탈 버전을 올려 테이블 구조가 바뀔 때 | §9.1 |
| **B. DB 이관** | 검증 서버 → 운영 서버, DB 서버 교체 | §9.2 |
| **C. 외부 데이터 초기 적재** | 기존 목록(엑셀·CMDB)을 포탈에 넣을 때 | §9.3 |

### 9.1 스키마 마이그레이션 (버전 업그레이드)

폐쇄망에서는 새 소스와 **새 wheel 번들**을 함께 반입해야 한다 — 의존성이 추가되었을 수 있다.

```bash
sudo systemctl stop wzoneportal

# ① 백업 먼저 — 되돌릴 수 없는 작업이다
pg_dump "postgresql://portal:<pw>@127.0.0.1/wzoneportal" -Fc -f /var/backups/before-upgrade.dump

# ② 소스 교체
sudo -u wzoneportal tar -xzf wzoneportal-new.tar.gz -C /opt/wzoneportal --strip-components=1

# ③ 의존성 반영 (오프라인)
W=/opt/wzoneportal/deploy/wheels/rhel9-py311-x86_64
cd /opt/wzoneportal
sudo -u wzoneportal .venv/bin/pip install \
    --no-index --find-links=$W/buildtools --find-links=$W/runtime -e /opt/wzoneportal

# ④ 스키마 반영 — 기동보다 먼저다
sudo -u wzoneportal .venv/bin/python -m alembic upgrade head

sudo systemctl start wzoneportal
curl -sk https://portal.example.internal/api/v1/health
```

**순서가 중요하다.** 마이그레이션을 올리고 나서 프로세스를 재기동한다. 반대로 하면
새 코드가 아직 없는 컬럼을 조회한다.

적용 전에 무엇이 실행될지 보려면:

```bash
sudo -u wzoneportal .venv/bin/python -m alembic current    # 지금 DB에 적용된 리비전
sudo -u wzoneportal .venv/bin/python -m alembic heads      # 코드가 요구하는 리비전
sudo -u wzoneportal .venv/bin/python -m alembic upgrade head --sql   # 실행될 SQL (적용하지 않음)
```

되돌릴 때:

```bash
sudo -u wzoneportal .venv/bin/python -m alembic downgrade -1
```

> **`downgrade`는 데이터를 지운다.** `0002` → `0001`은 Hyper-V 연결 컬럼 3개를 **DROP**하므로
> 등록된 Hyper-V/SCVMM 연결의 인증 정보가 사라진다. `0001`을 되돌리면 테이블 전체가 사라진다.
> 백업(①)을 확인하기 전에는 실행하지 않는다.

### 9.2 DB 이관 (pg_dump / pg_restore)

검증 서버의 데이터를 운영 서버로 옮기거나, DB 서버를 교체할 때다.

```bash
# ① 쓰기를 멈춘다
sudo systemctl stop wzoneportal

# ② 덤프
pg_dump "postgresql://portal:<pw>@<구서버>/wzoneportal" -Fc -f wzoneportal.dump

# ③ 새 서버에 §5대로 계정과 "빈 DB"를 만든다.
#    alembic upgrade head를 미리 실행하지 않는다 — 덤프에 스키마와 alembic_version이
#    들어 있어 복원이 "already exists"로 실패한다.

# ④ 복원
pg_restore --no-owner -d "postgresql://portal:<pw>@<신서버>/wzoneportal" wzoneportal.dump

# ⑤ 확인 후 기동
cd /opt/wzoneportal
sudo -u wzoneportal .venv/bin/python -m alembic current    # 0002_hyperv_connections면 정상
sudo systemctl start wzoneportal
```

> **`PORTAL_CREDENTIAL_ENCRYPTION_KEY`가 데이터와 함께 가야 한다.** `connections`의 비밀번호는
> 이 키로만 복호화된다. 키가 다른 서버에 데이터만 복원하면 **목록·조회는 정상인데 수집만 전부
> 실패**하므로 원인을 찾기 어렵다. `PORTAL_JWT_SECRET`은 달라도 된다 — 재로그인하면 된다.

버전이 다른 서버로 옮겼다면 복원 뒤 `alembic upgrade head`가 부족한 리비전만 이어서 적용한다.

폐쇄망 사이를 매체로 옮긴다면: **덤프에는 자격증명 암호문·계정 해시·감사 로그가 들어 있고,
인벤토리 자체가 공격 표면 정보다** (NFR-206). 매체를 암호화하고 이동 후 삭제한다.

### 9.3 외부 데이터 초기 적재

기존 엑셀·CMDB의 목록을 포탈에 넣는 경우다. **대상마다 넣는 방법이 다르다.**

| 대상 | SQL 직접 INSERT | 이유 |
|---|---|---|
| `connections` | **불가** | `password_encrypted`가 AES-GCM 암호문(`{키버전}${nonce}${암호문}`)이다. 평문을 넣으면 등록은 되지만 **수집이 전부 복호화 실패**한다 |
| `users` | **불가** | `password_hash`가 bcrypt다. 평문을 넣으면 로그인이 안 된다 |
| `virtual_machines` | **금지** | 수집이 유일한 원천이다 — ③ 참조 |
| `user_connection_scopes` | 가능하나 비권장 | UUID를 손으로 맞춰야 하고 감사 로그가 남지 않는다 |

**결론: 적재는 API로 한다.** 암호화·해시·감사 기록이 애플리케이션 계층에 있기 때문이다.

#### 인증 준비 — 쿠키 전용이다

API는 **세션 쿠키만** 받는다. `Authorization: Bearer`는 지원하지 않는다
(`src/api/deps.py` — D-014). 스크립트도 쿠키를 써야 한다.

```bash
# PORTAL_COOKIE_SECURE=true면 쿠키에 Secure가 붙어 curl이 http:// 요청에는 보내지 않는다.
# 반드시 nginx 경유 https:// 주소로 호출한다. 127.0.0.1:8080(HTTP)으로는 로그인 직후 401이 난다.
BASE=https://portal.example.internal

curl -sk -c /tmp/portal.jar -X POST $BASE/api/v1/auth/login \
     -H 'Content-Type: application/json' \
     --data-binary @login.json
```

이후 모든 호출에 `-b /tmp/portal.jar`를 붙인다. 비밀번호는 명령줄이 아니라 파일(`login.json`,
`chmod 600`)로 넘긴다 — 명령줄 인자는 `ps`로 다른 사용자에게 보인다.
작업이 끝나면 `shred -u /tmp/portal.jar login.json`으로 지운다.

#### ① 연결 일괄 등록

CSV(`display_name,address,username,password`)를 읽어 등록한다. `jq`로 JSON을 만들면
따옴표·특수문자가 있는 비밀번호도 안전하다.

```bash
while IFS=, read -r name addr user pass; do
  jq -n --arg n "$name" --arg a "$addr" --arg u "$user" --arg p "$pass" \
     '{kind:"vcenter", display_name:$n, address:$a, port:443,
       username:$u, password:$p, verify_tls:true}' \
  | curl -sk -b /tmp/portal.jar -X POST "$BASE/api/v1/connections" \
         -H 'Content-Type: application/json' --data-binary @- \
         -o /dev/null -w "$name -> %{http_code}\n"
done < connections.csv        # 201이면 성공
```

- `kind`가 판별자다 — `vcenter` / `hyperv-host` / `hyperv-cluster` / `scvmm`.
  WinRM 계열은 `port`(기본 5986)·`protocol`(`http`/`https`)·`auth_method`(`ntlm`/`kerberos`/`credssp`)가
  더 필요하고, 경로 A는 `session_configuration`(JEA 구성 이름)을 넣는다.
- `(address, username)`에 UNIQUE 제약이 있다 — 중복은 409로 떨어지고 나머지는 계속 진행된다.
- **CSV에 평문 비밀번호가 있다.** `chmod 600`으로 만들고 작업 후 `shred -u`로 지운다.

> **연결 수정 API가 없다** (Step 1 — 05 §13). 잘못 등록하면 삭제 후 재등록인데,
> **VM이 수집된 연결은 FK 제약(`ondelete=RESTRICT`)으로 삭제되지 않는다.**
> 그래서 순서가 중요하다 — **등록 → `POST /api/v1/connections/test`로 확인 → 그 다음 수집**.
> 대량 등록은 1건을 끝까지 확인한 뒤 나머지를 돌린다.

> **SCVMM·Hyper-V 호스트 연결은 아직 등록하지 않는다.** 어댑터는 구현되었지만 실환경 검증 전이고,
> JEA 배포·SCVMM 읽기 전용 계정이 준비되기 전에는 등록 대상이 아니다 (CLAUDE.md, 계획 05 §4.3).

#### ② 사용자 일괄 생성

가입은 **인증 없이 호출할 수 있는 공개 엔드포인트**이고 `pending` 상태로 들어간다.
관리자가 승인해야 활성이 된다.

```bash
# 가입 신청 — 202. 비밀번호는 10자 이상이어야 한다
curl -sk -X POST "$BASE/api/v1/auth/register" -H 'Content-Type: application/json' \
     --data-binary @user1.json

# 관리자가 목록에서 user_id를 확인하고 승인
curl -sk -b /tmp/portal.jar "$BASE/api/v1/users?status=pending"
curl -sk -b /tmp/portal.jar -X POST "$BASE/api/v1/users/<user_id>/approve"

# 조회 범위 부여 — 기본은 거부다. 주지 않으면 로그인은 되는데 목록이 비어 보인다
curl -sk -b /tmp/portal.jar -X PUT "$BASE/api/v1/users/<user_id>/scopes" \
     -H 'Content-Type: application/json' -d '{"connection_ids":["<connection_uuid>"]}'
```

관리자가 비밀번호를 정해 배포하려면 `POST /api/v1/users/<id>/reset-password`가 임시 비밀번호를
발급하고 `must_change_password`를 세운다 — 사용자는 최초 로그인 시 변경해야 한다 (FR-1008).

> Step 1~2 동안은 **가입 승인을 시작하지 않고 관리자 1~2명으로 운영한다** (05 §13).
> 계정 일괄 생성은 실환경 검증이 끝난 뒤의 작업이다.

#### ③ VM 인벤토리는 적재하지 않는다

기존 엑셀의 VM 목록을 `virtual_machines`에 넣고 싶어지지만, **넣으면 안 된다.**

- CI 식별 1순위가 `(connection_id, native_id)`다 (FR-303, `uq_vm_native` 제약). 손으로 만든 행은
  하이퍼바이저의 실제 `native_id`(vCenter의 `vm-1234` 같은 MoRef)와 다르므로, 첫 수집에서
  **같은 VM이 두 레코드로 생긴다.** 중복 레코드 생성은 결함으로 규정되어 있다
  (CLAUDE.md Key Constraints — 자원 식별 일관성).
- `first_seen_at`·`last_seen_at`이 거짓이 되어 신선도 표시와 미발견 처리(Step 3, FR-307)가 무너진다.
- `resource_identities`가 함께 만들어지지 않아 이후 매칭 규칙(BIOS UUID·MAC)이 어긋난다.

**연결만 등록하고 수집을 돌리면 목록은 자동으로 채워진다.**

```bash
curl -sk -b /tmp/portal.jar -X POST "$BASE/api/v1/connections/<connection_id>/collect"   # 202
```

엑셀과 수집 결과를 대조하고 싶다면, 적재가 아니라 **비교**로 다룬다 —
`GET /api/v1/virtual-machines`로 받은 목록과 엑셀을 오프라인에서 맞춰 보고,
차이가 나면 그것이 실측 결과다 (ROADMAP §15).

#### ④ 소유자·환경 같은 관리 메타데이터

**현재 스키마에 컬럼 자체가 없다.** 메타데이터는 **Step 7**에서 별도 테이블로 도입된다
(ROADMAP §22, 계획 07 §6). 지금 적재할 대상이 아니며, 엑셀은 그때까지 보관한다.

Step 7 설계의 전제가 **수집이 메타데이터를 덮어쓰지 않는 것**이다 (FR-602). 그때
`(connection_id, native_id)` 또는 BIOS UUID를 키로 일괄 적재하게 되므로,
**엑셀에 그 키를 미리 채워 두면 이후 작업이 쉬워진다.**

---

## 10. systemd 서비스

유닛 파일 전문과 옵션별 근거는 **05 §8**에 있다. 그대로 쓴다. RHEL에서 확인할 것만:

```bash
sudo vi /etc/systemd/system/wzoneportal.service    # 05 §8의 내용
sudo systemctl daemon-reload
sudo systemctl enable --now wzoneportal
sudo systemctl status wzoneportal
```

| 항목 | RHEL 9에서의 확인 |
|---|---|
| `WorkingDirectory=/opt/wzoneportal` | 없으면 `.env`를 못 찾아 기동 실패한다 (§7) |
| `After=postgresql.service` | DB가 같은 서버일 때. **AppStream 16은 `postgresql`, PGDG 17은 `postgresql-17`**로 유닛 이름이 다르다 |
| `ProtectSystem=strict` | `/opt`이 읽기 전용이 된다. 애플리케이션은 파일을 쓰지 않으므로 정상이다 |
| SELinux | systemd 서비스는 `unconfined`로 뜨므로 추가 정책이 필요 없다. nginx 쪽만 §12에서 손댄다 |

---

## 11. nginx와 TLS

설정 전문은 **05 §9**에 있다. RHEL 9에서 다른 점:

```bash
sudo mkdir -p /etc/nginx/conf.d
sudo vi /etc/nginx/conf.d/wzoneportal.conf     # 05 §9의 내용
sudo nginx -t
sudo systemctl enable --now nginx
```

- 인증서는 `/etc/pki/tls/certs`·`/etc/pki/tls/private`에 둔다 (Debian 계열의 `/etc/ssl`이 아니다).
  키는 `chmod 600`, 소유자 `root`.
- RHEL 기본 `/etc/nginx/nginx.conf`에 이미 `server { listen 80 }` 블록이 있다.
  **제거하거나 443으로 리디렉션**하지 않으면 80이 열린 채로 남는다.

---

## 12. 방화벽과 SELinux

```bash
# 인바운드는 443만 — 8080은 열지 않는다
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
sudo firewall-cmd --list-all

# nginx가 uvicorn(127.0.0.1:8080)으로 프록시하려면 필요하다
sudo setsebool -P httpd_can_network_connect 1
getsebool httpd_can_network_connect        # --> on
```

| 방향 | 대상 | 포트 |
|---|---|---|
| 인바운드 | 사내 운영자망 | 443 |
| 아웃바운드 | vCenter | 443 |
| 아웃바운드 | SCVMM / Hyper-V 호스트 | 5986 (Hyper-V 연결을 쓸 때만) |
| 아웃바운드 | DB 서버 | 5432 (별도 서버인 경우) |

`httpd_can_network_connect`를 켜지 않으면 nginx가 **502**를 반환한다. 이때
`sudo ausearch -m avc -ts recent`에 거부 기록이 남으므로 원인을 확인할 수 있다.

인증서를 기본 경로 밖에 두면 컨텍스트를 맞춰야 한다:

```bash
sudo semanage fcontext -a -t cert_t "/etc/pki/wzoneportal(/.*)?"
sudo restorecon -Rv /etc/pki/wzoneportal
```

---

## 13. 최초 기동과 관리자 계정

절차와 주의사항은 **05 §10**과 같다. 요약:

1. `.env`에 `PORTAL_BOOTSTRAP_ADMIN_USERNAME`·`PORTAL_BOOTSTRAP_ADMIN_PASSWORD`를 넣고 기동한다.
   관리자가 0명일 때만 생성된다 — 워커가 2개여도 1명만 만들어진다.
2. 로그에서 생성 메시지를 확인한다: `journalctl -u wzoneportal | grep -i bootstrap`
3. **두 값을 `.env`에서 지우고 재기동한다.** 남겨 두면 평문 관리자 비밀번호가 파일에 남는다.
4. 웹에서 로그인해 비밀번호를 변경한다.

---

## 14. 배포 검증

전체 12항목은 **05 §11**에 있다. 폐쇄망 설치 직후 최소한 이 순서로 확인한다.

```bash
# 1. 프로세스와 DB
curl -sk https://portal.example.internal/api/v1/health
# {"status":"healthy","checks":{"database":{"ok":true,...}}}

# 2. 정적 UI — 404면 §6의 -e 누락이다
curl -sk -o /dev/null -w '%{http_code}\n' https://portal.example.internal/login.html   # 200

# 3. 인증 없이 조회 API → 401이어야 한다
curl -sk -o /dev/null -w '%{http_code}\n' https://portal.example.internal/api/v1/virtual-machines   # 401

# 4. 쿠키 속성
curl -sik -X POST https://portal.example.internal/api/v1/auth/login \
     -H 'Content-Type: application/json' --data-binary @login.json | grep -i set-cookie
# portal_session=...; HttpOnly; Secure; SameSite=strict; Path=/

# 5. 감사 로그에 실제 클라이언트 IP가 남는지 — 전부 127.0.0.1이면 05 §8.1을 본다
psql "postgresql://portal:<pw>@127.0.0.1/wzoneportal" \
     -c "SELECT occurred_at, actor, action, client_ip FROM audit_events ORDER BY 1 DESC LIMIT 5"

# 6. 비밀번호가 평문이 아닌지
psql ... -c "SELECT username, left(password_hash,7) FROM users"      # $2b$12$ 형태
psql ... -c "SELECT display_name, left(password_encrypted,2) FROM connections"   # 1$ 형태
```

---

## 15. 폐쇄망 특유의 문제 해결

일반 증상표는 **05 §15**에 있다. 여기서는 오프라인 설치에서만 나오는 것을 다룬다.

| 증상 | 원인 | 조치 |
|---|---|---|
| `pip install -e .`가 `Getting requirements to build editable`에서 멈추고 실패 | PEP 517 빌드 격리가 setuptools를 받으려 함 | `--find-links=$W/buildtools`를 함께 준다 (§6) |
| `No matching distribution found for uvloop` | Windows에서 wheel을 받아 Linux 전용 의존이 빠짐 | 번들 README의 보정 절차로 재수집 (§1.2) |
| `ERROR: ... is not a supported wheel on this platform` | venv가 Python 3.11이 아님 | `python3.11 -m venv`로 다시 만든다. `python3`은 3.9다 |
| `Error: No available modular metadata for modular package` | 로컬 저장소에 모듈 메타데이터가 없음 | repo 정의에 `module_hotfixes=1` (§3.2) |
| `psql --version`이 13.x | 다운로드 전에 `module enable postgresql:16`을 안 함 | §1.3에서 다시 받는다. D-013 하한 미달이다 |
| `dnf`가 미러에 접속을 시도하며 지연 | 기본 저장소가 살아 있음 | `--disablerepo='*' --enablerepo=wzoneportal-local` (§3) |
| `CREATE EXTENSION pg_trgm` 실패 | `postgresql-contrib` 미반입 또는 DB 소유자 아님 | §1.3 / 05 §4.2 |
| 로그인 스크립트가 200을 받는데 다음 호출이 401 | `Secure` 쿠키를 `http://`로 호출 | `https://` (nginx 경유)로 호출한다 (§9.3) |
| nginx 502, 애플리케이션은 정상 | SELinux `httpd_can_network_connect` off | §12 |
| 연결 테스트가 `tls_valid`에서 실패 | vCenter 자체 서명 인증서 | 등록 시 TLS 검증 해제. **운영 인증서 도입 전까지의 임시 조치다** |

---

## 16. 설치 체크리스트

**반입 전**

- [ ] 소스 아카이브 (`.env` 미포함 확인)
- [ ] wheel 번들 — `uvloop` 포함, `colorama`·`sspilib` 제거 확인
- [ ] RPM 세트 — **대상 서버와 같은 RHEL 마이너 버전**에서 수집, `postgresql-contrib` 포함
- [ ] TLS 인증서·키
- [ ] 체크섬 파일

**설치**

- [ ] `python3.11 -V` 확인 (시스템 `python3`은 3.9)
- [ ] `psql --version` = **16.x 이상** (13.x면 D-013 하한 미달 — §1.3)
- [ ] PostgreSQL 기동, `portal` 역할 + `wzoneportal` DB (소유자 지정)
- [ ] `pip install -e .` 완료, `STATIC_DIR ... True`
- [ ] `.env` 작성 후 `chmod 600`, `PORTAL_COOKIE_SECURE=true`
- [ ] **`PORTAL_CREDENTIAL_ENCRYPTION_KEY`를 DB 백업과 분리 보관** — 보관 위치 문서화
- [ ] `alembic upgrade head` → 테이블 7개 + `pg_trgm`
- [ ] `alembic current` = `0002_hyperv_connections`
- [ ] systemd 기동, `enable` 확인
- [ ] nginx + TLS, 80 미개방 확인
- [ ] `firewall-cmd --list-all`에 443만
- [ ] `getsebool httpd_can_network_connect` = on
- [ ] 부트스트랩 관리자 생성 후 **`.env`에서 두 값 제거 + 재기동**
- [ ] §14 검증 6항목 통과

**운영 인계**

- [ ] 백업 스케줄 (05 §12.2) — 덤프와 암호화 키를 다른 매체에
- [ ] 실환경 검증 전에는 사내에 공개하지 않는다 (ROADMAP §3)
- [ ] SCVMM·Hyper-V 연결은 JEA·계정 준비 전까지 등록하지 않는다 (계획 05 §4.3)
