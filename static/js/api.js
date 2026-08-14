/* API 클라이언트 — plans/ROADMAP.md §9 엔드포인트.
 *
 * 백엔드(1-B)가 완성되어 실제 API를 호출한다. 목 구현은 아래에 남겨 두었으며
 * USE_MOCK = true로 바꾸면 백엔드 없이 화면만 확인할 수 있다.
 */

const USE_MOCK = false;
const BASE = '/api/v1';

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, options = {}) {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',   // 세션은 httpOnly 쿠키다 (D-014)
    ...options,
  });
  if (res.status === 204) return null;

  let body = null;
  try { body = await res.json(); } catch (e) { /* 본문 없음 */ }

  if (!res.ok) {
    throw new ApiError(errorMessage(body), res.status);
  }
  return body;
}

/* 오류 본문은 두 형태다.
 *  - 도메인 오류: {error, message, detail:{}}   (src/api/errors.py)
 *  - FastAPI 기본: {detail: "문구"} 또는 {detail: [검증 오류 …]}
 * detail을 그대로 쓰면 객체가 "[object Object]"로 출력되므로 형태를 가린다. */
function errorMessage(body) {
  if (!body) return '요청에 실패했습니다.';
  if (typeof body.message === 'string') return body.message;
  if (typeof body.detail === 'string') return body.detail;
  if (Array.isArray(body.detail) && body.detail.length > 0) {
    const first = body.detail[0];
    return first.msg || '입력값을 확인하세요.';
  }
  return '요청에 실패했습니다.';
}

/* ── 인증·계정 ────────────────────────────────────────────── */

