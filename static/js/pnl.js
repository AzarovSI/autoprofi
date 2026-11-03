/* ================================================================
   Раздел «P&L» (с 03.11.2026; права №127–130, пока только администратор).
   Подразделы: «Сводная» (ОЗОН + WB), «ОЗОН», «Wildberries» — помесячные
   прибыли и убытки выбранного года. Порядок строк — каскад юнит-экономики
   (продажи → удержания → продвижение → к оплате → себестоимость → прибыль).
   Данные — помесячные «Итоги» юнит-экономики (GET /api/pnl/data).
   Разметка — карточка и таблица показателей юнит-экономики (.rnp-card,
   .rnp-toolbar, .subtabs, .rnp-mtbl, .rnp-vc из app.css): в каждой колонке
   сумма полностью в рублях и справа — её доля от продаж.
   ================================================================ */
window.PnlView = (function () {
  'use strict';

  const MF = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
  const ML = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
  // Вкладка → ключи площадок в ответе API, класс карточки и подвкладки (как в юнит-экономике).
  const TABS = [
    { id: 'cross', label: 'Сводная', cls: 'cross', keys: ['ozon', 'vb'], title: 'Сводная · ОЗОН + Wildberries', note: 'рубли сложены, доли — от общей выручки' },
    { id: 'ozon', label: 'OZON', cls: 'ozon', keys: ['ozon'], title: 'ОЗОН' },
    { id: 'wb', label: 'Wildberries', cls: 'wb', keys: ['vb'], title: 'Wildberries', note: 'логистика и хранение почти нулевые — индивидуальные условия WB' },
  ];
  // Доли от выручки, которые при сложении месяцев/площадок пересчитываются через рубли.
  const PCTS = ['mp_holds', 'noart', 'commission', 'logistics', 'acquiring', 'storage', 'acceptance', 'fines', 'ads', 'cogs'];
  const SUMS = ['rev', 'orders_rub', 'qty', 'profit', 'payout'];
  // «Другие удержания» — остаток удержаний после строк состава (как tOtherHolds юнит-экономики);
  // у суммы месяцев — сумма месячных остатков (sum() кладёт её в c.other).
  const other = (c) => (c.other !== undefined ? c.other : c.mp_holds == null ? null
    : c.mp_holds - ['commission', 'logistics', 'acquiring', 'storage', 'acceptance', 'fines'].reduce((a, f) => a + (c[f] || 0), 0));
  // Доля строки от продаж (s) и её рубли = доля × продажи (точно, как каскад юнит-экономики).
  const shareOf = (f) => (c) => (f === 'other' ? other(c) : c[f]);
  const rubOf = (share) => (c) => { const s = share(c); return s == null || c.rev == null ? null : s * c.rev; };
  const line = (o) => Object.assign({ calc: rubOf(o.s), kind: 'rub' }, o);
  // Строки: b — блок (линия над первой строкой блока), g — сворачиваемая группа, head — её заголовок,
  // s — доля от продаж в колонке справа от суммы.
  const ROWS = [
    { b: 'f', f: 'orders_rub', l: 'Заказы, руб', kind: 'rub' },
    { b: 'f', f: 'qty', l: 'Продажи, шт', kind: 'qty' },
    { b: 'm', f: 'rev', l: 'Продажи, руб', kind: 'rub', bold: true },
    line({ b: 'm', s: shareOf('mp_holds'), l: '− Удержания площадки', color: 'C0392B', g: 'holds', head: true,
      tip: 'Удержания площадки по товарам, кроме рекламы' }),
    line({ b: 'm', s: shareOf('commission'), l: 'Комиссия', g: 'holds' }),
    line({ b: 'm', s: shareOf('logistics'), l: 'Логистика', g: 'holds' }),
    line({ b: 'm', s: shareOf('acquiring'), l: 'Эквайринг', g: 'holds' }),
    line({ b: 'm', s: shareOf('storage'), l: 'Хранение и прочие удержания', g: 'holds' }),
    line({ b: 'm', s: shareOf('acceptance'), l: 'Приёмка', g: 'holds' }),
    line({ b: 'm', s: shareOf('fines'), l: 'Штрафы', g: 'holds', color: 'D11414' }),
    line({ b: 'm', s: shareOf('other'), l: 'Другие удержания', g: 'holds',
      tip: 'Остаток удержаний площадки после строк выше; бывает отрицательным (возвраты удержаний)' }),
    line({ b: 'm', s: shareOf('noart'), l: '− Удержания без разбивки по товарам', color: '980000',
      tip: 'Удержания площадки, не привязанные к товарам (строка общих удержаний отчёта)' }),
    line({ b: 'm', s: shareOf('ads'), l: '− Продвижение', color: '9900FF',
      tip: 'Реклама, оплаченная деньгами (WB — «Баланс», без промо-бонусов; Ozon — «Продвижение всего»)' }),
    { b: 'm', f: 'payout', s: (c) => (c.payout == null ? null : c.payout / c.rev), l: '= К оплате на р/с', kind: 'rub', color: '1E7D34', bold: true,
      tip: 'Продажи − удержания − продвижение: сколько площадка перечисляет на расчётный счёт' },
    line({ b: 'c', s: shareOf('cogs'), l: '− Себестоимость', color: '0000FF' }),
    { b: 'c', f: 'profit', s: (c) => c.gm, l: '= Прибыль', kind: 'rub', bold: true, tip: 'Справа — доля от продаж, это и есть валовая маржинальность' },
    { b: 'r', f: 'gm', l: 'Маржинальность валовая, %', kind: 'pct', color: '16191D', tip: 'Прибыль ÷ Продажи, руб' },
    { b: 'r', f: 'nm', l: 'Маржинальность чистая, %', kind: 'pct', color: '1E7D34', tip: 'Прибыль ÷ К оплате на р/с' },
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

  // Сумма ячеек (месяцы и/или площадки): рубли и штуки складываются, доли — от суммарной выручки.
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
  const valStyle = (r) => (r.color ? `color:#${r.color};` : '') + (r.bold ? 'font-weight:700;' : '');
  // Ячейка: сумма и справа её доля от продаж (у строк без доли — пустое место той же ширины).
  function vc(c, r) {
    const share = r.s && c ? r.s(c) : null;
    return `<span class="rnp-vc"><span class="rnp-vc-v" style="${valStyle(r)}">${fmt(val(c, r), r.kind)}</span>`
      + `<span class="rnp-vc-d pnl-sh">${share == null ? '' : fmt(share, 'pct')}</span></span>`;
  }
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

  function tableHtml() {
    const y = S.year, ms = months(y);
    const part = (m) => isPartial(K(y, m));
    const closed = ms.filter((m) => !part(m));
    const comp = closed.filter((m) => comparable(y, m));
    const cols = ms.map((m) => cellOf(K(y, m)));
    const tot = sum(closed.map((m) => cellOf(K(y, m))));
    const ly = sum(comp.map((m) => cellOf(K(y - 1, m))));
    const sub = '<span class="pnl-sub"><span>руб</span><span>% продаж</span></span>';
    const hcol = (cls, d1, n) => `<th class="num rnp-wkcol${cls}"><span class="rnp-wkh"><span class="rnp-wkh-d1">${d1}</span><span class="rnp-wkh-n">${n}</span>${sub}</span></th>`;
    const head = `<tr><th class="rnp-m-name"></th>${ms.map((m) => hcol(part(m) ? ' pnl-part' : '', y + (part(m) ? ' · неполный' : ''), MF[m - 1])).join('')}`
      + hcol(' pnl-tot', range(closed), 'Итого ' + y) + hcol(' pnl-ly', range(comp), String(y - 1)) + '<th class="rnp-m-tail"></th></tr>';
    let body = '', last = null;
    ROWS.forEach((r) => {
      if (r.g && !r.head && !S.open[r.g]) return;
      const blk = r.b !== last ? ' rnp-t-blk' : ''; last = r.b;
      const name = r.head
        ? `<button class="rnp-exp-btn rnp-tgrp-toggle" type="button" data-g="${r.g}" title="Показать/скрыть состав">${S.open[r.g] ? '▾' : '▸'}</button>${U.esc(r.l)}`
        : U.esc(r.l);
      const nameCls = 'rnp-m-name' + (r.g && !r.head ? ' rnp-t-child-name' : '') + (r.head ? ' rnp-tgrp-head is-clickable' : '');
      const st = valStyle(r);
      let tr = `<tr class="${(r.bold ? 'rnp-t-total' : '') + blk}"><td class="${nameCls}"${r.head ? ` data-g="${r.g}"` : ''}${st ? ` style="${st}"` : ''}${r.tip ? ` title="${U.esc(r.tip)}"` : ''}>${name}</td>`;
      cols.forEach((c, i) => { tr += `<td class="num rnp-wkcol${part(ms[i]) ? ' pnl-part' : ''}">${vc(c, r)}</td>`; });
      tr += `<td class="num rnp-wkcol pnl-tot">${vc(tot, r)}</td><td class="num rnp-wkcol pnl-ly">${vc(ly, r)}</td>`;
      body += tr + '<td class="rnp-m-tail"></td></tr>';
    });
    return `<table class="tbl rnp-mtbl rnp-mtbl-test"><thead>${head}</thead><tbody>${body}</tbody></table>`;
  }

  function draw(root) {
    const t = tabDef();
    const sc = root.querySelector('.pnl-scroll'); if (sc) S.scroll = sc.scrollLeft;
    const ys = years();
    if (!S.year || !ys.includes(S.year)) S.year = ys[ys.length - 1];
    root.innerHTML = `
      <div class="card rnp-card mp-${t.cls} pnl-card">
        <div class="rnp-toolbar"><div class="rnp-toolbar-top"><div class="subtabs" id="pnl-subtabs"></div>
          <div class="seg pnl-year">${ys.map((y) => `<button type="button" class="seg-btn${y === S.year ? ' active' : ''}" data-y="${y}">${y}</button>`).join('')}</div></div></div>
        <div class="pnl-ct"><span class="rnp-card-title">P&amp;L ${S.year} · ${U.esc(t.title)}</span>${t.note ? `<span class="muted">${U.esc(t.note)}</span>` : ''}</div>
        <p class="pnl-desc">От продаж через удержания и продвижение к «К оплате на р/с» и прибыли — по отчётам MPPROFIT за месяц (те же «Итоги», что в помесячной юнит-экономике). В каждой колонке — сумма в рублях и справа её доля от продаж: доли расходов складываются в итоговую маржинальность, доля прибыли — это и есть валовая маржинальность. «Итого» — закрытые месяцы ${S.year} года; колонка ${S.year - 1} — те же месяцы прошлого года (подписаны над колонкой). Неполный месяц — бледнее, в «Итого» не входит.</p>
        <div class="pnl-scroll">${tableHtml()}</div>
      </div>`;
    subtabs(root);
    const scroll = root.querySelector('.pnl-scroll'); if (S.scroll) scroll.scrollLeft = S.scroll;
    root.querySelectorAll('.pnl-year .seg-btn').forEach((b) => b.addEventListener('click', () => { S.year = Number(b.dataset.y); draw(root); }));
    root.querySelectorAll('.rnp-tgrp-head[data-g]').forEach((td) => td.addEventListener('click', () => { S.open[td.dataset.g] = !S.open[td.dataset.g]; draw(root); }));
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
