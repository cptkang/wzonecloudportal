# 05. Linux 서버 배포 가이드 (Step 1)

> 작성일: 2026-08-07
> 대상: `plans/ROADMAP.md` Step 1 구현분
> 관련 결정: D-013(PostgreSQL), D-014(인증·쿠키), D-008·계획 10(자격증명), D-017(트랜잭션)

## 1. 이 문서의 범위

> **RHEL 9 + 폐쇄망이면 `docs/07_rhel_install_guide.md`를 먼저 본다.**
> 이 문서는 배포판 공통 레퍼런스다 — systemd 유닛·nginx 설정·운영·문제 해결의 원본이 여기에 있고,
> 07은 RHEL 9 오프라인 설치 흐름과 데이터 적재 방법을 다루며 세부는 이 문서를 가리킨다 (D-021).

Step 1 구현분을 사내 Linux 서버에 올려 **Step 2(실환경 적용·실측)를 수행할 수 있는 상태**로
만드는 절차다. 운영 개시(전사 공개)는 Step 2 완료 이후다 — 이유는 §13.

### 1.1 배포되는 것 / 아직 없는 것

| 있음 | 없음 (도입 Step) |
|---|---|
| API 서버 + 정적 UI (한 프로세스) | 수집 워커 프로세스 (Step 3) |
| PostgreSQL | Redis (Step 3) |
| 수동 수집 (`[지금 수집]`) | 주기 수집 스케줄 (Step 3) |
| 인증·계정·조회 범위 | 외부 인증(LDAP/SSO)·API 키 (Step 8) |
| 감사 로그 **기록** | 감사 로그 **조회 화면** (Step 8) |

**프로세스는 하나다.** `plans/06` Part B의 워커(`python -m src.main --mode worker`)는 Step 3에서
생기므로, 지금은 API 서버만 띄운다.

### 1.2 구성도

```
     사내망 (HTTPS)
          │
          ▼
   ┌──────────────┐   127.0.0.1:8080    ┌──────────────┐
   │    nginx     │ ──────────────────► │   uvicorn    │
   │  TLS 종료    │   X-Forwarded-For   │  src.main:app│
   └──────────────┘                     └──────┬───────┘
                                               │ 5432
                                        ┌──────▼───────┐        443
                                        │  PostgreSQL  │   ┌─────────┐
                                        │   17 또는 16 │   │ vCenter │◄── 수집 (읽기 전용)
                                        └──────────────┘   └─────────┘
```

vCenter로 향하는 443은 **포탈 서버에서 나가는** 방향이다 (CST-07). vCenter가 포탈로 접속하지 않는다.

---

## 2. 착수 전에 신청할 것

**승인이 구현보다 오래 걸린다.** 아래 4건은 서버 준비와 **동시에** 신청한다
(ROADMAP §26 리스크 1행).

| # | 항목 | 없으면 막히는 것 |
|---|---|---|
| 1 | **vCenter 읽기 전용 계정** (Read-Only 역할) | Step 2 전체 |
| 2 | **포탈 서버 → vCenter 443 방화벽** (CST-07) | 연결 테스트 1단계(`reachable`)부터 실패 |
| 3 | **DB 계정 권한** — 데이터베이스 소유 또는 동등 (§4.2) | 마이그레이션 실행 불가 |
| 4 | **내부 도메인 + TLS 인증서** | `PORTAL_COOKIE_SECURE=true`로 못 켜고, 켜면 로그인이 안 된다 (§15) |

3번은 ROADMAP §15.4의 17·18번 검증 항목과 같다. **§4.2의 레시피로 최소 권한이 충분함을 확인해
두었으니**, 사내 DB 정책이 그 레시피를 허용하는지만 확인하면 된다.

---

## 3. 서버 요건

| 항목 | 값 | 근거 |
|---|---|---|
| OS | RHEL/Rocky 9 또는 Ubuntu 22.04 LTS 이상 | systemd 기준으로 작성 |
| Python | **3.11 이상** | `pyproject.toml` `requires-python`. `StrEnum`·`datetime.UTC` 사용 |
| PostgreSQL | **17 표준 / 16 하한** | D-013 §6 — 14는 2026-11 지원 종료 |
| CPU / RAM | 2 vCPU / 4 GB (시작값) | 아래 참조 |
| 디스크 | OS 외 20 GB | 인벤토리는 텍스트라 작다. 로그·백업이 대부분 |

> **사이징을 지금 확정할 수 없다.** 관리 규모(NFR-104)와 수집 소요 시간(NFR-105)이 `[TODO]`이고,
> 이를 실측하는 것이 Step 2의 목적이다 (ROADMAP §15.3). 위 값은 **측정용 시작값**이며,
> Step 2에서 VM 총 건수와 수집 시간을 재고 조정한다.

### 3.1 배포판별 Python 3.11 확보

