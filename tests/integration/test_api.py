"""API 통합 테스트 — Step 1 완료 기준 (ROADMAP §13).

인증·권한·조회 범위·응답 계약을 관통해서 확인한다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from src.config import get_settings
from src.domain.auth import Role, UserStatus
from src.infrastructure.repository.user_repo import UserRepository
from src.infrastructure.security.password import hash_password
from src.main import create_app

ADMIN_PASSWORD = "admin-test-password"
VIEWER_PASSWORD = "viewer-test-password"


@pytest.fixture
async def app(session_factory):  # type: ignore[no-untyped-def]
    get_settings.cache_clear()
    application = create_app(get_settings())
    application.state.session_factory = session_factory
    return application


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def new_client(app):  # type: ignore[no-untyped-def]
    """쿠키를 공유하지 않는 별도 세션. 두 사용자를 동시에 다루는 테스트에 쓴다."""

    def factory() -> AsyncClient:
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    return factory


@pytest.fixture
async def admin(session) -> str:  # type: ignore[no-untyped-def]
    users = UserRepository(session)
    await users.add(
        username="admin",
        password_hash=hash_password(SecretStr(ADMIN_PASSWORD)),
        display_name="관리자",
        role=Role.ADMIN,
        status=UserStatus.ACTIVE,
    )
    await session.commit()
    return "admin"


async def _login(client: AsyncClient, username: str, password: str) -> None:
    res = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text


async def _create_connection(client: AsyncClient, name: str, address: str) -> UUID:
    res = await client.post(
        "/api/v1/connections",
        json={
            "kind": "vcenter",
            "display_name": name,
            "address": address,
            "port": 443,
            "username": "svc-inventory@vsphere.local",
            "password": "connection-secret-1234",
            "verify_tls": True,
        },
    )
    assert res.status_code == 201, res.text
    return UUID(res.json()["connection_id"])


# ── 기본 ─────────────────────────────────────────────────────


async def test_health_is_public(client: AsyncClient) -> None:
    res = await client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"


async def test_inventory_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/virtual-machines")).status_code == 401


async def test_every_route_outside_the_whitelist_requires_auth(
    client: AsyncClient, app
) -> None:  # type: ignore[no-untyped-def]
    """공개 경로는 화이트리스트다 (계획 08 §4.2).

    `PUBLIC_PATHS`를 상수로만 두면 인증 의존성을 빠뜨린 새 엔드포인트가 조용히 공개된다.
    이 테스트가 그 상수를 **강제되는 불변식**으로 만든다.
    """
    from src.api.deps import PUBLIC_PATHS

    checked = 0
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}
        if not path.startswith("/api/v1") or path in PUBLIC_PATHS or not methods:
            continue
        # 경로 파라미터는 아무 UUID나 넣는다. 인증이 먼저 걸리므로 대상 존재 여부는 무관하다.
        concrete = path.replace("{user_id}", str(uuid4())).replace("{connection_id}", str(uuid4()))
        for method in sorted(methods):
            res = await client.request(method, concrete, json={})
            assert res.status_code == 401, f"{method} {path} → {res.status_code} (인증 누락)"
            checked += 1

    # 라우터가 통째로 빠져도 통과하지 않도록 최소 건수를 확인한다
    assert checked >= 12


# ── 인증 (D-014) ─────────────────────────────────────────────


async def test_register_never_reveals_duplicate(client: AsyncClient) -> None:
    payload = {
        "username": "kim.dev",
        "password": "register-password-1",
        "display_name": "김개발",
        "email": "kim@example.invalid",
    }
    first = await client.post("/api/v1/auth/register", json=payload)
    second = await client.post("/api/v1/auth/register", json=payload)

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()


async def test_pending_account_cannot_sign_in(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"username": "pending.user", "password": "pending-password-1"},
    )
    res = await client.post(
        "/api/v1/auth/login",
        json={"username": "pending.user", "password": "pending-password-1"},
    )
    assert res.status_code == 401
    assert "승인" in res.json()["message"]


async def test_wrong_password_does_not_reveal_account_state(client: AsyncClient) -> None:
    """틀린 비밀번호로는 계정 존재·상태를 알 수 없어야 한다 (계획 09 §4.3)."""
    await client.post(
        "/api/v1/auth/register",
        json={"username": "pending.user", "password": "pending-password-1"},
    )
    existing = await client.post(
        "/api/v1/auth/login", json={"username": "pending.user", "password": "wrong-password-x"}
    )
    missing = await client.post(
        "/api/v1/auth/login", json={"username": "no.such.user", "password": "wrong-password-x"}
    )

    assert existing.status_code == missing.status_code == 401
    assert existing.json()["message"] == missing.json()["message"]


async def test_session_cookie_is_httponly(client: AsyncClient, admin: str) -> None:
    res = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    cookie_header = res.headers.get("set-cookie", "")
    assert "httponly" in cookie_header.lower()
    assert "samesite=strict" in cookie_header.lower()
    # 토큰을 본문에 넣지 않는다
    assert "token" not in res.json()


async def test_login_response_has_no_password_fields(client: AsyncClient, admin: str) -> None:
    res = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    body = res.text
    assert "password_hash" not in body
    assert ADMIN_PASSWORD not in body


# ── 연결 관리 ────────────────────────────────────────────────


async def test_connection_response_has_no_password(client: AsyncClient, admin: str) -> None:
    await _login(client, "admin", ADMIN_PASSWORD)
    await _create_connection(client, "vCenter DC1", "vcsa-dc1.example.invalid")

    res = await client.get("/api/v1/connections")
    assert res.status_code == 200
    assert "connection-secret-1234" not in res.text
    assert "password" not in res.json()[0]
    assert res.json()[0]["has_password"] is True


async def test_duplicate_connection_is_rejected(client: AsyncClient, admin: str) -> None:
    """FR-105 — 같은 주소+계정 중복 등록."""
    await _login(client, "admin", ADMIN_PASSWORD)
    await _create_connection(client, "vCenter DC1", "vcsa-dc1.example.invalid")

    res = await client.post(
        "/api/v1/connections",
        json={
            "kind": "vcenter",
            "display_name": "중복",
            "address": "vcsa-dc1.example.invalid",
            "port": 443,
            "username": "svc-inventory@vsphere.local",
            "password": "connection-secret-1234",
            "verify_tls": True,
        },
    )
    assert res.status_code == 409
    assert res.json()["detail"]["existing_display_name"] == "vCenter DC1"


async def test_write_is_visible_to_the_next_read(client: AsyncClient, admin: str) -> None:
    """등록 직후 목록에 바로 보여야 한다.

    커밋을 `get_db` teardown에만 맡기면 응답이 나간 뒤에 커밋되어, UI의
    "등록 → 목록 새로고침" 흐름에서 방금 만든 연결이 빠진다.
    """
    await _login(client, "admin", ADMIN_PASSWORD)
    for i in range(3):
        await _create_connection(client, f"vCenter {i}", f"vcsa-{i}.example.invalid")
        assert len((await client.get("/api/v1/connections")).json()) == i + 1


async def test_invalid_address_returns_422_with_field(client: AsyncClient, admin: str) -> None:
    await _login(client, "admin", ADMIN_PASSWORD)
    res = await client.post(
        "/api/v1/connections",
        json={
            "kind": "vcenter",
            "display_name": "잘못된 주소",
            "address": "not a hostname!",
            "port": 443,
            "username": "svc@vsphere.local",
            "password": "connection-secret-1234",
        },
    )
    assert res.status_code == 422


async def test_password_is_encrypted_at_rest(client: AsyncClient, admin: str, session) -> None:
    from sqlalchemy import select

    from src.infrastructure.db.models import ConnectionRow

    await _login(client, "admin", ADMIN_PASSWORD)
    await _create_connection(client, "vCenter DC1", "vcsa-dc1.example.invalid")

    row = (await session.execute(select(ConnectionRow))).scalar_one()
    assert "connection-secret-1234" not in row.password_encrypted
    assert row.password_encrypted.startswith("1$")  # {key_version}${nonce}${ciphertext}


# ── 권한과 조회 범위 ─────────────────────────────────────────


async def _approve_viewer(
    client: AsyncClient, username: str, connection_ids: list[str]
) -> str:
    users = (await client.get("/api/v1/users")).json()["items"]
    target = next(u for u in users if u["username"] == username)
    res = await client.post(
        f"/api/v1/users/{target['user_id']}/approve",
        json={"role": "viewer", "connection_ids": connection_ids},
    )
    assert res.status_code == 204, res.text
    return target["user_id"]


async def test_viewer_cannot_manage_connections_or_users(
    client: AsyncClient, admin: str
) -> None:
    await client.post(
        "/api/v1/auth/register", json={"username": "park.view", "password": "viewer-test-password"}
    )
    await _login(client, "admin", ADMIN_PASSWORD)
    await _approve_viewer(client, "park.view", [])
    await client.post("/api/v1/auth/logout")

    await _login(client, "park.view", VIEWER_PASSWORD)
    assert (await client.get("/api/v1/connections")).status_code == 403
    assert (await client.get("/api/v1/users")).status_code == 403


async def test_scope_filters_visible_vms(client: AsyncClient, admin: str, session) -> None:
    """FR-1003 — 범위 밖 자원이 목록에 보이지 않는다."""
    from src.infrastructure.repository.vm_repo import VirtualMachineRepository
    from datetime import UTC, datetime

    from tests.fakes.fake_reader import make_vm

    await client.post(
        "/api/v1/auth/register", json={"username": "park.view", "password": VIEWER_PASSWORD}
    )
    await _login(client, "admin", ADMIN_PASSWORD)
    conn_a = await _create_connection(client, "vCenter A", "vcsa-a.example.invalid")
    conn_b = await _create_connection(client, "vCenter B", "vcsa-b.example.invalid")

    repo = VirtualMachineRepository(session)
    now = datetime.now(UTC)
    await repo.upsert_virtual_machines(
        conn_a, [make_vm(connection_id=conn_a, name="a-vm", native_id="a1")], now
    )
    await repo.upsert_virtual_machines(
        conn_b, [make_vm(connection_id=conn_b, name="b-vm", native_id="b1")], now
    )
    await session.commit()

    # 관리자는 전체를 본다
    assert (await client.get("/api/v1/virtual-machines")).json()["total"] == 2

    await _approve_viewer(client, "park.view", [str(conn_a)])
    await client.post("/api/v1/auth/logout")
    await _login(client, "park.view", VIEWER_PASSWORD)

    body = (await client.get("/api/v1/virtual-machines")).json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "a-vm"

    # 범위 밖 connection_id는 403이 아니라 빈 결과다 — 403은 연결의 존재를 알려준다
    out_of_scope = await client.get(f"/api/v1/virtual-machines?connection_id={conn_b}")
    assert out_of_scope.status_code == 200
    assert out_of_scope.json()["total"] == 0


async def test_empty_scope_sees_nothing(client: AsyncClient, admin: str, session) -> None:
    """기본 거부 — 범위를 비운 채 승인하면 아무것도 보이지 않는다."""
    from datetime import UTC, datetime

    from src.infrastructure.repository.vm_repo import VirtualMachineRepository
    from tests.fakes.fake_reader import make_vm

    await client.post(
        "/api/v1/auth/register", json={"username": "park.view", "password": VIEWER_PASSWORD}
    )
    await _login(client, "admin", ADMIN_PASSWORD)
    conn = await _create_connection(client, "vCenter A", "vcsa-a.example.invalid")

    repo = VirtualMachineRepository(session)
    await repo.upsert_virtual_machines(
        conn, [make_vm(connection_id=conn, name="a-vm", native_id="a1")], datetime.now(UTC)
    )
    await session.commit()

    await _approve_viewer(client, "park.view", [])
    await client.post("/api/v1/auth/logout")
    await _login(client, "park.view", VIEWER_PASSWORD)

    assert (await client.get("/api/v1/virtual-machines")).json()["total"] == 0


async def test_scope_change_applies_without_relogin(
    client: AsyncClient, admin: str, session, new_client
) -> None:
    """범위는 요청마다 DB에서 조회하므로 재로그인이 필요 없다 (계획 09 §4.2)."""
    from datetime import UTC, datetime

    from src.infrastructure.repository.vm_repo import VirtualMachineRepository
    from tests.fakes.fake_reader import make_vm

    await client.post(
        "/api/v1/auth/register", json={"username": "park.view", "password": VIEWER_PASSWORD}
    )
    await _login(client, "admin", ADMIN_PASSWORD)
    conn = await _create_connection(client, "vCenter A", "vcsa-a.example.invalid")
    repo = VirtualMachineRepository(session)
    await repo.upsert_virtual_machines(
        conn, [make_vm(connection_id=conn, name="a-vm", native_id="a1")], datetime.now(UTC)
    )
    await session.commit()
    viewer_id = await _approve_viewer(client, "park.view", [])

    async with new_client() as viewer_client:
        await _login(viewer_client, "park.view", VIEWER_PASSWORD)
        assert (await viewer_client.get("/api/v1/virtual-machines")).json()["total"] == 0

        await client.put(
            f"/api/v1/users/{viewer_id}/scopes", json={"connection_ids": [str(conn)]}
        )
        # 같은 세션 쿠키 그대로 재조회
        assert (await viewer_client.get("/api/v1/virtual-machines")).json()["total"] == 1


async def test_disabled_account_is_rejected_immediately(
    client: AsyncClient, admin: str, new_client
) -> None:
    """비활성화 직후 기존 토큰으로 호출하면 401 — 토큰 만료를 기다리지 않는다."""
    await client.post(
        "/api/v1/auth/register", json={"username": "park.view", "password": VIEWER_PASSWORD}
    )
    await _login(client, "admin", ADMIN_PASSWORD)
    viewer_id = await _approve_viewer(client, "park.view", [])

    async with new_client() as viewer_client:
        await _login(viewer_client, "park.view", VIEWER_PASSWORD)
        assert (await viewer_client.get("/api/v1/auth/me")).status_code == 200

        await client.post(f"/api/v1/users/{viewer_id}/disable")

        assert (await viewer_client.get("/api/v1/auth/me")).status_code == 401


async def test_last_admin_cannot_be_demoted_or_disabled(
    client: AsyncClient, admin: str, session
) -> None:
    """관리자가 0명이 되면 DB를 직접 고치는 수밖에 없다 (계획 09 §4.6.1)."""
    users_repo = UserRepository(session)
    other = await users_repo.add(
        username="second.admin",
        password_hash=hash_password(SecretStr("second-admin-password")),
        role=Role.ADMIN,
        status=UserStatus.ACTIVE,
    )
    await session.commit()

    await _login(client, "admin", ADMIN_PASSWORD)
    # 다른 관리자가 있으므로 강등 가능
    assert (
        await client.patch(f"/api/v1/users/{other.user_id}", json={"role": "viewer"})
    ).status_code == 204

    # 이제 admin이 마지막 관리자다. 자기 자신은 애초에 변경할 수 없다.
    me = (await client.get("/api/v1/auth/me")).json()["user"]
    res = await client.post(f"/api/v1/users/{me['user_id']}/disable")
    assert res.status_code == 422


async def test_user_delete_endpoint_does_not_exist(app) -> None:  # type: ignore[no-untyped-def]
    """계정은 비활성화만 한다 (D-014)."""
    for route in app.routes:
        if getattr(route, "path", "") == "/api/v1/users/{user_id}":
            assert "DELETE" not in route.methods


# ── 응답 계약 (ROADMAP §9.2) ─────────────────────────────────


async def test_vm_list_uses_paged_response_and_nested_guest(
    client: AsyncClient, admin: str, session
) -> None:
    from datetime import UTC, datetime

    from src.domain.enums import GuestInfoAvailability
    from src.infrastructure.repository.vm_repo import VirtualMachineRepository
    from tests.fakes.fake_reader import make_vm

    await _login(client, "admin", ADMIN_PASSWORD)
    conn = await _create_connection(client, "vCenter A", "vcsa-a.example.invalid")

    repo = VirtualMachineRepository(session)
    await repo.upsert_virtual_machines(
        conn,
        [
            make_vm(connection_id=conn, name="ok-vm", native_id="a1"),
            make_vm(
                connection_id=conn,
                name="no-tools-vm",
                native_id="a2",
                guest_availability=GuestInfoAvailability.TOOLS_NOT_INSTALLED,
            ),
        ],
        datetime.now(UTC),
    )
    await session.commit()

    body = (await client.get("/api/v1/virtual-machines")).json()

    assert set(body) >= {"items", "total", "offset", "limit"}
    assert "page" not in body and "size" not in body

    by_name = {item["name"]: item for item in body["items"]}
    ok = by_name["ok-vm"]["guest"]
    assert ok["is_collected"] is True
    assert ok["unavailable_reason"] is None
    assert ok["os_name"]

    missing = by_name["no-tools-vm"]["guest"]
    assert missing["is_collected"] is False
    # 사유 문구는 **서버가** 만든다 — UI가 분기하면 화면마다 표현이 갈린다
    assert missing["unavailable_reason"] == "게스트 도구 미설치"


async def test_unknown_sort_column_returns_422(client: AsyncClient, admin: str) -> None:
    await _login(client, "admin", ADMIN_PASSWORD)
    res = await client.get("/api/v1/virtual-machines?sort_by=name;DROP+TABLE+users")
    assert res.status_code == 422
    assert res.json()["detail"]["field"] == "sort_by"


async def test_paging_has_no_gap_or_duplicate(client: AsyncClient, admin: str, session) -> None:
    """정렬 타이브레이커가 없으면 페이지 경계에서 행이 새거나 중복된다."""
    from datetime import UTC, datetime

    from src.infrastructure.repository.vm_repo import VirtualMachineRepository
    from tests.fakes.fake_reader import make_vm

    await _login(client, "admin", ADMIN_PASSWORD)
    conn = await _create_connection(client, "vCenter A", "vcsa-a.example.invalid")

    repo = VirtualMachineRepository(session)
    # 이름이 모두 같아 정렬 컬럼만으로는 순서가 정해지지 않는 최악 조건
    await repo.upsert_virtual_machines(
        conn,
        [make_vm(connection_id=conn, name="same-name", native_id=f"n{i}") for i in range(10)],
        datetime.now(UTC),
    )
    await session.commit()

    seen: list[str] = []
    for offset in range(0, 10, 3):
        body = (await client.get(f"/api/v1/virtual-machines?offset={offset}&limit=3")).json()
        seen.extend(item["resource_id"] for item in body["items"])

    assert len(seen) == 10
    assert len(set(seen)) == 10


async def test_me_exposes_permissions_and_accessible_connections(
    client: AsyncClient, admin: str
) -> None:
    await _login(client, "admin", ADMIN_PASSWORD)
    await _create_connection(client, "vCenter A", "vcsa-a.example.invalid")

    body = (await client.get("/api/v1/auth/me")).json()
    assert "connection.manage" in body["permissions"]
    assert body["user"]["scopes"] is None  # admin은 무제한
    assert len(body["accessible_connections"]) == 1
    assert set(body["accessible_connections"][0]) == {"connection_id", "display_name"}


# ── 감사 로그 ────────────────────────────────────────────────


async def test_audit_records_login_and_connection_create(
    client: AsyncClient, admin: str, session
) -> None:
    from sqlalchemy import select

    from src.infrastructure.db.models import AuditEventRow

    await _login(client, "admin", ADMIN_PASSWORD)
    await _create_connection(client, "vCenter A", "vcsa-a.example.invalid")

    rows = (await session.execute(select(AuditEventRow))).scalars().all()
    actions = {r.action for r in rows}
    assert "login" in actions
    assert "connection.create" in actions

    # 감사 detail에 비밀번호가 들어갈 경로가 없어야 한다
    for row in rows:
        assert "connection-secret-1234" not in str(row.detail)


async def test_audit_records_client_ip_for_every_action(
    client: AsyncClient, admin: str, session
) -> None:
    """`client_ip`가 비면 "누가 어디서 했는가"의 절반이 사라진다 (FR-1004).

    사용자 관리 액션만 비는 일이 있었다 — 연결 액션과 같은 수준으로 남겨야
    운영자가 `docs/05_deployment.md` §11-10으로 프록시 설정을 판정할 수 있다.
    """
    from sqlalchemy import select

    from src.infrastructure.db.models import AuditEventRow

    await client.post(
        "/api/v1/auth/register", json={"username": "park.view", "password": VIEWER_PASSWORD}
    )
    await _login(client, "admin", ADMIN_PASSWORD)
    conn = await _create_connection(client, "vCenter A", "vcsa-a.example.invalid")
    viewer_id = await _approve_viewer(client, "park.view", [str(conn)])
    await client.put(f"/api/v1/users/{viewer_id}/scopes", json={"connection_ids": []})

    rows = (await session.execute(select(AuditEventRow))).scalars().all()
    recorded = {r.action for r in rows}
    assert {"login", "connection.create", "user.approve", "scope.update"} <= recorded

    missing = sorted(r.action for r in rows if r.client_ip is None)
    assert not missing, f"client_ip가 비어 있는 액션: {missing}"


async def test_delete_connection_with_vms_is_refused(
    client: AsyncClient, admin: str, session
) -> None:
    from datetime import UTC, datetime

    from src.infrastructure.repository.vm_repo import VirtualMachineRepository
    from tests.fakes.fake_reader import make_vm

    await _login(client, "admin", ADMIN_PASSWORD)
    conn = await _create_connection(client, "vCenter A", "vcsa-a.example.invalid")

    repo = VirtualMachineRepository(session)
    await repo.upsert_virtual_machines(
        conn, [make_vm(connection_id=conn, name="a-vm", native_id="a1")], datetime.now(UTC)
    )
    await session.commit()

    res = await client.delete(f"/api/v1/connections/{conn}")
    assert res.status_code == 422
    assert res.json()["detail"]["vm_count"] == 1


async def test_temporary_password_forces_change_before_inventory(
    client: AsyncClient, admin: str, new_client
) -> None:
    await client.post(
        "/api/v1/auth/register", json={"username": "park.view", "password": VIEWER_PASSWORD}
    )
    await _login(client, "admin", ADMIN_PASSWORD)
    viewer_id = await _approve_viewer(client, "park.view", [])
    res = await client.post(f"/api/v1/users/{viewer_id}/reset-password")
    assert res.status_code == 200
    temporary = res.json()["temporary_password"]

    async with new_client() as viewer_client:
        await _login(viewer_client, "park.view", temporary)
        assert (await viewer_client.get("/api/v1/virtual-machines")).status_code == 403

        assert (
            await viewer_client.post(
                "/api/v1/auth/change-password",
                json={"current_password": temporary, "new_password": "brand-new-password-1"},
            )
        ).status_code == 204
        assert (await viewer_client.get("/api/v1/virtual-machines")).status_code == 200
