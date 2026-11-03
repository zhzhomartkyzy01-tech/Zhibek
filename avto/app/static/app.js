/* Сайт отдела автоперевозок. Чистый JavaScript, без внешних библиотек. */
'use strict';

const LS_TOKEN = 'avto.token';
const LS_FILTERS = 'avto.filters';
const REFRESH_MS = 60000;

const S = {
  token: lsGet(LS_TOKEN),
  me: null,
  refs: null,
  view: null,
  filters: Object.assign({ month: '', employee_id: '', client_id: '', showRefused: false }, jsonParse(lsGet(LS_FILTERS)) || {}),
  timer: null,
  modalOpen: 0,
  data: {},
  sort: { key: 'reg_no', dir: 1 },
  colFilters: {},
};

/* ================================================================ утилиты */

function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
function lsSet(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) { /* нет хранилища */ } }
function jsonParse(s) { try { return JSON.parse(s); } catch (e) { return null; } }
function saveFilters() { lsSet(LS_FILTERS, JSON.stringify(S.filters)); }

function esc(v) {
  return String(v == null ? '' : v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function money(n) {
  if (n == null || n === '' || isNaN(n)) return '—';
  return Math.round(Number(n)).toLocaleString('ru-RU').replace(/,/g, ' ');
}
function pct(p, digits = 1) {
  if (p == null || isNaN(p)) return '—';
  return Number(p).toFixed(digits).replace('.', ',') + '%';
}
function fdate(s) {
  if (!s) return '';
  const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : s;
}
function fdt(s) {
  if (!s) return '';
  const t = String(s).slice(11, 16);
  return fdate(s) + (t && t !== '00:00' ? ' ' + t : '');
}
function today() { return S.refs ? S.refs.today : new Date().toISOString().slice(0, 10); }
function curMonth() { return S.filters.month || today().slice(0, 7); }
const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
function monthTitle(m) { return MONTHS[Number(m.slice(5, 7)) - 1] + ' ' + m.slice(0, 4); }
function can(p) { return S.me && S.me.permissions.includes(p); }
function userName(id) { const u = (S.refs.users || []).find(x => x.id === Number(id)); return u ? u.name : ''; }
function managers() { return S.refs.users.filter(u => u.role === 'manager' && u.active); }
function $(sel, root) { return (root || document).querySelector(sel); }
function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }
function opt(value, label, selected) {
  return `<option value="${esc(value)}"${String(selected) === String(value) ? ' selected' : ''}>${esc(label)}</option>`;
}
function tag(f) { return `<span class="tag ${esc(f.level)}">${esc(f.text)}</span>`; }

function toast(msg, kind) {
  const el = document.createElement('div');
  el.className = 'toast ' + (kind || '');
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), kind === 'error' ? 7000 : 3500);
}

/* ================================================================ API */

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (S.token) opts.headers.Authorization = 'Bearer ' + S.token;
  if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(url, opts); } catch (e) { throw Object.assign(new Error('Нет связи с сервером. Проверьте, что компьютер с сайтом включён'), { status: 0 }); }
  if (res.status === 401 && S.token && !url.startsWith('/api/login')) {
    logout(true);
    throw Object.assign(new Error('Сессия истекла. Войдите заново'), { status: 401 });
  }
  const ct = res.headers.get('content-type') || '';
  const data = ct.includes('json') ? await res.json() : await res.blob();
  if (!res.ok) {
    const err = new Error((data && data.error) || 'Ошибка ' + res.status);
    err.status = res.status; err.data = data;
    throw err;
  }
  return { data, res };
}
const GET = url => api('GET', url).then(r => r.data);
const POST = (url, b) => api('POST', url, b || {}).then(r => r.data);
const PUT = (url, b) => api('PUT', url, b).then(r => r.data);
const DEL = url => api('DELETE', url).then(r => r.data);

async function download(url) {
  try {
    const { data, res } = await api('GET', url);
    const cd = res.headers.get('content-disposition') || '';
    const m = cd.match(/filename\*=UTF-8''([^;]+)/);
    const name = m ? decodeURIComponent(m[1]) : 'export.xlsx';
    const a = document.createElement('a');
    a.href = URL.createObjectURL(data);
    a.download = name;
    document.body.appendChild(a); a.click();
    setTimeout(() => { a.remove(); URL.revokeObjectURL(a.href); }, 5000);
  } catch (e) { toast(e.message, 'error'); }
}

function qs(params) {
  const p = Object.entries(params).filter(([, v]) => v !== '' && v != null && v !== false);
  return p.length ? '?' + p.map(([k, v]) => k + '=' + encodeURIComponent(v)).join('&') : '';
}

/* ================================================================ окна */

function modal({ title, body, foot, narrow, onClose }) {
  const ov = document.createElement('div');
  ov.className = 'overlay';
  ov.innerHTML = `<div class="modal${narrow ? ' narrow' : ''}" role="dialog" aria-modal="true">
    <div class="modal-head"><h2>${title}</h2><button class="btn ghost sm" data-close aria-label="Закрыть">✕</button></div>
    <div class="modal-body">${body}</div>${foot ? `<div class="modal-foot">${foot}</div>` : ''}</div>`;
  document.body.appendChild(ov);
  S.modalOpen++;
  const close = () => {
    if (!ov.isConnected) return;
    ov.remove(); S.modalOpen--;
    document.removeEventListener('keydown', onKey);
    if (onClose) onClose();
  };
  const onKey = e => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  ov.addEventListener('mousedown', e => { if (e.target === ov) close(); });
  $$('[data-close]', ov).forEach(b => b.addEventListener('click', close));
  const first = $('input:not([type=hidden]):not([disabled]), select:not([disabled]), textarea', ov);
  if (first && window.innerWidth > 700) setTimeout(() => first.focus(), 30);
  return { el: ov, close };
}

function confirmBox(text, okLabel = 'Да') {
  return new Promise(resolve => {
    let done = false;
    const m = modal({
      title: 'Подтвердите', narrow: true, body: `<p style="white-space:pre-line;margin:0">${esc(text)}</p>`,
      foot: `<button class="btn primary" data-ok>${esc(okLabel)}</button><button class="btn" data-close>Отмена</button>`,
      onClose: () => { if (!done) resolve(false); },
    });
    $('[data-ok]', m.el).addEventListener('click', () => { done = true; m.close(); resolve(true); });
  });
}

function formData(root) {
  const d = {};
  $$('[name]', root).forEach(el => {
    if (el.disabled && !el.dataset.always) return;
    d[el.name] = el.type === 'checkbox' ? el.checked : el.value;
  });
  return d;
}

/* ================================================================ вход */

async function renderLogin(message) {
  stopTimer();
  const app = $('#app');
  let info;
  try { info = await GET('/api/info'); } catch (e) {
    app.innerHTML = `<div class="login-wrap"><div class="card login"><h1>Нет связи с сервером</h1><p class="muted">${esc(e.message)}</p><button class="btn" onclick="location.reload()">Повторить</button></div></div>`;
    return;
  }
  const addr = info.addresses.map(a => `<div>${esc(a)}</div>`).join('');
  if (info.needs_setup) {
    app.innerHTML = `<div class="login-wrap"><div class="card login">
      <h1>Первый запуск</h1>
      <p class="muted">Задайте PIN администратора (4–6 цифр). Затем в разделе «Админ» добавьте сотрудников и их PIN.</p>
      <form id="setup"><label class="field"><span>PIN администратора</span>
        <input class="pin-input" name="pin" type="password" inputmode="numeric" pattern="\\d{4,6}" maxlength="6" autocomplete="new-password" required></label>
        <label class="field"><span>Повторите PIN</span>
        <input class="pin-input" name="pin2" type="password" inputmode="numeric" pattern="\\d{4,6}" maxlength="6" autocomplete="new-password" required></label>
        <div class="err" id="err"></div><button class="btn primary">Сохранить и войти</button></form>
      <div class="addr">Адрес сайта для сотрудников:${addr}</div></div></div>`;
    $('#setup').addEventListener('submit', async e => {
      e.preventDefault();
      const d = formData(e.target);
      if (d.pin !== d.pin2) { $('#err').textContent = 'PIN не совпадают'; return; }
      try { onLogin(await POST('/api/setup', { pin: d.pin })); } catch (er) { $('#err').textContent = er.message; }
    });
    return;
  }
  const last = lsGet('avto.lastUser');
  app.innerHTML = `<div class="login-wrap"><div class="card login">
    <h1>Отдел автоперевозок</h1><div class="muted">Zhebe Logistics</div>
    <form id="login">
      <label class="field"><span>Сотрудник</span><select name="user_id" required>
        <option value="">— выберите своё имя —</option>
        ${info.users.map(u => opt(u.id, u.name + (u.has_pin ? '' : ' (PIN не задан)'), last)).join('')}
      </select></label>
      <label class="field"><span>PIN</span><input class="pin-input" name="pin" type="password" inputmode="numeric" maxlength="6" autocomplete="current-password" required></label>
      <div class="err" id="err">${esc(message || '')}</div>
      <button class="btn primary">Войти</button>
    </form>
    <div class="addr">Адрес сайта:${addr}</div></div></div>`;
  const f = $('#login');
  if (last && f.user_id.value) f.pin.focus(); else f.user_id.focus();
  f.addEventListener('submit', async e => {
    e.preventDefault();
    const d = formData(f);
    try {
      const r = await POST('/api/login', { user_id: Number(d.user_id), pin: d.pin });
      lsSet('avto.lastUser', d.user_id);
      onLogin(r);
    } catch (er) { $('#err').textContent = er.message; f.pin.value = ''; f.pin.focus(); }
  });
}

function onLogin(r) {
  S.token = r.token;
  lsSet(LS_TOKEN, r.token);
  boot();
}

async function logout(expired) {
  if (!expired && S.token) { try { await POST('/api/logout'); } catch (e) { /* всё равно выходим */ } }
  S.token = null; S.me = null;
  lsSet(LS_TOKEN, null);
  $$('.overlay').forEach(o => o.remove()); S.modalOpen = 0;
  renderLogin(expired ? 'Сессия истекла. Войдите заново' : '');
}

/* ================================================================ каркас */

const VIEWS = [
  { key: 'summary', title: 'Сводка', render: viewSummary },
  { key: 'board', title: 'Доска', render: viewBoard, perm: null },
  { key: 'registry', title: 'Реестр', render: viewRegistry },
  { key: 'leads', title: 'Воронка', render: viewLeads },
  { key: 'reports', title: 'Отчёт за день', render: viewReports },
  { key: 'dirs', title: 'Справочники', render: viewDirs },
  { key: 'admin', title: 'Админ', render: viewAdmin, perm: 'admin' },
];

async function boot() {
  if (!S.token) return renderLogin();
  try {
    S.me = await GET('/api/me');
    S.refs = await GET('/api/refs');
  } catch (e) {
    if (e.status !== 401) $('#app').innerHTML = `<div class="boot">${esc(e.message)} <button class="btn" onclick="location.reload()">Повторить</button></div>`;
    return;
  }
  if (!can('view_all')) S.filters.employee_id = '';
  renderShell();
  window.onhashchange = route;
  route();
  startTimer();
}