```bash
# RHEL / Rocky 9  (기본 3.9)
sudo dnf install -y python3.11 python3.11-devel gcc

# Ubuntu 22.04  (기본 3.10)
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update && sudo apt install -y python3.11 python3.11-venv python3.11-dev build-essential

# Ubuntu 24.04는 기본 3.12라 그대로 쓴다
```

`gcc`/`build-essential`은 `bcrypt`·`asyncpg` 휠이 없는 플랫폼에서 필요하다. 휠이 있으면 쓰이지 않는다.

---

## 4. PostgreSQL 준비

### 4.1 설치

사내 DB 서버가 이미 있으면 §4.2만 수행한다. 포탈 서버에 함께 두는 경우:

```bash
# RHEL / Rocky 9 — PGDG 저장소
sudo dnf install -y https://download.postgresql.org/pub/repos/yum/reporpms/EL-9-x86_64/pgdg-redhat-repo-latest.noarch.rpm
sudo dnf -qy module disable postgresql
sudo dnf install -y postgresql17-server
sudo /usr/pgsql-17/bin/postgresql-17-setup initdb
sudo systemctl enable --now postgresql-17
```

```bash
# Ubuntu 22.04 / 24.04 — PGDG 저장소 (배포판 기본은 14·16이라 저장소를 추가한다)
sudo apt install -y postgresql-common
sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
sudo apt install -y postgresql-17
```

개발·검증 단계에서는 컨테이너로 대신할 수 있다 (`CLAUDE.md` 개발 환경과 같은 구성):

```bash
docker run -d --name wzoneportal-db -e POSTGRES_PASSWORD=devpass -e POSTGRES_USER=portal \
    -e POSTGRES_DB=wzoneportal -p 55432:5432 postgres:16-alpine
```

> 컨테이너의 `POSTGRES_USER` 계정은 **superuser다.** §4.2 최소 권한 레시피가 통하는지의 검증
> (ROADMAP §15.4-17·18)은 컨테이너로 할 수 없고, 운영 DB는 반드시 §4.2대로 만든다.
> 컨테이너를 지우면 데이터도 사라지므로 보존이 필요하면 볼륨(`-v <이름>:/var/lib/postgresql/data`)을 붙인다.

### 4.2 계정과 데이터베이스 — 최소 권한

**superuser가 필요 없다.** `pg_trgm`은 PostgreSQL 13부터 `trusted = true` 확장이라,
**데이터베이스를 소유한 일반 계정**이 만들 수 있다.

```sql
-- superuser(postgres)로 1회 실행
CREATE ROLE portal LOGIN PASSWORD '<강한-비밀번호>'
    NOSUPERUSER NOCREATEDB NOCREATEROLE;

-- 소유자로 지정하는 것이 핵심이다.
-- PostgreSQL 15부터 public 스키마의 CREATE 권한은 소유자에게만 있다.
CREATE DATABASE wzoneportal OWNER portal ENCODING 'UTF8';
```

> **검증 완료 (2026-08-07)**: `NOSUPERUSER NOCREATEDB NOCREATEROLE` 역할이 소유한 DB에서
> `alembic upgrade head`가 **테이블 6개 + `pg_trgm` 확장까지 전부 성공**했다.
> ROADMAP §15.4의 17·18번은 이 레시피를 쓸 수 있는 한 해소된다.
>
> **사내 정책이 DB 소유권을 주지 않는 경우** 다음 둘 중 하나가 필요하다.
> ① superuser가 `CREATE EXTENSION pg_trgm`을 미리 실행하고 `GRANT CREATE ON SCHEMA public TO portal`
> ② 마이그레이션을 DBA가 대행 — §7.3의 오프라인 SQL을 전달한다 (이 경우 배포 절차가 달라지므로 ROADMAP §15.4-18에 기록한다)

### 4.3 접속 허용

DB가 별도 서버면 `pg_hba.conf`에 포탈 서버만 허용한다. **`0.0.0.0/0`을 쓰지 않는다.**

```conf
# TYPE  DATABASE      USER    ADDRESS              METHOD
hostssl wzoneportal   portal  10.x.x.x/32          scram-sha-256
```

같은 서버면 기본 `local`/`127.0.0.1` 설정으로 충분하고 외부 노출이 없다.

---

## 5. 애플리케이션 설치

### 5.1 서비스 계정과 디렉토리

```bash
sudo useradd --system --home-dir /opt/wzoneportal --shell /usr/sbin/nologin wzoneportal
sudo mkdir -p /opt/wzoneportal
```

로그인 셸이 없는 시스템 계정을 쓴다. **이 서버는 다수 하이퍼바이저의 자격증명을 한곳에 모으므로,
침해되면 조직 전체가 영향을 받는다** (계획 10 §1).

### 5.2 소스 배치

