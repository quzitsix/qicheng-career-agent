'use strict';

const $ = (selector) => document.querySelector(selector);
const state = { status: null, sessions: [], session: null, resources: [], displayedResources: [], filter: 'all', search: '', visibleCount: 12, catalogView: true, busy: false, controller: null, toastTimer: null };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function safeURL(value) {
  if (!value) return null;
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : null; } catch { return null; }
}
function escapeHTML(value) { return String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]); }
function inlineMarkdown(text) {
  return escapeHTML(text).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
}
function renderMarkdown(target, text) {
  target.replaceChildren();
  let list = null, paragraph = [], code = null;
  const flush = () => { if (paragraph.length) { const node = el('p'); node.innerHTML = paragraph.map(inlineMarkdown).join('<br>'); target.append(node); paragraph = []; } };
  for (const line of String(text || '').split('\n')) {
    if (line.trim().startsWith('```')) {
      flush(); list = null;
      if (code) { target.append(code); code = null; } else { code = el('pre'); code.append(el('code')); }
      continue;
    }
    if (code) { code.firstChild.textContent += `${line}\n`; continue; }
    if (!line.trim()) { flush(); list = null; continue; }
    const heading = line.match(/^#{1,6}\s+(.+)/);
    const bullet = line.match(/^\s*[-*]\s+(.+)/);
    const number = line.match(/^\s*\d+[.)、]\s+(.+)/);
    if (heading) { flush(); list = null; const h = el('h3'); h.innerHTML = inlineMarkdown(heading[1]); target.append(h); }
    else if (bullet || number) {
      flush(); const type = bullet ? 'UL' : 'OL';
      if (!list || list.tagName !== type) { list = el(type.toLowerCase()); target.append(list); }
      const item = el('li'); item.innerHTML = inlineMarkdown((bullet || number)[1]); list.append(item);
    } else { list = null; paragraph.push(line); }
  }
  flush(); if (code) target.append(code);
}
function toast(message) {
  $('#toast').textContent = message; $('#toast').hidden = false;
  clearTimeout(state.toastTimer); state.toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4500);
}
function showLogin(message = '') {
  $('#login-screen').hidden = false; $('#app-shell').inert = true;
  $('#login-error').textContent = message; $('#login-error').hidden = !message;
  setTimeout(() => $('#invite-code').focus(), 30);
}
function hideLogin() { $('#login-screen').hidden = true; $('#app-shell').inert = false; }
async function api(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', ...options, headers: { 'Content-Type': 'application/json', ...options.headers } });
  if (!response.ok) {
    let body = {}; try { body = await response.json(); } catch { /* No JSON available. */ }
    if (response.status === 401) showLogin();
    throw new Error(typeof body.detail === 'string' ? body.detail : body.message || `请求暂未完成（${response.status}），请稍后重试。`);
  }
  return response.json();
}
function setBusy(busy) {
  state.busy = busy; $('#send-button').disabled = busy || !$('#message-input').value.trim();
  $('#stop-button').hidden = !busy;
  $('#new-chat').disabled = busy;
  $('#back-to-assistant').disabled = busy;
  for (const button of document.querySelectorAll('.suggestion, .session-item, .resource-action, .message-resource-note')) button.disabled = busy;
}
function scrollBottom(force = false) {
  const area = $('#chat-scroll');
  if (force || area.scrollHeight - area.scrollTop - area.clientHeight < 180) area.scrollTop = area.scrollHeight;
}
function closeSidebar() {
  $('#sidebar').classList.remove('open'); $('#sidebar-backdrop').hidden = true; $('#toggle-sidebar').setAttribute('aria-expanded', 'false');
}
function renderSessions() {
  const list = $('#session-list'); list.replaceChildren();
  if (!state.sessions.length) { list.append(el('p', 'muted empty-history', '还没有对话，来聊聊吧。')); return; }
  for (const session of state.sessions) {
    const button = el('button', `session-item${state.session?.id === session.id ? ' active' : ''}`);
    button.append(el('span', 'session-icon', session.mentor_id ? '◇' : '◌'), el('span', 'session-title', session.title || '新的探索'));
    button.title = session.title || '新的探索'; button.disabled = state.busy;
    button.onclick = () => loadSession(session.id).catch((error) => toast(error.message));
    list.append(button);
  }
}
function updateDataNotice() {
  const summary = state.status?.data_summary || {};
  const realMentors = summary.real_mentor_count || state.resources.filter((r) => r.type === 'mentor' && r.is_sample === false).length;
  const sampleActivities = summary.sample_activity_count || state.resources.filter((r) => r.type === 'activity' && r.is_sample !== false).length;
  const notice = realMentors
    ? `已接入 ${realMentors} 位导师的脱敏名录${sampleActivities ? ' · 活动仍为演示样例' : ''} · 预约信息尚未接入。`
    : '当前使用演示样例，不代表真实校内资源，也不会办理预约。';
  $('#data-notice').textContent = notice;
  if (realMentors && !summary.mentor_documents_count) {
    $('#welcome-description').textContent = '聊聊你的兴趣和困惑。我会根据已接入的行业与职业背景，帮你找到相关的导师方向和下一步。';
  }
}
function setResourceCollection(items, { catalogView = false, filter = 'all' } = {}) {
  state.displayedResources = items; state.catalogView = catalogView; state.filter = filter;
  state.search = ''; state.visibleCount = 12; $('#resource-search').value = '';
  updateResourceTabs(); renderResources();
}
function defaultResourceFilter() {
  return state.resources.some((r) => r.type === 'mentor' && r.is_sample === false) ? 'mentor' : 'all';
}
function updateContext() {
  const mentor = state.resources.find((item) => item.type === 'mentor' && (item.mentor_id || item.id) === state.session?.mentor_id);
  const isMentor = Boolean(state.session?.mentor_id);
  const name = mentor?.title || '历史样例数字导师';
  $('#mentor-banner').hidden = !isMentor;
  $('#mentor-name').textContent = name;
  $('#mentor-context-note').textContent = mentor?.is_sample === false ? 'AI 资料助手 · 基于已接入的资料交流，非导师本人' : '历史样例对话 · 虚构角色，非真实校友';
  $('#context-title').textContent = isMentor ? `${name} · AI 数字导师` : '职业发展助手';
  $('#message-input').placeholder = isMentor ? `向${name}的数字导师提问…` : '说说你的兴趣，或一个正在困扰你的问题…';
  $('#welcome').hidden = isMentor || $('#messages').children.length > 0;
}
async function refreshSessions() {
  const data = await api('/api/sessions'); state.sessions = data.sessions || []; renderSessions();
}
async function createSession(mentorId = null) {
  if (state.busy) return;
  setBusy(true);
  try {
    const response = await api('/api/sessions', { method: 'POST', body: JSON.stringify({ mentor_id: mentorId }) });
    state.session = response.session || response; sessionStorage.setItem('qicheng-session', state.session.id);
    $('#messages').replaceChildren(); setResourceCollection(state.resources, { catalogView: true, filter: defaultResourceFilter() }); updateContext();
    $('#resources-caption').textContent = '先随便看看，聊过之后我会帮你缩小范围。';
    if (mentorId) {
      const name = state.resources.find((item) => (item.mentor_id || item.id) === mentorId)?.title || '这位导师';
      const text = state.status?.mode === 'sample'
        ? `已进入${name}的独立数字导师空间。当前为离线规则演示，使用样例资料，并非导师本人实时回复。你可以先问“你的职业经历有哪些值得参考的地方？”`
        : `已进入${name}的独立数字导师空间。我会根据该导师的样例资料与你交流，并标明回答依据。你最想了解什么？`;
      addMessage('assistant', text);
    }
    await refreshSessions(); closeSidebar(); $('#message-input').focus();
  } finally { setBusy(false); }
}
async function loadSession(id) {
  if (state.busy) return;
  setBusy(true);
  try {
    const data = await api(`/api/sessions/${encodeURIComponent(id)}`);
    state.session = data.session; sessionStorage.setItem('qicheng-session', id);
    $('#messages').replaceChildren();
    let resources = null;
    for (const message of data.messages || []) {
      if (!['user', 'assistant'].includes(message.role) || (!message.content && !message.resources?.length)) continue;
      const node = addMessage(message.role, message.content || '');
      if (message.citations?.length) renderCitations(node, message.citations);
      if (message.resources?.length) { addResourceNote(node, message.resources); resources = message.resources; }
    }
    setResourceCollection(resources || state.resources, { catalogView: !resources, filter: resources ? 'all' : defaultResourceFilter() });
    $('#resources-caption').textContent = resources ? '这是当前对话找到的资源，点击卡片继续了解。' : '先随便看看，聊过之后我会帮你缩小范围。';
    renderResources(); updateContext(); renderSessions(); closeSidebar(); scrollBottom(true);
  } finally { setBusy(false); }
}
function formatDate(value) {
  if (!value) return '';
  const date = new Date(value); if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
}
function makeResourceCard(resource) {
  const mentor = resource.type === 'mentor';
  const card = el('article', 'resource-card');
  const top = el('div', 'resource-card-top');
  const kind = el('span', `resource-kind${mentor ? ' mentor-kind' : ''}`);
  kind.append(el('span', 'resource-kind-icon', mentor ? '◇' : '▦'), document.createTextNode(mentor ? '校友导师' : '校园活动'));
  top.append(kind); if (resource.is_sample !== false) top.append(el('span', 'sample-tag', '样例'));
  else if (mentor) top.append(el('span', 'privacy-tag', '脱敏名录'));
  card.append(top, el('h3', '', resource.title || resource.name || '未命名资源'));
  if (resource.subtitle) card.append(el('p', 'resource-subtitle', resource.subtitle));
  if (resource.description) card.append(el('p', 'resource-description', resource.description));
  if (mentor && resource.city) card.append(el('p', 'resource-meta', `工作地区：${resource.city}`));
  if (mentor && resource.education_domains?.length) card.append(el('p', 'resource-meta', `学科背景：${resource.education_domains.join('、')}`));
  if (resource.start_at) card.append(el('p', 'resource-meta', `◷ ${formatDate(resource.start_at)}`));
  if (resource.registration_deadline) card.append(el('p', 'resource-meta', `报名截止：${formatDate(resource.registration_deadline)}`));
  if (resource.location) card.append(el('p', 'resource-meta', `⌖ ${resource.location}`));
  if (resource.tags?.length) { const tags = el('div', 'resource-tags'); for (const tag of resource.tags.slice(0, 4)) tags.append(el('span', '', String(tag))); card.append(tags); }
  if (resource.is_sample !== false) card.append(el('p', 'resource-availability', mentor ? '样例导师 · 不支持实际预约' : '样例活动 · 不支持实际报名'));
  else if (resource.available === false) card.append(el('p', 'resource-availability', mentor ? '预约信息尚未接入' : resource.status === 'unknown' ? '报名状态待确认' : '当前不可报名'));
  if (resource.source_label) card.append(el('p', 'resource-meta', `来源：${resource.source_label}`));
  const actions = el('div', 'resource-actions');
  const detail = el('button', 'resource-action', mentor ? (resource.is_sample === false ? '了解匹配依据 ↗' : '了解样例导师 ↗') : '了解这个活动 ↗');
  detail.disabled = state.busy;
  detail.onclick = () => sendText(mentor ? `请根据已接入的脱敏名录，介绍“${resource.title}”的行业和职业背景，说明可能适合交流的方向。不要补充未提供的个人信息，也不要替导师承诺指导或预约。` : `请详细介绍“${resource.title}”，它适合我吗？`);
  actions.append(detail);
  if (mentor && resource.has_digital_mentor) {
    const button = el('button', 'resource-action', '与数字导师聊聊 →'); button.disabled = state.busy;
    button.onclick = () => createSession(resource.mentor_id || resource.id).catch((error) => toast(error.message)); actions.append(button);
  } else if (safeURL(resource.url)) {
    const link = el('a', 'resource-action', '查看原文'); link.href = safeURL(resource.url); link.target = '_blank'; link.rel = 'noopener noreferrer'; actions.append(link);
  }
  card.append(actions); return card;
}
function renderResources() {
  const list = $('#resource-list'); list.replaceChildren();
  const terms = state.search.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const resources = state.displayedResources.filter((item) => {
    if (state.filter !== 'all' && item.type !== state.filter) return false;
    const text = [item.title, item.subtitle, item.description, item.industry, item.city, item.organization_type,
      item.role_family, ...(item.tags || []), ...(item.education_domains || [])].filter(Boolean).join(' ').toLocaleLowerCase();
    return terms.every((term) => text.includes(term));
  });
  $('#resource-count').textContent = String(resources.length);
  $('#reset-resources').hidden = state.catalogView;
  const shown = Math.min(state.visibleCount, resources.length);
  $('#resource-results').textContent = `${state.catalogView ? '资源名录' : '本次推荐'} · 找到 ${resources.length} 项${shown < resources.length ? `，已展示 ${shown} 项` : ''}`;
  const more = $('#load-more-resources'); more.hidden = shown >= resources.length;
  more.textContent = `再显示 ${Math.min(12, resources.length - shown)} 项`;
  if (!resources.length) { list.append(el('div', 'resources-empty', '暂时没有符合条件的资源。可以调整关键词，或查看完整名录。')); return; }
  for (const resource of resources.slice(0, shown)) list.append(makeResourceCard(resource));
}

