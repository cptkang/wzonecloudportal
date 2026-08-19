/* 하이퍼바이저 연결 관리 — FR-101·104·105·106·109·202
 *
 * 이 화면의 [삭제]는 포탈의 연결 레코드를 지운다. 하이퍼바이저 자원은 건드리지 않는다 (D-005).
 * 수집은 202로 시작되고 진행 상황은 목록 폴링으로 확인한다 (ROADMAP §9.1).
 */

const POLL_INTERVAL_MS = 3000;

const page = {
  connections: [],
  pendingDelete: null,
  collecting: new Set(),   // 수집 시작 후 완료를 기다리는 연결
  pollTimer: null,
};

/* ── 연결 상태 판정 (docs/03_design_system.md §2.4) ─────────── */

function connState(conn) {
  if (conn.last_error) {
    return { cls: 'conn-state--err', icon: '⚠', label: '수집 실패', sub: conn.last_error };
  }
  if (!conn.last_success_at) {
    return { cls: 'conn-state--warn', icon: '⚠', label: '수집 대기', sub: '아직 수집한 적이 없습니다.' };
  }
  const mins = minutesSince(conn.last_success_at);
  if (mins !== null && mins > STALE_MINUTES) {
    return { cls: 'conn-state--warn', icon: '⚠', label: '수집 지연', sub: '마지막 성공 이후 24시간이 지났습니다.' };
  }
  return { cls: 'conn-state--ok', icon: '●', label: '정상', sub: '' };
}

function connRow(conn) {
  const row = document.createElement('div');
  row.className = 'table__row cols-conn';
  row.style.alignItems = 'center';

  const name = document.createElement('div');
  name.style.fontSize = '13px';
  name.style.fontWeight = '500';
  name.textContent = conn.display_name;

  const kind = document.createElement('div');
  kind.appendChild(hvBadge(conn.kind));

  const endpoint = document.createElement('div');
  endpoint.className = 'cell';
  const ep = document.createElement('span');
  ep.className = 'cell__sub';
  ep.style.fontSize = '11px';
  ep.style.color = 'var(--fg2)';
  ep.textContent = conn.address + ':' + conn.port;
  const user = document.createElement('span');
  user.className = 'cell__sub';
  user.textContent = conn.username;
  endpoint.append(ep, user);

  const state = document.createElement('div');
  const st = connState(conn);
  const stateCell = document.createElement('div');
  stateCell.className = 'cell';
  const stateLine = document.createElement('span');
  stateLine.className = 'conn-state ' + st.cls;
  stateLine.innerHTML = '<span style="font-size:10px"></span><span></span>';
  stateLine.children[0].textContent = st.icon;
  stateLine.children[1].textContent = page.collecting.has(conn.connection_id) ? '수집 중…' : st.label;
  stateCell.appendChild(stateLine);
  if (st.sub) {
    const sub = document.createElement('span');
    sub.className = 'state__sub';
    sub.textContent = st.sub;
    stateCell.appendChild(sub);
  }
  state.appendChild(stateCell);

  const sync = document.createElement('div');
  sync.appendChild(collectedEl(conn.last_success_at));

  const count = document.createElement('div');
  count.className = 'cell--num';
  count.textContent = fmtNumber(conn.vm_count);

  const actions = document.createElement('div');
  actions.className = 'cell__actions';

  // 시안의 [설정](연결 수정)은 Step 3 범위라 [지금 수집]으로 대체한다 (§5-9)
  const collectBtn = document.createElement('button');
  collectBtn.type = 'button';
  collectBtn.className = 'btn btn--sm';
  collectBtn.textContent = '지금 수집';
  collectBtn.disabled = page.collecting.has(conn.connection_id);
  collectBtn.addEventListener('click', () => startCollection(conn));

  const delBtn = document.createElement('button');
  delBtn.type = 'button';
  delBtn.className = 'btn btn--danger';
  delBtn.textContent = '삭제';
  delBtn.addEventListener('click', () => askDelete(conn));

  actions.append(collectBtn, delBtn);
  row.append(name, kind, endpoint, state, sync, count, actions);
  return row;
}

