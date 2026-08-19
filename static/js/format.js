/* 표시 형식 — docs/03_design_system.md §2 상태 표현 규칙.
 *
 * 이 파일이 "값 없음 / 수집 불가 / 미지원"을 가르는 유일한 지점이다 (FR-501·1204).
 * 빈 문자열을 그대로 렌더링하면 "IP가 없는 VM"으로 오해되므로, 어떤 값이든 이 함수를 거친다.
 */

const FIELD = {
  VALUE: 'value',            // 수집된 값
  UNAVAILABLE: 'unavailable', // 수집 불가 — 게스트 도구 미설치·중지
  NA: 'na',                  // 해당 없음 — 하이퍼바이저 미지원
  EMPTY: 'empty',            // 실제로 빈 값
};

/**
 * 상태 표시용 디스크립터를 만든다.
 * @param {*} value 수집된 값 (없으면 null)
 * @param {object} opts
 *   - unavailableReason: 수집 불가 사유. **서버가 만든 문구를 그대로 쓴다** (ROADMAP §9.2)
 *   - notSupported: 하이퍼바이저 미지원 (Step 5 이후 사용)
 *   - mono: 값 폰트를 monospace로 (IP·MAC·수치)
 *   - suffix: 값 뒤에 덧붙일 문구 (예: OS 출처 '(구성값)')
 *   - last / lastAgo: 수집 불가일 때 마지막으로 알려진 값
 */
function fieldOf(value, opts = {}) {
  if (opts.notSupported) {
    return { kind: FIELD.NA, text: '해당 없음', icon: '◌', sub: '' };
  }
  if (opts.unavailableReason) {
    const hasLast = Boolean(opts.last);
    return {
      kind: FIELD.UNAVAILABLE,
      text: '수집 불가 — ' + opts.unavailableReason,
      icon: '⚠',
      sub: hasLast ? '마지막 확인: ' + opts.last + (opts.lastAgo ? ' (' + opts.lastAgo + ')' : '') : '',
    };
  }
  if (value === null || value === undefined || value === '') {
    return { kind: FIELD.EMPTY, text: '—', icon: '', sub: '' };
  }
  return {
    kind: FIELD.VALUE,
    text: String(value) + (opts.suffix || ''),
    icon: '',
    sub: '',
    mono: Boolean(opts.mono),
  };
}

/** fieldOf 결과를 DOM으로 만든다. sub가 있으면 두 번째 줄로 붙인다. */
function stateEl(field, opts = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'cell';

  const line = document.createElement('span');
  line.className = 'state ' + stateClass(field);

  if (field.icon) {
    const icon = document.createElement('span');
    icon.className = 'state__icon';
    icon.textContent = field.icon;
    line.appendChild(icon);
  }
  const text = document.createElement('span');
  text.textContent = field.text;
  line.appendChild(text);
  wrap.appendChild(line);

  if (field.sub) {
    const sub = document.createElement('span');
    sub.className = 'state__sub' + (opts.chipSub ? ' state__sub--chip' : '');
    sub.textContent = field.sub;
    wrap.appendChild(sub);
  }
  return wrap;
}

function stateClass(field) {
  if (field.kind === FIELD.UNAVAILABLE) return 'state--unavailable';
  if (field.kind === FIELD.NA) return 'state--na';
  if (field.kind === FIELD.EMPTY) return 'state--empty';
  return field.mono ? 'state--value' : 'state--value-text';
}

/* ── 게스트 OS (FR-304 출처 병기) ──────────────────────────── */

/** 구성값에서 온 OS는 실제와 다를 수 있으므로 출처를 병기한다.
 *
 * 게스트 정보는 중첩 객체이며 사유 문구는 서버가 채운다 (계획 08 §6.3).
 * 수집 불가여도 폴백으로 남은 이전 값이 있으면 "마지막 확인"으로 병기한다 (ROADMAP §7.4). */
