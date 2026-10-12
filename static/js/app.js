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

  // Раздел → функция «Открыть раздел» (коды — app/permissions.py, docs/access_roles.md).
  const VIEW_RIGHTS = {
    rnp_sales: 'rnp.open', rnp: 'unit.open', prices: 'prices.open', skus: 'sales.open',
    abc: 'abc.open', warehouses: 'stock.open', catalog: 'catalog.open', users: 'users.open',
  };
  const MENU_ORDER = ['rnp_sales', 'rnp', 'prices', 'skus', 'abc', 'warehouses', 'catalog', 'users'];

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
    return !!VIEW_RIGHTS[view] && can(VIEW_RIGHTS[view]);
  }

  // --- Права роли (интерфейс только прячет; проверяет сервер) ---
  // Есть ли у пользователя функция `code`. Администратору можно всё.
  function can(code) {
    const u = API.getUser();
    return !!(u && (u.is_admin || (u.permissions || []).includes(code)));
  }
  // Площадка у роли: 'Wildberries' | 'Ozon' | 'Yandex' (или 'wb' | 'ozon' | 'yandex').
  const MP_NAMES = { wb: 'Wildberries', ozon: 'Ozon', yandex: 'Yandex', ya: 'Yandex' };
  const MP_SUFFIX = { Wildberries: 'wb', Ozon: 'ozon', Yandex: 'ya' };
  function mpName(mp) { return MP_NAMES[mp] || mp; }
  function hasMp(mp) {
    const u = API.getUser();
    return !!(u && (u.is_admin || (u.marketplaces || []).includes(mpName(mp))));
  }
  // Вкладка площадки раздела: функция вкладки и (кроме РНП и ABC) площадка роли.
  // section: 'rnp' | 'unit' | 'sales' | 'abc' | 'stock'.
  function canTab(section, mp) {
    const name = mpName(mp);
    if (!can(section + '.tab.' + MP_SUFFIX[name])) return false;
    return section === 'rnp' || section === 'abc' || hasMp(name);
  }
  function isAdmin() {
    const u = API.getUser();
    return !!(u && u.is_admin);
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

  // Пункты меню — по функции «Открыть раздел» роли (и owner-only).
  // Это удобство UI; реальная защита — на бэкенде (auth.require).
  function applyRoleVisibility() {
    document.querySelectorAll('#nav a[data-view]').forEach(a => {
      a.style.display = canSeeView(a.dataset.view) ? '' : 'none';
    });
  }
  // Перечитать права с сервера и обновить меню (после правки своей роли).
  async function refreshMe() {
    API.setUser(await API.me());
    applyRoleVisibility();
  }
  // Первый доступный раздел по порядку меню (или null).
  function firstView() {
    return MENU_ORDER.find(canSeeView) || null;
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
    // Права роли берём с сервера при каждом старте: изменённая роль подхватывается без перезахода.
    try {
      API.setUser(await API.me());
    } catch (e) {
      if (e.message === 'Не авторизовано') return;
      // Нет роли или вход для роли закрыт (403) — обратно на экран входа с причиной.
      API.clearToken();
      showLogin();
      document.getElementById('li-err').textContent = e.message;
      return;
    }
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
    // Если текущий раздел недоступен пользователю — первый доступный по меню.
    if (!canSeeView(state.view)) state.view = firstView();
    setView(state.view, true);
  }

  // ---------- навигация ----------
  function setView(view, force) {
    if (view === state.view && !force) { closeNav(); return; }
    // Защита: недоступный раздел (нет права или owner-only) нельзя открыть напрямую.
    if (!canSeeView(view)) view = firstView();
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

    if (!state.view) {
      root.innerHTML = '<div class="empty">Для вашей роли нет доступных разделов. Обратитесь к администратору.</div>';
      return;
    }
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

  return { start, state, weekSelect, setView, toast, renderView, can, hasMp, canTab, isAdmin, refreshMe };
})();

document.addEventListener('DOMContentLoaded', () => App.start());
