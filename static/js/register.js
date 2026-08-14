/* 가입 신청 (FR-1006)
 *
 * 아이디 중복 여부를 화면에 표시하지 않는다. 중복이어도 접수 안내를 보여준다.
 * 다르게 만들면 가입 폼이 계정 열거 수단이 된다 (계획 09 §4.5).
 */

function showError(message) {
  const el = document.getElementById('register-error');
  el.textContent = message;
  el.hidden = !message;
}

async function onSubmit(event) {
  event.preventDefault();
  showError('');

  const password = document.getElementById('f-password').value;
  if (password !== document.getElementById('f-password2').value) {
    showError('비밀번호가 서로 다릅니다.');
    return;
  }

  const btn = document.getElementById('btn-register');
  btn.disabled = true;
  try {
    await api.register({
      username: document.getElementById('f-username').value.trim().toLowerCase(),
      display_name: document.getElementById('f-display').value.trim(),
      email: document.getElementById('f-email').value.trim(),
      password,
    });
    // 성공·중복을 구분하지 않고 같은 안내를 보여준다
    document.getElementById('register-card').hidden = true;
    document.getElementById('done-card').hidden = false;
  } catch (err) {
    showError(err.message || '가입 신청에 실패했습니다.');
  } finally {
    btn.disabled = false;
  }
}

document.getElementById('register-form').addEventListener('submit', onSubmit);