function osField(vm) {
  const g = vm.guest || {};

  if (g.is_collected) {
    return fieldOf(g.os_name, {
      suffix: g.os_source === 'vm_config' ? ' (구성값)' : '',
    });
  }

  // 폴백으로 남은 이전 게스트 값이 가장 권위 있다 — 실제로 도구가 보고했던 값이다 (ROADMAP §7.4)
  if (g.os_name) {
    return fieldOf(null, {
      unavailableReason: g.unavailable_reason || '확인 필요',
      last: g.os_name,
      lastAgo: agoText(minutesSince(g.observed_at)),
    });
  }

  // 도구가 없어도 **VM 구성값 OS는 수집된다** (ROADMAP §5.3:
  // "guest.guestFullName → 없으면 config.guestFullName, 출처 표시 필수").
  // 이 분기가 없으면 도구 미설치 VM은 알 수 있는 OS까지 화면에서 사라진다.
  if (vm.configured_os) {
    const field = fieldOf(vm.configured_os, { suffix: ' (구성값)' });
    field.sub = g.unavailable_reason || '확인 필요';  // 도구 상태는 보조 줄에 남긴다
    return field;
  }

  return fieldOf(null, { unavailableReason: g.unavailable_reason || '확인 필요' });
}

/* ── 전원 상태 (§2.3) ──────────────────────────────────────── */

/* 도메인 PowerState와 같은 값을 쓴다 (계획 02 §3). 하이퍼바이저 고유 표기
   (poweredOn / Running)는 어댑터가 이미 정규화했다. */
const POWER = {
  on: { label: '실행 중', icon: '●', cls: 'power--on' },
  off: { label: '중지', icon: '■', cls: 'power--off' },
  suspended: { label: '일시 중단', icon: '❚❚', cls: 'power--suspended' },
  unknown: { label: '알 수 없음', icon: '○', cls: 'power--unknown' },
};

function powerEl(state) {
  const p = POWER[state] || POWER.unknown;
  const el = document.createElement('div');
  el.className = 'power ' + p.cls;
  el.innerHTML = '<span style="font-size:10px"></span><span></span>';
  el.children[0].textContent = p.icon;
  el.children[1].textContent = p.label;
  return el;
}

/* ── 시각 · 수량 ───────────────────────────────────────────── */

const STALE_MINUTES = 24 * 60; // 24시간 초과 시 경고 (§2.2)

function minutesSince(iso) {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.floor((Date.now() - t) / 60000));
}

function agoText(minutes) {
  if (minutes === null) return '—';
  if (minutes < 1) return '방금';
  if (minutes < 60) return minutes + '분 전';
  const h = Math.floor(minutes / 60);
  if (h < 24) return h + '시간 전';
  return Math.floor(h / 24) + '일 전';
}

function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('ko-KR', {
    year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

/** 표 안에서 쓰는 짧은 형식. 자릿수가 고정되어 열이 흔들리지 않는다. */
function fmtDateShort(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 수집 시각 셀 — 상대 시각(신선도 색) + 절대 시각 2줄 */
function collectedEl(iso) {
  const mins = minutesSince(iso);
  const stale = mins !== null && mins > STALE_MINUTES;

  const wrap = document.createElement('div');
  wrap.className = 'cell';

  const top = document.createElement('span');
  top.className = 'stale' + (stale ? ' stale--warn' : '');
  const icon = document.createElement('span');
  icon.textContent = stale ? '⚠' : '';
  const ago = document.createElement('span');
  ago.textContent = agoText(mins);
  top.append(icon, ago);

  const bottom = document.createElement('span');
  bottom.className = 'cell__sub';
  bottom.textContent = fmtDateShort(iso);
  bottom.title = fmtDateTime(iso);   // 전체 형식은 툴팁으로 (NFR-403)

  wrap.append(top, bottom);
  return wrap;
}

function fmtMemory(mb) {
  if (mb === null || mb === undefined) return '—';
  return (mb / 1024).toLocaleString('ko-KR', { maximumFractionDigits: 1 }) + ' GiB';
}

function fmtNumber(n) {
  if (n === null || n === undefined) return '—';
  return Number(n).toLocaleString('ko-KR');
}

/* ── 하이퍼바이저 배지 (§3.3) ──────────────────────────────── */

const HV = {
  vcenter: { tag: 'VC', label: 'vCenter' },
  hyperv: { tag: 'HV', label: 'Hyper-V' },
  'hyperv-host': { tag: 'HV', label: 'Hyper-V 호스트' },
  'hyperv-cluster': { tag: 'HV', label: 'Hyper-V 클러스터' },
  scvmm: { tag: 'VMM', label: 'SCVMM' },
};

function hvBadge(kind) {
  const hv = HV[kind] || { tag: '??', label: kind || '알 수 없음' };
  const el = document.createElement('span');
  el.className = 'badge';
  const tag = document.createElement('span');
  tag.className = 'badge__tag';
  tag.textContent = hv.tag;
  const label = document.createElement('span');
  label.textContent = hv.label;
  el.append(tag, label);
  return el;
}
