/* 사용자 관리 (FR-1006·1007·1008) — 관리자 전용
 *
 * 승인은 역할과 조회 범위를 함께 정하는 한 번의 동작이다. 따로 부여하게 만들면
 * 범위 없는 계정이 방치되고, 그 사용자는 로그인해도 빈 화면만 본다.
 */

const admin = {
  users: [],
  connections: [],      // 범위 선택지
  target: null,         // 다이얼로그 대상 사용자
  onConfirm: null,
};

/* ── 목록 ─────────────────────────────────────────────────── */

function pendingBlock(pending) {
  if (pending.length === 0) return null;   // 빈 블록은 상시 경고처럼 보인다

  const box = document.createElement('div');
  box.className = 'pending-box';

  const head = document.createElement('div');
  head.className = 'pending-box__head';
  head.innerHTML = '<span>◴</span><span></span>';
  head.children[1].textContent = `승인 대기 ${pending.length}건`;
  box.appendChild(head);

  pending.forEach((u) => {
    const row = document.createElement('div');
    row.className = 'pending-box__row';

    const name = document.createElement('span');
    name.style.fontFamily = 'var(--mono)';
    name.textContent = u.username;

    const display = document.createElement('span');
    display.textContent = u.display_name || '—';

    const email = document.createElement('span');
    email.style.color = 'var(--fg2)';
    email.textContent = u.email || '—';

    const when = document.createElement('span');
    when.style.color = 'var(--fg3)';
    when.style.fontSize = '11.5px';
    when.textContent = agoText(minutesSince(u.created_at));

    const actions = document.createElement('span');
    actions.className = 'pending-box__actions';
    const approve = document.createElement('button');
    approve.type = 'button';
    approve.className = 'btn btn--primary btn--sm';   // 이 블록에서 가장 중요한 동작
    approve.textContent = '승인';
    approve.addEventListener('click', () => openApprove(u));
    const reject = document.createElement('button');
    reject.type = 'button';
    reject.className = 'btn btn--danger';
    reject.textContent = '거부';
    reject.addEventListener('click', () => confirmReject(u));
    actions.append(approve, reject);

    row.append(name, display, email, when, actions);
    box.appendChild(row);
  });

  return box;
}

function userRow(u) {
  const isSelf = u.user_id === session.user.user_id;

  const row = document.createElement('div');
  row.className = 'table__row cols-user';
  row.style.alignItems = 'center';

  const username = document.createElement('div');
  username.style.fontFamily = 'var(--mono)';
  username.style.fontSize = '12.5px';
  username.textContent = u.username;

  const display = document.createElement('div');
  display.className = 'cell';
  const dn = document.createElement('span');
  dn.className = 'cell__text';
  dn.textContent = u.display_name || '—';
  const em = document.createElement('span');
  em.className = 'cell__sub';
  em.style.fontFamily = 'var(--sans)';
  em.textContent = u.email || '';
  display.append(dn, em);

  const role = document.createElement('div');
  role.appendChild(roleBadgeEl(u.role));

  const status = document.createElement('div');
  status.appendChild(accountStatusEl(u.status));

  const scope = document.createElement('div');
  scope.appendChild(scopeEl(u));

  // 수집 신선도(collectedEl)를 쓰지 않는다. 오래 로그인하지 않은 것은 경고할 일이 아니다.
  const login = document.createElement('div');
  if (u.last_login_at) {
    login.className = 'last-login';
    const ago = document.createElement('span');
    ago.className = 'last-login__ago';
    ago.textContent = agoText(minutesSince(u.last_login_at));
    const at = document.createElement('span');
    at.className = 'last-login__at';
    at.textContent = fmtDateShort(u.last_login_at);
    at.title = fmtDateTime(u.last_login_at);
    login.append(ago, at);
  } else {
    login.innerHTML = '<span class="state state--empty">로그인 이력 없음</span>';
  }

  const actions = document.createElement('div');
  actions.className = 'cell__actions';

  // 본인 계정에는 역할 변경·비활성화 버튼을 표시하지 않는다 (§3.8)
  if (!isSelf && u.status !== 'pending' && u.status !== 'rejected') {
    if (u.role !== 'admin') {
      actions.appendChild(button('범위', 'btn--sm', () => openScope(u)));
    }
    actions.appendChild(button('역할', 'btn--sm', () => confirmRoleChange(u)));
    actions.appendChild(button('비밀번호', 'btn--sm', () => confirmResetPassword(u)));
    actions.appendChild(
      u.status === 'active'
        ? button('비활성화', 'btn--danger', () => confirmDisable(u))
        : button('재활성화', 'btn--sm', () => confirmEnable(u))
    );
  }

  row.append(username, display, role, status, scope, login, actions);
  return row;
}