```bash
# 저장소에서 받거나 아카이브를 푼다
sudo -u wzoneportal git clone <저장소> /opt/wzoneportal
# 또는: sudo tar -xzf wzoneportal.tar.gz -C /opt/wzoneportal --strip-components=1

sudo chown -R wzoneportal:wzoneportal /opt/wzoneportal
```

### 5.3 가상환경과 의존성

```bash
sudo -u wzoneportal python3.11 -m venv /opt/wzoneportal/.venv
sudo -u wzoneportal /opt/wzoneportal/.venv/bin/pip install --upgrade pip
sudo -u wzoneportal /opt/wzoneportal/.venv/bin/pip install -e /opt/wzoneportal
```

> **`-e`(editable)를 반드시 쓴다.** 일반 설치(`pip install .`)는 `src/`만 site-packages로 복사하는데,
> 정적 파일 경로가 `src/main.py` 기준 `parent.parent / "static"`이라 site-packages 아래에서
> `static/`을 찾지 못한다. **오류 없이 UI만 404가 되므로** 알아채기 어렵다.
> `migrations/`·`alembic.ini`도 패키지에 포함되지 않으므로 소스 트리가 그대로 있어야 한다.

설치 확인:

```bash
sudo -u wzoneportal /opt/wzoneportal/.venv/bin/python -c \
  "from src.main import STATIC_DIR; print(STATIC_DIR, STATIC_DIR.is_dir())"
# /opt/wzoneportal/static True   ← False면 §5.3의 -e 누락이다
```

---

## 6. 설정 (`.env`)

### 6.1 키 생성

```bash
# 자격증명 암호화 키 (base64 32바이트) — NFR-208
/opt/wzoneportal/.venv/bin/python -c \
  "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"

# JWT 서명 키
/opt/wzoneportal/.venv/bin/python -c \
  "import os,base64;print(base64.b64encode(os.urandom(48)).decode())"
```

> **`PORTAL_CREDENTIAL_ENCRYPTION_KEY`를 잃어버리면 등록된 연결의 비밀번호를 복호화할 수 없다.**
> 자격증명은 이 키로만 풀리며 복구 경로가 없다 — 모든 연결을 다시 등록해야 한다.
> 키를 **DB 백업과 다른 곳에** 보관한다 (§12.2).

### 6.2 파일 작성

```bash
sudo -u wzoneportal cp /opt/wzoneportal/.env.example /opt/wzoneportal/.env
sudo -u wzoneportal vi /opt/wzoneportal/.env
sudo chmod 600 /opt/wzoneportal/.env      # 서비스 계정만 읽는다
```

| 키 | 운영값 | 비고 |
|---|---|---|
| `PORTAL_DATABASE_URL` | `postgresql+asyncpg://portal:<pw>@<host>:5432/wzoneportal` | 드라이버 `+asyncpg` 필수 |
| `PORTAL_JWT_SECRET` | §6.1 생성값 | 바꾸면 기존 세션이 전부 무효가 된다 |
| `PORTAL_CREDENTIAL_ENCRYPTION_KEY` | §6.1 생성값 | **분실 시 복구 불가** |
| `PORTAL_CREDENTIAL_KEY_VERSION` | `1` | 키 교체 시 증가 (§12.3) |
| `PORTAL_COOKIE_SECURE` | **`true`** | HTTPS 필수. HTTP면 로그인이 안 된다 (§15-1) |
| `PORTAL_API_HOST` | `127.0.0.1` | **외부에 직접 노출하지 않는다.** nginx가 앞단이다 |
| `PORTAL_API_PORT` | `8080` | |
| `PORTAL_LOG_LEVEL` | `INFO` | `DEBUG`는 상용에서 쓰지 않는다 (§12.1) |
| `PORTAL_BOOTSTRAP_ADMIN_USERNAME` | 최초 기동에만 | 생성 후 **제거** (§10) |
| `PORTAL_BOOTSTRAP_ADMIN_PASSWORD` | 최초 기동에만 | 생성 후 **제거** |

`.env`의 `list`/`dict` 타입 값은 **JSON 형식**으로 쓴다 (`CLAUDE.md` Known Mistakes 1번).
Step 1에서 해당하는 것은 `PORTAL_CREDENTIAL_LEGACY_KEYS`뿐이며 키 교체 중에만 쓴다.

### 6.3 `.env`는 작업 디렉토리 기준이다

설정은 `SettingsConfigDict(env_file=".env")`로 읽으며 이 경로는 **프로세스의 작업 디렉토리 기준**이다.
systemd 유닛에 `WorkingDirectory=`가 없으면 기동이 다음처럼 실패한다.

```
pydantic_core._pydantic_core.ValidationError: 3 validation errors for Settings
database_url  Field required
jwt_secret    Field required
```

§8의 유닛 파일에 `WorkingDirectory=/opt/wzoneportal`가 들어 있는 이유다.
`alembic.ini`의 `script_location = migrations`도 같은 이유로 작업 디렉토리에 의존한다.

---

## 7. 스키마 생성

