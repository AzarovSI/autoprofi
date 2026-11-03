/* ================================================================
   ABC · «P&L» (тестовая вкладка, право №127 abc.tab.pnl).
   Помесячный P&L выбранного года: сводная ОЗОН + WB, ОЗОН, WB.
   Порядок строк — каскад юнит-экономики (продажи → удержания →
   продвижение → к оплате → себестоимость → прибыль). Данные — помесячные
   «Итоги» юнит-экономики (/api/abc/summary, как «Сводная v2»).
   Разметка и стили — таблица показателей юнит-экономики (.rnp-card,
   .rnp-mtbl, .rnp-vc из app.css): в каждой колонке сумма полностью
   в рублях и справа — её доля от продаж (решение владельца 03.11.2026:
   вместо изменения к прошлому месяцу).
   ================================================================ */
window.ABCPnl = (function () {
  'use strict';

  const MF = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
  const ML = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
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
    line({ b: 'm', s: shareOf('other'), l: 'Другие удержания', g: 'holds' }),
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

  const S = { year: null, open: { holds: true } };
  let panelEl = null, SUMM = null;

  const K = (y, m) => y + '-' + String(m).padStart(2, '0');
  const nf0 = (v) => Math.round(v).toLocaleString('ru-RU');
  const nf1 = (v) => v.toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  function fmt(v, kind) {
    if (v == null || isNaN(v)) return '—';
    if (kind === 'pct') return nf1(Math.abs(v) < 0.0005 ? 0 : v * 100) + '%';
    const r = Math.round(v);
    return r === 0 ? '0' : nf0(r);
  }

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
  const raw = (src, k) => ((SUMM.mp || {})[src] || {})[k] || null;
  const cellOf = (t, k) => (t === 'all' ? sum([raw('ozon', k), raw('vb', k)]) : sum([raw(t, k)]));
  const lastDay = (k) => { const [y, m] = k.split('-').map(Number); return K(y, m) + '-' + String(new Date(y, m, 0).getDate()).padStart(2, '0'); };
  // Неполный месяц: «факт по» раньше последнего дня месяца — у площадки таблицы (t), у сводной — у любой.
  function isPartial(k, t) {
    const ps = t === 'all' ? Object.values(SUMM.partial || {}) : [(SUMM.partial || {})[t]];
    return ps.some((p) => p && p[k] && p[k] < lastDay(k));
  }
  // Сопоставимый месяц: каждая площадка таблицы, что есть в этом году, была и год назад
  // (иначе рост сводной — это просто подключение второй площадки).
  function comparable(t, y, m) {
    const srcs = t === 'all' ? ['ozon', 'vb'] : [t];
    const cur = srcs.filter((s) => raw(s, K(y, m)));
    return cur.length > 0 && cur.every((s) => raw(s, K(y - 1, m)) && !isPartial(K(y - 1, m), s));
  }
  const val = (c, r) => (c == null ? null : (r.calc ? r.calc(c) : c[r.f]));

  const valStyle = (r) => (r.color ? `color:#${r.color};` : '') + (r.bold ? 'font-weight:700;' : '');
  // Ячейка: сумма и справа её доля от продаж (у строк без доли — пустое место той же ширины).
  function vc(c, r) {
    const share = r.s && c ? r.s(c) : null;
    const sh = r.s ? `<span class="rnp-vc-d abcp-sh">${share == null ? '' : fmt(share, 'pct')}</span>` : '<span class="rnp-vc-d abcp-sh"></span>';
    return `<span class="rnp-vc"><span class="rnp-vc-v" style="${valStyle(r)}">${fmt(val(c, r), r.kind)}</span>${sh}</span>`;
  }

  function years() {
    const ys = new Set();
    ['ozon', 'vb'].forEach((s) => Object.keys((SUMM.mp || {})[s] || {}).forEach((k) => ys.add(Number(k.slice(0, 4)))));
    return Array.from(ys).sort();
  }
  function months(y) {   // месяцы года с данными хоть у одной площадки
    const ms = []; for (let m = 1; m <= 12; m++) if (raw('ozon', K(y, m)) || raw('vb', K(y, m))) ms.push(m); return ms;
  }
  const range = (ms) => (!ms.length ? '—' : ms.length === 1 ? ML[ms[0] - 1]
    : ms[ms.length - 1] - ms[0] + 1 !== ms.length ? ms.length + ' мес.: ' + ms.map((m) => ML[m - 1].slice(0, 3)).join(', ')
    : ms[0] === 1 && ms[ms.length - 1] === 12 ? 'январь–декабрь' : ML[ms[0] - 1] + '–' + ML[ms[ms.length - 1] - 1]);

  function table(t, title, mpCls, note) {
    const y = S.year, ms = months(y);
    const part = (m) => isPartial(K(y, m), t);
    const closed = ms.filter((m) => !part(m));
    const comp = closed.filter((m) => comparable(t, y, m));
    const cols = ms.map((m) => cellOf(t, K(y, m)));
    const tot = sum(closed.map((m) => cellOf(t, K(y, m))));
    const ly = sum(comp.map((m) => cellOf(t, K(y - 1, m))));
    const sub = '<span class="abcp-sub"><span>руб</span><span>% продаж</span></span>';
    const head = `<tr><th class="rnp-m-name"></th>${ms.map((m) => `<th class="num rnp-wkcol${part(m) ? ' abcp-part' : ''}"><span class="rnp-wkh"><span class="rnp-wkh-d1">${y}${part(m) ? ' · неполный' : ''}</span><span class="rnp-wkh-n">${MF[m - 1]}</span>${sub}</span></th>`).join('')}`
      + `<th class="num rnp-wkcol abcp-tot"><span class="rnp-wkh"><span class="rnp-wkh-d1">${range(closed)}</span><span class="rnp-wkh-n">Итого ${y}</span>${sub}</span></th>`
      + `<th class="num rnp-wkcol abcp-ly"><span class="rnp-wkh"><span class="rnp-wkh-d1">${range(comp)}</span><span class="rnp-wkh-n">${y - 1}</span>${sub}</span></th>`
      + `<th class="rnp-m-tail"></th></tr>`;
    let body = '', last = null;
    ROWS.forEach((r) => {
      if (r.g && !r.head && !S.open[r.g]) return;
      const blk = r.b !== last ? ' rnp-t-blk' : ''; last = r.b;
      const child = r.g && !r.head;
      const name = r.head
        ? `<button class="rnp-exp-btn rnp-tgrp-toggle" type="button" data-g="${r.g}" title="Показать/скрыть состав">${S.open[r.g] ? '▾' : '▸'}</button>${esc(r.l)}`
        : esc(r.l);
      const nameCls = 'rnp-m-name' + (child ? ' rnp-t-child-name' : '') + (r.head ? ' rnp-tgrp-head is-clickable' : '');
      const nameSt = valStyle(r);
      let tr = `<tr class="${(r.bold ? 'rnp-t-total' : '') + blk}"><td class="${nameCls}"${r.head ? ` data-g="${r.g}"` : ''}${nameSt ? ` style="${nameSt}"` : ''}${r.tip ? ` title="${esc(r.tip)}"` : ''}>${name}</td>`;
      cols.forEach((c, i) => { tr += `<td class="num rnp-wkcol${part(ms[i]) ? ' abcp-part' : ''}">${vc(c, r)}</td>`; });
      tr += `<td class="num rnp-wkcol abcp-tot">${vc(tot, r)}</td><td class="num rnp-wkcol abcp-ly">${vc(ly, r)}</td>`;
      body += tr + '<td class="rnp-m-tail"></td></tr>';
    });
    return `<div class="card rnp-card ${mpCls} abcp-card"><div class="abcp-ct"><span class="rnp-card-title">${title}</span>${note ? `<span class="muted">${note}</span>` : ''}</div>
      <div class="abcp-scroll"><table class="tbl rnp-mtbl rnp-mtbl-test"><thead>${head}</thead><tbody>${body}</tbody></table></div></div>`;
  }

  function draw() {
    const root = panelEl; if (!root) return;
    const ys = years();
    const seg = `<div class="seg abcp-year">${ys.map((y) => `<button type="button" class="seg-btn${y === S.year ? ' active' : ''}" data-y="${y}">${y}</button>`).join('')}</div>`;
    const scroll = Array.from(root.querySelectorAll('.abcp-scroll')).map((e) => e.scrollLeft);
    root.innerHTML = `<div class="s2-intro"><span class="s2-test">ТЕСТ</span>P&amp;L по месяцам — по отчётам MPPROFIT за месяц (те же «Итоги», что в помесячной юнит-экономике). Период ABC справа сверху на эту вкладку не влияет.</div>
      <div class="abcp-head"><div><h3>P&amp;L ${S.year} — прибыли и убытки по месяцам</h3><p class="muted">От продаж через удержания и продвижение к «К оплате на р/с» и прибыли. В каждой колонке — сумма в рублях и справа её доля от продаж: доли расходов складываются в итоговую маржинальность, доля прибыли — это и есть валовая маржинальность. «Итого» — закрытые месяцы ${S.year} года; колонка ${S.year - 1} — те же месяцы прошлого года (подписаны над колонкой).</p></div>${seg}</div>
      ${table('all', 'Сводная · ОЗОН + Wildberries', 'mp-cross', 'рубли сложены, доли — от общей выручки')}
      ${(SUMM.mp || {}).ozon ? table('ozon', 'ОЗОН', 'mp-ozon') : ''}
      ${(SUMM.mp || {}).vb ? table('vb', 'Wildberries', 'mp-wb', 'логистика и хранение почти нулевые — индивидуальные условия WB') : ''}
      <p class="abcp-foot">Неполный месяц — бледнее, в «Итого» не входит. «Другие удержания» — остаток удержаний площадки после строк состава (бывает отрицательным: возвраты удержаний).</p>`;
    // синхронная прокрутка таблиц по горизонтали, как сетка юнит-экономики
    const sc = Array.from(root.querySelectorAll('.abcp-scroll'));
    sc.forEach((e, i) => { if (scroll[i]) e.scrollLeft = scroll[i]; });
    sc.forEach((e) => e.addEventListener('scroll', () => { sc.forEach((o) => { if (o !== e && o.scrollLeft !== e.scrollLeft) o.scrollLeft = e.scrollLeft; }); }));
    root.querySelectorAll('.abcp-year .seg-btn').forEach((b) => b.addEventListener('click', () => { S.year = Number(b.dataset.y); draw(); }));
    root.querySelectorAll('.rnp-tgrp-head[data-g]').forEach((td) => td.addEventListener('click', () => { S.open[td.dataset.g] = !S.open[td.dataset.g]; draw(); }));
  }

  function render(panel, summary) {
    panelEl = panel; SUMM = summary || {};
    const ys = years();
    if (!ys.length) { panel.innerHTML = '<div class="empty">Нет месячных данных.</div>'; return; }
    if (!S.year || !ys.includes(S.year)) S.year = ys[ys.length - 1];
    draw();
  }

  return { render };
})();
