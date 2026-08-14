/* 로그인 (FR-1001) + 임시 비밀번호 변경 (FR-1008)
 *
 * 실패 문구는 서버가 준 것을 그대로 쓴다. 화면에서 사유를 추측해 덧붙이면
 * 계정 열거 방지가 깨진다 (계획 09 §4.3).
 */

function showError(id, message) {
  const el = document.getElementById(id);
  el.textContent = message;
  el.hidden = !message;
}

function showChangeForm() {
  document.getElementById('login-card').hidden = true;
  document.getElementById('change-card').hidden = false;
  document.getElementById('f-current').focus();
}

async function onLogin(event) {
  event.preventDefault();
  const btn = document.getElementById('btn-login');
  btn.disabled = true;
  showError('login-error', '');

  try {
    const res = await api.login(
      document.getElementById('f-username').value,
      document.getElementById('f-password').value
    );
    if (res.user.must_change_password) {
      showChangeForm();
      return;
    }
    location.replace('index.html');
  } catch (err) {
    showError('login-error', err.message || '로그인에 실패했습니다.');
  } finally {
    btn.disabled = false;
  }
}

async function onChangePassword(event) {
  event.preventDefault();
  showError('change-error', '');
  try {
    await api.changePassword(
      document.getElementById('f-current').value,
      document.getElementById('f-new').value
    );
    location.replace('index.html');
  } catch (err) {
    showError('change-error', err.message || '비밀번호를 변경하지 못했습니다.');
  }
}

async function init() {
  document.getElementById('login-form').addEventListener('submit', onLogin);
  document.getElementById('change-form').addEventListener('submit', onChangePassword);

  // 이미 로그인되어 있으면 목록으로 보낸다. 단 비밀번호 변경이 필요하면 여기 남는다.
  try {
    const me = await api.me();
    if (me.user.must_change_password) showChangeForm();
    else location.replace('index.html');
  } catch (err) {
    /* 미인증 — 정상 경로 */
  }
}

init();
