/* ================================================================
   Раздел «P&L» (с 03.11.2026; права №127–130, пока только администратор).
   Подразделы: «Сводная» (ОЗОН + WB), «Wildberries», «OZON» — помесячные
   прибыли и убытки выбранного года. Порядок строк — каскад юнит-экономики
   (продажи → удержания → продвижение → к оплате → себестоимость → прибыль).
   Данные — помесячные «Итоги» юнит-экономики (GET /api/pnl/data).
   Оформление — как РНП (решение владельца 03.11.2026): карточка площадки
   (.rnp-card + .rnp-toolbar + .subtabs), шапка таблицы липнет под тулбаром
   (.rnp-head-sticky, --rnp-filters-h), колонка названий закреплена слева,
   строки окрашены оттенками площадки (--lvl-bg-*). У месяца две колонки:
   сумма полностью в рублях и её доля от продаж.
   ================================================================ */
window.PnlView = (function () {
  'use strict';

  const MF = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
  const ML = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
  // Вкладка → ключи площадок в ответе API и класс карточки/подвкладки. Порядок — как в РНП
  // (Wildberries, OZON), «Сводная» — первой (решение владельца 03.11.2026).
  const TABS = [
    { id: 'cross', label: 'Сводная', cls: 'cross', keys: ['ozon', 'vb'], title: 'P&L по месяцам Ozon + Wildberries' },
    { id: 'wb', label: 'Wildberries', cls: 'wb', keys: ['vb'], title: 'P&L по месяцам Wildberries' },
    { id: 'ozon', label: 'OZON', cls: 'ozon', keys: ['ozon'], title: 'P&L по месяцам Ozon' },
  ];
  // Доли от выручки, которые при сложении месяцев/площадок пересчитываются через рубли.
  const PCTS = ['mp_holds', 'noart', 'commission', 'logistics', 'acquiring', 'storage', 'acceptance', 'fines', 'ads', 'cogs'];
  const SUMS = ['rev', 'profit', 'payout'];
  // «Другие удержания» — остаток удержаний после строк состава (как tOtherHolds юнит-экономики);
  // у суммы месяцев — сумма месячных остатков (sum() кладёт её в c.other).
  const other = (c) => (c.other !== undefined ? c.other : c.mp_holds == null ? null
    : c.mp_holds - ['commission', 'logistics', 'acquiring', 'storage', 'acceptance', 'fines'].reduce((a, f) => a + (c[f] || 0), 0));
  // Доля строки от продаж (s) и её рубли = доля × продажи (точно, как каскад юнит-экономики).
  const shareOf = (f) => (c) => (f === 'other' ? other(c) : c[f]);
  const rubOf = (share) => (c) => { const s = share(c); return s == null || c.rev == null ? null : s * c.rev; };
  const line = (o) => Object.assign({ calc: rubOf(o.s), kind: 'rub' }, o);
  // Строки. lvl — уровень заливки, как у узлов РНП: key — итоговые строки каскада (как «Итоги»),
  // grp — расходы-группы, sub — состав удержаний (белые), ratio — маржинальности.
  // g — сворачиваемая группа, head — её заголовок; s — доля от продаж в колонке справа от суммы.
  const ROWS = [
    { lvl: 'key', f: 'rev', l: 'Продажи, руб', kind: 'rub', s: () => null },
    line({ lvl: 'grp', s: shareOf('mp_holds'), l: '− Удержания площадки', color: 'C0392B', g: 'holds', head: true,
      tip: 'Удержания площадки по товарам, кроме рекламы' }),
    line({ lvl: 'sub', s: shareOf('commission'), l: 'Комиссия', g: 'holds' }),
    line({ lvl: 'sub', s: shareOf('logistics'), l: 'Логистика', g: 'holds' }),
    line({ lvl: 'sub', s: shareOf('acquiring'), l: 'Эквайринг', g: 'holds' }),
    line({ lvl: 'sub', s: shareOf('storage'), l: 'Хранение и прочие удержания', g: 'holds' }),
    line({ lvl: 'sub', s: shareOf('acceptance'), l: 'Приёмка', g: 'holds' }),
    line({ lvl: 'sub', s: shareOf('fines'), l: 'Штрафы', g: 'holds', color: 'D11414' }),
    line({ lvl: 'sub', s: shareOf('other'), l: 'Другие удержания', g: 'holds',
      tip: 'Остаток удержаний площадки после строк выше; бывает отрицательным (возвраты удержаний)' }),
    line({ lvl: 'grp', s: shareOf('noart'), l: '− Удержания без разбивки по товарам', color: '980000',
      tip: 'Удержания площадки, не привязанные к товарам (строка общих удержаний отчёта)' }),
    line({ lvl: 'grp', s: shareOf('ads'), l: '− Продвижение', color: '9900FF',
      tip: 'Реклама, оплаченная деньгами (WB — «Баланс», без промо-бонусов; Ozon — «Продвижение всего»)' }),
    { lvl: 'key', f: 'payout', s: (c) => (c.payout == null ? null : c.payout / c.rev), l: '= К оплате на р/с', kind: 'rub', color: '1E7D34',
      tip: 'Продажи − удержания − продвижение: сколько площадка перечисляет на расчётный счёт' },
    line({ lvl: 'grp', s: shareOf('cogs'), l: '− Себестоимость', color: '0000FF' }),
    { lvl: 'key', f: 'profit', s: (c) => c.gm, l: '= Прибыль', kind: 'rub', tip: 'Справа — доля от продаж, это и есть валовая маржинальность' },
    { lvl: 'ratio', f: 'gm', l: 'Маржинальность валовая, %', kind: 'pct', tip: 'Прибыль ÷ Продажи, руб' },
    { lvl: 'ratio', f: 'nm', l: 'Маржинальность чистая, %', kind: 'pct', color: '1E7D34', tip: 'Прибыль ÷ К оплате на р/с' },
  ];

  const S = { tab: null, year: null, open: { holds: true }, scroll: 0 };
  let D = null;       // ответ API текущей вкладки
  let CROSS = null;   // ответ «Сводной» (ОЗОН + WB) — из него же вкладки площадок
  // Сброс кэша (новые загрузки отчётов): см. API.cacheClear('/api/pnl').
  function invalidate() { CROSS = null; if (API.cacheClear) API.cacheClear('/api/pnl'); }

  const K = (y, m) => y + '-' + String(m).padStart(2, '0');
  const fmt = (v, kind) => {
    if (v == null || isNaN(v)) return '—';
    if (kind === 'pct') return (Math.abs(v) < 0.0005 ? 0 : v * 100).toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + '%';
    const r = Math.round(v);
    return r === 0 ? '0' : r.toLocaleString('ru-RU');
  };

  // Сумма ячеек (месяцы и/или площадки): рубли складываются, доли — от суммарной выручки.
  // Нет значения хоть в одной ячейке — нет и суммы (NULL ≠ 0).
  function sum(cells) {
    cells = cells.filter((c) => c && c.rev); if (!cells.length) return null;
    const o = {};
    SUMS.forEach((f) => { o[f] = cells.some((c) => c[f] == null) ? null : cells.reduce((s, c) => s + c[f], 0); });
    PCTS.forEach((f) => { o[f] = cells.some((c) => c[f] == null) ? null : cells.reduce((s, c) => s + c[f] * c.rev, 0) / o.rev; });
    // «без разбивки» есть не у каждого месяца (нет строки общих удержаний) — считаем по тем, где есть
    if (o.noart == null && cells.some((c) => c.noart != null)) o.noart = cells.reduce((s, c) => s + (c.noart || 0) * c.rev, 0) / o.rev;
    o.other = cells.some((c) => other(c) == null) ? null : cells.reduce((s, c) => s + other(c) * c.rev, 0) / o.rev;
    o.gm = o.profit != null ? o.profit / o.rev : null;
    o.nm = (o.payout > 0 && o.profit != null) ? o.profit / o.payout : null;
    return o;
  }
  const tabDef = () => TABS.find((t) => t.id === S.tab);
  const raw = (src, k) => ((D.mp || {})[src] || {})[k] || null;
  const cellOf = (k) => sum(tabDef().keys.map((s) => raw(s, k)));
  const lastDay = (k) => { const [y, m] = k.split('-').map(Number); return K(y, m) + '-' + String(new Date(y, m, 0).getDate()).padStart(2, '0'); };
  // Неполный месяц: «факт по» раньше последнего дня месяца хоть у одной площадки вкладки.
  const isPartial = (k, keys = tabDef().keys) => keys.some((s) => { const d = ((D.partial || {})[s] || {})[k]; return d && d < lastDay(k); });
  // Сопоставимый месяц: каждая площадка вкладки, что есть в этом году, была и год назад
  // (иначе рост сводной — это просто подключение второй площадки).
  function comparable(y, m) {
    const cur = tabDef().keys.filter((s) => raw(s, K(y, m)));
    return cur.length > 0 && cur.every((s) => raw(s, K(y - 1, m)) && !isPartial(K(y - 1, m), [s]));
  }
  const val = (c, r) => (c == null ? null : (r.calc ? r.calc(c) : c[r.f]));
  function years() {
    const ys = new Set();
    tabDef().keys.forEach((s) => Object.keys((D.mp || {})[s] || {}).forEach((k) => ys.add(Number(k.slice(0, 4)))));
    return Array.from(ys).sort();
  }
  function months(y) {   // месяцы года с данными хоть у одной площадки вкладки
    const ms = []; for (let m = 1; m <= 12; m++) if (tabDef().keys.some((s) => raw(s, K(y, m)))) ms.push(m); return ms;
  }
  const range = (ms) => (!ms.length ? '—' : ms.length === 1 ? ML[ms[0] - 1]
    : ms[ms.length - 1] - ms[0] + 1 !== ms.length ? ms.length + ' мес.: ' + ms.map((m) => ML[m - 1].slice(0, 3)).join(', ')
    : ms[0] === 1 && ms[ms.length - 1] === 12 ? 'январь–декабрь' : ML[ms[0] - 1] + '–' + ML[ms[ms.length - 1] - 1]);

  // Колонки: название | месяцы (сумма, %) | «Итого» (сумма, %) | прошлый год (сумма, %).
  // Одинаковый <colgroup> у шапки и тела — колонки совпадают при горизонтальной прокрутке.
  const W = { name: 300, rub: 128, pct: 64, tot: 148 };
  function colgroup(nm) {
    const c = (w) => `<col style="width:${w}px">`;
    let h = '<colgroup>' + c(W.name);
    for (let i = 0; i < nm; i++) h += c(W.rub) + c(W.pct);
    return h + c(W.tot) + c(W.pct) + c(W.tot) + c(W.pct) + '</colgroup>';
  }
  const tableW = (nm) => W.name + nm * (W.rub + W.pct) + 2 * (W.tot + W.pct);

  function tables() {
    const y = S.year, ms = months(y);
    const part = (m) => isPartial(K(y, m));
    const closed = ms.filter((m) => !part(m));
    const comp = closed.filter((m) => comparable(y, m));
    const cols = ms.map((m) => cellOf(K(y, m)));
    const tot = sum(closed.map((m) => cellOf(K(y, m))));
    const ly = sum(comp.map((m) => cellOf(K(y - 1, m))));
    const cg = colgroup(ms.length);
    const th = (cls, a, b) => `<th class="${cls}"><span class="pnl-h1">${a}</span><span class="pnl-h2">${b}</span></th>`;
    let head = '<tr><th class="pnl-name"></th>';
    ms.forEach((m) => {
      const p = part(m) ? ' pnl-part' : '';
      head += th('pnl-rub pnl-mstart' + p, MF[m - 1] + (part(m) ? '*' : ''), part(m) ? 'неполный' : 'руб') + th('pnl-pct' + p, '', '% продаж');
    });
    head += th('pnl-rub pnl-tot pnl-tstart', 'Итого ' + y, U.esc(range(closed))) + th('pnl-pct pnl-tot', '', '% продаж')
      + th('pnl-rub pnl-tot pnl-ly pnl-mstart', String(y - 1), U.esc(range(comp))) + th('pnl-pct pnl-tot pnl-ly', '', '% продаж') + '</tr>';
    let body = '';
    ROWS.forEach((r) => {
      if (r.g && !r.head && !S.open[r.g]) return;
      const st = r.color ? ` style="color:#${r.color}"` : '';
      const name = (r.head
        ? `<button class="rnp-exp-btn" type="button" data-g="${r.g}" title="Показать/скрыть состав">${S.open[r.g] ? '▾' : '▸'}</button>`
        : '<span class="exp-spacer"></span>') + `<span class="pnl-nm">${U.esc(r.l)}</span>`;
      const cell = (c, cls) => {
        const v = val(c, r), s = r.s && c ? r.s(c) : null;
        return `<td class="pnl-rub${cls}"${st}>${fmt(v, r.kind)}</td><td class="pnl-pct${cls.replace(' pnl-mstart', '').replace(' pnl-tstart', '')}">${r.s && c && s != null ? fmt(s, 'pct') : ''}</td>`;
      };
      let tr = `<tr class="pnl-r pnl-${r.lvl}${r.head ? ' pnl-head-row' : ''}"${r.head ? ` data-g="${r.g}"` : ''}><td class="pnl-name"${r.tip ? ` title="${U.esc(r.tip)}"` : ''}>${name}</td>`;
      cols.forEach((c, i) => { tr += cell(c, ' pnl-mstart' + (part(ms[i]) ? ' pnl-part' : '')); });
      tr += cell(tot, ' pnl-tot pnl-tstart') + cell(ly, ' pnl-tot pnl-ly pnl-mstart');
      body += tr + '</tr>';
    });
    const tw = ` style="width:${tableW(ms.length)}px"`;
    return {
      head: `<table class="pnl-t"${tw}>${cg}<thead>${head}</thead></table>`,
      body: `<table class="pnl-t"${tw}>${cg}<tbody>${body}</tbody></table>`,
    };
  }

  // Высота липкого тулбара → --rnp-filters-h (под ним липнет шапка таблицы), как в РНП.
  function syncToolbarH() {
    requestAnimationFrame(() => {
      const tb = document.querySelector('.pnl-card .rnp-toolbar');
      document.documentElement.style.setProperty('--rnp-filters-h', (tb ? Math.round(tb.getBoundingClientRect().height) : 0) + 'px');
    });
  }
  if (!window.__pnlResizeBound) { window.__pnlResizeBound = true; window.addEventListener('resize', () => { if (document.querySelector('.pnl-card')) syncToolbarH(); }); }

  function draw(root) {
    const t = tabDef();
    const prev = root.querySelector('.pnl-bscroll'); if (prev) S.scroll = prev.scrollLeft;
    const ys = years();
    if (!S.year || !ys.includes(S.year)) S.year = ys[ys.length - 1];
    const tb = tables();
    root.innerHTML = `
      <div class="card rnp-card mp-${t.cls} pnl-card">
        <div class="rnp-toolbar"><div class="rnp-toolbar-top"><div class="subtabs" id="pnl-subtabs"></div>
          <div class="rnp-period" id="pnl-period">
            <button class="rnp-period-field" id="pnl-period-field" type="button" title="Выбрать год">
              <svg class="rnp-period-ico" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/></svg>
              <span class="rnp-period-text">${S.year} год</span>
            </button>
            <div class="rnp-cal pnl-ycal" id="pnl-ycal" hidden></div>
          </div></div></div>
        <div class="rnp-head-sticky pnl-headwrap">
          <div class="rnp-card-title">${U.esc(t.title)}</div>
          <div class="pnl-hscroll">${tb.head}</div>
        </div>
        <div class="pnl-bscroll">${tb.body}</div>
      </div>`;
    subtabs(root);
    syncToolbarH();
    // Шапка прокручивается по горизонтали вместе с телом таблицы.
    const hs = root.querySelector('.pnl-hscroll'), bs = root.querySelector('.pnl-bscroll');
    bs.addEventListener('scroll', () => { hs.scrollLeft = bs.scrollLeft; });
    if (S.scroll) { bs.scrollLeft = S.scroll; hs.scrollLeft = bs.scrollLeft; }
    bindYear(root, ys);
    root.querySelectorAll('tr.pnl-head-row').forEach((tr) => tr.addEventListener('click', () => { S.open[tr.dataset.g] = !S.open[tr.dataset.g]; draw(root); }));
  }

  // Поле года — как поле периода «Продаж, шт.»: поповер с сеткой лет (под годом — месяцы с данными,
  // неполный год виден сразу), быстрый выбор «Текущий год» / «Прошлый год». Год один: P&L — за год.
  function bindYear(root, ys) {
    const field = root.querySelector('#pnl-period-field'), cal = root.querySelector('#pnl-ycal');
    const thisYear = new Date().getFullYear();
    const pick = (y) => { cal.hidden = true; field.classList.remove('open'); if (y !== S.year && ys.includes(y)) { S.year = y; draw(root); } };
    const short = (m) => ML[m - 1].slice(0, 3);
    function drawYears() {
      const cells = ys.map((y) => {
        const ms = months(y);
        const r = ms.length === 12 ? 'весь год' : ms.length ? short(ms[0]) + '–' + short(ms[ms.length - 1]) : '';
        return `<button type="button" class="pnl-y${y === S.year ? ' end' : ''}${y === thisYear ? ' today' : ''}" data-y="${y}"><b>${y}</b><span>${r}</span></button>`;
      }).join('');
      const pre = (y, l) => `<button type="button" class="rnp-cal-preset" data-y="${y}"${ys.includes(y) ? '' : ' disabled'}>${l}</button>`;
      cal.innerHTML = `<div class="rnp-cal-mtitle">Год</div><div class="pnl-ygrid">${cells}</div>
        <div class="rnp-cal-presets sp-mcal-presets">${pre(thisYear, 'Текущий год')}${pre(thisYear - 1, 'Прошлый год')}</div>`;
    }
    field.addEventListener('click', (e) => {
      e.stopPropagation();
      const open = cal.hidden;
      cal.hidden = !open; field.classList.toggle('open', open);
      if (open) drawYears();
    });
    cal.addEventListener('click', (e) => {
      e.stopPropagation();
      const b = e.target.closest('[data-y]');
      if (b && !b.disabled) pick(Number(b.dataset.y));
    });
  }
  // Клик мимо открытого поповера года закрывает его (один обработчик на модуль).
  if (!window.__pnlYearCloseBound) {
    window.__pnlYearCloseBound = true;
    document.addEventListener('click', (e) => {
      const cal = document.getElementById('pnl-ycal'), wrap = document.getElementById('pnl-period');
      if (cal && !cal.hidden && wrap && !wrap.contains(e.target)) { cal.hidden = true; const f = document.getElementById('pnl-period-field'); if (f) f.classList.remove('open'); }
    });
  }

  function subtabs(root) {
    const host = root.querySelector('#pnl-subtabs'); if (!host) return;
    visibleTabs().forEach((x) => {
      const b = document.createElement('button');
      b.className = 'subtab ' + x.cls + (x.id === S.tab ? ' active' : '');
      b.textContent = x.label;
      b.addEventListener('click', () => { if (x.id !== S.tab) { S.tab = x.id; S.scroll = 0; App.renderView(); } });
      host.appendChild(b);
    });
  }
  // Карточка без таблицы (нет данных / ошибка) — с подвкладками, чтобы можно было уйти на другую.
  function stub(root, msg) {
    root.innerHTML = `<div class="card rnp-card mp-${tabDef().cls} pnl-card"><div class="rnp-toolbar"><div class="subtabs" id="pnl-subtabs"></div></div><div class="empty">${U.esc(msg)}</div></div>`;
    subtabs(root);
  }

  // Вкладки роли: функция вкладки и её площадки (у «Сводной» — обе).
  function visibleTabs() {
    return TABS.filter((t) => App.can('pnl.tab.' + t.id) && (t.id === 'cross' ? App.hasMp('ozon') && App.hasMp('wb') : App.hasMp(t.id)));
  }

  async function render(root, ctl) {
    ctl.innerHTML = '';
    const tabs = visibleTabs();
    if (!tabs.length) { root.innerHTML = '<div class="empty">Нет доступных вкладок.</div>'; return; }
    if (!tabs.some((t) => t.id === S.tab)) S.tab = tabs[0].id;
    const tab = S.tab;
    // ОЗОН и WB есть в ответе «Сводной» — если он уже загружен, второй раз не считаем на сервере.
    const crossOk = tab !== 'cross' && tabs.some((t) => t.id === 'cross') && CROSS;
    let data;
    try { data = crossOk ? CROSS : await API.pnlData(tab); } catch (e) {
      if (root.isConnected && tab === S.tab) stub(root, 'Не удалось загрузить P&L: ' + ((e && e.message) || e));
      return;
    }
    if (!root.isConnected || tab !== S.tab) return;
    if (tab === 'cross') CROSS = data;
    D = data || {};
    if (!years().length) { stub(root, 'Нет месячных отчётов.'); return; }
    draw(root);
  }

  return { render, invalidate };
})();