```bash
cd /opt/wzoneportal      # alembic.ini가 상대 경로를 쓴다 (§6.3)
sudo -u wzoneportal /opt/wzoneportal/.venv/bin/python -m alembic upgrade head
```

확인:

```bash
psql "postgresql://portal:<pw>@<host>/wzoneportal" -c "\dt"
# alembic_version, audit_events, connections, resource_identities,
# user_connection_scopes, users, virtual_machines   ← 7개

psql "postgresql://portal:<pw>@<host>/wzoneportal" -c "SELECT extname FROM pg_extension"
# plpgsql, pg_trgm   ← pg_trgm이 없으면 §4.2 권한 문제다
```

`pg_trgm`은 Step 4 통합 검색(FR-403)에서 쓰지만 **권한 확인을 앞당기려 지금 만든다** (ROADMAP §4.1).

### 7.1 접속 URL은 `.env`에서 읽는다

`alembic.ini`에는 `sqlalchemy.url`이 **의도적으로 없다.** `migrations/env.py`가 애플리케이션과
같은 설정(`.env`의 `PORTAL_DATABASE_URL`)에서 URL을 읽는다 — 두 곳에 두면 어긋나고,
ini에 두면 자격증명이 저장소에 커밋된다 (NFR-203).

- 따라서 마이그레이션은 **§6의 `.env` 작성이 끝난 뒤에야** 실행할 수 있다. URL을 따로 넘기는 옵션은 없다.
- `alembic.ini`를 수정할 일이 생겨도 **ASCII만 쓴다.** `configparser`가 파일을 시스템 로캘
  인코딩으로 읽으므로, 한글 주석이 있으면 마이그레이션이 기동조차 못 한다 (`CLAUDE.md` Known Mistakes).

### 7.2 마이그레이션 상태 확인

```bash
cd /opt/wzoneportal
sudo -u wzoneportal .venv/bin/python -m alembic current   # DB에 적용된 리비전
sudo -u wzoneportal .venv/bin/python -m alembic heads     # 코드가 요구하는 최신 리비전
```

두 값이 같으면 최신이다. Step 1의 리비전은 `0001_step1_initial` 하나다.
`current`가 비어 있으면 스키마가 아직 없는 것이고, `heads`와 다르면 §12.4의 업그레이드 절차를 따른다.

### 7.3 DBA가 대행하는 경우 — 오프라인 SQL 생성

§4.2 ②의 경로다. DB에 접속하지 않고, DBA가 실행할 SQL을 파일로 만들어 전달한다.

```bash
cd /opt/wzoneportal
sudo -u wzoneportal .venv/bin/python -m alembic upgrade head --sql > /tmp/step1_schema.sql
```

- `.env`는 필요하다(§7.1 — URL로 방언을 결정한다). 접속은 하지 않는다.
- 생성된 SQL에 `CREATE EXTENSION IF NOT EXISTS pg_trgm`과 `alembic_version` 기록까지 포함되므로,
  DBA가 그대로 실행하면 이후 `alembic current`로 상태를 확인할 수 있다.
- 실행 계정이 DB 소유자가 아니면 확장 생성에서 막힌다 — §4.2 ①의 사전 조치가 먼저다.

---

## 8. systemd 서비스

```bash
sudo vi /etc/systemd/system/wzoneportal.service
```

```ini
[Unit]
Description=가상자원 인벤토리 포탈 (읽기 전용)
After=network-online.target
Wants=network-online.target
# DB가 같은 서버면 활성화한다
# After=postgresql-17.service
# Requires=postgresql-17.service

[Service]
Type=exec
User=wzoneportal
Group=wzoneportal

# .env와 alembic.ini가 상대 경로를 쓴다 — 없으면 기동 실패한다 (§6.3)
WorkingDirectory=/opt/wzoneportal

Environment=PYTHONUNBUFFERED=1
# ProtectSystem=strict으로 /opt이 읽기 전용이라 .pyc를 쓸 수 없다.
# 오류는 아니지만 매 기동 시 무의미한 시도를 하므로 끈다.
Environment=PYTHONDONTWRITEBYTECODE=1

ExecStart=/opt/wzoneportal/.venv/bin/python -m uvicorn src.main:app \
    --host 127.0.0.1 --port 8080 \
    --workers 2 \
    --proxy-headers --forwarded-allow-ips=127.0.0.1

Restart=on-failure
RestartSec=5

# ── 하드닝 — 이 서비스는 파일을 쓰지 않는다 ──
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictSUIDSGID=true
LockPersonality=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wzoneportal
sudo systemctl status wzoneportal
```

### 8.1 옵션 설명

