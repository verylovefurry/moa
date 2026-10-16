const form = document.querySelector('#connect-form');
const status = document.querySelector('#status');
function show(result) {
  status.className = result?.ok ? '' : 'error';
  status.textContent = result?.ok ? `연결됐어요. ${result.enabled ? '작업 시간이 자동으로 모이고 있어요.' : '모아에서 자동 추적을 시작해주세요.'}` : result?.error || '연결을 다시 시도해주세요.';
}
chrome.storage.local.get(['token', 'port']).then(({ token, port }) => {
  if (token) { form.elements.token.value = token; chrome.runtime.sendMessage({ type: 'sync' }).then(show); }
  if (port) form.elements.port.value = port;
});
form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button');
  button.disabled = true;
  try {
    await chrome.storage.local.set({ token: form.elements.token.value.trim().toUpperCase(), port: Number(form.elements.port.value) });
    show(await chrome.runtime.sendMessage({ type: 'sync' }));
  } catch { show({ ok: false, error: '확장을 새로고침한 뒤 다시 시도해주세요.' }); }
  finally { button.disabled = false; }
});
