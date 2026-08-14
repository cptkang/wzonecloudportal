/* 셸 — 사이드바·헤더·테마. docs/03_design_system.md §3.1
 * 페이지는 <body data-page="inventory|connections">로 자신을 알린다.
 */

const THEME_KEY = 'inv-portal-theme';

function initTheme() {
  let theme = 'light';
  try {
    const saved = window.localStorage.getItem(THEME_KEY);
    if (saved === 'dark' || saved === 'light') theme = saved;
  } catch (e) { /* 스토리지 차단 환경 */ }
  document.documentElement.dataset.theme = theme;
  return theme;
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  try { window.localStorage.setItem(THEME_KEY, next); } catch (e) { /* 무시 */ }
  renderThemeButton();
}

function renderThemeButton() {
  const btn = document.getElementById('theme-toggle');
  if (!btn) return;
  const dark = document.documentElement.dataset.theme === 'dark';
  btn.innerHTML = '<span></span><span></span>';
  btn.children[0].textContent = dark ? '☀' : '☾';
  btn.children[1].textContent = dark ? '라이트 테마로' : '다크 테마로';
}

/* ── 인증 가드 ─────────────────────────────────────────────
 * 각 페이지가 알아서 확인하게 만들면 새 화면에서 빠뜨린다. 여기 한 곳에 둔다.
 */

let session = null;   // { user, permissions[], accessible_connections[] }

function can(permission) {
  return Boolean(session && session.permissions.includes(permission));
}

/**
 * 세션을 확인하고 셸을 그린다. 인증되지 않았으면 로그인 화면으로 보낸다.
 * @param {string} page  사이드바 활성 항목
 * @param {string|null} requiredPermission  없으면 자원 목록으로 되돌린다
 * @returns {Promise<object|null>} 세션. 리다이렉트가 일어나면 null
 */
async function requireSession(page, requiredPermission = null) {
  try {
    session = await api.me();
  } catch (err) {
    location.replace('login.html');
    return null;
  }

  // 임시 비밀번호 사용자는 변경 전까지 다른 화면을 쓸 수 없다 (FR-1008)
  if (session.user.must_change_password && page !== 'password') {
    location.replace('login.html?change=1');
    return null;
  }

  if (requiredPermission && !can(requiredPermission)) {
    location.replace('index.html');
    return null;
  }

  renderShell(page);
  return session;
}

function renderShell(page) {
  const sidebar = document.getElementById('sidebar');
  const user = session ? session.user : null;

  // 권한 없는 메뉴는 아예 렌더링하지 않는다 (FR-1213).
  // 숨기는 것은 편의일 뿐이고 실제 차단은 API가 한다.
  const adminNav = can('connection.manage') || can('user.manage') ? `
      <div class="sidebar__group">관리</div>
      ${can('connection.manage') ? `
      <a class="nav-item" href="connections.html" data-nav="connections">
        <span>하이퍼바이저 연결</span>
        <span class="nav-item__count" id="nav-conn-count"></span>
      </a>` : ''}
      ${can('user.manage') ? `
      <a class="nav-item" href="users.html" data-nav="users">
        <span>사용자</span>
        <span class="nav-item__count" id="nav-user-count"></span>
      </a>` : ''}
  ` : '';

  sidebar.innerHTML = `
    <div class="sidebar__brand">
      <span class="sidebar__mark">IV</span>
      <span class="sidebar__titles">
        <span class="sidebar__title">가상자원 인벤토리</span>
        <span class="sidebar__kicker">READ-ONLY PORTAL</span>
      </span>
    </div>

    <nav class="sidebar__nav">
      <div class="sidebar__group">조회</div>
      <a class="nav-item" href="index.html" data-nav="inventory">
        <span>가상 머신</span>
        <span class="nav-item__count" id="nav-vm-count"></span>
      </a>
      ${adminNav}
    </nav>

    <div class="sidebar__foot">
      <div class="sidebar__user">
        <span class="sidebar__user-info">
          <span class="sidebar__user-name">${escapeHtml(user ? (user.display_name || user.username) : '')}</span>
          <span class="role role--${user ? user.role : 'viewer'}">${roleLabel(user ? user.role : '')}</span>
        </span>
        <button type="button" class="btn btn--sm" id="logout">로그아웃</button>
      </div>
      <div class="sidebar__notice">이 포탈은 자원을 조회만 합니다. 전원·스냅샷·리소스 변경 기능은 제공하지 않습니다.</div>
      <button type="button" class="btn btn--quiet" id="theme-toggle"></button>
    </div>
  `;

  const active = sidebar.querySelector(`[data-nav="${page}"]`);
  if (active) active.setAttribute('aria-current', 'page');

  document.getElementById('theme-toggle').addEventListener('click', toggleTheme);
  document.getElementById('logout').addEventListener('click', async () => {
    await api.logout();
    location.replace('login.html');
  });
  renderThemeButton();
}