function addMessage(role, text = '') {
  const article = el('article', `message ${role}`); article.dataset.role = role;
  if (role === 'assistant') {
    const header = el('div', 'message-header');
    header.append(el('span', 'message-avatar', '启'), el('span', '', state.session?.mentor_id ? 'AI 数字导师' : '启程助手'));
    if (state.status?.mode === 'sample') header.append(el('span', 'sample-tag', '离线演示'));
    article.append(header);
  }
  const content = el('div', 'message-content'); renderMarkdown(content, text); article.append(content);
  $('#messages').append(article); $('#welcome').hidden = true; return article;
}
function renderCitations(message, citations) {
  message.querySelector('.message-citations')?.remove();
  if (!citations?.length) return;
  const details = el('details', 'message-citations'); details.append(el('summary', '', `查看回答依据 · ${citations.length} 个来源`));
  for (const citation of citations) {
    const node = el('div', 'citation'); node.append(el('strong', '', citation.title || citation.source_label || '资料来源'));
    if (citation.excerpt) node.append(el('p', '', citation.excerpt));
    if (citation.source_label && citation.source_label !== citation.title) node.append(el('p', '', citation.source_label));
    if (safeURL(citation.url)) { const link = el('a', '', '打开来源 ↗'); link.href = safeURL(citation.url); link.target = '_blank'; link.rel = 'noopener noreferrer'; node.append(link); }
    details.append(node);
  }
  message.append(details);
}
function addResourceNote(message, resources) {
  message.querySelector('.message-resource-note')?.remove();
  if (!resources?.length) return;
  const button = el('button', 'message-resource-note', `↗ 查看这次找到的 ${resources.length} 项资源`);
  button.disabled = state.busy;
  button.onclick = () => {
    setResourceCollection(resources);
    $('#resources-caption').textContent = '这是这条回答引用的资源，点击卡片继续了解。';
    document.querySelector('.resources-panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  };
  message.append(button);
}
function updateResourceTabs() {
  for (const button of document.querySelectorAll('.resource-tab')) {
    const active = button.dataset.filter === state.filter; button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
  }
}
function fitInput() {
  const input = $('#message-input'); input.style.height = 'auto'; input.style.height = `${Math.min(150, Math.max(42, input.scrollHeight))}px`;
  $('#send-button').disabled = state.busy || !input.value.trim();
}
function sendText(text) {
  if (state.busy) return;
  $('#message-input').value = text; fitInput();
  $('#chat-form').requestSubmit();
  if (window.innerWidth < 681) document.querySelector('.conversation-panel').scrollIntoView({ behavior: 'smooth' });
}
async function sendMessage(event) {
  event.preventDefault(); const text = $('#message-input').value.trim(); if (!text || state.busy) return;
  setBusy(true);
  let assistant = null, status = null;
  try {
    if (!state.session) {
      const data = await api('/api/sessions', { method: 'POST', body: JSON.stringify({ mentor_id: null }) });
      state.session = data.session || data; sessionStorage.setItem('qicheng-session', state.session.id);
    }
    addMessage('user', text); $('#message-input').value = ''; fitInput();
    assistant = addMessage('assistant');
    status = el('div', 'message-status', state.status?.mode === 'sample' ? '正在运行离线演示…' : '正在理解你的问题…');
    assistant.insertBefore(status, assistant.querySelector('.message-content'));
    const trace = el('div', 'tool-trace'); assistant.insertBefore(trace, assistant.querySelector('.message-content'));
    const toolNodes = new Map(); let answer = ''; const resources = new Map(); const citations = new Map(); let completed = false;
    scrollBottom(true);
    state.controller = new AbortController();
    const response = await fetch('/api/chat', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: state.session.id, message: text }), signal: state.controller.signal });
    if (!response.ok) {
      let body = {}; try { body = await response.json(); } catch { /* No JSON available. */ }
      if (response.status === 401) showLogin();
      throw new Error(typeof body.detail === 'string' ? body.detail : body.message || `服务暂时不可用（${response.status}）。`);
    }
    if (!response.body) throw new Error('浏览器未收到响应流，请重试。');
    const handle = (item) => {
      if (item.type === 'status') { status.textContent = item.label || '正在处理…'; status.hidden = false; }
      if (item.type === 'tool') {
        const name = item.name || item.label || '工具'; let chip = toolNodes.get(name);
        if (!chip) { chip = el('span', 'tool-chip'); toolNodes.set(name, chip); trace.append(chip); }
        chip.className = `tool-chip ${item.status === 'done' ? 'done' : 'running'}`; chip.textContent = item.label || name;
      }
      if (item.type === 'delta') { status.hidden = true; answer += item.text || ''; renderMarkdown(assistant.querySelector('.message-content'), answer); }
      if (item.type === 'resources') {
        for (const resource of item.items || []) resources.set(`${resource.type}:${resource.id}`, resource);
        setResourceCollection([...resources.values()]);
        $('#resources-caption').textContent = '根据刚才的交流找到这些资源，你可以继续了解。'; addResourceNote(assistant, state.displayedResources);
      }
      if (item.type === 'citations') {
        for (const citation of item.items || []) citations.set(citation.url || citation.id || citation.title, citation);
        renderCitations(assistant, [...citations.values()]);
      }
      if (item.type === 'done') { completed = true; status.hidden = true; for (const chip of toolNodes.values()) chip.classList.replace('running', 'done'); }
      if (item.type === 'error') throw new Error(item.message || '本次回复暂未完成。');
      scrollBottom();
    };
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let pending = '';
    while (true) {
      const { value, done } = await reader.read(); pending += done ? decoder.decode() : decoder.decode(value, { stream: true });
      const lines = pending.split('\n'); pending = lines.pop() || '';
      for (const line of lines) if (line.trim()) handle(JSON.parse(line));
      if (done) break;
    }
    if (pending.trim()) handle(JSON.parse(pending));
    if (!completed) throw new Error(answer ? '连接提前结束，以上回复可能不完整。请稍后继续提问。' : '连接已结束，但未收到回复。请重试。');
    status.hidden = true;
  } catch (error) {
    if (status) status.hidden = true;
    if (assistant) {
      const message = el('div', 'message-error', error.name === 'AbortError' ? '已停止接收回复。' : error.message || '连接遇到问题，请稍后重试。');
      if (error.name !== 'AbortError') {
        const retry = el('button', 'retry-button', '重新编辑问题');
        retry.onclick = () => { $('#message-input').value = text; fitInput(); $('#message-input').focus(); };
        message.append(retry);
      }
      assistant.append(message);
      for (const chip of assistant.querySelectorAll('.tool-chip.running')) chip.classList.remove('running');
    } else toast(error.message);
  } finally {
    state.controller = null; setBusy(false); updateContext(); scrollBottom();
    try { await refreshSessions(); } catch { /* The conversation already shows connection errors. */ }
    if (window.innerWidth > 680) $('#message-input').focus();
  }
}
async function bootstrap() {
  state.status = await api('/api/status');
  const live = state.status.mode === 'deepseek';
  $('#connection-status').classList.toggle('offline', !live);
  $('#connection-status').replaceChildren(el('i'), document.createTextNode(live ? 'DeepSeek 模式' : '离线规则演示'));
  $('#composer-hint').textContent = live ? '支持多轮追问 · 资料有据可查' : '离线规则演示 · 非实时模型回复';
  updateDataNotice();
  if (!state.status.authenticated) { showLogin(); return; }
  hideLogin();
  const [resources, sessions] = await Promise.all([api('/api/resources'), api('/api/sessions')]);
  state.resources = [...(resources.mentors || []).map((r) => ({ ...r, type: 'mentor' })), ...(resources.activities || []).map((r) => ({ ...r, type: 'activity' }))];
  // The imported mentor directory is the first resource view; all cards are already deidentified by the server.
  state.resources.sort((a, b) => Number(b.type === 'mentor') - Number(a.type === 'mentor'));
  state.sessions = sessions.sessions || []; renderSessions();
  setResourceCollection(state.resources, { catalogView: true, filter: defaultResourceFilter() }); updateDataNotice();
  const previous = sessionStorage.getItem('qicheng-session');
  if (previous && state.sessions.some((session) => session.id === previous)) await loadSession(previous);
  else { state.session = null; updateContext(); }
}
$('#chat-form').addEventListener('submit', sendMessage);
$('#message-input').addEventListener('input', fitInput);
$('#message-input').addEventListener('keydown', (event) => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('#chat-form').requestSubmit(); } });
$('#stop-button').onclick = () => state.controller?.abort();
$('#new-chat').onclick = () => createSession().catch((error) => toast(error.message));
$('#back-to-assistant').onclick = () => createSession().catch((error) => toast(error.message));
$('#toggle-sidebar').onclick = () => { const open = !$('#sidebar').classList.contains('open'); $('#sidebar').classList.toggle('open', open); $('#sidebar-backdrop').hidden = !open; $('#toggle-sidebar').setAttribute('aria-expanded', String(open)); };
$('#sidebar-backdrop').onclick = closeSidebar;
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeSidebar(); });
for (const button of document.querySelectorAll('.suggestion')) button.onclick = () => sendText(button.dataset.prompt);
for (const button of document.querySelectorAll('.resource-tab')) button.onclick = () => { state.filter = button.dataset.filter; state.visibleCount = 12; updateResourceTabs(); renderResources(); };
$('#resource-search').addEventListener('input', () => { state.search = $('#resource-search').value; state.visibleCount = 12; renderResources(); });
$('#load-more-resources').onclick = () => { state.visibleCount += 12; renderResources(); };
$('#reset-resources').onclick = () => {
  setResourceCollection(state.resources, { catalogView: true, filter: defaultResourceFilter() });
  $('#resources-caption').textContent = '按行业、职业背景或城市筛选导师，进一步交流需由项目运营方确认。';
};
$('#login-form').onsubmit = async (event) => {
  event.preventDefault(); $('#login-submit').disabled = true; $('#login-error').hidden = true;
  try { await api('/api/login', { method: 'POST', body: JSON.stringify({ code: $('#invite-code').value.trim() }) }); $('#invite-code').value = ''; await bootstrap(); }
  catch (error) { $('#login-error').textContent = error.message; $('#login-error').hidden = false; }
  finally { $('#login-submit').disabled = false; }
};
bootstrap().catch((error) => {
  $('#connection-status').classList.add('offline'); $('#connection-status').replaceChildren(el('i'), document.createTextNode('连接暂不可用'));
  $('#resource-list').replaceChildren(el('div', 'resources-empty', '暂时无法加载资源，请确认本地服务正在运行后刷新。'));
  toast(error.message);
});