function button(label, cls, onClick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'btn ' + cls;
  b.textContent = label;
  b.addEventListener('click', onClick);
  return b;
}

function render() {
  const pendingArea = document.getElementById('pending-area');
  pendingArea.innerHTML = '';
  const pending = admin.users.filter((u) => u.status === 'pending');
  const block = pendingBlock(pending);
  if (block) pendingArea.appendChild(block);

  const host = document.getElementById('user-rows');
  host.innerHTML = '';
  const rest = admin.users.filter((u) => u.status !== 'pending');
  if (rest.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'table__empty';
    empty.textContent = '등록된 사용자가 없습니다.';
    host.appendChild(empty);
  } else {
    const frag = document.createDocumentFragment();
    rest.forEach((u) => frag.appendChild(userRow(u)));
    host.appendChild(frag);
  }

  setNavCounts({ userCount: admin.users.length });
}

async function reload() {
  try {
    const res = await api.listUsers();
    admin.users = res.items;
    render();
  } catch (err) {
    showToast(err.message || '사용자 목록을 불러오지 못했습니다.', true);
  }
}

/* ── 승인 ─────────────────────────────────────────────────── */

function renderScopeCheckboxes(hostId, selected) {
  const host = document.getElementById(hostId);
  host.innerHTML = '';
  if (admin.connections.length === 0) {
    host.innerHTML = '<span class="field__hint">등록된 연결이 없습니다.</span>';
    return;
  }
  admin.connections.forEach((c) => {
    const label = document.createElement('label');
    label.className = 'checkbox';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.value = c.connection_id;
    input.checked = selected.includes(c.connection_id);
    const span = document.createElement('span');
    span.textContent = c.display_name;
    label.append(input, span);
    host.appendChild(label);
  });
}

function selectedScopes(hostId) {
  return Array.from(document.querySelectorAll(`#${hostId} input:checked`)).map((i) => i.value);
}

function openApprove(user) {
  admin.target = user;
  document.getElementById('approve-name').textContent = user.display_name || user.username;
  document.querySelector('input[name="approve-role"][value="viewer"]').checked = true;
  renderScopeCheckboxes('approve-scopes', []);
  syncApproveRoleState();
  setError('approve-error', '');
  document.getElementById('approve-modal').hidden = false;
}

/** 관리자는 범위 제한을 받지 않으므로 선택을 비활성화한다 (계획 11 §13.3.1) */
function syncApproveRoleState() {
  const role = document.querySelector('input[name="approve-role"]:checked').value;
  const list = document.getElementById('approve-scopes');
  const hint = document.getElementById('approve-scope-hint');
  list.dataset.disabled = String(role === 'admin');
  hint.textContent = role === 'admin'
    ? '관리자는 범위 설정 없이 전체를 조회합니다.'
    : '선택하지 않으면 자원이 보이지 않습니다.';
}

async function confirmApprove() {
  const user = admin.target;
  const role = document.querySelector('input[name="approve-role"]:checked').value;
  const ids = role === 'admin' ? [] : selectedScopes('approve-scopes');

  // 범위 없이 승인해도 진행은 허용한다. 의도적인 보류일 수 있다.
  if (role !== 'admin' && ids.length === 0) {
    showToast('범위를 지정하지 않아 이 사용자에게는 자원이 보이지 않습니다.');
  }

  try {
    await api.approveUser(user.user_id, role, ids);
    document.getElementById('approve-modal').hidden = true;
    showToast(`${user.username} 계정을 승인했습니다.`);
    await reload();
  } catch (err) {
    setError('approve-error', err.message || '승인하지 못했습니다.');
  }
}

/* ── 범위 변경 ────────────────────────────────────────────── */

function openScope(user) {
  admin.target = user;
  document.getElementById('scope-name').textContent = user.display_name || user.username;
  renderScopeCheckboxes('scope-list', user.scopes || []);
  setError('scope-error', '');
  document.getElementById('scope-modal').hidden = false;
}

async function saveScope() {
  try {
    await api.setUserScopes(admin.target.user_id, selectedScopes('scope-list'));
    document.getElementById('scope-modal').hidden = true;
    showToast('조회 범위를 저장했습니다.');
    await reload();
  } catch (err) {
    setError('scope-error', err.message || '범위를 저장하지 못했습니다.');
  }
}

/* ── 확인 다이얼로그 ──────────────────────────────────────── */

