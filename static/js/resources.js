/* 가상 머신 목록 — FR-401·1203·1205
 *
 * 서버 페이징 전제다 (docs/03_design_system.md §5-2). 전체를 받아 클라이언트에서
 * 자르지 않는다. 5,000건 규모에서 무너진다.
 */

const PAGE_SIZE = 50;

const view = {
  connectionId: null,
  page: 1,
  total: 0,
  totalAll: 0,          // 필터 없을 때의 건수. 네비·헤더 표시용
  connections: [],      // /auth/me가 준 접근 가능 연결 (id·이름만)
};

function renderConnectionFilter() {
  const host = document.getElementById('conn-filter');
  host.innerHTML = '';
  if (view.connections.length <= 1) return; // 연결이 하나면 선택지가 없다

  const seg = document.createElement('div');
  seg.className = 'seg';

  const label = document.createElement('span');
  label.className = 'seg__label';
  label.textContent = '연결';

  const group = document.createElement('div');
  group.className = 'seg__group';

  const options = [{ id: null, label: '전체' }].concat(
    view.connections.map((c) => ({ id: c.connection_id, label: c.display_name }))
  );

  options.forEach((opt) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'seg__btn';
    btn.textContent = opt.label;
    btn.setAttribute('aria-pressed', String(view.connectionId === opt.id));
    btn.addEventListener('click', () => {
      view.connectionId = opt.id;
      view.page = 1;
      loadVirtualMachines();
      renderConnectionFilter();
    });
    group.appendChild(btn);
  });

  seg.append(label, group);
  host.appendChild(seg);
}

function vmRow(vm) {
  const row = document.createElement('div');
  row.className = 'table__row cols-vm';

  // 이름 + 하이퍼바이저 고유 ID
  const name = document.createElement('div');
  const nameCell = document.createElement('div');
  nameCell.className = 'cell';
  const main = document.createElement('span');
  main.className = 'cell__main';
  main.textContent = vm.name;
  main.title = vm.name;
  const sub = document.createElement('span');
  sub.className = 'cell__sub';
  sub.textContent = vm.native_id;
  nameCell.append(main, sub);
  name.appendChild(nameCell);

  // 전원
  const power = document.createElement('div');
  power.appendChild(powerEl(vm.power_state));

  // 게스트 OS — 수집 불가/출처 병기가 여기서 갈린다 (FR-501·304)
  const os = document.createElement('div');
  os.appendChild(stateEl(osField(vm)));

  const vcpu = document.createElement('div');
  vcpu.className = 'cell--num';
  vcpu.textContent = fmtNumber(vm.vcpu_count);

  const mem = document.createElement('div');
  mem.className = 'cell--num';
  mem.textContent = fmtMemory(vm.memory_mb);

  // 호스트 — Step 6에서 MoRef를 실제 호스트명으로 해석한다
  const host = document.createElement('div');
  host.appendChild(stateEl(fieldOf(vm.host_native_id, { mono: true })));

  const collected = document.createElement('div');
  collected.appendChild(collectedEl(vm.last_seen_at));

  row.append(name, power, os, vcpu, mem, host, collected);
  return row;
}

function renderRows(items) {
  const host = document.getElementById('vm-rows');
  host.innerHTML = '';

  if (items.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'table__empty';
    // 조회 범위가 없는 사용자에게 "연결을 등록하세요"라고 안내하면 안 된다.
    // 그 사용자는 연결을 등록할 권한이 없고, 필요한 것은 관리자의 범위 부여다.
    if (view.connections.length === 0) {
      empty.textContent = can('connection.manage')
        ? '등록된 연결이 없습니다. 하이퍼바이저 연결을 먼저 등록하세요.'
        : '조회할 수 있는 연결이 없습니다. 관리자에게 조회 범위를 요청하세요.';
    } else {
      empty.textContent = '수집된 가상 머신이 없습니다.'
        + (can('collection.trigger') ? ' 연결 화면에서 수집을 실행하세요.' : '');
    }
    host.appendChild(empty);
    return;
  }

  const frag = document.createDocumentFragment();
  items.forEach((vm) => frag.appendChild(vmRow(vm)));
  host.appendChild(frag);
}

function renderPager(shown) {
  const pager = document.getElementById('pager');
  const lastPage = Math.max(1, Math.ceil(view.total / PAGE_SIZE));
  pager.hidden = view.total <= PAGE_SIZE;

  const from = view.total === 0 ? 0 : (view.page - 1) * PAGE_SIZE + 1;
  document.getElementById('pager-pos').textContent =
    `${fmtNumber(from)}–${fmtNumber(from + shown - 1)} / ${fmtNumber(view.total)}`;

  document.getElementById('prev-page').disabled = view.page <= 1;
  document.getElementById('next-page').disabled = view.page >= lastPage;
}

async function loadVirtualMachines() {
  try {
    const res = await api.listVirtualMachines({
      connectionId: view.connectionId,
      offset: (view.page - 1) * PAGE_SIZE,
      limit: PAGE_SIZE,
    });
    view.total = res.total;
    if (!view.connectionId) view.totalAll = res.total;

    renderRows(res.items);
    renderPager(res.items.length);
    document.getElementById('vm-count').textContent =
      view.connectionId ? `${fmtNumber(res.total)} / 전체 ${fmtNumber(view.totalAll)}` : fmtNumber(res.total);
    setNavCounts({ vmCount: view.totalAll });

    // 헤더 신선도는 사용자가 실제로 보는 데이터 기준으로 표시한다
    renderSyncStatus(res.items.map((vm) => ({ last_success_at: vm.last_seen_at })));
  } catch (err) {
    showToast(err.message || '목록을 불러오지 못했습니다.', true);
  }
}

async function init() {
  const me = await requireSession('inventory');
  if (!me) return;

  // 연결 목록은 admin 전용이므로, 조회 화면의 필터는 /auth/me가 준
  // 접근 가능 연결(id·이름)만 쓴다. 자격증명·상태는 여기 오지 않는다.
  view.connections = me.accessible_connections || [];

  setNavCounts({ connCount: view.connections.length });
  renderSyncStatus([]);
  renderConnectionFilter();
  await loadVirtualMachines();

  document.getElementById('prev-page').addEventListener('click', () => {
    if (view.page > 1) { view.page -= 1; loadVirtualMachines(); }
  });
  document.getElementById('next-page').addEventListener('click', () => {
    const lastPage = Math.max(1, Math.ceil(view.total / PAGE_SIZE));
    if (view.page < lastPage) { view.page += 1; loadVirtualMachines(); }
  });
}

init();
