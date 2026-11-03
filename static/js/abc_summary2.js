/* ================================================================
   ABC · «Сводная v2» (тестовая вкладка, право №126 abc.tab.summary_v2).
   Блоки: год к году (плитки) → выбранный месяц (плитки) → календарь
   «годы × месяцы» → куда уходит рубль продаж → ассортимент → ABC.
   ОЗОН и WB — помесячные «Итоги» юнит-экономики (/api/abc/summary),
   Яндекс — выручка и прибыль из /api/abc/data (его выплату не сверяли).
   Ассортимент и ABC — по выбранному периоду ABC (STATE.computed).
   ================================================================ */
window.ABCSummary2 = (function () {
  'use strict';

  const C = { oz: '#2f6bff', wb: '#cb11ab', ya: '#e8a200', all: '#4A5561', both: '#6b7480' };
  const P = { holds: '#e0655a', ads: '#b98ae0', cogs: '#3a5fcf', other: '#c9d0d6', profit: '#2f9a5f' };
  const MS = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек'];
  const MSL = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
  const MF = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
  const NAMES = { all: 'Итого', oz: 'ОЗОН', wb: 'Wildberries', ya: 'Яндекс' };
  // Показатели плиток и календаря. ow — «у Итого только ОЗОН + ВБ» (выплату Яндекса не сверяли).
  const METRICS = [
    { f: 'rev', l: 'Выручка', u: 'млн ₽' },
    { f: 'payout', l: 'К оплате на р/с', u: 'млн ₽', ow: true },
    { f: 'profit', l: 'Прибыль', u: 'млн ₽' },
    { f: 'gm', l: 'Маржинальность валовая', u: '%' },
    { f: 'nm', l: 'Маржинальность чистая', u: '%', ow: true },
  ];
  const PCT = { gm: 1, nm: 1, holds: 1, ads: 1, cogs: 1 };
  const NEG = { holds: 1, ads: 1, cogs: 1 };
  // Каскад «рубля продаж» снизу вверх: прибыль → себестоимость → продвижение → удержания → остаток.
  const STRUCT = [
    ['Прибыль', P.profit, (c) => c.gm],
    ['Себестоимость', P.cogs, (c) => c.cogs],
    ['Продвижение', P.ads, (c) => c.ads],
    ['Удержания площадки', P.holds, (c) => c.holds],
    ['Налог и прочее', P.other, (c) => 1 - (c.gm + c.cogs + c.ads + c.holds)],
  ];

  const S = { charts: [], month: null, mp: 'all' };   // выбранный месяц плиток и площадка календаря
  let D = null;    // данные текущей отрисовки
  let panelEl = null, ctxRef = null;

  const K = (y, m) => y + '-' + String(m).padStart(2, '0');
  const ym = (k) => k.split('-').map(Number);
  const lab = (k) => { const [y, m] = ym(k); return MS[m - 1] + ' ' + String(y).slice(2); };
  const nf1 = (v) => v.toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const nf0 = (v) => Math.round(v).toLocaleString('ru-RU');
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmtV = (v, f) => v == null || isNaN(v) ? '—' : PCT[f] ? nf1(v * 100) + '%' : nf1(v / 1e6);
  const fmtCell = (v, f) => v == null || isNaN(v) ? '—' : PCT[f] ? nf1(v * 100) : nf1(v / 1e6);
  const alpha = (hex, a) => { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; };

  // Изменение: рубли — в %, доли — в п.п.; цвет — хорошо/плохо с учётом полярности (рост расходов — красный).
  function dval(cur, base, f) {
    if (cur == null || base == null || isNaN(cur) || isNaN(base)) return null;
    if (PCT[f]) return (cur - base) * 100;
    return base ? (cur - base) / Math.abs(base) * 100 : null;
  }
  function dlt(cur, base, f, short) {
    const d = dval(cur, base, f);
    if (d == null) return '<span class="s2-d flat">—</span>';
    const a = Math.abs(d);
    const t = (PCT[f] ? nf1(a) : (a >= 10 ? nf0(a) : nf1(a))) + (short ? '' : (PCT[f] ? ' п.п.' : '%'));
    if (t === '0' || t.indexOf('0,0') === 0) return `<span class="s2-d flat">0</span>`;
    const good = NEG[f] ? d < 0 : d > 0;
    const tip = PCT[f] ? 'процентных пунктов' : '%';
    return `<span class="s2-d ${good ? 'good' : 'bad'}" title="Изменение, ${tip}">${d > 0 ? '▲' : '▼'} ${t}</span>`;
  }

  /* ============ Данные ============ */
  // M[ключ месяца] = { oz, wb, ya, ow (ОЗОН+ВБ), all (Итого) }.
  function buildData(summary, ctx) {
    const M = {};
    const put = (k, g, v) => { (M[k] || (M[k] = {}))[g] = v; };
    ['ozon', 'vb'].forEach((src) => {
      const g = src === 'ozon' ? 'oz' : 'wb';
      Object.entries((summary.mp || {})[src] || {}).forEach(([k, c]) => put(k, g, {
        rev: c.rev, profit: c.profit, payout: c.payout, holds: c.holds, ads: c.ads, cogs: c.cogs, buyout: c.buyout,
      }));
    });
    // Яндекс: Σ товаров + прибыль строки общих удержаний (как в нынешнем «Год к году»).
    const ya = {};
    (ctx.raw.yandex || []).forEach((r) => { if (!r.ym) return; const a = ya[r.ym] || (ya[r.ym] = { rev: 0, profit: 0 }); a.rev += r.revenue; a.profit += r.profit; });
    (ctx.raw.holds || []).filter((h) => h.mp === 'ya').forEach((h) => { if (ya[h.ym]) ya[h.ym].profit += h.profit; });
    Object.entries(ya).forEach(([k, v]) => { if (v.rev) put(k, 'ya', v); });
    Object.keys(M).forEach((k) => {
      const o = M[k].oz, w = M[k].wb, y = M[k].ya;
      const parts = [o, w].filter(Boolean);
      if (parts.length) {
        const rev = parts.reduce((s, x) => s + x.rev, 0);
        const wsum = (f) => { let n = 0, d = 0; parts.forEach((x) => { if (x[f] != null) { n += x[f] * x.rev; d += x.rev; } }); return d ? n / d : null; };
        const payout = parts.some((x) => x.payout == null) ? null : parts.reduce((s, x) => s + x.payout, 0);
        M[k].ow = { rev, profit: parts.reduce((s, x) => s + (x.profit || 0), 0), payout,
          holds: wsum('holds'), ads: wsum('ads'), cogs: wsum('cogs'), buyout: wsum('buyout') };
      }
      const ow = M[k].ow;
      M[k].all = {
        rev: (ow ? ow.rev : 0) + (y ? y.rev : 0), profit: (ow ? ow.profit : 0) + (y ? y.profit : 0),
        payout: ow ? ow.payout : null, owProfit: ow ? ow.profit : null,
      };
    });
    // Неполные месяцы — из журнала загрузок ABC: у какого-то МП «факт по» раньше конца месяца или без даты
    // (текущий месяц). «Факт по» последнему дню — месяц полный.
    const partial = {};
    const meta = {}; (ctx.monthMeta || []).forEach((m) => { meta[m.key] = m; });
    const mark = (k, ds) => { const end = meta[k] && meta[k].end;
      const short = ds.filter((d) => !d || (end && d < end));
      if (short.length) partial[k] = short.concat(partial[k] ? [partial[k]] : []).filter(Boolean).sort()[0] || ''; };
    Object.values(meta).forEach((m) => mark(m.key, Object.values(m.partial || {})));
    Object.values(summary.partial || {}).forEach((byKey) => Object.entries(byKey).forEach(([k, d]) => mark(k, [d])));
    const keys = Object.keys(M).filter((k) => M[k].all.rev).sort((a, b) => { const [ya1, ma] = ym(a), [yb, mb] = ym(b); return ya1 - yb || ma - mb; });
    const closed = keys.filter((k) => !(k in partial));
    return { M, keys, partial, lastClosed: closed[closed.length - 1] || keys[keys.length - 1], last: keys[keys.length - 1] };
  }

  const isPart = (k) => k in D.partial;
  const has = (k, g) => !!(D.M[k] && D.M[k][g] && !isPart(k));
  // Значение показателя за месяц (доли/маржи — из сумм).
  function mval(k, g, f) {
    const x = D.M[k] && D.M[k][g]; if (!x) return null;
    if (f === 'gm') return x.rev && x.profit != null ? x.profit / x.rev : null;
    if (f === 'nm') { const p = g === 'all' ? x.owProfit : x.profit; return x.payout > 0 && p != null ? p / x.payout : null; }
    return x[f] == null ? null : x[f];
  }
  // Сумма/отношение по набору месяцев.
  function agg(keys, g, f) {
    keys = keys.filter((k) => has(k, g)); if (!keys.length) return null;
    const sum = (ff, gg = g) => { let s = 0; for (const k of keys) { const v = (D.M[k][gg] || {})[ff]; if (v == null) return null; s += v; } return s; };
    if (f === 'gm') { const r = sum('rev'), pr = sum('profit'); return r && pr != null ? pr / r : null; }
    if (f === 'nm') { const p = sum('payout'); const pr = g === 'all' ? sum('owProfit') : sum('profit'); return p > 0 && pr != null ? pr / p : null; }
    if (PCT[f]) { let n = 0, d = 0; keys.forEach((k) => { const x = D.M[k][g]; if (x && x[f] != null) { n += x[f] * x.rev; d += x.rev; } }); return d ? n / d : null; }
    if (f === 'payout' && g === 'ya') return null;
    return sum(f);
  }
  const yearKeys = (y) => D.keys.filter((k) => ym(k)[0] === y && !isPart(k));
  // Сопоставимые месяцы года y и y−1 — есть у площадки в обоих годах (как в нынешнем «Год к году»).
  function comp(y, g, f) {
    const a = [], b = [];
    for (let m = 1; m <= 12; m++) if (has(K(y, m), g) && has(K(y - 1, m), g)) { a.push(K(y, m)); b.push(K(y - 1, m)); }
    return { cur: agg(a, g, f), base: agg(b, g, f), n: a.length, from: a[0], to: a[a.length - 1] };
  }
  function yearRange(y) {
    const ks = yearKeys(y); if (!ks.length) return '';
    const a = ym(ks[0])[1], b = ym(ks[ks.length - 1])[1];
    return (a === 1 && b === 12) ? '' : `${MSL[a - 1]}–${MSL[b - 1]}`;
  }
  const grps = (m) => m.ow ? ['oz', 'wb'] : ['oz', 'wb', 'ya'];

  /* ============ Разметка блоков ============ */
  const legend = (items) => `<div class="s2-lg">${items.map(([c, t]) => `<span><i style="background:${c}"></i>${t}</span>`).join('')}</div>`;
  const head = (title, sub, right) => `<div class="s2-h"><div><h3>${title}</h3>${sub ? `<p class="muted">${sub}</p>` : ''}</div>${right || ''}</div>`;
  const dot = (g) => `<i class="s2-dot" style="background:${C[g]}"></i>`;

  function yoyBlock() {
    const y = ym(D.lastClosed)[0];
    const years = [y - 2, y - 1, y].filter((yy) => yearKeys(yy).length);
    const ytd = comp(y, 'all', 'rev');
    const range = ytd.n ? `${MSL[ym(ytd.from)[1] - 1]}–${MSL[ym(ytd.to)[1] - 1]}` : '';
    const tiles = METRICS.map((m) => {
      const c = comp(y, 'all', m.f);
      const big = c.cur == null ? '—' : PCT[m.f] ? nf1(c.cur * 100) + '<small>%</small>' : nf0(c.cur / 1e6) + '<small>млн ₽</small>';
      const yrs = years.map((yy) => {
        const v = agg(yearKeys(yy), 'all', m.f); const r = yearRange(yy);
        const cc = comp(yy, 'all', m.f);
        return `<span class="n${yy === y ? ' cur' : ''}">${yy}${r ? ' · ' + r : ''}</span><span class="v">${v == null ? '—' : PCT[m.f] ? nf1(v * 100) + '%' : nf1(v / 1e6)}</span>${cc.n ? dlt(cc.cur, cc.base, m.f) : '<span></span>'}`;
      }).join('');
      const mp = grps(m).map((g) => { const cc = comp(y, g, m.f); if (!cc.n) return `<span class="n">${dot(g)}${NAMES[g]}</span><span class="v">—</span><span></span>`;
        return `<span class="n">${dot(g)}${NAMES[g]}</span><span class="v">${fmtV(cc.cur, m.f)}</span>${dlt(cc.cur, cc.base, m.f)}`; }).join('');
      return `<div class="s2-tile"><div class="s2-tl">${m.l}${m.ow ? ' <span class="s2-ow">· ОЗОН + ВБ</span>' : ''}</div>
        <div class="s2-tv">${big}</div><div class="s2-ts">${range} ${y} к ${range} ${y - 1} ${c.n ? dlt(c.cur, c.base, m.f) : ''}</div>
        <div class="s2-rows">${yrs}</div><div class="s2-rows">${mp}</div>
        <div class="s2-mini"><canvas data-mini="${m.f}"></canvas></div>
        <div class="s2-cap"><span>янв</span><span><i class="s2-dot" style="background:#b8c2ca"></i>${y - 1} <i class="s2-dot" style="background:#2f3a45;margin-left:6px"></i>${y}</span><span>дек</span></div></div>`;
    }).join('');
    return `<div class="card s2-card">${head('Год к году', `Крупно — ${range} ${y} к тем же месяцам ${y - 1} года. Ниже — суммы по годам и рост к сопоставимым месяцам (только месяцы, которые есть в обоих годах), площадки и помесячно ${y - 1} против ${y}. Неполный месяц в суммы не входит.`)}
      <div class="s2-t5">${tiles}</div></div>`;
  }

  function monthBlock() {
    const k = S.month;
    const [y, m] = ym(k);
    const prev = K(m === 1 ? y - 1 : y, m === 1 ? 12 : m - 1), ly = K(y - 1, m);
    const idx = D.keys.indexOf(k);
    const pnote = isPart(k) ? ` · <b class="s2-part">неполный${D.partial[k] ? ', по ' + D.partial[k].slice(8, 10) + '.' + D.partial[k].slice(5, 7) : ''}</b>` : '';
    const tiles = METRICS.map((mm) => {
      const v = mval(k, 'all', mm.f);
      const big = v == null ? '—' : PCT[mm.f] ? nf1(v * 100) + '<small>%</small>' : nf1(v / 1e6) + '<small>млн ₽</small>';
      const mp = grps(mm).map((g) => `<span class="n">${dot(g)}${NAMES[g]}</span><span class="v">${fmtV(mval(k, g, mm.f), mm.f)}</span>${dlt(mval(k, g, mm.f), mval(ly, g, mm.f), mm.f)}`).join('');
      return `<div class="s2-tile s2-tile-m"><div class="s2-tl">${mm.l}${mm.ow ? ' <span class="s2-ow">· ОЗОН + ВБ</span>' : ''}</div><div class="s2-tv">${big}</div>
        <div class="s2-tm"><span>к ${MSL[ym(prev)[1] - 1]} ${dlt(v, mval(prev, 'all', mm.f), mm.f)}</span><span>к ${MSL[m - 1]} ${String(y - 1).slice(2)} ${dlt(v, mval(ly, 'all', mm.f), mm.f)}</span></div>
        <div class="s2-rows">${mp}</div></div>`;
    }).join('');
    const sel = `<div class="s2-msel"><button type="button" data-step="-1"${idx <= 0 ? ' disabled' : ''}>‹</button><span>${MF[m - 1]} ${y}</span><button type="button" data-step="1"${idx >= D.keys.length - 1 ? ' disabled' : ''}>›</button></div>`;
    return `<div class="card s2-card">${head(`Месяц: ${MF[m - 1].toLowerCase()} ${y}${pnote}`, `К прошлому месяцу и к тому же месяцу ${y - 1} года; у площадок — к ${MSL[m - 1]} ${y - 1}.`, sel)}
      <div class="s2-t5">${tiles}</div></div>`;
  }

  const pastel = (t) => { const a = [244, 184, 181], m = [250, 238, 191], b = [183, 220, 185]; const mix = (x, y, s) => x.map((c, i) => Math.round(c + (y[i] - c) * s)); const c = t < .5 ? mix(a, m, t * 2) : mix(m, b, (t - .5) * 2); return `rgb(${c.join(',')})`; };
  function calendarBlock() {
    const g = S.mp;
    const y = ym(D.lastClosed)[0];
    const years = [y - 2, y - 1, y].filter((yy) => D.keys.some((k) => ym(k)[0] === yy && D.M[k][g]));
    const mets = METRICS.filter((m) => !(g === 'ya' && m.ow));
    let tb = '';
    mets.forEach((m) => {
      const vals = []; years.forEach((yy) => { for (let i = 1; i <= 12; i++) if (has(K(yy, i), g)) { const v = mval(K(yy, i), g, m.f); if (v != null) vals.push(v); } });
      const lo = Math.min(...vals), hi = Math.max(...vals);
      tb += `<tr class="s2-grp"><td colspan="16">${m.l}, ${m.u}${g === 'all' && m.ow ? ' <span class="s2-ow">· ОЗОН + ВБ</span>' : ''}</td></tr>`;
      years.forEach((yy) => {
        let cells = '';
        for (let i = 1; i <= 12; i++) {
          const k = K(yy, i), v = D.M[k] && D.M[k][g] ? mval(k, g, m.f) : null;
          if (v == null) cells += '<td class="na">·</td>';
          else if (isPart(k)) cells += `<td class="part" title="Неполный месяц">${fmtCell(v, m.f)}*</td>`;
          else cells += `<td style="background:${pastel(hi === lo ? .5 : (v - lo) / (hi - lo))}">${fmtCell(v, m.f)}</td>`;
        }
        const tot = agg(yearKeys(yy), g, m.f), cc = comp(yy, g, m.f), r = yearRange(yy);
        tb += `<tr${yy === y ? ' class="cur"' : ''}><td class="l">${yy}</td>${cells}<td class="tot sep">${tot == null ? '—' : fmtCell(tot, m.f)}</td><td class="cmp sep">${cc.n ? dlt(cc.cur, cc.base, m.f) : ''}</td><td class="rng">${r || 'янв–дек'}</td></tr>`;
      });
      let gr = '';
      for (let i = 1; i <= 12; i++) { const k = K(y, i); gr += (has(k, g) && has(K(y - 1, i), g)) ? `<td>${dlt(mval(k, g, m.f), mval(K(y - 1, i), g, m.f), m.f, true)}</td>` : '<td></td>'; }
      tb += `<tr class="s2-gr"><td class="l">рост ${y} к ${y - 1}</td>${gr}<td class="sep"></td><td class="sep"></td><td></td></tr>`;
    });
    const chips = `<div class="s2-chips">${['all', 'oz', 'wb', 'ya'].map((x) => `<button type="button" data-mp="${x}" class="${x === g ? 'on' : ''}">${NAMES[x]}</button>`).join('')}</div>`;
    return `<div class="card s2-card">${head('Календарь: годы × месяцы', 'Строки — годы, колонки — месяцы; справа — сумма за год и рост к сопоставимым месяцам прошлого года. Цвет — от слабого месяца к сильному внутри показателя. Маржинальность — в %, её изменение — в п.п.', chips)}
      <div class="s2-scroll"><table class="s2-cal"><thead><tr><th></th>${MS.map((x) => `<th>${x}</th>`).join('')}<th class="sep">За год</th><th class="sep">Рост к пр. году</th><th>месяцы</th></tr></thead><tbody>${tb}</tbody></table></div>
      <p class="s2-foot">* неполный месяц — без окраски, в сумму года не входит.${g === 'ya' ? ' Яндекс: к оплате и чистая маржинальность не выводятся — выплату ещё не сверяли.' : ''}</p></div>`;
  }

  function structBlock() {
    const lg = legend(STRUCT.slice().reverse().map((s) => [s[1], s[0]]));
    const col = (g, t) => `<div><div class="s2-ct">${g === 'ow' ? dot('all') : dot(g)}${t}</div><div class="s2-ch"><canvas data-struct="${g}"></canvas></div></div>`;
    return `<div class="card s2-card">${head('Куда уходит рубль продаж', 'Доли выручки, % — как каскад юнит-экономики: удержания площадки (с удержаниями без разбивки по товарам), продвижение, себестоимость, прибыль. Слева — годы, справа — 13 последних месяцев.', lg)}
      <div class="s2-g3">${col('ow', 'Итого (ОЗОН + ВБ)')}${col('oz', 'ОЗОН')}${col('wb', 'Wildberries')}</div></div>`;
  }

  function assortBlock(ctx) {
    const cr = (ctx.computed && ctx.computed.summary && ctx.computed.summary.cross) || {};
    const fc = cr.forecast || {};
    const cur = [cr.only_ozon || 0, cr.on_both || 0, cr.only_vb || 0], nxt = [fc.only_ozon || 0, fc.on_both || 0, fc.only_vb || 0];
    const bar = (a, title) => { const t = a[0] + a[1] + a[2];
      return `<div class="s2-sb"><div class="s2-sbt">${title}<b>${nf0(t)}</b></div><div class="s2-sbb">${[[a[0], C.oz], [a[1], C.both], [a[2], C.wb]].map(([v, c]) => v ? `<span style="flex:${v};background:${c}">${v}</span>` : '').join('')}</div></div>`; };
    const dd = (a, b) => { const d = b - a; return d === 0 ? '<span class="muted">0</span>' : `<span class="${d < 0 ? 's2-dn' : 's2-up'}">${d > 0 ? '+' : '−'}${Math.abs(d)}</span>`; };
    const drop = cr.on_both ? Math.round((cr.on_both - (fc.on_both || 0)) / cr.on_both * 100) : null;
    const s = (ctx.computed && ctx.computed.summary) || {};
    const ozR = (s.ozon || {}).total_revenue || 0, vbR = (s.vb || {}).total_revenue || 0, ozP = (s.ozon || {}).total_profit || 0, vbP = (s.vb || {}).total_profit || 0;
    const revOff = (fc.rev_off_ozon || 0) + (fc.rev_off_vb || 0), profOff = (fc.prof_off_ozon || 0) + (fc.prof_off_vb || 0);
    const share = (p, t) => t ? nf1(Math.abs(p) / Math.abs(t) * 100) + '%' : '—';
    const mln = (v) => nf1(v / 1e6);
    const profTxt = (v) => `<b class="${v > 0 ? 's2-dn' : v < 0 ? 's2-up' : ''}">${v > 0 ? '−' : v < 0 ? '+' : ''}${mln(Math.abs(v))}</b>`;
    return `<div class="card s2-card">${head('Ассортимент: пересечение ОЗОН × Wildberries', `Цель — развести матрицы площадок. Слева — за выбранный период ABC (${esc(ctx.periodText)}), справа — SKU по месяцам.`, legend([[C.oz, 'Только ОЗОН'], [C.both, 'На обоих'], [C.wb, 'Только ВБ']]))}
      <div class="s2-as"><div>
        ${bar(cur, 'Сейчас')}${bar(nxt, 'После плана действий <span class="badge-forecast">ПРОГНОЗ</span>')}
        <table class="s2-mini-t"><thead><tr><th></th><th>Сейчас</th><th>После плана</th><th>Изм.</th></tr></thead><tbody>
          <tr><td>Всего SKU</td><td>${cr.total_skus || 0}</td><td>${fc.total_skus || 0}</td><td>${dd(cr.total_skus || 0, fc.total_skus || 0)}</td></tr>
          <tr class="b"><td>На обоих МП</td><td>${cur[1]}</td><td>${nxt[1]}</td><td>${dd(cur[1], nxt[1])}${drop != null ? ` <span class="muted">(−${drop}%)</span>` : ''}</td></tr>
          <tr><td>Только ОЗОН</td><td>${cur[0]}</td><td>${nxt[0]}</td><td>${dd(cur[0], nxt[0])}</td></tr>
          <tr><td>Только ВБ</td><td>${cur[2]}</td><td>${nxt[2]}</td><td>${dd(cur[2], nxt[2])}</td></tr></tbody></table>
        <div class="s2-plan"><div><span>Вывести совсем</span><b class="s2-dn">${fc.removed_full || 0}</b></div><div><span>Убрать с ОЗОН</span><b>${fc.moved_off_ozon || 0}</b></div>
          <div><span>Убрать с ВБ</span><b>${fc.moved_off_vb || 0}</b></div><div><span>Без изменений</span><b>${fc.unchanged || 0}</b></div></div>
        <div class="s2-eff"><span class="muted">Эффект плана за период, млн ₽:</span> выручка <b>−${mln(revOff)}</b> <span class="muted">(−${share(revOff, ozR + vbR)})</span> · прибыль ${profTxt(profOff)} <span class="muted">(${profOff > 0 ? '−' : profOff < 0 ? '+' : ''}${share(profOff, ozP + vbP)})</span><br>
          <span class="muted">ОЗОН: −${mln(fc.rev_off_ozon || 0)} / ${profTxt(fc.prof_off_ozon || 0)} · ВБ: −${mln(fc.rev_off_vb || 0)} / ${profTxt(fc.prof_off_vb || 0)}</span></div>
      </div><div><div class="s2-ct">SKU по месяцам <span class="muted">— над столбцом доля «на обоих»</span></div><div class="s2-ch s2-ch-t"><canvas data-overlap="1"></canvas></div></div></div></div>`;
  }

  function abcBlock(ctx, recLabels) {
    const c = ctx.computed || {};
    const src = [c.ozon || [], c.vb || [], c.ya || []];
    const cats = ['A', 'B', 'C', 'D', 'Z'];
    const note = { D: 'убыток / минус', Z: 'нет продаж' };
    const rec = ['KEEP_STAR', 'KEEP', 'REVIEW', 'REMOVE_HERE', 'CONSIDER_REMOVE_HERE', 'REMOVE'];
    const cnt = (arr, f, v) => arr.filter((r) => r[f] === v).length;
    const maxR = Math.max(1, ...src.flatMap((a) => rec.map((r) => cnt(a, 'rec_code', r))));
    const rowsAbc = cats.map((k) => `<tr${k === 'D' ? ' class="dl"' : ''}><td class="l">${k}${note[k] ? ` <span class="muted">${note[k]}</span>` : ''}</td>${src.map((a) => `<td class="sep">${cnt(a, 'abc_revenue', k)}</td><td${k === 'D' ? ' class="s2-dn"' : ''}>${cnt(a, 'abc_profit', k)}</td>`).join('')}</tr>`).join('');
    const rowsRec = rec.map((r, i) => `<tr${i === 0 ? ' class="top"' : ''}><td class="l">${esc(recLabels[r] || r)}</td>${src.map((a, j) => { const v = cnt(a, 'rec_code', r);
      return `<td colspan="2" class="sep"><span class="s2-hb"><i style="width:${(v / maxR * 100).toFixed(0)}%;background:${[C.oz, C.wb, C.ya][j]}"></i></span>${v}</td>`; }).join('')}</tr>`).join('');
    return `<div class="card s2-card s2-abc">${head('ABC и рекомендации', `Число SKU за выбранный период (${esc(ctx.periodText)}). A — 80% суммы, B — следующие 15%, C — последние 5%.`)}
      <table class="s2-abc-t"><thead><tr><th></th><th colspan="2" class="g oz">ОЗОН</th><th colspan="2" class="g wb">Wildberries</th><th colspan="2" class="g ya">Яндекс</th></tr>
        <tr><th></th>${src.map(() => '<th class="sep">по выручке</th><th>по прибыли</th>').join('')}</tr></thead><tbody>${rowsAbc}${rowsRec}</tbody></table></div>`;
  }

  /* ============ Графики ============ */
  const tick = { font: { size: 10.5 }, color: '#5a6470' };
  function chart(el, cfg) { if (!el || !window.Chart) return; S.charts.push(new Chart(el, cfg)); }

  function drawMini(root) {
    const y = ym(D.lastClosed)[0];
    root.querySelectorAll('canvas[data-mini]').forEach((el) => {
      const f = el.dataset.mini;
      const ds = [[y - 1, '#b8c2ca'], [y, '#2f3a45']].map(([yy, col]) => ({ label: String(yy),
        data: MS.map((_, i) => { const k = K(yy, i + 1); return has(k, 'all') ? mval(k, 'all', f) : null; }),
        backgroundColor: col, borderRadius: 1.5, barPercentage: 0.9, categoryPercentage: 0.82 }));
      chart(el, { type: 'bar', data: { labels: MS, datasets: ds }, options: { responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false }, tooltip: { mode: 'index', intersect: false, callbacks: { label: (c) => c.dataset.label + ': ' + fmtV(c.raw, f) } } },
        scales: { x: { display: false }, y: { display: false, beginAtZero: !PCT[f] } } } });
    });
  }

  function drawStruct(root) {
    const y = ym(D.lastClosed)[0];
    const years = [y - 2, y - 1, y].filter((yy) => yearKeys(yy).length);
    const win = D.keys.slice(-13);
    root.querySelectorAll('canvas[data-struct]').forEach((el) => {
      const g = el.dataset.struct;
      const yv = (yy) => { const ks = yearKeys(yy); const gm = agg(ks, g, 'gm'); return gm == null ? null : { gm, cogs: agg(ks, g, 'cogs'), ads: agg(ks, g, 'ads'), holds: agg(ks, g, 'holds') }; };
      const mv = (k) => { const x = D.M[k] && D.M[k][g]; return x && x.rev && x.profit != null ? { gm: x.profit / x.rev, cogs: x.cogs, ads: x.ads, holds: x.holds } : null; };
      const full = (c) => c && ['gm', 'cogs', 'ads', 'holds'].every((f) => c[f] != null);
      const src = years.map(yv).concat([null]).concat(win.map(mv));
      const labels = years.map((yy) => String(yy) + (yy === y && yearRange(yy) ? '*' : '')).concat(['']).concat(win.map(lab));
      const faded = (i) => i > years.length && isPart(win[i - years.length - 1]);
      const ds = STRUCT.map(([l, col, fn]) => ({ label: l, data: src.map((c) => full(c) ? +(fn(c) * 100).toFixed(1) : null),
        backgroundColor: src.map((_, i) => faded(i) ? alpha(col, .4) : col), borderColor: '#fff', borderWidth: { top: 1.5 }, borderSkipped: false, barPercentage: 0.86, categoryPercentage: 0.9 }));
      const nY = years.length;
      const seg = { id: 'seg', afterDatasetsDraw(ch) { const c = ch.ctx; c.save(); c.font = '600 10px -apple-system,system-ui,sans-serif'; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillStyle = '#fff';
        ch.data.datasets.forEach((d, di) => { if (di === 4) return; ch.getDatasetMeta(di).data.forEach((b, i) => { const v = d.data[i]; if (v == null || v < 4.5) return; c.fillText(String(Math.round(v)), b.x, (b.y + b.base) / 2); }); });
        const sp = ch.getDatasetMeta(0).data[nY]; if (sp) { c.strokeStyle = '#d2dae4'; c.setLineDash([3, 3]); c.beginPath(); c.moveTo(sp.x, ch.chartArea.top); c.lineTo(sp.x, ch.chartArea.bottom); c.stroke(); }
        c.restore(); } };
      chart(el, { type: 'bar', data: { labels, datasets: ds }, options: { responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false }, tooltip: { mode: 'index', intersect: false, filter: (it) => it.raw != null, callbacks: { label: (it) => it.dataset.label + ': ' + nf1(it.raw) + '%' } } },
        scales: { x: { stacked: true, grid: { display: false }, ticks: { ...tick, autoSkip: false, maxRotation: 50, font: (c) => ({ size: 10.5, weight: c.index < nY ? '700' : '400' }) } }, y: { stacked: true, display: false, min: 0, max: 100 } } }, plugins: [seg] });
    });
  }

  // SKU по месяцам: товар «на площадке» в месяце, если у него есть строка месячного отчёта (как в ABC).
  function drawOverlap(root, ctx) {
    const el = root.querySelector('canvas[data-overlap]'); if (!el) return;
    const sets = {};
    const add = (rows, mp) => rows.forEach((r) => { if (!r.sku || !r.ym) return; const s = sets[r.ym] || (sets[r.ym] = { oz: new Set(), wb: new Set() }); s[mp].add(r.sku); });
    add(ctx.raw.ozon || [], 'oz'); add(ctx.raw.vb || [], 'wb');
    const win = Object.keys(sets).sort().slice(-13);
    const cnt = win.map((k) => { const s = sets[k]; let both = 0; s.oz.forEach((x) => { if (s.wb.has(x)) both++; }); return { oz: s.oz.size - both, both, wb: s.wb.size - both }; });
    const ds = [['oz', 'Только ОЗОН', C.oz], ['both', 'На обоих', C.both], ['wb', 'Только ВБ', C.wb]].map(([f, l, col]) => ({ label: l, data: cnt.map((c) => c[f]),
      backgroundColor: win.map((k) => isPart(k) ? alpha(col, .4) : col), borderColor: '#fff', borderWidth: { top: 1.5 }, borderSkipped: false, barPercentage: 0.8 }));
    const top = { id: 'top', afterDatasetsDraw(ch) { const c = ch.ctx; c.save(); c.font = '600 10.5px -apple-system,system-ui,sans-serif'; c.fillStyle = '#4A5561'; c.textAlign = 'center';
      ch.getDatasetMeta(2).data.forEach((b, i) => { const t = cnt[i].oz + cnt[i].both + cnt[i].wb; if (t) c.fillText(Math.round(cnt[i].both / t * 100) + '%', b.x, b.y - 5); }); c.restore(); } };
    chart(el, { type: 'bar', data: { labels: win.map(lab), datasets: ds }, options: { responsive: true, maintainAspectRatio: false, animation: false, layout: { padding: { top: 16 } },
      plugins: { legend: { display: false }, tooltip: { mode: 'index', intersect: false } },
      scales: { x: { stacked: true, grid: { display: false }, ticks: tick }, y: { stacked: true, grid: { color: 'rgba(20,30,46,0.06)' }, ticks: tick, border: { display: false } } } }, plugins: [top] });
  }

  /* ============ Отрисовка ============ */
  function destroy() { S.charts.forEach((c) => { try { c.destroy(); } catch (_) {} }); S.charts = []; }

  function draw() {
    const ctx = ctxRef, root = panelEl; if (!root) return;
    destroy();
    root.innerHTML = `<div class="s2-intro"><span class="s2-test">ТЕСТ</span>Сводная v2 — новая подача «Обзора». Год к году и месяцы — по отчётам MPPROFIT за месяц (как помесячная юнит-экономика); ассортимент и ABC — по периоду в календаре справа сверху.</div>`
      + yoyBlock() + monthBlock() + calendarBlock() + structBlock() + assortBlock(ctx) + abcBlock(ctx, ctx.recLabels || {});
    drawMini(root); drawStruct(root); drawOverlap(root, ctx);
    root.querySelectorAll('.s2-msel button').forEach((b) => b.addEventListener('click', () => {
      const i = D.keys.indexOf(S.month) + Number(b.dataset.step);
      if (i >= 0 && i < D.keys.length) { S.month = D.keys[i]; draw(); }
    }));
    root.querySelectorAll('.s2-chips button').forEach((b) => b.addEventListener('click', () => { S.mp = b.dataset.mp; draw(); }));
  }

  // ctx: { raw, computed, monthMeta, periodText, recLabels }
  function render(panel, summary, ctx) {
    panelEl = panel; ctxRef = ctx;
    D = buildData(summary, ctx);
    if (!D.keys.length) { destroy(); panel.innerHTML = '<div class="empty">Нет месячных данных.</div>'; return; }
    if (!S.month || D.keys.indexOf(S.month) < 0) S.month = D.lastClosed;
    draw();
  }

  return { render, destroy };
})();