function defaultView() { return S.me.role === 'manager' ? 'board' : 'summary'; }

function renderShell() {
  const views = VIEWS.filter(v => !v.perm || can(v.perm));
  $('#app').innerHTML = `
    <header class="top">
      <div class="top-row">
        <div class="brand"><img src="/static/icon.svg" alt="">Автоперевозки</div>
        <div class="spacer"></div>
        <button class="btn sm" id="refresh" title="Обновить данные">⟳ Обновить</button>
        <div class="userbox"><div class="who">${esc(S.me.name)}<small>${esc(S.me.role_name)}</small></div>
          <button class="btn sm" id="logout">Выйти</button></div>
      </div>
      <nav class="tabs">${views.map(v => `<a href="#${v.key}" data-view="${v.key}">${v.title}</a>`).join('')}</nav>
    </header>
    <main><div id="banner"></div><div id="view"></div></main>`;
  $('#logout').addEventListener('click', () => logout(false));
  $('#refresh').addEventListener('click', () => refresh(true));
  renderBanner();
}

function renderBanner() {
  const b = $('#banner');
  if (!b) return;
  b.innerHTML = S.me.report_reminder
    ? `<div class="banner"><b>Сдайте отчёт за день.</b> Это займёт минуту. <a href="#reports" class="btn sm">Открыть отчёт</a></div>` : '';
}

function route() {
  let key = location.hash.slice(1) || defaultView();
  let v = VIEWS.find(x => x.key === key && (!x.perm || can(x.perm)));
  if (!v) { v = VIEWS.find(x => x.key === defaultView()); key = v.key; }
  S.view = v;
  $$('nav.tabs a').forEach(a => a.classList.toggle('active', a.dataset.view === key));
  $('#view').innerHTML = '<div class="empty">Загрузка…</div>';
  v.render();
}

async function refresh(manual) {
  if (S.modalOpen && !manual) return;
  try {
    S.me = await GET('/api/me');
    S.refs = await GET('/api/refs');
    renderBanner();
    if (S.view) await S.view.render(true);
    if (manual) toast('Данные обновлены');
  } catch (e) { if (manual) toast(e.message, 'error'); }
}
function startTimer() { stopTimer(); S.timer = setInterval(() => refresh(false), REFRESH_MS); }
function stopTimer() { if (S.timer) clearInterval(S.timer); S.timer = null; }

function stamp() { return `<span class="updated">обновлено ${new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}</span>`; }

/* общая панель фильтров: месяц, сотрудник, клиент */
function filterBar({ title, employee = true, client = false, extra = '', label = 'Сотрудник' }) {
  const emp = employee && can('view_all')
    ? `<label class="field"><span>${label}</span><select data-f="employee_id">${opt('', 'Все')}${S.refs.users.filter(u => ['manager', 'head'].includes(u.role)).map(u => opt(u.id, u.name, S.filters.employee_id)).join('')}</select></label>` : '';
  const cl = client
    ? `<label class="field"><span>Клиент</span><select data-f="client_id">${opt('', 'Все')}${S.refs.clients.map(c => opt(c.id, c.name, S.filters.client_id)).join('')}</select></label>` : '';
  return `<div class="toolbar"><h2 class="title">${title}</h2>
    <label class="field"><span>Месяц</span><input type="month" data-f="month" value="${esc(curMonth())}"></label>
    ${emp}${cl}${extra}${stamp()}</div>`;
}
function bindFilters(root, rerender) {
  $$('[data-f]', root).forEach(el => el.addEventListener('change', () => {
    S.filters[el.dataset.f] = el.type === 'checkbox' ? el.checked : el.value;
    saveFilters();
    rerender();
  }));
}

/* ================================================================ сводка */