| 옵션 | 이유 |
|---|---|
| `--workers 2` | 재기동·장애 시 가용성. 빈 DB에 동시 기동해도 부트스트랩 관리자는 1명만 생성된다 (§10) |
| `--proxy-headers` | 기본값 `True`. `X-Forwarded-For`로 실제 클라이언트 IP를 복원한다 |
| `--forwarded-allow-ips` | 기본값 `127.0.0.1`. **nginx가 다른 서버에 있으면 그 IP로 바꾼다** |
| `ProtectSystem=strict` | 애플리케이션이 런타임에 파일을 쓰지 않으므로 전체 읽기 전용으로 둘 수 있다 |

> **`--proxy-headers`를 끄면 감사 로그의 IP가 전부 `127.0.0.1`이 된다.** 누가 어디서 연결을
> 등록했는지 추적할 수 없어 FR-1004의 목적이 무너진다.

### 8.2 워커 수를 늘릴 때의 제약

수집 중복 실행을 막는 분산 락은 **Step 3(Redis)**에서 도입된다. 지금은 `[지금 수집]`을 연달아
누르면 같은 연결에 대해 수집이 두 번 시작될 수 있다.

데이터는 안전하다 — `uq_vm_native` 제약이 중복 레코드를 물리적으로 막고, 진 트랜잭션은
롤백되며 로그에 남는다. **다만 하이퍼바이저에 불필요한 부하가 가므로** Step 2 실측 중에는
버튼을 연타하지 않는다.

---

## 9. nginx와 TLS

```bash
sudo vi /etc/nginx/conf.d/wzoneportal.conf
```

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name portal.example.internal;

    ssl_certificate     /etc/pki/tls/certs/portal.crt;
    ssl_certificate_key /etc/pki/tls/private/portal.key;
    ssl_protocols       TLSv1.2 TLSv1.3;

    # 인벤토리 정보는 그 자체로 공격 표면 정보다 (NFR-206)
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options    "nosniff" always;
    add_header X-Frame-Options           "DENY" always;
    add_header Referrer-Policy           "same-origin" always;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        # 이 두 줄이 없으면 감사 로그의 IP가 전부 nginx 주소가 된다 (§8.1)
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name portal.example.internal;
    return 301 https://$host$request_uri;
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

**HTTPS는 선택이 아니다.** `PORTAL_COOKIE_SECURE=true`인 세션 쿠키는 HTTPS에서만 브라우저가
되돌려 보낸다. HTTP로 접속하면 로그인은 성공하는데 이후 요청이 전부 401이 되어
로그인 화면으로 되돌아가는 루프가 생긴다 (§15-1).

> **연결 테스트 타임아웃**: `POST /connections/test`는 도달 확인 5초 + vCenter 인증 시간이
> 걸린다. nginx 기본 `proxy_read_timeout 60s`로 충분하다. 수집은 202로 즉시 반환되므로
> 긴 타임아웃이 필요 없다 (ROADMAP §9.1).

---

## 10. 최초 기동과 관리자 계정

1. `.env`에 `PORTAL_BOOTSTRAP_ADMIN_USERNAME` / `PORTAL_BOOTSTRAP_ADMIN_PASSWORD`를 넣고 기동한다.
2. 로그를 확인한다.

```bash
sudo journalctl -u wzoneportal -n 30 --no-pager
# "부트스트랩 관리자 계정을 생성했습니다. 생성 후 환경변수를 제거하세요."
```

3. 로그인해 동작을 확인한 뒤 **두 환경변수를 `.env`에서 지우고 재기동한다.**

```bash
sudo -u wzoneportal sed -i '/^PORTAL_BOOTSTRAP_ADMIN_/d' /opt/wzoneportal/.env
sudo systemctl restart wzoneportal
```

계정이 이미 있으면 이 로직은 아무것도 하지 않으므로 **남겨 둬도 계정이 되살아나지는 않는다.**
그래도 지우는 이유는 **평문 비밀번호가 파일에 남기 때문**이다.

> 하드코딩된 기본 비밀번호는 없다. 환경변수가 없으면 계정을 만들지 않고 경고만 남긴다 (계획 09 §7).
> 워커 여러 개가 빈 DB에 동시 기동해도 `username` UNIQUE 제약으로 1명만 생성된다.

### 10.1 Step 1~2 동안의 계정 운영

**가입 승인을 시작하지 않는다** (ROADMAP §3). 수집 데이터의 정확성이 확인되지 않은 상태이며,
인벤토리 정보는 그 자체로 공격 표면 정보다 (NFR-206). 관리자 계정 1~2개로만 운영한다.

---

## 11. 배포 검증

순서대로 확인한다. 앞 단계가 통과해야 다음이 의미가 있다.

```bash
# 1. 프로세스와 DB
curl -s https://portal.example.internal/api/v1/health | jq
# {"status":"healthy","checks":{"database":{"ok":true,...}}}

# 2. 정적 UI (404면 §5.3의 -e 누락)
curl -s -o /dev/null -w "%{http_code}\n" https://portal.example.internal/login.html   # 200

# 3. 인증 없이 조회 API 호출 → 401이어야 한다
curl -s -o /dev/null -w "%{http_code}\n" https://portal.example.internal/api/v1/virtual-machines   # 401

# 4. 로그인 → 쿠키에 HttpOnly·Secure·SameSite=Strict가 있어야 한다
curl -s -i -X POST https://portal.example.internal/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<비밀번호>"}' | grep -i set-cookie
# set-cookie: portal_session=...; HttpOnly; Secure; SameSite=strict; Path=/
```