function renderConnections() {
  const host = document.getElementById('conn-rows');
  host.innerHTML = '';

  if (page.connections.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'table__empty';
    empty.textContent = '등록된 연결이 없습니다. [연결 추가]로 vCenter 또는 Hyper-V/SCVMM을 등록하세요.';
    host.appendChild(empty);
  } else {
    const frag = document.createDocumentFragment();
    page.connections.forEach((c) => frag.appendChild(connRow(c)));
    host.appendChild(frag);
  }

  setNavCounts({
    connCount: page.connections.length,
    vmCount: page.connections.reduce((s, c) => s + (c.vm_count || 0), 0),
  });
  renderSyncStatus(page.connections);
}

async function reload() {
  try {
    page.connections = await api.listConnections();
    renderConnections();
  } catch (err) {
    showToast(err.message || '연결 목록을 불러오지 못했습니다.', true);
  }
}

/* ── 수집 (FR-202) ────────────────────────────────────────── */

async function startCollection(conn) {
  try {
    const res = await api.startCollection(conn.connection_id);
    page.collecting.add(conn.connection_id);
    renderConnections();
    // 서버가 중복 실행을 막는다 — 다른 탭이나 다른 관리자가 이미 시작했을 수 있다.
    // 오류가 아니므로 202로 오며, 어느 쪽이든 완료를 봐야 하니 폴링은 똑같이 시작한다.
    showToast(
      res && res.status === 'already_running'
        ? `${conn.display_name} 수집이 이미 진행 중입니다.`
        : `${conn.display_name} 수집을 시작했습니다.`
    );
    startPolling();
  } catch (err) {
    showToast(err.message || '수집을 시작하지 못했습니다.', true);
  }
}

/** 수집은 백그라운드로 진행되므로 완료를 목록 폴링으로 판정한다 */
function startPolling() {
  if (page.pollTimer) return;
  page.pollTimer = setInterval(async () => {
    const before = new Map(page.connections.map((c) => [c.connection_id, c.last_success_at]));
    await reload();

    page.connections.forEach((c) => {
      if (!page.collecting.has(c.connection_id)) return;
      const changed = before.get(c.connection_id) !== c.last_success_at;
      if (changed || c.last_error) {
        page.collecting.delete(c.connection_id);
        showToast(
          c.last_error ? `${c.display_name} 수집 실패: ${c.last_error}` : `${c.display_name} 수집이 끝났습니다.`,
          Boolean(c.last_error)
        );
      }
    });

    if (page.collecting.size === 0) {
      clearInterval(page.pollTimer);
      page.pollTimer = null;
      renderConnections();
    }
  }, POLL_INTERVAL_MS);
}

/* ── 삭제 (FR-109) ────────────────────────────────────────── */

function askDelete(conn) {
  page.pendingDelete = conn;
  document.getElementById('del-name').textContent = conn.display_name;
  document.getElementById('del-modal').hidden = false;
}

function closeDelete() {
  page.pendingDelete = null;
  document.getElementById('del-modal').hidden = true;
}

async function confirmDelete() {
  const conn = page.pendingDelete;
  if (!conn) return;
  try {
    await api.deleteConnection(conn.connection_id);
    closeDelete();
    showToast(`${conn.display_name} 연결을 삭제했습니다.`);
    await reload();
  } catch (err) {
    closeDelete();
    showToast(err.message || '연결을 삭제하지 못했습니다.', true);
  }
}

/* ── 등록 (FR-101·104·105·106) ────────────────────────────── */

const STAGE_LABEL = {
  reachable: '네트워크',
  tls_valid: 'TLS',
  authenticated: '인증',
  authorized: '조회 권한',
};

