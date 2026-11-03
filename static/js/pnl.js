/* ================================================================
   Раздел «P&L» (с 03.11.2026; права №127–130, пока только администратор).
   Подразделы: «Сводная» (площадки роли), «Wildberries», «OZON», «Yandex» — помесячные
   прибыли и убытки выбранного года. Порядок строк — каскад юнит-экономики
   (продажи → удержания → продвижение → к оплате → себестоимость → прибыль).
   Данные — помесячные «Итоги» юнит-экономики (GET /api/pnl/data).
   Оформление — как РНП (решение владельца 03.11.2026): карточка площадки
   (.rnp-card + .rnp-toolbar + .subtabs), шапка таблицы липнет под тулбаром
   (.rnp-head-sticky, --rnp-filters-h), колонка названий закреплена слева,
   строки окрашены оттенками площадки (--lvl-bg-*). У месяца две колонки:
   сумма полностью в рублях и её доля от продаж. Под таблицей — дашборд
   «Структура и динамика года» (плитки, каскад, рост к прошлому году,
   статьи по месяцам в рублях и долях, изменение к прошлому месяцу).
   ================================================================ */
window.PnlView = (function () {
  'use strict';

  const MF = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
  const ML = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
  // Вкладка → ключи площадок в ответе API и класс карточки/подвкладки. Порядок — как в РНП
  // (Wildberries, OZON, Yandex), «Сводная» — первой (решение владельца 03.11.2026).
  const MP_TITLE = { vb: 'Wildberries', ozon: 'Ozon', ya: 'Yandex' };
  const TABS = [
    { id: 'cross', label: 'Сводная', cls: 'cross', keys: ['vb', 'ozon', 'ya'] },
    { id: 'wb', label: 'Wildberries', cls: 'wb', keys: ['vb'] },
    { id: 'ozon', label: 'OZON', cls: 'ozon', keys: ['ozon'] },
    { id: 'ya', label: 'Yandex', cls: 'yandex', keys: ['ya'] },
  ];
  // Заголовок карточки: площадки, которые есть в ответе (у «Сводной» — площадки роли).
  const tabTitle = (t) => 'P&L по месяцам ' + t.keys.filter((k) => (D.mp || {})[k]).map((k) => MP_TITLE[k]).join(' + ');
  // Доли от выручки, которые при сложении месяцев/площадок пересчитываются через рубли.
  const PCTS = ['mp_holds', 'noart', 'commission', 'logistics', 'acquiring', 'storage', 'acceptance', 'fines', 'ads', 'cogs'];
  // Состав удержаний без разбивки (строка общих удержаний): нет значения у месяца с общей строкой — 0.
  const NOART_PARTS = ['noart_comm', 'noart_logi'];
  const noartOther = (c) => (c.noart == null ? null : c.noart - (c.noart_comm || 0) - (c.noart_logi || 0));
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
    line({ lvl: 'grp', s: shareOf('noart'), l: '− Удержания без разбивки по товарам', color: '980000', g: 'noart', head: true,
      tip: 'Удержания площадки, не привязанные к товарам (строка общих удержаний отчёта)' }),
    // Состав — разница «ИТОГО − Σ товаров» по колонкам отчёта (Яндекс 2025: вся комиссия — здесь, по товарам её нет).
    line({ lvl: 'sub', s: (c) => (c.noart == null ? null : c.noart_comm || 0), l: 'Комиссия', g: 'noart' }),
    line({ lvl: 'sub', s: (c) => (c.noart == null ? null : c.noart_logi || 0), l: 'Логистика', g: 'noart' }),
    line({ lvl: 'sub', s: noartOther, l: 'Прочие удержания', g: 'noart', tip: 'Остальные удержания без разбивки (хранение, приёмка и др.)' }),
    line({ lvl: 'grp', s: shareOf('ads'), l: '− Продвижение', color: '9900FF',
      tip: 'Реклама, оплаченная деньгами (WB — «Баланс», без промо-бонусов; Ozon — «Продвижение всего»)' }),
    { lvl: 'key', f: 'payout', s: (c) => (c.payout == null ? null : c.payout / c.rev), l: '= К оплате на р/с', kind: 'rub', color: '1E7D34',
      tip: 'Продажи − удержания − продвижение: сколько площадка перечисляет на расчётный счёт' },
    line({ lvl: 'grp', s: shareOf('cogs'), l: '− Себестоимость', color: '0000FF' }),
    { lvl: 'key', f: 'profit', s: (c) => c.gm, l: '= Прибыль', kind: 'rub', tip: 'Справа — доля от продаж, это и есть валовая маржинальность' },
    { lvl: 'ratio', f: 'gm', l: 'Маржинальность валовая, %', kind: 'pct', tip: 'Прибыль ÷ Продажи, руб' },
    { lvl: 'ratio', f: 'nm', l: 'Маржинальность чистая, %', kind: 'pct', color: '1E7D34', tip: 'Прибыль ÷ К оплате на р/с' },
  ];

  const S = { tab: null, year: null, open: { holds: true, noart: true }, scroll: 0 };
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
    NOART_PARTS.forEach((f) => { o[f] = o.noart == null ? null : cells.reduce((s, c) => s + (c[f] || 0) * c.rev, 0) / o.rev; });
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


  /* ============ Дашборд под таблицей: структура и динамика года (03.11.2026, макет v1 владельца) ============ */
  // Рубли статей месяца/суммы: удержания площадки — с удержаниями без разбивки по товарам.
  const DC = { rev: '#4A5561', holds: '#e0655a', ads: '#b98ae0', cogs: '#3a5fcf', payout: '#1E7D34', profit: '#2f9a5f' };
  const DART = [['holds', 'Удержания площадки', 'neg'], ['ads', 'Продвижение', 'neg'], ['payout', 'К оплате на р/с', 'pos'],
    ['cogs', 'Себестоимость', 'neg'], ['profit', 'Прибыль', 'pos']];
  function rubs(c) {
    if (!c) return null;
    const r = c.rev;
    const items = c.mp_holds == null ? null : c.mp_holds * r, noart = (c.noart || 0) * r;
    return { rev: r, hold_items: items, hold_noart: noart, holds: items == null ? null : items + noart,
      ads: c.ads == null ? null : c.ads * r, cogs: c.cogs == null ? null : c.cogs * r, payout: c.payout, profit: c.profit };
  }
  const growth = (a, b) => (a != null && b) ? (a / Math.abs(b) - 1) * 100 : null;
  const fmtPct1 = (v) => (v == null ? '—' : (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + '%');
  const pp = (v) => Math.abs(v).toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const CHARTS = [];
  function destroyCharts() { CHARTS.splice(0).forEach((c) => { try { c.destroy(); } catch (_) {} }); }

  function dashData() {
    const y = S.year, ms = months(y).filter((m) => !isPartial(K(y, m)));
    const comp = ms.filter((m) => comparable(y, m));
    return {
      y, ms, comp,
      cur: ms.map((m) => rubs(cellOf(K(y, m)))),
      tot: rubs(sum(ms.map((m) => cellOf(K(y, m))))),
      totC: rubs(sum(comp.map((m) => cellOf(K(y, m))))),
      totLy: rubs(sum(comp.map((m) => cellOf(K(y - 1, m))))),
      // прошлый месяц января — декабрь прошлого года (если он полный)
      prevJan: (ms[0] === 1 && !isPartial(K(y - 1, 12))) ? rubs(cellOf(K(y - 1, 12))) : null,
    };
  }

  function dashHtml(t) {
    const d = dashData();
    if (!d.ms.length || !d.tot || !d.tot.rev) return '';
    const shortM = (m) => ML[m - 1].slice(0, 3);
    const rng = (l) => (l.length ? shortM(l[0]) + '–' + shortM(l[l.length - 1]) : '—');
    const salesG = d.comp.length ? growth(d.totC.rev, d.totLy.rev) : null;
    const tile = (f, l, pol, sub) => {
      const v = d.tot[f], sh = f === 'rev' ? null : (v == null ? null : v / d.tot.rev * 100);
      const g = d.comp.length ? growth(d.totC[f], d.totLy[f]) : null;
      const good = (x) => (pol === 'neg' ? x <= 0 : x >= 0);
      const arr = g == null ? '<span class="pnl-d-muted">—</span>' : `<span class="pnl-d-g ${good(g) ? 'good' : 'bad'}">${g > 0 ? '▲' : '▼'} ${pp(g)}%</span>`;
      const vs = (f !== 'rev' && g != null && salesG != null) ? g - salesG : null;
      const vsTxt = vs == null ? '' : `<div class="pnl-d-vs ${good(vs) ? 'good' : 'bad'}">${vs > 0 ? 'быстрее' : 'медленнее'} продаж на ${pp(vs)} п.п.</div>`;
      return `<div class="pnl-d-tile"><div class="pnl-d-tl"><i style="background:${DC[f]}"></i>${l}</div>
        <div class="pnl-d-tv">${fmt(v, 'rub')} <small>₽</small></div>
        <div class="pnl-d-ts">${sh == null ? 'база — 100%' : fmt(sh / 100, 'pct') + ' продаж'} · к ${d.y - 1} ${arr}</div>${vsTxt}${sub || ''}</div>`;
    };
    const holdSub = `<div class="pnl-d-sub"><span>по товарам</span><b>${fmt(d.tot.hold_items, 'rub')}</b><span>без разбивки</span><b>${fmt(d.tot.hold_noart, 'rub')}</b></div>`;
    const tiles = tile('rev', 'Продажи', 'pos') + tile('holds', 'Удержания площадки', 'neg', holdSub) + tile('ads', 'Продвижение', 'neg')
      + tile('payout', 'К оплате на р/с', 'pos') + tile('cogs', 'Себестоимость', 'neg') + tile('profit', 'Прибыль', 'pos');
    // темп к прошлому месяцу
    let tb = '';
    [['rev', 'Продажи', 'base']].concat(DART).forEach(([f, l, pol]) => {
      tb += `<tr><td class="l"><i style="background:${DC[f]}"></i>${l}</td>`;
      d.ms.forEach((m, i) => {
        const a = d.cur[i], b = i ? (d.ms[i - 1] === m - 1 ? d.cur[i - 1] : null) : d.prevJan;
        const g = b ? growth(a[f], b[f]) : null, sg = b ? growth(a.rev, b.rev) : null;
        if (g == null) { tb += '<td class="na">—</td>'; return; }
        let cls = '';
        if (pol !== 'base' && sg != null && Math.abs(g - sg) >= 3) cls = (pol === 'neg' ? g < sg : g > sg) ? 'good' : 'bad';
        tb += `<td class="${cls}">${fmtPct1(g)}</td>`;
      });
      tb += '</tr>';
    });
    const cmpNote = !d.comp.length ? `сравнения с ${d.y - 1} нет — прошлогодних данных за эти месяцы нет`
      : `сравнение с ${d.y - 1} — по сопоставимым месяцам (${rng(d.comp)})`;
    const lg = [['holds', 'Удержания площадки'], ['ads', 'Продвижение'], ['cogs', 'Себестоимость'], ['profit', 'Прибыль']]
      .map(([f, l]) => `<span><i style="background:${DC[f]}"></i>${l}</span>`).join('');
    return `<div class="card rnp-card mp-${t.cls} pnl-d-card">
      <div class="pnl-d-h"><span class="rnp-card-title">Структура и динамика ${d.y}</span><span class="pnl-d-muted">закрытые месяцы ${rng(d.ms)} · ${cmpNote}</span></div>
      <div class="pnl-d-tiles">${tiles}</div>
      <div class="pnl-d-g2">
        <div><div class="pnl-d-ct">Из чего складывается год<span>от продаж к прибыли, ₽ и % продаж</span></div><div class="pnl-d-ch"><canvas data-ch="wf"></canvas></div></div>
        <div><div class="pnl-d-ct">Растут ли расходы вместе с продажами<span>рост к ${d.y - 1}; пунктир — рост продаж</span></div>
          ${d.comp.length ? '<div class="pnl-d-ch"><canvas data-ch="yoy"></canvas></div>' : `<div class="pnl-d-empty">Нет данных за те же месяцы ${d.y - 1} года.</div>`}</div>
      </div>
      <div class="pnl-d-lg">${lg}</div>
      <div class="pnl-d-g2">
        <div><div class="pnl-d-ct">Продажи по месяцам<span>столбец — продажи месяца, разложенные на статьи, ₽</span></div><div class="pnl-d-ch"><canvas data-ch="abs"></canvas></div></div>
        <div><div class="pnl-d-ct">Доли от продаж по месяцам<span>сколько процентов продаж уходит на каждую статью</span></div><div class="pnl-d-ch"><canvas data-ch="shr"></canvas></div></div>
      </div>
      <div class="pnl-d-ct">Изменение к прошлому месяцу<span>на сколько процентов статья выросла (+) или упала (−) против прошлого месяца</span></div>
      <div class="pnl-d-key"><span><i class="good"></i>лучше продаж — расход вырос меньше продаж или упал сильнее; прибыль и «К оплате» выросли больше продаж</span>
        <span><i class="bad"></i>хуже продаж — расход вырос больше продаж; прибыль и «К оплате» — меньше</span>
        <span><i></i>без цвета — разница с продажами меньше 3 п.п.</span></div>
      <table class="pnl-d-t"><thead><tr><th></th>${d.ms.map((m) => `<th>${MF[m - 1]}</th>`).join('')}</tr></thead><tbody>${tb}</tbody></table>
      <p class="pnl-d-note">Пример: продажи +10%, продвижение +20% — красный (реклама растёт вдвое быстрее продаж); продажи +10%, удержания +2% — зелёный.
        Удержания площадки — вместе с удержаниями без разбивки по товарам. Неполный месяц в дашборд не входит.</p>
    </div>`;
  }

  function drawDash(root) {
    if (!window.Chart) return;
    const d = dashData();
    if (!d.ms.length || !d.tot || !d.tot.rev) return;
    const cv = (k) => root.querySelector(`.pnl-d-card canvas[data-ch="${k}"]`);
    const tick = { font: { size: 10.5 }, color: '#5a6470' };
    const mono = 'ui-monospace,SFMono-Regular,Menlo,monospace', sans = '-apple-system,system-ui,sans-serif';
    const t = d.tot;
    // каскад
    const steps = [['Продажи', 0, t.rev, DC.rev], ['− Удержания', t.rev - t.holds, t.rev, DC.holds],
      ['− Продвижение', t.rev - t.holds - t.ads, t.rev - t.holds, DC.ads], ['= К оплате', 0, t.payout, DC.payout],
      ['− Себестоимость', t.payout - t.cogs, t.payout, DC.cogs], ['= Прибыль', 0, t.profit, DC.profit]];
    if (cv('wf')) CHARTS.push(new Chart(cv('wf'), { type: 'bar',
      data: { labels: steps.map((s) => s[0]), datasets: [{ data: steps.map((s) => [Math.min(s[1], s[2]), Math.max(s[1], s[2])]), backgroundColor: steps.map((s) => s[3]), borderRadius: 3, barPercentage: 0.72 }] },
      options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, animation: false, layout: { padding: { right: 150 } },
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: { x: { display: false, min: Math.min(0, t.profit) }, y: { grid: { display: false }, ticks: { ...tick, font: { size: 11.5, weight: '600' } }, border: { display: false } } } },
      plugins: [{ id: 'wl', afterDatasetsDraw(ch) { const c = ch.ctx; c.save(); c.textBaseline = 'middle';
        ch.getDatasetMeta(0).data.forEach((b, i) => { const v = steps[i][2] - steps[i][1], x = Math.max(b.x, b.base) + 6;
          c.font = '700 11.5px ' + mono; c.fillStyle = '#16191D'; c.fillText(fmt(v, 'rub'), x, b.y - 6);
          c.font = '500 10.5px ' + sans; c.fillStyle = '#6b7480'; c.fillText(fmt(v / t.rev, 'pct') + ' продаж', x, b.y + 7); }); c.restore(); } }] }));
    // рост к прошлому году
    if (cv('yoy')) {
      const yy = [['rev', 'Продажи']].concat(DART);
      const gv = yy.map(([f]) => growth(d.totC[f], d.totLy[f]));
      CHARTS.push(new Chart(cv('yoy'), { type: 'bar',
        data: { labels: yy.map((a) => a[1]), datasets: [{ data: gv, backgroundColor: yy.map(([f]) => DC[f]), borderRadius: 3, barPercentage: 0.66 }] },
        options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, animation: false, layout: { padding: { right: 70, left: 4 } },
          plugins: { legend: { display: false }, tooltip: { enabled: false } },
          scales: { x: { display: false, beginAtZero: true }, y: { grid: { display: false }, ticks: { ...tick, font: { size: 11.5, weight: '600' } }, border: { display: false } } } },
        plugins: [{ id: 'yl', afterDatasetsDraw(ch) { const c = ch.ctx; c.save();
          if (gv[0] != null) { const x0 = ch.scales.x.getPixelForValue(gv[0]); c.strokeStyle = '#4A5561'; c.setLineDash([4, 3]); c.beginPath(); c.moveTo(x0, ch.chartArea.top); c.lineTo(x0, ch.chartArea.bottom); c.stroke(); c.setLineDash([]); }
          c.textBaseline = 'middle'; c.font = '700 11.5px ' + mono; c.fillStyle = '#16191D';
          ch.getDatasetMeta(0).data.forEach((b, i) => { const v = gv[i]; if (v == null) return; const right = v >= 0;
            c.textAlign = right ? 'left' : 'right'; c.fillText(fmtPct1(v), right ? Math.max(b.x, b.base) + 6 : Math.min(b.x, b.base) - 6, b.y); }); c.restore(); } }] }));
    }
    // продажи по месяцам (рубли) и доли
    const parts = [['holds', 'Удержания площадки'], ['ads', 'Продвижение'], ['cogs', 'Себестоимость'], ['profit', 'Прибыль']];
    const labels = d.ms.map((m) => ML[m - 1].slice(0, 3));
    if (cv('abs')) CHARTS.push(new Chart(cv('abs'), { type: 'bar',
      data: { labels, datasets: parts.map(([f, l]) => ({ label: l, data: d.cur.map((c) => c[f]), backgroundColor: DC[f], borderColor: '#fff', borderWidth: { top: 1 }, borderSkipped: false, stack: 's', barPercentage: 0.78 })) },
      options: { responsive: true, maintainAspectRatio: false, animation: false, layout: { padding: { top: 84 } },
        plugins: { legend: { display: false }, tooltip: { mode: 'index', intersect: false, callbacks: { label: (it) => it.dataset.label + ': ' + fmt(it.raw, 'rub') + ' ₽' } } },
        scales: { x: { stacked: true, grid: { display: false }, ticks: tick }, y: { stacked: true, display: false } } },
      plugins: [{ id: 'tl', afterDatasetsDraw(ch) { const c = ch.ctx; c.save(); c.font = '700 10.5px ' + mono; c.fillStyle = '#16191D'; c.textBaseline = 'middle';
        ch.data.labels.forEach((_, i) => { let top = Infinity, x = 0; ch.data.datasets.forEach((ds, di) => { const el = ch.getDatasetMeta(di).data[i]; if (el && ds.data[i] > 0) { top = Math.min(top, el.y); x = el.x; } });
          if (top === Infinity) return; c.save(); c.translate(x, top - 6); c.rotate(-Math.PI / 2); c.fillText(fmt(d.cur[i].rev, 'rub'), 0, 0); c.restore(); }); c.restore(); } }] }));
    if (cv('shr')) CHARTS.push(new Chart(cv('shr'), { type: 'bar',
      data: { labels, datasets: parts.map(([f, l]) => ({ label: l, data: d.cur.map((c) => (c[f] == null ? null : c[f] / c.rev * 100)), backgroundColor: DC[f], borderColor: '#fff', borderWidth: { top: 1.5 }, borderSkipped: false, stack: 's', barPercentage: 0.78 })) },
      options: { responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false }, tooltip: { mode: 'index', intersect: false, callbacks: { label: (it) => it.dataset.label + ': ' + fmt(it.raw / 100, 'pct') } } },
        scales: { x: { stacked: true, grid: { display: false }, ticks: tick }, y: { stacked: true, display: false, min: 0, max: 100 } } },
      plugins: [{ id: 'sl', afterDatasetsDraw(ch) { const c = ch.ctx; c.save(); c.font = '600 10px ' + sans; c.fillStyle = '#fff'; c.textAlign = 'center'; c.textBaseline = 'middle';
        ch.data.datasets.forEach((ds, di) => ch.getDatasetMeta(di).data.forEach((b, i) => { const v = ds.data[i]; if (v != null && v >= 4.5) c.fillText(fmt(v / 100, 'pct').replace('%', ''), b.x, (b.y + b.base) / 2); })); c.restore(); } }] }));
  }

  function draw(root) {
    destroyCharts();
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
          <div class="rnp-card-title">${U.esc(tabTitle(t))}</div>
          <div class="pnl-hscroll">${tb.head}</div>
        </div>
        <div class="pnl-bscroll">${tb.body}</div>
      </div>${dashHtml(t)}`;
    subtabs(root);
    drawDash(root);
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
    destroyCharts();
    root.innerHTML = `<div class="card rnp-card mp-${tabDef().cls} pnl-card"><div class="rnp-toolbar"><div class="subtabs" id="pnl-subtabs"></div></div><div class="empty">${U.esc(msg)}</div></div>`;
    subtabs(root);
  }

  // Вкладки роли: функция вкладки и её площадки (у «Сводной» — обе).
  // «Сводная» — по площадкам роли (сервер отдаёт только их), нужна хотя бы одна.
  function visibleTabs() {
    return TABS.filter((t) => App.can('pnl.tab.' + t.id)
      && (t.id === 'cross' ? ['ozon', 'wb', 'ya'].some((m) => App.hasMp(m)) : App.hasMp(t.id)));
  }

  async function render(root, ctl) {
    ctl.innerHTML = '';
    const tabs = visibleTabs();
    if (!tabs.length) { root.innerHTML = '<div class="empty">Нет доступных вкладок.</div>'; return; }
    if (!tabs.some((t) => t.id === S.tab)) S.tab = tabs[0].id;
    const tab = S.tab;
    // Площадки есть в ответе «Сводной» — если он уже загружен и в нём есть площадка вкладки, второй раз не считаем.
    const crossOk = tab !== 'cross' && CROSS && tabDef().keys.every((k) => (CROSS.mp || {})[k]);
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