브라우저에서:

| # | 확인 | 기대 |
|---|---|---|
| 5 | 로그인 → 자원 목록 | 사이드바에 관리 메뉴가 보인다 |
| 6 | 개발자 도구 콘솔에서 `document.cookie` | **빈 문자열** (토큰을 JS가 읽을 수 없다) |
| 7 | 연결 관리 → `[연결 추가]` → `[연결 테스트]` | 4단계 결과. 실패해도 화면이 깨지지 않는다 |
| 8 | 등록 후 `[지금 수집]` | 상태가 `수집 중…` → 완료 또는 사유 표시 |
| 9 | 자원 목록 | 게스트 OS 컬럼에 `수집 불가 — …`가 빈 칸이 아닌 문구로 표시 |

**9번이 이 MVP의 시금석이다** (ROADMAP §5.3). 빈 칸이면 "값 없음 / 수집 불가" 구분이
파이프라인 어딘가에서 끊긴 것이다.

```bash
# 10. 감사 로그에 실제 클라이언트 IP가 남는지 (§8.1)
psql "postgresql://portal:<pw>@<host>/wzoneportal" \
  -c "SELECT occurred_at, actor, action, client_ip FROM audit_events ORDER BY event_id DESC LIMIT 5"
# client_ip가 전부 127.0.0.1이면 --proxy-headers 또는 nginx 헤더 설정 문제다
```

```bash
# 11. 비밀번호가 평문으로 저장되지 않았는지
psql "postgresql://portal:<pw>@<host>/wzoneportal" \
  -c "SELECT display_name, left(password_encrypted, 12) FROM connections"
# 1$... 형식 ({key_version}${nonce}${ciphertext})

# 12. 로그에 자격증명이 없는지
sudo journalctl -u wzoneportal --since today | grep -iE "password|pwd|secret" | head
# 값이 보이면 안 된다. 마스킹 필터가 붙어 있다 (계획 10 §5.1)
```

---

## 12. 운영

### 12.1 로그

로그는 stdout으로 나가 journald가 받는다. 자격증명 마스킹 필터가 **핸들러에** 부착되어 있어
하위 로거의 기록까지 걸러진다 (계획 10 §5.1).

```bash
sudo journalctl -u wzoneportal -f                    # 실시간
sudo journalctl -u wzoneportal --since "1 hour ago"
```

> **`PORTAL_LOG_LEVEL=DEBUG`를 상용에서 켜지 않는다.** pyVmomi·SQLAlchemy가 접속 문자열과
> 파라미터를 상세히 기록하며, 마스킹 필터는 알려진 패턴만 가린다.

### 12.2 백업

```bash
pg_dump "postgresql://portal:<pw>@<host>/wzoneportal" -Fc -f wzoneportal-$(date +%F).dump
```

백업에는 `connections.password_encrypted`(암호문)와 `users.password_hash`가 들어 있다.

> **암호화 키를 DB 백업과 같은 곳에 두지 않는다.** 한 곳이 유출되면 둘 다 잃는다.
> 반대로 **키만 잃어도 자격증명은 복구되지 않는다** — 백업이 있어도 소용없다.
>
> 보관 위치·주기·보존 기간은 **아직 정해지지 않았다** (D-013 미결 사항).
> Step 2 착수 전에 정한다.

### 12.3 키 교체

`CredentialCipher.needs_rotation()`으로 구버전 키로 암호화된 레코드를 판별할 수 있고,
저장 형식에 `key_version` 접두사가 있어 **교체 경로 자체는 확보되어 있다.**

**다만 계획 10 §3.4의 재암호화 스크립트(`scripts/rotate_credential_key.py`)는 아직 없다.**
지금 키를 바꾸려면 모든 연결을 삭제 후 재등록해야 한다. 운영 중 교체가 필요해지기 전에
스크립트를 만든다.

### 12.4 업그레이드

```bash
cd /opt/wzoneportal
sudo -u wzoneportal git pull                                  # 또는 새 아카이브 배치
sudo -u wzoneportal .venv/bin/pip install -e .                # 의존성 변경 반영
sudo -u wzoneportal .venv/bin/python -m alembic upgrade head  # 스키마 변경 반영
sudo systemctl restart wzoneportal
curl -s https://portal.example.internal/api/v1/health
```

**순서가 중요하다.** 마이그레이션을 먼저 올리고 프로세스를 재기동한다.

되돌릴 때:

```bash
sudo -u wzoneportal .venv/bin/python -m alembic downgrade -1
```