/* 종류별 기본값·도움말 (계획 05 §2.1·§4.1·§4.2) */
const KIND_META = {
  vcenter: {
    port: 443,
    addressPlaceholder: 'vcsa.example.local',
    userPlaceholder: 'svc-inventory@vsphere.local',
    hint: '',
  },
  scvmm: {
    port: 5986,
    addressPlaceholder: 'scvmm.example.local',
    userPlaceholder: 'DOMAIN\\svc-inventory',
    hint: 'SCVMM 관리 서버 주소를 직접 지정하세요 (콘솔만 설치된 서버 경유 금지). '
      + 'SCVMM이 관리하는 호스트를 별도로 등록하면 같은 VM이 중복 수집됩니다.',
  },
  'hyperv-host': {
    port: 5986,
    addressPlaceholder: 'hv-host01.example.local',
    userPlaceholder: 'DOMAIN\\svc-inventory',
    hint: 'SCVMM이 관리하는 호스트라면 이 연결 대신 SCVMM으로 등록하세요. '
      + '함께 등록하면 같은 VM이 중복 수집됩니다.',
  },
  'hyperv-cluster': {
    port: 5986,
    addressPlaceholder: 'hv-cluster01.example.local',
    userPlaceholder: 'DOMAIN\\svc-inventory',
    hint: '클러스터 이름으로 등록하면 노드 목록을 얻어 노드별로 수집합니다. '
      + 'SCVMM이 관리하는 클러스터라면 SCVMM으로 등록하세요.',
  },
};

const WINRM_PORTS = { https: 5986, http: 5985 };

function currentKind() {
  return document.getElementById('f-kind').value;
}

function isWinRmKind(kind) {
  return kind === 'scvmm' || kind === 'hyperv-host' || kind === 'hyperv-cluster';
}

function formPayload() {
  const kind = currentKind();
  const payload = {
    kind,
    display_name: document.getElementById('f-name').value.trim(),
    address: document.getElementById('f-address').value.trim(),
    port: Number(document.getElementById('f-port').value) || KIND_META[kind].port,
    username: document.getElementById('f-user').value.trim(),
    password: document.getElementById('f-pass').value,
    verify_tls: document.getElementById('f-tls').checked,
  };
  if (isWinRmKind(kind)) {
    payload.protocol = document.getElementById('f-protocol').value;
    payload.auth_method = document.getElementById('f-auth').value;
    if (kind !== 'scvmm') {
      const jea = document.getElementById('f-jea').value.trim();
      payload.session_configuration = jea || null;
    }
  }
  return payload;
}

/* CredSSP는 자격증명 위임, HTTP는 전송 암호화 없음 — 선택 시 경고한다 (계획 05 §4.1) */
function renderWinRmWarnings() {
  const warn = document.getElementById('winrm-warn');
  const parts = [];
  if (document.getElementById('f-auth').value === 'credssp') {
    parts.push('CredSSP는 자격증명이 대상 서버로 위임됩니다. 이중 홉이 꼭 필요한 경우에만 쓰세요.');
  }
  if (document.getElementById('f-protocol').value === 'http') {
    parts.push('HTTP는 전송 계층 암호화가 없습니다. 테스트 용도로만 쓰세요.');
  }
  warn.textContent = parts.join(' ');
  warn.hidden = parts.length === 0;
}

function onProtocolChange() {
  const portEl = document.getElementById('f-port');
  const proto = document.getElementById('f-protocol').value;
  const other = proto === 'https' ? WINRM_PORTS.http : WINRM_PORTS.https;
  // 사용자가 포트를 직접 바꾸지 않았을 때만 따라간다
  if (Number(portEl.value) === other) portEl.value = String(WINRM_PORTS[proto]);
  renderWinRmWarnings();
}

function onKindChange() {
  const kind = currentKind();
  const meta = KIND_META[kind];
  const winrm = isWinRmKind(kind);

  document.getElementById('winrm-fields').hidden = !winrm;
  document.getElementById('jea-field').hidden = kind !== 'hyperv-host' && kind !== 'hyperv-cluster';

  const hintEl = document.getElementById('kind-hint');
  hintEl.textContent = meta.hint;
  hintEl.hidden = !meta.hint;

  document.getElementById('f-address').placeholder = meta.addressPlaceholder;
  document.getElementById('f-user').placeholder = meta.userPlaceholder;

  // 다른 종류의 기본 포트에서 벗어나지 않았다면 기본값을 따라간다
  const portEl = document.getElementById('f-port');
  const defaults = Object.values(KIND_META).map((m) => m.port).concat(WINRM_PORTS.http);
  if (!portEl.value || defaults.includes(Number(portEl.value))) {
    portEl.value = String(winrm ? WINRM_PORTS[document.getElementById('f-protocol').value] : meta.port);
  }
  renderWinRmWarnings();
  renderCheckResult(null);
}