const ROLE_LABEL = { admin: '관리자', operator: '운영자', viewer: '조회자' };
function roleLabel(role) { return ROLE_LABEL[role] || role || ''; }

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/** 헤더의 전체 수집 상태. 연결 중 가장 최근 성공 시각을 쓴다. */
function renderSyncStatus(connections) {
  const el = document.getElementById('sync-status');
  if (!el) return;

  const times = connections
    .map((c) => c.last_success_at)
    .filter(Boolean)
    .map((t) => Date.parse(t))
    .filter((t) => !Number.isNaN(t));

  if (times.length === 0) {
    el.innerHTML = '<span class="topbar__dot topbar__dot--none"></span><span>수집 이력 없음</span>';
    return;
  }

  const latest = new Date(Math.max(...times)).toISOString();
  const mins = minutesSince(latest);
  const stale = mins !== null && mins > STALE_MINUTES;

  el.innerHTML = `
    <span class="topbar__dot ${stale ? 'topbar__dot--warn' : ''}"></span>
    <span>전체 수집</span>
    <span class="topbar__at"></span>
    <span class="topbar__sep">·</span>
    <span></span>
  `;
  el.querySelector('.topbar__at').textContent = fmtDateTime(latest);
  el.lastElementChild.textContent = agoText(mins);
}

function setNavCounts({ vmCount, connCount, userCount }) {
  const vm = document.getElementById('nav-vm-count');
  const conn = document.getElementById('nav-conn-count');
  const user = document.getElementById('nav-user-count');
  if (vm && vmCount !== undefined) vm.textContent = fmtNumber(vmCount);
  if (conn && connCount !== undefined) conn.textContent = fmtNumber(connCount);
  if (user && userCount !== undefined) user.textContent = fmtNumber(userCount);
}

/* ── 계정 상태 표시 (docs/03_design_system.md §2.5~2.7) ────── */

const ACCOUNT_STATUS = {
  pending: { label: '승인 대기', icon: '◴', cls: 'acct--pending' },
  active: { label: '활성', icon: '●', cls: 'acct--active' },
  disabled: { label: '비활성', icon: '■', cls: 'acct--disabled' },
  rejected: { label: '거부됨', icon: '✕', cls: 'acct--rejected' },
};

function accountStatusEl(status) {
  const s = ACCOUNT_STATUS[status] || { label: status, icon: '', cls: '' };
  const el = document.createElement('span');
  el.className = 'acct ' + s.cls;
  el.innerHTML = '<span style="font-size:10px"></span><span></span>';
  el.children[0].textContent = s.icon;
  el.children[1].textContent = s.label;
  return el;
}

function roleBadgeEl(role) {
  const el = document.createElement('span');
  el.className = 'role role--' + role;
  el.textContent = roleLabel(role);
  return el;
}

/** 조회 범위. 승인됐지만 범위가 없으면 강조한다 (§2.7). */
function scopeEl(user) {
  const el = document.createElement('span');
  if (user.role === 'admin' || user.scopes === null) {
    el.className = 'scope scope--all';
    el.textContent = '전체';
  } else if (!user.scopes || user.scopes.length === 0) {
    el.className = 'scope scope--none';
    el.innerHTML = '<span style="font-size:10px">⚠</span><span>범위 없음</span>';
  } else {
    el.className = 'scope';
    el.textContent = `연결 ${user.scopes.length}개`;
  }
  return el;
}

/* ── 알림 ─────────────────────────────────────────────────── */

let toastTimer = null;

function showToast(message, isError = false) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = message;
  el.className = 'toast' + (isError ? ' toast--err' : '');
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4000);
}

initTheme();