> `downgrade`는 **테이블을 지운다.** Step 1의 `0001` 리비전을 되돌리면 수집·계정·감사 데이터가
> 전부 사라진다. 실행 전 §12.2의 백업을 확인한다.

### 12.5 DB 이전 (서버 교체·이관)

DB 서버를 교체하거나 검증 서버의 데이터를 새 서버로 옮길 때의 절차다.

1. 서비스를 중지한다 (이전 중 쓰기 방지): `sudo systemctl stop wzoneportal`
2. 원본에서 덤프: §12.2와 같은 `pg_dump -Fc`
3. 새 서버에 §4대로 계정과 **빈 DB**를 만든다. **`alembic upgrade head`를 미리 실행하지 않는다** —
   덤프에 스키마와 `alembic_version`이 들어 있어, 미리 만들면 복원이 "already exists"로 실패한다.
4. 복원 후 `.env`의 `PORTAL_DATABASE_URL`을 새 서버로 바꾸고 기동한다.

```bash
pg_dump "postgresql://portal:<pw>@<구서버>/wzoneportal" -Fc -f wzoneportal-migrate.dump
pg_restore --no-owner -d "postgresql://portal:<pw>@<신서버>/wzoneportal" wzoneportal-migrate.dump

cd /opt/wzoneportal
sudo -u wzoneportal .venv/bin/python -m alembic current   # 0001_step1_initial (head)면 정상
sudo systemctl start wzoneportal
```

복원을 확인할 때는 §11의 1번(health)과 8번(수집)을 다시 수행한다.

> **`PORTAL_CREDENTIAL_ENCRYPTION_KEY`가 함께 가야 데이터가 산다.** `connections`의 자격증명
> 암호문은 이 키로만 복호화된다 (§6.1). 키가 다른 서버에 데이터만 복원하면 목록·조회는
> 정상인데 **수집만 전부 실패**하므로 원인을 찾기 어렵다.
> `PORTAL_JWT_SECRET`은 달라도 된다 — 기존 세션이 무효가 될 뿐이며 재로그인하면 된다.

새 서버의 코드가 더 새 버전이면, 복원 뒤 `alembic upgrade head`가 부족한 리비전만 이어서 적용한다
(§12.4와 같은 순서 — 마이그레이션 먼저, 기동은 그다음).

---

## 13. Step 1~2 단계의 운영 제약

| 제약 | 이유 |
|---|---|
| **전사 공개하지 않는다** | 수집 데이터의 정확성이 미검증이고, 인벤토리는 공격 표면 정보다 (ROADMAP §3, NFR-206) |
| 가입 승인을 시작하지 않는다 | 위와 같음. 관리자 1~2명으로 운영 |
| 수집은 수동으로만 | 주기 수집은 Step 3 |
| 연결 수정 불가 | 삭제 후 재등록으로 대체 (ROADMAP §5.2) |
| 수집된 VM이 있는 연결은 삭제 불가 | 2단계 확인과 자원 보존 정책이 Step 3 |
| 자원이 사라져도 목록에 남는다 | 미발견 처리(FR-307)가 Step 3. **`last_seen_at`으로 판단한다** |

마지막 항목이 실측 중 오해를 부르기 쉽다. Step 1에는 `lifecycle`이 항상 `active`이므로,
vCenter에서 삭제된 VM도 목록에 계속 보인다. 수집 시각으로 구분한다.

---

## 14. 방화벽 · SELinux

```bash
# 인바운드는 443만 — 8080을 열지 않는다
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload

# nginx가 uvicorn으로 프록시하려면 필요 (RHEL 계열)
sudo setsebool -P httpd_can_network_connect 1
```

| 방향 | 대상 | 포트 | 용도 |
|---|---|---|---|
| 인바운드 | 사내 운영자망 | 443 | 포탈 접속 |
| 아웃바운드 | **vCenter** | 443 | 수집 (CST-07) |
| 아웃바운드 | DB 서버 | 5432 | 별도 서버인 경우만 |

vCenter 방화벽이 없으면 연결 테스트가 **1단계(`reachable`)에서 실패**하므로 원인을 바로 알 수 있다.

---

## 15. 문제 해결

