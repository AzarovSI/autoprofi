// Оркестратор приложения: логин, навигация, общее состояние.
const App = (() => {
  const state = {
    user: null,
    weeks: [],        // [{year, week, period_text}]
    sel: null,        // выбранная неделя {year, week}
    view: 'rnp_sales',
  };

  const titles = {
    rnp_sales: 'РНП', rnp: 'Юнит-экономика', skus: 'Продажи, шт.', abc: 'ABC', warehouses: 'Склады', prices: 'Цены', catalog: 'Товары', users: 'Пользователи',
  };

  // Разделы, доступные только администратору.
  const ADMIN_VIEWS = ['users'];

  // --- Разделы «только для владельца» (временно скрыты от всех, кроме меня) ---
  // Пока раздел «в разработке», он виден и доступен ТОЛЬКО этим e-mail'ам.
  // Когда раздел будет готов и его надо открыть всем — просто убрать
  // 'warehouses' из OWNER_VIEWS (одна строка) и пересобрать. Список
  // OWNER_EMAILS менять не нужно.
  const OWNER_EMAILS = ['azarov.si@gmail.com'];
  // 30.07.2026: «Склады» открыты всем авторизованным (убран 'warehouses').
  // Механизм owner-only оставлен для будущих разделов «в разработке».
  const OWNER_VIEWS = [];
  function isOwner() {
    const u = API.getUser();
    const em = (u && u.email || '').trim().toLowerCase();
    return !!em && OWNER_EMAILS.map(e => e.toLowerCase()).includes(em);
  }
  // true, если раздел доступен текущему пользователю (учитывая owner-only).
  function canSeeView(view) {
    if (OWNER_VIEWS.includes(view) && !isOwner()) return false;
    if (ADMIN_VIEWS.includes(view) && !isAdmin()) return false;
    return true;
  }

  function isAdmin() {
    const u = API.getUser();
    return !!(u && (u.role || '').toLowerCase() === 'admin');
  }

  // ---------- экраны ----------
  function showLogin() {
    document.getElementById('login-screen').classList.remove('hidden');
    document.getElementById('app-screen').classList.add('hidden');
  }
  function showApp() {
    document.getElementById('login-screen').classList.add('hidden');
    document.getElementById('app-screen').classList.remove('hidden');
  }

  // Скрываем пункты меню только-для-админа, если роль не admin.
  // Это удобство UI; реальная защита — на бэкенде (require_admin).
  function applyRoleVisibility() {
    const admin = isAdmin();
    document.querySelectorAll('#nav a[data-admin]').forEach(a => {
      a.style.display = admin ? '' : 'none';
    });
    // Разделы «только для владельца» (по data-owner) — скрыты от всех, кроме меня.
    const owner = isOwner();
    document.querySelectorAll('#nav a[data-owner]').forEach(a => {
      const v = a.dataset.view;
      // если раздел больше не owner-only (убран из OWNER_VIEWS) — показываем всем
      const restricted = OWNER_VIEWS.includes(v);
      a.style.display = (!restricted || owner) ? '' : 'none';
    });
  }

  // keep-alive: пока вкладка открыта, не даём прод-сэндбоксу заснуть.
  let _ka = null;
  function startKeepAlive() {
    if (_ka || !API.API_BASE) return; // пингуем только на проде (есть префикс)
    _ka = setInterval(() => {
      if (document.hidden) return; // вкладка неактивна — не пингуем
      fetch(API.API_BASE + '/health').catch(() => {});
    }, 45000); // каждые 45 секунд
  }

  // ---------- инициализация после логина ----------
  async function boot() {
    state.user = API.getUser();
    startKeepAlive();
    document.getElementById('foot-name').textContent = state.user ? state.user.name : '';
    try {
      state.weeks = await API.weeks();
    } catch (e) {
      if (e.message === 'Не авторизовано') return;
      state.weeks = [];
    }
    state.sel = state.weeks.length ? { year: state.weeks[0].year, week: state.weeks[0].week } : null;
    showApp();
    applyRoleVisibility();
    // Если текущий раздел недоступен пользователю — переводим на РНП.
    if (!canSeeView(state.view)) state.view = 'rnp_sales';
    setView(state.view, true);
  }

  // ---------- навигация ----------
  function setView(view, force) {
    if (view === state.view && !force) { closeNav(); return; }
    // Защита: недоступный раздел (админский или owner-only) нельзя открыть напрямую.
    if (!canSeeView(view)) view = 'rnp_sales';
    // Перед уходом с РНП-отчёта запоминаем позицию прокрутки окна, чтобы при
    // возврате в этот отчёт вернуться на то же место (состояние раскрытия
    // хранится в самих отчётах по маркетплейсам).
    if (Views.saveRnpScroll) Views.saveRnpScroll(state.view);
    state.view = view;
    document.querySelectorAll('#nav a').forEach(a => {
      const active = a.dataset.view === view;
      a.classList.toggle('active', active);
      if (active) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
    const t = titles[view] || '';
    document.getElementById('view-title').textContent = t;
    const crumb = document.getElementById('crumb-cur');
    if (crumb) crumb.textContent = t;
    closeNav();
    renderView();
  }

  // Токен поколения рендера: защищает от гонки, когда асинхронный рендер
  // предыдущего вида дорезолвивается ПОСЛЕ переключения на другой
  // и иначе перезаписывает чужой контент (напр. РНП поверх ABC).
  let renderGen = 0;

  function renderView() {
    const gen = ++renderGen;
    U.disposeAll();
    const host = document.getElementById('view-root');
    const ctl = document.getElementById('topbar-controls');
    ctl.innerHTML = '';

    // НА КАЖДЫЙ РЕНДЕР создаём НОВЫЙ внутренний контейнер и отдаём
    // его виду как `root`. Если пользователь переключится на другой вид
    // пока идёт асинхронная загрузка, старый вид допишет в СВОЙ
    // (уже отсоединённый) контейнер — это не попадёт на экран.
    const root = document.createElement('div');
    root.className = 'view-pane';
    root.innerHTML = '<div class="loader"><span class="spinner"></span></div>';
    host.innerHTML = '';
    host.appendChild(root);

    const fn = Views[state.view];
    if (fn) {
      Promise.resolve(fn(root, ctl, state)).catch(err => {
        // Игнорируем ошибку устаревшего (уже переключённого) рендера.
        if (gen !== renderGen) return;
        root.innerHTML = `<div class="empty">Ошибка: ${U.esc(err.message)}</div>`;
      });
    }
  }

  // ---------- селектор недели (общий контрол для нескольких вью) ----------
  function weekSelect(onChange) {
    const sel = document.createElement('select');
    state.weeks.forEach(w => {
      const o = document.createElement('option');
      o.value = w.year + '-' + w.week;
      o.textContent = `${w.period_text} (нед. ${w.week})`;
      if (state.sel && w.year === state.sel.year && w.week === state.sel.week) o.selected = true;
      sel.appendChild(o);
    });
    sel.addEventListener('change', () => {
      const [y, wk] = sel.value.split('-').map(Number);
      state.sel = { year: y, week: wk };
      onChange();
    });
    const wrap = document.createElement('div');
    wrap.className = 'ctl';
    const lbl = document.createElement('label'); lbl.textContent = 'Неделя:';
    wrap.appendChild(lbl); wrap.appendChild(sel);
    return wrap;
  }

  // ---------- тост ----------
  let toastTimer = null;
  function toast(msg, kind = 'ok') {
    let el = document.getElementById('app-toast');
    if (!el) { el = document.createElement('div'); el.id = 'app-toast'; document.body.appendChild(el); }
    el.className = 'toast ' + kind;
    el.textContent = msg;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.remove(); }, 4000);
  }

  function closeNav() {
    document.getElementById('nav').classList.remove('open');
    document.getElementById('nav-toggle').setAttribute('aria-expanded', 'false');
  }

  // ---------- события ----------
  function bind() {
    document.getElementById('login-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const email = document.getElementById('li-email').value.trim();
      const pass = document.getElementById('li-pass').value;
      const btn = document.getElementById('li-btn');
      const err = document.getElementById('li-err');
      err.textContent = '';
      btn.disabled = true; btn.textContent = 'Вход…';
      try {
        await API.login(email, pass);
        await boot();
      } catch (ex) {
        err.textContent = ex.message;
      } finally {
        btn.disabled = false; btn.textContent = 'Войти';
      }
    });

    document.getElementById('nav').addEventListener('click', (e) => {
      const a = e.target.closest('a[data-view]');
      if (a) { e.preventDefault(); setView(a.dataset.view); }
    });

    const navToggle = document.getElementById('nav-toggle');
    if (navToggle) navToggle.addEventListener('click', () => {
      const open = document.getElementById('nav').classList.toggle('open');
      navToggle.setAttribute('aria-expanded', String(open));
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && document.getElementById('nav').classList.contains('open')) {
        closeNav();
        navToggle.focus();
      }
    });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.appbar')) closeNav();
    });

    document.getElementById('logout-btn').addEventListener('click', () => {
      API.clearToken();
      showLogin();
    });

    window.addEventListener('ap:unauthorized', () => showLogin());
  }

  // ---------- старт ----------
  async function start() {
    bind();
    // Прогреваем бэкенд заранее (прод засыпает при простое), не блокируя UI.
    if (API.wakeBackend) { API.wakeBackend(); }
    if (API.getToken()) {
      await boot();
    } else {
      showLogin();
    }
  }

  return { start, state, weekSelect, setView, toast, renderView };
})();

document.addEventListener('DOMContentLoaded', () => App.start());