function openConfirm({ title, body, okLabel, okClass = 'btn--primary', onOk }) {
  document.getElementById('confirm-title').textContent = title;
  document.getElementById('confirm-body').innerHTML = body;
  const ok = document.getElementById('btn-ok-confirm');
  ok.textContent = okLabel;
  ok.className = 'btn ' + okClass;
  admin.onConfirm = onOk;
  setError('confirm-error', '');
  document.getElementById('confirm-modal').hidden = false;
}

function confirmReject(user) {
  openConfirm({
    title: '가입을 거부할까요?',
    body: `<strong>${user.username}</strong> 님의 가입 신청을 거부합니다. 거부된 계정은 다시 승인할 수 없으며, 재신청은 새 계정으로 해야 합니다.`,
    okLabel: '거부',
    okClass: 'btn--danger-solid',
    onOk: () => api.rejectUser(user.user_id, null),
  });
}

function confirmDisable(user) {
  openConfirm({
    title: '계정을 비활성화할까요?',
    body: `<strong>${user.username}</strong> 님은 로그인할 수 없게 됩니다. 계정과 활동 기록은 삭제되지 않습니다.`,
    okLabel: '비활성화',
    okClass: 'btn--danger-solid',
    onOk: () => api.setUserEnabled(user.user_id, false),
  });
}

function confirmEnable(user) {
  openConfirm({
    title: '계정을 다시 활성화할까요?',
    body: `<strong>${user.username}</strong> 님이 다시 로그인할 수 있게 됩니다.`,
    okLabel: '재활성화',
    onOk: () => api.setUserEnabled(user.user_id, true),
  });
}

function confirmRoleChange(user) {
  const next = { viewer: 'operator', operator: 'admin', admin: 'viewer' };
  const target = next[user.role];
  openConfirm({
    title: '역할을 변경할까요?',
    body: `<strong>${user.username}</strong> 님의 역할을 <strong>${roleLabel(user.role)}</strong>에서 <strong>${roleLabel(target)}</strong>(으)로 변경합니다.`
      + (target === 'admin' ? ' 관리자는 모든 연결과 사용자를 관리할 수 있습니다.' : ''),
    okLabel: '변경',
    onOk: () => api.changeUserRole(user.user_id, target),
  });
}

function confirmResetPassword(user) {
  openConfirm({
    title: '임시 비밀번호를 발급할까요?',
    body: `<strong>${user.username}</strong> 님의 비밀번호를 임시 비밀번호로 바꿉니다. 기존 비밀번호는 사용할 수 없게 됩니다.`,
    okLabel: '발급',
    onOk: async () => {
      const res = await api.resetUserPassword(user.user_id);
      showSecret(user, res.temporary_password);
    },
  });
}

async function runConfirm() {
  try {
    await admin.onConfirm();
    document.getElementById('confirm-modal').hidden = true;
    await reload();
  } catch (err) {
    setError('confirm-error', err.message || '처리하지 못했습니다.');
  }
}

/* ── 임시 비밀번호 1회 표시 ───────────────────────────────── */

function showSecret(user, password) {
  document.getElementById('secret-name').textContent = user.display_name || user.username;
  document.getElementById('secret-value').textContent = password;
  document.getElementById('secret-modal').hidden = false;
}

async function copySecret() {
  try {
    await navigator.clipboard.writeText(document.getElementById('secret-value').textContent);
    showToast('복사했습니다.');
  } catch (err) {
    showToast('복사하지 못했습니다. 직접 선택해 복사하세요.', true);
  }
}

/* ── 공통 ─────────────────────────────────────────────────── */

function setError(id, message) {
  const el = document.getElementById(id);
  el.textContent = message || '';
  el.hidden = !message;
}

function closeAll() {
  ['approve-modal', 'scope-modal', 'confirm-modal', 'secret-modal']
    .forEach((id) => { document.getElementById(id).hidden = true; });
}

async function init() {
  // 관리자가 아니면 자원 목록으로 되돌린다. 실제 차단은 API가 한다.
  const me = await requireSession('users', 'user.manage');
  if (!me) return;

  admin.connections = me.accessible_connections || [];

  document.getElementById('btn-cancel-approve').addEventListener('click', closeAll);
  document.getElementById('btn-confirm-approve').addEventListener('click', confirmApprove);
  document.getElementById('approve-roles').addEventListener('change', syncApproveRoleState);

  document.getElementById('btn-cancel-scope').addEventListener('click', closeAll);
  document.getElementById('btn-save-scope').addEventListener('click', saveScope);

  document.getElementById('btn-cancel-confirm').addEventListener('click', closeAll);
  document.getElementById('btn-ok-confirm').addEventListener('click', runConfirm);

  document.getElementById('btn-copy-secret').addEventListener('click', copySecret);
  document.getElementById('btn-close-secret').addEventListener('click', closeAll);

  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeAll(); });

  await reload();
}

init();