| 증상 | 원인 | 조치 |
|---|---|---|
| 로그인은 200인데 계속 로그인 화면으로 돌아온다 | HTTP 접속인데 `PORTAL_COOKIE_SECURE=true` | HTTPS로 접속한다. 개발 서버라면 `false`로 내린다 |
| 기동 실패 — `ValidationError: database_url Field required` | `WorkingDirectory` 누락으로 `.env`를 못 찾음 (§6.3) | 유닛에 `WorkingDirectory=/opt/wzoneportal` 추가 |
| `/login.html`이 404 | 비-editable 설치로 `static/` 경로 어긋남 (§5.3) | `pip install -e .`로 재설치 후 §5.3 확인 명령 실행 |
| `alembic: Path doesn't exist: migrations` | 프로젝트 루트가 아닌 곳에서 실행 | `cd /opt/wzoneportal` 후 실행 |
| `CREATE EXTENSION pg_trgm` 권한 오류 | DB 소유자가 아님 (§4.2) | 소유자로 변경하거나 superuser가 확장을 미리 생성 |
| 감사 로그 `client_ip`가 전부 `127.0.0.1` | nginx `X-Forwarded-For` 미설정 또는 `--forwarded-allow-ips` 불일치 | §9·§8.1 확인 |
| 연결 테스트가 `reachable`에서 실패 | 방화벽 또는 DNS (§14) | 서버에서 `curl -vk https://<vcenter>` 로 확인 |
| 연결 테스트가 `tls_valid`/`authenticated`에서 실패 | 자체 서명 인증서 | 등록 시 `TLS 인증서 검증` 해제. **운영 인증서 도입 전까지의 임시 조치다** |
| 상태가 `자격증명 오류`로 바뀌고 수집이 멈춤 | 인증 실패 — **의도된 동작** | 비밀번호 확인 후 연결을 재등록한다. 반복 시도하면 AD 계정이 잠긴다 (CST-05) |
| 목록이 비어 있고 "조회할 수 있는 연결이 없습니다" | 조회 범위 미부여 (기본 거부) | 사용자 관리에서 `[범위]` 부여 |

### 15.1 SameSite=Strict의 정상 동작

메신저·메일의 링크로 포탈에 처음 들어오면 브라우저가 쿠키를 보내지 않아 로그인 화면이 뜬다.
CSRF 방어를 위한 의도된 동작이며(D-014), 주소창에 직접 입력하거나 북마크로 들어오면 정상이다.

---

## 16. 배포 체크리스트

**사전 승인**

- [ ] vCenter 읽기 전용 계정 발급
- [ ] 포탈 서버 → vCenter 443 방화벽 개방
- [ ] DB 계정 권한 확보 (§4.2 레시피 적용 가능 여부)
- [ ] 내부 도메인 + TLS 인증서

**설치**

- [ ] Python 3.11 이상
- [ ] PostgreSQL 17(또는 16), 소유자 계정으로 DB 생성
- [ ] `pip install -e .` — §5.3 확인 명령이 `True`
- [ ] `.env` 작성, 권한 `600`, 키 2종 생성
- [ ] `alembic upgrade head` — 테이블 7개 + `pg_trgm`
- [ ] systemd 유닛에 `WorkingDirectory` 포함
- [ ] nginx TLS + `X-Forwarded-For`

**검증** (§11)

- [ ] `/api/v1/health` → `healthy`
- [ ] `/login.html` → 200
- [ ] 미인증 조회 API → 401
- [ ] 쿠키에 `HttpOnly; Secure; SameSite=strict`
- [ ] 브라우저에서 `document.cookie`가 빈 문자열
- [ ] 등록 → 수집 → 목록 관통
- [ ] 게스트 OS 컬럼에 `수집 불가 — 사유` 표시
- [ ] `audit_events.client_ip`가 실제 클라이언트 IP
- [ ] `password_encrypted`가 `1$…` 형식
- [ ] 로그에 자격증명 없음

**마무리**

- [ ] 부트스트랩 환경변수 제거 후 재기동
- [ ] 백업 1회 수행 및 복원 확인
- [ ] 암호화 키를 백업과 **다른 곳**에 보관
- [ ] Step 2 검증 항목(ROADMAP §15) 준비 — `docs/04_field_validation.md` 작성 시작

---

## 부록 A. 컨테이너로 배포하는 경우

컨테이너를 쓰더라도 §4~§7의 요건은 같다. 달라지는 지점만 정리한다.

| 항목 | 조치 |
|---|---|
| 작업 디렉토리 | `WORKDIR /app` — §6.3의 이유가 그대로 적용된다 |
| 설치 방식 | `pip install -e .` 유지. `static/`·`migrations/`를 이미지에 포함한다 |
| 설정 | `.env` 대신 컨테이너 환경변수를 쓴다. 환경변수가 `.env`보다 우선한다 |
| 비밀값 | 이미지에 굽지 않는다. 오케스트레이터의 secret으로 주입한다 |
| 마이그레이션 | 앱 기동 전 별도 잡으로 실행한다. 여러 복제본이 동시에 `upgrade`하면 충돌한다 |
| 프록시 헤더 | `--forwarded-allow-ips`를 **인그레스 대역**으로 지정한다. `127.0.0.1` 기본값으로는 동작하지 않는다 |
| 로그 | stdout 그대로. 컨테이너 런타임이 수집한다 |

마지막 두 줄이 컨테이너에서 가장 자주 틀리는 지점이다. 특히 `--forwarded-allow-ips`를 그대로
두면 감사 로그의 IP가 전부 인그레스 주소가 된다.
