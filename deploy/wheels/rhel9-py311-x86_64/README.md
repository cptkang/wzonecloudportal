# RHEL/Rocky 9 오프라인 설치용 wheel 번들

`docs/05_deployment.md` §5.3의 설치 절차를 **인터넷 없이** 수행하기 위한 wheel 모음이다.

| 항목 | 값 |
|---|---|
| 대상 OS | RHEL / Rocky Linux 9.x (glibc 2.34) |
| 대상 Python | CPython **3.11** (`dnf install python3.11` — 배포 문서 §3.1) |
| 아키텍처 | x86_64 |
| 수집일 | 2026-08-19 |

**Python 3.11 전용이다.** 3.12/3.13 venv에는 `cp311` 태그 wheel이 설치되지 않는다.
aarch64도 마찬가지로 별도 번들이 필요하다.

## 구성

| 폴더 | 내용 |
|---|---|
| `runtime/` | `pyproject.toml` `[project].dependencies` 전개 (42개). 실서버 구동에 필요 |
| `dev/` | `[project.optional-dependencies].dev` 전개 (21개). 서버에서 테스트·품질 게이트를 돌릴 때만 |
| `buildtools/` | `pip`·`setuptools`·`wheel`·`packaging`. `pip install -e .`의 빌드 격리에 필요 |

`requirements-runtime.txt` / `requirements-dev.txt`는 위 wheel의 버전을 고정한 목록이다.

## 설치

```bash
W=/opt/wzoneportal/deploy/wheels/rhel9-py311-x86_64

sudo -u wzoneportal python3.11 -m venv /opt/wzoneportal/.venv
sudo -u wzoneportal /opt/wzoneportal/.venv/bin/pip install \
    --no-index --find-links=$W/buildtools --upgrade pip setuptools wheel

# 배포 문서 §5.3 — `-e`(editable)를 반드시 쓴다 (static/·migrations/ 경로 때문)
sudo -u wzoneportal /opt/wzoneportal/.venv/bin/pip install \
    --no-index --find-links=$W/buildtools --find-links=$W/runtime \
    -e /opt/wzoneportal

# 서버에서 테스트까지 돌릴 경우에만
sudo -u wzoneportal /opt/wzoneportal/.venv/bin/pip install \
    --no-index --find-links=$W/dev -r $W/requirements-dev.txt
```

## 재생성

인터넷이 되는 장비에서 실행한다. `--python-version 3.11`과 manylinux 플랫폼 태그를 지정하므로
Windows/macOS에서도 리눅스용 wheel을 받을 수 있다.

```bash
PLAT="--platform manylinux_2_34_x86_64 --platform manylinux_2_28_x86_64 \
      --platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64"

pip download --only-binary=:all: --python-version 3.11 $PLAT -d runtime \
    "fastapi" "uvicorn[standard]" "pydantic>=2" "pydantic-settings" \
    "sqlalchemy[asyncio]>=2" "asyncpg" "alembic" "pyvmomi>=8.0.3,<10" "pypsrp" \
    "cryptography" "bcrypt>=4" "python-jose[cryptography]"

pip download --only-binary=:all: --python-version 3.11 $PLAT -d dev \
    "pytest" "pytest-asyncio" "pytest-cov" "httpx" "ruff" "mypy"

pip download --only-binary=:all: --python-version 3.11 $PLAT -d buildtools \
    "pip" "setuptools" "wheel"

# ↓ 반드시 수동 보정한다 (아래 참고)
pip download --only-binary=:all: --python-version 3.11 $PLAT --no-deps -d runtime "uvloop"
rm -f runtime/colorama-*.whl runtime/sspilib-*.whl dev/colorama-*.whl
```

> **`pip download`는 `--platform`을 줘도 의존성의 환경 마커를 *실행 중인* OS 기준으로 평가한다.**
> Windows에서 받으면 Linux 전용 `uvloop`(uvicorn[standard])이 **누락되고**,
> Windows 전용 `colorama`·`sspilib`이 **딸려온다**. 위 두 줄이 그 보정이다.
> 리눅스에서 받으면 보정이 필요 없다.

## 검증 방법

이 번들은 아래로 확인했다 — Rocky Linux 9.8 / glibc 2.34 / Python 3.11.13 컨테이너에서
runtime·dev 설치, `pip check` 통과, editable 설치 성공, 주요 모듈 import 성공.

```bash
docker run --rm -v "$PWD:/src:ro" rockylinux/rockylinux:9 bash -c '
  dnf install -y python3.11 >/dev/null 2>&1
  W=/src/deploy/wheels/rhel9-py311-x86_64
  python3.11 -m venv /tmp/venv
  /tmp/venv/bin/pip install --no-index --find-links=$W/runtime -r $W/requirements-runtime.txt
  /tmp/venv/bin/pip check'
```