function renderCheckResult(result) {
  const host = document.getElementById('check-result');
  host.innerHTML = '';
  if (!result) return;

  const box = document.createElement('div');
  box.className = 'check';

  result.stages.forEach((s) => {
    const row = document.createElement('div');
    row.className = 'check__row ' + (s.passed ? 'check--pass' : 'check--fail');
    const icon = document.createElement('span');
    icon.className = 'check__icon';
    icon.textContent = s.passed ? '✓' : '✕';
    const label = document.createElement('span');
    label.className = 'check__label';
    label.textContent = STAGE_LABEL[s.stage] || s.stage;
    const detail = document.createElement('span');
    detail.className = 'check__detail';
    detail.textContent = s.detail || '';
    row.append(icon, label, detail);
    box.appendChild(row);
  });

  if (result.server_version) {
    const ver = document.createElement('div');
    ver.className = 'check__row check--pass';
    ver.innerHTML = '<span class="check__icon"></span><span class="check__label">서버</span>';
    const detail = document.createElement('span');
    detail.className = 'check__detail';
    detail.textContent = result.server_version;
    ver.appendChild(detail);
    box.appendChild(ver);
  }

  host.appendChild(box);
}

function setFormError(message) {
  const el = document.getElementById('add-error');
  el.textContent = message || '';
  el.hidden = !message;
}

function openAdd() {
  document.getElementById('add-form').reset();
  document.getElementById('f-kind').value = 'vcenter';
  document.getElementById('f-tls').checked = true;
  onKindChange();
  document.getElementById('f-port').value = '443';
  renderCheckResult(null);
  setFormError('');
  document.getElementById('add-modal').hidden = false;
  document.getElementById('f-name').focus();
}

function closeAdd() {
  document.getElementById('add-modal').hidden = true;
}

async function testConnection() {
  const payload = formPayload();
  if (!payload.address || !payload.username || !payload.password) {
    setFormError('주소·계정·비밀번호를 먼저 입력하세요.');
    return;
  }
  const btn = document.getElementById('btn-test');
  btn.disabled = true;
  btn.textContent = '확인 중…';
  setFormError('');
  try {
    renderCheckResult(await api.testConnection(payload));
  } catch (err) {
    setFormError(err.message || '연결 테스트에 실패했습니다.');
  } finally {
    btn.disabled = false;
    btn.textContent = '연결 테스트';
  }
}

async function saveConnection(event) {
  event.preventDefault();
  const payload = formPayload();
  const btn = document.getElementById('btn-save');
  btn.disabled = true;
  setFormError('');
  try {
    const created = await api.createConnection(payload);
    closeAdd();
    showToast(`${created.display_name} 연결을 등록했습니다.`);
    await reload();
  } catch (err) {
    setFormError(err.message || '연결을 등록하지 못했습니다.');
  } finally {
    btn.disabled = false;
  }
}

/* ── 초기화 ───────────────────────────────────────────────── */

async function init() {
  // 연결 관리는 관리자 전용이다 (NFR-210). 실제 차단은 API가 한다.
  if (!await requireSession('connections', 'connection.manage')) return;

  document.getElementById('add-conn').addEventListener('click', openAdd);
  document.getElementById('btn-cancel-add').addEventListener('click', closeAdd);
  document.getElementById('btn-test').addEventListener('click', testConnection);
  document.getElementById('add-form').addEventListener('submit', saveConnection);
  document.getElementById('f-kind').addEventListener('change', onKindChange);
  document.getElementById('f-auth').addEventListener('change', renderWinRmWarnings);
  document.getElementById('f-protocol').addEventListener('change', onProtocolChange);

  document.getElementById('btn-cancel-del').addEventListener('click', closeDelete);
  document.getElementById('btn-confirm-del').addEventListener('click', confirmDelete);

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!document.getElementById('del-modal').hidden) closeDelete();
    else if (!document.getElementById('add-modal').hidden) closeAdd();
  });

  reload();
}

init();