async function viewSummary() {
  const root = $('#view');
  let d;
  try { d = await GET('/api/summary' + qs({ month: curMonth(), employee_id: S.filters.employee_id })); } catch (e) { root.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const t = d.tiles;
  const tile = (label, value, sub, color) => `<div class="card tile ${color || ''}"><div class="label">${label}</div><div class="value">${value}</div>${sub ? `<div class="sub">${sub}</div>` : ''}</div>`;
  const rq = t.requests;
  const reasons = Object.entries(rq.refused_reasons).map(([k, v]) => `${esc(k)}: ${v}`).join(', ');
  const title = 'Сводка' + (d.employee_id ? ' — ' + esc(userName(d.employee_id)) : ' по отделу');
  root.innerHTML = filterBar({ title }) + `
    <div class="tiles">
      ${tile('Рейсов / план', `${t.trips} / ${Math.round(t.plan * 10) / 10}`, pct(t.plan_pct, 0) + ' плана', t.plan_color)}
      ${tile('Выручка, ₸', money(t.revenue))}
      ${tile('Маржа, ₸', money(t.margin), pct(t.margin_pct) + (t.margin_color ? ` — ниже ${d.min_margin_pct}%` : ''), t.margin_color)}
      ${tile('Средний рейс, ₸', money(t.avg_trip))}
      ${tile('Активных рейсов', t.active)}
      ${tile('Просрочек сейчас', t.overdue, t.overdue ? '<a href="#board">на доске</a>' : 'нет', t.overdue ? 'red' : 'green')}
      ${tile('Заявки месяца', `${rq.received} → ${rq.rate_sent} → ${rq.confirmed}`, `получено → ставка → подтверждено; отказ: ${rq.refused}${reasons ? ' (' + reasons + ')' : ''}`)}
    </div>
    <div class="section"><h2>По сотрудникам</h2>${employeeTable(d)}</div>
    <div class="grid2 section">
      <div class="card"><h3>Доля клиентов в марже месяца</h3><p class="small muted">Красным — больше ${d.dependency_pct}%: высокая зависимость</p>${bars(d.clients, true)}</div>
      <div class="card"><h3>Перевозчики по числу рейсов</h3><p class="small muted">&nbsp;</p>${bars(d.carriers, false)}</div>
    </div>
    <div class="grid2 section">
      <div class="card chart"><h3>Рейсы за 12 месяцев</h3>${chart(d.dynamics, 'trips', d.plan)}
        <div class="legend"><span><i style="background:#6b8fe6"></i>рейсы</span><span><i style="background:#c81e1e"></i>план</span></div></div>
      <div class="card chart"><h3>Маржа за 12 месяцев, ₸</h3>${chart(d.dynamics, 'margin')}</div>
    </div>
    ${d.propose_permanent && d.propose_permanent.length ? `<div class="card section"><h3>Предложить перевод в постоянные</h3>
      <p class="small muted">Разовые клиенты с 3 и более успешными рейсами (п. 2.4 регламента).</p>
      ${d.propose_permanent.map(c => `<span class="tag blue" style="margin:2px">${esc(c.name)} — ${c.delivered} рейс.</span>`).join(' ')}</div>` : ''}`;
  bindFilters(root, viewSummary);
}

function employeeTable(d) {
  if (!d.employees.length) return '<div class="card empty">Нет менеджеров</div>';
  const col = c => c ? `c-${c}` : '';
  const rows = d.employees.map(r => {
    const loadCls = r.load_pct > 100 ? 'over' : (r.load_pct < 35 ? 'under' : '');
    return `<tr>
      <td><b>${esc(r.name)}</b></td>
      <td class="num">${r.trips}</td><td class="num">${r.clients}</td>
      <td class="num">${money(r.revenue)}</td>
      <td class="num">${money(r.margin)} <span class="${col(r.margin_color)}">${pct(r.margin_pct)}</span></td>
      <td class="num">${money(r.avg_trip)}</td>
      <td>${r.active} / ${S.refs.settings.overload_threshold} <span class="${r.load_label === 'перегрузка' ? 'c-red' : 'muted'} small">${esc(r.load_label || '')}</span>
        <div class="loadbar"><div class="${loadCls}" style="width:${Math.min(100, r.load_pct || 0)}%"></div></div></td>
      <td class="num ${col(r.rate_color)}">${pct(r.rate_pct, 0)}<div class="small muted">${r.rate_ok}/${r.rate_total}</div></td>
      <td class="num ${col(r.close_color)}">${pct(r.close_pct, 0)}<div class="small muted">${r.close_ok}/${r.close_total}</div></td>
      <td class="num ${r.overdue ? 'c-red' : ''}">${r.overdue}</td>
      <td class="num ${col(r.calls_color)}">${r.calls} / ${r.kp}<div class="small muted">${pct(r.calls_pct, 0)} / ${pct(r.kp_pct, 0)}</div></td>
      <td class="num ${r.reports < r.reports_of ? 'c-yellow' : ''}">${r.reports} из ${r.reports_of}</td>
      <td class="num">${r.leads_in_work} / <span class="${r.leads_stuck ? 'c-yellow' : ''}">${r.leads_stuck}</span></td>
      <td class="num">${r.new_clients}</td></tr>`;
  }).join('');
  return `<div class="table-wrap"><table class="t"><thead><tr>
    <th>Сотрудник</th><th class="num">Рейсов</th><th class="num">Клиентов</th><th class="num">Выручка, ₸</th><th class="num">Маржа, ₸ и %</th>
    <th class="num">Средний рейс</th><th>Загрузка</th><th class="num" title="Ставка отправлена за 2 рабочих часа">Ставка за 2 ч</th>
    <th class="num" title="Закрыт не позже 5 рабочих дней после выгрузки">Закрытие за 5 дн.</th><th class="num">Просрочки</th>
    <th class="num" title="Сумма за 5 рабочих дней и % от нормы">Звонки / КП за 5 дн.</th><th class="num">Отчётов</th>
    <th class="num" title="Лиды на этапах 1–3 / из них зависли">Лиды / зависли</th><th class="num">Новых клиентов</th>
    </tr></thead><tbody>${rows}</tbody></table></div>`;
}

function bars(items, money_) {
  if (!items.length) return '<div class="empty">Нет данных за месяц</div>';
  const max = Math.max(...items.map(i => i.pct || 0), 1);
  return `<div class="bars">${items.map(b => `<div class="bar-row">
      <div class="name${b.high ? ' high' : ''}" title="${esc(b.name)}">${esc(b.name)}${b.high ? '<br><span class="tag red">высокая зависимость</span>' : ''}</div>
      <div class="bar-track"><div class="bar-fill ${b.high ? 'high' : ''} ${b.rest ? 'rest' : ''}" style="width:${(b.pct || 0) / max * 100}%"></div></div>
      <div class="num" title="${money_ ? money(b.value) + ' ₸' : b.value + ' рейс.'}">${pct(b.pct, 0)}${money_ ? '' : ` <span class="muted small">${b.value}</span>`}</div></div>`).join('')}</div>`;
}

function chart(items, key, plan) {
  const W = 560, H = 210, L = 62, B = 34, T = 12, R = 8;
  const vals = items.map(i => i[key] || 0);
  const max = Math.max(...vals, plan || 0, 1) * 1.1;
  const bw = (W - L - R) / items.length;
  const y = v => T + (H - T - B) * (1 - v / max);
  const fmt = v => key === 'margin' ? (v >= 1e6 ? (v / 1e6).toFixed(1).replace('.', ',') + ' млн' : Math.round(v / 1000) + ' тыс') : Math.round(v);
  let g = '';
  for (let k = 0; k <= 4; k++) {
    const v = max / 1.1 * k / 4, yy = y(v);
    g += `<line x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}" stroke="#e2e5ea"/><text x="${L - 6}" y="${yy + 4}" text-anchor="end">${fmt(v)}</text>`;
  }
  items.forEach((it, i) => {
    const v = it[key] || 0, x = L + i * bw + bw * 0.18, w = bw * 0.64;
    g += `<rect x="${x}" y="${y(v)}" width="${w}" height="${Math.max(0, H - B - y(v))}" rx="3" fill="${i === items.length - 1 ? '#1d4ed8' : '#6b8fe6'}"><title>${esc(it.title)}: ${key === 'margin' ? money(v) + ' ₸' : v + ' рейсов'}</title></rect>`;
    g += `<text x="${L + i * bw + bw / 2}" y="${H - B + 14}" text-anchor="middle">${MONTHS[Number(it.month.slice(5, 7)) - 1].slice(0, 3)}</text>`;
    if (it.month.endsWith('-01') || i === 0) g += `<text x="${L + i * bw + bw / 2}" y="${H - B + 27}" text-anchor="middle">${it.month.slice(0, 4)}</text>`;
  });
  if (plan) g += `<line x1="${L}" x2="${W - R}" y1="${y(plan)}" y2="${y(plan)}" stroke="#c81e1e" stroke-dasharray="5 4" stroke-width="1.5"/>`;
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="График">${g}</svg>`;
}

/* ================================================================ доска рейсов */

async function viewBoard() {
  const root = $('#view');
  let d;
  try { d = await GET('/api/trips' + qs({ scope: 'board', month: curMonth(), employee_id: S.filters.employee_id, client_id: S.filters.client_id })); } catch (e) { root.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  S.data.board = d;
  const add = can('trips_edit') ? '<button class="btn primary" id="new-trip">+ Новый рейс</button>' : '';
  const cols = S.refs.board_columns.map(c => {
    const items = d.trips.filter(t => t.column === c.key);
    return `<div class="col"><div class="col-head"><span>${esc(c.title)}</span><span class="count">${items.length}</span></div>
      ${items.map(tripCard).join('') || '<div class="empty small">пусто</div>'}</div>`;
  }).join('');
  root.innerHTML = filterBar({ title: 'Доска рейсов', client: true, extra: add }) + `<div class="board">${cols}</div>`;
  bindFilters(root, viewBoard);
  if ($('#new-trip')) $('#new-trip').addEventListener('click', () => tripForm(null));
  bindTripActions($('.board', root));
}

function tripCard(t) {
  const st = S.refs.trip_stages;
  const edit = can('trips_edit');
  const next = t.stage >= 1 && t.stage <= 9 ? st[t.stage + 1] : null;
  return `<div class="tcard${t.overdue ? ' overdue' : ''}" data-id="${t.id}">
    <div class="row1"><span>№ ${t.reg_no || '—'} · ${fdate(t.ship_date) || fdate(t.received_at)}</span><span>${esc(t.employee_name || '')}</span></div>
    <div class="client">${esc(t.client_name || '—')}</div>
    <div class="route">${esc(t.origin || '?')} → ${esc(t.destination || '?')}</div>
    <div class="stage">${esc(t.stage_name)}</div>
    <div class="meta">${t.carrier_name ? esc(t.carrier_name) : '<i>перевозчик не назначен</i>'}${t.driver_location && [6, 7].includes(t.stage) ? ' · 📍' + esc(t.driver_location) : ''}</div>
    <div class="money"><span>Ставка: <b>${money(t.price)}</b></span>${t.margin != null ? `<span>Маржа: <b>${money(t.margin)}</b> (${pct(t.margin_pct)})</span>` : ''}</div>
    ${t.flags.length ? `<div class="flags">${t.flags.map(tag).join('')}</div>` : ''}
    <div class="actions">
      ${edit && next ? `<button class="btn sm next" data-act="advance" title="Перейти к этапу «${esc(next)}»">→ ${esc(next)}</button>` : ''}
      ${edit && [6, 7].includes(t.stage) ? '<button class="btn sm" data-act="contact">На связи</button>' : ''}
      <button class="btn sm" data-act="open">${edit ? 'Изменить' : 'Открыть'}</button>
      ${edit && [1, 2, 3].includes(t.stage) ? '<button class="btn sm danger" data-act="refuse">Отказ</button>' : ''}
    </div></div>`;
}

function findTrip(id) {
  const lists = [S.data.board && S.data.board.trips, S.data.registry && S.data.registry.trips].filter(Boolean);
  for (const l of lists) { const t = l.find(x => x.id === id); if (t) return t; }
  return null;
}

function bindTripActions(root) {
  root.addEventListener('click', async e => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const card = b.closest('[data-id]');
    const id = Number(card.dataset.id);
    const t = findTrip(id);
    const act = b.dataset.act;
    if (act === 'open') return tripForm(id);
    if (act === 'advance') return advanceTrip(t);
    if (act === 'contact') return contactDialog(t);
    if (act === 'refuse') return refuseDialog(t);
  });
}

async function advanceTrip(t, after) {
  try {
    const r = await POST(`/api/trips/${t.id}/advance`, { version: t.version });
    toast(`Этап: ${r.stage_name}`, 'ok');
    if (after) after(r); else refresh(true);
  } catch (e) {
    toast(e.message, 'error');
    if (e.data && e.data.code === 'missing' && !after) tripForm(t.id, e.data.missing);
    if (e.status === 409) refresh(true);
  }
}

function contactDialog(t, after) {
  const m = modal({
    title: 'Водитель на связи', narrow: true,
    body: `<p class="muted small" style="margin-top:0">Время связи запишет сервер. Местонахождение — по желанию.</p>
      <label class="field"><span>Где сейчас водитель</span><input name="location" value="${esc(t.driver_location || '')}" placeholder="Например: Шымкент, граница"></label>`,
    foot: '<button class="btn primary" data-ok>Отметить</button><button class="btn" data-close>Отмена</button>',
  });
  $('[data-ok]', m.el).addEventListener('click', async () => {
    try {
      const r = await POST(`/api/trips/${t.id}/contact`, { location: $('[name=location]', m.el).value });
      m.close(); toast('Связь отмечена', 'ok');
      if (after) after(r); else refresh(true);
    } catch (e) { toast(e.message, 'error'); }
  });
}

function refuseDialog(t, after) {
  const m = modal({
    title: 'Отказ клиента', narrow: true,
    body: `<label class="field"><span>Причина <b>*</b></span><select name="reason">${opt('', '— выберите —')}${S.refs.trip_refusal_reasons.map(r => opt(r, r)).join('')}</select></label>
      <label class="field" style="margin-top:10px"><span>Комментарий</span><textarea name="comment"></textarea></label>`,
    foot: '<button class="btn danger" data-ok>Оформить отказ</button><button class="btn" data-close>Отмена</button>',
  });
  $('[data-ok]', m.el).addEventListener('click', async () => {
    const d = formData(m.el);
    if (!d.reason) return toast('Выберите причину отказа', 'error');
    try {
      const r = await POST(`/api/trips/${t.id}/refuse`, { version: t.version, reason: d.reason, comment: d.comment });
      m.close(); toast('Отказ оформлен', 'ok');
      if (after) after(r); else refresh(true);
    } catch (e) { toast(e.message, 'error'); }
  });
}

/* ---------------------------------------------------------------- форма рейса */

const TRIP_GROUPS = [
  ['Заявка', [
    ['client_name', 'Клиент', 'client', 1], ['client_type', 'Тип клиента', 'ctype', 1], ['employee_id', 'Сотрудник', 'employee', 1],
    ['origin', 'Пункт отправления', 'place', 1], ['destination', 'Пункт назначения', 'place', 1], ['body_type', 'Тент/Реф', 'body', 1],
    ['price', 'Стоимость перевозки, ₸', 'money', 2], ['ship_date', 'Дата отправки', 'date', 6],
  ]],
  ['Перевозчик и машина', [
    ['carrier_name', 'Перевозчик', 'carrier', 4], ['carrier_rate', 'Ставка перевозчика, ₸', 'money', 4], ['_margin', 'Разница (маржа)', 'margin'],
    ['truck_brand', 'Марка машины', 'text'], ['plate', 'Гос. номер (тягач/прицеп)', 'text'], ['driver', 'Данные водителя (ФИО)', 'text'],
    ['driver_location', 'Местонахождение водителя', 'text'], ['ttn', 'Номер ТТН', 'text', 8],
  ]],
  ['Для бухгалтерии', [
    ['payment_doc', 'Платёжка', 'text'], ['pay_client', 'Оплата от клиента', 'text'], ['pay_agent', 'Оплата агенту', 'text'],
    ['postpay', 'Постоплата', 'text'], ['comment', 'Комментарий', 'textarea'],
  ]],
];

async function tripForm(id, missingLabels) {
  let t = null;
  if (id) { try { t = await GET('/api/trips/' + id); } catch (e) { return toast(e.message, 'error'); } }
  const edit = can('trips_edit') && (!t || !t.deleted);
  const v = t || { stage: 1, employee_id: S.me.id, client_type: 'Разовый', body_type: '' };
  const stage = v.stage;
  const missing = new Set(missingLabels || []);
  const dl = (id_, items) => `<datalist id="${id_}">${items.map(x => `<option value="${esc(x)}">`).join('')}</datalist>`;
  const reqStage = { 1: 1, 2: 2, 4: 4, 6: 6, 8: 8 };
  const field = ([name, label, kind, req]) => {
    const required = req && (stage >= req || (stage === 0 && req <= 1));
    const nextReq = req && !required && req === stage + 1;
    const lbl = `<span>${esc(label)}${required ? ' <b>*</b>' : ''}${nextReq ? ' <span class="small">(к след. этапу)</span>' : ''}</span>`;
    const val = name === 'client_name' ? v.client_name : name === 'carrier_name' ? v.carrier_name : v[name];
    const dis = edit ? '' : ' disabled';
    let input;
    switch (kind) {
      case 'client': input = `<input name="${name}" list="dl-clients" value="${esc(val)}" autocomplete="off"${dis}>`; break;
      case 'carrier': input = `<input name="${name}" list="dl-carriers" value="${esc(val)}" autocomplete="off"${dis}>`; break;
      case 'place': input = `<input name="${name}" list="dl-places" value="${esc(val)}" autocomplete="off"${dis}>`; break;
      case 'ctype': input = `<select name="${name}"${dis}>${S.refs.client_types.map(c => opt(c, c, val)).join('')}</select>`; break;
      case 'body': input = `<select name="${name}"${dis}>${opt('', '—')}${S.refs.body_types.map(c => opt(c, c, val)).join('')}</select>`; break;
      case 'employee': {
        const ok = edit && can('reassign');
        input = `<select name="${name}"${ok ? '' : ' disabled'}>${S.refs.users.filter(u => ['manager', 'head'].includes(u.role) || u.id === Number(val)).map(u => opt(u.id, u.name, val)).join('')}</select>`;
        break;
      }
      case 'money': input = `<input name="${name}" type="text" inputmode="numeric" value="${val == null ? '' : money(val)}"${dis}>`; break;
      case 'date': input = `<input name="${name}" type="date" value="${esc(val || '')}"${dis}>`; break;
      case 'textarea': return `<label class="field wide"><span>${esc(label)}</span><textarea name="${name}"${dis}>${esc(val || '')}</textarea></label>`;
      case 'margin': return `<div class="field"><span class="small muted">${esc(label)}</span><div id="margin-box" style="padding:7px 0;font-weight:600">—</div></div>`;
      default: input = `<input name="${name}" value="${esc(val == null ? '' : val)}"${dis}>`;
    }
    return `<label class="field${missing.has(label.replace(/, ₸$/, '').replace(' (ФИО)', '').replace(' (тягач/прицеп)', '')) ? ' missing' : ''}">${lbl}${input}</label>`;
  };
  const st = S.refs.trip_stages;
  const stageBar = t ? `<div class="stagebar">${[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(s => {
    const cls = stage === 0 ? (t.times[s] ? 'done' : '') : s < stage ? 'done' : s === stage ? 'cur' : '';
    return `<span class="${cls}">${s}. ${esc(st[s])}</span>`;
  }).join('')}${stage === 0 ? `<span class="ref">Отказ клиента: ${esc(t.refusal_reason || '')}</span>` : ''}</div>
    ${t.flags.length ? `<div class="flags" style="margin-bottom:10px">${t.flags.map(tag).join(' ')}</div>` : ''}` : '';
  const hist = t ? `<div class="fgroup"><h3>История этапов</h3><div class="table-wrap"><table class="t"><thead><tr><th>Этап</th><th>Время</th><th>Кто</th></tr></thead><tbody>
    ${t.history.map(h => `<tr><td>${esc(h.stage_name)}</td><td class="nowrap">${fdt(h.at)}${h.imported ? ' <span class="tag grey">импорт</span>' : ''}</td><td>${esc(h.user_name || '')}</td></tr>`).join('')}
    ${t.last_contact_at ? `<tr><td>Последняя связь с водителем</td><td>${fdt(t.last_contact_at)}</td><td></td></tr>` : ''}
    </tbody></table></div>
    <details class="log"><summary>Журнал изменений (${t.log.length})</summary><div class="log-list">${t.log.map(logLine).join('')}</div></details></div>` : '';
  const next = t && stage >= 1 && stage <= 9 ? st[stage + 1] : null;
  const foot = edit ? `
    <button class="btn primary" data-act="save">${t ? 'Сохранить' : 'Создать рейс'}</button>
    ${next ? `<button class="btn next" data-act="save-advance">Сохранить и → ${esc(next)}</button>` : ''}
    ${t && [6, 7].includes(stage) ? '<button class="btn" data-act="contact">На связи</button>' : ''}
    <span class="spacer"></span>
    ${t && stage !== 1 ? '<button class="btn" data-act="rollback">Вернуть на прошлый этап</button>' : ''}
    ${t && [1, 2, 3].includes(stage) ? '<button class="btn danger" data-act="refuse">Отказ</button>' : ''}
    ${t && can('delete') ? '<button class="btn danger" data-act="delete">Удалить</button>' : ''}`
    : '<button class="btn" data-close>Закрыть</button>';
  const m = modal({
    title: t ? `Рейс № ${t.reg_no || '—'} · ${esc(t.client_name || '')} <span class="muted small">${esc(t.stage_name)}</span>` : 'Новый рейс',
    body: stageBar + TRIP_GROUPS.map(([g, fields]) => `<div class="fgroup"><h3>${g}</h3><div class="fgrid">${fields.map(field).join('')}</div></div>`).join('')
      + hist + dl('dl-clients', S.refs.clients.map(c => c.name)) + dl('dl-carriers', S.refs.carriers.map(c => c.name)) + dl('dl-places', S.refs.places),
    foot,
  });
  const el = m.el;
  const num = s => { const n = String(s || '').replace(/[\s ]/g, '').replace(',', '.'); return n === '' ? null : Number(n); };
  const updMargin = () => {
    const p = num($('[name=price]', el).value), r = num($('[name=carrier_rate]', el).value);
    const box = $('#margin-box', el);
    if (p == null || r == null || isNaN(p) || isNaN(r)) { box.textContent = '—'; return; }
    const mm = p - r, pp = p ? mm / p * 100 : null;
    box.innerHTML = `${money(mm)} ₸ <span class="${pp != null && pp < S.refs.settings.min_margin_pct ? 'c-red' : 'muted'}">${pct(pp)}</span>`;
  };
  $$('[name=price],[name=carrier_rate]', el).forEach(i => i.addEventListener('input', updMargin));
  updMargin();
  const cn = $('[name=client_name]', el);
  if (cn) cn.addEventListener('change', () => {
    const c = S.refs.clients.find(x => x.name.toLowerCase() === cn.value.trim().toLowerCase());
    if (c) $('[name=client_type]', el).value = c.type;
  });
  const collect = () => {
    const d = formData(el);
    delete d._margin;
    ['price', 'carrier_rate'].forEach(k => { if (k in d) d[k] = d[k].replace(/[\s ]/g, ''); });
    if (!can('reassign')) delete d.employee_id;
    if (t) d.version = t.version;
    return d;
  };
  const save = async () => {
    const d = collect();
    return t ? PUT('/api/trips/' + t.id, d) : POST('/api/trips', d);
  };
  const reopen = (r, miss) => { m.close(); tripForm(r.id, miss); };
  $$('.modal-foot [data-act]', el).forEach(b => b.addEventListener('click', async () => {
    const act = b.dataset.act;
    try {
      if (act === 'save') { const r = await save(); toast(t ? 'Сохранено' : 'Рейс создан', 'ok'); m.close(); refresh(true); return; }
      if (act === 'save-advance') {
        const r = await save();
        try { await POST(`/api/trips/${r.id}/advance`, { version: r.version }); toast('Сохранено, этап изменён', 'ok'); m.close(); refresh(true); }
        catch (e) { toast(e.message, 'error'); reopen(r, e.data && e.data.missing); }
        return;
      }
      if (act === 'contact') { m.close(); return contactDialog(t, () => tripForm(t.id)); }
      if (act === 'refuse') { m.close(); return refuseDialog(t, () => { refresh(true); }); }
      if (act === 'rollback') {
        if (!await confirmBox(`Вернуть рейс на этап «${stage === 0 ? 'до отказа' : st[stage - 1]}»? Время этапа «${st[stage]}» будет стёрто (запишется в журнал).`, 'Вернуть')) return;
        await POST(`/api/trips/${t.id}/rollback`, { version: t.version }); toast('Этап возвращён', 'ok'); m.close(); refresh(true); tripForm(t.id); return;
      }
      if (act === 'delete') {
        if (!await confirmBox('Удалить рейс? Администратор сможет его восстановить.', 'Удалить')) return;
        await DEL('/api/trips/' + t.id); toast('Рейс удалён', 'ok'); m.close(); refresh(true);
      }
    } catch (e) {
      toast(e.message, 'error');
      if (e.data && e.data.missing) $$('label.field', el).forEach(l => {
        const txt = (l.querySelector('span') || {}).textContent || '';
        l.classList.toggle('missing', e.data.missing.some(x => txt.startsWith(x)));
      });
    }
  }));
}

const ACTIONS = {
  create: 'создал(а)', update: 'изменил(а)', stage: 'этап', rollback: 'вернул(а) этап', refuse: 'отказ', contact: 'на связи',
  delete: 'удалил(а)', restore: 'восстановил(а)', import: 'импорт', import_update: 'обновлено импортом', merge: 'объединение',
  login: 'вход', locked: 'блокировка входа', set_pin: 'новый PIN', unlock: 'разблокировка', setup_pin: 'первый PIN',
};
const CHANGE_LABELS = {
  stage: 'этап', client_id: 'клиент', carrier_id: 'перевозчик', employee_id: 'сотрудник', price: 'стоимость',
  carrier_rate: 'ставка перевозчика', ship_date: 'дата отправки', last_contact_at: 'связь', driver_location: 'местонахождение',
  refusal_reason: 'причина отказа', comment: 'комментарий', stage_time_removed: 'стёрто время этапа',
};
function changesText(changes) {
  if (!changes) return '';
  const f = x => x == null || x === '' ? '∅' : (typeof x === 'object' ? JSON.stringify(x) : String(x));
  return Object.entries(changes).map(([k, v]) => `${esc(CHANGE_LABELS[k] || k)}: ${Array.isArray(v) && v.length === 2 ? esc(f(v[0])) + ' → ' + esc(f(v[1])) : esc(f(v))}`).join('; ');
}
function logLine(a) {
  const ch = changesText(a.changes);
  return `<div><span class="muted">${fdt(a.at)}</span> <b>${esc(a.user_name || 'система')}</b> ${esc(ACTIONS[a.action] || a.action)}${ch ? ': ' + ch : ''}</div>`;
}

/* ================================================================ реестр */

const REG_COLS = [
  ['reg_no', '№', 'num'], ['ship_date', 'Дата отправки', 'date'], ['truck_brand', 'Марка машины'], ['plate', 'Гос. номер'],
  ['driver', 'Водитель'], ['origin', 'Пункт отправления'], ['destination', 'Пункт назначения'], ['price', 'Стоимость перевозки', 'money'],
  ['body_type', 'Тент/Реф'], ['payment_doc', 'Платёжка'], ['driver_location', 'Местонахождение'], ['client_name', 'Клиент'],
  ['employee_name', 'Сотрудник'], ['carrier_name', 'Перевозчик'], ['carrier_rate', 'Ставка перевозчика', 'money'],
  ['margin', 'Разница', 'money'], ['margin_pct', '%', 'pct'], ['ttn', 'Номер ТТН'], ['status_1c', 'Статус'], ['stage_name', 'Этап'],
  ['pay_client', 'Оплата от клиента'], ['pay_agent', 'Оплата агенту'], ['postpay', 'Постоплата'], ['client_type', 'Тип клиента'],
];

async function viewRegistry(isRefresh) {
  const root = $('#view');
  let d;
  try { d = await GET('/api/trips' + qs({ scope: 'registry', month: curMonth(), employee_id: S.filters.employee_id, client_id: S.filters.client_id })); } catch (e) { root.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  S.data.registry = d;
  const focused = document.activeElement && document.activeElement.dataset.cf;
  const extra = `<label class="check" style="align-self:center"><input type="checkbox" data-f="showRefused"${S.filters.showRefused ? ' checked' : ''}> отказы</label>
    ${can('trips_edit') ? '<button class="btn" id="new-trip">+ Новый рейс</button>' : ''}
    <button class="btn primary" id="export1c" title="Рейсы месяца на этапах «Загружен» и дальше, в формате реестра для 1С">⬇ Выгрузить для 1С</button>`;
  root.innerHTML = filterBar({ title: 'Реестр ' + monthTitle(curMonth()).toLowerCase(), client: true, extra }) + '<div id="reg-table"></div>';
  bindFilters(root, viewRegistry);
  $('#export1c').addEventListener('click', () => download('/api/export/1c' + qs({ month: curMonth(), employee_id: S.filters.employee_id })));
  if ($('#new-trip')) $('#new-trip').addEventListener('click', () => tripForm(null));
  renderRegistryTable();
  if (focused) { const i = $(`[data-cf="${focused}"]`); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }
}

function renderRegistryTable() {
  const d = S.data.registry;
  let items = d.trips.filter(t => S.filters.showRefused || t.stage !== 0);
  const cf = S.colFilters;
  const cell = (t, [k, , kind]) => {
    const v = t[k];
    if (kind === 'money') return money(v);
    if (kind === 'pct') return pct(v);
    if (kind === 'date') return fdate(v);
    return v == null ? '' : String(v);
  };
  items = items.filter(t => REG_COLS.every(c => !cf[c[0]] || cell(t, c).toLowerCase().includes(cf[c[0]].toLowerCase())));
  const { key, dir } = S.sort;
  items.sort((a, b) => {
    const x = a[key], y = b[key];
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    return (typeof x === 'number' ? x - y : String(x).localeCompare(String(y), 'ru')) * dir;
  });
  const tot = k => items.filter(t => t.stage !== 0).reduce((s, t) => s + (t[k] || 0), 0);
  const tp = tot('price'), tm = tot('margin');
  const head = REG_COLS.map(([k, l, kind]) => `<th class="sortable ${['money', 'pct', 'num'].includes(kind) ? 'num' : ''}" data-sort="${k}">${esc(l)}${key === k ? (dir > 0 ? ' ▲' : ' ▼') : ''}</th>`).join('');
  const filt = REG_COLS.map(([k]) => `<th><input data-cf="${k}" value="${esc(cf[k] || '')}" placeholder="фильтр"></th>`).join('');
  const body = items.map(t => `<tr class="click${t.stage === 0 ? ' refused' : ''}" data-id="${t.id}">${REG_COLS.map(c => {
    const kind = c[2];
    let v = esc(cell(t, c));
    if (c[0] === 'stage_name') v = `${v}${t.flags.filter(f => f.level === 'red').map(f => ' ' + tag(f)).join('')}`;
    if (c[0] === 'margin_pct' && t.margin_pct != null && t.margin_pct < S.refs.settings.min_margin_pct) v = `<span class="c-red">${v}</span>`;
    return `<td class="${['money', 'pct', 'num'].includes(kind) ? 'num' : ''}${c[0] === 'stage_name' ? ' keep' : ''}" title="${esc(cell(t, c))}">${v}</td>`;
  }).join('')}</tr>`).join('');
  const foot = REG_COLS.map(([k]) => `<td class="num">${k === 'reg_no' ? '' : k === 'ship_date' ? 'Итого: ' + items.filter(t => t.stage !== 0).length : k === 'price' ? money(tp) : k === 'carrier_rate' ? money(tot('carrier_rate')) : k === 'margin' ? money(tm) : k === 'margin_pct' ? pct(tp ? tm / tp * 100 : null) : ''}</td>`).join('');
  $('#reg-table').innerHTML = items.length || Object.values(cf).some(Boolean)
    ? `<div class="table-wrap" style="max-height:calc(100vh - 210px)"><table class="t registry"><thead><tr>${head}</tr><tr class="filters">${filt}</tr></thead><tbody>${body}</tbody><tfoot><tr>${foot}</tr></tfoot></table></div>`
    : '<div class="card empty">В этом месяце рейсов нет</div>';
  const wrap = $('#reg-table');
  $$('th[data-sort]', wrap).forEach(th => th.addEventListener('click', () => {
    const k = th.dataset.sort;
    S.sort = { key: k, dir: S.sort.key === k ? -S.sort.dir : 1 };
    renderRegistryTable();
  }));
  $$('input[data-cf]', wrap).forEach(i => i.addEventListener('input', () => {
    S.colFilters[i.dataset.cf] = i.value;
    clearTimeout(i._t);
    i._t = setTimeout(() => {
      const k = i.dataset.cf, pos = i.selectionStart;
      renderRegistryTable();
      const n = $(`[data-cf="${k}"]`); if (n) { n.focus(); n.setSelectionRange(pos, pos); }
    }, 250);
  }));
  $$('tbody tr[data-id]', wrap).forEach(tr => tr.addEventListener('click', () => tripForm(Number(tr.dataset.id))));
}

/* ================================================================ воронка */

async function viewLeads() {
  const root = $('#view');
  let d;
  try { d = await GET('/api/leads' + qs({ month: curMonth(), owner_id: S.filters.employee_id })); } catch (e) { root.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  S.data.leads = d;
  const f = d.funnel;
  const ls = S.refs.lead_stages;
  const edit = can('leads_edit');
  const conv = `<div class="funnel-stats">${[1, 2, 3, 4].map((k, i) => `${i ? `<div class="funnel-arrow">→<br>${pct(f.conversion[i - 1].pct, 0)}</div>` : ''}<div class="funnel-step"><b>${f.counts[k]}</b>${esc(ls[k])}</div>`).join('')}
    <div class="funnel-step"><b>${f.refused}</b>отказ${Object.keys(f.refused_reasons).length ? ` <span class="small muted">(${Object.entries(f.refused_reasons).map(([k, v]) => esc(k) + ': ' + v).join(', ')})</span>` : ''}</div></div>`;
  const src = f.by_source.length ? `<div class="table-wrap" style="margin-bottom:14px"><table class="t"><thead><tr><th>Источник</th><th class="num">Лидов</th><th class="num">КП</th><th class="num">Первый рейс</th><th class="num">Отказ</th><th class="num">Конверсия в рейс</th></tr></thead><tbody>
    ${f.by_source.map(s => `<tr><td>${esc(s.source)}</td><td class="num">${s.leads}</td><td class="num">${s.kp}</td><td class="num">${s.first_trip}</td><td class="num">${s.refused}</td><td class="num">${pct(s.leads ? s.first_trip / s.leads * 100 : null, 0)}</td></tr>`).join('')}</tbody></table></div>` : '';
  const cols = [1, 2, 3, 4, 0].map(k => {
    const items = d.leads.filter(l => l.stage === k);
    return `<div class="col"><div class="col-head"><span>${esc(ls[k])}</span><span class="count">${items.length}</span></div>${items.map(l => `
      <div class="tcard" data-id="${l.id}">
        <div class="row1"><span>${esc(l.source || '')}</span><span>${esc(l.owner_name || '')}</span></div>
        <div class="client">${esc(l.company)}</div>
        ${l.cargo ? `<div class="meta">${esc(l.cargo)}${l.volume ? ' · ' + esc(l.volume) + ' маш./мес' : ''}</div>` : ''}
        ${[1, 2, 3].includes(l.stage) ? `<div class="meta">${l.days_on_stage != null ? l.days_on_stage + ' дн. на этапе' : ''}</div>` : ''}
        ${l.next_step ? `<div class="meta">След. шаг: ${esc(l.next_step)}${l.next_step_date ? ' — ' + fdate(l.next_step_date) : ''}</div>` : ''}
        ${l.stage === 0 ? `<div class="meta">Причина: ${esc(l.refusal_reason || '')}</div>` : ''}
        ${l.flags.length ? `<div class="flags">${l.flags.map(tag).join('')}</div>` : ''}
        <div class="actions">
          ${edit && [1, 2].includes(l.stage) ? `<button class="btn sm next" data-act="advance">→ ${esc(ls[l.stage + 1])}</button>` : ''}
          <button class="btn sm" data-act="open">${edit ? 'Изменить' : 'Открыть'}</button>
          ${edit && [1, 2, 3].includes(l.stage) ? '<button class="btn sm danger" data-act="refuse">Отказ</button>' : ''}
        </div></div>`).join('') || '<div class="empty small">пусто</div>'}</div>`;
  }).join('');
  root.innerHTML = filterBar({ title: 'Воронка новых клиентов', label: 'Ответственный', extra: edit ? '<button class="btn primary" id="new-lead">+ Новый лид</button>' : '' })
    + `<p class="small muted" style="margin-top:-6px">Конверсия — по лидам, созданным в ${monthTitle(curMonth()).toLowerCase()}. На доске: все лиды в работе + закрытые в этом месяце.</p>`
    + conv + src + `<div class="board leads">${cols}</div>`;
  bindFilters(root, viewLeads);
  if ($('#new-lead')) $('#new-lead').addEventListener('click', () => leadForm(null));
  $('.board', root).addEventListener('click', async e => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const id = Number(b.closest('[data-id]').dataset.id);
    const l = d.leads.find(x => x.id === id);
    if (b.dataset.act === 'open') return leadForm(id);
    if (b.dataset.act === 'advance') {
      try { await POST(`/api/leads/${id}/advance`, { version: l.version }); toast('Этап изменён', 'ok'); refresh(true); } catch (er) { toast(er.message, 'error'); }
    }
    if (b.dataset.act === 'refuse') leadRefuse(l);
  });
}

function leadRefuse(l, after) {
  const m = modal({
    title: 'Отказ: ' + esc(l.company), narrow: true,
    body: `<label class="field"><span>Причина <b>*</b></span><select name="reason">${opt('', '— выберите —')}${S.refs.lead_refusal_reasons.map(r => opt(r, r)).join('')}</select></label>
      <label class="field" style="margin-top:10px"><span>Комментарий</span><textarea name="comment"></textarea></label>`,
    foot: '<button class="btn danger" data-ok>Оформить отказ</button><button class="btn" data-close>Отмена</button>',
  });
  $('[data-ok]', m.el).addEventListener('click', async () => {
    const d = formData(m.el);
    if (!d.reason) return toast('Выберите причину', 'error');
    try { await POST(`/api/leads/${l.id}/refuse`, { version: l.version, reason: d.reason, comment: d.comment }); m.close(); toast('Отказ оформлен', 'ok'); refresh(true); if (after) after(); } catch (e) { toast(e.message, 'error'); }
  });
}

async function leadForm(id) {
  let l = null;
  if (id) { try { l = await GET('/api/leads/' + id); } catch (e) { return toast(e.message, 'error'); } }
  const edit = can('leads_edit');
  const v = l || { owner_id: S.me.id, source: '' };
  const dis = edit ? '' : ' disabled';
  const ls = S.refs.lead_stages;
  const f = (name, label, input) => `<label class="field"><span>${label}</span>${input || `<input name="${name}" value="${esc(v[name] || '')}"${dis}>`}</label>`;
  const body = `${l ? `<div class="stagebar">${[1, 2, 3, 4].map(s => `<span class="${l.stage === 0 ? (l.times[s] ? 'done' : '') : s < l.stage ? 'done' : s === l.stage ? 'cur' : ''}">${s}. ${esc(ls[s])}</span>`).join('')}${l.stage === 0 ? `<span class="ref">Отказ: ${esc(l.refusal_reason || '')}</span>` : ''}</div>
      ${l.flags.length ? `<div class="flags" style="margin-bottom:10px">${l.flags.map(tag).join(' ')}</div>` : ''}` : ''}
    <div class="fgrid">
      ${f('company', 'Компания <b>*</b>', `<input name="company" value="${esc(v.company || '')}" list="dl-clients2" autocomplete="off"${dis}>`)}
      ${f('source', 'Источник', `<select name="source"${dis}>${opt('', '—')}${S.refs.lead_sources.map(s => opt(s, s, v.source)).join('')}</select>`)}
      ${f('owner_id', 'Ответственный', `<select name="owner_id"${edit && can('reassign') ? '' : ' disabled'}>${S.refs.users.filter(u => ['manager', 'head'].includes(u.role) || u.id === v.owner_id).map(u => opt(u.id, u.name, v.owner_id)).join('')}</select>`)}
      ${f('contact_name', 'Контактное лицо')}${f('contact_phone', 'Телефон', `<input name="contact_phone" type="tel" value="${esc(v.contact_phone || '')}"${dis}>`)}
      ${f('cargo', 'Что возят и куда')}${f('volume', 'Ожидаемый объём, машин в месяц')}
      ${f('next_step', 'Следующий шаг')}${f('next_step_date', 'Дата следующего шага', `<input name="next_step_date" type="date" value="${esc(v.next_step_date || '')}"${dis}>`)}
      <label class="field wide"><span>Комментарий</span><textarea name="comment"${dis}>${esc(v.comment || '')}</textarea></label>
    </div><div id="dup-warn" class="err" style="margin-top:8px"></div>
    ${l ? `<div class="fgroup" style="margin-top:12px"><h3>История этапов</h3>${l.history.map(h => `<div class="small">${fdt(h.at)} — ${esc(h.stage_name)} <span class="muted">${esc(h.user_name || 'автоматически')}</span></div>`).join('')}
      <details class="log"><summary>Журнал изменений (${l.log.length})</summary><div class="log-list">${l.log.map(logLine).join('')}</div></details></div>` : ''}
    <datalist id="dl-clients2">${S.refs.clients.map(c => `<option value="${esc(c.name)}">`).join('')}</datalist>`;
  const foot = edit ? `<button class="btn primary" data-act="save">${l ? 'Сохранить' : 'Создать лид'}</button>
    ${l && [1, 2].includes(l.stage) ? `<button class="btn next" data-act="advance">→ ${esc(ls[l.stage + 1])}</button>` : ''}
    <span class="spacer"></span>
    ${l && [2, 3, 0].includes(l.stage) ? '<button class="btn" data-act="rollback">Вернуть этап</button>' : ''}
    ${l && [1, 2, 3].includes(l.stage) ? '<button class="btn danger" data-act="refuse">Отказ</button>' : ''}
    ${l && can('delete') ? '<button class="btn danger" data-act="delete">Удалить</button>' : ''}` : '<button class="btn" data-close>Закрыть</button>';
  const m = modal({ title: l ? esc(l.company) : 'Новый лид', body, foot });
  const el = m.el;
  const comp = $('[name=company]', el);
  if (comp && edit) comp.addEventListener('change', async () => {
    try { const r = await GET('/api/leads/check' + qs({ company: comp.value })); $('#dup-warn', el).textContent = r.warnings.join('\n'); } catch (e) { /* не критично */ }
  });
  const save = async confirm => {
    const d = formData(el);
    if (!can('reassign')) delete d.owner_id;
    if (l) d.version = l.version;
    if (confirm) d.confirm = true;
    return l ? PUT('/api/leads/' + l.id, d) : POST('/api/leads', d);
  };
  $$('.modal-foot [data-act]', el).forEach(b => b.addEventListener('click', async () => {
    const act = b.dataset.act;
    try {
      if (act === 'save') {
        try { await save(false); } catch (e) {
          if (e.data && e.data.code === 'duplicate') {
            if (!await confirmBox(e.message + '\n\nВсё равно сохранить?', 'Сохранить')) return;
            await save(true);
          } else throw e;
        }
        toast('Сохранено', 'ok'); m.close(); refresh(true); return;
      }
      if (act === 'advance') { await POST(`/api/leads/${l.id}/advance`, { version: l.version }); toast('Этап изменён', 'ok'); m.close(); refresh(true); return; }
      if (act === 'rollback') { await POST(`/api/leads/${l.id}/rollback`, { version: l.version }); toast('Этап возвращён', 'ok'); m.close(); refresh(true); return; }
      if (act === 'refuse') { m.close(); leadRefuse(l); return; }
      if (act === 'delete') {
        if (!await confirmBox('Удалить лид? Администратор сможет его восстановить.', 'Удалить')) return;
        await DEL('/api/leads/' + l.id); toast('Удалено', 'ok'); m.close(); refresh(true);
      }
    } catch (e) { toast(e.message, 'error'); }
  }));
}

/* ================================================================ дневной отчёт */

async function viewReports() {
  const root = $('#view');
  const isManager = can('reports_own');
  const selUser = S.data.reportUser || (S.me.role === 'manager' || !managers().length ? S.me.id : managers()[0].id);
  const selDate = S.data.reportDate || today();
  let grid, day;
  try {
    grid = await GET('/api/reports/grid');
    if (isManager || can('reports_edit_all')) day = await GET('/api/reports/day' + qs({ date: selDate, user_id: selUser !== S.me.id ? selUser : '' }));
  } catch (e) { root.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const r = (day && day.report) || {};
  const h = day && day.hints;
  const minDate = can('reports_edit_all') ? '' : new Date(new Date(today()).getTime() - 3 * 864e5).toISOString().slice(0, 10);
  const userSel = can('reports_edit_all')
    ? `<label class="field"><span>Сотрудник</span><select id="rep-user">${S.refs.users.filter(u => ['manager', 'head'].includes(u.role)).map(u => opt(u.id, u.name, selUser)).join('')}</select></label>` : '';
  const form = day ? `<div class="card section" style="max-width:640px">
    <div class="toolbar" style="margin-bottom:10px"><h2 class="title">Отчёт за день</h2>${userSel}
      <label class="field"><span>Дата</span><input type="date" id="rep-date" value="${esc(selDate)}" max="${today()}"${minDate ? ` min="${minDate}"` : ''}></label></div>
    <div class="hints"><b>Уже видно в системе за ${fdate(day.date)}:</b> лидов создано — ${h.leads_created}, лидов переведено в «КП отправлено» — ${h.leads_kp},
      рейсов сменили этап — ${h.trip_stages}, новых заявок — ${h.trips_created}.</div>
    <form id="rep-form"><div class="fgrid">
      <label class="field"><span>Звонки новым клиентам</span><input name="calls" type="number" min="0" inputmode="numeric" value="${r.calls ?? ''}"></label>
      <label class="field"><span>КП / расчёты отправлено</span><input name="proposals" type="number" min="0" inputmode="numeric" value="${r.proposals ?? ''}"></label>
      <label class="field"><span>Новые контакты</span><input name="contacts" type="number" min="0" inputmode="numeric" value="${r.contacts ?? ''}"></label>
      <label class="field"><span>Встречи / выезды</span><input name="meetings" type="number" min="0" inputmode="numeric" value="${r.meetings ?? ''}"></label>
      <label class="field wide"><span>Чем ещё занимался <span id="cnt" class="small"></span></span><textarea name="notes" maxlength="500">${esc(r.notes || '')}</textarea></label>
    </div>
    <div style="margin-top:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <button class="btn primary"${day.can_edit ? '' : ' disabled'}>${day.report ? 'Обновить отчёт' : 'Сдать отчёт'}</button>
      ${day.report ? `<span class="small muted">Сохранён ${fdt(day.report.updated_at)}</span>` : ''}
      ${day.can_edit ? '' : '<span class="small c-red">Этот отчёт изменить нельзя (старше 3 дней)</span>'}
      <span class="small muted">Норма в день: ${S.refs.settings.norm_calls} звонков, ${S.refs.settings.norm_kp} КП</span></div></form></div>` : '';
  const cellCls = { green: 'bg-green', yellow: 'bg-yellow', red: 'bg-red', none: 'bg-none' };
  const gridHtml = `<div class="section"><h2 style="margin-bottom:6px">Отчёты за 10 рабочих дней</h2>
    <p class="small muted" style="margin:0 0 8px">В ячейке: звонки / КП. Зелёный — обе нормы (${grid.norm_calls} / ${grid.norm_kp}), жёлтый — звонков не меньше половины нормы, красный — меньше, серый — отчёт не сдан.</p>
    <div class="table-wrap"><table class="t grid"><thead><tr><th>Сотрудник</th>${grid.days.map(d => `<th class="num">${fdate(d).slice(0, 5)}</th>`).join('')}</tr></thead><tbody>
    ${grid.grid.map(g => `<tr><td class="nowrap"><b>${esc(g.name)}</b></td>${g.cells.map(c => `<td class="cell ${cellCls[c.color]}">${c.calls == null ? 'нет' : c.calls + ' / ' + c.kp}</td>`).join('')}</tr>`).join('') || `<tr><td colspan="${grid.days.length + 1}" class="empty">Нет менеджеров</td></tr>`}
    </tbody></table></div></div>`;
  const feed = `<div class="card section"><h3 style="margin-bottom:10px">Чем занимались</h3><div class="feed">${grid.feed.map(f => `<div class="item"><div class="head">${fdate(f.date)} · <b>${esc(f.user_name)}</b> · звонки ${f.calls}, КП ${f.kp}</div><div class="body">${esc(f.notes)}</div></div>`).join('') || '<div class="muted">Пока пусто</div>'}</div></div>`;
  root.innerHTML = `<div class="toolbar"><h2 class="title">Дневной отчёт</h2>${stamp()}</div>` + form + gridHtml + feed;
  const fm = $('#rep-form');
  if (fm) {
    const ta = fm.notes, cnt = $('#cnt');
    const upd = () => { cnt.textContent = `(${ta.value.length}/500)`; };
    ta.addEventListener('input', upd); upd();
    fm.addEventListener('submit', async e => {
      e.preventDefault();
      const d = formData(fm);
      d.date = $('#rep-date').value;
      if (selUser !== S.me.id) d.user_id = selUser;
      try { await PUT('/api/reports', d); toast('Отчёт сохранён', 'ok'); S.me = await GET('/api/me'); renderBanner(); viewReports(); } catch (er) { toast(er.message, 'error'); }
    });
    $('#rep-date').addEventListener('change', e => { S.data.reportDate = e.target.value; viewReports(); });
    if ($('#rep-user')) $('#rep-user').addEventListener('change', e => { S.data.reportUser = Number(e.target.value); viewReports(); });
  }
}

/* ================================================================ справочники */

async function viewDirs() {
  const root = $('#view');
  const tab = S.data.dirTab || 'clients';
  let items;
  try { items = await GET('/api/' + tab); } catch (e) { root.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const admin = can('admin');
  const isC = tab === 'clients';
  const head = isC ? '<th>Клиент</th><th>Тип</th><th class="num">Рейсов</th><th class="num">Успешных</th><th>Последний рейс</th><th>Комментарий</th>'
    : '<th>Перевозчик</th><th>Телефон</th><th>Направления</th><th>Типы машин</th><th class="num">Рейсов</th>';
  const rowsHtml = items.map(c => `<tr data-id="${c.id}">${admin ? `<td><input type="checkbox" data-sel="${c.id}"></td>` : ''}
    ${isC ? `<td><b>${esc(c.name)}</b>${c.propose_permanent ? ' <span class="tag blue">предложить перевод в постоянные</span>' : ''}</td><td>${esc(c.type)}</td><td class="num">${c.trips}</td><td class="num">${c.delivered}</td><td>${fdate(c.last_trip)}</td><td>${esc(c.comment || '')}</td>`
      : `<td><b>${esc(c.name)}</b></td><td>${esc(c.phone || '')}</td><td>${esc(c.directions || '')}</td><td>${esc(c.vehicle_types || '')}</td><td class="num">${c.trips}</td>`}
    ${admin ? '<td><button class="btn sm" data-edit>Изменить</button></td>' : ''}</tr>`).join('');
  root.innerHTML = `<div class="toolbar"><h2 class="title">Справочники</h2>
      ${admin ? `<button class="btn" id="dir-add">+ Добавить</button><button class="btn" id="dir-merge" title="Отметьте дубли галочками">Объединить отмеченные</button>` : ''}${stamp()}</div>
    <div class="subtabs"><button class="btn ${isC ? 'active' : ''}" data-tab="clients">Клиенты</button><button class="btn ${!isC ? 'active' : ''}" data-tab="carriers">Перевозчики</button></div>
    <div class="table-wrap"><table class="t"><thead><tr>${admin ? '<th></th>' : ''}${head}${admin ? '<th></th>' : ''}</tr></thead><tbody>${rowsHtml || '<tr><td colspan="9" class="empty">Пусто</td></tr>'}</tbody></table></div>
    <p class="small muted">Новые клиенты и перевозчики добавляются сами из формы рейса и при импорте. Дубли вида «Coca Cola» и «Coca Cola » склеиваются автоматически; другие (например, «Кока-Кола») объедините вручную.</p>`;
  $$('[data-tab]', root).forEach(b => b.addEventListener('click', () => { S.data.dirTab = b.dataset.tab; viewDirs(); }));
  if (!admin) return;
  const edit = c => {
    const v = c || { type: 'Разовый' };
    const m = modal({
      title: c ? 'Изменить' : 'Добавить', narrow: true,
      body: `<div class="fgrid" style="grid-template-columns:1fr">
        <label class="field"><span>Название</span><input name="name" value="${esc(v.name || '')}"></label>
        ${isC ? `<label class="field"><span>Тип</span><select name="type">${S.refs.client_types.map(t => opt(t, t, v.type)).join('')}</select></label>
          <label class="field"><span>Комментарий</span><input name="comment" value="${esc(v.comment || '')}"></label>`
        : `<label class="field"><span>Телефон</span><input name="phone" value="${esc(v.phone || '')}"></label>
          <label class="field"><span>Направления</span><input name="directions" value="${esc(v.directions || '')}"></label>
          <label class="field"><span>Типы машин</span><input name="vehicle_types" value="${esc(v.vehicle_types || '')}"></label>`}</div>`,
      foot: `<button class="btn primary" data-ok>Сохранить</button>${c ? '<button class="btn danger" data-del>Удалить</button>' : ''}<button class="btn" data-close>Отмена</button>`,
    });
    $('[data-ok]', m.el).addEventListener('click', async () => {
      try { c ? await PUT(`/api/${tab}/${c.id}`, formData(m.el)) : await POST('/api/' + tab, formData(m.el)); m.close(); toast('Сохранено', 'ok'); refresh(true); } catch (e) { toast(e.message, 'error'); }
    });
    if (c) $('[data-del]', m.el).addEventListener('click', async () => {
      if (!await confirmBox(`Удалить «${c.name}» из справочника? Рейсы останутся.`)) return;
      try { await DEL(`/api/items/${tab.slice(0, -1)}/${c.id}`); m.close(); toast('Удалено', 'ok'); refresh(true); } catch (e) { toast(e.message, 'error'); }
    });
  };
  $('#dir-add').addEventListener('click', () => edit(null));
  $$('[data-edit]', root).forEach(b => b.addEventListener('click', () => edit(items.find(x => x.id === Number(b.closest('tr').dataset.id)))));
  $('#dir-merge').addEventListener('click', () => {
    const ids = $$('[data-sel]:checked', root).map(i => Number(i.dataset.sel));
    if (ids.length < 2) return toast('Отметьте галочками 2 или больше записей', 'error');
    const sel = items.filter(x => ids.includes(x.id));
    const m = modal({
      title: 'Объединить дубли', narrow: true,
      body: `<p class="small muted" style="margin-top:0">Все рейсы перейдут на основную запись, остальные будут удалены (их можно восстановить).</p>
        <label class="field"><span>Основная запись</span><select name="target">${sel.map(x => opt(x.id, `${x.name} (${x.trips} рейс.)`)).join('')}</select></label>`,
      foot: '<button class="btn primary" data-ok>Объединить</button><button class="btn" data-close>Отмена</button>',
    });
    $('[data-ok]', m.el).addEventListener('click', async () => {
      const target = Number($('[name=target]', m.el).value);
      try { await POST(`/api/${tab}/merge`, { target_id: target, source_ids: ids.filter(i => i !== target) }); m.close(); toast('Объединено', 'ok'); refresh(true); } catch (e) { toast(e.message, 'error'); }
    });
  });
}

/* ================================================================ администрирование */

const ADMIN_TABS = [['users', 'Сотрудники'], ['settings', 'Настройки'], ['import', 'Импорт реестра'], ['backups', 'Резервные копии'], ['deleted', 'Удалённые'], ['audit', 'Журнал']];

async function viewAdmin() {
  const root = $('#view');
  const tab = S.data.adminTab || 'users';
  root.innerHTML = `<div class="toolbar"><h2 class="title">Администрирование</h2><button class="btn" id="export-all">⬇ Выгрузить все данные</button></div>
    <div class="subtabs">${ADMIN_TABS.map(([k, l]) => `<button class="btn ${k === tab ? 'active' : ''}" data-tab="${k}">${l}</button>`).join('')}</div><div id="admin-body"><div class="empty">Загрузка…</div></div>`;
  $$('[data-tab]', root).forEach(b => b.addEventListener('click', () => { S.data.adminTab = b.dataset.tab; viewAdmin(); }));
  $('#export-all').addEventListener('click', () => download('/api/export/all'));
  const body = $('#admin-body');
  try { await ({ users: adminUsers, settings: adminSettings, import: adminImport, backups: adminBackups, deleted: adminDeleted, audit: adminAudit })[tab](body); } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

async function adminUsers(body) {
  const users = await GET('/api/users');
  body.innerHTML = `<div style="margin-bottom:10px"><button class="btn primary" id="u-add">+ Сотрудник</button></div>
    <div class="table-wrap"><table class="t"><thead><tr><th>Имя</th><th>Роль</th><th>PIN</th><th>Статус</th><th></th></tr></thead><tbody>
    ${users.filter(u => !u.deleted).map(u => `<tr data-id="${u.id}"><td><b>${esc(u.name)}</b></td><td>${esc(u.role_name)}</td>
      <td>${u.has_pin ? '<span class="tag green">задан</span>' : '<span class="tag orange">не задан</span>'}</td>
      <td>${u.active ? 'активен' : '<span class="tag grey">отключён</span>'}${u.locked_until && u.locked_until > new Date().toISOString().replace('T', ' ') ? ' <span class="tag red">заблокирован</span>' : ''}</td>
      <td class="nowrap"><button class="btn sm" data-edit>Изменить</button> <button class="btn sm" data-pin>Задать PIN</button> ${u.locked_until ? '<button class="btn sm" data-unlock>Разблокировать</button>' : ''} <button class="btn sm danger" data-del>Удалить</button></td></tr>`).join('')}
    </tbody></table></div><p class="small muted">Имя сотрудника должно совпадать с колонкой «Сотрудник» в реестре 1С (например, «Жексебаев М.»).</p>`;
  const edit = u => {
    const v = u || { role: 'manager', active: 1 };
    const m = modal({
      title: u ? 'Сотрудник' : 'Новый сотрудник', narrow: true,
      body: `<div class="fgrid" style="grid-template-columns:1fr">
        <label class="field"><span>Имя (как в реестре)</span><input name="name" value="${esc(v.name || '')}"></label>
        <label class="field"><span>Роль</span><select name="role">${Object.entries(S.refs.roles).map(([k, l]) => opt(k, l, v.role)).join('')}</select></label>
        ${u ? '' : '<label class="field"><span>PIN (4–6 цифр)</span><input name="pin" inputmode="numeric" maxlength="6"></label>'}
        <label class="check"><input type="checkbox" name="active"${v.active ? ' checked' : ''}> Активен (может входить)</label></div>`,
      foot: '<button class="btn primary" data-ok>Сохранить</button><button class="btn" data-close>Отмена</button>',
    });
    $('[data-ok]', m.el).addEventListener('click', async () => {
      try { u ? await PUT('/api/users/' + u.id, formData(m.el)) : await POST('/api/users', formData(m.el)); m.close(); toast('Сохранено', 'ok'); S.refs = await GET('/api/refs'); viewAdmin(); } catch (e) { toast(e.message, 'error'); }
    });
  };
  $('#u-add').addEventListener('click', () => edit(null));
  body.addEventListener('click', async e => {
    const tr = e.target.closest('tr[data-id]');
    if (!tr) return;
    const u = users.find(x => x.id === Number(tr.dataset.id));
    if (e.target.matches('[data-edit]')) edit(u);
    if (e.target.matches('[data-pin]')) {
      const m = modal({ title: 'PIN для ' + esc(u.name), narrow: true, body: '<label class="field"><span>Новый PIN (4–6 цифр)</span><input name="pin" inputmode="numeric" maxlength="6" class="pin-input"></label><p class="small muted">Сотрудник выйдет со всех устройств.</p>', foot: '<button class="btn primary" data-ok>Сохранить</button><button class="btn" data-close>Отмена</button>' });
      $('[data-ok]', m.el).addEventListener('click', async () => { try { await POST(`/api/users/${u.id}/pin`, formData(m.el)); m.close(); toast('PIN сохранён', 'ok'); viewAdmin(); } catch (er) { toast(er.message, 'error'); } });
    }
    if (e.target.matches('[data-unlock]')) { try { await POST(`/api/users/${u.id}/unlock`); toast('Разблокирован', 'ok'); viewAdmin(); } catch (er) { toast(er.message, 'error'); } }
    if (e.target.matches('[data-del]')) {
      if (!await confirmBox(`Удалить сотрудника «${u.name}»? Его рейсы и отчёты останутся.`)) return;
      try { await DEL('/api/users/' + u.id); toast('Удалён', 'ok'); viewAdmin(); } catch (er) { toast(er.message, 'error'); }
    }
  });
}

const SETTINGS_FIELDS = [
  ['plan_trips', 'План рейсов на месяц (отдел)'], ['min_margin_pct', 'Минимальная маржа, %'],
  ['overload_threshold', 'Порог перегрузки, активных рейсов на сотрудника'], ['norm_calls', 'Норма звонков в день'], ['norm_kp', 'Норма КП в день'],
  ['rate_hours', 'Срок ставки, рабочих часов'], ['contact_hours', 'Интервал связи с водителем, часов'], ['close_days', 'Срок закрытия после выгрузки, рабочих дней'],
  ['lead_stuck_days', 'Лид «завис», дней на этапе'], ['client_dependency_pct', 'Порог зависимости от клиента, % маржи'],
];
const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

async function adminSettings(body) {
  const d = await GET('/api/settings');
  const s = d.settings;
  body.innerHTML = `<form id="set-form" class="card section"><div class="fgrid">
    ${SETTINGS_FIELDS.map(([k, l]) => `<label class="field"><span>${l} <span class="small">(по умолч. ${d.defaults[k]})</span></span><input name="${k}" type="number" min="0" step="any" value="${esc(s[k])}"></label>`).join('')}
    <label class="field"><span>Начало рабочего дня</span><input name="work_start" type="time" value="${esc(s.work_start)}"></label>
    <label class="field"><span>Конец рабочего дня</span><input name="work_end" type="time" value="${esc(s.work_end)}"></label>
    <label class="field"><span>Напоминание об отчёте с</span><input name="report_reminder" type="time" value="${esc(s.report_reminder)}"></label>
    <label class="field"><span>Папка резервных копий (можно сетевую)</span><input name="backup_dir" value="${esc(s.backup_dir)}"></label>
    <div class="field wide"><span class="small muted">Рабочие дни</span><div style="display:flex;gap:12px;flex-wrap:wrap">${WEEKDAYS.map((w, i) => `<label class="check"><input type="checkbox" data-wd="${i}"${s.work_days.includes(i) ? ' checked' : ''}>${w}</label>`).join('')}</div></div>
    </div><div style="margin-top:12px"><button class="btn primary">Сохранить настройки</button></div></form>
    <div class="card"><h3>Праздничные дни РК</h3><p class="small muted">Не считаются рабочими при расчёте сроков. По одному на строку: <code>ДД.ММ.ГГГГ название</code>.</p>
      <textarea id="hol" style="min-height:160px;font-family:monospace">${d.holidays.map(h => fdate(h.date) + (h.name ? ' ' + h.name : '')).join('\n')}</textarea>
      <div style="margin-top:10px"><button class="btn primary" id="hol-save">Сохранить праздники</button></div></div>`;
  $('#set-form').addEventListener('submit', async e => {
    e.preventDefault();
    const f = formData(e.target);
    Object.keys(f).forEach(k => { if (k.startsWith('data')) delete f[k]; });
    f.work_days = $$('[data-wd]', e.target).filter(i => i.checked).map(i => Number(i.dataset.wd));
    try { await PUT('/api/settings', f); S.refs = await GET('/api/refs'); toast('Настройки сохранены', 'ok'); } catch (er) { toast(er.message, 'error'); }
  });
  $('#hol-save').addEventListener('click', async () => {
    const items = [];
    for (const line of $('#hol').value.split('\n').map(x => x.trim()).filter(Boolean)) {
      const m = line.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})\s*(.*)$/);
      if (!m) return toast('Не понял строку: ' + line, 'error');
      items.push({ date: `${m[3]}-${m[2].padStart(2, '0')}-${m[1].padStart(2, '0')}`, name: m[4] });
    }
    try { await PUT('/api/holidays', { holidays: items }); toast('Праздники сохранены', 'ok'); viewAdmin(); } catch (er) { toast(er.message, 'error'); }
  });
}

async function adminImport(body) {
  body.innerHTML = `<div class="card" style="max-width:760px">
    <h3>Импорт старого реестра (.xlsx)</h3>
    <p class="small muted">Колонки ищутся по заголовкам, обрабатываются все листы с заголовками реестра. Строки без клиента пропускаются.
      «Доставлен» → этап «Закрыт», «В пути» → этап «В пути». Повторный импорт того же файла не создаёт дублей.</p>
    <input type="file" id="imp-file" accept=".xlsx"><div style="margin-top:10px"><button class="btn primary" id="imp-preview">Проверить файл</button></div>
    <div id="imp-result" style="margin-top:14px"></div></div>`;
  let b64 = null;
  const read = file => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(',')[1]); r.onerror = rej; r.readAsDataURL(file); });
  const show = (r, committed) => {
    const sk = r.skipped.length ? `<details ${committed ? '' : 'open'}><summary>Пропущено строк: ${r.skipped.length}</summary><div class="log-list">${r.skipped.map(s => `<div>${esc(s.sheet)}, строка ${s.row}: ${esc(s.reason)}</div>`).join('')}</div></details>` : '';
    const unk = r.unknown_employees.length && !committed ? `<div class="banner" style="margin-top:10px">Неизвестные сотрудники — отметьте, кого добавить (будут созданы неактивными, без PIN):<br>
      ${r.unknown_employees.map(n => `<label class="check" style="margin-right:12px"><input type="checkbox" data-emp="${esc(n)}" checked> ${esc(n)}</label>`).join('')}</div>` : '';
    $('#imp-result').innerHTML = `<div class="${committed ? 'hints' : ''}"><b>${committed ? 'Импорт выполнен' : 'Предпросмотр'}.</b> Листы: ${r.sheets.map(esc).join(', ')}.<br>
      Новых рейсов: <b>${r.new}</b>, обновлено: <b>${r.updated}</b>, без изменений: <b>${r.unchanged}</b>, пропущено: <b>${r.skipped.length}</b>.
      ${Object.keys(r.months).length ? '<br>По месяцам: ' + Object.entries(r.months).map(([m, n]) => `${monthTitle(m)} — ${n}`).join(', ') : ''}</div>
      ${sk}${unk}${committed ? '' : `<div style="margin-top:12px"><button class="btn primary" id="imp-commit"${r.new + r.updated + r.unknown_employees.length ? '' : ' disabled'}>Загрузить в базу</button></div>`}`;
    if (!committed && $('#imp-commit')) $('#imp-commit').addEventListener('click', async () => {
      const add = $$('[data-emp]:checked').map(i => i.dataset.emp);
      try { const rr = await POST('/api/import', { data_b64: b64, commit: true, add_employees: add }); show(rr, true); S.refs = await GET('/api/refs'); toast('Импорт выполнен', 'ok'); } catch (e) { toast(e.message, 'error'); }
    });
  };
  $('#imp-preview').addEventListener('click', async () => {
    const f = $('#imp-file').files[0];
    if (!f) return toast('Выберите файл', 'error');
    try { b64 = await read(f); show(await POST('/api/import', { data_b64: b64, commit: false, add_employees: [] }), false); } catch (e) { toast(e.message, 'error'); }
  });
}

async function adminBackups(body) {
  const d = await GET('/api/backups');
  body.innerHTML = `<div class="card" style="max-width:760px"><h3>Резервные копии</h3>
    <p class="small muted">Копия базы делается автоматически раз в сутки, хранятся 30 последних. Папка: <code>${esc(d.dir)}</code> (меняется в настройках).
    Как восстановить — в README, раздел «Восстановление».</p>
    ${d.last_error ? `<div class="banner">Ошибка последней копии: ${esc(d.last_error)}</div>` : ''}
    <button class="btn primary" id="bk-now">Сделать копию сейчас</button>
    <div class="table-wrap" style="margin-top:12px"><table class="t"><thead><tr><th>Файл</th><th class="num">Размер</th></tr></thead><tbody>
    ${d.files.map(f => `<tr><td>${esc(f.name)}</td><td class="num">${money(f.size / 1024)} КБ</td></tr>`).join('') || '<tr><td colspan="2" class="empty">Копий пока нет</td></tr>'}</tbody></table></div></div>`;
  $('#bk-now').addEventListener('click', async () => { try { const r = await POST('/api/backups'); toast('Копия сохранена: ' + r.file, 'ok'); viewAdmin(); } catch (e) { toast(e.message, 'error'); } });
}

async function adminDeleted(body) {
  const d = await GET('/api/deleted');
  const sec = (title, entity, items, label) => `<div class="card section"><h3>${title} (${items.length})</h3>${items.length ? `<div class="table-wrap" style="margin-top:8px"><table class="t"><tbody>
    ${items.map(x => `<tr><td>${label(x)}</td><td style="width:1%"><button class="btn sm" data-restore="${entity}/${x.id}">Восстановить</button></td></tr>`).join('')}</tbody></table></div>` : '<div class="muted small">нет</div>'}</div>`;
  body.innerHTML = sec('Рейсы', 'trip', d.trips, x => `${fdate(x.ship_date)} ${esc(x.client || '')} ${esc(x.origin || '')} → ${esc(x.destination || '')}`)
    + sec('Лиды', 'lead', d.leads, x => esc(x.company)) + sec('Клиенты', 'client', d.clients, x => esc(x.name))
    + sec('Перевозчики', 'carrier', d.carriers, x => esc(x.name)) + sec('Сотрудники', 'user', d.users, x => esc(x.name))
    + sec('Дневные отчёты', 'daily_report', d.daily_reports, x => `${fdate(x.date)} ${esc(x.user_name)}`);
  $$('[data-restore]', body).forEach(b => b.addEventListener('click', async () => {
    try { await POST('/api/restore/' + b.dataset.restore); toast('Восстановлено', 'ok'); viewAdmin(); } catch (e) { toast(e.message, 'error'); }
  }));
}

async function adminAudit(body) {
  const items = await GET('/api/audit?limit=500');
  const ent = { trip: 'рейс', lead: 'лид', user: 'сотрудник', client: 'клиент', carrier: 'перевозчик', daily_report: 'отчёт', settings: 'настройки', holidays: 'праздники', backup: 'копия' };
  body.innerHTML = `<div class="table-wrap" style="max-height:70vh"><table class="t"><thead><tr><th>Когда</th><th>Кто</th><th>Запись</th><th>Действие</th><th>Что изменилось</th></tr></thead><tbody>
    ${items.map(a => `<tr><td class="nowrap">${fdt(a.at)}</td><td class="nowrap">${esc(a.user_name || 'система')}</td><td class="nowrap">${esc(ent[a.entity] || a.entity)}${a.entity_id ? ' #' + a.entity_id : ''}</td>
      <td>${esc(ACTIONS[a.action] || a.action)}</td><td class="small">${changesText(a.changes)}</td></tr>`).join('')}
    </tbody></table></div><p class="small muted">Показаны последние 500 записей. Полный журнал — в «Выгрузить все данные».</p>`;
}

/* ================================================================ старт */

boot();
