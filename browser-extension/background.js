const browserName = navigator.userAgent.includes('Edg/') ? 'msedge.exe' : 'chrome.exe';
let queue = Promise.resolve();

function matches(url, pattern) {
  try {
    const a = new URL(url), b = new URL(pattern);
    const path = b.pathname.replace(/\/$/, '');
    return ['http:', 'https:'].includes(a.protocol) && a.hostname === b.hostname && a.port === b.port && (!path || a.pathname === path || a.pathname.startsWith(path + '/'));
  } catch { return false; }
}

async function synchronize() {
  const { token, port = 3847 } = await chrome.storage.local.get(['token', 'port']);
  if (!token) return { ok: false, error: '연결 코드를 입력해주세요.' };
  const base = `http://127.0.0.1:${port}`;
  const headers = { 'X-Moa-Token': token, 'Content-Type': 'application/json' };
  try {
    const configResponse = await fetch(`${base}/api/bridge`, { headers, signal: AbortSignal.timeout(4000) });
    if (!configResponse.ok) throw new Error(configResponse.status === 401 ? '연결 코드를 확인해주세요.' : '모아 앱에 연결하지 못했어요.');
    const config = await configResponse.json();
    const window = await chrome.windows.getLastFocused();
    const [tab] = await chrome.tabs.query({ active: true, windowId: window.id });
    // Only configured URLs leave the browser. Titles and page contents are never sent.
    const url = config.enabled && window.focused && config.rules.some(rule => matches(tab?.url || '', rule.matcher)) ? tab.url : '';
    const response = await fetch(`${base}/api/browser`, { method: 'POST', headers, body: JSON.stringify({ browser: browserName, focused: Boolean(window.focused), url }), signal: AbortSignal.timeout(4000) });
    if (!response.ok) throw new Error('연결 코드를 확인해주세요.');
    await chrome.action.setBadgeText({ text: '' });
    return { ok: true, enabled: config.enabled };
  } catch (error) {
    return { ok: false, error: error.message === 'Failed to fetch' ? '모아 앱을 먼저 실행해주세요.' : error.message };
  }
}

function sync() {
  queue = queue.catch(() => {}).then(synchronize);
  return queue;
}

chrome.tabs.onActivated.addListener(() => { sync(); });
chrome.tabs.onUpdated.addListener((_id, change) => { if (change.url || change.status === 'complete') sync(); });
chrome.windows.onFocusChanged.addListener(() => { sync(); });
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === 'moa-heartbeat') sync(); });
chrome.runtime.onInstalled.addListener(() => { chrome.alarms.create('moa-heartbeat', { periodInMinutes: 0.5 }); sync(); });
chrome.runtime.onStartup.addListener(() => { chrome.alarms.create('moa-heartbeat', { periodInMinutes: 0.5 }); sync(); });
chrome.storage.onChanged.addListener(() => { sync(); });
chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  if (message.type === 'sync') { sync().then(respond).catch(() => respond({ ok: false, error: '연결을 다시 시도해주세요.' })); return true; }
});
chrome.alarms.create('moa-heartbeat', { periodInMinutes: 0.5 });