const api = {
  async register(payload) {
    if (USE_MOCK) return mock.register(payload);
    return request('/auth/register', { method: 'POST', body: JSON.stringify(payload) });
  },

  async login(username, password) {
    if (USE_MOCK) return mock.login(username, password);
    return request('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    });
  },

  async logout() {
    if (USE_MOCK) return mock.logout();
    return request('/auth/logout', { method: 'POST' });
  },

  /** 현재 사용자 + permissions[]. UI 메뉴 노출 판단에 쓴다 (FR-1213) */
  async me() {
    if (USE_MOCK) return mock.me();
    return request('/auth/me');
  },

  async changePassword(currentPassword, newPassword) {
    if (USE_MOCK) return mock.changePassword(currentPassword, newPassword);
    return request('/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    });
  },

  /* ── 사용자 관리 (admin) ────────────────────────────────── */

  async listUsers(status = null) {
    if (USE_MOCK) return mock.listUsers(status);
    const q = status ? '?status=' + encodeURIComponent(status) : '';
    return request('/users' + q);
  },

  async approveUser(userId, role, connectionIds) {
    if (USE_MOCK) return mock.approveUser(userId, role, connectionIds);
    return request(`/users/${encodeURIComponent(userId)}/approve`, {
      method: 'POST',
      body: JSON.stringify({ role, connection_ids: connectionIds }),
    });
  },

  async rejectUser(userId, reason) {
    if (USE_MOCK) return mock.rejectUser(userId, reason);
    return request(`/users/${encodeURIComponent(userId)}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    });
  },

  /** 계정을 삭제하지 않는다. 비활성화만 한다 (D-014) */
  async setUserEnabled(userId, enabled) {
    if (USE_MOCK) return mock.setUserEnabled(userId, enabled);
    return request(`/users/${encodeURIComponent(userId)}/${enabled ? 'enable' : 'disable'}`, {
      method: 'POST',
    });
  },

  async changeUserRole(userId, role) {
    if (USE_MOCK) return mock.changeUserRole(userId, role);
    return request(`/users/${encodeURIComponent(userId)}`, {
      method: 'PATCH',
      body: JSON.stringify({ role }),
    });
  },

  async setUserScopes(userId, connectionIds) {
    if (USE_MOCK) return mock.setUserScopes(userId, connectionIds);
    return request(`/users/${encodeURIComponent(userId)}/scopes`, {
      method: 'PUT',
      body: JSON.stringify({ connection_ids: connectionIds }),
    });
  },

  async resetUserPassword(userId) {
    if (USE_MOCK) return mock.resetUserPassword(userId);
    return request(`/users/${encodeURIComponent(userId)}/reset-password`, { method: 'POST' });
  },

  /* ── 연결 ────────────────────────────────────────────────── */

  async listConnections() {
    if (USE_MOCK) return mock.listConnections();
    return request('/connections');
  },

  async testConnection(payload) {
    if (USE_MOCK) return mock.testConnection(payload);
    return request('/connections/test', { method: 'POST', body: JSON.stringify(payload) });
  },

  async createConnection(payload) {
    if (USE_MOCK) return mock.createConnection(payload);
    return request('/connections', { method: 'POST', body: JSON.stringify(payload) });
  },

  async deleteConnection(id) {
    if (USE_MOCK) return mock.deleteConnection(id);
    return request('/connections/' + encodeURIComponent(id), { method: 'DELETE' });
  },

  /** 수집은 202로 즉시 반환되고 진행 상황은 연결 목록 폴링으로 확인한다 (ROADMAP §9.1) */
  async startCollection(id) {
    if (USE_MOCK) return mock.startCollection(id);
    return request('/connections/' + encodeURIComponent(id) + '/collect', { method: 'POST' });
  },

  /* ── 가상 머신 ──────────────────────────────────────────── */

  /** 페이징은 offset/limit이다. page/size가 아니다 (ROADMAP §9.2) */
  async listVirtualMachines({ connectionId = null, offset = 0, limit = 50 } = {}) {
    if (USE_MOCK) return mock.listVirtualMachines({ connectionId, offset, limit });
    const q = new URLSearchParams({ offset: String(offset), limit: String(limit) });
    if (connectionId) q.set('connection_id', connectionId);
    return request('/virtual-machines?' + q.toString());
  },
};

/* ────────────────────────────────────────────────────────────
 * 목 데이터 — 백엔드 완성 전 화면 확인용.
 *
 * NFR-206: 실제 서버명·IP·도메인을 넣지 않는다. 아래 값은 모두 가상이다.
 * 필드 구성은 ROADMAP §7의 Step 1 스키마와 일치시킨다 — Step 4 이후 필드(IP·디스크·
 * 스냅샷)는 일부러 넣지 않는다. 넣으면 화면이 실제보다 완성되어 보인다.
 * ──────────────────────────────────────────────────────────── */

const mock = (() => {
  const now = Date.now();
  const iso = (minutesAgo) => new Date(now - minutesAgo * 60000).toISOString();

  /* 계정 (D-014). 실제 세션은 httpOnly 쿠키이며 JS가 읽을 수 없다.
     목에서는 페이지 이동 간 상태 유지를 위해 sessionStorage에 사용자명만 둔다. */
  const SESSION_KEY = 'mock-session-user';

  const ROLE_PERMISSIONS = {
    viewer: ['resource.read', 'export'],
    operator: ['resource.read', 'metadata.write', 'export'],
    admin: ['resource.read', 'metadata.write', 'export', 'connection.manage',
            'collection.trigger', 'user.manage', 'audit.read'],
  };

  /* 목 계정은 페이지를 새로 열면 초기화된다. 승인 → 조회로 이어지는 흐름을 확인하려면
     상태가 유지되어야 하므로 sessionStorage에 담는다. 실제 구현에서는 DB가 이 역할을 한다. */
  const USERS_KEY = 'mock-users';

  function persistUsers() {
    try { window.sessionStorage.setItem(USERS_KEY, JSON.stringify(users)); } catch (e) { /* 무시 */ }
  }

  function restoreUsers() {
    try {
      const raw = window.sessionStorage.getItem(USERS_KEY);
      if (raw) return JSON.parse(raw);
    } catch (e) { /* 무시 */ }
    return null;
  }

  let users = [
    {
      user_id: 'u1', username: 'admin', display_name: '관리자', email: 'admin@corp.local',
      role: 'admin', status: 'active', must_change_password: false,
      scopes: null,                       // admin은 무제한
      last_login_at: iso(5), created_at: iso(20160), password: 'admin1234!',
    },
    {
      user_id: 'u2', username: 'lee.ops', display_name: '이운영', email: 'lee@corp.local',
      role: 'operator', status: 'active', must_change_password: false,
      scopes: ['c1'],
      last_login_at: iso(180), created_at: iso(10080), password: 'operator1234!',
    },
    {
      user_id: 'u3', username: 'park.view', display_name: '박조회', email: 'park@corp.local',
      role: 'viewer', status: 'active', must_change_password: false,
      scopes: [],                         // 승인은 됐지만 범위 없음 — 화면에서 강조된다
      last_login_at: null, created_at: iso(4320), password: 'viewer1234!',
    },
    {
      user_id: 'u4', username: 'kim.dev', display_name: '김개발', email: 'kim@corp.local',
      role: 'viewer', status: 'pending', must_change_password: false,
      scopes: [], last_login_at: null, created_at: iso(120), password: 'devpass1234!',
    },
    {
      user_id: 'u5', username: 'choi.sec', display_name: '최보안', email: 'choi@corp.local',
      role: 'viewer', status: 'pending', must_change_password: false,
      scopes: [], last_login_at: null, created_at: iso(35), password: 'secpass1234!',
    },
    {
      user_id: 'u6', username: 'old.staff', display_name: '퇴사자', email: 'old@corp.local',
      role: 'viewer', status: 'disabled', must_change_password: false,
      scopes: ['c1'], last_login_at: iso(43200), created_at: iso(86400), password: 'x',
    },
  ];

  users = restoreUsers() || users;

  const STATUS_MESSAGE = {
    pending: '가입 신청이 검토 중입니다. 관리자 승인 후 로그인할 수 있습니다.',
    rejected: '가입이 승인되지 않았습니다. 관리자에게 문의하세요.',
    disabled: '비활성화된 계정입니다. 관리자에게 문의하세요.',
  };

  function currentUser() {
    let username = null;
    try { username = window.sessionStorage.getItem(SESSION_KEY); } catch (e) { /* 차단 환경 */ }
    if (!username) return null;
    return users.find((u) => u.username === username) || null;
  }

  function toResponse(u) {
    return {
      user_id: u.user_id, username: u.username, display_name: u.display_name,
      email: u.email, role: u.role, status: u.status,
      must_change_password: u.must_change_password,
      scopes: u.role === 'admin' ? null : (u.scopes || []),
      last_login_at: u.last_login_at, created_at: u.created_at,
    };
  }

  /** 요청자의 조회 범위. admin은 null(무제한). */
  function scopeOf(u) {
    if (!u) return [];
    return u.role === 'admin' ? null : (u.scopes || []);
  }

  /** 대상을 제외한 활성 관리자 수. 마지막 관리자 보호에 쓴다 (계획 09 §4.6.1). */
  function activeAdminCount(excludeUserId) {
    return users.filter(
      (u) => u.role === 'admin' && u.status === 'active' && u.user_id !== excludeUserId
    ).length;
  }

  function hash(s) {
    let h = 0;
    for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) | 0;
    return h;
  }

  let connections = [
    {
      connection_id: 'c1', kind: 'vcenter', display_name: 'vCenter 본사 (DC1)',
      address: 'vcsa-dc1.corp.local', port: 443, username: 'svc-inventory@vsphere.local',
      verify_tls: true, last_attempt_at: iso(12), last_success_at: iso(12),
      last_error: null, vm_count: 9,
    },
    {
      connection_id: 'c2', kind: 'vcenter', display_name: 'vCenter DR (DC3)',
      address: 'vcsa-dc3.corp.local', port: 443, username: 'svc-inventory@vsphere.local',
      verify_tls: false, last_attempt_at: iso(30), last_success_at: iso(4260),
      last_error: '인증에 실패했습니다. 계정 또는 비밀번호를 확인하세요.', vm_count: 3,
    },
  ];

  /* 전원 상태는 도메인 값(on/off/suspended/unknown)이다 (계획 02 §3).
     사유 문구는 서버가 만든다 — 목도 같은 문구를 쓴다 (계획 02 §5.1 UNAVAILABLE_REASONS). */
  const UNAVAILABLE_REASON = {
    tools_not_installed: '게스트 도구 미설치',
    tools_not_running: '게스트 도구 미동작',
    unknown: '확인 필요',
  };

  const vms = [
    ['web-prd-01', 'c1', 'on', 4, 8192, 'Ubuntu 22.04 LTS', 'guest_tools', 'available', 'web-prd-01.corp.local', 'host-1042', 12],
    ['web-prd-02', 'c1', 'on', 4, 8192, 'Ubuntu 22.04 LTS', 'guest_tools', 'available', 'web-prd-02.corp.local', 'host-1043', 12],
    ['api-prd-01', 'c1', 'on', 8, 16384, null, null, 'tools_not_running', null, 'host-1041', 12],
    ['batch-prd-01', 'c1', 'off', 8, 32768, 'Rocky Linux 8', 'vm_config', 'unknown', null, 'host-1047', 12],
    ['db-prd-01', 'c1', 'on', 16, 131072, 'Oracle Linux 8.9', 'guest_tools', 'available', 'db-prd-01.corp.local', 'host-1048', 12],
    ['dev-jenkins', 'c1', 'on', 8, 16384, 'Debian 12', 'guest_tools', 'available', 'dev-jenkins.corp.local', 'host-1052', 12],
    ['dev-sandbox-14', 'c1', 'suspended', 2, 8192, 'Windows 11 23H2', 'vm_config', 'tools_not_running', null, 'host-1055', 12],
    ['dev-sandbox-15', 'c1', 'off', 2, 4096, null, null, 'tools_not_installed', null, 'host-1055', 12],
    ['legacy-erp-01', 'c1', 'on', 8, 24576, null, null, 'tools_not_installed', null, 'host-1061', 12],
    ['dmz-relay-01', 'c2', 'on', 2, 2048, 'Alpine 3.20', 'guest_tools', 'available', 'dmz-relay-01.corp.local', 'host-2011', 4260],
    ['mon-graf-01', 'c2', 'on', 4, 8192, 'Ubuntu 24.04 LTS', 'guest_tools', 'available', 'mon-graf-01.corp.local', 'host-2012', 4260],
    ['bkp-proxy-01', 'c2', 'on', 6, 12288, 'Windows Server 2019', 'vm_config', 'tools_not_running', null, 'host-2013', 4260],
  ].map((r, i) => {
    const availability = r[7];
    const collected = availability === 'available';
    return {
      resource_id: 'vm-' + (i + 1),
      connection_id: r[1],
      connection_name: r[1] === 'c1' ? 'vCenter 본사 (DC1)' : 'vCenter DR (DC3)',
      hypervisor: 'vcenter',
      native_id: '5001' + String(1000 + i * 37),
      name: r[0],
      power_state: r[2],
      vcpu_count: r[3],
      memory_mb: r[4],
      // 게스트 정보는 중첩 객체다 (계획 08 §6.3)
      guest: {
        availability,
        is_collected: collected,
        unavailable_reason: collected ? null : UNAVAILABLE_REASON[availability],
        os_name: r[5],
        os_source: r[6],
        hostname: r[8],
        observed_at: collected ? iso(r[10]) : iso(r[10] + 4320),
      },
      host_native_id: r[9],
      lifecycle: 'active',
      last_seen_at: iso(r[10]),
    };
  });

  const delay = (ms) => new Promise((r) => setTimeout(r, ms));

  function requireAdmin() {
    const me = currentUser();
    if (!me) throw new ApiError('로그인이 필요합니다.', 401);
    if (me.role !== 'admin') throw new ApiError('권한이 없습니다.', 403);
    return me;
  }

  return {
    /* ── 인증 ────────────────────────────────────────────── */

    async register(payload) {
      await delay(400);
      const username = (payload.username || '').trim().toLowerCase();
      // 중복이어도 같은 응답을 준다. 다르면 가입 폼이 계정 열거 수단이 된다 (계획 09 §4.5)
      if (!users.some((u) => u.username === username)) {
        users.push({
          user_id: 'u' + (users.length + 1), username,
          display_name: payload.display_name, email: payload.email,
          role: 'viewer', status: 'pending', must_change_password: false,
          scopes: [], last_login_at: null,
          created_at: new Date().toISOString(), password: payload.password,
        });
        persistUsers();
      }
      return { message: '가입 신청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다.' };
    },

    async login(username, password) {
      await delay(500);
      const u = users.find((x) => x.username === (username || '').trim().toLowerCase());

      // 계정 없음과 비밀번호 불일치를 구분하지 않는다
      if (!u || u.password !== password) {
        throw new ApiError('아이디 또는 비밀번호가 올바르지 않습니다.', 401);
      }
      // 상태 사유는 비밀번호가 맞은 뒤에만 알려준다 (D-014 §4번)
      if (u.status !== 'active') {
        throw new ApiError(STATUS_MESSAGE[u.status], 403);
      }

      u.last_login_at = new Date().toISOString();
      persistUsers();
      try { window.sessionStorage.setItem(SESSION_KEY, u.username); } catch (e) { /* 무시 */ }
      return { user: toResponse(u) };
    },

    async logout() {
      await delay(80);
      try { window.sessionStorage.removeItem(SESSION_KEY); } catch (e) { /* 무시 */ }
      return null;
    },

    async me() {
      await delay(60);
      const u = currentUser();
      if (!u) throw new ApiError('로그인이 필요합니다.', 401);

      // 상태를 매 요청 재확인한다. 비활성화·거부된 계정이 토큰 만료까지
      // 계속 접근하면 권한 회수가 즉시 이뤄지지 않는다 (계획 09 §6).
      if (u.status !== 'active') {
        try { window.sessionStorage.removeItem(SESSION_KEY); } catch (e) { /* 무시 */ }
        throw new ApiError('세션이 유효하지 않습니다. 다시 로그인하세요.', 401);
      }

      const allowed = scopeOf(u);
      return {
        user: toResponse(u),
        permissions: ROLE_PERMISSIONS[u.role],
        // 조회 화면의 연결 필터용. /connections는 admin 전용이라 여기서 내려준다.
        // 자격증명·상태는 넣지 않는다 — 식별에 필요한 최소 정보만.
        accessible_connections: connections
          .filter((c) => allowed === null || allowed.includes(c.connection_id))
          .map((c) => ({ connection_id: c.connection_id, display_name: c.display_name })),
      };
    },

    async changePassword(currentPassword, newPassword) {
      await delay(300);
      const u = currentUser();
      if (!u) throw new ApiError('로그인이 필요합니다.', 401);
      if (u.password !== currentPassword) {
        throw new ApiError('현재 비밀번호가 올바르지 않습니다.', 400);
      }
      u.password = newPassword;
      u.must_change_password = false;
      persistUsers();
      return null;
    },

    /* ── 사용자 관리 ─────────────────────────────────────── */

    async listUsers(status) {
      await delay(150);
      requireAdmin();
      const items = users
        .filter((u) => !status || u.status === status)
        .map(toResponse);
      return { items, total: items.length };
    },

    async approveUser(userId, role, connectionIds) {
      await delay(250);
      const actor = requireAdmin();
      const u = users.find((x) => x.user_id === userId);
      if (!u) throw new ApiError('사용자를 찾을 수 없습니다.', 404);
      if (u.status !== 'pending') {
        throw new ApiError('승인 대기 상태의 계정만 승인할 수 있습니다.', 400);
      }
      u.status = 'active';
      u.role = role;
      u.scopes = role === 'admin' ? null : (connectionIds || []);
      u.approved_by = actor.username;
      persistUsers();
      return toResponse(u);
    },

    async rejectUser(userId, reason) {
      await delay(200);
      requireAdmin();
      const u = users.find((x) => x.user_id === userId);
      if (!u) throw new ApiError('사용자를 찾을 수 없습니다.', 404);
      u.status = 'rejected';
      u.reject_reason = reason || null;
      persistUsers();
      return toResponse(u);
    },

    async setUserEnabled(userId, enabled) {
      await delay(200);
      const actor = requireAdmin();
      const u = users.find((x) => x.user_id === userId);
      if (!u) throw new ApiError('사용자를 찾을 수 없습니다.', 404);
      if (u.user_id === actor.user_id) {
        throw new ApiError('자기 자신의 계정 상태는 변경할 수 없습니다.', 400);
      }
      if (!enabled && u.role === 'admin' && activeAdminCount(u.user_id) === 0) {
        throw new ApiError('마지막 관리자입니다. 다른 관리자를 지정한 뒤에 변경하세요.', 400);
      }
      u.status = enabled ? 'active' : 'disabled';
      persistUsers();
      return toResponse(u);
    },

    async changeUserRole(userId, role) {
      await delay(200);
      const actor = requireAdmin();
      const u = users.find((x) => x.user_id === userId);
      if (!u) throw new ApiError('사용자를 찾을 수 없습니다.', 404);
      if (u.user_id === actor.user_id) {
        throw new ApiError('자기 자신의 역할은 변경할 수 없습니다.', 400);
      }
      if (u.role === 'admin' && role !== 'admin' && activeAdminCount(u.user_id) === 0) {
        throw new ApiError('마지막 관리자입니다. 다른 관리자를 지정한 뒤에 변경하세요.', 400);
      }
      u.role = role;
      if (role === 'admin') u.scopes = null;
      else if (u.scopes === null) u.scopes = [];
      persistUsers();
      return toResponse(u);
    },

    async setUserScopes(userId, connectionIds) {
      await delay(200);
      requireAdmin();
      const u = users.find((x) => x.user_id === userId);
      if (!u) throw new ApiError('사용자를 찾을 수 없습니다.', 404);
      if (u.role === 'admin') throw new ApiError('관리자는 범위 제한을 받지 않습니다.', 400);
      u.scopes = connectionIds || [];
      persistUsers();
      return toResponse(u);
    },

    async resetUserPassword(userId) {
      await delay(300);
      requireAdmin();
      const u = users.find((x) => x.user_id === userId);
      if (!u) throw new ApiError('사용자를 찾을 수 없습니다.', 404);
      // 실제 구현은 서버가 생성한다. 값은 응답에 1회만 실리고 저장·로깅하지 않는다
      const temp = 'Tmp-' + Math.abs(hash(u.username + Date.now())).toString(36).slice(0, 8) + '!';
      u.password = temp;
      u.must_change_password = true;
      persistUsers();
      return { temporary_password: temp };
    },

    /* ── 연결 ────────────────────────────────────────────── */

    async listConnections() {
      await delay(120);
      requireAdmin();       // 연결 관리는 관리자 전용 (NFR-210)
      return connections.map((c) => ({ ...c }));
    },

    async testConnection(payload) {
      await delay(700);
      const bad = (payload.password || '').length < 4;
      return {
        stages: [
          { stage: 'reachable', passed: true, detail: payload.address + ':' + (payload.port || 443) },
          { stage: 'tls_valid', passed: true, detail: payload.verify_tls ? '인증서 검증 통과' : '검증 안 함' },
          { stage: 'authenticated', passed: !bad, detail: bad ? '인증에 실패했습니다. 계정 또는 비밀번호를 확인하세요.' : payload.username },
          { stage: 'authorized', passed: !bad, detail: bad ? '' : '조회 가능: virtual_machine' },
        ],
        readable_types: bad ? [] : ['virtual_machine'],
        server_version: bad ? null : 'VMware vCenter Server 7.0.3',
      };
    },

    async createConnection(payload) {
      await delay(300);
      if (connections.some((c) => c.address === payload.address && c.username === payload.username)) {
        throw new ApiError('같은 주소와 계정으로 등록된 연결이 이미 있습니다.', 409);
      }
      const created = {
        connection_id: 'c' + (connections.length + 1 + Math.floor(Math.random() * 1000)),
        kind: 'vcenter',
        display_name: payload.display_name,
        address: payload.address,
        port: payload.port || 443,
        username: payload.username,
        verify_tls: payload.verify_tls !== false,
        last_attempt_at: null, last_success_at: null, last_error: null, vm_count: 0,
      };
      connections.push(created);
      return created;
    },

    async deleteConnection(id) {
      await delay(200);
      const conn = connections.find((c) => c.connection_id === id);
      if (conn && conn.vm_count > 0) {
        // 실제 API는 ON DELETE RESTRICT를 409로 변환한다 (ROADMAP §9.1)
        // 목에서는 인벤토리 기록까지 함께 지우는 동작을 흉내 낸다
      }
      connections = connections.filter((c) => c.connection_id !== id);
      return null;
    },

    async startCollection(id) {
      await delay(200);
      const conn = connections.find((c) => c.connection_id === id);
      if (conn) conn.last_attempt_at = new Date().toISOString();
      // 실제 수집은 백그라운드로 진행된다. 목에서는 2초 뒤 완료로 표시한다.
      setTimeout(() => {
        if (!conn) return;
        conn.last_success_at = new Date().toISOString();
        conn.last_error = null;
      }, 2000);
      return { status: 'accepted' };
    },

    async listVirtualMachines({ connectionId, offset, limit }) {
      await delay(160);
      const me = currentUser();
      if (!me) throw new ApiError('로그인이 필요합니다.', 401);

      const known = new Set(connections.map((c) => c.connection_id));
      let items = vms.filter((v) => known.has(v.connection_id));

      // 조회 범위 적용. 실제 구현은 SQL에서 필터한다 (계획 09 §3.2)
      const allowed = scopeOf(me);
      if (allowed !== null) items = items.filter((v) => allowed.includes(v.connection_id));

      // 범위 밖 connection_id는 403이 아니라 빈 결과다 — 403은 연결의 존재를 알려준다
      if (connectionId) items = items.filter((v) => v.connection_id === connectionId);

      const total = items.length;
      return { items: items.slice(offset, offset + limit), total, offset, limit };
    },
  };
})();
