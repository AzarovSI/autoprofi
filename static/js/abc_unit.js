/* ================================================================
   ABC · «Юнит-экономика» (тестовая вкладка, право №127 abc.tab.unit).
   Помесячная юнит-экономика выбранного года: сводная ОЗОН + WB, ОЗОН, WB.
   Данные — помесячные «Итоги» юнит-экономики (/api/abc/summary, как
   «Сводная v2»). Разметка и стили — таблица показателей юнит-экономики
   (.rnp-card, .rnp-mtbl, .rnp-vc, .t-arrow из app.css): суммы полностью
   в рублях, расходы — рублями, под ними — доля от продаж.
   ================================================================ */
window.ABCUnit = (function () {
  'use strict';

  const MF = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
  const ML = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
  // Доли от выручки, которые при сложении месяцев/площадок пересчитываются через рубли.
  const PCTS = ['mp_holds', 'noart', 'commission', 'logistics', 'acquiring', 'storage', 'acceptance', 'fines', 'ads', 'cogs', 'buyout'];
  const SUMS = ['rev', 'orders_rub', 'qty', 'profit', 'payout'];
  const rub = (f) => (c) => (c[f] == null || c.rev == null ? null : c[f] * c.rev);
  // «Другие удержания» — остаток удержаний после строк состава (как tOtherHolds юнит-экономики);
  // у суммы месяцев — сумма месячных остатков (sum() кладёт её в c.other).
  const other = (c) => (c.other !== undefined ? c.other : c.mp_holds == null ? null
    : c.mp_holds - ['commission', 'logistics', 'acquiring', 'storage', 'acceptance', 'fines'].reduce((a, f) => a + (c[f] || 0), 0));
  // Строки: b — блок (линия над первой строкой блока), g — сворачиваемая группа, head — её заголовок,
  // pct — доля от продаж под рублёвой строкой (приглушена), pol — полярность изменения (neg — рост плохо).
  const ROWS = [
    { b: 'f', f: 'orders_rub', l: 'Заказы, руб', kind: 'rub', pol: 'pos' },
    { b: 'f', f: 'buyout', l: 'Выкуп, %', kind: 'pct', pol: 'pos', color: '96A5AF' },
    { b: 'f', f: 'qty', l: 'Продажи, шт', kind: 'qty', pol: 'pos' },
    { b: 'm', f: 'rev', l: 'Продажи, руб', kind: 'rub', pol: 'pos', bold: true },
    { b: 'm', calc: rub('mp_holds'), l: '− Удержания площадки, руб', kind: 'rub', pol: 'neg', color: 'C0392B', g: 'holds', head: true,
      tip: 'Удержания площадки по товарам, кроме рекламы' },
    { b: 'm', f: 'mp_holds', l: '% от продаж', kind: 'pct', pol: 'neg', color: 'C0392B', pct: true },
    { b: 'm', calc: rub('commission'), l: 'Комиссия, руб', kind: 'rub', pol: 'neg', g: 'holds' },
    { b: 'm', calc: rub('logistics'), l: 'Логистика, руб', kind: 'rub', pol: 'neg', g: 'holds' },
    { b: 'm', calc: rub('acquiring'), l: 'Эквайринг, руб', kind: 'rub', pol: 'neg', g: 'holds' },
    { b: 'm', calc: rub('storage'), l: 'Хранение и прочие удержания, руб', kind: 'rub', pol: 'neg', g: 'holds' },
    { b: 'm', calc: rub('acceptance'), l: 'Приёмка, руб', kind: 'rub', pol: 'neg', g: 'holds' },
    { b: 'm', calc: rub('fines'), l: 'Штрафы, руб', kind: 'rub', pol: 'neg', g: 'holds', color: 'D11414' },
    { b: 'm', calc: (c) => (other(c) == null ? null : other(c) * c.rev), l: 'Другие удержания, руб', kind: 'rub', pol: 'neg', g: 'holds' },
    { b: 'm', calc: rub('noart'), l: '− Удержания без разбивки по товарам, руб', kind: 'rub', pol: 'neg', color: '980000',
      tip: 'Удержания площадки, не привязанные к товарам (строка общих удержаний отчёта)' },
    { b: 'm', f: 'noart', l: '% от продаж', kind: 'pct', pol: 'neg', color: '980000', pct: true },
    { b: 'm', calc: rub('ads'), l: '− Продвижение, руб', kind: 'rub', pol: 'neg', color: '9900FF',
      tip: 'Реклама, оплаченная деньгами (WB — «Баланс», без промо-бонусов; Ozon — «Продвижение всего»)' },
    { b: 'm', f: 'ads', l: '% от продаж', kind: 'pct', pol: 'neg', color: '9900FF', pct: true },
    { b: 'm', f: 'payout', l: '= К оплате на р/с, руб', kind: 'rub', pol: 'pos', color: '1E7D34', bold: true,
      tip: 'Продажи − удержания − продвижение: сколько площадка перечисляет на расчётный счёт' },
    { b: 'c', calc: rub('cogs'), l: '− Себестоимость, руб', kind: 'rub', pol: 'neg', color: '0000FF' },
    { b: 'c', f: 'cogs', l: '% от продаж', kind: 'pct', pol: 'neg', color: '0000FF', pct: true },
    { b: 'c', f: 'profit', l: '= Прибыль, руб', kind: 'rub', pol: 'pos', bold: true },
    { b: 'r', f: 'gm', l: 'Маржинальность валовая, %', kind: 'pct', pol: 'pos', color: '16191D', tip: 'Прибыль ÷ Продажи, руб' },
    { b: 'r', f: 'nm', l: 'Маржинальность чистая, %', kind: 'pct', pol: 'pos', color: '1E7D34', tip: 'Прибыль ÷ К оплате на р/с' },
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

  // Изменение как в юнит-экономике (tArrowDelta): рубли/штуки — %, доли — п.п.;
  // треугольник — направление, его цвет — хорошо/плохо с учётом полярности, число чёрное, без знака.
  function delta(cur, prev, r, title) {
    if (cur == null || prev == null || isNaN(cur) || isNaN(prev)) return '<span class="rnp-vc-d t-delta flat"></span>';
    const pp = r.kind === 'pct';
    // % изменения суммы осмыслен только при одном знаке и заметной базе: у мелких и меняющих знак строк
    // («Другие удержания», штрафы с возвратами) он уходит в тысячи процентов — не показываем.
    if (!pp && (Math.abs(prev) < (r.kind === 'qty' ? 1 : 1000) || cur * prev < 0)) return '<span class="rnp-vc-d t-delta flat"></span>';
    const d = pp ? (cur - prev) * 100 : (cur - prev) / Math.abs(prev) * 100;
    const a = Math.abs(d), t = a >= 100 && !pp ? nf0(a) : nf1(a);
    if (t === '0,0' || (!pp && a > 999)) return '<span class="rnp-vc-d t-delta flat"></span>';
    const good = r.pol === 'neg' ? d < 0 : d > 0;
    return `<span class="rnp-vc-d t-delta" title="${esc(title)}, ${pp ? 'процентных пунктов' : '%'}"><span class="t-arrow ${good ? 'good' : 'bad'}">${d > 0 ? '▲' : '▼'}</span>${t}</span>`;
  }
  const valStyle = (r) => (r.color ? `color:#${r.color};` : '') + (r.bold ? 'font-weight:700;' : '');
  const vc = (v, r, d) => `<span class="rnp-vc"><span class="rnp-vc-v" style="${valStyle(r)}">${fmt(v, r.kind)}</span>${d}</span>`;

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
    const prevKey = (m) => (m === 1 ? K(y - 1, 12) : K(y, m - 1));
    const prevOf = (m) => cellOf(t, prevKey(m));
    const tot = sum(closed.map((m) => cellOf(t, K(y, m))));
    const totC = sum(comp.map((m) => cellOf(t, K(y, m))));
    const ly = sum(comp.map((m) => cellOf(t, K(y - 1, m))));
    const head = `<tr><th class="rnp-m-name"></th>${ms.map((m) => `<th class="num rnp-wkcol${part(m) ? ' abcu-part' : ''}"><span class="rnp-wkh"><span class="rnp-wkh-d1">${y}${part(m) ? ' · неполный' : ''}</span><span class="rnp-wkh-n">${MF[m - 1]}</span></span></th>`).join('')}`
      + `<th class="num rnp-wkcol abcu-tot"><span class="rnp-wkh"><span class="rnp-wkh-d1">${range(closed)}</span><span class="rnp-wkh-n">Итого ${y}</span></span></th>`
      + `<th class="num rnp-wkcol abcu-ly"><span class="rnp-wkh"><span class="rnp-wkh-d1">${range(comp)}</span><span class="rnp-wkh-n">${y - 1}</span></span></th>`
      + `<th class="rnp-m-tail"></th></tr>`;
    let body = '', last = null;
    ROWS.forEach((r) => {
      if (r.g && !r.head && !S.open[r.g]) return;
      const blk = r.b !== last ? ' rnp-t-blk' : ''; last = r.b;
      const child = (r.g && !r.head) || r.pct;
      const name = r.head
        ? `<button class="rnp-exp-btn rnp-tgrp-toggle" type="button" data-g="${r.g}" title="Показать/скрыть состав">${S.open[r.g] ? '▾' : '▸'}</button>${esc(r.l)}`
        : (r.pct ? `<span class="abcu-dim">${esc(r.l)}</span>` : esc(r.l));
      const nameCls = 'rnp-m-name' + (child ? ' rnp-t-child-name' : '') + (r.head ? ' rnp-tgrp-head is-clickable' : '');
      const nameSt = valStyle(r);
      let tr = `<tr class="${(r.bold ? 'rnp-t-total' : '') + blk + (r.pct ? ' abcu-pct' : '')}"><td class="${nameCls}"${r.head ? ` data-g="${r.g}"` : ''}${nameSt ? ` style="${nameSt}"` : ''}${r.tip ? ` title="${esc(r.tip)}"` : ''}>${name}</td>`;
      cols.forEach((c, i) => {
        const m = ms[i];
        const noMoM = part(m) || isPartial(prevKey(m), t);
        tr += `<td class="num rnp-wkcol${part(m) ? ' abcu-part' : ''}">${vc(val(c, r), r, noMoM ? delta(null, null, r) : delta(val(c, r), val(prevOf(m), r), r, 'Изменение к прошлому месяцу'))}</td>`;
      });
      tr += `<td class="num rnp-wkcol abcu-tot">${vc(val(tot, r), r, delta(val(totC, r), val(ly, r), r, `Изменение к ${y - 1} году по сопоставимым месяцам`))}</td>`;
      tr += `<td class="num rnp-wkcol abcu-ly"><span class="rnp-vc"><span class="rnp-vc-v">${fmt(val(ly, r), r.kind)}</span><span class="rnp-vc-d t-delta flat"></span></span></td>`;
      body += tr + '<td class="rnp-m-tail"></td></tr>';
    });
    return `<div class="card rnp-card ${mpCls} abcu-card"><div class="abcu-ct"><span class="rnp-card-title">${title}</span>${note ? `<span class="muted">${note}</span>` : ''}</div>
      <div class="abcu-scroll"><table class="tbl rnp-mtbl rnp-mtbl-test"><thead>${head}</thead><tbody>${body}</tbody></table></div></div>`;
  }

  function draw() {
    const root = panelEl; if (!root) return;
    const ys = years();
    const seg = `<div class="seg abcu-year">${ys.map((y) => `<button type="button" class="seg-btn${y === S.year ? ' active' : ''}" data-y="${y}">${y}</button>`).join('')}</div>`;
    const scroll = Array.from(root.querySelectorAll('.abcu-scroll')).map((e) => e.scrollLeft);
    root.innerHTML = `<div class="s2-intro"><span class="s2-test">ТЕСТ</span>Юнит-экономика по месяцам — по отчётам MPPROFIT за месяц (те же «Итоги», что в помесячной юнит-экономике). Период ABC справа сверху на эту вкладку не влияет.</div>
      <div class="abcu-head"><div><h3>Юнит-экономика ${S.year}</h3><p class="muted">От продаж через удержания и продвижение к «К оплате на р/с» и прибыли. Суммы — в рублях; под расходом — его доля от продаж. Рядом с числом — изменение к прошлому месяцу. «Итого» — закрытые месяцы ${S.year} года; его изменение и колонка ${S.year - 1} — по сопоставимым месяцам (они подписаны над колонкой).</p></div>${seg}</div>
      ${table('all', 'Сводная · ОЗОН + Wildberries', 'mp-cross', 'рубли сложены, доли — от общей выручки')}
      ${(SUMM.mp || {}).ozon ? table('ozon', 'ОЗОН', 'mp-ozon') : ''}
      ${(SUMM.mp || {}).vb ? table('vb', 'Wildberries', 'mp-wb', 'логистика и хранение почти нулевые — индивидуальные условия WB') : ''}
      <p class="abcu-foot">Изменение: рубли и штуки — в %, доли и маржинальность — в процентных пунктах; треугольник — направление, цвет — хорошо или плохо (рост расходов — красный). Неполный месяц — бледнее, в «Итого» не входит.</p>`;
    // синхронная прокрутка таблиц по горизонтали, как сетка юнит-экономики
    const sc = Array.from(root.querySelectorAll('.abcu-scroll'));
    sc.forEach((e, i) => { if (scroll[i]) e.scrollLeft = scroll[i]; });
    sc.forEach((e) => e.addEventListener('scroll', () => { sc.forEach((o) => { if (o !== e && o.scrollLeft !== e.scrollLeft) o.scrollLeft = e.scrollLeft; }); }));
    root.querySelectorAll('.abcu-year .seg-btn').forEach((b) => b.addEventListener('click', () => { S.year = Number(b.dataset.y); draw(); }));
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
