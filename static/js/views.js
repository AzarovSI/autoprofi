// Экраны дашборда. Каждый: Views[name](root, ctl, state) -> рендерит в root, контролы в ctl.
const Views = (() => {

  // ============================================================
  // helpers для построения общих контролов «диапазон недель»
  // ============================================================
  function periodControl(state, current, onChange) {
    // current: {mode:'last'|'range', last_n, start, end}
    const wrap = document.createElement('div');
    wrap.className = 'ctl';
    wrap.style.flexWrap = 'wrap';

    const seg = document.createElement('div');
    seg.className = 'seg';
    [['4', 4], ['8', 8], ['12', 12], ['Все', 0]].forEach(([lbl, n]) => {
      const b = document.createElement('button');
      b.textContent = lbl;
      b.classList.toggle('active', current.mode === 'last' && current.last_n === n);
      b.addEventListener('click', () => { onChange({ mode: 'last', last_n: n }); });
      seg.appendChild(b);
    });
    const presetLbl = document.createElement('label');
    presetLbl.textContent = 'Период:';
    wrap.appendChild(presetLbl);
    wrap.appendChild(seg);

    // ручной диапазон с–по
    const rng = document.createElement('div');
    rng.className = 'ctl';
    const opts = state.weeks.slice().reverse(); // от старых к новым для удобства
    const mkSel = (val) => {
      const s = document.createElement('select');
      opts.forEach(w => {
        const o = document.createElement('option');
        o.value = w.year + '-' + w.week;
        o.textContent = `нед. ${w.week} (${w.period_text.split(' - ')[0]})`;
        if (val === o.value) o.selected = true;
        s.appendChild(o);
      });
      return s;
    };
    const startSel = mkSel(current.start);
    const endSel = mkSel(current.end || (state.weeks[0] && (state.weeks[0].year + '-' + state.weeks[0].week)));
    const dash = document.createElement('span'); dash.textContent = '—'; dash.className = 'muted';
    const lbl2 = document.createElement('label'); lbl2.textContent = 'или с:';
    const apply = () => onChange({ mode: 'range', start: startSel.value, end: endSel.value });
    startSel.addEventListener('change', apply);
    endSel.addEventListener('change', apply);
    rng.appendChild(lbl2); rng.appendChild(startSel); rng.appendChild(dash); rng.appendChild(endSel);
    wrap.appendChild(rng);
    return wrap;
  }

  function periodParams(p) {
    if (p.mode === 'range') return { start: p.start, end: p.end };
    if (p.last_n && p.last_n > 0) return { last_n: p.last_n };
    return {}; // все недели
  }

  // состояние периода на уровне модуля (сохраняется между перерисовками)
  const period = { mode: 'last', last_n: 0, start: null, end: null }; // 0 = все

  // ============================================================
  // 1. ОБЗОР НЕДЕЛИ
  // ============================================================
  async function overview(root, ctl, state) {
    if (!state.sel) { root.innerHTML = '<div class="empty">Нет данных. Загрузите отчёт.</div>'; return; }
    ctl.appendChild(App.weekSelect(() => App.renderView()));

    const [series, mp, cats] = await Promise.all([
      API.summarySeries({}),
      API.mpCompare({ year: state.sel.year, week: state.sel.week }),
      API.byCategory({ year: state.sel.year, week: state.sel.week, level: 3 }),
    ]);

    // найдём текущую неделю и её WoW в ряду
    const row = series.rows.find(r => r.year === state.sel.year && r.week === state.sel.week)
      || series.rows[series.rows.length - 1];
    const w = row.wow || {};

    const summary = await API.summary({ year: state.sel.year, week: state.sel.week });

    const kpis = [
      { label: 'Выручка', value: U.fmtMoney(summary.revenue), wow: U.wowBadge(w.revenue, { money: true }), accent: true },
      { label: 'Чистая прибыль', value: U.fmtMoney(summary.profit_net), wow: U.wowBadge(w.profit_net, { money: true }), accent: true },
      { label: 'Маржа', value: U.fmtPct(summary.margin_pct), wow: U.wowBadge(w.margin_pct, { pp: true }) },
      { label: 'Продаж, шт', value: U.fmtNum(summary.sales_qty), wow: U.wowBadge(w.sales_qty) },
      { label: 'Удержания', value: U.fmtMoney(summary.holds_total), wow: '' },
      { label: 'Товаров', value: U.fmtNum(summary.n_items), wow: '' },
    ];

    root.innerHTML = `
      <div class="kpi-grid">
        ${kpis.map(k => `
          <div class="kpi ${k.accent ? 'accent' : ''}">
            <div class="label">${k.label}</div>
            <div class="value">${k.value}</div>
            <div class="wow-slot">${k.wow}</div>
          </div>`).join('')}
      </div>
      <div class="grid-2">
        <div class="card">
          <h3>Структура расходов <span class="hint">из чего складывается прибыль</span></h3>
          <div class="chart" id="ov-waterfall"></div>
        </div>
        <div class="card">
          <h3>Ozon vs Wildberries <span class="hint">выручка и маржа за неделю</span></h3>
          <div class="chart" id="ov-mp"></div>
        </div>
      </div>
      <div class="card">
        <h3>Топ категорий по выручке <span class="hint">неделя ${state.sel.week}</span></h3>
        <div class="chart tall" id="ov-cats"></div>
      </div>
    `;

    drawWaterfall(document.getElementById('ov-waterfall'), summary);
    drawMpCompare(document.getElementById('ov-mp'), mp);
    drawCatBar(document.getElementById('ov-cats'), cats);
  }

  function drawWaterfall(el, s) {
    const t = U.chartTheme();
    const steps = [
      { name: 'Выручка', val: s.revenue, type: 'total' },
      { name: 'Себест.', val: -s.cogs },
      { name: 'Комиссия', val: -s.commission },
      { name: 'Логистика', val: -s.logistics },
      { name: 'Хранение', val: -s.storage },
      { name: 'Реклама', val: -s.promo },
      { name: 'Налог', val: -s.tax },
      { name: 'Удержания', val: -s.holds_total },
      { name: 'Чистая', val: s.profit_net, type: 'total' },
    ];
    const cats = steps.map(x => x.name);
    let running = 0;
    const base = [], pos = [], neg = [], totals = [];
    steps.forEach(x => {
      if (x.type === 'total') {
        base.push(0); pos.push('-'); neg.push('-'); totals.push(Math.round(x.val));
        running = x.val;
      } else {
        totals.push('-');
        if (x.val >= 0) { base.push(running); pos.push(Math.round(x.val)); neg.push('-'); running += x.val; }
        else { running += x.val; base.push(running); neg.push(Math.round(-x.val)); pos.push('-'); }
      }
    });
    const chart = U.makeChart(el);
    chart.setOption({
      ...U.baseOption(),
      legend: { show: false },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' },
        backgroundColor: t.tooltipBg, borderColor: t.tooltipBorder, textStyle: { color: t.text },
        formatter: (p) => {
          const i = p[0].dataIndex;
          return `${cats[i]}<br/><b>${U.fmtMoney(Math.round(steps[i].val))}</b>`;
        } },
      xAxis: { type: 'category', data: cats, axisLabel: { color: t.dim, fontSize: 11, interval: 0, rotate: 30 }, axisLine: { lineStyle: { color: t.axis } } },
      yAxis: { type: 'value', axisLabel: { color: t.dim, formatter: v => U.fmtMoneyShort(v) }, splitLine: { lineStyle: { color: t.split } } },
      series: [
        { name: 'base', type: 'bar', stack: 'w', itemStyle: { color: 'transparent' }, data: base },
        { name: 'Доход', type: 'bar', stack: 'w', itemStyle: { color: t.good, borderRadius: [3, 3, 0, 0] }, data: pos },
        { name: 'Расход', type: 'bar', stack: 'w', itemStyle: { color: t.bad, borderRadius: [3, 3, 0, 0] }, data: neg },
        { name: 'Итог', type: 'bar', stack: 'w', itemStyle: { color: t.accent, borderRadius: [3, 3, 0, 0] }, data: totals },
      ],
    });
  }

  function drawMpCompare(el, mp) {
    const t = U.chartTheme();
    const names = mp.map(x => x.marketplace === 'Wildberries' ? 'WB' : x.marketplace);
    const chart = U.makeChart(el);
    chart.setOption({
      ...U.baseOption(),
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: t.tooltipBg, borderColor: t.tooltipBorder, textStyle: { color: t.text },
        formatter: (ps) => {
          const i = ps[0].dataIndex;
          return `${names[i]}<br/>Выручка: <b>${U.fmtMoney(mp[i].revenue)}</b><br/>Чистая: <b>${U.fmtMoney(mp[i].profit_net)}</b><br/>Маржа: <b>${U.fmtPct(mp[i].margin_pct)}</b>` } },
      legend: { top: 0, textStyle: { color: t.dim } },
      xAxis: { type: 'category', data: names, axisLabel: { color: t.dim }, axisLine: { lineStyle: { color: t.axis } } },
      yAxis: [
        { type: 'value', name: '₽', axisLabel: { color: t.dim, formatter: v => U.fmtMoneyShort(v) }, splitLine: { lineStyle: { color: t.split } } },
        { type: 'value', name: '%', axisLabel: { color: t.dim, formatter: v => v + '%' }, splitLine: { show: false } },
      ],
      series: [
        { name: 'Выручка', type: 'bar', data: mp.map(x => Math.round(x.revenue)), itemStyle: { color: t.accent, borderRadius: [4, 4, 0, 0] }, barWidth: '34%' },
        { name: 'Чистая прибыль', type: 'bar', data: mp.map(x => Math.round(x.profit_net)), itemStyle: { color: t.ozon, borderRadius: [4, 4, 0, 0] }, barWidth: '34%' },
        { name: 'Маржа %', type: 'line', yAxisIndex: 1, data: mp.map(x => x.margin_pct), itemStyle: { color: t.good }, lineStyle: { width: 3 }, symbolSize: 8 },
      ],
    });
  }

  function drawCatBar(el, cats) {
    const t = U.chartTheme();
    const top = cats.slice(0, 12).reverse();
    const chart = U.makeChart(el);
    chart.setOption({
      ...U.baseOption(),
      grid: { left: 8, right: 80, top: 10, bottom: 8, containLabel: true },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: t.tooltipBg, borderColor: t.tooltipBorder, textStyle: { color: t.text },
        formatter: (ps) => `${ps[0].name}<br/>Выручка: <b>${U.fmtMoney(ps[0].value)}</b>` },
      legend: { show: false },
      xAxis: { type: 'value', axisLabel: { color: t.dim, formatter: v => U.fmtMoneyShort(v) }, splitLine: { lineStyle: { color: t.split } } },
      yAxis: { type: 'category', data: top.map(c => c.category), axisLabel: { color: t.dim }, axisLine: { lineStyle: { color: t.axis } } },
      series: [{
        type: 'bar', data: top.map(c => Math.round(c.revenue)),
        itemStyle: { color: t.accent, borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', color: t.dim, fontSize: 11, formatter: p => U.fmtMoneyShort(p.value) },
      }],
    });
  }

  // экспорт первого экрана; остальные добавляются ниже через Object.assign
  const api = { overview };

  // ============================================================
  // 2. ДИНАМИКА (столбцы по неделям, уровни: бизнес/МП/категории/SKU)
  // ============================================================
  const dynState = { level: 'business', metric: 'revenue' };

  async function dynamics(root, ctl, state) {
    if (!state.weeks.length) { root.innerHTML = '<div class="empty">Нет данных.</div>'; return; }
    ctl.appendChild(periodControl(state, period, (p) => { Object.assign(period, p); App.renderView(); }));

    root.innerHTML = `
      <div class="card">
        <h3>Показатель по неделям <span class="hint">столбцы — недели, сравнение динамики</span></h3>
        <div class="ctl" style="margin-bottom:14px; flex-wrap:wrap; gap:14px;">
          <div class="ctl"><label>Уровень:</label>
            <div class="seg" id="dyn-level">
              <button data-v="business">Весь бизнес</button>
              <button data-v="mp">По МП</button>
              <button data-v="category">Категории</button>
              <button data-v="sku">Товары</button>
            </div>
          </div>
          <div class="ctl"><label>Показатель:</label>
            <div class="seg" id="dyn-metric">
              <button data-v="revenue">Выручка</button>
              <button data-v="profit">Прибыль</button>
              <button data-v="margin_pct">Маржа %</button>
              <button data-v="sales_qty">Продажи</button>
            </div>
          </div>
        </div>
        <div class="chart tall" id="dyn-chart"></div>
      </div>
      <div class="card">
        <h3>Таблица по неделям <span class="hint" id="dyn-tbl-hint"></span></h3>
        <div id="dyn-table"></div>
      </div>
    `;
    const segLevel = root.querySelector('#dyn-level');
    const segMetric = root.querySelector('#dyn-metric');
    const syncSeg = () => {
      segLevel.querySelectorAll('button').forEach(b => b.classList.toggle('active', b.dataset.v === dynState.level));
      segMetric.querySelectorAll('button').forEach(b => b.classList.toggle('active', b.dataset.v === dynState.metric));
    };
    segLevel.addEventListener('click', e => { const b = e.target.closest('button'); if (b) { dynState.level = b.dataset.v; syncSeg(); loadDyn(state); } });
    segMetric.addEventListener('click', e => { const b = e.target.closest('button'); if (b) { dynState.metric = b.dataset.v; syncSeg(); loadDyn(state); } });
    syncSeg();
    await loadDyn(state);
  }

  async function loadDyn(state) {
    const chartEl = document.getElementById('dyn-chart');
    const tblEl = document.getElementById('dyn-table');
    const hint = document.getElementById('dyn-tbl-hint');
    if (!chartEl) return;
    const pp = periodParams(period);
    const metric = dynState.metric;
    const t = U.chartTheme();

    if (dynState.level === 'business' || dynState.level === 'mp') {
      const datasets = [];
      if (dynState.level === 'business') {
        const s = await API.summarySeries({ ...pp });
        datasets.push({ name: 'Весь бизнес', color: t.accent, rows: s.rows, weeks: s.weeks });
      } else {
        const [oz, wb] = await Promise.all([
          API.summarySeries({ ...pp, marketplace: 'ozon' }),
          API.summarySeries({ ...pp, marketplace: 'wb' }),
        ]);
        datasets.push({ name: 'Ozon', color: t.ozon, rows: oz.rows, weeks: oz.weeks });
        datasets.push({ name: 'WB', color: t.wb, rows: wb.rows, weeks: wb.weeks });
      }
      const weeks = datasets[0].weeks;
      const labels = weeks.map(w => 'нед. ' + w.week);
      const isLine = metric === 'margin_pct';
      const series = datasets.map(ds => ({
        name: ds.name, type: isLine ? 'line' : 'bar',
        data: ds.rows.map(r => r[metric]),
        itemStyle: { color: ds.color, borderRadius: isLine ? 0 : [4, 4, 0, 0] },
        lineStyle: { width: 3 }, symbolSize: 8, barMaxWidth: 46,
      }));
      const chart = U.makeChart(chartEl);
      chart.setOption({
        ...U.baseOption(),
        tooltip: { trigger: 'axis', axisPointer: { type: isLine ? 'line' : 'shadow' }, backgroundColor: t.tooltipBg, borderColor: t.tooltipBorder, textStyle: { color: t.text }, valueFormatter: v => fmtMetric(metric, v) },
        xAxis: { type: 'category', data: labels, axisLabel: { color: t.dim }, axisLine: { lineStyle: { color: t.axis } } },
        yAxis: { type: 'value', axisLabel: { color: t.dim, formatter: v => metric === 'margin_pct' ? v + '%' : (metric === 'sales_qty' ? U.fmtNum(v) : U.fmtMoneyShort(v)) }, splitLine: { lineStyle: { color: t.split } } },
        series,
      });
      hint.textContent = metricLabel(metric) + ' и изменение к предыдущей неделе';
      renderSeriesTable(tblEl, datasets, weeks, metric);
    } else if (dynState.level === 'category') {
      const m = metric === 'margin_pct' ? 'revenue' : metric;
      const data = await API.categoryMatrix({ ...pp, metric: m, level: 3 });
      drawMatrixChart(chartEl, data, m);
      hint.textContent = 'категории × недели';
      renderMatrixTable(tblEl, data, m, 'category');
    } else {
      const data = await API.skuMatrix({ ...pp, metric, limit: 60 });
      drawMatrixChart(chartEl, data, metric, true);
      hint.textContent = 'товары × недели (топ-60)';
      renderMatrixTable(tblEl, data, metric, 'sku');
    }
  }

  function metricLabel(m) {
    return { revenue: 'Выручка', profit: 'Прибыль', profit_net: 'Чистая прибыль', margin_pct: 'Маржа %', sales_qty: 'Продажи, шт' }[m] || m;
  }
  function fmtMetric(m, v) {
    if (m === 'margin_pct') return U.fmtPct(v);
    if (m === 'sales_qty') return U.fmtNum(v);
    return U.fmtMoney(v);
  }

  function renderSeriesTable(el, datasets, weeks, metric) {
    let h = '<div class="tbl-wrap"><table class="tbl"><thead><tr><th class="l">Неделя</th>';
    datasets.forEach(ds => { h += `<th>${U.esc(ds.name)}</th><th>WoW</th>`; });
    h += '</tr></thead><tbody>';
    weeks.forEach((wk, i) => {
      h += `<tr><td class="l">нед. ${wk.week} <span class="muted">${U.esc(wk.period_text)}</span></td>`;
      datasets.forEach(ds => {
        const r = ds.rows[i];
        const val = r ? r[metric] : null;
        const wow = r && r.wow ? r.wow[metric] : null;
        h += `<td class="num">${fmtMetric(metric, val)}</td><td>${U.wowPill(wow)}</td>`;
      });
      h += '</tr>';
    });
    h += '</tbody></table></div>';
    el.innerHTML = h;
  }

  function drawMatrixChart(el, data, metric, isSku) {
    const t = U.chartTheme();
    const labels = data.weeks.map(w => 'нед. ' + w.week);
    const top = data.rows.slice(0, 8);
    const series = top.map((r, idx) => ({
      name: U.esc(isSku ? r.seller_article : r.category),
      type: 'bar', data: r.cells,
      itemStyle: { color: t.palette[idx % t.palette.length], borderRadius: [3, 3, 0, 0] },
      barMaxWidth: 30,
    }));
    const chart = U.makeChart(el);
    chart.setOption({
      ...U.baseOption(),
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: t.tooltipBg, borderColor: t.tooltipBorder, textStyle: { color: t.text }, valueFormatter: v => fmtMetric(metric, v) },
      legend: { top: 0, type: 'scroll', textStyle: { color: t.dim, fontSize: 11 } },
      xAxis: { type: 'category', data: labels, axisLabel: { color: t.dim }, axisLine: { lineStyle: { color: t.axis } } },
      yAxis: { type: 'value', axisLabel: { color: t.dim, formatter: v => metric === 'sales_qty' ? U.fmtNum(v) : U.fmtMoneyShort(v) }, splitLine: { lineStyle: { color: t.split } } },
      series,
    });
  }

  function renderMatrixTable(el, data, metric, kind) {
    const weeks = data.weeks;
    let h = '<div class="tbl-wrap"><table class="tbl"><thead><tr>';
    h += `<th class="l">${kind === 'sku' ? 'Товар' : 'Категория'}</th>`;
    weeks.forEach(w => { h += `<th>нед. ${w.week}</th>`; });
    h += '<th>Итог</th><th>WoW</th></tr></thead><tbody>';
    data.rows.forEach(r => {
      const name = kind === 'sku'
        ? `${U.mpPill(r.marketplace)} <b>${U.esc(r.seller_article)}</b> <span class="muted">${U.esc((r.item_name || '').slice(0, 40))}</span>`
        : U.esc(r.category);
      h += `<tr><td class="l">${name}</td>`;
      r.cells.forEach(c => { h += `<td class="num">${fmtMetric(metric, c)}</td>`; });
      h += `<td class="num"><b>${fmtMetric(metric, r.total)}</b></td><td>${U.wowPill(r.wow)}</td></tr>`;
    });
    h += '</tbody></table></div>';
    el.innerHTML = h;
  }

  // ============================================================
  // 3. OZON vs WB (по неделям)
  // ============================================================
  async function compare(root, ctl, state) {
    if (!state.weeks.length) { root.innerHTML = '<div class="empty">Нет данных.</div>'; return; }
    ctl.appendChild(periodControl(state, period, (p) => { Object.assign(period, p); App.renderView(); }));
    const pp = periodParams(period);
    const [oz, wb] = await Promise.all([
      API.summarySeries({ ...pp, marketplace: 'ozon' }),
      API.summarySeries({ ...pp, marketplace: 'wb' }),
    ]);
    root.innerHTML = `
      <div class="grid-2">
        <div class="card"><h3>Выручка по неделям</h3><div class="chart" id="cmp-rev"></div></div>
        <div class="card"><h3>Чистая прибыль по неделям</h3><div class="chart" id="cmp-net"></div></div>
      </div>
      <div class="card"><h3>Маржа по неделям</h3><div class="chart" id="cmp-margin"></div></div>
      <div class="card"><h3>Сводная таблица (выручка)</h3><div id="cmp-tbl"></div></div>
    `;
    const weeks = oz.weeks;
    drawCmp(document.getElementById('cmp-rev'), weeks, oz.rows, wb.rows, 'revenue');
    drawCmp(document.getElementById('cmp-net'), weeks, oz.rows, wb.rows, 'profit_net');
    drawCmp(document.getElementById('cmp-margin'), weeks, oz.rows, wb.rows, 'margin_pct');
    const datasets = [
      { name: 'Ozon', color: U.chartTheme().ozon, rows: oz.rows },
      { name: 'WB', color: U.chartTheme().wb, rows: wb.rows },
    ];
    renderSeriesTable(document.getElementById('cmp-tbl'), datasets, weeks, 'revenue');
  }

  function drawCmp(el, weeks, ozRows, wbRows, metric) {
    const t = U.chartTheme();
    const labels = weeks.map(w => 'нед. ' + w.week);
    const isLine = metric === 'margin_pct';
    const chart = U.makeChart(el);
    chart.setOption({
      ...U.baseOption(),
      tooltip: { trigger: 'axis', axisPointer: { type: isLine ? 'line' : 'shadow' }, backgroundColor: t.tooltipBg, borderColor: t.tooltipBorder, textStyle: { color: t.text }, valueFormatter: v => fmtMetric(metric, v) },
      xAxis: { type: 'category', data: labels, axisLabel: { color: t.dim }, axisLine: { lineStyle: { color: t.axis } } },
      yAxis: { type: 'value', axisLabel: { color: t.dim, formatter: v => metric === 'margin_pct' ? v + '%' : U.fmtMoneyShort(v) }, splitLine: { lineStyle: { color: t.split } } },
      series: [
        { name: 'Ozon', type: isLine ? 'line' : 'bar', data: ozRows.map(r => r[metric]), itemStyle: { color: t.ozon, borderRadius: [4, 4, 0, 0] }, lineStyle: { width: 3 }, symbolSize: 8, barMaxWidth: 40 },
        { name: 'WB', type: isLine ? 'line' : 'bar', data: wbRows.map(r => r[metric]), itemStyle: { color: t.wb, borderRadius: [4, 4, 0, 0] }, lineStyle: { width: 3 }, symbolSize: 8, barMaxWidth: 40 },
      ],
    });
  }

  // ============================================================
  // 4. КАТЕГОРИИ (drill-down У1 -> У2 -> У3)
  // ============================================================
  const catNav = { l1: null, l2: null };

  async function categories(root, ctl, state) {
    if (!state.sel) { root.innerHTML = '<div class="empty">Нет данных.</div>'; return; }
    ctl.appendChild(App.weekSelect(() => App.renderView()));
    const level = catNav.l2 ? 3 : (catNav.l1 ? 2 : 1);
    const data = await API.byCategory({ year: state.sel.year, week: state.sel.week, level, l1: catNav.l1, l2: catNav.l2 });

    let crumbs = `<a href="#" data-crumb="root">Все категории (У1)</a>`;
    if (catNav.l1) crumbs += ` › <a href="#" data-crumb="l1">${U.esc(catNav.l1)}</a>`;
    if (catNav.l2) crumbs += ` › <span>${U.esc(catNav.l2)}</span>`;

    root.innerHTML = `
      <div class="card">
        <h3>Drill-down по категориям <span class="hint">кликните строку, чтобы углубиться</span></h3>
        <div style="margin-bottom:12px; font-size:13px;">${crumbs}</div>
        <div class="chart" id="cat-chart"></div>
      </div>
      <div class="card">
        <h3>Таблица (уровень ${level}) <span class="hint">${data.length} строк</span></h3>
        <div id="cat-tbl"></div>
      </div>
    `;
    drawCatBar(document.getElementById('cat-chart'), data);

    const totalRev = data.reduce((a, c) => a + c.revenue, 0) || 1;
    let h = '<div class="tbl-wrap"><table class="tbl"><thead><tr><th class="l">Категория</th><th>Выручка</th><th>Доля</th><th>Прибыль</th><th>Маржа</th><th>Продаж</th><th>SKU</th></tr></thead><tbody>';
    data.forEach(c => {
      const margin = c.revenue ? c.profit / c.revenue * 100 : 0;
      const canDrill = level < 3 && c.category !== '(без категории)';
      h += `<tr ${canDrill ? `data-drill="${U.esc(c.category)}" style="cursor:pointer"` : ''}>
        <td class="l">${canDrill ? '<span class="expander">▸</span> ' : ''}${U.esc(c.category)}</td>
        <td class="num">${U.fmtMoney(c.revenue)}</td>
        <td class="num">${U.fmtPct(c.revenue / totalRev * 100)}</td>
        <td class="num">${U.fmtMoney(c.profit)}</td>
        <td class="num ${U.marginClass(margin)}">${U.fmtPct(margin)}</td>
        <td class="num">${U.fmtNum(c.sales_qty)}</td>
        <td class="num">${c.n}</td></tr>`;
    });
    h += '</tbody></table></div>';
    const tbl = document.getElementById('cat-tbl');
    tbl.innerHTML = h;
    tbl.querySelectorAll('tr[data-drill]').forEach(tr => {
      tr.addEventListener('click', () => {
        const val = tr.dataset.drill;
        if (level === 1) catNav.l1 = val;
        else if (level === 2) catNav.l2 = val;
        App.renderView();
      });
    });
    root.querySelectorAll('[data-crumb]').forEach(a => a.addEventListener('click', (e) => {
      e.preventDefault();
      if (a.dataset.crumb === 'root') { catNav.l1 = null; catNav.l2 = null; }
      else if (a.dataset.crumb === 'l1') { catNav.l2 = null; }
      App.renderView();
    }));
  }

  // ============================================================
  // 5. ТОВАРЫ / SKU (таблица + раскрытие драйверов маржи)
  // ============================================================
  const skuState = { sort: 'revenue', search: '', mp: 'all' };

  // ============================================================
  // Раздел «Продажи, шт.» — план/факт по иерархии и месяцам.
  // Вкладки: Wildberries / Ozon / Сводная. Фильтры: год, статус, менеджер.
  // Чекбокс «прогноз выполнения» (по умолчанию ВКЛ) — для незавершённого месяца.
  // ============================================================
  // yoy — показывать дорожку факта прошлого года (year-1) между Планом и Фактом; по умолчанию ВЫКЛ.
  const salesState = { tab: 'wb', year: null, status: '', manager: '', forecast: true, hideStatus: true, yoy: false, rub: false, manualOnly: false, search: '', searchExpandSig: '', expanded: {}, data: null };
  const RU_MON_SH = ['', 'Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек'];

  function salesMpParam() {
    if (salesState.tab === 'ozon') return 'ozon';
    if (salesState.tab === 'yandex') return 'yandex';
    if (salesState.tab === 'cross') return 'cross';
    return 'wb';
  }
  // целое число штук; '—' для пусто
  function spNum(v) { return (v === null || v === undefined || isNaN(v)) ? '—' : U.fmtNum(Math.round(v)); }
  // целое число штук, но НОЛЬ/пусто не выводим (чтобы не перегружать таблицу)
  function spNumZ(v) {
    if (v === null || v === undefined || isNaN(v) || Math.round(v) === 0) return '';
    return U.fmtNum(Math.round(v));
  }
  function spPct(fact, plan) { return (!plan || plan <= 0) ? null : (fact / plan * 100); }
  function spPctClass(p) { if (p === null) return ''; if (p >= 100) return 'sp-good'; if (p >= 70) return 'sp-warn'; return 'sp-bad'; }
  function statusBadge(s) { return `<span class="status-badge ${stClass(s)}">${U.esc(stDisplay(s))}</span>`; }

  // Светофор для процента прогноза выполнения: ≥100 зелёный, 70–99 жёлтый, <70 красный.
  function spFcClass(p) { if (p === null || p === undefined || isNaN(p)) return ''; if (p >= 100) return 'sp-fc-good'; if (p >= 70) return 'sp-fc-warn'; return 'sp-fc-bad'; }

  // Ячейка месяца: столбцы «План» (синий) и «Факт» (чёрный) рядом.
  // % выполнения не выводим. Прогноз — только в ТЕКУЩЕМ месяце при включённой
  // галочке «прогноз выполнения», СПРАВА от факта, в процентах, по системе светофор.
  function salesMonthCell(m, forecast, isCur, edit) {
    if (!m) return '<div class="sp-cell"></div>';
    const planTxt = spNumZ(m.plan);
    const factTxt = spNumZ(m.fact);
    // Прогноз — только в текущем месяце при включённой галочке, в процентах (прогноз/план×100).
    let fcHtml = '';
    if (forecast && isCur && m.partial && m.plan > 0 && Math.round(m.fc || 0) !== 0) {
      const fcPct = (m.fc / m.plan) * 100;
      fcHtml = `<span class="sp-fc ${spFcClass(fcPct)}" title="Прогноз выполнения плана месяца">${U.fmtNum(Math.round(fcPct))}%</span>`;
    }
    // Три фиксированные дорожки (план | факт | %) — всегда выводим все три ячейки,
    // чтобы планы были строго под планами, факт под фактом, % под %. Нули/пустые — без текста.
    let planCls = 'sp-plan';
    let planAttr = ' title="План, шт"';
    // Содержимое дорожки плана. Для редактируемых листов с ПУСТЫМ планом
    // выводим бледный прочерк-плейсхолдер «–»: он даёт видимую и кликабельную
    // область, чтобы двойным кликом можно было завести план «с нуля».
    let planInner = planTxt;
    if (edit) {
      planCls += ' sp-plan-edit';
      const rawPlan = (m.plan != null && !isNaN(m.plan)) ? Math.round(m.plan) : 0;
      if (m.is_manual) {
        planCls += ' sales-plan-manual';
        const prevTxt = (m.prev_qty === null || m.prev_qty === undefined) ? '—' : U.fmtNum(Math.round(m.prev_qty));
        planAttr = ` title="Было: ${prevTxt}"`;
      } else if (!planTxt) {
        planCls += ' sp-plan-empty';
        planInner = '–';
        planAttr = ' title="Двойной клик — задать план"';
      } else {
        planAttr = ' title="Двойной клик — изменить план"';
      }
      planAttr += ` data-art="${U.esc(edit.art)}" data-month="${edit.month}" data-plan="${rawPlan}"`;
    }
    const planHtml = `<span class="${planCls}"${planAttr}>${planInner}</span>`;
    const factHtml = `<span class="sp-fact" title="Факт, шт">${factTxt}</span>`;
    // Дорожка факта прошлого года (YoY) — светло-серая, тем же шрифтом, что план/факт.
    // Появляется МЕЖДУ планом и фактом сразу, как только включена галочка «факт YoY».
    const yoyOn = salesState.yoy;
    const prevHtml = yoyOn
      ? `<span class="sp-fact-prev" title="Факт прошлого года, шт">${spNumZ(m.fact_prev)}</span>`
      : '';
    // Дорожка рублёвого плана (галочка «продажи в руб.») — синяя нежирная,
    // сразу ПОД планом_шт (между планом и YoY/фактом). Значение в рублях,
    // целое. Независима от YoY (можно включать обе одновременно).
    const rubOn = salesState.rub;
    const rubHtml = rubOn
      ? `<span class="sp-plan-rub" title="План, руб.">${spNumZ(m.plan_rub)}</span>`
      : '';
    // Третья дорожка под % — только в текущем месяце (класс sp-cell-fc). Если % нет —
    // всё равно резервируем дорожку пустым span, чтобы колонки не «гуляли».
    const isCurCell = forecast && isCur && m.partial;
    // Если в ячейке заполнено только ОДНО значение (только план ИЛИ только факт) и
    // нет дорожки прогноза — выводим его на всю ширину ячейки с прижатием вправо,
    // чтобы все одиночные числа выстраивались в одну вертикаль у правого края
    // (иначе план-одиночка «висит» по центру, а факт-одиночка — справа: лесенка).
    // Одиночное выравнивание отключаем при развёрнутой YoY-дорожке: там порядок дорожек фиксирован (план|YoY|факт).
    const onlyOne = !isCurCell && !yoyOn && !rubOn && ((planTxt && !factTxt) || (!planTxt && factTxt));
    let cls = isCurCell ? 'sp-cell sp-cell-fc' : 'sp-cell';
    if (onlyOne) cls += ' sp-cell-single';
    if (rubOn) cls += ' sp-cell-rub';
    if (yoyOn) cls += ' sp-cell-yoy';
    const fcCell = isCurCell ? (fcHtml || '<span class="sp-fc"></span>') : '';
    // Порядок дорожек: План | (План_руб) | (Факт прошлого года) | Факт | (прогноз %).
    return `<div class="${cls}">${planHtml}${rubHtml}${prevHtml}${factHtml}${fcCell}</div>`;
  }

  // Фильтр «исправлено вручную»: у листа (level 4) есть ручная правка,
  // если хоть в одном месяце is_manual === true.
  function leafHasManual(node) {
    if (node.level !== 4 || !node.months) return false;
    return node.months.some(m => m && m.is_manual);
  }
  // Узел показывается при активном фильтре: лист — если сам ручной;
  // группа — если хотя бы один потомок-лист ручной (рекурсивно).
  function nodeVisibleManual(node) {
    if (node.level === 4) return leafHasManual(node);
    if (node.children) return node.children.some(nodeVisibleManual);
    return false;
  }

  // Поиск по артикулу (подстрока, регистронезависимо) — как в РНП Юнитка.
  // Работает во всех трёх подразделах (WB / OZON / Сводная): у листа всегда
  // есть leaf_info.seller_article. Совместим с фильтром «исправлено вручную» (И-логика).
  function salesSearchActive() {
    return !!(salesState.search && salesState.search.trim());
  }
  // Проходит ли лист (level 4) через поисковый фильтр по артикулу.
  function salesLeafMatchesSearch(node) {
    const q = (salesState.search || '').trim().toLowerCase();
    if (!q) return true;
    const art = ((node.leaf_info || {}).seller_article || '').toLowerCase();
    return art.indexOf(q) !== -1;
  }
  // Виден ли узел при активном поиске: лист — если сам совпал; группа — если
  // хотя бы один потомок-лист совпал (рекурсивно).
  function nodeVisibleSearch(node) {
    if (node.level === 4) return salesLeafMatchesSearch(node);
    if (node.children) return node.children.some(nodeVisibleSearch);
    return false;
  }

  // Рекурсивная отрисовка строк дерева с учётом раскрытия (salesState.expanded).
  function salesBuildRows(node, depth, isCross, months, curMonth) {
    let html = '';
    const applyManual = salesState.manualOnly && !isCross;
    const applySearch = salesSearchActive();
    // При активном фильтре «исправлено вручную» пропускаем узлы без ручных правок.
    if (applyManual && !nodeVisibleManual(node)) return '';
    // При активном поиске пропускаем узлы, в поддереве которых нет совпадений по артикулу.
    if (applySearch && !nodeVisibleSearch(node)) return '';
    const hasChildren = node.children && node.children.length;
    // При активном фильтре/поиске проходящие группы всегда раскрыты (чтобы товары были
    // видны), не трогая сохранённое состояние salesState.expanded.
    const expanded = (applyManual || applySearch) ? true : !!salesState.expanded[node.key];
    const isLeaf = node.level === 4;
    const li = node.leaf_info || {};
    const indent = depth * 15;
    let nameCell;
    if (isLeaf) {
      nameCell = `<td class="sp-name sp-art" style="padding-left:${indent + 16}px">${U.esc(li.seller_article)}</td>`;
    } else {
      const exp = hasChildren ? `<span class="sp-exp">${expanded ? '▾' : '▸'}</span>` : '';
      nameCell = `<td class="sp-name sp-grp lvl${node.level}" style="padding-left:${indent}px">${exp}${U.esc(node.name)}</td>`;
    }
    let statusCells = '';
    if (!salesState.hideStatus) {
      if (isCross) {
        statusCells = isLeaf
          ? `<td class="sp-st sp-st1">${statusBadge(li.ozon_status)}</td><td class="sp-st sp-st2">${statusBadge(li.vb_status)}</td><td class="sp-st sp-st3">${statusBadge(li.ya_status)}</td>`
          : `<td class="sp-st sp-st1"></td><td class="sp-st sp-st2"></td><td class="sp-st sp-st3"></td>`;
      } else {
        statusCells = isLeaf ? `<td class="sp-st sp-st1">${statusBadge(li.status)}</td>` : `<td class="sp-st sp-st1"></td>`;
      }
    }
    const canEdit = isLeaf && !isCross;
    const monthCells = months.map(mo => {
      const cls = mo === curMonth ? 'sp-cur' : '';
      const edit = canEdit ? { art: li.seller_article, month: mo } : null;
      return `<td class="sp-mth ${cls}">${salesMonthCell(node.months[mo - 1], salesState.forecast, mo === curMonth, edit)}</td>`;
    }).join('');
    // Итоги за год в ШТУКАХ: сумма помесячных план/факт по узлу.
    let totPlanQty = 0, totFactQty = 0;
    for (let i = 0; i < node.months.length; i++) {
      totPlanQty += node.months[i].plan || 0;
      totFactQty += node.months[i].fact || 0;
    }
    const totals = `<td class="num sp-sum sp-sum-plan">${U.fmtNum(Math.round(totPlanQty))}</td>`
      + `<td class="num sp-sum">${U.fmtNum(Math.round(totFactQty))}</td>`;
    const rowCls = `sp-row lvl${node.level} ` + (isLeaf ? 'sp-leaf' : 'sp-group') + (hasChildren ? ' sp-has' : '');
    html += `<tr class="${rowCls}" data-key="${U.esc(node.key)}"${hasChildren ? ' style="cursor:pointer"' : ''}>${nameCell}${statusCells}${monthCells}${totals}</tr>`;
    if (expanded && hasChildren) {
      node.children.forEach(c => { html += salesBuildRows(c, depth + 1, isCross, months, curMonth); });
    }
    return html;
  }

  // Перерисовка только таблицы (без перезапроса) — для чекбокса и раскрытия.
  // ВАЖНО: innerHTML пересоздаёт скролл-контейнер .sp-wrap, из-за чего его
  // прокрутка сбрасывалась в 0 и таблица «прыгала» вверх при раскрытии группы.
  // Поэтому запоминаем позицию прокрутки до перерисовки и восстанавливаем после.
  function salesPaintTable(host, data) {
    const prevWrap = host.querySelector('.sp-wrap');
    const prevScrollTop = prevWrap ? prevWrap.scrollTop : 0;
    const prevScrollLeft = prevWrap ? prevWrap.scrollLeft : 0;
    const isCross = !!data.is_cross;
    const months = data.months || [];
    const now = new Date();
    const curMonth = (salesState.year === now.getFullYear()) ? (now.getMonth() + 1) : null;
    const tree = data.tree;
    if (!tree || !tree.children || !tree.children.length) {
      host.innerHTML = '<div class="empty">Нет данных по выбранным фильтрам.</div>';
      return;
    }
    const stHead = salesState.hideStatus
      ? ''
      : (isCross
          ? '<th class="sp-st sp-st1">Статус OZ</th><th class="sp-st sp-st2">Статус WB</th><th class="sp-st sp-st3">Статус YA</th>'
          : '<th class="sp-st sp-st1">Статус</th>');
    const mthHead = months.map(mo =>
      `<th class="sp-mth ${mo === curMonth ? 'sp-cur' : ''}">${RU_MON_SH[mo]}</th>`).join('');
    let rows = salesBuildRows(tree, 0, isCross, months, curMonth);
    // Фильтр/поиск дал пустой результат — аккуратная заглушка на всю ширину
    // таблицы, не ломая верстку. Поиск приоритетнее в тексте сообщения.
    if (!rows && (salesSearchActive() || (salesState.manualOnly && !isCross))) {
      const colspan = 1 + (salesState.hideStatus ? 0 : (isCross ? 3 : 1)) + months.length + 2;
      const msg = salesSearchActive()
        ? `Ничего не найдено по артикулу «${U.esc(salesState.search)}»`
        : 'Нет товаров с ручными правками плана за выбранный год';
      rows = `<tr><td class="muted" colspan="${colspan}" style="padding:16px; text-align:center;">${msg}</td></tr>`;
    }
    // Класс уровня таблицы — управляет правой границей последней липкой
    // статус-колонки (sp-has-st2 для «Сводной», sp-has-st1only для одного МП).
    // Если статусы скрыты — ни одного класса, липким остаётся только артикул.
    let stTblCls = '';
    if (!salesState.hideStatus) stTblCls = isCross ? ' sp-has-st3' : ' sp-has-st1only';
    host.innerHTML = `
      <div class="tbl-wrap sp-wrap">
        <table class="tbl sp-tbl${stTblCls}">
          <thead>
            <tr>
              <th class="sp-name">Иерархия / Артикул</th>
              ${stHead}
              ${mthHead}
              <th class="num sp-sum sp-sum-plan">Σ план, шт.</th>
              <th class="num sp-sum">Σ факт, шт.</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <div class="sp-legend muted">В ячейке месяца: <b class="sp-lg-plan">план</b> / ${salesState.rub ? `<b class="sp-lg-rub">план&nbsp;руб.</b> / ` : ''}${salesState.yoy ? `<b class="sp-lg-prev">факт ${data.prev_year}</b> / ` : ''}<b>факт</b>${salesState.forecast ? '; <b class="sp-fc sp-fc-good">%</b> — прогноз выполнения плана текущего месяца (светофор: ≥100 зелёный, 70–99 жёлтый, &lt;70 красный)' : ''}. Σ план/факт, шт. — сумма за год в штуках.</div>`;
    // Восстанавливаем позицию прокрутки нового контейнера — чтобы при раскрытии
    // группы или переключении чекбоксов таблица оставалась на том же месте.
    const newWrap = host.querySelector('.sp-wrap');
    if (newWrap && (prevScrollTop || prevScrollLeft)) {
      newWrap.scrollTop = prevScrollTop;
      newWrap.scrollLeft = prevScrollLeft;
    }
  }

  // Локальное обновление данных после ручной правки ячейки плана:
  // находим лист по seller_article, ставим новое значение/флаги, а разницу
  // (delta) протягиваем в план того же месяца у всех родительских узлов.
  // Затем перерисовываем таблицу (раскрытие/скролл сохраняются).
  function applyPlanCellUpdate(art, month, res) {
    const data = salesState.data;
    if (!data || !data.tree) return;
    const idx = month - 1;
    const path = [];
    let leaf = null;
    (function walk(node) {
      if (leaf) return;
      path.push(node);
      const li = node.leaf_info || {};
      if (node.level === 4 && li.seller_article === art) { leaf = node; return; }
      if (node.children) { for (const c of node.children) { walk(c); if (leaf) return; } }
      path.pop();
    })(data.tree);
    if (!leaf) return;
    const lm = leaf.months[idx];
    const oldPlan = (lm && lm.plan != null && !isNaN(lm.plan)) ? lm.plan : 0;
    const delta = res.plan_qty - oldPlan;
    lm.plan = res.plan_qty;
    lm.is_manual = !!res.is_manual;
    lm.prev_qty = (res.prev_qty === undefined) ? null : res.prev_qty;
    // Протягиваем разницу в предков (все узлы пути, кроме самого листа).
    for (let i = 0; i < path.length - 1; i++) {
      const pm = path[i].months[idx];
      if (pm) pm.plan = (pm.plan || 0) + delta;
    }
    if (API.cacheClear) API.cacheClear('/api/sales');
    const host = document.querySelector('#sp-tbl');
    if (host) salesPaintTable(host, data);
  }

  // Переключатель МП раздела «Продажи шт.» (общий для реальных
  // табов и для заглушки Yandex) — чтобы оформление было единым.
  function skusSubtabsHtml() {
    return [['wb', 'Wildberries', 'wb'], ['ozon', 'OZON', 'ozon'], ['yandex', 'Yandex', 'yandex'], ['cross', 'Сводная', 'cross']]
      .map(([id, label, c]) => `<button class="subtab ${c}${salesState.tab === id ? ' active' : ''}" data-stab="${id}">${label}</button>`)
      .join('');
  }
  // Привязка обработчиков к переключателю МП (вынесено, т.к. используется
  // и в заглушке Yandex, и в основном рендере).
  function skusBindSubtabs(root) {
    root.querySelectorAll('#sp-subtabs .subtab').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.dataset.stab;
        if (id === salesState.tab) return;
        salesState.tab = id;
        salesState.expanded = {};
        App.renderView();
      });
    });
  }

  async function skus(root, ctl, state) {
    ctl.innerHTML = '';
    const isAdmin = App && App.state && App.state.user && App.state.user.role === 'admin';
    root.innerHTML = '<div class="loader"><span class="spinner"></span></div>';

    // YANDEX — полноценная вкладка, как Ozon/WB: факт из fact_monthly
    // (месячные отчёты), план из sales_plan (WHERE marketplace='Yandex';
    // пока пуст — план-колонки будут нулевыми, факт заполнен). Роутер
    // /api/sales generic по marketplace, поэтому отдельная ветка не нужна.

    const reqYear = salesState.year || new Date().getFullYear();
    let data;
    try {
      data = await API.salesPlanFact({
        marketplace: salesMpParam(), year: reqYear,
        status: salesState.status || undefined, manager: salesState.manager || undefined,
      });
    } catch (e) { root.innerHTML = `<div class="empty">Ошибка: ${U.esc(e.message)}</div>`; return; }

    // Год по умолчанию: если запрошенного нет в данных — берём последний доступный.
    if (!salesState.year) {
      const yrs = data.years || [];
      salesState.year = yrs.includes(reqYear) ? reqYear : (yrs.length ? yrs[yrs.length - 1] : reqYear);
      if (salesState.year !== reqYear) {
        data = await API.salesPlanFact({
          marketplace: salesMpParam(), year: salesState.year,
          status: salesState.status || undefined, manager: salesState.manager || undefined,
        });
      }
    }
    salesState.data = data;
    // Корень «Итоги» раскрыт по умолчанию.
    if (data.tree && data.tree.key && Object.keys(salesState.expanded).length === 0) {
      salesState.expanded[data.tree.key] = true;
    }

    const isSingle = salesState.tab !== 'cross';
    const subtabs = skusSubtabsHtml();
    const yearOpts = (data.years || []).map(y =>
      `<option value="${y}"${y === salesState.year ? ' selected' : ''}>${y}</option>`).join('');
    const statusOpts = ['<option value="">Все статусы</option>']
      .concat((data.statuses || []).map(s => `<option value="${U.esc(s)}"${salesState.status === s ? ' selected' : ''}>${U.esc(s)}</option>`)).join('');
    const mgrOpts = ['<option value="">Все менеджеры</option>']
      .concat((data.managers || []).map(m => `<option value="${U.esc(m)}"${salesState.manager === m ? ' selected' : ''}>${U.esc(m)}</option>`)).join('');

    // Класс маркетплейса для карточки — как в РНП (цветная полоса/обводка).
    // Сводная — нейтральная (серо-белая), т.к. не относится к конкретному МП.
    const spMpCls = salesState.tab === 'wb' ? 'mp-wb'
      : salesState.tab === 'ozon' ? 'mp-ozon'
      : salesState.tab === 'yandex' ? 'mp-yandex' : 'mp-cross';
    root.innerHTML = `
      <div class="card sp-card ${spMpCls}">
        <div class="sp-toolbar">
          <div class="subtabs" id="sp-subtabs">${subtabs}</div>
          <div class="ctl sp-ctl" style="gap:12px; flex-wrap:wrap; align-items:center;">
            <div class="sp-filters">
              <input class="input-sm" id="sp-search-art" placeholder="Поиск по артикулу…" value="${U.esc(salesState.search || '')}">
              <label class="ctl">Год: <select id="sp-year" class="input-sm">${yearOpts}</select></label>
              <label class="ctl">Статус: <select id="sp-status" class="input-sm">${statusOpts}</select></label>
              <label class="ctl">Менеджер: <select id="sp-mgr" class="input-sm">${mgrOpts}</select></label>
              <div class="sp-settings" id="sp-settings">
                <button type="button" class="input-sm sp-settings-btn" id="sp-settings-btn" aria-expanded="false" title="Отображение дорожек и фильтров таблицы">Настройки</button>
                <div class="sp-settings-pop" id="sp-settings-pop" hidden>
                  <label class="sp-fc-check" style="cursor:pointer;"><input type="checkbox" id="sp-forecast"${salesState.forecast ? ' checked' : ''}> прогноз выполнения</label>
                  <label class="sp-fc-check" style="cursor:pointer;"><input type="checkbox" id="sp-hide-status"${salesState.hideStatus ? ' checked' : ''}> скрыть статус</label>
                  <label class="sp-fc-check" style="cursor:pointer;" title="Показать факт продаж прошлого года в ячейках месяцев"><input type="checkbox" id="sp-yoy"${salesState.yoy ? ' checked' : ''}> факт YoY</label>
                  <label class="sp-fc-check" style="cursor:pointer;" title="Показать рублёвый план (план_шт × цена) в ячейках месяцев"><input type="checkbox" id="sp-rub"${salesState.rub ? ' checked' : ''}> продажи в руб.</label>
                  ${isSingle ? `<label class="sp-fc-check" style="cursor:pointer;" title="Показать только товары с ручными правками плана в этом году"><input type="checkbox" id="sp-manual-only"${salesState.manualOnly ? ' checked' : ''}> исправлено вручную</label>` : ''}
                </div>
              </div>
              <button class="btn-sm" id="sp-expand" type="button" title="Развернуть все группировки">Развернуть всё</button>
              <button class="btn-sm" id="sp-collapse" type="button" title="Свернуть все группировки до корня («Итоги»)">Свернуть всё</button>
            </div>
            ${(isSingle) ? `<div class="sp-actions">
              <button class="btn" id="sp-export" title="Выгрузить план в Excel">⬇️ Выгрузить</button>
              ${isAdmin ? `<button class="btn" id="sp-import-btn" title="Загрузить план из Excel">⬆️ Загрузить</button>
              <input type="file" id="sp-import-file" accept=".xlsx,.xls" class="hidden" style="display:none">` : ''}
            </div>` : `<div class="sp-actions">
              <button class="btn" id="sp-report-export" title="Выгрузить красивый отчёт в Excel: иерархия номенклатуры, план/факт помесячно и прогноз выполнения текущего месяца">📊 Выгрузить отчёт</button>
            </div>`}
          </div>
        </div>
        <div id="sp-imp-result"></div>
        <div class="rnp-card-title">Продажи, шт. — план/факт</div>
        <div id="sp-tbl"></div>
      </div>`;

    salesPaintTable(root.querySelector('#sp-tbl'), data);
    syncSalesToolbarHeight();

    // --- Вкладки маркетплейса (общий биндер с заглушкой Yandex) ---
    skusBindSubtabs(root);
    // --- Поиск по артикулу (debounce 300мс, без перезапроса) — как в РНП Юнитка.
    // Запрос сохраняется в salesState.search и переживает переключение вкладок. ---
    const spSearchEl = root.querySelector('#sp-search-art');
    if (spSearchEl) {
      let debSp;
      spSearchEl.addEventListener('input', () => {
        clearTimeout(debSp);
        debSp = setTimeout(() => {
          salesState.search = spSearchEl.value.trim();
          salesPaintTable(root.querySelector('#sp-tbl'), salesState.data);
        }, 300);
      });
    }
    // --- Фильтры ---
    root.querySelector('#sp-year').addEventListener('change', (e) => { salesState.year = Number(e.target.value); salesState.expanded = {}; App.renderView(); });
    root.querySelector('#sp-status').addEventListener('change', (e) => { salesState.status = e.target.value; salesState.expanded = {}; App.renderView(); });
    root.querySelector('#sp-mgr').addEventListener('change', (e) => { salesState.manager = e.target.value; salesState.expanded = {}; App.renderView(); });
    // --- Чекбокс прогноза — без перезапроса ---
    root.querySelector('#sp-forecast').addEventListener('change', (e) => {
      salesState.forecast = e.target.checked;
      salesPaintTable(root.querySelector('#sp-tbl'), salesState.data);
    });
    // --- Чекбокс «скрыть статус»: убирает столбцы статуса из таблицы
    // (только перерисовка; выгрузка/загрузка плана не затрагиваются). ---
    root.querySelector('#sp-hide-status').addEventListener('change', (e) => {
      salesState.hideStatus = e.target.checked;
      salesPaintTable(root.querySelector('#sp-tbl'), salesState.data);
    });
    // --- Чекбокс «факт YoY»: включает/выключает дорожку факта прошлого года
    // (сразу между Планом и Фактом). Без перезапроса — только перерисовка. ---
    root.querySelector('#sp-yoy').addEventListener('change', (e) => {
      salesState.yoy = e.target.checked;
      salesPaintTable(root.querySelector('#sp-tbl'), salesState.data);
    });
    root.querySelector('#sp-rub').addEventListener('change', (e) => {
      salesState.rub = e.target.checked;
      salesPaintTable(root.querySelector('#sp-tbl'), salesState.data);
    });
    // --- Чекбокс «исправлено вручную»: оставляет только товары с ручными
    // правками плана (только ozon/wb). Без перезапроса — только перерисовка. ---
    const manualChk = root.querySelector('#sp-manual-only');
    if (manualChk) {
      manualChk.addEventListener('change', (e) => {
        salesState.manualOnly = e.target.checked;
        salesPaintTable(root.querySelector('#sp-tbl'), salesState.data);
      });
    }
    // --- Раскрытие/сворачивание узлов дерева ---
    const tblHost = root.querySelector('#sp-tbl');
    tblHost.addEventListener('click', (e) => {
      const tr = e.target.closest('tr.sp-has');
      if (!tr) return;
      const key = tr.dataset.key;
      salesState.expanded[key] = !salesState.expanded[key];
      salesPaintTable(tblHost, salesState.data);
    });
    // --- Инлайн-правка плана (ozon/wb): двойной клик по ячейке «План» листа. ---
    tblHost.addEventListener('dblclick', (e) => {
      const span = e.target.closest('.sp-plan-edit');
      if (!span || span.querySelector('input')) return;
      const art = span.dataset.art;
      const month = Number(span.dataset.month);
      const cur = Number(span.dataset.plan) || 0;
      const wasEmpty = span.classList.contains('sp-plan-empty');
      const oldHtml = span.innerHTML;
      const oldCls = span.className;
      const inp = document.createElement('input');
      inp.type = 'number'; inp.min = '0'; inp.step = '1';
      inp.className = 'sp-plan-input';
      // Пустую (прочерк) ячейку открываем с пустым полем — чтобы сразу вводить число.
      inp.value = wasEmpty ? '' : cur;
      span.innerHTML = '';
      span.appendChild(inp);
      inp.focus(); inp.select();
      let done = false;
      const cancel = () => { if (done) return; done = true; span.className = oldCls; span.innerHTML = oldHtml; };
      const save = async () => {
        if (done) return;
        const raw = inp.value.trim();
        const val = raw === '' ? 0 : Number(raw);
        if (isNaN(val) || val < 0) { cancel(); return; }
        if (val === cur) { cancel(); return; }
        done = true;
        try {
          const res = await API.salesPlanCell({
            seller_article: art, marketplace: salesMpParam(),
            year: salesState.year, month, plan_qty: val,
          });
          applyPlanCellUpdate(art, month, res);
        } catch (err) {
          if (App && App.toast) App.toast(err.message || 'Не удалось сохранить план', 'err');
          salesPaintTable(tblHost, salesState.data);
        }
      };
      inp.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') { ev.preventDefault(); inp.blur(); }
        else if (ev.key === 'Escape') { ev.preventDefault(); const h = oldHtml, c = oldCls; done = true; span.className = c; span.innerHTML = h; }
      });
      inp.addEventListener('blur', save);
    });
    // --- Выпадающий список «Настройки»: пять чекбоксов отображения убраны
    // с панели фильтров в поповер, чтобы шапка влезала на экран ноутбука.
    // Сами id чекбоксов не менялись — обработчики выше работают как раньше. ---
    const spSetWrap = root.querySelector('#sp-settings');
    const spSetBtn = root.querySelector('#sp-settings-btn');
    const spSetPop = root.querySelector('#sp-settings-pop');
    if (spSetWrap && spSetBtn && spSetPop) {
      const closeSet = () => { spSetPop.hidden = true; spSetBtn.setAttribute('aria-expanded', 'false'); };
      spSetBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const open = spSetPop.hidden;
        spSetPop.hidden = !open;
        spSetBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
      // Клик внутри поповера не закрывает его — иначе нельзя отметить
      // несколько галочек подряд.
      spSetPop.addEventListener('click', (e) => e.stopPropagation());
      document.addEventListener('click', closeSet);
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeSet(); });
    }
    // --- Кнопка «Развернуть всё» — как в РНП заказы: раскрывает все узлы дерева. ---
    const spExpand = root.querySelector('#sp-expand');
    if (spExpand) {
      spExpand.addEventListener('click', () => {
        const walk = (node) => {
          if (node && node.children && node.children.length) {
            salesState.expanded[node.key] = true;
            node.children.forEach(walk);
          }
        };
        if (salesState.data && salesState.data.tree) walk(salesState.data.tree);
        salesPaintTable(tblHost, salesState.data);
      });
    }
    // --- Кнопка «Свернуть всё»: схлопывает всё дерево до корня «Итоги»
    // (без перезапроса — только перерисовка таблицы), как в разделе РНП. ---
    const spCollapse = root.querySelector('#sp-collapse');
    if (spCollapse) {
      spCollapse.addEventListener('click', () => {
        const rootKey = salesState.data && salesState.data.tree ? salesState.data.tree.key : null;
        salesState.expanded = {};
        if (rootKey) salesState.expanded[rootKey] = true;
        salesPaintTable(tblHost, salesState.data);
      });
    }

    // --- Выгрузка плана (один МП) ---
    const expBtn = root.querySelector('#sp-export');
    if (expBtn) {
      expBtn.addEventListener('click', async () => {
        const prev = expBtn.innerHTML;
        expBtn.disabled = true; expBtn.innerHTML = '<span class="btn-spin"></span>Выгружаю…';
        try {
          const url = API.salesPlanExportUrl({ marketplace: salesMpParam(), year: salesState.year });
          const resp = await fetch(url, { headers: { Authorization: 'Bearer ' + API.getToken() } });
          if (!resp.ok) throw new Error('HTTP ' + resp.status);
          const cd = resp.headers.get('Content-Disposition') || '';
          let fname = `plan_${salesMpParam()}_${salesState.year}.xlsx`;
          const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
          if (m) { try { fname = decodeURIComponent(m[1]); } catch (e) { fname = m[1]; } }
          const blob = await resp.blob();
          const dlUrl = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = dlUrl; a.download = fname;
          document.body.appendChild(a); a.click();
          setTimeout(() => { URL.revokeObjectURL(dlUrl); a.remove(); }, 1500);
          App.toast('Шаблон плана выгружен', 'ok');
        } catch (e) { App.toast('Ошибка выгрузки: ' + e.message, 'err'); }
        finally { expBtn.disabled = false; expBtn.innerHTML = prev; }
      });
    }
    // --- Выгрузка сводного отчёта (вкладка «Сводная») ---
    const repBtn = root.querySelector('#sp-report-export');
    if (repBtn) {
      repBtn.addEventListener('click', async () => {
        const prev = repBtn.innerHTML;
        repBtn.disabled = true; repBtn.innerHTML = '<span class="btn-spin"></span>Формирую…';
        try {
          // Фильтры экрана (статус/менеджер) учитываются в отчёте.
          const p = { marketplace: salesMpParam(), year: salesState.year };
          if (salesState.status) p.status = salesState.status;
          if (salesState.manager) p.manager = salesState.manager;
          const url = API.salesReportExportUrl(p);
          const resp = await fetch(url, { headers: { Authorization: 'Bearer ' + API.getToken() } });
          if (!resp.ok) throw new Error('HTTP ' + resp.status);
          const cd = resp.headers.get('Content-Disposition') || '';
          let fname = `otchet_prodazhi_${salesMpParam()}_${salesState.year}.xlsx`;
          const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
          if (m) { try { fname = decodeURIComponent(m[1]); } catch (e) { fname = m[1]; } }
          const blob = await resp.blob();
          const dlUrl = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = dlUrl; a.download = fname;
          document.body.appendChild(a); a.click();
          setTimeout(() => { URL.revokeObjectURL(dlUrl); a.remove(); }, 1500);
          App.toast('Отчёт выгружен', 'ok');
        } catch (e) { App.toast('Ошибка выгрузки отчёта: ' + e.message, 'err'); }
        finally { repBtn.disabled = false; repBtn.innerHTML = prev; }
      });
    }
    // --- Загрузка плана (один МП, admin) ---
    const impBtn = root.querySelector('#sp-import-btn');
    const impFile = root.querySelector('#sp-import-file');
    const impRes = root.querySelector('#sp-imp-result');
    if (impBtn && impFile) {
      impBtn.addEventListener('click', () => impFile.click());
      impFile.addEventListener('change', async () => {
        const f = impFile.files && impFile.files[0];
        if (!f) return;
        const prev = impBtn.innerHTML;
        impBtn.disabled = true; impBtn.innerHTML = '<span class="btn-spin"></span>Загружаю…';
        if (impRes) impRes.innerHTML = '';
        try {
          const r = await API.salesPlanImport(f, { marketplace: salesMpParam(), year: salesState.year });
          if (API.cacheClear) API.cacheClear('/api/sales');
          if (impRes) impRes.innerHTML = `<div class="note ok" style="margin-bottom:12px; padding:10px 14px; border-radius:8px; background:rgba(46,204,113,.12); border:1px solid rgba(46,204,113,.4);">
            <b>План загружен.</b> Артикулов: <b>${r.articles || 0}</b>, записей плана: <b>${r.upserted || 0}</b>.</div>`;
          App.toast('План загружен: записей ' + (r.upserted || 0), 'ok');
          App.renderView();
        } catch (e) {
          if (impRes) impRes.innerHTML = `<div class="note err" style="margin-bottom:12px; padding:10px 14px; border-radius:8px; background:rgba(192,57,43,.12); border:1px solid rgba(192,57,43,.4); color:var(--danger,#c0392b);">Ошибка загрузки плана: ${U.esc(e.message)}</div>`;
          App.toast('Ошибка загрузки плана: ' + e.message, 'err');
        } finally { impBtn.disabled = false; impBtn.innerHTML = prev; impFile.value = ''; }
      });
    }
  }

  // Липкая панель управления «Продаж» может переноситься на 2 строки на узком
  // экране — измеряем реальную высоту и пишем в --sp-toolbar-h, чтобы шапка
  // таблицы прилипала ровно под ней, а не за ней.
  function syncSalesToolbarHeight() {
    requestAnimationFrame(() => {
      const r = document.documentElement;
      const tb = document.querySelector('.sp-toolbar');
      r.style.setProperty('--sp-toolbar-h', tb ? Math.round(tb.getBoundingClientRect().height) + 'px' : '0px');
      // Смещение верха таблицы от верха окна (appbar + заголовок + липкая панель).
      // Прибавляем текущий scrollY, чтобы получить смещение при scrollY=0.
      const wrap = document.querySelector('.sp-wrap');
      if (wrap) {
        const top = Math.round(wrap.getBoundingClientRect().top + window.scrollY);
        r.style.setProperty('--sp-wrap-top', top + 'px');
      }
    });
  }
  // При ресайзе окна панель может перенестись — пересчитываем (один раз на модуль).
  if (!window.__spToolbarResizeBound) {
    window.__spToolbarResizeBound = true;
    window.addEventListener('resize', () => syncSalesToolbarHeight());
  }

  async function loadSkus(state) {
    const el = document.getElementById('sku-tbl');
    if (!el) return;
    el.innerHTML = '<div class="loader"><span class="spinner"></span></div>';
    const rows = await API.bySku({ year: state.sel.year, week: state.sel.week, marketplace: skuState.mp, sort: skuState.sort, search: skuState.search || null, limit: 1000 });
    const items = rows.filter(r => r.row_type === 'Товар');
    if (!items.length) { el.innerHTML = '<div class="empty">Ничего не найдено.</div>'; return; }
    let h = '<div class="tbl-wrap"><table class="tbl"><thead><tr>'
      + '<th class="l"></th><th class="l">Артикул / Товар</th><th>МП</th><th>Категория</th>'
      + '<th>Выручка</th><th>Прибыль</th><th>Маржа</th><th>Продаж</th></tr></thead><tbody>';
    items.forEach((r, i) => {
      h += `<tr class="sku-main" data-idx="${i}" data-sa="${U.esc(r.seller_article)}" data-mp="${r.marketplace === 'Ozon' ? 'ozon' : 'wb'}" style="cursor:pointer">
        <td class="l"><span class="expander">▸</span></td>
        <td class="l"><b>${U.esc(r.seller_article)}</b><br/><span class="muted">${U.esc((r.item_name || '').slice(0, 56))}</span></td>
        <td>${U.mpPill(r.marketplace)}</td>
        <td class="l muted">${U.esc(r.category_l3 || '—')}</td>
        <td class="num">${U.fmtMoney(r.revenue)}</td>
        <td class="num">${U.fmtMoney(r.profit)}</td>
        <td class="num ${U.marginClass(r.margin_pct)}">${U.fmtPct(r.margin_pct)}</td>
        <td class="num">${U.fmtNum(r.sales_qty)}</td></tr>
        <tr class="drv-row hidden" id="drv-${i}"><td colspan="8"></td></tr>`;
    });
    h += '</tbody></table></div>';
    el.innerHTML = h;
    el.querySelectorAll('tr.sku-main').forEach(tr => {
      tr.addEventListener('click', async () => {
        const idx = tr.dataset.idx;
        const drvRow = document.getElementById('drv-' + idx);
        const exp = tr.querySelector('.expander');
        if (!drvRow.classList.contains('hidden')) { drvRow.classList.add('hidden'); exp.textContent = '▸'; return; }
        exp.textContent = '▾';
        drvRow.classList.remove('hidden');
        const cell = drvRow.firstElementChild;
        cell.innerHTML = '<div class="loader"><span class="spinner"></span></div>';
        try {
          const d = await API.skuDrivers({ seller_article: tr.dataset.sa, year: state.sel.year, week: state.sel.week, marketplace: tr.dataset.mp });
          cell.innerHTML = renderDrivers(d);
        } catch (e) { cell.innerHTML = `<div class="empty">Ошибка: ${U.esc(e.message)}</div>`; }
      });
    });
  }

  function renderDrivers(d) {
    if (!d.found) return '<div class="empty">Нет данных по товару за неделю.</div>';
    const maxAbs = Math.max(...d.drivers.map(x => Math.abs(x.value)), 1);
    const rows = d.drivers.map(x => {
      const w = Math.min(100, Math.abs(x.value) / maxAbs * 100);
      const cls = x.sign === 'plus' ? 'plus' : 'minus';
      return `<div class="drv-bar-row">
        <span class="nm">${U.esc(x.label)}</span>
        <span class="bar"><span class="${cls}" style="width:${w}%"></span></span>
        <span class="val">${U.fmtMoney(x.value)}</span>
        <span class="pct">${U.fmtPct(x.pct)}</span>
      </div>`;
    }).join('');
    return `<div class="drv-box">
      <div class="kpi-grid" style="margin-bottom:14px;">
        <div class="kpi"><div class="label">Выручка</div><div class="value" style="font-size:20px">${U.fmtMoney(d.revenue)}</div></div>
        <div class="kpi"><div class="label">Чистая прибыль</div><div class="value" style="font-size:20px">${U.fmtMoney(d.profit)}</div></div>
        <div class="kpi accent"><div class="label">Маржинальность</div><div class="value ${U.marginClass(d.margin_pct)}" style="font-size:20px">${U.fmtPct(d.margin_pct)}</div></div>
        <div class="kpi"><div class="label">Продаж, шт</div><div class="value" style="font-size:20px">${U.fmtNum(d.sales_qty)}</div></div>
      </div>
      <div style="font-size:12px; color:var(--text-dim); margin-bottom:8px;">Что влияет на маржу (доля от выручки):</div>
      ${rows}
    </div>`;
  }

  // ============================================================
  // 6. СПРАВОЧНИК (редактор категорий У1/У2/У3 + статус)
  // ============================================================
  // undistArticles: список артикулов, помеченных РНП как нераспределённые
  // для конкретного маркетплейса. Если задан — справочник показывает РОВНО
  // эти товары, и их число совпадает со счётчиком «Требуют распределения» в РНП.
  // undistMp: подпись маркетплейса для плашки активного фильтра.
  // mp: активный маркетплейс справочника ('ozon' | 'wb').
  // Статус и менеджер раздельные по МП; категории — общие по артикулу.
  const catState = { mp: 'ozon', search: '', only_unc: false, tree: null, undistArticles: null, undistMp: '' };
  // Каноничное название маркетплейса для API (как в fact_weekly).
  function catMpName() { return catState.mp === 'wb' ? 'Wildberries' : (catState.mp === 'yandex' ? 'Yandex' : 'Ozon'); }

  async function catalog(root, ctl, state) {
    if (!catState.tree) catState.tree = await API.catalogTree({ marketplace: catMpName() });
    const isAdmin = App && App.state && App.state.user && App.state.user.role === 'admin';
    // Переключатель маркетплейсов (как в РНП): статус/менеджер раздельные по МП.
    const subtabs = [['ozon', 'OZON', 'ozon'], ['wb', 'Wildberries', 'wb'], ['yandex', 'Yandex', 'yandex']]
      .map(([id, label, c]) => `<button class="subtab ${c}${catState.mp === id ? ' active' : ''}" data-cmp="${id}">${label}</button>`)
      .join('');
    // Легенда статусов: фирменные плашки (те же CSS-классы, что и у селекторов)
    // + пояснение. Помогает менеджерам единообразно классифицировать товар.
    const STATUS_LEGEND = [
      ['CORE', 'карточка товара находится в основной боевой матрице.'],
      ['NEW', 'новый товар, новинка или ещё не вышел на плановые показатели продаж.'],
      ['Closeout', 'товар находится в распродаже с целью вывода из продаж.'],
      ['Sale', 'плановая распродажа товара (регулярное промо, профицит товаров на складах).'],
      ['?', 'товар требует внимания или обсуждения на предстоящем собрании.'],
      ['-', 'товар выведен из продаж/распродан; скрывается из отчётов, если за период не было движений.'],
    ];
    const legendHtml = `
      <div class="cat-legend">
        <div class="cat-legend-title">Обозначения статусов</div>
        <div class="cat-legend-list">
          ${STATUS_LEGEND.map(([s, t]) =>
            `<div class="cat-legend-item"><span class="status-badge ${stClass(s)}">${U.esc(s)}</span><span class="cat-legend-text">${U.esc(t)}</span></div>`).join('')}
        </div>
        <div class="cat-legend-note">Иерархия товаров является общей для двух маркетплейсов. Если поменять категорию в справочнике в одном маркетплейсе, она автоматически перезапишется в другом. Индивидуальными для каждого маркетплейса являются только поля «Статус» и «Менеджер».</div>
      </div>`;
    root.innerHTML = `
      <div class="card">
        <h3>Справочник товаров</h3>
        <div class="subtabs" id="cat-subtabs" style="margin-bottom:14px;">${subtabs}</div>
        <div class="ctl" style="margin-bottom:14px; gap:14px; flex-wrap:wrap;">
          <input class="input-sm" id="cat-search" placeholder="Поиск по артикулу…" value="${U.esc(catState.search)}">
          <label class="ctl" style="cursor:pointer;"><input type="checkbox" id="cat-unc" ${catState.only_unc ? 'checked' : ''}> только нераспределённые</label>
          <span style="flex:1"></span>
          ${isAdmin ? `<button class="btn btn-primary" id="cat-add-btn" style="width:auto; min-width:170px; padding:8px 14px; text-align:center;">➕ Добавить товар</button>` : ''}
          <button class="btn" id="cat-export" style="width:auto; min-width:170px; padding:8px 14px; text-align:center;">⬇️ Выгрузить Excel</button>
          ${isAdmin ? `<button class="btn" id="cat-import-btn" style="width:auto; min-width:170px; padding:8px 14px; text-align:center;">⬆️ Загрузить Excel</button>
          <input type="file" id="cat-import-file" accept=".xlsx,.xls" class="hidden" style="display:none">` : ''}
        </div>
        ${legendHtml}
        <div id="cat-add-form"></div>
        <div id="cat-imp-result"></div>
        <div id="cat-list"><div class="loader"><span class="spinner"></span></div></div>
      </div>
    `;
    const searchEl = root.querySelector('#cat-search');
    const uncEl = root.querySelector('#cat-unc');
    let deb = null;
    // Любое ручное действие (поиск/галочка) сбрасывает фильтр «нераспределённые из РНП».
    searchEl.addEventListener('input', () => { clearTimeout(deb); deb = setTimeout(() => { catState.undistArticles = null; catState.undistMp = ''; catState.search = searchEl.value.trim(); loadCatalog(); }, 350); });
    uncEl.addEventListener('change', () => { catState.undistArticles = null; catState.undistMp = ''; catState.only_unc = uncEl.checked; loadCatalog(); });

    // --- Переключение маркетплейса ---
    // Смена МП полностью перестраивает справочник: другой набор
    // статусов/менеджеров и другой список артикулов.
    root.querySelectorAll('#cat-subtabs .subtab').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.dataset.cmp;
        if (id === catState.mp) return;
        catState.mp = id;
        catState.tree = null;          // дерево фильтров зависит от МП
        catState.undistArticles = null; // сброс фильтра «нераспределённые из РНП»
        catState.undistMp = '';
        App.renderView();              // полный перерендер (перечитает catalogTree для нового МП)
      });
    });

    // --- Выгрузка в Excel ---
    const expBtn = root.querySelector('#cat-export');
    if (expBtn) {
      expBtn.addEventListener('click', async () => {
        const prevHtml = expBtn.innerHTML;
        expBtn.disabled = true; expBtn.innerHTML = '<span class="btn-spin"></span>Выгружаю…';
        try {
          const url = API.catalogExportUrl({ marketplace: catMpName() });
          const resp = await fetch(url, { headers: { Authorization: 'Bearer ' + API.getToken() } });
          if (!resp.ok) throw new Error('HTTP ' + resp.status);
          const cd = resp.headers.get('Content-Disposition') || '';
          let fname = 'spravochnik.xlsx';
          const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
          if (m) { try { fname = decodeURIComponent(m[1]); } catch (e) { fname = m[1]; } }
          const blob = await resp.blob();
          const dlUrl = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = dlUrl; a.download = fname;
          document.body.appendChild(a); a.click();
          setTimeout(() => { URL.revokeObjectURL(dlUrl); a.remove(); }, 1500);
          App.toast('Файл выгружен', 'ok');
        } catch (e) {
          App.toast('Ошибка выгрузки: ' + e.message, 'err');
        } finally {
          expBtn.disabled = false; expBtn.innerHTML = prevHtml;
        }
      });
    }

    // --- Загрузка из Excel (admin) ---
    const impBtn = root.querySelector('#cat-import-btn');
    const impFile = root.querySelector('#cat-import-file');
    const impRes = root.querySelector('#cat-imp-result');
    if (impBtn && impFile) {
      impBtn.addEventListener('click', () => impFile.click());
      impFile.addEventListener('change', async () => {
        const f = impFile.files && impFile.files[0];
        if (!f) return;
        const prevHtml = impBtn.innerHTML;
        impBtn.disabled = true; impBtn.innerHTML = '<span class="btn-spin"></span>Загружаю…';
        if (impRes) impRes.innerHTML = '';
        try {
          const r = await API.catalogImport(f, { marketplace: catMpName() });
          if (API.cacheClear) { API.cacheClear('/api/catalog'); API.cacheClear('/api/metrics/by_category'); API.cacheClear('/api/trend/category_matrix'); }
          catState.tree = await API.catalogTree({ marketplace: catMpName() });
          if (impRes) {
            const errBlock = (r.error_count && r.errors && r.errors.length)
              ? `<div style="margin-top:8px; color:var(--danger,#c0392b);">Ошибки (${r.error_count}):<ul style="margin:4px 0 0 18px;">${r.errors.map(e => `<li>${U.esc(String(e))}</li>`).join('')}</ul></div>`
              : '';
            impRes.innerHTML = `<div class="note ok" style="margin-bottom:14px; padding:12px 14px; border-radius:8px; background:rgba(46,204,113,.12); border:1px solid rgba(46,204,113,.4);">
              <b>Импорт завершён.</b> Обновлено: <b>${r.updated || 0}</b>, добавлено: <b>${r.inserted || 0}</b>, пропущено: <b>${r.skipped || 0}</b>.${errBlock}
            </div>`;
          }
          App.toast('Импорт: обновлено ' + (r.updated || 0) + ', добавлено ' + (r.inserted || 0), 'ok');
          await loadCatalog();
        } catch (e) {
          if (impRes) impRes.innerHTML = `<div class="note err" style="margin-bottom:14px; padding:12px 14px; border-radius:8px; background:rgba(192,57,43,.12); border:1px solid rgba(192,57,43,.4); color:var(--danger,#c0392b);">Ошибка импорта: ${U.esc(e.message)}</div>`;
          App.toast('Ошибка импорта: ' + e.message, 'err');
        } finally {
          impBtn.disabled = false; impBtn.innerHTML = prevHtml;
          impFile.value = '';
        }
      });
    }

    setupCatAddForm(root);

    await loadCatalog();
  }

  function catAddFormHtml() {
    const tree = catState.tree || {};
    const l1v = (tree.l1 || []);
    const l2v = [...new Set((tree.l2 || []).map(x => x.name))];
    const l3v = [...new Set((tree.l3 || []).map(x => x.name))];
    const mgrv = (tree.managers || []);
    const dl = (id, vals) => '<datalist id="' + id + '">' + vals.map(v => '<option value="' + U.esc(v) + '">').join('') + '</datalist>';
    const stSelHtml = catStatusSelectHtml(null).replace('c-st cat-status-sel', 'na-st cat-status-sel');
    return dl('adl-l1', l1v) + dl('adl-l2', l2v) + dl('adl-l3', l3v) + dl('adl-mgr', mgrv) +
      '<div class="cat-add-card">' +
        '<div class="cat-add-title">Новый товар — ' + U.esc(catMpName()) + '</div>' +
        '<div class="cat-add-grid">' +
          '<label class="cat-add-field"><span>Артикул продавца *</span><input class="input-sm" id="na-art" placeholder="напр. AGR-140" autocomplete="off"></label>' +
          '<label class="cat-add-field cat-add-wide"><span>Наименование</span><input class="input-sm" id="na-name" placeholder="необязательно" autocomplete="off"></label>' +
          '<label class="cat-add-field"><span>Уровень 1</span><input class="input-sm" id="na-l1" list="adl-l1" autocomplete="off"></label>' +
          '<label class="cat-add-field"><span>Уровень 2</span><input class="input-sm" id="na-l2" list="adl-l2" autocomplete="off"></label>' +
          '<label class="cat-add-field"><span>Уровень 3</span><input class="input-sm" id="na-l3" list="adl-l3" autocomplete="off"></label>' +
          '<label class="cat-add-field"><span>Статус</span>' + stSelHtml + '</label>' +
          '<label class="cat-add-field"><span>Менеджер</span><input class="input-sm" id="na-mgr" list="adl-mgr" autocomplete="off"></label>' +
          '<label class="cat-add-field cat-add-wide"><span>Ссылка на товар</span><input class="input-sm" id="na-url" type="url" placeholder="https://…" autocomplete="off"></label>' +
        '</div>' +
        '<div class="cat-add-actions">' +
          '<button class="btn btn-primary" id="na-save" style="width:auto; min-width:140px; padding:8px 16px;">Сохранить</button>' +
          '<button class="btn" id="na-cancel" style="width:auto; min-width:110px; padding:8px 16px;">Отмена</button>' +
          '<span class="cat-add-hint">Товар сразу появится в РНП и «Продажи, шт» с прочерками. Факт подтянется автоматически при загрузке отчёта с таким же артикулом.</span>' +
        '</div>' +
      '</div>';
  }

  function openCatAddForm(root, addBtn, addHost) {
    if (addHost.dataset.open === '1') {
      addHost.dataset.open = ''; addHost.innerHTML = '';
      addBtn.classList.remove('is-active');
      return;
    }
    addHost.dataset.open = '1';
    addBtn.classList.add('is-active');
    addHost.innerHTML = catAddFormHtml();
    const naSt = addHost.querySelector('.na-st');
    if (naSt) naSt.addEventListener('change', () => { naSt.className = 'input-sm na-st cat-status-sel ' + stClass(naSt.value); });
    const artEl = addHost.querySelector('#na-art');
    if (artEl) artEl.focus();
    const closeForm = () => { addHost.dataset.open = ''; addHost.innerHTML = ''; addBtn.classList.remove('is-active'); };
    addHost.querySelector('#na-cancel').addEventListener('click', closeForm);
    const saveBtn = addHost.querySelector('#na-save');
    const doSave = async () => {
      const art = (artEl.value || '').trim();
      if (!art) { App.toast('Укажите артикул', 'err'); artEl.focus(); return; }
      const body = {
        marketplace: catMpName(),
        sample_name: (addHost.querySelector('#na-name').value || '').trim() || null,
        category_l1: (addHost.querySelector('#na-l1').value || '').trim() || null,
        category_l2: (addHost.querySelector('#na-l2').value || '').trim() || null,
        category_l3: (addHost.querySelector('#na-l3').value || '').trim() || null,
        status: stToApi(naSt ? naSt.value : null),
        manager: (addHost.querySelector('#na-mgr').value || '').trim() || null,
        product_url: (addHost.querySelector('#na-url').value || '').trim() || null,
      };
      saveBtn.disabled = true; saveBtn.innerHTML = '<span class="btn-spin"></span>Сохраняю…';
      try {
        await API.catalogUpdate(art, body);
        if (API.cacheClear) { API.cacheClear('/api/catalog'); API.cacheClear('/api/metrics/by_category'); API.cacheClear('/api/trend/category_matrix'); API.cacheClear('/api/rnp'); API.cacheClear('/api/sales'); API.cacheClear('/api/abc'); }
        // новый товар/статус влияет на дашборд ABC — сбросить его памятный кэш
        if (window.ABCDash && window.ABCDash.invalidate) window.ABCDash.invalidate();
        catState.tree = await API.catalogTree({ marketplace: catMpName() });
        App.toast('Товар добавлен: ' + art, 'ok');
        closeForm();
        catState.search = '';
        const se = root.querySelector('#cat-search'); if (se) se.value = '';
        await loadCatalog();
      } catch (e) {
        App.toast('Ошибка: ' + e.message, 'err');
        saveBtn.disabled = false; saveBtn.innerHTML = 'Сохранить';
      }
    };
    saveBtn.addEventListener('click', doSave);
    artEl.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); doSave(); } });
  }

  function setupCatAddForm(root) {
    const addBtn = root.querySelector('#cat-add-btn');
    const addHost = root.querySelector('#cat-add-form');
    if (!addBtn || !addHost) return;
    addBtn.addEventListener('click', () => openCatAddForm(root, addBtn, addHost));
  }

  async function loadCatalog() {
    const el = document.getElementById('cat-list');
    if (!el) return;
    el.innerHTML = '<div class="loader"><span class="spinner"></span></div>';
    // Режим «нераспределённые из РНП»: показываем РОВНО те артикулы,
    // что пришли со счётчика РНП. Грузим весь справочник и фильтруем локально.
    const undistMode = Array.isArray(catState.undistArticles);
    let items = await API.catalogItems({
      marketplace: catMpName(),
      search: undistMode ? null : (catState.search || null),
      only_uncategorized: undistMode ? false : catState.only_unc,
      limit: 2000,
    });
    if (undistMode) {
      // Сравнение артикулов без учёта регистра/лишних пробелов: справочник хранит
      // артикулы в каноне (ВЕРХНИЙ регистр), а РНП отдаёт их из фактов в исходном
      // написании. Нормализуем обе стороны одинаково, иначе точное сравнение строк
      // не находит совпадений и справочник выглядит пустым.
      const canon = (s) => (s == null ? '' : String(s).trim().replace(/\s+/g, ' ').toUpperCase());
      const set = new Set(catState.undistArticles.map(canon));
      items = items.filter(it => set.has(canon(it.seller_article)));
    }
    if (!items.length) { el.innerHTML = '<div class="empty">Ничего не найдено.</div>'; return; }
    const tree = catState.tree;
    const datalist = (id, vals) => `<datalist id="${id}">${vals.map(v => `<option value="${U.esc(v)}">`).join('')}</datalist>`;
    const l1vals = tree.l1 || [];
    const l2vals = [...new Set((tree.l2 || []).map(x => x.name))];
    const l3vals = [...new Set((tree.l3 || []).map(x => x.name))];
    const stvals = tree.statuses || [];
    const mgrvals = tree.managers || [];
    let h = datalist('dl-l1', l1vals) + datalist('dl-l2', l2vals) + datalist('dl-l3', l3vals) + datalist('dl-st', stvals) + datalist('dl-mgr', mgrvals);
    // Плашка активного фильтра «нераспределённые из РНП» с кнопкой сброса.
    if (undistMode) {
      h += `<div class="cat-undist-bar">
        Фильтр: нераспределённые из РНП${catState.undistMp ? ' · ' + U.esc(catState.undistMp) : ''} — <b>${items.length}</b>
        <button class="btn-link" id="cat-undist-reset" type="button">Сбросить</button>
      </div>`;
    }
    h += '<div class="tbl-wrap"><table class="tbl"><thead><tr><th class="l">Артикул</th><th class="l">Уровень 1</th><th class="l">Уровень 2</th><th class="l">Уровень 3</th><th class="l">Статус</th><th class="l">Менеджер</th><th class="l">Ссылка на товар</th><th></th></tr></thead><tbody>';
    items.forEach(it => {
      const sa = U.esc(it.seller_article);
      h += `<tr data-sa="${sa}">
        <td class="l"><b>${sa}</b></td>
        <td class="l"><input class="input-sm c-l1" list="dl-l1" style="width:150px" value="${U.esc(it.category_l1 || '')}"></td>
        <td class="l"><input class="input-sm c-l2" list="dl-l2" style="width:150px" value="${U.esc(it.category_l2 || '')}"></td>
        <td class="l"><input class="input-sm c-l3" list="dl-l3" style="width:150px" value="${U.esc(it.category_l3 || '')}"></td>
        <td class="l">${catStatusSelectHtml(it.status)}</td>
        <td class="l"><input class="input-sm c-mgr" list="dl-mgr" style="width:130px" value="${U.esc(it.manager || '')}"></td>
        <td class="l"><input class="input-sm c-url" type="url" placeholder="https://…" style="width:220px" value="${U.esc(it.product_url || '')}"></td>
        <td><button class="btn c-save" style="width:auto; padding:6px 12px;">Сохранить</button></td>
      </tr>`;
    });
    h += '</tbody></table></div>';
    el.innerHTML = h;
    // Сброс фильтра «нераспределённые из РНП» → возврат к полному справочнику.
    const undResetBtn = el.querySelector('#cat-undist-reset');
    if (undResetBtn) {
      undResetBtn.addEventListener('click', () => {
        catState.undistArticles = null;
        catState.undistMp = '';
        loadCatalog();
      });
    }
    el.querySelectorAll('tr[data-sa]').forEach(tr => {
      const btn = tr.querySelector('.c-save');
      // перекрашивание селектора статуса при выборе (сохранение — по кнопке «Сохранить»)
      const stSel = tr.querySelector('.c-st');
      if (stSel) stSel.addEventListener('change', () => {
        stSel.className = 'input-sm c-st cat-status-sel ' + stClass(stSel.value);
      });
      btn.addEventListener('click', async () => {
        const body = {
          // категории общие для артикула, статус/менеджер — раздельно по маркетплейсу
          marketplace: catMpName(),
          category_l1: tr.querySelector('.c-l1').value.trim() || null,
          category_l2: tr.querySelector('.c-l2').value.trim() || null,
          category_l3: tr.querySelector('.c-l3').value.trim() || null,
          status: stToApi(tr.querySelector('.c-st').value),
          manager: tr.querySelector('.c-mgr').value.trim() || null,
          product_url: tr.querySelector('.c-url').value.trim() || null,
        };
        btn.disabled = true; btn.textContent = '…';
        try {
          await API.catalogUpdate(tr.dataset.sa, body);
          // изменили категории/статус — сброс кэша справочника и категорий
          if (API.cacheClear) { API.cacheClear('/api/catalog'); API.cacheClear('/api/metrics/by_category'); API.cacheClear('/api/trend/category_matrix'); API.cacheClear('/api/abc'); }
          // статус/категории влияют на дашборд ABC — сбросить его памятный кэш
          if (window.ABCDash && window.ABCDash.invalidate) window.ABCDash.invalidate();
          catState.tree = await API.catalogTree({ marketplace: catMpName() });
          App.toast('Сохранено: ' + tr.dataset.sa, 'ok');
          btn.textContent = '✓';
          setTimeout(() => { btn.textContent = 'Сохранить'; btn.disabled = false; }, 1200);
        } catch (e) {
          App.toast('Ошибка: ' + e.message, 'err');
          btn.textContent = 'Сохранить'; btn.disabled = false;
        }
      });
    });
  }

  // ============================================================
  // 7. ЗАГРУЗКА (форма Excel + история)
  // ============================================================
  async function upload(root, ctl, state) {
    root.innerHTML = `
      <div class="up-hint">
        Подходит как еженедельный, так и ежемесячный формат отчётов MPPROFIT — система определит тип автоматически.
      </div>
      <div class="grid-2">
        <div class="card">
          <h3>Ozon — загрузка отчёта</h3>
          <div class="upload-zone" id="uz-ozon">
            <div style="font-size:28px;">⬆️</div>
            <div>Перетащите Excel сюда или <a href="#" id="pick-ozon">выберите файл</a></div>
            <div class="muted" style="font-size:12px;margin-top:6px;">формат MPPROFIT, .xlsx · неделя или месяц</div>
            <input type="file" id="file-ozon" accept=".xlsx,.xls" class="hidden">
          </div>
          <label class="fact-date-row">Факт по дату (для месячного отчёта):
            <input type="date" id="fact-date-ozon" class="input-sm">
          </label>
          <div class="muted fact-date-hint">Нужно только для месячного отчёта — до какого числа собран факт. Влияет на прогноз выполнения плана. Пусто — месяц считается полным.</div>
        </div>
        <div class="card">
          <h3>Wildberries — загрузка отчёта</h3>
          <div class="upload-zone" id="uz-wb">
            <div style="font-size:28px;">⬆️</div>
            <div>Перетащите Excel сюда или <a href="#" id="pick-wb">выберите файл</a></div>
            <div class="muted" style="font-size:12px;margin-top:6px;">формат MPPROFIT, .xlsx · неделя или месяц</div>
            <input type="file" id="file-wb" accept=".xlsx,.xls" class="hidden">
          </div>
          <label class="fact-date-row">Факт по дату (для месячного отчёта):
            <input type="date" id="fact-date-wb" class="input-sm">
          </label>
          <div class="muted fact-date-hint">Нужно только для месячного отчёта — до какого числа собран факт. Влияет на прогноз выполнения плана. Пусто — месяц считается полным.</div>
        </div>
      </div>
      <div class="card">
        <div class="up-hist-head">
          <h3 style="margin:0;">Журнал загрузок</h3>
          <div class="seg" id="hist-filter">
            <button class="seg-btn active" data-kind="unit_econ">Все</button>
            <button class="seg-btn" data-kind="weekly">Неделя</button>
            <button class="seg-btn" data-kind="monthly">Месяц</button>
          </div>
        </div>
        <div id="up-history"><div class="loader"><span class="spinner"></span></div></div>
      </div>
    `;
    setupUploadZone(root, 'ozon');
    setupUploadZone(root, 'wb');
    // фильтр журнала по типу
    const fbar = root.querySelector('#hist-filter');
    if (fbar) {
      fbar.addEventListener('click', async (e) => {
        const btn = e.target.closest('.seg-btn');
        if (!btn) return;
        fbar.querySelectorAll('.seg-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        await loadHistory(btn.dataset.kind);
      });
    }
    await loadHistory('unit_econ');
  }

  function setupUploadZone(root, mp) {
    const zone = root.querySelector('#uz-' + mp);
    if (!zone) return;
    const input = root.querySelector('#file-' + mp);
    const pick = root.querySelector('#pick-' + mp);
    if (pick) pick.addEventListener('click', (e) => { e.preventDefault(); input.click(); });
    input.addEventListener('change', () => { if (input.files[0]) doUpload(mp, input.files[0], zone); });
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('drag'); }));
    zone.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) doUpload(mp, f, zone); });
  }

  async function doUpload(mp, file, zone) {
    // Чистим плашки от ПРЕДЫДУЩЕЙ загрузки, чтобы старое сообщение
    // не «впеклось» в orig и не висело после новой корректной загрузки.
    zone.querySelectorAll('.up-warn, .note.err').forEach(el => el.remove());
    const orig = zone.innerHTML;
    let warnHtml = '';   // предупреждение о пропущенных строках (успех)
    let errHtml  = '';   // понятная ошибка (неудача)
    // «Факт по дату» (необязательно) — относится к месячному отчёту.
    const fdEl = document.getElementById('fact-date-' + mp);
    const factDate = fdEl && fdEl.value ? fdEl.value : null;
    zone.innerHTML = '<div class="loader"><span class="spinner"></span><div style="margin-top:8px">Загрузка ' + U.esc(file.name) + '…</div></div>';
    try {
      const res = await API.uploadReport(mp, file, factDate);
      // Система сама определила тип — показываем распознанный период.
      // Пример: «✓ Ozon, месячный отчёт, Май 2026»
      const lbl = res.kind_label || (res.period_kind === 'monthly' ? 'месячный отчёт' : 'недельный отчёт');
      const hum = res.period_human ? ', ' + res.period_human : '';
      // Есть предупреждение о пропущенных «мусорных» строках — покажем отдельно.
      if (res.warning) {
        App.toast('✓ Загружено с предупреждением: ' + res.warning, 'warn');
        warnHtml = '<div class="up-warn">⚠ ' + U.esc(res.warning) + '</div>';
      } else {
        App.toast('✓ ' + (res.marketplace || mp) + ', ' + lbl + hum + ' · строк: ' + U.fmtNum(res.rows_loaded), 'ok');
      }
      // новые данные — полный сброс кэша
      if (API.cacheClear) { API.cacheClear(); }
      // сбросить и памятный кэш дашборда ABC (иначе покажет старые данные)
      if (window.ABCDash && window.ABCDash.invalidate) window.ABCDash.invalidate();
      App.state.weeks = await API.weeks();
      if (App.state.weeks.length && !App.state.sel) App.state.sel = { year: App.state.weeks[0].year, week: App.state.weeks[0].week };
    } catch (e) {
      // Понятное сообщение об ошибке (detail с бэкенда) — и toast, и плашка в зоне.
      App.toast('Ошибка загрузки: ' + e.message, 'err');
      errHtml = '<div class="note err" style="margin-top:10px;padding:9px 12px;font-size:12.5px;line-height:1.4">'
        + '✖ Отчёт не загружен. ' + U.esc(e.message) + '</div>';
    } finally {
      zone.innerHTML = orig;
      setupUploadZone(document.getElementById('view-root'), mp);
      // Предупреждение/ошибку показываем под зоной загрузки (видно дольше toast).
      if (warnHtml || errHtml) { zone.insertAdjacentHTML('beforeend', warnHtml + errHtml); }
      // сохраняем текущий фильтр журнала
      const af = document.querySelector('#hist-filter .seg-btn.active');
      await loadHistory(af ? af.dataset.kind : 'all');
    }
  }

  // Зона загрузки ДНЕВНОГО API-файла Ozon (отдельный эндпоинт /api/upload/ozon_daily).
  function setupOzDayZone(root) {
    const zone = root.querySelector('#uz-ozday');
    if (!zone) return;
    const input = root.querySelector('#file-ozday');
    const pick = root.querySelector('#pick-ozday');
    if (pick) pick.addEventListener('click', (e) => { e.preventDefault(); input.click(); });
    input.addEventListener('change', () => { if (input.files[0]) doUploadOzDay(input.files[0], zone); });
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('drag'); }));
    zone.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) doUploadOzDay(f, zone); });
  }

  async function doUploadOzDay(file, zone) {
    zone.querySelectorAll('.up-warn, .note.err').forEach(el => el.remove());
    const orig = zone.innerHTML;
    let warnHtml = '', errHtml = '';
    zone.innerHTML = '<div class="loader"><span class="spinner"></span><div style="margin-top:8px">Загрузка ' + U.esc(file.name) + '…</div></div>';
    try {
      const res = await API.uploadOzonDaily(file);
      const dates = (res.dates || []);
      const period = res.period_text || (dates.length ? dates[0] + '…' + dates[dates.length - 1] : '');
      App.toast('✓ Ozon дневной · строк: ' + U.fmtNum(res.rows_loaded) + (period ? ' · ' + period : ''), 'ok');
      const unm = (res.unmatched || []);
      if (unm.length) {
        warnHtml = '<div class="up-warn">⚠ Нераспределённых артикулов: ' + unm.length + ' — заполните группы/статус/менеджера в Справочнике.</div>';
      }
      if (API.cacheClear) { API.cacheClear('/api/rnp_sales'); API.cacheClear('/api/catalog'); }
    } catch (e) {
      App.toast('Ошибка загрузки: ' + e.message, 'err');
      errHtml = '<div class="note err" style="margin-top:10px;padding:9px 12px;font-size:12.5px;line-height:1.4">✖ Файл не загружен. ' + U.esc(e.message) + '</div>';
    } finally {
      zone.innerHTML = orig;
      setupOzDayZone(document.getElementById('view-root'));
      if (warnHtml || errHtml) { zone.insertAdjacentHTML('beforeend', warnHtml + errHtml); }
      // Обновляем тот журнал, который сейчас на экране: старый (раздел «Загрузка»)
      // или новый (подраздел «Загрузка данных» в РНП продажи).
      const af = document.querySelector('#hist-filter .seg-btn.active');
      if (af) await loadHistory(af.dataset.kind);
      if (document.getElementById('uphist-ozon')) await loadRnpsUploadHistory();
    }
  }

  // Зона загрузки отчёта РЕКЛАМЫ Ozon (эндпоинт /api/upload/ozon_ads).
  function setupOzAdsZone(root) {
    const zone = root.querySelector('#uz-ozads');
    if (!zone) return;
    const input = root.querySelector('#file-ozads');
    const pick = root.querySelector('#pick-ozads');
    if (pick) pick.addEventListener('click', (e) => { e.preventDefault(); input.click(); });
    input.addEventListener('change', () => { if (input.files[0]) doUploadOzAds(input.files[0], zone); });
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('drag'); }));
    zone.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) doUploadOzAds(f, zone); });
  }

  async function doUploadOzAds(file, zone) {
    zone.querySelectorAll('.up-warn, .note.err').forEach(el => el.remove());
    const orig = zone.innerHTML;
    let warnHtml = '', errHtml = '';
    zone.innerHTML = '<div class="loader"><span class="spinner"></span><div style="margin-top:8px">Загрузка ' + U.esc(file.name) + '…</div></div>';
    try {
      const res = await API.uploadOzonAds(file);
      const period = res.period_text || '';
      const created = res.created || 0;
      let toastMsg = '✓ Ozon реклама · обновлено строк: ' + U.fmtNum(res.updated);
      if (created) toastMsg += ' · создано (реклама без продаж): ' + U.fmtNum(created);
      if (period) toastMsg += ' · ' + period;
      App.toast(toastMsg, 'ok');
      if (created) {
        warnHtml += '<div class="up-warn" style="background:#eef7ee;border-color:#c7e2c7;color:#2f6b2f">✓ Расход по товарам без продаж за эту дату учтён: создано строк ' + U.fmtNum(created) + ' (продажи=0, записан расход и CTR).</div>';
      }
      const unm = (res.unmatched || []);
      if (unm.length) {
        // unmatched — массив объектов {sku, name}. Артикула нет (товар
        // никогда не продавался), поэтому показываем SKU Озон + название.
        var unmList = unm.map(function(x){
          if (x && typeof x === 'object') {
            var nm = x.name ? (' — ' + U.esc(x.name)) : '';
            return '<li><b>' + U.esc(String(x.sku)) + '</b>' + nm + '</li>';
          }
          return '<li><b>' + U.esc(String(x)) + '</b></li>';
        }).join('');
        warnHtml += '<div class="up-warn">⚠ Товары без продаж — расход на рекламу не привязан (' + unm.length + '). '
          + 'У них нет ни одной продажи, поэтому ваш артикул системе неизвестен. Артикулы маркетплейса (SKU Ozon):'
          + '<ul style="margin:6px 0 0 18px;padding:0;font-size:12.5px;">' + unmList + '</ul></div>';
      }
      if (API.cacheClear) { API.cacheClear('/api/rnp_sales'); if (API.cacheClear) API.cacheClear('/api/abc'); }
      if (window.ABCDash && window.ABCDash.invalidate) window.ABCDash.invalidate();
    } catch (e) {
      App.toast('Ошибка загрузки: ' + e.message, 'err');
      errHtml = '<div class="note err" style="margin-top:10px;padding:9px 12px;font-size:12.5px;line-height:1.4">✖ Файл не загружен. ' + U.esc(e.message) + '</div>';
    } finally {
      zone.innerHTML = orig;
      setupOzAdsZone(document.getElementById('view-root'));
      if (warnHtml || errHtml) { zone.insertAdjacentHTML('beforeend', warnHtml + errHtml); }
      if (document.getElementById('uphist-ozon')) await loadRnpsUploadHistory();
    }
  }

  // Зона загрузки общего отчёта ОСТАТКОВ склада АВТОПРОФИ (МСК).
  // Дата берётся из поля #date-ozstock (в файле даты нет).
  function setupOzStockZone(root) {
    const zone = root.querySelector('#uz-ozstock');
    if (!zone) return;
    const input = root.querySelector('#file-ozstock');
    const pick = root.querySelector('#pick-ozstock');
    const dateEl = root.querySelector('#date-ozstock');
    // По умолчанию — сегодняшняя дата (локальная), если не выбрана.
    if (dateEl && !dateEl.value) {
      const now = new Date();
      const iso = now.getFullYear() + '-' +
        String(now.getMonth() + 1).padStart(2, '0') + '-' +
        String(now.getDate()).padStart(2, '0');
      dateEl.value = iso;
    }
    if (pick) pick.addEventListener('click', (e) => { e.preventDefault(); input.click(); });
    input.addEventListener('change', () => { if (input.files[0]) doUploadOzStock(input.files[0], zone); });
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('drag'); }));
    zone.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) doUploadOzStock(f, zone); });
  }

  async function doUploadOzStock(file, zone) {
    zone.querySelectorAll('.up-warn, .note.err').forEach(el => el.remove());
    const dateEl = document.getElementById('date-ozstock');
    const stockDate = dateEl ? dateEl.value : '';
    if (!stockDate) {
      App.toast('Сначала укажите дату, за которую загружаются остатки.', 'err');
      return;
    }
    const orig = zone.innerHTML;
    let warnHtml = '', errHtml = '';
    zone.innerHTML = '<div class="loader"><span class="spinner"></span><div style="margin-top:8px">Загрузка ' + U.esc(file.name) + '…</div></div>';
    try {
      const res = await API.uploadOzonStock(file, stockDate);
      const period = res.period_text || '';
      const synced = res.synced_ozon || 0;
      const fileTotal = res.file_total || res.rows_loaded || 0;
      const skipped = res.skipped_not_in_catalog || 0;
      App.toast('✓ Остатки склада · записано товаров: ' + U.fmtNum(res.rows_loaded) + (period ? ' · ' + period : ''), 'ok');
      let extra = '';
      if (skipped) { extra = ' Пропущено вне справочника дашборда: ' + U.fmtNum(skipped) + ' (из ' + U.fmtNum(fileTotal) + ' в файле).'; }
      warnHtml = '<div class="up-warn" style="background:#f0f6f0;border-color:#c5dcc5;color:#2f5a2f;">✓ Записано в справочник остатков: ' + U.fmtNum(res.rows_loaded) + ' товаров. Синхронизировано с продажами Ozon (матрица РНП): ' + U.fmtNum(synced) + '.' + extra + '</div>';
      if (API.cacheClear) { API.cacheClear('/api/rnp_sales'); }
    } catch (e) {
      App.toast('Ошибка загрузки: ' + e.message, 'err');
      errHtml = '<div class="note err" style="margin-top:10px;padding:9px 12px;font-size:12.5px;line-height:1.4">✖ Файл не загружен. ' + U.esc(e.message) + '</div>';
    } finally {
      zone.innerHTML = orig;
      setupOzStockZone(document.getElementById('view-root'));
      if (warnHtml || errHtml) { zone.insertAdjacentHTML('beforeend', warnHtml + errHtml); }
      if (document.getElementById('uphist-ozon')) await loadRnpsUploadHistory();
    }
  }

  // Зона загрузки отчёта Ozon «Список товаров» (XLSX или CSV): рейтинг + отзывы.
  // Дата берётся из поля #date-ozreviews (в файле даты нет).
  function setupOzReviewsZone(root) {
    const zone = root.querySelector('#uz-ozreviews');
    if (!zone) return;
    const input = root.querySelector('#file-ozreviews');
    const pick = root.querySelector('#pick-ozreviews');
    const dateEl = root.querySelector('#date-ozreviews');
    if (dateEl && !dateEl.value) {
      const now = new Date();
      const iso = now.getFullYear() + '-' +
        String(now.getMonth() + 1).padStart(2, '0') + '-' +
        String(now.getDate()).padStart(2, '0');
      dateEl.value = iso;
    }
    if (pick) pick.addEventListener('click', (e) => { e.preventDefault(); input.click(); });
    input.addEventListener('change', () => { if (input.files[0]) doUploadOzReviews(input.files[0], zone); });
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('drag'); }));
    zone.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) doUploadOzReviews(f, zone); });
  }

  async function doUploadOzReviews(file, zone) {
    zone.querySelectorAll('.up-warn, .note.err').forEach(el => el.remove());
    const dateEl = document.getElementById('date-ozreviews');
    const reportDate = dateEl ? dateEl.value : '';
    if (!reportDate) {
      App.toast('Сначала укажите дату, на которую действителен отчёт.', 'err');
      return;
    }
    const orig = zone.innerHTML;
    let warnHtml = '', errHtml = '';
    zone.innerHTML = '<div class="loader"><span class="spinner"></span><div style="margin-top:8px">Загрузка ' + U.esc(file.name) + '…</div></div>';
    try {
      const res = await API.uploadOzonReviews(file, reportDate);
      const period = res.period_text || '';
      const synced = res.synced_ozon || 0;
      const fileTotal = res.file_total || 0;
      const skipped = res.skipped_not_in_catalog || 0;
      App.toast('✓ Рейтинг/отзывы · обновлено строк: ' + U.fmtNum(synced) + (period ? ' · ' + period : ''), 'ok');
      let extra = '';
      if (skipped) { extra = ' Пропущено вне справочника дашборда: ' + U.fmtNum(skipped) + ' (из ' + U.fmtNum(fileTotal) + ' в файле).'; }
      warnHtml = '<div class="up-warn" style="background:#f0f6f0;border-color:#c5dcc5;color:#2f5a2f;">✓ Обновлено в матрице РНП Ozon (рейтинг + отзывы): ' + U.fmtNum(synced) + ' строк.' + extra + '</div>';
      if (API.cacheClear) { API.cacheClear('/api/rnp_sales'); }
    } catch (e) {
      App.toast('Ошибка загрузки: ' + e.message, 'err');
      errHtml = '<div class="note err" style="margin-top:10px;padding:9px 12px;font-size:12.5px;line-height:1.4">✖ Файл не загружен. ' + U.esc(e.message) + '</div>';
    } finally {
      zone.innerHTML = orig;
      setupOzReviewsZone(document.getElementById('view-root'));
      if (warnHtml || errHtml) { zone.insertAdjacentHTML('beforeend', warnHtml + errHtml); }
      if (document.getElementById('uphist-ozon')) await loadRnpsUploadHistory();
    }
  }

  // ═══ Wildberries — зоны загрузки для раздела «РНП заказы» ═══
  // Полная аналогия зон Ozon: drag&drop + клик, спиннер, тост, обновление журнала WB.

  // Воронка продаж WB (эндпоинт /api/upload/wb_daily). Даты из файла.
  function setupWbDayZone(root) {
    const zone = root.querySelector('#uz-wbday');
    if (!zone) return;
    const input = root.querySelector('#file-wbday');
    const pick = root.querySelector('#pick-wbday');
    if (pick) pick.addEventListener('click', (e) => { e.preventDefault(); input.click(); });
    input.addEventListener('change', () => { if (input.files[0]) doUploadWbDay(input.files[0], zone); });
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('drag'); }));
    zone.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) doUploadWbDay(f, zone); });
  }

  async function doUploadWbDay(file, zone) {
    zone.querySelectorAll('.up-warn, .note.err').forEach(el => el.remove());
    const orig = zone.innerHTML;
    let warnHtml = '', errHtml = '';
    zone.innerHTML = '<div class="loader"><span class="spinner"></span><div style="margin-top:8px">Загрузка ' + U.esc(file.name) + '…</div></div>';
    try {
      const res = await API.uploadWbDaily(file);
      const dates = (res.dates || []);
      const period = res.period_text || (dates.length ? dates[0] + '…' + dates[dates.length - 1] : '');
      App.toast('✓ WB Воронка · строк: ' + U.fmtNum(res.rows_loaded) + (period ? ' · ' + period : ''), 'ok');
      const unm = (res.unmatched || []);
      if (unm.length) {
        warnHtml = '<div class="up-warn">⚠ Нераспределённых артикулов: ' + unm.length + ' — заполните группы/статус/менеджера в Справочнике (Wildberries).</div>';
      }
      if (API.cacheClear) { API.cacheClear('/api/rnp_sales'); API.cacheClear('/api/catalog'); }
    } catch (e) {
      App.toast('Ошибка загрузки: ' + e.message, 'err');
      errHtml = '<div class="note err" style="margin-top:10px;padding:9px 12px;font-size:12.5px;line-height:1.4">✖ Файл не загружен. ' + U.esc(e.message) + '</div>';
    } finally {
      zone.innerHTML = orig;
      setupWbDayZone(document.getElementById('view-root'));
      if (warnHtml || errHtml) { zone.insertAdjacentHTML('beforeend', warnHtml + errHtml); }
      const af = document.querySelector('#hist-filter .seg-btn.active');
      if (af) await loadHistory(af.dataset.kind);
      if (document.getElementById('uphist-wb')) await loadRnpsUploadHistory();
    }
  }

  // «Аналитика продаж» Яндекс.Маркет (эндпоинт /api/upload/ya_daily).
  // Дата берётся из колонки «День» самого отчёта — поле даты в форме не нужно.
  function setupYaDayZone(root) {
    const zone = root.querySelector('#uz-yaday');
    if (!zone) return;
    const input = root.querySelector('#file-yaday');
    const pick = root.querySelector('#pick-yaday');
    if (pick) pick.addEventListener('click', (e) => { e.preventDefault(); input.click(); });
    input.addEventListener('change', () => { if (input.files[0]) doUploadYaDay(input.files[0], zone); });
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('drag'); }));
    zone.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) doUploadYaDay(f, zone); });
  }

  async function doUploadYaDay(file, zone) {
    zone.querySelectorAll('.up-warn, .note.err').forEach(el => el.remove());
    const orig = zone.innerHTML;
    let warnHtml = '', errHtml = '';
    zone.innerHTML = '<div class="loader"><span class="spinner"></span><div style="margin-top:8px">Загрузка ' + U.esc(file.name) + '…</div></div>';
    try {
      const res = await API.uploadYaDaily(file);
      const dates = (res.dates || []);
      const period = res.period_text || (dates.length ? dates[0] + '…' + dates[dates.length - 1] : '');
      App.toast('✓ Yandex Аналитика продаж · строк: ' + U.fmtNum(res.rows_loaded) + (period ? ' · ' + period : ''), 'ok');
      const unm = (res.unmatched || []);
      if (unm.length) {
        warnHtml = '<div class="up-warn">⚠ Нераспределённых артикулов: ' + unm.length + ' — заполните группы/статус/менеджера в Справочнике (Yandex).</div>';
      }
      if (API.cacheClear) { API.cacheClear('/api/rnp_sales'); API.cacheClear('/api/catalog'); }
    } catch (e) {
      App.toast('Ошибка загрузки: ' + e.message, 'err');
      errHtml = '<div class="note err" style="margin-top:10px;padding:9px 12px;font-size:12.5px;line-height:1.4">✖ Файл не загружен. ' + U.esc(e.message) + '</div>';
    } finally {
      zone.innerHTML = orig;
      setupYaDayZone(document.getElementById('view-root'));
      if (warnHtml || errHtml) { zone.insertAdjacentHTML('beforeend', warnHtml + errHtml); }
      const af = document.querySelector('#hist-filter .seg-btn.active');
      if (af) await loadHistory(af.dataset.kind);
      if (document.getElementById('uphist-ya')) await loadRnpsUploadHistory();
    }
  }

  // Ежедневные остатки склада Яндекс (эндпоинт /api/upload/ya_stock).
  // Дата берётся из поля #date-yastock (в файле даты нет).
  function setupYaStockZone(root) {
    const zone = root.querySelector('#uz-yastock');
    if (!zone) return;
    const input = root.querySelector('#file-yastock');
    const pick = root.querySelector('#pick-yastock');
    const dateEl = root.querySelector('#date-yastock');
    if (dateEl && !dateEl.value) {
      const now = new Date();
      const iso = now.getFullYear() + '-' +
        String(now.getMonth() + 1).padStart(2, '0') + '-' +
        String(now.getDate()).padStart(2, '0');
      dateEl.value = iso;
    }
    if (pick) pick.addEventListener('click', (e) => { e.preventDefault(); input.click(); });
    input.addEventListener('change', () => { if (input.files[0]) doUploadYaStock(input.files[0], zone); });
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('drag'); }));
    zone.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) doUploadYaStock(f, zone); });
  }

  async function doUploadYaStock(file, zone) {
    zone.querySelectorAll('.up-warn, .note.err').forEach(el => el.remove());
    const dateEl = document.getElementById('date-yastock');
    const stockDate = dateEl ? dateEl.value : '';
    if (!stockDate) {
      App.toast('Сначала укажите дату, за которую загружаются остатки Яндекс.', 'err');
      return;
    }
    const orig = zone.innerHTML;
    let warnHtml = '', errHtml = '';
    zone.innerHTML = '<div class="loader"><span class="spinner"></span><div style="margin-top:8px">Загрузка ' + U.esc(file.name) + '…</div></div>';
    try {
      const res = await API.uploadYaStock(file, stockDate);
      const period = res.period_text || '';
      const upd = res.updated || 0;
      const ins = res.inserted || 0;
      const skuTotal = res.sku_total || 0;
      const artsNew = res.arts_new || 0;
      App.toast('✓ Остатки Яндекс · строк: ' + U.fmtNum(res.rows_loaded) + (period ? ' · ' + period : ''), 'ok');
      let extra = '';
      if (ins) { extra = ' Заведено строк-заготовок (остаток без продаж): ' + U.fmtNum(ins) + '.'; }
      warnHtml = '<div class="up-warn" style="background:#fff8e0;border-color:#f0d9a0;color:#6b5600;">✓ Обновлён остаток Яндекс за ' + U.esc(period) + ': обновлено строк ' + U.fmtNum(upd) + ' из ' + U.fmtNum(skuTotal) + ' SKU в файле.' + extra + '</div>';
      if (artsNew) { warnHtml += '<div class="up-warn">⚠ Новых артикулов (нераспределённые): ' + artsNew + ' — заполните группы/статус/менеджера в Справочнике (Yandex).</div>'; }
      if (API.cacheClear) { API.cacheClear('/api/rnp_sales'); }
    } catch (e) {
      App.toast('Ошибка загрузки: ' + e.message, 'err');
      errHtml = '<div class="note err" style="margin-top:10px;padding:9px 12px;font-size:12.5px;line-height:1.4">✖ Файл не загружен. ' + U.esc(e.message) + '</div>';
    } finally {
      zone.innerHTML = orig;
      setupYaStockZone(document.getElementById('view-root'));
      if (warnHtml || errHtml) { zone.insertAdjacentHTML('beforeend', warnHtml + errHtml); }
      if (document.getElementById('uphist-ya')) await loadRnpsUploadHistory();
    }
  }

  // Рейтинг/отзывы WB (эндпоинт /api/upload/wb_reviews).
  // Дата берётся из колонки «Дата» самого отчёта — поле даты в форме не нужно.
  function setupWbReviewsZone(root) {
    const zone = root.querySelector('#uz-wbreviews');
    if (!zone) return;
    const input = root.querySelector('#file-wbreviews');
    const pick = root.querySelector('#pick-wbreviews');
    if (pick) pick.addEventListener('click', (e) => { e.preventDefault(); input.click(); });
    input.addEventListener('change', () => { if (input.files[0]) doUploadWbReviews(input.files[0], zone); });
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('drag'); }));
    zone.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) doUploadWbReviews(f, zone); });
  }

  async function doUploadWbReviews(file, zone) {
    zone.querySelectorAll('.up-warn, .note.err').forEach(el => el.remove());
    const orig = zone.innerHTML;
    let warnHtml = '', errHtml = '';
    zone.innerHTML = '<div class="loader"><span class="spinner"></span><div style="margin-top:8px">Загрузка ' + U.esc(file.name) + '…</div></div>';
    try {
      const res = await API.uploadWbReviews(file);
      const period = res.period_text || res.report_date || '';
      const updated = (res.updated || 0) + (res.inserted || 0);
      const sppN = res.spp_updated || 0;
      App.toast('✓ WB Рейтинг/отзывы · обновлено строк: ' + U.fmtNum(updated) + (sppN ? ' · СПП: ' + U.fmtNum(sppN) : '') + (period ? ' · ' + period : ''), 'ok');
      const skipped = res.skipped_not_in_catalog || 0;
      const fileTotal = res.file_total || 0;
      let extra = '';
      if (sppN) { extra += ' СПП проставлено: ' + U.fmtNum(sppN) + '.'; }
      if (skipped) { extra += ' Пропущено вне справочника WB: ' + U.fmtNum(skipped) + ' (из ' + U.fmtNum(fileTotal) + ' в файле).'; }
      warnHtml = '<div class="up-warn" style="background:#f0f6f0;border-color:#c5dcc5;color:#2f5a2f;">✓ Обновлено в матрице РНП Wildberries (отзывы + СПП): ' + U.fmtNum(updated) + ' строк.' + extra + '</div>';
      if (API.cacheClear) { API.cacheClear('/api/rnp_sales'); }
    } catch (e) {
      App.toast('Ошибка загрузки: ' + e.message, 'err');
      errHtml = '<div class="note err" style="margin-top:10px;padding:9px 12px;font-size:12.5px;line-height:1.4">✖ Файл не загружен. ' + U.esc(e.message) + '</div>';
    } finally {
      zone.innerHTML = orig;
      setupWbReviewsZone(document.getElementById('view-root'));
      if (warnHtml || errHtml) { zone.insertAdjacentHTML('beforeend', warnHtml + errHtml); }
      if (document.getElementById('uphist-wb')) await loadRnpsUploadHistory();
    }
  }

  // Реклама WB (эндпоинт /api/upload/wb_ads). Дата из файла.
  function setupWbAdsZone(root) {
    const zone = root.querySelector('#uz-wbads');
    if (!zone) return;
    const input = root.querySelector('#file-wbads');
    const pick = root.querySelector('#pick-wbads');
    if (pick) pick.addEventListener('click', (e) => { e.preventDefault(); input.click(); });
    input.addEventListener('change', () => { if (input.files[0]) doUploadWbAds(input.files[0], zone); });
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('drag'); }));
    zone.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) doUploadWbAds(f, zone); });
  }

  async function doUploadWbAds(file, zone) {
    zone.querySelectorAll('.up-warn, .note.err').forEach(el => el.remove());
    const orig = zone.innerHTML;
    let warnHtml = '', errHtml = '';
    zone.innerHTML = '<div class="loader"><span class="spinner"></span><div style="margin-top:8px">Загрузка ' + U.esc(file.name) + '…</div></div>';
    try {
      const res = await API.uploadWbAds(file);
      const period = res.period_text || '';
      const updated = (res.updated || 0) + (res.inserted || 0);
      App.toast('✓ WB реклама · обновлено строк: ' + U.fmtNum(updated) + (period ? ' · ' + period : ''), 'ok');
      const skipped = res.skipped_not_in_catalog || 0;
      if (skipped) {
        warnHtml = '<div class="up-warn">⚠ Артикулов без строки продаж за эту дату: ' + U.fmtNum(skipped) + '. Сначала загрузите Воронку WB за ту же дату, затем повторите загрузку рекламы.</div>';
      }
      if (API.cacheClear) { API.cacheClear('/api/rnp_sales'); }
    } catch (e) {
      App.toast('Ошибка загрузки: ' + e.message, 'err');
      errHtml = '<div class="note err" style="margin-top:10px;padding:9px 12px;font-size:12.5px;line-height:1.4">✖ Файл не загружен. ' + U.esc(e.message) + '</div>';
    } finally {
      zone.innerHTML = orig;
      setupWbAdsZone(document.getElementById('view-root'));
      if (warnHtml || errHtml) { zone.insertAdjacentHTML('beforeend', warnHtml + errHtml); }
      if (document.getElementById('uphist-wb')) await loadRnpsUploadHistory();
    }
  }

  // Зона загрузки отчёта мониторинга цен конкурентов (PriceVA) — OZON.
  function setupOzPricevaZone(root) {
    const zone = root.querySelector('#uz-ozpriceva');
    if (!zone) return;
    const input = root.querySelector('#file-ozpriceva');
    const pick = root.querySelector('#pick-ozpriceva');
    const dateEl = root.querySelector('#date-ozpriceva');
    if (dateEl && !dateEl.value) {
      const now = new Date();
      const iso = now.getFullYear() + '-' +
        String(now.getMonth() + 1).padStart(2, '0') + '-' +
        String(now.getDate()).padStart(2, '0');
      dateEl.value = iso;
    }
    if (pick) pick.addEventListener('click', (e) => { e.preventDefault(); input.click(); });
    input.addEventListener('change', () => { if (input.files[0]) doUploadOzPriceva(input.files[0], zone); });
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('drag'); }));
    zone.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) doUploadOzPriceva(f, zone); });
  }

  async function doUploadOzPriceva(file, zone) {
    zone.querySelectorAll('.up-warn, .note.err').forEach(el => el.remove());
    const dateEl = document.getElementById('date-ozpriceva');
    const reportDate = dateEl ? dateEl.value : '';
    if (!reportDate) {
      App.toast('Сначала укажите дату, на которую загружаются цены конкурентов.', 'err');
      return;
    }
    const orig = zone.innerHTML;
    let warnHtml = '', errHtml = '';
    zone.innerHTML = '<div class="loader"><span class="spinner"></span><div style="margin-top:8px">Загрузка ' + U.esc(file.name) + '…</div></div>';
    try {
      const res = await API.uploadOzonPriceva(file, reportDate);
      const period = res.period_text || '';
      const total = res.rows_loaded || 0;
      const updated = res.updated || 0;
      const inserted = res.inserted || 0;
      const fileTotal = res.file_total || 0;
      const skipped = res.skipped_not_in_catalog || 0;
      App.toast('✓ Цены конкурентов · записано: ' + U.fmtNum(total) + (period ? ' · ' + period : ''), 'ok');
      let extra = '';
      if (skipped) { extra = ' Пропущено вне справочника дашборда: ' + U.fmtNum(skipped) + ' (из ' + U.fmtNum(fileTotal) + ' в файле).'; }
      warnHtml = '<div class="up-warn" style="background:#f0f6f0;border-color:#c5dcc5;color:#2f5a2f;">✓ Цены конкурентов записаны: ' + U.fmtNum(total) + ' товаров (обновлено строк продаж: ' + U.fmtNum(updated) + ', создано строк только с ценами: ' + U.fmtNum(inserted) + ').' + extra + '</div>';
      if (API.cacheClear) { API.cacheClear('/api/rnp_sales'); }
    } catch (e) {
      App.toast('Ошибка загрузки: ' + e.message, 'err');
      errHtml = '<div class="note err" style="margin-top:10px;padding:9px 12px;font-size:12.5px;line-height:1.4">✖ Файл не загружен. ' + U.esc(e.message) + '</div>';
    } finally {
      zone.innerHTML = orig;
      setupOzPricevaZone(document.getElementById('view-root'));
      if (warnHtml || errHtml) { zone.insertAdjacentHTML('beforeend', warnHtml + errHtml); }
      if (document.getElementById('uphist-ozon')) await loadRnpsUploadHistory();
    }
  }

  // Зона загрузки отчёта мониторинга цен конкурентов (PriceVA) — WILDBERRIES.
  function setupWbPricevaZone(root) {
    const zone = root.querySelector('#uz-wbpriceva');
    if (!zone) return;
    const input = root.querySelector('#file-wbpriceva');
    const pick = root.querySelector('#pick-wbpriceva');
    const dateEl = root.querySelector('#date-wbpriceva');
    if (dateEl && !dateEl.value) {
      const now = new Date();
      const iso = now.getFullYear() + '-' +
        String(now.getMonth() + 1).padStart(2, '0') + '-' +
        String(now.getDate()).padStart(2, '0');
      dateEl.value = iso;
    }
    if (pick) pick.addEventListener('click', (e) => { e.preventDefault(); input.click(); });
    input.addEventListener('change', () => { if (input.files[0]) doUploadWbPriceva(input.files[0], zone); });
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('drag'); }));
    zone.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) doUploadWbPriceva(f, zone); });
  }

  async function doUploadWbPriceva(file, zone) {
    zone.querySelectorAll('.up-warn, .note.err').forEach(el => el.remove());
    const dateEl = document.getElementById('date-wbpriceva');
    const reportDate = dateEl ? dateEl.value : '';
    if (!reportDate) {
      App.toast('Сначала укажите дату, на которую загружаются цены конкурентов.', 'err');
      return;
    }
    const orig = zone.innerHTML;
    let warnHtml = '', errHtml = '';
    zone.innerHTML = '<div class="loader"><span class="spinner"></span><div style="margin-top:8px">Загрузка ' + U.esc(file.name) + '…</div></div>';
    try {
      const res = await API.uploadWbPriceva(file, reportDate);
      const period = res.period_text || '';
      const total = res.rows_loaded || 0;
      const updated = res.updated || 0;
      const inserted = res.inserted || 0;
      const fileTotal = res.file_total || 0;
      const skipped = res.skipped_not_in_catalog || 0;
      App.toast('✓ Цены конкурентов · записано: ' + U.fmtNum(total) + (period ? ' · ' + period : ''), 'ok');
      let extra = '';
      if (skipped) { extra = ' Пропущено вне справочника дашборда: ' + U.fmtNum(skipped) + ' (из ' + U.fmtNum(fileTotal) + ' в файле).'; }
      warnHtml = '<div class="up-warn" style="background:#f0f6f0;border-color:#c5dcc5;color:#2f5a2f;">✓ Цены конкурентов записаны: ' + U.fmtNum(total) + ' товаров (обновлено строк продаж: ' + U.fmtNum(updated) + ', создано строк только с ценами: ' + U.fmtNum(inserted) + ').' + extra + '</div>';
      if (API.cacheClear) { API.cacheClear('/api/rnp_sales'); }
    } catch (e) {
      App.toast('Ошибка загрузки: ' + e.message, 'err');
      errHtml = '<div class="note err" style="margin-top:10px;padding:9px 12px;font-size:12.5px;line-height:1.4">✖ Файл не загружен. ' + U.esc(e.message) + '</div>';
    } finally {
      zone.innerHTML = orig;
      setupWbPricevaZone(document.getElementById('view-root'));
      if (warnHtml || errHtml) { zone.insertAdjacentHTML('beforeend', warnHtml + errHtml); }
      if (document.getElementById('uphist-wb')) await loadRnpsUploadHistory();
    }
  }

  // Зона загрузки отчёта «ИНДЕКС ЦЕН» (Pi) — WILDBERRIES.
  function setupWbPiZone(root) {
    const zone = root.querySelector('#uz-wbpi');
    if (!zone) return;
    const input = root.querySelector('#file-wbpi');
    const pick = root.querySelector('#pick-wbpi');
    const dateEl = root.querySelector('#date-wbpi');
    if (dateEl && !dateEl.value) {
      const now = new Date();
      const iso = now.getFullYear() + '-' +
        String(now.getMonth() + 1).padStart(2, '0') + '-' +
        String(now.getDate()).padStart(2, '0');
      dateEl.value = iso;
    }
    if (pick) pick.addEventListener('click', (e) => { e.preventDefault(); input.click(); });
    input.addEventListener('change', () => { if (input.files[0]) doUploadWbPi(input.files[0], zone); });
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('drag'); }));
    zone.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) doUploadWbPi(f, zone); });
  }

  async function doUploadWbPi(file, zone) {
    zone.querySelectorAll('.up-warn, .note.err').forEach(el => el.remove());
    const dateEl = document.getElementById('date-wbpi');
    const reportDate = dateEl ? dateEl.value : '';
    if (!reportDate) {
      App.toast('Сначала укажите дату, на которую загружается индекс цен.', 'err');
      return;
    }
    const orig = zone.innerHTML;
    let warnHtml = '', errHtml = '';
    zone.innerHTML = '<div class="loader"><span class="spinner"></span><div style="margin-top:8px">Загрузка ' + U.esc(file.name) + '…</div></div>';
    try {
      const res = await API.uploadWbPi(file, reportDate);
      const period = res.period_text || '';
      const total = res.rows_loaded || 0;
      const updated = res.updated || 0;
      const inserted = res.inserted || 0;
      const fileTotal = res.file_total || 0;
      const skipped = res.skipped_not_in_catalog || 0;
      const noPi = res.skipped_no_pi || 0;
      App.toast('✓ Индекс цен · записано: ' + U.fmtNum(total) + (period ? ' · ' + period : ''), 'ok');
      let extra = '';
      if (skipped) { extra += ' Пропущено вне справочника: ' + U.fmtNum(skipped) + ' (из ' + U.fmtNum(fileTotal) + ' в файле).'; }
      if (noPi) { extra += ' Без корректных цен (Pi=—): ' + U.fmtNum(noPi) + '.'; }
      warnHtml = '<div class="up-warn" style="background:#f0f6f0;border-color:#c5dcc5;color:#2f5a2f;">✓ Индекс цен записан: ' + U.fmtNum(total) + ' товаров (обновлено строк продаж: ' + U.fmtNum(updated) + ', создано строк-заглушек: ' + U.fmtNum(inserted) + ').' + extra + '</div>';
      if (API.cacheClear) { API.cacheClear('/api/rnp_sales'); }
    } catch (e) {
      App.toast('Ошибка загрузки: ' + e.message, 'err');
      errHtml = '<div class="note err" style="margin-top:10px;padding:9px 12px;font-size:12.5px;line-height:1.4">✖ Файл не загружен. ' + U.esc(e.message) + '</div>';
    } finally {
      zone.innerHTML = orig;
      setupWbPiZone(document.getElementById('view-root'));
      if (warnHtml || errHtml) { zone.insertAdjacentHTML('beforeend', warnHtml + errHtml); }
      if (document.getElementById('uphist-wb')) await loadRnpsUploadHistory();
    }
  }

  // Плашка типа отчёта: Неделя (нейтральная) / Месяц (акцент).
  function kindPill(kind) {
    if (kind === 'monthly') return '<span class="kind-pill kind-month">Месяц</span>';
    if (kind === 'weekly')  return '<span class="kind-pill kind-week">Неделя</span>';
    if (kind === 'daily')   return '<span class="kind-pill kind-week">День</span>';
    if (kind === 'ads')     return '<span class="kind-pill kind-week">Реклама</span>';
    if (kind === 'stock')   return '<span class="kind-pill kind-week">Остатки</span>';
    if (kind === 'reviews') return '<span class="kind-pill kind-week">Отзывы</span>';
    if (kind === 'competitors') return '<span class="kind-pill kind-week">Конкуренты</span>';
    // WB-загрузчики пишут свои period_kind (wb_daily/wb_ads/wb_reviews) —
    // маппим на те же метки, что у Ozon-аналогов (День/Реклама/Отзывы).
    if (kind === 'wb_daily')   return '<span class="kind-pill kind-week">День</span>';
    if (kind === 'wb_ads')     return '<span class="kind-pill kind-week">Реклама</span>';
    if (kind === 'wb_reviews') return '<span class="kind-pill kind-week">Отзывы</span>';
    if (kind === 'wb_pi')      return '<span class="kind-pill kind-week">Pi</span>';
    if (kind === 'wb_stock')   return '<span class="kind-pill kind-week">Остатки</span>';
    // Яндекс: воронка «Аналитика продаж» (загрузчик ya_daily_loader.py).
    if (kind === 'ya_daily')   return '<span class="kind-pill kind-week">День</span>';
    if (kind === 'ya_stock')   return '<span class="kind-pill kind-week">Остатки</span>';
    if (kind === 'cost')       return '<span class="kind-pill kind-week">Себест.</span>';
    // Остатки Ozon по складам (раздел «Склады → OZON») — загрузчик ozon_wh_stock_loader.py
    // пишет period_kind='ozon_stock' (константа PERIOD_KIND) — маппим на ту же метку, что и wb_stock.
    if (kind === 'ozon_stock') return '<span class="kind-pill kind-week">Остатки</span>';
    return '<span class="kind-pill kind-na">—</span>';
  }

  // Рус. названия месяцев для колонки «Период»
  const RU_MONTHS_SHORT = ['', 'Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн',
    'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек'];

  // Токен последнего запроса журнала: защита от гонки (быстрые переключения фильтра).
  let __histReq = 0;
  async function loadHistory(kind) {
    const el = document.getElementById('up-history');
    if (!el) return;
    const my = ++__histReq;
    const rows = await API.uploadHistory(kind || 'all');
    // Если за время ожидания был новый вызов — этот результат устарел, игнорируем.
    if (my !== __histReq) return;
    if (!rows.length) { el.innerHTML = '<div class="empty">Загрузок по этому фильтру нет.</div>'; return; }
    let h = '<div class="tbl-wrap"><table class="tbl"><thead><tr><th class="l">Дата</th><th>Тип</th><th>МП</th><th class="l">Период</th><th>Строк</th><th>Статус</th><th class="l">Пользователь</th><th class="l">Сообщение</th></tr></thead><tbody>';
    rows.forEach(r => {
      const dt = new Date(r.uploaded_at).toLocaleString('ru-RU');
      const ok = r.status === 'OK';
      // приписка к периоду: неделя → «нед.NN», месяц → «Мес ГГГГ»
      let tag = '';
      if (r.period_kind === 'weekly' && r.week) tag = 'нед.' + r.week;
      else if (r.period_kind === 'monthly' && r.month) tag = (RU_MONTHS_SHORT[r.month] || '') + ' ' + (r.year || '');
      h += `<tr><td class="l muted">${dt}</td>
        <td>${kindPill(r.period_kind)}</td>
        <td>${U.mpPill(r.marketplace)}</td>
        <td class="l">${U.esc(r.period_text || '—')} <span class="muted">${U.esc(tag)}</span></td>
        <td class="num">${U.fmtNum(r.rows_loaded)}</td>
        <td><span class="pill ${ok ? 'up' : 'down'}">${U.esc(r.status)}</span></td>
        <td class="l muted">${U.esc(r.uploaded_by_name || '—')}</td>
        <td class="l muted">${U.esc(r.message || '')}</td></tr>`;
    });
    h += '</tbody></table></div>';
    el.innerHTML = h;
  }

  // Журналы загрузок для подраздела «Загрузка данных» в РНП заказы.
  // Три отдельных журнала, каждый со своим фильтром по типам отчётов:
  //   #uphist-ozon   — kind='rnps_ozon'   (daily, ads, reviews, competitors)
  //   #uphist-wb     — kind='rnps_wb'     (WB-типы; пока пусто)
  //   #uphist-common — kind='rnps_common' (stock — остатки АВТОПРОФИ)
  // Общий рендер строк таблицы для любого журнала загрузок.
  function renderUploadHistoryTable(rows) {
    if (!rows.length) return '<div class="empty">Загрузок нет.</div>';
    let h = '<div class="tbl-wrap"><table class="tbl"><thead><tr><th class="l">Дата</th><th>Тип</th><th>МП</th><th class="l">Период</th><th>Строк</th><th>Статус</th><th class="l">Пользователь</th><th class="l">Сообщение</th></tr></thead><tbody>';
    rows.forEach(r => {
      const dt = new Date(r.uploaded_at).toLocaleString('ru-RU');
      const ok = r.status === 'OK';
      h += `<tr><td class="l muted">${dt}</td>
        <td>${kindPill(r.period_kind)}</td>
        <td>${U.mpPill(r.marketplace)}</td>
        <td class="l">${U.esc(r.period_text || '—')}</td>
        <td class="num">${U.fmtNum(r.rows_loaded)}</td>
        <td><span class="pill ${ok ? 'up' : 'down'}">${U.esc(r.status)}</span></td>
        <td class="l muted">${U.esc(r.uploaded_by_name || '—')}</td>
        <td class="l muted">${U.esc(r.message || '')}</td></tr>`;
    });
    h += '</tbody></table></div>';
    return h;
  }

  // Загрузка одного журнала: elId — id контейнера, kind — фильтр backend'а.
  // Защита от гонки запросов через счётчик на самом элементе (dataset).
  async function loadOneUploadHistory(elId, kind) {
    const el = document.getElementById(elId);
    if (!el) return;
    const my = String((+(el.dataset.req || 0)) + 1);
    el.dataset.req = my;
    const rows = await API.uploadHistory(kind);
    if (el.dataset.req !== my) return;
    el.innerHTML = renderUploadHistoryTable(rows);
  }

  // Обновляет все три журнала подраздела «Загрузка данных».
  async function loadRnpsUploadHistory() {
    await Promise.all([
      loadOneUploadHistory('uphist-ozon', 'rnps_ozon'),
      loadOneUploadHistory('uphist-wb', 'rnps_wb'),
      loadOneUploadHistory('uphist-ya', 'rnps_ya'),
      loadOneUploadHistory('uphist-common', 'rnps_common'),
    ]);
  }

  // ============================================================
  // 8. ПОЛЬЗОВАТЕЛИ (только админ) — список + добавление + удаление
  // ============================================================
  // Роли: 'admin' (Администратор — полные права) и 'user' (Пользователь —
  // просмотр всех разделов, кроме «Пользователи» и «Загрузка»).
  // Жёсткое удаление; нельзя удалить самого себя (своя строка без кнопки).
  async function users(root, ctl, state) {
    root.innerHTML = `
      <div class="card">
        <h3>👥 Пользователи системы
          <span class="hint">логин — это e-mail; пароль задаёте вы при добавлении</span>
        </h3>
        <div class="users-toolbar">
          <button class="btn-sm primary" id="usr-add-btn">+ Добавить пользователя</button>
        </div>
        <div id="usr-form-wrap"></div>
        <div id="usr-list"><div class="loader"><span class="spinner"></span></div></div>
      </div>
    `;
    const addBtn = root.querySelector('#usr-add-btn');
    const formWrap = root.querySelector('#usr-form-wrap');
    if (addBtn) addBtn.addEventListener('click', () => toggleUserForm(formWrap, addBtn));
    await loadUsersList(root);
  }

  // отрисовка/скрытие формы добавления
  function toggleUserForm(formWrap, addBtn) {
    if (formWrap.dataset.open === '1') { closeUserForm(formWrap, addBtn); return; }
    formWrap.dataset.open = '1';
    addBtn.classList.add('hidden');
    formWrap.innerHTML = `
      <div class="usr-form">
        <div class="usr-form-grid">
          <div class="field">
            <label>ФИО</label>
            <input type="text" class="input-sm" id="usr-name" placeholder="Иван Иванов" autocomplete="off">
          </div>
          <div class="field">
            <label>E-mail (логин)</label>
            <input type="email" class="input-sm" id="usr-email" placeholder="user@example.com" autocomplete="off">
          </div>
          <div class="field">
            <label>Пароль</label>
            <div class="pass-wrap">
              <input type="password" class="input-sm" id="usr-pass" placeholder="Задайте пароль" autocomplete="new-password">
              <button type="button" class="pass-eye" id="usr-pass-eye" title="Показать пароль" aria-label="Показать пароль">👁</button>
            </div>
          </div>
          <div class="field">
            <label>Роль</label>
            <select class="select" id="usr-role">
              <option value="user" selected>Пользователь</option>
              <option value="admin">Администратор</option>
            </select>
          </div>
        </div>
        <div class="usr-form-actions">
          <button class="btn-sm primary" id="usr-save">Сохранить</button>
          <button class="btn-sm" id="usr-cancel">Отмена</button>
        </div>
      </div>
    `;
    const passInput = formWrap.querySelector('#usr-pass');
    const eye = formWrap.querySelector('#usr-pass-eye');
    // кнопка «показать пароль» (глазик)
    eye.addEventListener('click', () => {
      const show = passInput.type === 'password';
      passInput.type = show ? 'text' : 'password';
      eye.classList.toggle('on', show);
      eye.title = show ? 'Скрыть пароль' : 'Показать пароль';
      eye.setAttribute('aria-label', eye.title);
      passInput.focus();
    });
    formWrap.querySelector('#usr-cancel').addEventListener('click', () => closeUserForm(formWrap, addBtn));
    formWrap.querySelector('#usr-save').addEventListener('click', () => saveNewUser(formWrap, addBtn));
    formWrap.querySelector('#usr-name').focus();
  }

  function closeUserForm(formWrap, addBtn) {
    formWrap.dataset.open = '';
    formWrap.innerHTML = '';
    addBtn.classList.remove('hidden');
  }

  async function saveNewUser(formWrap, addBtn) {
    const name = (formWrap.querySelector('#usr-name').value || '').trim();
    const email = (formWrap.querySelector('#usr-email').value || '').trim();
    const pass = formWrap.querySelector('#usr-pass').value || '';
    const role = formWrap.querySelector('#usr-role').value || 'user';
    if (!name) { App.toast('Укажите ФИО', 'err'); return; }
    if (!email || !/.+@.+\..+/.test(email)) { App.toast('Укажите корректный e-mail', 'err'); return; }
    if (!pass) { App.toast('Задайте пароль', 'err'); return; }
    const saveBtn = formWrap.querySelector('#usr-save');
    saveBtn.disabled = true;
    saveBtn.innerHTML = '<span class="btn-spin"></span>Сохранение…';
    try {
      await API.userCreate({ display_name: name, email, password: pass, role });
      App.toast('Пользователь добавлен: ' + email, 'ok');
      closeUserForm(formWrap, addBtn);
      await loadUsersList(document.getElementById('view-root'));
    } catch (e) {
      // 409 — e-mail уже существует
      const msg = /409/.test(e.message) ? 'Пользователь с таким e-mail уже существует' : ('Ошибка: ' + e.message);
      App.toast(msg, 'err');
      saveBtn.disabled = false;
      saveBtn.innerHTML = 'Сохранить';
    }
  }

  async function loadUsersList(root) {
    const el = root.querySelector('#usr-list');
    if (!el) return;
    let rows;
    try { rows = await API.usersList(); }
    catch (e) { el.innerHTML = '<div class="empty">Не удалось загрузить список: ' + U.esc(e.message) + '</div>'; return; }
    if (!rows.length) { el.innerHTML = '<div class="empty">Пользователей пока нет.</div>'; return; }
    // сравниваем по email — объект текущего пользователя хранит email/name/role (без id)
    const me = API.getUser();
    const myEmail = (me && me.email ? me.email : '').toLowerCase();
    // Формат последнего визита: «ДД.ММ.ГГГГ, ЧЧ:ММ» (по локали ru-RU).
    const fmtSeen = (iso) => {
      if (!iso) return '—';
      const d = new Date(iso);
      if (isNaN(d.getTime())) return '—';
      const date = d.toLocaleDateString('ru-RU');
      const tm = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
      return date + ', ' + tm;
    };
    let h = '<div class="tbl-wrap"><table class="tbl"><thead><tr>'
      + '<th class="l">ФИО</th><th class="l">E-mail (логин)</th><th class="l">Роль</th>'
      + '<th class="l">Последний визит</th><th class="l">Добавлен</th><th></th></tr></thead><tbody>';
    rows.forEach(r => {
      const isMe = myEmail && (r.email || '').toLowerCase() === myEmail;
      const dt = r.created_at ? new Date(r.created_at).toLocaleDateString('ru-RU') : '—';
      const seen = fmtSeen(r.last_seen_at);
      const rolePill = r.role === 'admin'
        ? '<span class="pill pill-admin">Администратор</span>'
        : '<span class="pill pill-user">Пользователь</span>';
      const editBtn = `<button class="btn-sm usr-edit" data-id="${r.id}" data-name="${U.esc(r.display_name || '')}" data-email="${U.esc(r.email)}" data-role="${U.esc(r.role || 'user')}">Изменить</button>`;
      const delCell = isMe
        ? '<span class="muted" style="font-size:11.5px;">это вы</span>'
        : `<button class="btn-sm usr-del" data-id="${r.id}" data-email="${U.esc(r.email)}">Удалить</button>`;
      const seenCell = seen === '—'
        ? '<span class="muted">—</span>'
        : `<span class="usr-seen">${U.esc(seen)}</span>`;
      h += `<tr data-uid="${r.id}"><td class="l">${U.esc(r.display_name || '—')}</td>`
        + `<td class="l">${U.esc(r.email)}</td>`
        + `<td class="l">${rolePill}</td>`
        + `<td class="l">${seenCell}</td>`
        + `<td class="l muted">${dt}</td>`
        + `<td class="l"><span class="usr-actions">${editBtn}${delCell}</span></td></tr>`;
    });
    h += '</tbody></table></div>';
    el.innerHTML = h;
    el.querySelectorAll('.usr-del').forEach(btn => {
      btn.addEventListener('click', () => deleteUser(btn.dataset.id, btn.dataset.email, root));
    });
    el.querySelectorAll('.usr-edit').forEach(btn => {
      btn.addEventListener('click', () => openEditUserForm(btn, root));
    });
  }

  // Инлайн-форма редактирования пользователя: раскрывается строкой под самим
  // пользователем (переиспользует стили .usr-form формы добавления). Одновременно
  // открыта только одна форма. Пароль необязателен (пустой = не менять).
  function openEditUserForm(btn, root) {
    const el = root.querySelector('#usr-list');
    if (!el) return;
    const tr = btn.closest('tr');
    if (!tr) return;
    // Повторный клик по «Изменить» той же строки — закрыть форму (тоггл).
    const existing = el.querySelector('tr.usr-edit-row');
    const wasSame = existing && existing.dataset.forUid === btn.dataset.id;
    if (existing) existing.remove();
    if (wasSame) return;

    const id = btn.dataset.id;
    const name = btn.dataset.name || '';
    const email = btn.dataset.email || '';
    const role = btn.dataset.role || 'user';
    const editTr = document.createElement('tr');
    editTr.className = 'usr-edit-row';
    editTr.dataset.forUid = id;
    editTr.innerHTML = `
      <td colspan="6">
        <div class="usr-form">
          <div class="usr-form-grid">
            <div class="field">
              <label>ФИО</label>
              <input type="text" class="input-sm" id="usre-name" autocomplete="off" value="${U.esc(name)}">
            </div>
            <div class="field">
              <label>E-mail (логин)</label>
              <input type="email" class="input-sm" id="usre-email" autocomplete="off" value="${U.esc(email)}">
            </div>
            <div class="field">
              <label>Новый пароль</label>
              <div class="pass-wrap">
                <input type="password" class="input-sm" id="usre-pass" placeholder="Оставьте пустым, чтобы не менять" autocomplete="new-password">
                <button type="button" class="pass-eye" id="usre-pass-eye" title="Показать пароль" aria-label="Показать пароль">👁</button>
              </div>
            </div>
            <div class="field">
              <label>Роль</label>
              <select class="select" id="usre-role">
                <option value="user"${role !== 'admin' ? ' selected' : ''}>Пользователь</option>
                <option value="admin"${role === 'admin' ? ' selected' : ''}>Администратор</option>
              </select>
            </div>
          </div>
          <div class="usr-form-actions">
            <button class="btn-sm primary" id="usre-save">Сохранить</button>
            <button class="btn-sm" id="usre-cancel">Отмена</button>
          </div>
        </div>
      </td>`;
    tr.after(editTr);

    const passInput = editTr.querySelector('#usre-pass');
    const eye = editTr.querySelector('#usre-pass-eye');
    eye.addEventListener('click', () => {
      const show = passInput.type === 'password';
      passInput.type = show ? 'text' : 'password';
      eye.classList.toggle('on', show);
      eye.title = show ? 'Скрыть пароль' : 'Показать пароль';
      eye.setAttribute('aria-label', eye.title);
      passInput.focus();
    });
    editTr.querySelector('#usre-cancel').addEventListener('click', () => editTr.remove());
    editTr.querySelector('#usre-save').addEventListener('click', () => saveEditUser(id, editTr, root));
    editTr.querySelector('#usre-name').focus();
  }

  async function saveEditUser(id, editTr, root) {
    const name = (editTr.querySelector('#usre-name').value || '').trim();
    const email = (editTr.querySelector('#usre-email').value || '').trim();
    const pass = editTr.querySelector('#usre-pass').value || '';
    const role = editTr.querySelector('#usre-role').value || 'user';
    if (!name) { App.toast('Укажите ФИО', 'err'); return; }
    if (!email || !/.+@.+\..+/.test(email)) { App.toast('Укажите корректный e-mail', 'err'); return; }
    const saveBtn = editTr.querySelector('#usre-save');
    saveBtn.disabled = true;
    saveBtn.innerHTML = '<span class="btn-spin"></span>Сохранение…';
    try {
      const body = { display_name: name, email, role };
      if (pass.trim()) body.password = pass;
      await API.userUpdate(id, body);
      App.toast('Пользователь обновлён: ' + email, 'ok');
      await loadUsersList(root);
    } catch (e) {
      const msg = /409/.test(e.message) ? 'Пользователь с таким e-mail уже существует' : ('Ошибка: ' + e.message);
      App.toast(msg, 'err');
      saveBtn.disabled = false;
      saveBtn.innerHTML = 'Сохранить';
    }
  }

  async function deleteUser(id, email, root) {
    if (!confirm('Удалить пользователя ' + email + '?\nДоступ будет немедленно прекращён. При необходимости его можно добавить заново.')) return;
    try {
      await API.userDelete(id);
      App.toast('Пользователь удалён: ' + email, 'ok');
      await loadUsersList(root);
    } catch (e) {
      App.toast('Ошибка удаления: ' + e.message, 'err');
    }
  }

  // ============================================================
  // РНП UNIT — юнит-экономика по маркетплейсам (OZON / Wildberries)
  // ============================================================
  // searchArt — фильтр по артикулу (подстрока); mgrFilter — менеджер ('' = все); statusFilter — статус ('' = все)
  // marginNegOn  — триггер светофора: РНП % < 0 показываем красным (по умолч. включён)
  // marginTarget — целевая маржинальность в процентах (строка из поля ввода, '' = не задана);
  //                всё, что 0 <= РНП % < целевой, показываем оранжевым
  // periodFrom/periodTo: выбранный диапазон дат фильтра периода (строки 'YYYY-MM-DD').
  // Данные в БД недельные, но фильтр ежедневный: неделя показывается, если
  // хотя бы один её день попадает в [periodFrom, periodTo] (см. rnpVisibleWeeks).
  // По умолчанию — текущий месяц (выставляется при первой загрузке).
  // topMetric — ключ показателя «верхнего уровня» в строке-заголовке узла
  //             (дефолт 'rnp' = Маржинальность %; выбирается селектором,
  //              хранится только на время сессии, как остальные фильтры).
  const rnpState = { mp: 'ozon', expanded: {}, metricsOpen: {}, data: null, searchArt: '', mgrFilter: '', statusFilter: '', managers: [], statuses: [], weeks: [], metrics: [], marginNegOn: true, marginTarget: '', periodFrom: '', periodTo: '', topMetric: 'rnp', filterExpandSig: '', holdsOpen: false, promoOpen: false, uiSnap: {} };

  // ── Память состояния дерева РНП по маркетплейсам (на время открытой вкладки).
  // Проблема: при переключении подтаба МП (Ozon/WB/Сводная) раскрытие групп,
  // товаров и метрик раньше СБРАСЫВАЛОСЬ (expanded/metricsOpen = {}). Теперь
  // вместо сброса «свопаем» состояние: перед уходом с текущего МП сохраняем
  // снимок в st.uiSnap[oldMp], а при входе на новый МП — восстанавливаем
  // снимок st.uiSnap[newMp] (пустой = свежий отчёт, корень раскроется сам).
  // Ключи снимка перечислены в fields (у заказов их больше, чем у юнитки).
  // scroll хранится там же и восстанавливается после отрисовки таблицы.
  // st — это rnpState (юнитка) или rnpSalesState (заказы); оба имеют .uiSnap.
  function _rnpUiFields(st) {
    // Поля раскрытия, которые надо запоминать (те, что есть у данного state).
    return ['expanded', 'metricsOpen', 'monthsOpen', 'pricesOpen', 'piOpen']
      .filter(k => st[k] && typeof st[k] === 'object');
  }
  // Сохранить текущее состояние раскрытия+scroll в снимок МП `mp`.
  // Дерево РНП не имеет внутреннего overflow-y — вертикально скроллится окно,
  // поэтому запоминаем window.scrollY.
  function rnpUiSnapSave(st, mp) {
    if (!mp) return;
    const snap = st.uiSnap[mp] || (st.uiSnap[mp] = {});
    _rnpUiFields(st).forEach(k => { snap[k] = st[k]; });
    // pricesOpen у заказов — булево, не объект: сохраняем отдельно если есть.
    if (typeof st.pricesOpen === 'boolean') snap.pricesOpenBool = st.pricesOpen;
    snap.winScrollY = window.scrollY || window.pageYOffset || 0;
  }
  // Восстановить состояние раскрытия для МП `mp` (объекты раскрытия). scroll
  // применяется отдельно — после того как таблица отрисована (rnpUiScrollApply).
  function rnpUiSnapLoad(st, mp) {
    const snap = st.uiSnap[mp];
    if (!snap) {
      // Нет снимка → свежее состояние для этого МП (как первый вход).
      _rnpUiFields(st).forEach(k => { st[k] = {}; });
      if (typeof st.pricesOpen === 'boolean') st.pricesOpen = false;
      return false;
    }
    _rnpUiFields(st).forEach(k => { st[k] = snap[k] || {}; });
    if (typeof st.pricesOpen === 'boolean') st.pricesOpen = !!snap.pricesOpenBool;
    return true;
  }
  // Применить сохранённую вертикальную прокрутку окна (после отрисовки дерева).
  // Два rAF — чтобы дождаться раскладки отрисованной таблицы (высота стабильна).
  function rnpUiScrollApply(st, mp) {
    const snap = st.uiSnap[mp];
    if (!snap || !snap.winScrollY) return;
    const y = snap.winScrollY;
    requestAnimationFrame(() => requestAnimationFrame(() => { window.scrollTo(0, y); }));
  }

  // Предустановленные статусы товара — выбираются из списка и в РНП-отчёте,
  // и в справочнике (руками вписывать статус нельзя).
  //   NULL  — статус НЕ задан (нераспределён);
  //   '-'   — осознанно «выведен из продаж» (распределён; скрывается без движений);
  //   '?'   — «требует внимания/обсуждения» (распределён; всегда показывается).
  const STATUS_PRESETS = ['CORE', 'NEW', 'Closeout', 'Sale', '?', '-'];
  // Нормализация: пустое/NULL из БД показываем как '-'.
  function stDisplay(v) { return (v && String(v).trim()) ? String(v).trim() : '-'; }
  // Что отправлять на бэкенд: сохраняем РОВНО выбранное значение (включая '-' и
  // '?'); только реально пустое → null. Так '-' (выведен из продаж) отличается
  // в БД от NULL (статус не задан) — это нужно для логики распределённости/скрытия.
  function stToApi(v) { const s = (v == null) ? '' : String(v).trim(); return s === '' ? null : s; }
  // CSS-класс по статусу — для аккуратной фирменной раскраски плашки/селектора.
  function stClass(v) {
    const s = stDisplay(v);
    if (s === 'CORE') return 'st-core';
    if (s === 'NEW') return 'st-new';
    if (s === 'Closeout') return 'st-closeout';
    if (s === 'Sale') return 'st-sale';
    if (s === '?') return 'st-question';
    return 'st-none';
  }
  // <select> предустановленных статусов для строки-товара РНП.
  function statusSelectHtml(sa, current) {
    const cur = stDisplay(current);
    const opts = STATUS_PRESETS.map(s =>
      `<option value="${U.esc(s)}"${s === cur ? ' selected' : ''}>${U.esc(s)}</option>`).join('');
    return `<select class="rnp-status-sel ${stClass(cur)}" data-sa="${U.esc(sa)}" title="Статус товара">${opts}</select>`;
  }
  // Опции фильтра статусов наверху панели: всегда весь набор предустановленных
  // статусов (а не только те, что фактически встречаются). Значение '-' фильтрует
  // товары без статуса. Первый пункт «Все статусы» (value='') — без фильтра.
  function statusFilterOptionsHtml() {
    return ['<option value="">Все статусы</option>']
      .concat(STATUS_PRESETS.map(s =>
        `<option value="${U.esc(s)}"${rnpState.statusFilter === s ? ' selected' : ''}>${U.esc(s)}</option>`))
      .join('');
  }
  // <select> предустановленных статусов для справочника (класс c-st — читается при сохранении).
  function catStatusSelectHtml(current) {
    const cur = stDisplay(current);
    const opts = STATUS_PRESETS.map(s =>
      `<option value="${U.esc(s)}"${s === cur ? ' selected' : ''}>${U.esc(s)}</option>`).join('');
    return `<select class="input-sm c-st cat-status-sel ${stClass(cur)}" style="width:120px">${opts}</select>`;
  }

  // helper: pct хранится во фракции (0..1) -> выводим X,X%
  function rp(v, digits = 1) {
    if (v === null || v === undefined || isNaN(v)) return '—';
    return U.fmtPct(v * 100, digits);
  }

  // helper: компактный период недели из period_text вида '01.06.2026 - 07.06.2026'
  // -> '01-07.06' (один месяц) либо '01.06-07.07' (разные месяцы); год убираем
  function compactPeriod(periodText) {
    if (!periodText) return '';
    const m = String(periodText).match(/(\d{2})\.(\d{2})\.\d{4}\s*[-–]\s*(\d{2})\.(\d{2})\.\d{4}/);
    if (!m) return periodText;
    const [, d1, mo1, d2, mo2] = m;
    return mo1 === mo2 ? `${d1}-${d2}.${mo1}` : `${d1}.${mo1}-${d2}.${mo2}`;
  }

  // helper: разбор period_text вида '01.06.2026 - 07.06.2026' на даты начала/конца
  // (без года) — для трёхстрочного заголовка недели как в отчёте «Недели API v2»
  function weekDates(periodText) {
    if (!periodText) return { start: '', end: '' };
    const m = String(periodText).match(/(\d{2})\.(\d{2})\.\d{4}\s*[-–]\s*(\d{2})\.(\d{2})\.\d{4}/);
    if (!m) return { start: periodText, end: '' };
    const [, d1, mo1, d2, mo2] = m;
    return { start: `${d1}.${mo1}`, end: `${d2}.${mo2}` };
  }

  // helper: трёхстрочный заголовок недели (как в эталоне): дата начала / дата конца / номер крупно
  function weekColHead(w) {
    const dt = weekDates(w.period_text);
    return `<span class="rnp-wkh">`
      + `<span class="rnp-wkh-d1">${U.esc(dt.start)}</span>`
      + `<span class="rnp-wkh-d2">${U.esc(dt.end)}</span>`
      + `<span class="rnp-wkh-n">${U.esc(String(w.week))}</span>`
      + `</span>`;
  }

  // helper: заголовок столбца недели для ТАБЛИЦЫ ПОКАЗАТЕЛЕЙ — тот же трёхстрочный вид
  function weekColHeadShort(w) { return weekColHead(w); }

  // ============ ФИЛЬТР ПО ПЕРИОДУ (ежедневный → недельные данные) ============
  // Даты храним и сравниваем как строки 'YYYY-MM-DD' — лексикографическое
  // сравнение таких строк совпадает с хронологическим (без проблем с таймзонами).
  function isoDate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${dd}`;
  }
  // 'YYYY-MM-DD' -> Date (локальная полночь), для отрисовки календаря
  function parseISO(s) {
    if (!s) return null;
    const [y, m, d] = s.split('-').map(Number);
    return new Date(y, m - 1, d);
  }
  // '2026-04-20' -> '20.04.2026'
  function fmtRu(iso) {
    if (!iso) return '';
    const [y, m, d] = iso.split('-');
    return `${d}.${m}.${y}`;
  }
  // Подпись в поле периода: '20.04.2026 — 26.04.2026' (или одна дата)
  function periodLabel() {
    const f = rnpState.periodFrom, t = rnpState.periodTo;
    if (!f && !t) return 'Все недели';
    if (f && t) return f === t ? fmtRu(f) : `${fmtRu(f)} — ${fmtRu(t)}`;
    return fmtRu(f || t);
  }
  // Диапазон «текущий месяц/квартал/год» относительно «сегодня».
  // Базовую дату берём по НАЧАЛУ (period_start) последней загруженной недели
  // данных (данные могут быть «в прошлом» относительно календарного «сегодня»,
  // поэтому ориентируемся на них). Именно начало недели, а не конец: если
  // последняя неделя хвостом заезжает в следующий месяц/квартал (напр. нед. 27:
  // 29.06–05.07), то по period_end пресет «утёк» бы в пустой июль/Q3. Берём
  // начало недели — база стабильно относится к тому месяцу/кварталу, где лежит
  // основная масса данных. Fallback: period_end той же недели, затем сегодня.
  function periodBaseDate() {
    const wks = rnpState.weeks || [];
    for (let i = wks.length - 1; i >= 0; i--) {
      const wk = wks[i];
      const start = wk && wk.period_start;
      if (start) return parseISO(start);
      const end = wk && wk.period_end;
      if (end) return parseISO(end);
    }
    return new Date();
  }
  function presetRange(kind) {
    const base = periodBaseDate();
    const y = base.getFullYear(), m = base.getMonth();
    let from, to;
    if (kind === 'month') { from = new Date(y, m, 1); to = new Date(y, m + 1, 0); }
    else if (kind === 'quarter') { const q = Math.floor(m / 3); from = new Date(y, q * 3, 1); to = new Date(y, q * 3 + 3, 0); }
    else { from = new Date(y, 0, 1); to = new Date(y, 11, 31); } // year
    return { from: isoDate(from), to: isoDate(to) };
  }
  // Основная логика: отфильтровать недели по пересечению с выбранным диапазоном.
  // Пересечение интервалов: period_start <= to И period_end >= from.
  // Это и есть «хотя бы один день недели попадает в фильтр». Если диапазон
  // не задан — возвращаем все недели. Если у недели нет дат (period_start/end
  // null) — не прячем её (безопасный fallback).
  function rnpVisibleWeeks(weeks) {
    const from = rnpState.periodFrom, to = rnpState.periodTo;
    if (!from && !to) return weeks;
    const lo = from || '0000-01-01';
    const hi = to || '9999-12-31';
    return (weeks || []).filter(w => {
      const ps = w.period_start, pe = w.period_end;
      if (!ps || !pe) return true;
      return ps <= hi && pe >= lo;
    });
  }

  // ------------------------------------------------------------------
  // Дельта неделя-к-неделе (как в отчёте «Недели API v2»)
  // pct/pct_node -> разница в пунктах (тек − пред), вывод как X,X%
  // rub/qty/price -> относительное изменение (тек − пред)/пред, вывод X,X%
  // ------------------------------------------------------------------
  function calcDelta(cur, prev, kind) {
    if (cur === null || cur === undefined || isNaN(cur)) return null;
    if (prev === null || prev === undefined || isNaN(prev)) return null;
    if (kind === 'pct' || kind === 'pct_node') {
      return { val: cur - prev, mode: 'pp' };
    }
    if (prev === 0) return null;
    return { val: (cur - prev) / prev, mode: 'rel' };
  }

  // форматирование дельты в компактную подпись со знаком, фракция -> X,X%
  function fmtDelta(d) {
    if (!d) return '';
    const sign = d.val > 0 ? '+' : (d.val < 0 ? '−' : '');
    const pct = Math.abs(d.val * 100).toFixed(1).replace('.', ',');
    return `${sign}${pct}%`;
  }

  // CSS-класс направления дельты (нейтральная подсветка: рост/падение)
  function deltaDir(d) {
    if (!d || d.val === 0) return 'flat';
    return d.val > 0 ? 'up' : 'down';
  }

  // CSS-класс дельты С УЧЁТОМ ПОЛЯРНОСТИ метрики:
  //   'up'   — зелёный (хорошее изменение), 'down' — красный (плохое).
  // polarity: 'pos' — рост хорошо; 'neg' — рост плохо (расходы);
  // 'none' — дельта не красится/не показывается (обрабатывается выше).
  function deltaDirPolar(d, polarity) {
    if (!d || d.val === 0) return 'flat';
    const rising = d.val > 0;
    // для 'neg' инвертируем: рост расхода — плохо (красный 'down')
    const good = (polarity === 'neg') ? !rising : rising;
    return good ? 'up' : 'down';
  }

  // ячейка «значение + дельта» (две под-колонки в одной td) для таблицы показателей.
  // mt (необязательно) задаёт цвет/жирность ЗНАЧЕНИЯ — как у метрик товара,
  // чтобы одноимённые групповые метрики красились так же. Дельта остаётся
  // нейтральной (зелёная/красная по направлению).
  function valDeltaCell(curCell, prevCell, key, kind, mt) {
    const cur = curCell ? curCell[key] : null;
    const prev = prevCell ? prevCell[key] : null;
    const d = calcDelta(cur, prev, kind);
    const valTxt = rnpFmt(cur, kind, key);
    const dTxt = fmtDelta(d);
    let st = '';
    if (mt && mt.color) st += `color:#${mt.color};`;
    if (mt && mt.bold)  st += 'font-weight:700;';
    const styleAttr = st ? ` style="${st}"` : '';
    return `<span class="rnp-vc">`
      + `<span class="rnp-vc-v"${styleAttr}>${valTxt}</span>`
      + (dTxt ? `<span class="rnp-vc-d ${deltaDir(d)}">${dTxt}</span>` : `<span class="rnp-vc-d flat"></span>`)
      + `</span>`;
  }

  // Форматирование значения ячейки по типу метрики (метрики ГРУПП)
  function rnpFmt(val, kind, key) {
    if (val === null || val === undefined || isNaN(val)) return '—';
    if (kind === 'pct_node' || kind === 'pct') return rp(val);
    if (kind === 'rub') return U.fmtMoneyShort(val);
    if (kind === 'qty') return U.fmtNum(val);
    if (kind === 'price') return U.fmtMoney(val);
    if (key === 'rating') return U.fmtNum(val, 1);  // Рейтинг: 4,8
    return U.fmtNum(val);
  }

  // Форматирование значения метрики ТОВАРА (по эталону):
  //   pct   -> 0,0%
  //   qty   -> целое число (#,##0)
  //   rub   -> #,##0 — число без символа «₽» и без сокращения
  //   price -> #,##0,0 — удельная (Логистика на ед.) с 1 знаком, ср. цена — целое
  function rnpFmtProduct(val, kind, key) {
    if (val === null || val === undefined || isNaN(val)) return '—';
    if (kind === 'pct') return rp(val);
    if (kind === 'qty') return U.fmtNum(val);
    if (kind === 'price') return U.fmtNum(val, key === 'p_log_per_unit' ? 1 : 0);
    if (kind === 'rub') return U.fmtNum(val);
    if (key === 'rating') return U.fmtNum(val, 1);  // Рейтинг: 4,8
    return U.fmtNum(val);
  }

  // ячейка «значение + дельта» для метрик ТОВАРА: значение с собственным
  // цветом/жирностью метрики; дельта к прошлой неделе — нейтральная подсветка
  function valDeltaCellProduct(curCell, prevCell, mt) {
    const cur = curCell ? curCell[mt.key] : null;
    const prev = prevCell ? prevCell[mt.key] : null;
    const valTxt = rnpFmtProduct(cur, mt.kind, mt.key);
    let st = '';
    if (mt.color) st += `color:#${mt.color};`;
    if (mt.bold) st += 'font-weight:700;';
    const styleAttr = st ? ` style="${st}"` : '';
    // polarity 'none' (План продаж) — дельту НЕ показываем вовсе:
    // план ставится вручную, его «изменение» — наше решение, не факт.
    if (mt.polarity === 'none') {
      return `<span class="rnp-vc">`
        + `<span class="rnp-vc-v"${styleAttr}>${valTxt}</span>`
        + `<span class="rnp-vc-d flat"></span>`
        + `</span>`;
    }
    const d = calcDelta(cur, prev, mt.kind);
    const dTxt = fmtDelta(d);
    const dir = deltaDirPolar(d, mt.polarity);
    return `<span class="rnp-vc">`
      + `<span class="rnp-vc-v"${styleAttr}>${valTxt}</span>`
      + (dTxt ? `<span class="rnp-vc-d ${dir}">${dTxt}</span>` : `<span class="rnp-vc-d flat"></span>`)
      + `</span>`;
  }

  // CSS-класс для значения РНП (порог рентабельности)
  function rnpClass(frac) {
    if (frac === null || frac === undefined || isNaN(frac)) return '';
    if (frac >= 0.15) return 'good';
    if (frac < 0) return 'bad';
    return 'warn';
  }

  // Класс подсветки «светофор» для значения РНП % (frac хранится во фракции 0..1):
  //   • красный  (rnp-sig-neg) — frac < 0, если включена галка «отрицательная маржинальность»;
  //   • оранжевый (rnp-sig-low) — 0 <= frac < целевой, если задана «целевая маржинальность %»;
  //   • иначе — без класса (значение остаётся обычным чёрным).
  // marginTarget пользователь вводит в ПРОЦЕНТАХ (напр. 5), поэтому делим на 100
  // для сравнения с фракцией.
  function marginSignalClass(frac) {
    if (frac === null || frac === undefined || isNaN(frac)) return '';
    if (rnpState.marginNegOn && frac < 0) return 'rnp-sig-neg';
    const tgtRaw = String(rnpState.marginTarget == null ? '' : rnpState.marginTarget).replace(',', '.').trim();
    if (tgtRaw !== '') {
      const tgt = parseFloat(tgtRaw);
      if (!isNaN(tgt) && frac >= 0 && frac < tgt / 100) return 'rnp-sig-low';
    }
    return '';
  }

  // ------------------------------------------------------------------
  // Главная функция вкладки РНП UNIT
  // ------------------------------------------------------------------
  // Подтабы раздела «РНП Юнит-экономика»: Wildberries, OZON и (для админа)
  // «Загрузка данных» — прижата вправо, нейтральный серый стиль 'cross'
  // (по образцу подтабов «РНП продажи»).
  // Хелперы маппинга текущего МП юнит-экономики (rnpState.mp) → параметр API
  // и → значение маркетплейса в БД. Единая точка правды, чтобы не плодить
  // тернарники (иначе Яндекс «утекал» бы в ozon).
  function rnpMpParam() {
    return rnpState.mp === 'wb' ? 'wb'
         : rnpState.mp === 'yandex' ? 'yandex' : 'ozon';
  }
  function rnpMpDbName() {
    return rnpState.mp === 'wb' ? 'Wildberries'
         : rnpState.mp === 'yandex' ? 'Yandex' : 'Ozon';
  }
  function rnpMpCls() {
    return rnpState.mp === 'cross' ? 'cross'
         : rnpState.mp === 'wb' ? 'wb'
         : rnpState.mp === 'yandex' ? 'yandex' : 'ozon';
  }

  function rnpSubtabs(host) {
    if (!host) return;
    const tabs = [['wb', 'Wildberries', 'wb'], ['ozon', 'OZON', 'ozon'],
                  ['yandex', 'Yandex', 'yandex']];
    const __u = API.getUser && API.getUser();
    const __isAdmin = !!(__u && (__u.role || '').toLowerCase() === 'admin');
    // Эндпоинты загрузки защищены require_admin — таб только для админов.
    if (__isAdmin) tabs.push(['upload', 'Загрузка данных', 'cross', true]);
    tabs.forEach(([id, label, c, pushRight]) => {
      const b = document.createElement('button');
      b.className = 'subtab ' + c + (rnpState.mp === id ? ' active' : '') + (pushRight ? ' subtab-right' : '');
      b.textContent = label;
      b.addEventListener('click', () => {
        if (id === rnpState.mp) return;
        // Сохраняем снимок раскрытия+scroll ТЕКУЩЕГО МП перед уходом.
        rnpUiSnapSave(rnpState, rnpState.mp);
        rnpState.mp = id;
        // Списки менеджеров/статусов у каждого МП свои — сбрасываем
        // фильтры, иначе фильтр по менеджеру из прошлого МП «повисает»
        // (dropdown показывает «Все», а таблица пустая).
        rnpState.mgrFilter = ''; rnpState.statusFilter = '';
        // Восстанавливаем снимок НОВОГО МП (пустой → свежее состояние).
        rnpUiSnapLoad(rnpState, id);
        rnpState.data = null; rnpState.filterExpandSig = '';
        App.renderView();
      });
      host.appendChild(b);
    });
  }

  async function rnp(root, ctl, state) {
    // Верхняя панель страницы (ctl) больше НЕ используется для РНП —
    // вкладки и фильтры переехали ВНУТРЬ карточки (тулбар .rnp-toolbar),
    // как в разделе «Продажи, шт». Заголовок страницы остаётся стандартным.
    ctl.innerHTML = '';
    ctl.className = 'ctl';

    // ЗАГРУЗКА ДАННЫХ — подраздел (только админ): еженедельные/ежемесячные
    // отчёты MPPROFIT (Ozon + Wildberries) + общий журнал загрузок.
    // Перенесён сюда из бывшего раздела главного меню «Загрузка».
    if (rnpState.mp === 'upload') {
      root.innerHTML = `
        <div class="card rnp-card mp-cross">
          <div class="rnp-toolbar"><div class="subtabs" id="rnp-subtabs"></div></div>
        </div>
        <div class="up-hint">
          Подходит как еженедельный, так и ежемесячный формат отчётов MPPROFIT — система определит тип автоматически.
        </div>
        <div class="grid-3">
          <div class="card">
            <h3>Ozon — загрузка отчёта</h3>
            <div class="upload-zone" id="uz-ozon">
              <div style="font-size:28px;">⬆️</div>
              <div>Перетащите Excel сюда или <a href="#" id="pick-ozon">выберите файл</a></div>
              <div class="muted" style="font-size:12px;margin-top:6px;">формат MPPROFIT, .xlsx · неделя или месяц</div>
              <input type="file" id="file-ozon" accept=".xlsx,.xls" class="hidden">
            </div>
            <label class="fact-date-row">Факт по дату (для месячного отчёта):
              <input type="date" id="fact-date-ozon" class="input-sm">
            </label>
            <div class="muted fact-date-hint">Нужно только для месячного отчёта — до какого числа собран факт. Влияет на прогноз выполнения плана. Пусто — месяц считается полным.</div>
          </div>
          <div class="card">
            <h3>Wildberries — загрузка отчёта</h3>
            <div class="upload-zone" id="uz-wb">
              <div style="font-size:28px;">⬆️</div>
              <div>Перетащите Excel сюда или <a href="#" id="pick-wb">выберите файл</a></div>
              <div class="muted" style="font-size:12px;margin-top:6px;">формат MPPROFIT, .xlsx · неделя или месяц</div>
              <input type="file" id="file-wb" accept=".xlsx,.xls" class="hidden">
            </div>
            <label class="fact-date-row">Факт по дату (для месячного отчёта):
              <input type="date" id="fact-date-wb" class="input-sm">
            </label>
            <div class="muted fact-date-hint">Нужно только для месячного отчёта — до какого числа собран факт. Влияет на прогноз выполнения плана. Пусто — месяц считается полным.</div>
          </div>
          <div class="card">
            <h3>Yandex — загрузка отчёта</h3>
            <div class="upload-zone" id="uz-yandex">
              <div style="font-size:28px;">⬆️</div>
              <div>Перетащите Excel сюда или <a href="#" id="pick-yandex">выберите файл</a></div>
              <div class="muted" style="font-size:12px;margin-top:6px;">формат MPPROFIT, .xlsx · неделя или месяц</div>
              <input type="file" id="file-yandex" accept=".xlsx,.xls" class="hidden">
            </div>
            <label class="fact-date-row">Факт по дату (для месячного отчёта):
              <input type="date" id="fact-date-yandex" class="input-sm">
            </label>
            <div class="muted fact-date-hint">Нужно только для месячного отчёта — до какого числа собран факт. Влияет на прогноз выполнения плана. Пусто — месяц считается полным.</div>
          </div>
        </div>
        <div class="card">
          <div class="up-hist-head">
            <h3 style="margin:0;">Журнал загрузок</h3>
            <div class="seg" id="hist-filter">
              <button class="seg-btn active" data-kind="unit_econ">Все</button>
              <button class="seg-btn" data-kind="weekly">Неделя</button>
              <button class="seg-btn" data-kind="monthly">Месяц</button>
            </div>
          </div>
          <div id="up-history"><div class="loader"><span class="spinner"></span></div></div>
        </div>`;
      rnpSubtabs(root.querySelector('#rnp-subtabs'));
      setupUploadZone(root, 'ozon');
      setupUploadZone(root, 'wb');
      setupUploadZone(root, 'yandex');
      const fbar = root.querySelector('#hist-filter');
      if (fbar) {
        fbar.addEventListener('click', async (e) => {
          const btn = e.target.closest('.seg-btn');
          if (!btn) return;
          fbar.querySelectorAll('.seg-btn').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          await loadHistory(btn.dataset.kind);
        });
      }
      await loadHistory('unit_econ');
      return;
    }

    // Тулбар (вкладки + фильтры) липнет при скролле под шапкой приложения.
    // Шапка таблицы прилипает ровно под ним — высота тулбара
    // измеряется после рендера карточки (см. syncRnpFiltersHeight в конце).

    root.innerHTML = '<div class="loading">Загрузка отчёта…</div>';

    // Единый рендер для обоих маркетплейсов: берём активный (Ozon/WB).
    // Бэкенд сам учитывает специфику WB (реклама из wb_ads_total, штрафы).
    const isCross = rnpState.mp === 'cross';
    // Если текущая rnpState.topMetric отсутствует в АКТУАЛЬНОМ (отфильтрованном
    // по режиму) наборе верхних метрик — сбрасываем на 'rnp'. Покрывает и cross,
    // и переключение WB→Ozon (когда выбрана была недоступная там «Штрафы, руб.»).
    if (!rnpTopMetrics().some(m => m.key === rnpState.topMetric)) rnpState.topMetric = 'rnp';
    const data = isCross
      ? await API.rnpTreeCross()
      : await API.rnpTree({ marketplace: rnpMpParam() });
    rnpState.data = data;

    // Узел «Итоги» (корень) раскрыт по умолчанию при первой загрузке,
    // если пользователь ещё не менял состояние раскрытия вручную
    if (data.tree && data.tree.key && Object.keys(rnpState.expanded).length === 0) {
      rnpState.expanded[data.tree.key] = true;
    }

    const weeks = data.weeks || [];
    const metrics = data.metrics || [];
    // отдельный набор метрик для ТОВАРОВ (листьев дерева)
    rnpState.productMetrics = data.product_metrics || [];
    const undist = data.undistributed || { count: 0, articles: [] };

    if (!weeks.length) {
      const lbl = rnpState.mp === 'cross' ? 'данным'
        : (rnpState.mp === 'wb' ? 'Wildberries' : (rnpState.mp === 'yandex' ? 'Yandex' : 'OZON'));
      root.innerHTML = `<div class="empty">Нет загруженных недель по ${lbl}.</div>`;
      return;
    }

    // служебное сообщение о нераспределённых товарах.
    // Число кликабельно: ведёт в Справочник с включённым фильтром «только нераспределённые».
    let undistHtml = '';
    if (undist.count > 0) {
      undistHtml = `<div class="rnp-warn">
        <div class="rnp-warn-h">⚠️ Требуют распределения в справочнике: <a href="#" id="rnp-undist-link" class="rnp-warn-count">${undist.count}</a></div>
        <div class="rnp-warn-d">Эти товары есть в справочнике, но у них не заполнены группы (К1/К2/К3), статус или менеджер, поэтому не попадают в иерархию (их обороты учтены только в строке «Итоги»). Заполните группы, статус и менеджера в разделе «Справочник».</div>
      </div>`;
    }

    // легенда недель — выровнена ровно над колонками РНП в строках узлов.
    // Каждый элемент шириной с колонку (var(--rnp-col)), заголовок по центру.
    const wkLegend = weeks.map(w =>
      `<span class="rnp-wk-leg">${weekColHead(w)}</span>`).join('');

    // Списки менеджеров/статусов и набор недель/метрик сохраняем в состоянии:
    // — списки нужны для общей панели фильтров наверху (в т.ч. на вкладке-заглушке);
    // — weeks/metrics нужны обработчикам фильтров, которые живут в верхней панели.
    rnpState.managers = data.managers || [];
    rnpState.statuses = data.statuses || [];
    rnpState.weeks = weeks;
    rnpState.metrics = metrics;
    // По умолчанию при первой загрузке — текущий месяц (относительно базовой
    // даты = последняя загруженная неделя). Выставляем только если период
    // ещё не выбран пользователем (чтобы не сбрасывать его выбор при переключении МП).
    if (!rnpState.periodFrom && !rnpState.periodTo && weeks.length) {
      const r = presetRange('month');
      rnpState.periodFrom = r.from;
      rnpState.periodTo = r.to;
    }
    // Опции фильтров и подпись периода обновляются ПОСЛЕ построения
    // панели фильтров внутри карточки (см. ниже после buildRnpFilters).

    root.innerHTML = `
      ${undistHtml}
      <div class="card rnp-card mp-${rnpMpCls()}">
        <div class="rnp-toolbar">
          <div class="subtabs" id="rnp-subtabs"></div>
          <div class="rnp-filters-host" id="rnp-filters-host"></div>
        </div>
        <div class="rnp-head-sticky">
          <div class="rnp-card-title">Юнит-экономика по неделям</div>
          <div class="rnp-legend-row">
            <span class="rnp-legend-fill rnp-fixleft"></span>
            ${rnpState.mp === 'cross'
              ? `<span class="rnp-status-leg rnp-status-leg2 rnp-fixleft">Статус OZ</span><span class="rnp-status-leg rnp-status-leg2b rnp-fixleft">Статус WB</span>`
              : `<span class="rnp-status-leg rnp-fixleft">Статус</span>`}
            <span class="rnp-legend-mspacer rnp-fixleft"></span>
            <div class="rnp-wk-scroll" data-rnp-scroll><div class="rnp-wk-legend">${wkLegend}</div></div>
          </div>
        </div>
        <div class="tbl-wrap rnp-matrix-wrap">
          <div id="rnp-matrix"></div>
        </div>
        <div class="rnp-hscroll-bar" data-rnp-scroll><div class="rnp-hscroll-phantom"></div></div>
      </div>`;

    // Клик по числу нераспределённых → Справочник, отфильтрованный РОВНО по тем
    // артикулам, что РНП пометил нераспределёнными для текущего маркетплейса.
    // Так число строк в справочнике совпадает со счётчиком в РНП.
    const undistLink = root.querySelector('#rnp-undist-link');
    if (undistLink) {
      undistLink.addEventListener('click', (e) => {
        e.preventDefault();
        const arts = (undist.articles || []).map(a => a.seller_article).filter(Boolean);
        catState.search = '';
        catState.only_unc = false;
        catState.undistArticles = arts;
        // Справочник открываем на том же маркетплейсе, что и РНП.
        catState.mp = rnpMpParam();
        catState.tree = null;  // дерево фильтров зависит от МП — перезагрузится
        catState.undistMp = catMpName();
        App.setView('catalog');
      });
    }

    // Вкладки маркетплейсов (WB → OZON) и фильтры строим ВНУТРИ тулбара карточки.
    const subtabsHost = root.querySelector('#rnp-subtabs');
    if (subtabsHost) rnpSubtabs(subtabsHost);
    // Общая панель фильтров (поиск + менеджер + статус + показатель + светофор + период).
    const filtersHost = root.querySelector('#rnp-filters-host');
    if (filtersHost) buildRnpFilters(filtersHost);
    // Опции фильтров и подпись периода — по текущим данным (панель только что собрана).
    refreshRnpFilterOptions();
    refreshRnpPeriodLabel();

    renderRnpMatrix(data.tree, weeks, metrics);
    // Восстанавливаем вертикальную прокрутку окна на место, где были до
    // ухода (по текущему МП). Состояние раскрытия уже применено (rnpState.expanded).
    rnpUiScrollApply(rnpState, rnpState.mp);

    // Тулбар (.rnp-toolbar), шапка таблицы (.rnp-head-sticky) и строка «Итоги» появились
    // в DOM — пересчитываем высоты липких элементов, чтобы шапка и «Итоги»
    // прилипали ровно под тулбаром.
    syncRnpFiltersHeight();
  }

  // ------------------------------------------------------------------
  // КАТАЛОГ МЕТРИК «ВЕРХНЕГО УРОВНЯ» (показатель в строке-заголовке узла).
  // Первый пункт — маржинальность (дефолт). isPct управляет светофором и
  // доступностью поля «Целевой показатель»/галки «Отрицательная маржа».
  // Ключи берём ровно те, что лежат в node.cells на ВСЕХ уровнях: для
  // процентов удержаний на уровне групп используются p_*-ключи (как в
  // productMetricRows), чтобы значения совпадали с карточкой метрик.
  // ------------------------------------------------------------------
  const RNP_TOP_METRICS = [
    { key: 'rnp',           label: 'Маржинальность %',              kind: 'pct_node', isPct: true },
    { key: 'orders_qty',    label: 'Заказы, шт',                    kind: 'qty',      isPct: false },
    { key: 'sales_qty',     label: 'Продажи, шт',                   kind: 'qty',      isPct: false },
    { key: 'p_cogs_pct',    label: 'Себестоимость от выручки, %',   kind: 'pct',      isPct: true },
    { key: 'avg_price',     label: 'Ср. цена продажи',              kind: 'price',    isPct: false },
    { key: 'p_holds_pct',   label: 'Все удержания, %',              kind: 'pct',      isPct: true },
    { key: 'p_promo_pct',   label: 'Продвижение, %',                kind: 'pct',      isPct: true },
    { key: 'p_storage_pct', label: 'Хранение и проч. удержания, %', kind: 'pct',      isPct: true },
    { key: 'p_fines_rub',   label: 'Штрафы, руб.',                  kind: 'rub',      isPct: false },
    { key: 'p_fines_pct',   label: 'Штрафы, %',                     kind: 'pct',      isPct: true },
  ];

  // Набор показателей «верхнего уровня» для режима «Сводная» (агрегат Ozon+WB).
  // Ключи совпадают с c_*-ключами cross-ячейки. Маржинальность (rnp) — общая.
  const RNP_TOP_METRICS_CROSS = [
    { key: 'rnp',          label: 'Маржинальность %', kind: 'pct_node', isPct: true },
    { key: 'c_orders_qty', label: 'Заказы, шт',       kind: 'qty',      isPct: false },
    { key: 'c_sales_qty',  label: 'Продажи, шт',      kind: 'qty',      isPct: false },
    { key: 'c_revenue',    label: 'Продажи, руб',     kind: 'rub',      isPct: false },
    { key: 'c_profit',     label: 'Прибыль, руб',     kind: 'rub',      isPct: false },
    { key: 'c_holds_pct',  label: 'Все удержания, %', kind: 'pct',      isPct: true },
  ];

  // Текущий набор верхних метрик по режиму (cross → отдельный набор).
  // «Штрафы, руб.» (p_fines_rub) — только для Wildberries (в Ozon штрафов в
  // рублях нет), поэтому в остальных режимах опция исключается.
  function rnpTopMetrics() {
    if (rnpState.mp === 'cross') return RNP_TOP_METRICS_CROSS;
    return RNP_TOP_METRICS.filter(m => !(m.key === 'p_fines_rub' && rnpState.mp !== 'wb'));
  }

  // Видна ли метрика с данным ключом при текущем маркетплейсе.
  // «Штрафы, руб.» (p_fines_rub) — только Wildberries (в Ozon/Сводной штрафов
  // в рублях нет). Применяется единообразно к строкам групп и товара.
  function rnpMetricVisible(key) {
    return !(key === 'p_fines_rub' && rnpState.mp !== 'wb');
  }

  // Текущая запись каталога по rnpState.topMetric (дефолт — маржинальность).
  function getTopMetricDef() {
    const list = rnpTopMetrics();
    return list.find(m => m.key === rnpState.topMetric) || list[0];
  }

  // Блокирует/разблокирует светофорные элементы («Отрицательная маржа» +
  // «Целевой показатель») в зависимости от того, процентная ли верхняя
  // метрика. Для штучных/денежных — disabled + приглушение (НЕ скрываем).
  function updateTargetFieldState() {
    const isPct = getTopMetricDef().isPct;
    const negEl = document.getElementById('rnp-margin-neg');
    const tgtEl = document.getElementById('rnp-margin-target');
    const checkWrap = negEl ? negEl.closest('.rnp-sig-check') : null;
    const tgtWrap = tgtEl ? tgtEl.closest('.rnp-sig-target') : null;
    const tip = 'Доступно только для процентных показателей';
    if (negEl) negEl.disabled = !isPct;
    if (tgtEl) tgtEl.disabled = !isPct;
    if (checkWrap) {
      checkWrap.style.opacity = isPct ? '' : '0.45';
      checkWrap.title = isPct ? 'Подсвечивать РНП % менее нуля красным' : tip;
    }
    if (tgtWrap) {
      tgtWrap.style.opacity = isPct ? '' : '0.45';
      tgtWrap.title = isPct ? 'Всё, что выше 0 и ниже целевой, подсветится оранжевым' : tip;
    }
  }

  // ------------------------------------------------------------------
  // ОБЩАЯ ПАНЕЛЬ ФИЛЬТРОВ (в верхней шапке, рядом с суб-табами).
  // Общая для обоих маркетплейсов; состояние хранится в rnpState
  // и сохраняется при переключении. Применяется к тому, что сейчас открыто.
  // ------------------------------------------------------------------
  function buildRnpFilters(ctl) {
    const managers = rnpState.managers || [];
    const mgrOptions = ['<option value="">Все менеджеры</option>']
      .concat(managers.map(m =>
        `<option value="${U.esc(m)}"${rnpState.mgrFilter === m ? ' selected' : ''}>${U.esc(m)}</option>`))
      .join('');
    // Статусы — весь предустановленный набор (не только встречающиеся).
    const statusOptions = statusFilterOptionsHtml();
    // Показатель верхнего уровня — опции из каталога RNP_TOP_METRICS.
    const topMetricOptions = rnpTopMetrics().map(m =>
      `<option value="${U.esc(m.key)}"${rnpState.topMetric === m.key ? ' selected' : ''}>${U.esc(m.label)}</option>`)
      .join('');

    const wrap = document.createElement('div');
    wrap.className = 'rnp-filters';
    const negChecked = rnpState.marginNegOn ? ' checked' : '';
    wrap.innerHTML = `
      <input class="input-sm" id="rnp-search-art" placeholder="Поиск по артикулу…" value="${U.esc(rnpState.searchArt || '')}">
      <select id="rnp-mgr-filter">${mgrOptions}</select>
      <select id="rnp-status-filter">${statusOptions}</select>
      <select id="rnp-top-metric" title="Показатель в строке-заголовке узла">${topMetricOptions}</select>
      <label class="rnp-sig-check" title="Подсвечивать РНП % менее нуля красным">
        <input type="checkbox" id="rnp-margin-neg"${negChecked}>
        <span>Отрицательная маржа</span>
      </label>
      <div class="rnp-sig-target" title="Всё, что выше 0 и ниже целевой, подсветится оранжевым">
        <input class="input-sm" id="rnp-margin-target" type="number" min="0" step="0.1" inputmode="decimal"
          placeholder="Цель" title="Целевой показатель маржинальности, %" value="${U.esc(rnpState.marginTarget || '')}">
        <span class="rnp-sig-target-suf">%</span>
      </div>
      <button class="btn-sm" id="rnp-expand" type="button" title="Развернуть все группировки">Развернуть всё</button>
      <button class="btn-sm" id="rnp-collapse" type="button" title="Свернуть все группировки до корня («Итоги»)">Свернуть всё</button>
      <div class="rnp-period" id="rnp-period">
        <button class="rnp-period-field" id="rnp-period-field" type="button" title="Выбрать период (недели показываются, если хотя бы один их день попадает в диапазон)">
          <svg class="rnp-period-ico" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/></svg>
          <span class="rnp-period-text" id="rnp-period-text">${U.esc(periodLabel())}</span>
        </button>
        <div class="rnp-cal" id="rnp-cal" hidden></div>
      </div>`;
    ctl.appendChild(wrap);

    // Обработчики работают по активному маркетплейсу: перерисовывают
    // матрицу только если есть загруженные данные (Ozon). На заглушке WB
    // поля просто запоминают выбор в rnpState (применится при переходе на Ozon).
    function applyFilters() {
      if (rnpState.data && rnpState.data.tree) {
        renderRnpMatrix(rnpState.data.tree, rnpState.weeks || [], rnpState.metrics || []);
      }
    }
    const searchArtEl = wrap.querySelector('#rnp-search-art');
    const mgrFilterEl = wrap.querySelector('#rnp-mgr-filter');
    const statusFilterEl = wrap.querySelector('#rnp-status-filter');
    let debF;
    searchArtEl.addEventListener('input', () => {
      clearTimeout(debF);
      debF = setTimeout(() => {
        rnpState.searchArt = searchArtEl.value.trim();
        applyFilters();
      }, 300);
    });
    mgrFilterEl.addEventListener('change', () => {
      rnpState.mgrFilter = mgrFilterEl.value;
      applyFilters();
    });
    statusFilterEl.addEventListener('change', () => {
      rnpState.statusFilter = statusFilterEl.value;
      applyFilters();
    });

    // Селектор показателя верхнего уровня: меняем метрику в шапке узлов,
    // обновляем доступность светофорных полей и перерисовываем матрицу.
    const topMetricEl = wrap.querySelector('#rnp-top-metric');
    topMetricEl.addEventListener('change', () => {
      rnpState.topMetric = topMetricEl.value;
      updateTargetFieldState();
      applyFilters();
    });

    // Светофор маржинальности: галка «отрицательная» (красный) и
    // поле «целевая» (оранжевый). Оба сохраняют состояние в rnpState
    // и перерисовывают матрицу (общие для обоих маркетплейсов).
    const marginNegEl = wrap.querySelector('#rnp-margin-neg');
    const marginTargetEl = wrap.querySelector('#rnp-margin-target');
    marginNegEl.addEventListener('change', () => {
      rnpState.marginNegOn = marginNegEl.checked;
      applyFilters();
    });
    let debT;
    marginTargetEl.addEventListener('input', () => {
      clearTimeout(debT);
      debT = setTimeout(() => {
        rnpState.marginTarget = marginTargetEl.value.trim();
        applyFilters();
      }, 300);
    });

    // Кнопка «Свернуть»: быстро схлопывает всё дерево до корня «Итоги»,
    // чтобы после работы с фильтрами не приходилось закрывать группировки вручную.
    // Состояние как на скриншоте: раскрыт только корень, видны узлы lvl-1
    // (Продукция ECOM, ТД АВТОПРОФИ) в свёрнутом виде; таблицы метрик тоже закрыты.
    // Не трогает фильтры/поиск — при активном фильтре дерево всё равно раскрывается
    // автоматически (чтобы результаты были видны), поэтому кнопка полезна после сброса фильтров.
    const collapseEl = wrap.querySelector('#rnp-collapse');
    if (collapseEl) {
      collapseEl.addEventListener('click', () => {
        const rootKey = rnpState.data && rnpState.data.tree ? rnpState.data.tree.key : null;
        rnpState.expanded = {};
        rnpState.metricsOpen = {};
        if (rootKey) rnpState.expanded[rootKey] = true;
        applyFilters();
      });
    }
    // Кнопка «Развернуть всё» — как в РНП заказы: раскрывает все группировки
    // дерева (таблицы метрик metricsOpen не трогаем — аналогично rnpsExpandAll).
    const expandEl = wrap.querySelector('#rnp-expand');
    if (expandEl) {
      expandEl.addEventListener('click', () => {
        if (rnpState.data && rnpState.data.tree) rnpExpandAll(rnpState.data.tree);
        applyFilters();
      });
    }

    bindRnpPeriod(wrap, applyFilters);
    // Стартовое состояние светофорных полей по текущей верхней метрике.
    updateTargetFieldState();
  }

  // Обновляет текст в поле периода по текущему rnpState.periodFrom/To.
  // Поле может ещё не существовать — тогда просто ничего не делаем.
  function refreshRnpPeriodLabel() {
    const el = document.getElementById('rnp-period-text');
    if (el) el.textContent = periodLabel();
  }

  // Привязывает поле периода и поповер-календарь (два месяца, выбор диапазона,
  // пресеты Тек.месяц/квартал/год). Два клика по дням задают диапазон.
  function bindRnpPeriod(wrap, applyFilters) {
    const periodWrap = wrap.querySelector('#rnp-period');
    const periodFieldEl = wrap.querySelector('#rnp-period-field');
    const calEl = wrap.querySelector('#rnp-cal');
    if (!periodFieldEl || !calEl) return;

    const MONTHS_RU = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
    const DOW_RU = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
    // Черновик выбора (до применения). from/to — ISO-строки.
    const cal = { from: '', to: '', view: null, open: false };

    function openCal() {
      cal.from = rnpState.periodFrom || '';
      cal.to = rnpState.periodTo || '';
      const b = cal.from ? parseISO(cal.from) : periodBaseDate();
      cal.view = new Date(b.getFullYear(), b.getMonth(), 1);
      cal.open = true;
      calEl.hidden = false;
      periodFieldEl.classList.add('open');
      drawCal();
    }
    function closeCal() {
      cal.open = false;
      calEl.hidden = true;
      periodFieldEl.classList.remove('open');
    }
    function applyPeriod() {
      rnpState.periodFrom = cal.from || '';
      rnpState.periodTo = cal.to || '';
      refreshRnpPeriodLabel();
      closeCal();
      applyFilters();
    }
    function monthHtml(first) {
      const y = first.getFullYear(), mo = first.getMonth();
      const title = `${MONTHS_RU[mo]} ${y}`;
      let lead = first.getDay() - 1; if (lead < 0) lead = 6;
      const daysIn = new Date(y, mo + 1, 0).getDate();
      const f = cal.from, t = cal.to;
      const lo = (f && t) ? (f <= t ? f : t) : f;
      const hi = (f && t) ? (f <= t ? t : f) : f;

      // Ячейка одного дня. adjacent=true — день соседнего месяца (серый).
      // posInRow — позиция в строке 0..6 (для скругления краёв полосы по краям недели).
      function dayCell(dt, adjacent, posInRow) {
        const iso = isoDate(dt);
        let c = 'rnp-cal-cell day';
        if (adjacent) c += ' adjacent';
        let inRange = false, isEnd = false;
        if (lo && hi) {
          if (iso === lo || iso === hi) { isEnd = true; }
          else if (iso > lo && iso < hi) { inRange = true; }
        } else if (f && iso === f) { isEnd = true; }
        if (inRange) c += ' in-range';
        if (isEnd) c += ' end';
        // Скругление краёв светлой полосы: слева — у левого края диапазона
        // или начала недели; справа — у правого края диапазона или конца недели.
        if (inRange || isEnd) {
          if (iso === lo || posInRow === 0) c += ' band-l';
          if (iso === hi || posInRow === 6) c += ' band-r';
        }
        return `<button type="button" class="${c}" data-cal-day="${iso}"><span class="d">${dt.getDate()}</span></button>`;
      }

      let cells = '', pos = 0;
      // Ведущие дни предыдущего месяца (серые), чтобы не было пустых клеток.
      for (let i = lead; i > 0; i--) {
        cells += dayCell(new Date(y, mo, 1 - i), true, pos); pos = (pos + 1) % 7;
      }
      // Дни текущего месяца.
      for (let d = 1; d <= daysIn; d++) {
        cells += dayCell(new Date(y, mo, d), false, pos); pos = (pos + 1) % 7;
      }
      // Замыкающие дни следующего месяца — добиваем сетку до 6 строк (42 ячейки).
      const fill = 42 - (lead + daysIn);
      for (let i = 1; i <= fill; i++) {
        cells += dayCell(new Date(y, mo + 1, i), true, pos); pos = (pos + 1) % 7;
      }
      return `<div class="rnp-cal-month">
        <div class="rnp-cal-mtitle">${title}</div>
        <div class="rnp-cal-dow">${DOW_RU.map(x => `<span>${x}</span>`).join('')}</div>
        <div class="rnp-cal-grid">${cells}</div>
      </div>`;
    }
    function drawCal() {
      const m1 = new Date(cal.view.getFullYear(), cal.view.getMonth(), 1);
      const m2 = new Date(cal.view.getFullYear(), cal.view.getMonth() + 1, 1);
      calEl.innerHTML = `
        <div class="rnp-cal-nav">
          <button type="button" class="rnp-cal-arrow" data-cal-nav="-1" title="Предыдущий месяц">‹</button>
          <button type="button" class="rnp-cal-arrow" data-cal-nav="1" title="Следующий месяц">›</button>
        </div>
        <div class="rnp-cal-months">${monthHtml(m1)}${monthHtml(m2)}</div>
        <div class="rnp-cal-presets">
          <button type="button" class="rnp-cal-preset" data-cal-preset="month">Тек. месяц</button>
          <button type="button" class="rnp-cal-preset" data-cal-preset="quarter">Тек. квартал</button>
          <button type="button" class="rnp-cal-preset" data-cal-preset="year">Тек. год</button>
        </div>`;
    }

    periodFieldEl.addEventListener('click', (e) => {
      e.stopPropagation();
      cal.open ? closeCal() : openCal();
    });
    calEl.addEventListener('click', (e) => {
      e.stopPropagation();
      const nav = e.target.closest('[data-cal-nav]');
      if (nav) {
        const delta = Number(nav.getAttribute('data-cal-nav'));
        cal.view = new Date(cal.view.getFullYear(), cal.view.getMonth() + delta, 1);
        drawCal();
        return;
      }
      const pre = e.target.closest('[data-cal-preset]');
      if (pre) {
        const r = presetRange(pre.getAttribute('data-cal-preset'));
        cal.from = r.from; cal.to = r.to;
        applyPeriod();
        return;
      }
      const day = e.target.closest('[data-cal-day]');
      if (day) {
        const iso = day.getAttribute('data-cal-day');
        if (!cal.from || (cal.from && cal.to)) {
          cal.from = iso; cal.to = '';
          drawCal();
        } else {
          if (iso < cal.from) { cal.to = cal.from; cal.from = iso; }
          else { cal.to = iso; }
          applyPeriod();
        }
      }
    });
    document.addEventListener('click', (e) => {
      if (cal.open && periodWrap && !periodWrap.contains(e.target)) closeCal();
    });
  }

  // Перестраивает опции выпадающих списков менеджеров/статусов наверху
  // после загрузки данных (на момент построения панели списки могли быть пусты).
  function refreshRnpFilterOptions() {
    const mgrEl = document.getElementById('rnp-mgr-filter');
    const stEl = document.getElementById('rnp-status-filter');
    if (mgrEl) {
      mgrEl.innerHTML = ['<option value="">Все менеджеры</option>']
        .concat((rnpState.managers || []).map(m =>
          `<option value="${U.esc(m)}"${rnpState.mgrFilter === m ? ' selected' : ''}>${U.esc(m)}</option>`))
        .join('');
    }
    if (stEl) {
      // Весь предустановленный набор статусов (не только встречающиеся).
      stEl.innerHTML = statusFilterOptionsHtml();
    }
  }

  // Измеряет высоты двух липких элементов и записывает их в CSS-переменные,
  // чтобы элементы ниже по стеку прилипали ровно под предыдущими:
  //   --rnp-filters-h     — высота тулбара (.rnp-toolbar: вкладки + фильтры),
  //     под которым липнет шапка таблицы;
  //   --rnp-headsticky-h  — высота шапки таблицы (.rnp-head-sticky: заголовок + легенда недель),
  //     под которой фиксируется строка «Итоги» (lvl-0).
  // Пересчитывается при ресайзе (тулбар/легенда могут переноситься на несколько строк).
  function syncRnpFiltersHeight() {
    requestAnimationFrame(() => {
      const root = document.documentElement;
      const tb = document.querySelector('.rnp-toolbar');
      if (!tb) {
        root.style.setProperty('--rnp-filters-h', '0px');
        root.style.setProperty('--rnp-headsticky-h', '0px');
        return;
      }
      const h = Math.round(tb.getBoundingClientRect().height);
      root.style.setProperty('--rnp-filters-h', h + 'px');
      const hs = document.querySelector('.rnp-head-sticky');
      const hsH = hs ? Math.round(hs.getBoundingClientRect().height) : 0;
      root.style.setProperty('--rnp-headsticky-h', hsH + 'px');
    });
  }
  // При ресайзе окна тулбар может перенестись на другое число строк —
  // пересчитываем высоту (вешаем один раз на модуль).
  if (!window.__rnpFiltersResizeBound) {
    window.__rnpFiltersResizeBound = true;
    window.addEventListener('resize', () => syncRnpFiltersHeight());
  }

  // ------------------------------------------------------------------
  // Рендер иерархии: для каждого раскрытого узла — таблица метрики×недели
  // ------------------------------------------------------------------
  // Активны ли фильтры РНП (поиск по артикулу, выбор менеджера или статуса)
  function rnpFilterActive() {
    return !!(rnpState.searchArt && rnpState.searchArt.trim()) || !!rnpState.mgrFilter || !!rnpState.statusFilter;
  }

  // Проходит ли товар (лист, level 4) через фильтры
  function rnpLeafMatches(node) {
    const li = node.leaf_info || {};
    const q = (rnpState.searchArt || '').trim().toLowerCase();
    if (q) {
      const art = (li.seller_article || '').toLowerCase();
      if (art.indexOf(q) === -1) return false;
    }
    if (rnpState.mgrFilter) {
      if ((li.manager || '') !== rnpState.mgrFilter) return false;
    }
    if (rnpState.statusFilter) {
      // Сравниваем через нормализацию: пустой/NULL статус = '-' (без статуса),
      // так фильтр '-' отбирает товары без присвоенного статуса.
      if (stDisplay(li.status) !== rnpState.statusFilter) return false;
    }
    return true;
  }

  // ------------------------------------------------------------------
  // АВТОСКРЫТИЕ «спящих» товаров.
  // Правило: если у товара (лист, level 4) статус «-» (не присвоен) И за
  // ВЫБРАННЫЙ период (видимые недели) по нему нет НИКАКИХ движений —
  // товар автоматически скрывается из отчёта. Пустые группы-родители,
  // в которых после этого не осталось видимых товаров, тоже скрываются.
  // Движение = ненулевое значение хотя бы по одному из полей:
  // заказы (orders_qty), продажи (sales_qty), выручка (revenue), прибыль (profit).
  // ------------------------------------------------------------------
  const RNP_MOVE_KEYS = ['orders_qty', 'sales_qty', 'revenue', 'profit'];
  const RNP_MOVE_KEYS_CROSS = ['c_orders_qty', 'c_sales_qty', 'c_revenue', 'c_profit'];

  // Есть ли у листа движения за переданные (видимые) недели
  function rnpLeafHasMovement(node, vWeeks) {
    const cells = node.cells || {};
    const moveKeys = rnpState.mp === 'cross' ? RNP_MOVE_KEYS_CROSS : RNP_MOVE_KEYS;
    for (let i = 0; i < vWeeks.length; i++) {
      const cv = cells[vWeeks[i].key];
      if (!cv) continue;
      for (let j = 0; j < moveKeys.length; j++) {
        const v = cv[moveKeys[j]];
        if (v !== null && v !== undefined && !isNaN(v) && Number(v) !== 0) return true;
      }
    }
    return false;
  }

  // Подлежит ли лист автоскрытию: статус «-» (нет статуса) и нет движений в периоде
  function rnpLeafAutoHidden(node, vWeeks) {
    const li = node.leaf_info || {};
    if (stDisplay(li.status) !== '-') return false;
    return !rnpLeafHasMovement(node, vWeeks);
  }

  // Рекурсивно убирает «спящие» товары и схлопывает опустевшие группы.
  // Возвращает копию дерева без скрытых узлов (корень/Итоги не удаляем).
  function rnpAutoHideTree(node, vWeeks) {
    if (node.level === 4) {
      return rnpLeafAutoHidden(node, vWeeks) ? null : node;
    }
    const kids = (node.children || [])
      .map(c => rnpAutoHideTree(c, vWeeks))
      .filter(Boolean);
    if (node.level !== 0 && kids.length === 0) return null;
    return Object.assign({}, node, { children: kids });
  }

  // Рекурсивно строит отфильтрованную копию дерева:
  // товары (level 4) оставляем только подходящие под фильтры,
  // категории — только те, в которых остался хотя бы один товар.
  // Агрегированные ячейки узлов НЕ пересчитываем — показываем как есть
  // (фильтр сужает видимый список, а не пересчитывает экономику).
  function rnpFilterTree(node) {
    if (node.level === 4) {
      return rnpLeafMatches(node) ? node : null;
    }
    const kids = (node.children || [])
      .map(rnpFilterTree)
      .filter(Boolean);
    if (node.level !== 0 && kids.length === 0) return null;
    return Object.assign({}, node, { children: kids });
  }

  function renderRnpMatrix(tree, weeks, metrics) {
    const host = document.getElementById('rnp-matrix');
    if (!host) return;
    // Фильтр по периоду: оставляем только недели, пересекающиеся с выбранным
    // диапазоном. Рендерим по этому набору И перестраиваем легенду (шапку) недель,
    // чтобы шапка и строки показывали одни и те же колонки.
    const vWeeks = rnpVisibleWeeks(weeks);
    const legHost = document.querySelector('.rnp-card .rnp-wk-legend');
    if (legHost) legHost.innerHTML = vWeeks.map(w => `<span class="rnp-wk-leg">${weekColHead(w)}</span>`).join('');
    if (!vWeeks.length) {
      host.innerHTML = '<div class="empty" style="padding:24px 12px;">За выбранный период нет недель. Измените диапазон дат.</div>';
      syncRnpHScroll();
      return;
    }
    // Автоскрытие «спящих» товаров (статус «-» + нет движений в видимых неделях)
    // применяем ВСЕГДА, до пользовательских фильтров, по выбранному периоду.
    const baseTree = rnpAutoHideTree(tree, vWeeks) || Object.assign({}, tree, { children: [] });
    let viewTree = baseTree;
    const filtering = rnpFilterActive();
    if (filtering) {
      viewTree = rnpFilterTree(baseTree) || Object.assign({}, baseTree, { children: [] });
      // Авто-раскрытие при фильтре делаем ОДИН раз на каждое НОВОЕ сочетание
      // фильтров (поиск/менеджер/статус), а НЕ на каждый рендер. Иначе ручное
      // сворачивание (кнопка «Свернуть» и треугольник у группы) тут же затиралось
      // бы обратно в раскрытое при ближайшем перерендере, т.к. toggle/collapse
      // тоже идут через renderRnpMatrix. Сигнатура запоминает применённый набор
      // фильтров; пока он не изменился — уважаем rnpState.expanded.
      const sig = JSON.stringify([
        (rnpState.searchArt || '').trim(),
        rnpState.mgrFilter || '',
        rnpState.statusFilter || '',
      ]);
      if (rnpState.filterExpandSig !== sig) {
        rnpExpandAll(viewTree);
        rnpState.filterExpandSig = sig;
      }
    } else {
      // Фильтр снят — сбрасываем сигнатуру, чтобы следующее его включение
      // снова один раз авто-раскрыло дерево.
      rnpState.filterExpandSig = '';
    }
    if (filtering && (!viewTree.children || viewTree.children.length === 0)) {
      host.innerHTML = '<div class="empty" style="padding:24px 12px;">Ничего не найдено по заданным фильтрам.</div>';
      return;
    }
    host.innerHTML = nodeBlock(viewTree, vWeeks, metrics, true);
    bindRnpToggles(host, viewTree, vWeeks, metrics);
    syncRnpHScroll();
    // Синхронизируем снимок раскрытия текущего МП (без scroll — он пишется
    // при уходе). Так любое раскрытие/сворачивание сразу попадает в uiSnap[mp].
    if (rnpState.uiSnap) {
      const _snap = rnpState.uiSnap[rnpState.mp] || (rnpState.uiSnap[rnpState.mp] = {});
      _snap.expanded = rnpState.expanded;
      _snap.metricsOpen = rnpState.metricsOpen;
    }
  }

  // ------------------------------------------------------------------
  // ГОРИЗОНТАЛЬНЫЙ СКРОЛЛ НЕДЕЛЬ С ФИКСИРОВАННОЙ ЛЕВОЙ КОЛОНКОЙ.
  // Левая часть строки (имя + СТАТУС + кнопка метрик) фиксирована, а область
  // колонок недель вынесена в отдельные горизонтальные скроллеры
  // (.rnp-wk-scroll — по одному на строку-узел, на легенду и на таблицу метрик).
  // Их нельзя объединить в один overflow-x:auto контейнер: это сделало бы его
  // и вертикальным scroll-контейнером и сломало бы window-sticky строки «Итоги»
  // и шапки. Поэтому держим много независимых горизонтальных скроллеров и
  // синхронизируем их scrollLeft между собой одним общим значением.
  // ------------------------------------------------------------------
  function syncRnpHScroll() {
    const card = document.querySelector('.rnp-card');
    if (!card) return;
    // Все горизонтальные скроллеры: недели по строкам/легенде/метрикам + ЕДИНЫЙ
    // нижний бар (.rnp-hscroll-bar). У всех data-rnp-scroll — берём их вместе.
    const scrollers = () => Array.from(card.querySelectorAll('[data-rnp-scroll]'));
    // Применяем общую позицию ко всем скроллерам, кроме источника события.
    const apply = (left, src) => {
      rnpState.hScroll = left;
      scrollers().forEach(el => { if (el !== src && el.scrollLeft !== left) el.scrollLeft = left; });
    };
    // Ширина «фантома» нижнего бара = реальная ширина содержимого недель.
    // Так у единого нижнего бара появляется бегунок ровно на ту же длину прокрутки,
    // что и у скрытых построчных скроллеров.
    const syncPhantom = () => {
      const bar = card.querySelector('.rnp-hscroll-bar');
      const ph = bar && bar.querySelector('.rnp-hscroll-phantom');
      const ref = card.querySelector('.rnp-wk-scroll[data-rnp-scroll]');
      if (ph && ref) ph.style.width = ref.scrollWidth + 'px';
    };
    // Один раз навешиваем делегированный обработчик на карточку.
    if (!card.__rnpHScrollBound) {
      card.__rnpHScrollBound = true;
      let raf = 0, pendingSrc = null;
      card.addEventListener('scroll', (e) => {
        const src = e.target;
        // Реагируем и на построчные .rnp-wk-scroll, и на единый нижний бар.
        const isScroller = src.classList && (src.classList.contains('rnp-wk-scroll') || src.classList.contains('rnp-hscroll-bar'));
        if (!isScroller) return;
        pendingSrc = src;
        if (raf) return;
        raf = requestAnimationFrame(() => {
          raf = 0;
          apply(pendingSrc.scrollLeft, pendingSrc);
        });
      }, true);
      // Пересчитываем ширину фантома при ресайзе окна (меняется ширина области недель).
      window.addEventListener('resize', () => requestAnimationFrame(syncPhantom));
    }
    syncPhantom();
    // Восстанавливаем сохранённую позицию на свежесозданных скроллерах
    // (после перерендера дерева раскрытие/сворачивание не должно сбрасывать прокрутку).
    const left = rnpState.hScroll || 0;
    if (left) scrollers().forEach(el => { el.scrollLeft = left; });
  }

  // Помечает все узлы дерева как раскрытые (для режима фильтрации)
  function rnpExpandAll(node) {
    if (node.children && node.children.length) {
      rnpState.expanded[node.key] = true;
      node.children.forEach(rnpExpandAll);
    }
  }

  // Блок одного узла: заголовок-строка + (опционально) таблица метрик + дети
  function nodeBlock(node, weeks, metrics, isRoot, parent) {
    const hasChildren = node.children && node.children.length > 0;
    const isItem = node.level === 4;
    // раскрытие детей — свёрнуто по умолчанию (включая Итоги/корень)
    const childrenOpen = !!rnpState.expanded[node.key];
    // раскрытие таблицы метрик — независимо, свёрнуто по умолчанию (включая Итоги/корень)
    const metricsOpen = !!rnpState.metricsOpen[node.key];

    // строка-заголовок узла с РНП по неделям
    const indent = node.level * 14;
    // красная стрелка слева:
    // — у групп (есть дети) раскрывает уровни иерархии;
    // — у артикула (товар, level 4) раскрывает его таблицу показателей.
    let arrow;
    if (hasChildren) {
      arrow = `<button class="rnp-exp-btn" data-xkey="${U.esc(node.key)}" title="Раскрыть/свернуть уровни">${childrenOpen ? '▾' : '▸'}</button>`;
    } else if (isItem) {
      arrow = `<button class="rnp-exp-btn" data-xmkey="${U.esc(node.key)}" title="Показать/скрыть показатели">${metricsOpen ? '▾' : '▸'}</button>`;
    } else {
      arrow = `<span class="exp-spacer"></span>`;
    }
    // товар: основной текст = артикул, наименование в tooltip
    let nameHtml;
    if (isItem && node.leaf_info) {
      nameHtml = `<span class="rnp-nm rnp-art-main" title="${U.esc(node.name)}">${U.esc(node.leaf_info.seller_article)}</span>`;
    } else {
      nameHtml = `<span class="rnp-nm">${U.esc(node.name)}</span>`;
    }

    // Значения верхнего показателя по неделям в строке-заголовке:
    // метрика выбирается селектором (дефолт — РНП %), формат по её типу,
    // дельта к прошлой неделе. Светофор — ТОЛЬКО для процентных метрик.
    const tm = getTopMetricDef();
    const rnpCells = weeks.map((w, i) => {
      const v = (node.cells[w.key] || {})[tm.key];
      const prev = i > 0 ? (node.cells[weeks[i - 1].key] || {})[tm.key] : null;
      const d = calcDelta(v, prev, tm.kind);
      const dTxt = fmtDelta(d);
      // Светофор (красный при отрицательной марже, оранжевый ниже целевой)
      // применяем только к процентным показателям; для штучных/денежных — нет.
      const sig = tm.isPct ? marginSignalClass(v) : '';
      return `<span class="rnp-hd-wk">`
        + `<span class="rnp-hd-v${sig ? ' ' + sig : ''}">${rnpFmt(v, tm.kind)}</span>`
        + (dTxt ? `<span class="rnp-hd-d ${deltaDir(d)}">${dTxt}</span>` : `<span class="rnp-hd-d flat"></span>`)
        + `</span>`;
    }).join('');

    // маленькая иконка-график показа метрик в конце строки —
    // только у групп товаров (узлы с собственными агрегированными метриками),
    // у артикулов (товар, level 4) иконка не нужна
    const mBtn = isItem
      ? `<span class="rnp-mbtn-spacer"></span>`
      : `<button class="rnp-mbtn ${metricsOpen ? 'on' : ''}" data-mkey="${U.esc(node.key)}" title="Показать/скрыть показатели" aria-label="Показатели">`
      + `<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="13" x2="3" y2="8"/><line x1="8" y1="13" x2="8" y2="4"/><line x1="13" y1="13" x2="13" y2="9"/></svg>`
      + `</button>`;

    // Статус товара — показываем только у строк-товаров (листьев, level 4).
    // У категорий это пустой блок-распорка для выравнивания столбцов.
    // У строк-товаров (lvl-4) статус выбирается из предустановленных значений
    // прямо в отчёте: <select>. При изменении сохраняем в справочник (catalog_marketplace).
    // У групп — пустой блок-распорка для выравнивания столбцов.
    let statusHtml;
    if (rnpState.mp === 'cross') {
      // В сводной — ДВА read-only столбца: статус OZ и статус WB (бейджи, без select).
      // Менять статус по конкретному МП пользователь будет в его собственной вкладке.
      if (isItem && node.leaf_info) {
        statusHtml = `<div class="rnp-node-status rnp-node-status2 rnp-fixleft">${statusBadge(node.leaf_info.status_oz)}</div><div class="rnp-node-status rnp-node-status2b rnp-fixleft">${statusBadge(node.leaf_info.status_wb)}</div>`;
      } else {
        statusHtml = `<div class="rnp-node-status rnp-node-status2 rnp-fixleft"></div><div class="rnp-node-status rnp-node-status2b rnp-fixleft"></div>`;
      }
    } else if (isItem && node.leaf_info) {
      const sa = node.leaf_info.seller_article || '';
      statusHtml = `<div class="rnp-node-status rnp-fixleft">${statusSelectHtml(sa, node.leaf_info.status)}</div>`;
    } else {
      statusHtml = `<div class="rnp-node-status rnp-fixleft"></div>`;
    }

    // Отступ вложенности применяется ВНУТРИ столбца имени (фикс. ширина,
    // box-sizing:border-box) — так столбцы «Статус» и недели не сдвигаются.
    let h = `<div class="rnp-node lvl-${node.level} ${childrenOpen ? 'open' : ''}">
      <div class="rnp-node-head">
        <div class="rnp-node-name rnp-fixleft" data-key="${U.esc(node.key)}" style="padding-left:${indent + 3}px">${arrow}${nameHtml}</div>
        ${statusHtml}
        <span class="rnp-node-mbtn rnp-fixleft">${mBtn}</span>
        <div class="rnp-wk-scroll" data-rnp-scroll><div class="rnp-node-rnp">${rnpCells}</div></div>
      </div>`;

    // таблица метрики×недели — только если явно раскрыта.
    // Названия показателей выравниваем по тексту названия узла:
    // отступ = отступ узла + место под стрелку (indent + 27px).
    if (metricsOpen) {
      const mnamePad = indent + 27;
      h += `<div class="rnp-node-body" style="--rnp-mname-pad:${mnamePad}px"><div class="rnp-wk-scroll rnp-mtbl-scroll" data-rnp-scroll>${metricTable(node, weeks, metrics, parent)}</div></div>`;
    }
    // дети — если раскрыты
    if (childrenOpen && hasChildren) {
      h += `<div class="rnp-children">`;
      node.children.forEach(ch => { h += nodeBlock(ch, weeks, metrics, false, node); });
      h += `</div>`;
    }
    h += `</div>`;
    return h;
  }

  // ==================================================================
  // ГРУППОВЫЕ МЕТРИКИ (уровни 0–3)
  // Набор согласован с пользователем: базовые показатели + диагностика
  // (убыточные SKU, доля A-товаров, возвраты) + структура затрат в % от
  // выручки. Проценты считаем на бэкенде взвешенно (Σчисл/Σзнам), здесь —
  // только форматирование и дельты неделя-к-неделе.
  // ------------------------------------------------------------------

  // Рекурсивный обход листьев (level 4) узла за конкретную неделю.
  // Возвращает агрегаты для диагностических метрик группы:
  //   lossN   — число артикулов с прибылью < 0 (при ненулевой выручке);
  //   totalN  — число артикулов с ненулевой выручкой;
  //   aShareFrac — доля выручки A-товаров (ABC) в общей выручке группы.
  function leafStats(node, wkey) {
    let lossN = 0, totalN = 0, revA = 0, revAll = 0;
    const walk = (n) => {
      if (n.level === 4) {
        const c = n.cells ? n.cells[wkey] : null;
        const rev = c ? c.revenue : null;
        if (rev !== null && rev !== undefined && !isNaN(rev) && rev !== 0) {
          totalN += 1;
          revAll += rev;
          const pr = c.profit;
          if (pr !== null && pr !== undefined && !isNaN(pr) && pr < 0) lossN += 1;
          if (c.abc === 'A') revA += rev;
        }
        return;
      }
      if (n.children) n.children.forEach(walk);
    };
    walk(node);
    return {
      lossN, totalN,
      aShareFrac: revAll > 0 ? (revA / revAll) : null,
    };
  }

  // ячейка «Убыточных SKU»: основное значение «n / всего», под ним —
  // изменение числа убыточных к прошлой неделе. Рост убыточных = плохо (красный).
  function lossSkuCell(node, weeks, i) {
    const cur = leafStats(node, weeks[i].key);
    const valTxt = cur.totalN > 0 ? (cur.lossN + ' / ' + cur.totalN) : '—';
    const negCls = cur.lossN > 0 ? ' rnp-sig-neg' : '';
    let dHtml = '<span class="rnp-vc-d flat"></span>';
    if (i > 0) {
      const prev = leafStats(node, weeks[i - 1].key);
      const diff = cur.lossN - prev.lossN;
      if (diff !== 0) {
        // рост убыточных (diff > 0) — направление «вниз» (красный)
        const dir = diff > 0 ? 'down' : 'up';
        const sign = diff > 0 ? '+' : '−';
        dHtml = '<span class="rnp-vc-d ' + dir + '">' + sign + Math.abs(diff) + '</span>';
      }
    }
    return '<span class="rnp-vc">'
      + '<span class="rnp-vc-v' + negCls + '">' + valTxt + '</span>'
      + dHtml
      + '</span>';
  }

  // ячейка «Доля A-товаров %»: доля выручки A-сегмента + дельта в пунктах
  function aShareCell(node, weeks, i) {
    const cur = leafStats(node, weeks[i].key).aShareFrac;
    const prev = i > 0 ? leafStats(node, weeks[i - 1].key).aShareFrac : null;
    const d = calcDelta(cur, prev, 'pct');
    const valTxt = rp(cur);
    const dTxt = fmtDelta(d);
    return '<span class="rnp-vc">'
      + '<span class="rnp-vc-v">' + valTxt + '</span>'
      + (dTxt ? '<span class="rnp-vc-d ' + deltaDir(d) + '">' + dTxt + '</span>' : '<span class="rnp-vc-d flat"></span>')
      + '</span>';
  }

  function _shareFrac(a, b) {
    if (a === null || a === undefined || isNaN(a)) return null;
    if (!b) return null;
    return a / b;
  }

  // ячейка «Доля в общей выручке %»: выручка узла / выручка КОРНЯ (Итоги).
  // Считаем от общего оборота — так доли сопоставимы между группами на любом
  // уровне (виден реальный вес группы в выручке). У самого корня — «—».
  function shareOfParentCell(node, parent, weeks, i) {
    // корень дерева (узел «Итоги») — знаменатель для всех уровней
    const root = rnpState.data ? rnpState.data.tree : null;
    const isRoot = !parent;            // у корня родителя нет
    const cell = node.cells[weeks[i].key] || {};
    const rcell = root ? (root.cells[weeks[i].key] || {}) : null;
    const frac = (!isRoot && rcell && rcell.revenue) ? _shareFrac(cell.revenue, rcell.revenue) : null;
    let prevFrac = null;
    if (i > 0 && !isRoot && root) {
      const c0 = node.cells[weeks[i - 1].key] || {};
      const r0 = root.cells[weeks[i - 1].key] || {};
      prevFrac = r0.revenue ? _shareFrac(c0.revenue, r0.revenue) : null;
    }
    const d = calcDelta(frac, prevFrac, 'pct');
    const valTxt = isRoot ? '—' : rp(frac);
    const dTxt = isRoot ? '' : fmtDelta(d);
    return '<span class="rnp-vc">'
      + '<span class="rnp-vc-v">' + valTxt + '</span>'
      + (dTxt ? '<span class="rnp-vc-d ' + deltaDir(d) + '">' + dTxt + '</span>' : '<span class="rnp-vc-d flat"></span>')
      + '</span>';
  }

  // ячейка «Возвраты %» = 1 − выкуп %. Дельта в пунктах. Рост возвратов = плохо.
  function returnsCell(node, weeks, i) {
    const cell = node.cells[weeks[i].key] || {};
    const bo = cell.buyout_pct;
    const ret = (bo === null || bo === undefined || isNaN(bo)) ? null : (1 - bo);
    let prevRet = null;
    if (i > 0) {
      const pbo = (node.cells[weeks[i - 1].key] || {}).buyout_pct;
      prevRet = (pbo === null || pbo === undefined || isNaN(pbo)) ? null : (1 - pbo);
    }
    const d = calcDelta(ret, prevRet, 'pct');
    const valTxt = rp(ret);
    // рост возвратов — «вниз» (красный); снижение — «вверх» (зелёный)
    let dir = 'flat';
    if (d && d.val > 0) dir = 'down';
    else if (d && d.val < 0) dir = 'up';
    const dTxt = fmtDelta(d);
    return '<span class="rnp-vc">'
      + '<span class="rnp-vc-v">' + valTxt + '</span>'
      + (dTxt ? '<span class="rnp-vc-d ' + dir + '">' + dTxt + '</span>' : '<span class="rnp-vc-d flat"></span>')
      + '</span>';
  }

  // ячейка «Δ выручки н/н %»: крупным основным значением — относительное
  // изменение выручки к прошлой неделе (со знаком и подсветкой направления).
  // Первая неделя — «—».
  function deltaOnlyCell(node, weeks, i, key) {
    if (i === 0) {
      return '<span class="rnp-vc"><span class="rnp-vc-v rnp-vc-muted">—</span><span class="rnp-vc-d flat"></span></span>';
    }
    const cur = (node.cells[weeks[i].key] || {})[key];
    const prev = (node.cells[weeks[i - 1].key] || {})[key];
    const d = calcDelta(cur, prev, 'rub');
    const dir = deltaDir(d);
    const valTxt = d ? fmtDelta(d) : '—';
    return '<span class="rnp-vc">'
      + '<span class="rnp-vc-v ' + dir + '">' + valTxt + '</span>'
      + '<span class="rnp-vc-d flat"></span>'
      + '</span>';
  }

  // УНИКАЛЬНЫЕ метрики групп — тех, что НЕТ у товаров. Рендерятся ВНИЗУ
  // карточки группы — ПОСЛЕ товарного набора (последняя товарная — «Выкуп, %»).
  // Первая — с разделителем сверху (sep), отделяет от товарного блока.
  // type: 'cell' — значение+дельта; 'calc' — вычисляемая (см. switch в metricTable).
  const GROUP_ONLY_PLAN = [
    { label: 'Доля в общей выручке, %', type: 'calc', calc: 'share', sep: true },
    // Прочие удержания маркетплейса БЕЗ привязки к артикулу (общая реклама на
    // аккаунт, штрафы, прочие взаиморасчёты). Показывается ТОЛЬКО в «Итогах» (rootOnly).
    { label: 'Прочие удержания (общие), руб', type: 'cell', key: 'noart_exp_rub', kind: 'rub', color: '980000', rootOnly: true },
    { label: 'Возвраты, %',             type: 'calc', calc: 'returns' },
    { label: 'Убыточных SKU',           type: 'calc', calc: 'loss' },
    { label: 'Доля A-товаров, %',       type: 'calc', calc: 'ashare' },
  ];

  // Строка «Маржинальность, %» для карточки метрик. Добавляется ПЕРВОЙ,
  // когда верхний показатель НЕ маржа (чтобы маржа не пропадала из вида).
  // Жирная, нейтрального цвета; структура совпадает со строками карточки.
  // valDeltaCell читает cell['rnp'] — подходит и для товара, и для групп.
  function rnpMarginRow(node, weeks) {
    let row = '<tr class="rnp-m-strong"><td class="rnp-m-name rnp-m-strong" style="font-weight:700;">Маржинальность, %</td>';
    weeks.forEach((w, i) => {
      const cell = node.cells[w.key] || {};
      const prevCell = i > 0 ? (node.cells[weeks[i - 1].key] || {}) : null;
      row += `<td class="num rnp-wkcol">${valDeltaCell(cell, prevCell, 'rnp', 'pct_node', { bold: true })}</td>`;
    });
    row += '<td class="rnp-m-tail"></td></tr>';
    return row;
  }

  // Строки метрик ТОВАРА (единый набор product_metrics): цвет/жирность по
  // эталону, группировки «Продвижение» (от продаж ▸ от заказов) и «Все
  // удержания», полярность окраски дельт. Возвращает HTML строк <tr>.
  // Используется и для товаров/Сводной, и для ВЕРХА карточки групп —
  // групповые ячейки содержат те же p_*-ключи, поэтому вывод 1-в-1.
  function productMetricRows(node, weeks) {
    // p_fines_rub скрываем вне Wildberries (Ozon/Сводная) — см. rnpMetricVisible.
    const pmetrics = (rnpState.productMetrics || []).filter(mt => rnpMetricVisible(mt.key));
    // Индекс заголовка группы «Все удержания, %» и границы её детей.
    // Дети = ВСЕ метрики после p_holds_pct до конца списка; если после
    // встретится метрика с sep=true (начало нового блока) — это граница.
    const holdsIdx = pmetrics.findIndex(mt => mt.key === 'p_holds_pct');
    let holdsEnd = pmetrics.length; // конец диапазона детей (исключительно)
    if (holdsIdx !== -1) {
      for (let j = holdsIdx + 1; j < pmetrics.length; j++) {
        if (pmetrics[j].sep) { holdsEnd = j; break; }
      }
    }
    // Группа «Продвижение, от продаж» (голова p_promo_pct) с единственным
    // ребёнком «Продвижение, от заказов» (p_promo_orders_pct) — раскрывается
    // отдельно (rnpState.promoOpen).
    const promoIdx = pmetrics.findIndex(mt => mt.key === 'p_promo_pct');
    const promoChildIdx = pmetrics.findIndex(mt => mt.key === 'p_promo_orders_pct');
    let body = '';
    pmetrics.forEach((mt, idx) => {
      const isHoldsHead = idx === holdsIdx;
      const isHoldsChild = holdsIdx !== -1 && idx > holdsIdx && idx < holdsEnd;
      const isPromoHead = idx === promoIdx;
      const isPromoChild = idx === promoChildIdx;
      // Дети групп рендерятся только когда своя группа раскрыта.
      if (isHoldsChild && !rnpState.holdsOpen) return;
      if (isPromoChild && !rnpState.promoOpen) return;
      let nameSt = '';
      if (mt.color) nameSt += `color:#${mt.color};`;
      if (mt.bold) nameSt += 'font-weight:700;';
      const nameStyle = nameSt ? ` style="${nameSt}"` : '';
      const strong = mt.bold ? ' rnp-m-strong' : '';
      const sepCls = mt.sep ? ' rnp-m-sep' : '';
      const childCls = (isHoldsChild || isPromoChild) ? ' rnp-m-holds-child' : '';
      // Заголовок группы: слева треугольник (тот же символ/стиль,
      // что у стрелки раскрытия узла ▸/▾). Дети — с лёгким отступом.
      let nameInner;
      if (isHoldsHead) {
        const tri = rnpState.holdsOpen ? '▾' : '▸';
        nameInner = `<button class="rnp-exp-btn rnp-holds-toggle" title="Показать/скрыть удержания">${tri}</button>${U.esc(mt.label)}`;
      } else if (isPromoHead && promoChildIdx !== -1) {
        const tri = rnpState.promoOpen ? '▾' : '▸';
        nameInner = `<button class="rnp-exp-btn rnp-promo-toggle" title="Показать/скрыть продвижение от заказов">${tri}</button>${U.esc(mt.label)}`;
      } else {
        nameInner = U.esc(mt.label);
      }
      let headCls = '';
      if (isHoldsHead) headCls = ' rnp-m-holds-head is-clickable';
      else if (isPromoHead && promoChildIdx !== -1) headCls = ' rnp-m-promo-head is-clickable';
      let row = `<tr class="${(strong + sepCls + childCls).trim()}"><td class="rnp-m-name${strong}${childCls}${headCls}"${nameStyle}>${nameInner}</td>`;
      weeks.forEach((w, i) => {
        const cell = node.cells[w.key] || {};
        const prevCell = i > 0 ? (node.cells[weeks[i - 1].key] || {}) : null;
        row += `<td class="num rnp-wkcol">${valDeltaCellProduct(cell, prevCell, mt)}</td>`;
      });
      // распорка справа — забирает остаток ширины, чтобы подсветка строки
      // при наведении тянулась на всю ширину (как у строк-узлов РНП)
      row += '<td class="rnp-m-tail"></td>';
      row += '</tr>';
      body += row;
    });
    return body;
  }

  // Таблица: строки = метрики, столбцы = недели (каждая = значение + дельта)
  // Стиль повторяет отчёт «Недели API v2»: плотная таблица, трёхстрочный заголовок недели
  function metricTable(node, weeks, metrics, parent) {
    // заголовок «Показатель» не нужен ни у групп, ни у артикулов —
    // раскрываем узел и сразу идут метрики (подписи недель — в общей шапке сверху)

    // ТОВАР (лист, level 4): новый набор метрик с цветом/жирностью по эталону.
    // В режиме «Сводная» (cross) тот же единый набор product_metrics рендерим
    // на ЛЮБОМ уровне дерева (у cross-узлов одинаковые c_*-ключи на всех уровнях).
    if (node.level === 4 || rnpState.mp === 'cross') {
      let body = '';
      // Если наверху НЕ маржа — маржинальность показываем первой строкой карточки.
      if (rnpState.topMetric !== 'rnp') body += rnpMarginRow(node, weeks);
      body += productMetricRows(node, weeks);
      return `<table class="tbl rnp-mtbl">
        <tbody>${body}</tbody>
      </table>`;
    }

    // ГРУППЫ (level 0–3): ВЕРХ карточки — тот же набор метрик товара (порядок,
    // группировки «Продвижение»/«Все удержания», полярность окраски дельт), а
    // НИЖЕ — уникальные групповые метрики (нет у товаров), см. GROUP_ONLY_PLAN.
    let body = '';
    // Если наверху НЕ маржа — маржинальность показываем первой строкой карточки.
    if (rnpState.topMetric !== 'rnp') body += rnpMarginRow(node, weeks);
    body += productMetricRows(node, weeks);
    GROUP_ONLY_PLAN.forEach(m => {
      // строки только для корня «Итоги» (level 0) — пропускаем у групп
      if (m.rootOnly && node.level !== 0) return;
      // p_fines_rub — только Wildberries (в Ozon/Сводной скрыта).
      if (!rnpMetricVisible(m.key)) return;
      const strong = m.strong ? ' rnp-m-strong' : '';
      const sepCls = m.sep ? ' rnp-m-sep' : '';
      // Окраска НАЗВАНИЯ групповой метрики — по тем же цветам, что у одноимённых
      // метрик товара (Комиссия/Логистика — серый, Продвижение — фиолетовый,
      // Хранение — тёмно-красный). Уникальные групповые метрики цвета не имеют.
      let nameSt = '';
      if (m.color) nameSt += `color:#${m.color};`;
      if (m.bold)  nameSt += 'font-weight:700;';
      const nameStyle = nameSt ? ` style="${nameSt}"` : '';
      let row = `<tr class="${(strong + sepCls).trim()}"><td class="rnp-m-name${strong}"${nameStyle}>${U.esc(m.label)}</td>`;
      weeks.forEach((w, i) => {
        const cell = node.cells[w.key] || {};
        const prevCell = i > 0 ? (node.cells[weeks[i - 1].key] || {}) : null;
        let inner = '';
        if (m.type === 'cell') {
          inner = valDeltaCell(cell, prevCell, m.key, m.kind, m);
        } else if (m.type === 'deltaOnly') {
          inner = deltaOnlyCell(node, weeks, i, m.key);
        } else if (m.type === 'calc') {
          switch (m.calc) {
            case 'share':   inner = shareOfParentCell(node, parent, weeks, i); break;
            case 'loss':    inner = lossSkuCell(node, weeks, i); break;
            case 'ashare':  inner = aShareCell(node, weeks, i); break;
            case 'returns': inner = returnsCell(node, weeks, i); break;
            default:        inner = '<span class="rnp-vc"><span class="rnp-vc-v">—</span><span class="rnp-vc-d flat"></span></span>';
          }
        }
        row += `<td class="num rnp-wkcol">${inner}</td>`;
      });
      // распорка справа — забирает остаток ширины, чтобы подсветка строки
      // при наведении тянулась на всю ширину (как у строк-узлов РНП)
      row += '<td class="rnp-m-tail"></td>';
      row += '</tr>';
      body += row;
    });

    return `<table class="tbl rnp-mtbl">
      <tbody>${body}</tbody>
    </table>`;
  }

  // Навешиваем клики: стрелка — раскрытие детей; иконка — показ метрик
  function bindRnpToggles(host, tree, weeks, metrics) {
    // раскрытие детей (клик по красной стрелке или по имени узла)
    const toggleChildren = (key) => {
      rnpState.expanded[key] = !rnpState.expanded[key];
      renderRnpMatrix(rnpState.data.tree, weeks, metrics);
    };
    // переключение раскрытия таблицы метрик (по ключу узла)
    const toggleMetrics = (key) => {
      rnpState.metricsOpen[key] = !rnpState.metricsOpen[key];
      renderRnpMatrix(rnpState.data.tree, weeks, metrics);
    };
    host.querySelectorAll('.rnp-exp-btn').forEach(el => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        const xkey = el.getAttribute('data-xkey');
        const xmkey = el.getAttribute('data-xmkey');
        if (xkey != null) toggleChildren(xkey);          // группа: раскрыть уровни
        else if (xmkey != null) toggleMetrics(xmkey);    // артикул: раскрыть показатели
      });
    });
    // сворачиваемая группа «Все удержания, %»: общий флаг rnpState.holdsOpen
    const toggleHolds = (e) => {
      e.stopPropagation();
      rnpState.holdsOpen = !rnpState.holdsOpen;
      renderRnpMatrix(rnpState.data.tree, weeks, metrics);
    };
    host.querySelectorAll('.rnp-holds-toggle').forEach(el => {
      el.addEventListener('click', toggleHolds);
    });
    // клик по названию строки-заголовка тоже переключает группу
    host.querySelectorAll('.rnp-m-holds-head').forEach(el => {
      el.addEventListener('click', (e) => {
        if (e.target.closest('.rnp-holds-toggle')) return; // стрелка уже обработана
        toggleHolds(e);
      });
    });
    // сворачиваемая группа «Продвижение, от продаж»: флаг rnpState.promoOpen
    const togglePromo = (e) => {
      e.stopPropagation();
      rnpState.promoOpen = !rnpState.promoOpen;
      renderRnpMatrix(rnpState.data.tree, weeks, metrics);
    };
    host.querySelectorAll('.rnp-promo-toggle').forEach(el => {
      el.addEventListener('click', togglePromo);
    });
    host.querySelectorAll('.rnp-m-promo-head').forEach(el => {
      el.addEventListener('click', (e) => {
        if (e.target.closest('.rnp-promo-toggle')) return; // стрелка уже обработана
        togglePromo(e);
      });
    });
    // дополнительно: клик по имени узла повторяет действие его стрелки
    host.querySelectorAll('.rnp-node-name').forEach(el => {
      const node = el.closest('.rnp-node');
      const btn = el.querySelector('.rnp-exp-btn');
      if (!btn) return; // нет стрелки — не вешаем
      const xkey = btn.getAttribute('data-xkey');
      const xmkey = btn.getAttribute('data-xmkey');
      el.classList.add('is-clickable');
      el.addEventListener('click', (e) => {
        if (e.target.closest('.rnp-exp-btn')) return; // стрелка уже обработана
        if (xkey != null) toggleChildren(xkey);
        else if (xmkey != null) toggleMetrics(xmkey);
      });
    });
    // показ/скрытие таблицы метрик
    host.querySelectorAll('.rnp-mbtn').forEach(el => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        const key = el.getAttribute('data-mkey');
        rnpState.metricsOpen[key] = !rnpState.metricsOpen[key];
        renderRnpMatrix(rnpState.data.tree, weeks, metrics);
      });
    });
    // Смена статуса товара прямо в отчёте: <select> предустановленных значений.
    // Сохраняем в справочник (catalog_marketplace) для текущего маркетплейса и
    // обновляем leaf_info в дереве, чтобы значение не «откатилось» при ре-рендере.
    host.querySelectorAll('.rnp-status-sel').forEach(sel => {
      // клик по селектору не должен сворачивать/раскрывать строку-узел
      sel.addEventListener('click', (e) => e.stopPropagation());
      sel.addEventListener('change', async (e) => {
        e.stopPropagation();
        const sa = sel.getAttribute('data-sa');
        const newVal = sel.value;            // '-' = без статуса
        const prevVal = stDisplay(rnpFindLeafStatus(rnpState.data.tree, sa));
        if (!sa) return;
        // перекрашиваем селектор сразу (оптимистично)
        sel.className = 'rnp-status-sel ' + stClass(newVal);
        sel.disabled = true;
        try {
          await API.catalogUpdate(sa, {
            marketplace: rnpMpDbName(),
            status: stToApi(newVal),
          });
          // обновляем дерево в памяти, чтобы ре-рендер показал новое значение
          rnpSetLeafStatus(rnpState.data.tree, sa, stToApi(newVal));
          // справочник/категории могли поменяться — сбрасываем их кэш
          if (API.cacheClear) {
            API.cacheClear('/api/catalog');
            API.cacheClear('/api/rnp/tree');
            API.cacheClear('/api/abc');
          }
          // статус влияет на дашборд ABC (в т.ч. «Сравнение МП») — сбросить его памятный кэш
          if (window.ABCDash && window.ABCDash.invalidate) window.ABCDash.invalidate();
          App.toast('Статус обновлён: ' + sa + ' → ' + newVal, 'ok');
        } catch (err) {
          // откат значения и цвета при ошибке
          sel.value = prevVal;
          sel.className = 'rnp-status-sel ' + stClass(prevVal);
          App.toast('Ошибка сохранения статуса: ' + err.message, 'err');
        } finally {
          sel.disabled = false;
        }
      });
    });
  }

  // Дерево РНП — это ОДИН корневой узел («Итоги») с полем children.
  // Найти статус листа (товара) по артикулу.
  function rnpFindLeafStatus(tree, sa) {
    let found = null;
    const walk = (n) => {
      if (found !== null || !n) return;
      const li = n.leaf_info;
      if (li && li.seller_article === sa) { found = li.status; return; }
      if (n.children) n.children.forEach(walk);
    };
    walk(tree);
    return found;
  }
  // Записать новый статус листа (товара) по артикулу.
  function rnpSetLeafStatus(tree, sa, status) {
    const walk = (n) => {
      if (!n) return;
      const li = n.leaf_info;
      if (li && li.seller_article === sa) { li.status = status; }
      if (n.children) n.children.forEach(walk);
    };
    walk(tree);
  }


  // ============================================================
  // ABC — анализ товарной матрицы (ОЗОН × Wildberries)
  // Реализация вынесена в модуль window.ABCDash (static/js/abc_module.js):
  // загрузка /api/abc/data, расчёт ABC/рекомендаций и рендер 5 вкладок.
  // ============================================================
  function abc(root, ctl, state) {
    ctl.innerHTML = '';
    if (!window.ABCDash || typeof window.ABCDash.render !== 'function') {
      root.innerHTML = '<div class="empty">Модуль ABC не загружен (abc_module.js).</div>';
      return;
    }
    return window.ABCDash.render(root, state);
  }

  // ============================================================
  // РНП ПРОДАЖИ — дерево дневных продаж Ozon по месяцам.
  // Два подраздела: Wildberries (заглушка) и OZON (реальный отчёт).
  // Колонки = месяцы (свёрнуты до итога) → разворот в дни. В каждом месяце:
  // [дни…] Итог месяца | План | Прогноз. Метрики: группа/Итоги — 8, товар — 21
  // (+ вложенная подгруппа «Цены»). Светофор — только на «Заказы, шт».
  // ============================================================
  const rnpSalesState = {
    mp: 'ozon', expanded: {}, metricsOpen: {}, monthsOpen: {}, pricesOpen: false, piOpen: {}, uiSnap: {},
    // Активная вложенная вкладка внутри подтаба «Загрузка данных»: 'ozon' | 'wb' | 'common'.
    uploadTab: 'ozon',
    data: null, loading: false, error: '',
    // Монотонный счётчик запросов дерева — защита от ГОНКИ при быстром
    // переключении МП (Ozon→WB) до завершения загрузки. Каждый вызов
    // rnpsLoad()/rnpSales() фиксирует свой номер; поздний ответ, чей номер
    // устарел или МП сменился, НЕ пишет в state и НЕ трогает DOM.
    _reqSeq: 0,
    searchArt: '', mgrFilter: '', statusFilter: '', from: '', to: '',
    filterExpandSig: '',
    // Комментарии к дневным ячейкам: Map по ключу `sa|date` → массив комментов (новые сверху).
    comments: new Map(),
    // === Фильтр аномалий (отклонений от нормы) ===
    anomOn: false,                 // открыт ли режим аномалий (панель доступна)
    anomApplied: false,            // применён ли фильтр (только по кнопке «Применить»)
    anomBase: 'rolling7',          // период сравнения: week | rolling7 | custom
    anomCurFrom: '', anomCurTo: '',// для custom
    anomSelected: [],              // выбранные rule_key (мультивыбор)
    anomRulesDef: null,            // определения правил из БД [{rule_key,label,...}]
    anomHits: null,                // { art_upper: [rule_key,...] } — результат применённого фильтра
    anomCounts: null,              // { rule_key: N } — превью-счётчики по всем правилам
    anomPeriods: null,             // { cur:[..], prev:[..] }
    anomRelAvailable: false,       // доступен ли предыдущий период
    anomNote: '',                  // подсказка (нет базы и т.п.)
    anomLoading: false,
  };

  // Карта rule_key → короткая метка для бейджа (компактно).
  const ANOM_BADGE = {
    orders_qty: 'Заказы',  cancels_qty: 'Отмены', card_visits: 'Переходы',
    cr_cart_pct: 'CR корз.', cr_order_pct: 'CR заказ', avg_position: 'Позиция',
    spp_pct: 'СПП', drr_total_pct: 'ДРР', ctr_pct: 'CTR',
    rating: 'Рейтинг', reviews: 'Отзывы', price_index_pi: 'Pi',
    stock_ozon_cover: 'Ост.Ozon', stock_ap_cover: 'Наш склад',
    forecast_plan: 'План', no_ads: 'Без рекл.',
  };

  // Ключ ячейки комментариев.
  function rnpsCmtKey(sa, date) { return String(sa) + '|' + String(date); }
  // Массив комментариев для ячейки (может быть пустым).
  function rnpsCmtFor(sa, date) { return rnpSalesState.comments.get(rnpsCmtKey(sa, date)) || []; }

  // Форматирование значения ячейки по типу метрики.
  function rnpsFmt(v, kind) {
    if (v === null || v === undefined || (typeof v === 'number' && isNaN(v))) return '—';
    switch (kind) {
      case 'qty':   return U.fmtNum(v, 0);
      case 'rub':   return U.fmtNum(v, 0);        // полное число с разделителем, без ₽
      case 'price': return U.fmtNum(v, 0) + ' ₽';
      case 'pct':   return rp(v);                 // доля 0..1 → X,X%
      case 'pi':    return U.fmtNum(v, 2);        // Индекс цены Pi — всегда 2 знака
      case 'num':   return U.fmtNum(v, Math.abs(v) < 100 ? 1 : 0);
      default:      return U.fmtNum(v, 0);
    }
  }

  // Плоский список колонок по месяцам с учётом развёрнутости.
  // Тип: 'day' (день) | 'total' (итог месяца) | 'plan' | 'forecast'.
  function rnpsColumns(months) {
    const cols = [];
    (months || []).forEach(m => {
      if (rnpSalesState.monthsOpen[m.key]) {
        (m.days || []).forEach(d => cols.push({ type: 'day', key: d.key, mk: m.key, label: d.label }));
      }
      cols.push({ type: 'total', key: m.key, mk: m.key, label: 'Итог' });
      cols.push({ type: 'plan', key: m.key, mk: m.key, label: 'План' });
      cols.push({ type: 'forecast', key: m.key, mk: m.key, label: 'Прогноз' });
    });
    return cols;
  }

  // Значение «Заказы, шт» (верхняя метрика) в конкретной колонке узла.
  function rnpsTopValue(node, col) {
    if (col.type === 'plan') return (node.plan || {})[col.mk];
    if (col.type === 'forecast') return (node.forecast || {})[col.mk];
    return ((node.cells || {})[col.key] || {}).orders_qty;
  }

  // Светофор для «Заказы, шт»: 3-цветная шкала красный→жёлтый→зелёный по колонке.
  function rnpsHeatColor(v, mn, mx) {
    if (v === null || v === undefined || isNaN(v) || mx <= mn) return '';
    const t = Math.max(0, Math.min(1, (v - mn) / (mx - mn)));
    // red #f4b8b5 → cream #faeebf → green #b7dcb9 (мягкая пастель)
    const lerp = (a, b, k) => Math.round(a + (b - a) * k);
    let r, g, b2;
    if (t < 0.5) {
      const k = t / 0.5;
      r = lerp(0xf4, 0xfa, k); g = lerp(0xb8, 0xee, k); b2 = lerp(0xb5, 0xbf, k);
    } else {
      const k = (t - 0.5) / 0.5;
      r = lerp(0xfa, 0xb7, k); g = lerp(0xee, 0xdc, k); b2 = lerp(0xbf, 0xb9, k);
    }
    return `rgb(${r},${g},${b2})`;
  }

  // Мин/макс «Заказы, шт» ПО КАЖДОЙ СТРОКЕ (узлу) ОТДЕЛЬНО — только по
  // колонкам-ДНЯМ ИМЕННО этого узла (как условное форматирование по строке).
  // Итог месяца (сумма дней) в шкалу НЕ входит — иначе он всегда максимум.
  // План/Прогноз в шкалу тоже не входят.
  function rnpsNodeHeatRange(node, cols) {
    let mn = Infinity, mx = -Infinity;
    cols.forEach(c => {
      if (c.type !== 'day') return;
      const v = ((node.cells || {})[c.key] || {}).orders_qty;
      if (v === null || v === undefined || isNaN(v)) return;
      if (v < mn) mn = v;
      if (v > mx) mx = v;
    });
    return { mn, mx };
  }

  // Светофор прогноза выполнения плана (шт): ≥100% зелёный, 70–99% жёлтый, <70% красный.
  // v — ДОЛЯ (0..1), т.е. 1.0 = 100%. Совпадает с логикой раздела «Продажи, шт».
  function rnpsFcClass(v) {
    if (v === null || v === undefined || isNaN(v)) return '';
    if (v >= 1) return 'rnps-fc-good';
    if (v >= 0.7) return 'rnps-fc-warn';
    return 'rnps-fc-bad';
  }

  // Оборачиваемость (дни запаса) — бейдж слева от месячной суммы.
  // Цвет шрифта по светофору: красный — недостаточно (< red),
  // зелёный — достаточно, синий — излишек (> blue или ∞).
  // v: число дней | "inf" | 0 | null.
  function rnpsTurnBadge(node, col, isItem) {
    const data = rnpSalesState.data || {};
    const cfg = data.turnover_cfg || {};
    // Только: включено, уровень артикула, итог месяца, ПОСЛЕДНИЙ месяц.
    if (cfg.enabled === false) return '';
    if (!isItem || col.type !== 'total') return '';
    if (!data.last_month || col.mk !== data.last_month) return '';
    const tv = (node.turnover || {})[col.mk];
    if (tv === null || tv === undefined) return '';
    const red = (cfg.red != null) ? Number(cfg.red) : 20;
    const blue = (cfg.blue != null) ? Number(cfg.blue) : 60;
    let cls, text, title;
    if (tv === 'inf') {
      cls = 'rnps-turn-blue'; text = '∞';
      title = 'Оборачиваемость: есть остаток, нет заказов (излишек)';
    } else {
      const n = Number(tv);
      if (isNaN(n)) return '';
      const days = Math.round(n);
      text = String(days);
      if (n < red) cls = 'rnps-turn-red';
      else if (n > blue) cls = 'rnps-turn-blue';
      else cls = 'rnps-turn-green';
      title = `Оборачиваемость: ~${days} дн. запаса (остаток Ozon / среднедневные заказы)`;
    }
    return `<span class="rnps-turn ${cls}" title="${U.esc(title)}">${text}</span>`;
  }

  // Одна ячейка верхней метрики (Заказы, шт / План / Прогноз).
  // range — {mn, mx} по колонкам-ДНЯМ ИМЕННО этого узла.
  function rnpsTopCell(node, col, range, isItem) {
    const v = rnpsTopValue(node, col);
    let kind = 'qty';
    if (col.type === 'forecast') kind = 'pct';
    let style = '';
    // Светофором красим ТОЛЬКО дни у товаров (лист); группы/«Итоги» — без окраски.
    // Итог месяца всегда без светофора (нейтральная подложка из CSS).
    if (col.type === 'day' && isItem) {
      const bg = rnpsHeatColor(v, range.mn, range.mx);
      if (bg) style = ` style="background:${bg}"`;
    }
    const text = rnpsFmt(v, kind);
    let cls = col.type === 'total' ? ' rnps-total'
      : (col.type === 'plan' ? ' rnps-plan'
        : (col.type === 'forecast' ? ' rnps-forecast' : ''));
    // Прогноз выполнения плана (шт) — цвет цифр по светофору (на всех уровнях дерева).
    if (col.type === 'forecast') {
      const fc = rnpsFcClass(v);
      if (fc) cls += ' ' + fc;
    }
    // Комментарии к дневной ячейке товара (как в Google Sheets): по клику
    // открывается popover. Маркер-уголок слева-вверху — только если есть комментарии.
    let cmtAttrs = '';
    let cmtMark = '';
    if (col.type === 'day' && isItem && node.leaf_info) {
      const sa = node.leaf_info.seller_article || '';
      const date = col.key;
      cls += ' rnps-cell-cmt';
      cmtAttrs = ` data-sa="${U.esc(sa)}" data-date="${U.esc(date)}"`;
      const list = rnpsCmtFor(sa, date);
      if (list.length) {
        cls += ' has-cmt';
        cmtMark = `<span class="rnps-cmt-mark" title="Комментарии: ${list.length}"></span>`;
      }
    }
    // Оборачиваемость (дни запаса) — бейдж-пилюля в ЛЕВОЙ части ячейки итога,
    // сумма штук — справа; между ними разрыв (space-between). Только
    // артикул + итог последнего месяца.
    const turnBadge = rnpsTurnBadge(node, col, isItem);
    if (turnBadge) cls += ' rnps-has-turn';
    return `<span class="rnps-col${cls}"${style}${cmtAttrs}>${cmtMark}${turnBadge}${text}</span>`;
  }

  // Одна ячейка обычной метрики в таблице показателей.
  function rnpsMetricCell(node, col, mk, kind) {
    // План/Прогноз показываем только в верхней строке; в таблице метрик — пусто.
    // Классы rnps-plan/forecast/total нужны, чтобы подложка и разделитель блока
    // итогов шли по всей высоте таблицы, а не только в строке «Заказы, шт».
    if (col.type === 'plan' || col.type === 'forecast') {
      const cls = col.type === 'plan' ? 'rnps-plan' : 'rnps-forecast';
      return `<span class="rnps-col rnps-empty ${cls}">·</span>`;
    }
    const totalCls = col.type === 'total' ? ' rnps-total' : '';
    const v = ((node.cells || {})[col.key] || {})[mk];
    return `<span class="rnps-col${totalCls}">${rnpsFmt(v, kind)}</span>`;
  }

  // Таблица показателей узла: группа (8) или товар (21, с подгруппой «Цены»).
  function rnpsMetricTable(node, cols) {
    const data = rnpSalesState.data || {};
    const isItem = node.level === 4;
    const defs = isItem ? (data.product_metrics || []) : (data.group_metrics || []);
    let rows = '';
    defs.forEach(mt => {
      const sub = mt.sub || 0;
      // Вложенные метрики подгруппы «Цены» (sub===2) — только когда раскрыта.
      if (sub === 2 && !rnpSalesState.pricesOpen[node.key]) return;
      if (sub === 4 && !rnpSalesState.piOpen[node.key]) return;
      let nameSt = '';
      if (mt.color) nameSt += `color:#${mt.color};`;
      if (mt.bold) nameSt += 'font-weight:700;';
      const pad = (sub === 2 || sub === 4) ? 18 : 0;
      let nameInner;
      if (sub === 1) {
        const tri = rnpSalesState.pricesOpen[node.key] ? '▾' : '▸';
        nameInner = `<button class="rnp-exp-btn rnps-prices-toggle" data-pkey="${U.esc(node.key)}" title="Показать/скрыть цены">${tri}</button>${U.esc(mt.label)}`;
      } else if (sub === 3) {
        const open = !!rnpSalesState.piOpen[node.key];
        nameInner = `<button class="rnp-exp-btn rnps-pi-toggle" data-pkey="${U.esc(node.key)}" aria-expanded="${open}" aria-label="Показать/скрыть индексы цен" title="Показать/скрыть индексы цен">${open ? '▾' : '▸'}</button>${U.esc(mt.label)}`;
      } else {
        nameInner = U.esc(mt.label);
      }
      const headCls = sub === 1 ? ' rnps-prices-head is-clickable' : sub === 3 ? ' rnps-pi-head is-clickable' : '';
      let row = `<div class="rnps-m-row">`
        + `<span class="rnps-m-name rnp-fixleft${headCls}" title="${U.esc(mt.label)}" data-pkey="${(sub === 1 || sub === 3) ? U.esc(node.key) : ''}" style="${nameSt}padding-left:${pad}px">${nameInner}</span>`
        + `<span class="rnps-m-status rnp-fixleft"></span>`
        + `<span class="rnps-m-mbtn rnp-fixleft"></span>`
        + `<span class="rnp-wk-scroll" data-rnp-scroll><span class="rnps-m-vals" style="${nameSt}">`;
      cols.forEach(c => { row += rnpsMetricCell(node, c, mt.key, mt.kind); });
      row += `</span></span></div>`;
      rows += row;
    });
    return rows;
  }

  // Блок одного узла: строка-заголовок + (опц.) таблица метрик + дети.
  function rnpsNodeBlock(node, cols) {
    const hasChildren = node.children && node.children.length > 0;
    const isItem = node.level === 4;
    const childrenOpen = !!rnpSalesState.expanded[node.key];
    const metricsOpen = !!rnpSalesState.metricsOpen[node.key];
    const indent = node.level * 14;

    let arrow;
    if (hasChildren) {
      arrow = `<button class="rnp-exp-btn" data-xkey="${U.esc(node.key)}" title="Раскрыть/свернуть уровни">${childrenOpen ? '▾' : '▸'}</button>`;
    } else if (isItem) {
      arrow = `<button class="rnp-exp-btn" data-xmkey="${U.esc(node.key)}" title="Показать/скрыть показатели">${metricsOpen ? '▾' : '▸'}</button>`;
    } else {
      arrow = `<span class="exp-spacer"></span>`;
    }

    let nameHtml;
    let anomRowCls = '';
    if (isItem && node.leaf_info) {
      // При применённом фильтре — НЕ показываем бейджи у артикула (они перекрывали текст).
      // Вместо этого — мягкая подсветка строки + компактный индикатор числа отклонений.
      let mark = '';
      const keys = rnpsAnomFor(node.leaf_info.seller_article);
      if (keys.length) {
        anomRowCls = ' rnp-anom-hit';
        const titleFull = keys.map(k => rnpsAnomRuleLabel(k)).join(', ');
        mark = `<span class="rnp-anom-mark" title="Отклонения: ${U.esc(titleFull)}">${keys.length}</span>`;
      }
      // Клик по артикулу → открыть ссылку на товар из справочника (новая вкладка),
      // либо, если ссылка не задана, подсказка. Треугольник рядом раскрывает метрики.
      const purl = node.leaf_info.product_url || '';
      const artTitle = purl ? 'Открыть карточку товара' : 'Добавьте ссылку на товар в справочнике';
      nameHtml = `<span class="rnp-nm rnp-art-main rnp-art-link" data-arturl="${U.esc(purl)}" title="${U.esc(artTitle)}">${U.esc(node.leaf_info.seller_article)}</span>${mark}`;
    } else {
      nameHtml = `<span class="rnp-nm">${U.esc(node.name)}</span>`;
    }

    // Статус — только у товара (select с сохранением в справочник Ozon).
    let statusHtml;
    if (isItem && node.leaf_info) {
      statusHtml = `<div class="rnp-node-status rnp-fixleft">${statusSelectHtml(node.leaf_info.seller_article || '', node.leaf_info.status)}</div>`;
    } else {
      statusHtml = `<div class="rnp-node-status rnp-fixleft"></div>`;
    }

    // У ГРУПП — кнопка-столбики (раскрыть метрики). У ТОВАРОВ — иконка-стопка
    // монет: клик открывает popup юнит-экономики артикула за 3 последние недели.
    // Монохромный SVG в стиле остальных иконок, без знака валюты.
    // ue_neg — флаг с бэкенда: у товара ОТРИЦАТЕЛЬНАЯ маржинальность (%)
    // по последней подгруженной неделе → монетки красим в красный (--bad).
    const ueNeg = !!(node.leaf_info || {}).ue_neg;
    const mBtn = isItem
      ? `<button class="rnp-uebtn${ueNeg ? ' rnp-uebtn--neg' : ''}" data-ue-sa="${U.esc((node.leaf_info || {}).seller_article || '')}" title="Юнит-экономика артикула за 3 недели">`
        + `<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="8" cy="4" rx="5" ry="2"/><path d="M3 4v3c0 1.1 2.24 2 5 2s5-.9 5-2V4"/><path d="M3 7.5v3c0 1.1 2.24 2 5 2s5-.9 5-2v-3"/></svg>`
        + `</button>`
      : `<button class="rnp-mbtn ${metricsOpen ? 'on' : ''}" data-mkey="${U.esc(node.key)}" title="Показать/скрыть показатели">`
        + `<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="13" x2="3" y2="8"/><line x1="8" y1="13" x2="8" y2="4"/><line x1="13" y1="13" x2="13" y2="9"/></svg>`
        + `</button>`;

    // Светофор считается по мин/макс «Заказы, шт» ИМЕННО этой строки.
    const heatRange = rnpsNodeHeatRange(node, cols);
    let topCells = '';
    cols.forEach(c => { topCells += rnpsTopCell(node, c, heatRange, isItem); });

    let h = `<div class="rnp-node lvl-${node.level} ${childrenOpen ? 'open' : ''}">
      <div class="rnp-node-head${anomRowCls}">
        <div class="rnp-node-name rnp-fixleft" data-key="${U.esc(node.key)}" style="padding-left:${indent + 3}px">${arrow}${nameHtml}</div>
        ${statusHtml}
        <span class="rnp-node-mbtn rnp-fixleft">${mBtn}</span>
        <div class="rnp-wk-scroll" data-rnp-scroll><div class="rnps-node-vals">${topCells}</div></div>
      </div>`;

    if (metricsOpen) {
      h += `<div class="rnp-node-body">${rnpsMetricTable(node, cols)}</div>`;
    }
    if (childrenOpen && hasChildren) {
      h += `<div class="rnp-children">`;
      node.children.forEach(ch => { h += rnpsNodeBlock(ch, cols); });
      h += `</div>`;
    }
    h += `</div>`;
    return h;
  }

  // Шапка колонок: месяцы (с кнопкой разворота дней) + Итог/План/Прогноз.
  function rnpsLegend(months) {
    let html = '';
    (months || []).forEach(m => {
      const open = !!rnpSalesState.monthsOpen[m.key];
      if (open) {
        (m.days || []).forEach(d => {
          html += `<span class="rnps-leg rnps-leg-day">${U.esc(d.label)}</span>`;
        });
      }
      const tri = open ? '▾' : '▸';
      html += `<span class="rnps-leg rnps-leg-total">`
        + `<button class="rnp-exp-btn rnps-month-toggle" data-mkey="${U.esc(m.key)}" title="Показать/скрыть дни">${tri}</button>`
        + `<span class="rnps-leg-mlabel">${U.esc(m.label)}</span></span>`;
      html += `<span class="rnps-leg rnps-leg-plan">План</span>`;
      html += `<span class="rnps-leg rnps-leg-forecast">Прогноз</span>`;
    });
    return html;
  }

  // Активен ли пользовательский фильтр (поиск/менеджер/статус/аномалии).
  function rnpsFilterActive() {
    return !!(rnpSalesState.searchArt && rnpSalesState.searchArt.trim())
      || !!rnpSalesState.mgrFilter || !!rnpSalesState.statusFilter
      || (rnpSalesState.anomApplied && !!rnpSalesState.anomHits);
  }

  // Сработавшие правила аномалий для артикула (только когда фильтр применён).
  function rnpsAnomFor(sa) {
    if (!rnpSalesState.anomApplied || !rnpSalesState.anomHits) return [];
    return rnpSalesState.anomHits[String(sa || '').toUpperCase()] || [];
  }

  // Полная метка правила из БД (для title бейджа/чекбокса).
  function rnpsAnomRuleLabel(k) {
    const def = (rnpSalesState.anomRulesDef || []).find(r => r.rule_key === k);
    return (def && def.label) || ANOM_BADGE[k] || k;
  }

  // Короткая подпись порога правила для показа в списке (чтобы не открывать шестерёнку).
  function rnpsAnomThreshText(r) {
    if (!r) return '';
    const t = (r.threshold != null) ? U.fmtNum(r.threshold, (Math.abs(r.threshold) < 10 && r.threshold % 1 !== 0) ? 1 : 0) : '';
    const t2 = (r.threshold2 != null) ? U.fmtNum(r.threshold2, 0) : '';
    switch (r.kind) {
      case 'rel': {
        // Относительное изменение в % между периодами.
        if (r.direction === 'both' || r.direction === 'dev') return `±${t}%`;
        if (r.direction === 'up')   return `>+${t}%`;
        if (r.direction === 'down') return `−${t}%`;
        return `${t}%`;
      }
      case 'abs':
        if (r.direction === 'lt') return `< ${t}`;
        if (r.direction === 'gt') return `> ${t}`;
        return t;
      case 'cover':
        return `< ${t} дн`;
      case 'forecast':
        return `< ${t}% или > ${t2}%`;
      case 'filter':
        return '';
      default:
        return t;
    }
  }

  // Проходит ли лист (товар) текущий фильтр.
  function rnpsLeafPass(li) {
    if (!li) return false;
    const q = (rnpSalesState.searchArt || '').trim().toLowerCase();
    if (q && !String(li.seller_article || '').toLowerCase().includes(q)) return false;
    if (rnpSalesState.mgrFilter && (li.manager || '') !== rnpSalesState.mgrFilter) return false;
    if (rnpSalesState.statusFilter && stDisplay(li.status) !== rnpSalesState.statusFilter) return false;
    // Фильтр аномалий: товар проходит, если сработало ХОТЯ БЫ ОДНО из выбранных правил (только после Применить).
    if (rnpSalesState.anomApplied && rnpSalesState.anomHits) {
      if (rnpsAnomFor(li.seller_article).length === 0) return false;
    }
    return true;
  }

  // Загрузка аномалий с бэкенда.
  // forApply=false — ПРЕВЬЮ: грузим счётчики по ВСЕМ правилам (без фильтрации дерева).
  // forApply=true  — ПРИМЕНЕНИЕ: грузим hits по ВЫБРАННЫМ правилам (фильтр дерева).
  async function rnpsLoadAnomalies(forApply) {
    rnpSalesState.anomLoading = true;
    try {
      // Определения правил (один раз или после правки порогов).
      if (!rnpSalesState.anomRulesDef) {
        const rr = await API.anomalyRules();
        rnpSalesState.anomRulesDef = (rr && rr.rules) || [];
      }
      const p = { marketplace: 'ozon', base: rnpSalesState.anomBase };
      if (rnpSalesState.anomBase === 'custom') {
        p.cur_from = rnpSalesState.anomCurFrom;
        p.cur_to = rnpSalesState.anomCurTo;
      }
      // Фильтрация hits по выбранным правилам — только при применении.
      if (forApply && rnpSalesState.anomSelected && rnpSalesState.anomSelected.length) {
        p.rules = rnpSalesState.anomSelected.join(',');
      }
      if (API.cacheClear) API.cacheClear('/api/rnp_sales/anomalies');
      const d = await API.anomalies(p);
      // Счётчики и мета-данные периода обновляем всегда (для панели).
      rnpSalesState.anomCounts = d.counts || {};
      rnpSalesState.anomPeriods = d.periods || null;
      rnpSalesState.anomRelAvailable = !!d.rel_available;
      rnpSalesState.anomNote = d.note || '';
      // hits применяем к дереву только при forApply.
      if (forApply) {
        rnpSalesState.anomHits = d.hits || {};
        rnpSalesState.anomApplied = true;
      }
    } catch (e) {
      if (forApply) rnpSalesState.anomHits = {};
      rnpSalesState.anomNote = 'Ошибка загрузки аномалий: ' + (e && e.message || e);
    } finally {
      rnpSalesState.anomLoading = false;
    }
  }

  // Группы правил для панели выбора (порядок и заголовки).
  const ANOM_GROUPS = [
    { kind: 'rel',      title: 'Динамика (сравнение периодов)' },
    { kind: 'abs',      title: 'Абсолютные пороги' },
    { kind: 'cover',    title: 'Покрытие остатками' },
    { kind: 'turnover', title: 'Оборачиваемость (дни запаса)' },
    { kind: 'forecast', title: 'Прогноз плана' },
    { kind: 'filter',   title: 'Срезы' },
  ];
  // Виды правил, которые НЕ участвуют в фильтре аномалий (только окраска/пороги).
  const ANOM_NONFILTER_KINDS = new Set(['turnover']);

  // Счётчик найденных SKU (только когда фильтр применён).
  function rnpsAnomTotal() {
    if (!rnpSalesState.anomApplied || !rnpSalesState.anomHits) return 0;
    return Object.keys(rnpSalesState.anomHits).length;
  }

  // Дата YYYY-MM-DD → ДД.ММ (коротко).
  function _anomDM(s) {
    if (!s) return '';
    const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})$/);
    return m ? `${m[3]}.${m[2]}` : String(s);
  }

  // Короткая понятная подпись периодов сравнения.
  // Формат: «11.07–13.07 сравнивается с 08.07–10.07».
  function rnpsAnomPeriodInfo() {
    const p = rnpSalesState.anomPeriods;
    if (!p || !p.cur) return '';
    const cur = `${_anomDM(p.cur[0])}–${_anomDM(p.cur[1])}`;
    if (rnpSalesState.anomRelAvailable && p.prev) {
      return `${cur} ↔ предыд. ${_anomDM(p.prev[0])}–${_anomDM(p.prev[1])}`;
    }
    return cur;
  }

  // Рендер переключателя «Аномалии» + выпадающая панель (мультивыбор правил + период + пороги).
  function rnpsRenderAnomHost() {
    const host = document.getElementById('rnps-anom-host');
    if (!host) return;
    const on = rnpSalesState.anomOn;
    const applied = rnpSalesState.anomApplied;
    const total = applied ? rnpsAnomTotal() : 0;
    // Счётчик на кнопке — только когда фильтр применён.
    const cnt = applied ? `<span class="rnp-anom-count">${total}</span>` : '';
    const loading = rnpSalesState.anomLoading ? ' is-loading' : '';
    const cls = (on ? ' on' : '') + (applied ? ' applied' : '');
    host.innerHTML = `
      <button class="btn-sm rnp-anom-toggle${cls}${loading}" id="rnps-anom-btn" type="button" title="Показать товары с отклонениями от нормы">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        Аномалии${cnt}
      </button>
      <div class="rnp-anom-panel" id="rnps-anom-panel" hidden></div>`;
    rnpsBindAnomHost();
  }

  // Содержимое выпадающей панели (перестраивается при открытии).
  function rnpsAnomPanelHtml() {
    const defs = rnpSalesState.anomRulesDef || [];
    const sel = new Set(rnpSalesState.anomSelected || []);
    const relOff = !rnpSalesState.anomRelAvailable;
    // Период сравнения.
    const baseOpt = (v, lbl) => `<option value="${v}"${rnpSalesState.anomBase === v ? ' selected' : ''}>${lbl}</option>`;
    let periodRow = `
      <div class="rnp-anom-row">
        <label class="rnp-anom-lbl">База сравнения</label>
        <select class="input-sm" id="rnps-anom-base">
          ${baseOpt('week', 'Неделя (ISO)')}
          ${baseOpt('rolling7', 'Скользящие 7 дней')}
          ${baseOpt('custom', 'Произвольный')}
        </select>
      </div>`;
    if (rnpSalesState.anomBase === 'custom') {
      periodRow += `
        <div class="rnp-anom-row">
          <input type="date" class="input-sm" id="rnps-anom-from" value="${U.esc(rnpSalesState.anomCurFrom || '')}">
          <span class="rnp-anom-dash">—</span>
          <input type="date" class="input-sm" id="rnps-anom-to" value="${U.esc(rnpSalesState.anomCurTo || '')}">
        </div>`;
    }
    const info = rnpsAnomPeriodInfo();
    const infoRow = info ? `<div class="rnp-anom-info">${U.esc(info)}</div>` : '';
    const noteRow = rnpSalesState.anomNote ? `<div class="rnp-anom-note">${U.esc(rnpSalesState.anomNote)}</div>` : '';

    // Группы правил с чекбоксами.
    let groupsHtml = '';
    ANOM_GROUPS.forEach(g => {
      // Оборачиваемость — не фильтр аномалий, а окраска метрики; в список чекбоксов не попадает.
      if (ANOM_NONFILTER_KINDS.has(g.kind)) return;
      const items = defs.filter(r => r.kind === g.kind && r.enabled);
      if (!items.length) return;
      const relBlocked = (g.kind === 'rel' && relOff);
      let rows = '';
      items.forEach(r => {
        const cnt = (rnpSalesState.anomCounts && rnpSalesState.anomCounts[r.rule_key] != null)
          ? `<span class="rnp-anom-rc">${rnpSalesState.anomCounts[r.rule_key]}</span>` : '';
        const checked = sel.has(r.rule_key) ? ' checked' : '';
        const th = rnpsAnomThreshText(r);
        const thHtml = th ? `<span class="rnp-anom-th">${U.esc(th)}</span>` : '';
        // Убираем хвосты типа «<», «>», «(< / >)», «< дней» — порог теперь показан отдельно.
        let lbl = String(r.label || r.rule_key)
          .replace(/\s*[:：]?\s*покрытие\s*<\s*дней\s*$/i, ': покрытие')
          .replace(/\s*\(<\s*\/\s*>\)\s*$/i, '')
          .replace(/\s*[<>]\s*$/,'')
          .trim();
        rows += `
          <label class="rnp-anom-item${relBlocked ? ' is-disabled' : ''}">
            <input type="checkbox" class="rnp-anom-cb" value="${U.esc(r.rule_key)}"${checked}${relBlocked ? ' disabled' : ''}>
            <span class="rnp-anom-item-lbl">${U.esc(lbl)}</span>${thHtml}${cnt}
          </label>`;
      });
      const warn = relBlocked ? ' <span class="rnp-anom-gwarn">— нет предыдущего периода</span>' : '';
      groupsHtml += `<div class="rnp-anom-group"><div class="rnp-anom-gtitle">${U.esc(g.title)}${warn}</div>${rows}</div>`;
    });

    return `
      <div class="rnp-anom-phead">
        <span>Правила отклонений</span>
        <button class="rnp-anom-gear" id="rnps-anom-gear" type="button" title="Настройка порогов">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        </button>
      </div>
      ${periodRow}
      ${infoRow}
      ${noteRow}
      <div class="rnp-anom-groups">${groupsHtml || '<div class="rnp-anom-empty">Нет включённых правил</div>'}</div>
      <div class="rnp-anom-actions">
        <button class="btn-sm" id="rnps-anom-all" type="button">Все</button>
        <button class="btn-sm" id="rnps-anom-none" type="button">Сбросить</button>
        <span class="rnp-anom-actions-sp"></span>
        <button class="btn-sm primary" id="rnps-anom-apply" type="button">Применить</button>
      </div>`;
  }

  // Применить аномалии: загрузить hits по выбранным правилам, применить фильтр, СВЕРНУТЬ панель.
  async function rnpsAnomApply() {
    // Нужно выбрать хотя бы одно правило.
    if (!rnpSalesState.anomSelected || !rnpSalesState.anomSelected.length) {
      if (App && App.toast) App.toast('Выберите хотя бы одно правило отклонения', 'err');
      return;
    }
    rnpSalesState.filterExpandSig = '';
    rnpsRenderAnomHost();       // показать состояние «загрузка»
    await rnpsLoadAnomalies(true);   // forApply=true → hits + anomApplied=true
    closePanel();               // сворачиваем панель после применения
    rnpsRenderAnomHost();       // обновить счётчик на кнопке
    rnpsRenderMatrix();
  }

  // Открыть/закрыть панель — вынесено наружу для повторного вызова.
  let _anomPanelDocHandler = null;
  function openPanel() {
    const panel = document.getElementById('rnps-anom-panel');
    if (!panel) return;
    panel.innerHTML = rnpsAnomPanelHtml();
    panel.hidden = false;
    rnpsBindAnomPanel();
    // Закрытие по клику вне панели.
    if (_anomPanelDocHandler) document.removeEventListener('click', _anomPanelDocHandler, true);
    _anomPanelDocHandler = (e) => {
      const host = document.getElementById('rnps-anom-host');
      if (host && !host.contains(e.target)) closePanel();
    };
    setTimeout(() => document.addEventListener('click', _anomPanelDocHandler, true), 0);
  }
  function closePanel() {
    const panel = document.getElementById('rnps-anom-panel');
    if (panel) panel.hidden = true;
    if (_anomPanelDocHandler) { document.removeEventListener('click', _anomPanelDocHandler, true); _anomPanelDocHandler = null; }
  }

  // Привязка кнопки-переключателя и открытия панели.
  function rnpsBindAnomHost() {
    const btn = document.getElementById('rnps-anom-btn');
    if (!btn) return;
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!rnpSalesState.anomOn) {
        // ВКЛЮЧАЕМ режим: только открываем панель и грузим превью-счётчики.
        // Фильтр НЕ применяется — только после выбора правил и кнопки «Применить».
        rnpSalesState.anomOn = true;
        rnpsRenderAnomHost();
        await rnpsLoadAnomalies(false);   // превью: счётчики по всем правилам
        rnpsRenderAnomHost();
        openPanel();
      } else {
        // ВЫКЛЮЧАЕМ режим полностью: снимаем фильтр и закрываем панель.
        rnpSalesState.anomOn = false;
        rnpSalesState.anomApplied = false;
        closePanel();
        rnpsRenderAnomHost();
        rnpsRenderMatrix();
      }
    });
  }

  // Привязка элементов внутри панели.
  function rnpsBindAnomPanel() {
    const panel = document.getElementById('rnps-anom-panel');
    if (!panel) return;
    panel.addEventListener('click', (e) => e.stopPropagation());
    // База сравнения.
    const baseEl = panel.querySelector('#rnps-anom-base');
    if (baseEl) baseEl.addEventListener('change', async () => {
      rnpSalesState.anomBase = baseEl.value;
      // Для готовых баз (не custom) сразу обновляем счётчики превью.
      if (rnpSalesState.anomBase !== 'custom') { await rnpsLoadAnomalies(false); }
      openPanel();  // перерисовать (показать/скрыть даты, обновить счётчики)
    });
    const fromEl = panel.querySelector('#rnps-anom-from');
    if (fromEl) fromEl.addEventListener('change', async () => {
      rnpSalesState.anomCurFrom = fromEl.value;
      if (rnpSalesState.anomCurFrom && rnpSalesState.anomCurTo) { await rnpsLoadAnomalies(false); openPanel(); }
    });
    const toEl = panel.querySelector('#rnps-anom-to');
    if (toEl) toEl.addEventListener('change', async () => {
      rnpSalesState.anomCurTo = toEl.value;
      if (rnpSalesState.anomCurFrom && rnpSalesState.anomCurTo) { await rnpsLoadAnomalies(false); openPanel(); }
    });
    // Чекбоксы правил.
    panel.querySelectorAll('.rnp-anom-cb').forEach(cb => {
      cb.addEventListener('change', () => {
        const set = new Set(rnpSalesState.anomSelected || []);
        if (cb.checked) set.add(cb.value); else set.delete(cb.value);
        rnpSalesState.anomSelected = Array.from(set);
      });
    });
    // Все / Сбросить.
    const allEl = panel.querySelector('#rnps-anom-all');
    if (allEl) allEl.addEventListener('click', () => {
      const defs = (rnpSalesState.anomRulesDef || []).filter(r => r.enabled);
      const relOff = !rnpSalesState.anomRelAvailable;
      rnpSalesState.anomSelected = defs
        .filter(r => !(r.kind === 'rel' && relOff))
        .map(r => r.rule_key);
      openPanel();
    });
    const noneEl = panel.querySelector('#rnps-anom-none');
    if (noneEl) noneEl.addEventListener('click', () => { rnpSalesState.anomSelected = []; openPanel(); });
    // Применить.
    const applyEl = panel.querySelector('#rnps-anom-apply');
    if (applyEl) applyEl.addEventListener('click', () => { rnpsAnomApply(); });
    // Шестерёнка — настройка порогов.
    const gearEl = panel.querySelector('#rnps-anom-gear');
    if (gearEl) gearEl.addEventListener('click', (e) => { e.stopPropagation(); rnpsOpenThresholds(); });
  }

  // Формат порога для поля ввода (число как есть).
  function _thNum(v) { return (v === null || v === undefined) ? '' : String(v); }

  // Подсказка по единице порога в зависимости от вида правила.
  function _thHint(r) {
    if (r.kind === 'rel') return '%';
    if (r.kind === 'cover') return 'дн.';
    if (r.kind === 'turnover') return 'дн. запаса';
    if (r.kind === 'forecast') return '% от плана';
    if (r.rule_key === 'rating') return 'баллы';
    if (r.rule_key === 'reviews') return 'шт.';
    if (r.rule_key === 'price_index_pi') return 'Pi';
    return '';
  }

  // Popup юнит-экономики товара (клик по иконке-монеткам в РНП заказы).
  // Лёгкий запрос /api/rnp/product_ue — шесть ключевых метрик артикула
  // за 3 последние недели с подсветкой динамики (как в юнит-экономике).
  async function rnpsOpenUePopup(sa) {
    const mp = rnpSalesState.mp || 'ozon';
    let ov = document.getElementById('rnps-ue-overlay');
    if (ov) ov.remove();
    ov = document.createElement('div');
    ov.id = 'rnps-ue-overlay';
    ov.className = 'rnp-ue-overlay';
    ov.innerHTML = `
      <div class="rnp-ue-modal">
        <div class="rnp-ue-head">
          <span class="rnp-ue-title">Юнит-экономика · <b>${U.esc(sa)}</b></span>
          <button class="rnp-th-x" id="rnps-ue-x" type="button" title="Закрыть">✕</button>
        </div>
        <div class="rnp-ue-body"><div class="empty" style="padding:26px 12px;">Загрузка…</div></div>
      </div>`;
    document.body.appendChild(ov);
    const close = () => { ov.remove(); document.removeEventListener('keydown', onEsc); };
    const onEsc = (e) => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onEsc);
    ov.addEventListener('click', (e) => { if (e.target === ov) close(); });
    ov.querySelector('#rnps-ue-x').addEventListener('click', close);

    let res;
    try {
      res = await API.rnpProductUe({ marketplace: mp, article: sa, weeks: 3 });
    } catch (err) {
      const body = ov.querySelector('.rnp-ue-body');
      if (body) body.innerHTML = `<div class="empty" style="padding:26px 12px; color:#b00;">Не удалось загрузить данные.</div>`;
      return;
    }
    const body = ov.querySelector('.rnp-ue-body');
    if (!body) return;
    const weeks = res.weeks || [];
    const metrics = res.metrics || [];
    const cells = res.cells || {};
    if (!weeks.length) {
      body.innerHTML = `<div class="empty" style="padding:26px 12px;">Нет недельных данных по артикулу.</div>`;
      return;
    }
    // Шапка: метрика + по неделям.
    let head = `<th class="rnp-ue-mcol">Метрика</th>`;
    weeks.forEach(w => {
      const per = w.period_text ? `<span class="rnp-ue-wper">${U.esc(rnpsWeekShort(w.period_text))}</span>` : '';
      head += `<th class="rnp-ue-wcol"><span class="rnp-ue-wlbl">${U.esc(w.label)}</span>${per}</th>`;
    });
    // Строки метрик.
    let rows = '';
    const UE_ROW_CLS = { profit:'rnp-ue-r-profit', p_cogs_pct:'rnp-ue-r-cogs', p_promo_pct:'rnp-ue-r-promo', p_holds_pct:'rnp-ue-r-holds' };
    metrics.forEach(m => {
      const rcls = UE_ROW_CLS[m.key] || '';
      let tds = `<td class="rnp-ue-mname">${U.esc(m.label)}</td>`;
      weeks.forEach((w, i) => {
        const cur = cells[w.key] ? cells[w.key][m.key] : null;
        const valTxt = rnpFmtProduct(cur, m.kind, m.key);
        let dHtml = '<span class="rnp-ue-d flat"></span>';
        if (i > 0) {
          const prev = cells[weeks[i - 1].key] ? cells[weeks[i - 1].key][m.key] : null;
          const d = calcDelta(cur, prev, m.kind);
          const dTxt = fmtDelta(d);
          if (dTxt) dHtml = `<span class="rnp-ue-d ${deltaDirPolar(d, m.polarity)}">${dTxt}</span>`;
        }
        tds += `<td class="rnp-ue-cell"><span class="rnp-ue-v">${valTxt}</span>${dHtml}</td>`;
      });
      rows += `<tr class="${rcls}">${tds}</tr>`;
    });
    body.innerHTML = `
      <table class="rnp-ue-tbl"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>
      <div class="rnp-ue-foot">Динамика — к предыдущей неделе. Цифры совпадают с разделом «Юнит-экономика».</div>`;
  }

  // Компактный период недели: '01.06.2026 - 07.06.2026' -> '01–07.06'.
  function rnpsWeekShort(pt) {
    if (!pt) return '';
    const m = String(pt).match(/(\d{2})\.(\d{2})\.\d{4}\s*[-–]\s*(\d{2})\.(\d{2})\.\d{4}/);
    if (!m) return pt;
    const [, d1, mo1, d2, mo2] = m;
    return (mo1 === mo2) ? `${d1}–${d2}.${mo1}` : `${d1}.${mo1}–${d2}.${mo2}`;
  }

  // Модальное окно настройки порогов (шестерёнка). Пороги общие для команды (БД).
  function rnpsOpenThresholds() {
    const defs = (rnpSalesState.anomRulesDef || []).filter(r => r.enabled);
    // Перекрытие.
    let ov = document.getElementById('rnps-th-overlay');
    if (ov) ov.remove();
    ov = document.createElement('div');
    ov.id = 'rnps-th-overlay';
    ov.className = 'rnp-th-overlay';

    let rows = '';
    ANOM_GROUPS.forEach(g => {
      const items = defs.filter(r => r.kind === g.kind);
      if (!items.length) return;
      rows += `<div class="rnp-th-gtitle">${U.esc(g.title)}</div>`;
      items.forEach(r => {
        const hint = _thHint(r);
        // forecast и turnover — два порога.
        let inputs;
        if (r.kind === 'forecast' || r.kind === 'turnover') {
          // Подписи под вид правила: forecast — невып./перевып.; turnover — недост./излишек.
          const s1 = (r.kind === 'turnover') ? 'недост. &lt;' : 'невып. &lt;';
          const s2 = (r.kind === 'turnover') ? 'излишек &gt;' : 'перевып. &gt;';
          inputs = `
            <div class="rnp-th-inp2">
              <span class="rnp-th-sub">${s1}</span>
              <input type="number" step="any" class="input-sm rnp-th-inp" data-rk="${U.esc(r.rule_key)}" data-f="threshold" value="${_thNum(r.threshold)}">
              <span class="rnp-th-sub">${s2}</span>
              <input type="number" step="any" class="input-sm rnp-th-inp" data-rk="${U.esc(r.rule_key)}" data-f="threshold2" value="${_thNum(r.threshold2)}">
              <span class="rnp-th-hint">${U.esc(hint)}</span>
            </div>`;
        } else {
          inputs = `
            <div class="rnp-th-inp2">
              <input type="number" step="any" class="input-sm rnp-th-inp" data-rk="${U.esc(r.rule_key)}" data-f="threshold" value="${_thNum(r.threshold)}">
              <span class="rnp-th-hint">${U.esc(hint)}</span>
            </div>`;
        }
        rows += `
          <div class="rnp-th-row">
            <label class="rnp-th-enable" title="Включить/выключить правило">
              <input type="checkbox" class="rnp-th-en" data-rk="${U.esc(r.rule_key)}"${r.enabled ? ' checked' : ''}>
            </label>
            <span class="rnp-th-lbl">${U.esc(r.label || r.rule_key)}</span>
            ${inputs}
          </div>`;
      });
    });

    ov.innerHTML = `
      <div class="rnp-th-modal">
        <div class="rnp-th-head">
          <span>Настройка порогов аномалий</span>
          <button class="rnp-th-x" id="rnps-th-x" type="button" title="Закрыть">✕</button>
        </div>
        <div class="rnp-th-note">Пороги общие для всей команды. Относительные — в %, покрытие — в днях.</div>
        <div class="rnp-th-body">${rows}</div>
        <div class="rnp-th-foot">
          <button class="btn-sm" id="rnps-th-cancel" type="button">Отмена</button>
          <button class="btn-sm primary" id="rnps-th-save" type="button">Сохранить</button>
        </div>
      </div>`;
    document.body.appendChild(ov);

    const close = () => { ov.remove(); };
    ov.addEventListener('click', (e) => { if (e.target === ov) close(); });
    ov.querySelector('#rnps-th-x').addEventListener('click', close);
    ov.querySelector('#rnps-th-cancel').addEventListener('click', close);
    ov.querySelector('#rnps-th-save').addEventListener('click', async () => {
      // Собрать изменённые значения по rule_key.
      const byKey = {};
      ov.querySelectorAll('.rnp-th-inp').forEach(inp => {
        const rk = inp.getAttribute('data-rk'); const f = inp.getAttribute('data-f');
        if (!byKey[rk]) byKey[rk] = { rule_key: rk };
        const v = inp.value.trim();
        byKey[rk][f] = (v === '') ? null : Number(v);
      });
      ov.querySelectorAll('.rnp-th-en').forEach(cb => {
        const rk = cb.getAttribute('data-rk');
        if (!byKey[rk]) byKey[rk] = { rule_key: rk };
        byKey[rk].enabled = cb.checked;
      });
      const rules = Object.values(byKey);
      const saveBtn = ov.querySelector('#rnps-th-save');
      saveBtn.disabled = true; saveBtn.textContent = 'Сохранение…';
      try {
        await API.anomalyRulesUpdate(rules);
        if (API.cacheClear) API.cacheClear('/api/rnp_sales/anomaly_rules');
        rnpSalesState.anomRulesDef = null;  // перечитать при след. загрузке
        App.toast('Пороги сохранены', 'ok');
        close();
        // Оборачиваемость: пороги влияют на окраску бейджа — обновим локально и перерисуем.
        const trn = byKey['turnover_days'];
        if (trn && rnpSalesState.data && rnpSalesState.data.turnover_cfg) {
          const cfg = rnpSalesState.data.turnover_cfg;
          if (trn.threshold != null) cfg.red = Number(trn.threshold);
          if (trn.threshold2 != null) cfg.blue = Number(trn.threshold2);
          if (trn.enabled != null) cfg.enabled = !!trn.enabled;
          rnpsRenderMatrix();
        }
        // Пересчёт после изменения порогов.
        if (rnpSalesState.anomApplied) {
          // Фильтр уже применён — пересчитать и перерисовать дерево.
          await rnpsAnomApply();
        } else if (rnpSalesState.anomOn) {
          // Режим открыт (превью) — обновить счётчики в панели.
          await rnpsLoadAnomalies(false);
          if (document.getElementById('rnps-anom-panel') && !document.getElementById('rnps-anom-panel').hidden) openPanel();
        }
      } catch (err) {
        App.toast('Ошибка сохранения: ' + (err && err.message || err), 'err');
        saveBtn.disabled = false; saveBtn.textContent = 'Сохранить';
      }
    });
  }

  // Отфильтрованная копия дерева: группа остаётся, если есть подходящий лист.
  function rnpsFilterTree(node) {
    if (node.level === 4) {
      return rnpsLeafPass(node.leaf_info) ? node : null;
    }
    const kids = (node.children || []).map(rnpsFilterTree).filter(Boolean);
    if (!kids.length && node.level !== 0) return null;
    return Object.assign({}, node, { children: kids });
  }

  function rnpsExpandAll(node) {
    if (node.children && node.children.length) {
      rnpSalesState.expanded[node.key] = true;
      node.children.forEach(rnpsExpandAll);
    }
  }

  // Рендер матрицы (шапка + строки) в уже собранную карточку.
  function rnpsRenderMatrix() {
    const data = rnpSalesState.data;
    const host = document.getElementById('rnps-matrix');
    const legHost = document.querySelector('.rnp-card .rnps-legend');
    if (!host || !data) return;
    const months = data.months || [];
    const cols = rnpsColumns(months);
    if (legHost) legHost.innerHTML = rnpsLegend(months);
    if (!months.length) {
      host.innerHTML = '<div class="empty" style="padding:24px 12px;">За выбранный период нет данных.</div>';
      syncRnpHScroll(); syncRnpFiltersHeight();
      return;
    }
    let viewTree = data.tree;
    if (rnpsFilterActive()) {
      viewTree = rnpsFilterTree(data.tree) || Object.assign({}, data.tree, { children: [] });
      const sig = JSON.stringify([(rnpSalesState.searchArt || '').trim(), rnpSalesState.mgrFilter || '', rnpSalesState.statusFilter || '']);
      if (rnpSalesState.filterExpandSig !== sig) {
        rnpsExpandAll(viewTree);
        rnpSalesState.filterExpandSig = sig;
      }
      if (!viewTree.children || !viewTree.children.length) {
        host.innerHTML = '<div class="empty" style="padding:24px 12px;">Ничего не найдено по заданным фильтрам.</div>';
        syncRnpHScroll(); syncRnpFiltersHeight();
        return;
      }
    } else {
      rnpSalesState.filterExpandSig = '';
    }
    host.innerHTML = rnpsNodeBlock(viewTree, cols);
    rnpsBindToggles(host);
    syncRnpHScroll(); syncRnpFiltersHeight();
    // Синхронизируем снимок раскрытия текущего МП (без scroll — он пишется
    // при уходе). Любое раскрытие/сворачивание товаров/метрик/месяцев
    // сразу попадает в uiSnap[mp].
    if (rnpSalesState.uiSnap) {
      const _snap = rnpSalesState.uiSnap[rnpSalesState.mp] || (rnpSalesState.uiSnap[rnpSalesState.mp] = {});
      _snap.expanded = rnpSalesState.expanded;
      _snap.metricsOpen = rnpSalesState.metricsOpen;
      _snap.monthsOpen = rnpSalesState.monthsOpen;
      _snap.pricesOpenBool = rnpSalesState.pricesOpen;
      _snap.piOpen = rnpSalesState.piOpen;
    }
  }

  function rnpsBindToggles(host) {
    const rerender = () => rnpsRenderMatrix();
    // Разворот месяцев в шапке.
    const legHost = document.querySelector('.rnp-card .rnps-legend');
    if (legHost) {
      legHost.querySelectorAll('.rnps-month-toggle').forEach(el => {
        el.addEventListener('click', (e) => {
          e.stopPropagation();
          const mk = el.getAttribute('data-mkey');
          rnpSalesState.monthsOpen[mk] = !rnpSalesState.monthsOpen[mk];
          rerender();
        });
      });
    }
    // Разворот уровней / показателей.
    host.querySelectorAll('.rnp-exp-btn').forEach(el => {
      if (el.classList.contains('rnps-prices-toggle')) return;
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        const xkey = el.getAttribute('data-xkey');
        const xmkey = el.getAttribute('data-xmkey');
        if (xkey != null) { rnpSalesState.expanded[xkey] = !rnpSalesState.expanded[xkey]; rerender(); }
        else if (xmkey != null) { rnpSalesState.metricsOpen[xmkey] = !rnpSalesState.metricsOpen[xmkey]; rerender(); }
      });
    });
    // Клик по имени узла = клик по стрелке.
    host.querySelectorAll('.rnp-node-name').forEach(el => {
      const btn = el.querySelector('.rnp-exp-btn');
      if (!btn) return;
      const xkey = btn.getAttribute('data-xkey');
      const xmkey = btn.getAttribute('data-xmkey');
      el.classList.add('is-clickable');
      el.addEventListener('click', (e) => {
        if (e.target.closest('.rnp-exp-btn')) return;
        // Клик по самому артикулу обрабатывается отдельно (ссылка/подсказка), не раскрываем метрики.
        if (e.target.closest('.rnp-art-link')) return;
        if (xkey != null) { rnpSalesState.expanded[xkey] = !rnpSalesState.expanded[xkey]; rerender(); }
        else if (xmkey != null) { rnpSalesState.metricsOpen[xmkey] = !rnpSalesState.metricsOpen[xmkey]; rerender(); }
      });
    });
    // Клик по артикулу товара → открыть ссылку из справочника в новой вкладке,
    // либо подсказка, если ссылка не задана. Треугольник рядом раскрывает метрики (не трогаем).
    host.querySelectorAll('.rnp-art-link').forEach(el => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        const url = (el.getAttribute('data-arturl') || '').trim();
        if (url) {
          window.open(url, '_blank', 'noopener');
        } else {
          App.toast('Добавьте ссылку на товар в справочнике', 'err');
        }
      });
    });
    // Иконка-график: показ таблицы метрик у групп.
    host.querySelectorAll('.rnp-mbtn').forEach(el => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        const key = el.getAttribute('data-mkey');
        rnpSalesState.metricsOpen[key] = !rnpSalesState.metricsOpen[key];
        rerender();
      });
    });
    // Иконка-монетки у ТОВАРА → popup юнит-экономики артикула за 3 недели.
    host.querySelectorAll('.rnp-uebtn').forEach(el => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        const sa = el.getAttribute('data-ue-sa');
        if (sa) rnpsOpenUePopup(sa);
      });
    });
    // Подгруппа «Цены» (per-node).
    host.querySelectorAll('.rnps-prices-toggle, .rnps-prices-head').forEach(el => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        const key = el.getAttribute('data-pkey');
        if (!key) return;
        rnpSalesState.pricesOpen[key] = !rnpSalesState.pricesOpen[key];
        rerender();
      });
    });
    host.querySelectorAll('.rnps-pi-toggle, .rnps-pi-head').forEach(el => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        const key = el.getAttribute('data-pkey');
        if (!key) return;
        rnpSalesState.piOpen[key] = !rnpSalesState.piOpen[key];
        rerender();
      });
    });
    // Клик по дневной ячейке товара → popover комментариев (как в Google Sheets).
    host.querySelectorAll('.rnps-cell-cmt').forEach(cell => {
      cell.addEventListener('click', (e) => {
        e.stopPropagation();
        const sa = cell.getAttribute('data-sa');
        const date = cell.getAttribute('data-date');
        if (!sa || !date) return;
        rnpsOpenCmtPopover(cell, sa, date);
      });
    });
    // Смена статуса товара → сохранение в справочник. Для Yandex — свой
    // справочник ('Yandex'). Для Ozon/WB поведение НЕ меняем
    // (исторически шло 'Ozon' — оставляем как есть).
    const rnpsMpName = (rnpSalesState.mp === 'yandex') ? 'Yandex' : 'Ozon';
    host.querySelectorAll('.rnp-status-sel').forEach(sel => {
      sel.addEventListener('click', (e) => e.stopPropagation());
      sel.addEventListener('change', async (e) => {
        e.stopPropagation();
        const sa = sel.getAttribute('data-sa');
        const newVal = sel.value;
        if (!sa) return;
        sel.className = 'rnp-status-sel ' + stClass(newVal);
        sel.disabled = true;
        try {
          await API.catalogUpdate(sa, { marketplace: rnpsMpName, status: stToApi(newVal) });
          rnpsSetLeafStatus(rnpSalesState.data.tree, sa, stToApi(newVal));
          if (API.cacheClear) { API.cacheClear('/api/catalog'); API.cacheClear('/api/rnp_sales/tree'); API.cacheClear('/api/abc'); }
          // статус влияет на дашборд ABC (в т.ч. «Сравнение МП») — сбросить его памятный кэш
          if (window.ABCDash && window.ABCDash.invalidate) window.ABCDash.invalidate();
          App.toast('Статус обновлён: ' + sa + ' → ' + newVal, 'ok');
        } catch (err) {
          App.toast('Ошибка сохранения статуса: ' + err.message, 'err');
        } finally {
          sel.disabled = false;
        }
      });
    });
  }

  // ==================================================================
  // КОММЕНТАРИИ К ЯЧЕЙКАМ (popover, как в Google Sheets)
  // ------------------------------------------------------------------
  // Открыт ли popover и для какой ячейки.
  const rnpsCmtPop = { el: null, sa: null, date: null, anchor: null, editingId: null, onDoc: null, onScroll: null };

  // Формат даты-времени создания/правки: «дд.мм.гггг, чч:мм» (локальное время).
  function rnpsFmtDT(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    const p = (n) => String(n).padStart(2, '0');
    return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()}, ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  // Может ли текущий пользователь редактировать/удалять комментарий (UI-уровень).
  // Сервер — источник истины (автор по author_id или admin), здесь — только видимость кнопок.
  function rnpsCanModifyCmt(c) {
    const u = API.getUser && API.getUser();
    if (!u) return false;
    if (u.role === 'admin') return true;
    return u.id != null && c.author_id != null && String(u.id) === String(c.author_id);
  }

  // Отрисовка содержимого popover (список + форма добавления).
  function rnpsCmtPopHtml(sa, date) {
    const list = rnpsCmtFor(sa, date);
    let items = '';
    if (!list.length) {
      items = `<div class="rnps-cmt-empty">Комментариев пока нет.</div>`;
    } else {
      list.forEach(c => {
        const canMod = rnpsCanModifyCmt(c);
        const edited = c.updated_at && c.created_at && c.updated_at !== c.created_at;
        if (rnpsCmtPop.editingId === c.id) {
          items += `<div class="rnps-cmt-item" data-cid="${c.id}">
            <div class="rnps-cmt-editbox">
              <textarea class="rnps-cmt-edit-ta" maxlength="2000">${U.esc(c.body)}</textarea>
              <div class="rnps-cmt-editrow">
                <button class="btn-sm rnps-cmt-save" data-cid="${c.id}">Сохранить</button>
                <button class="btn-sm rnps-cmt-canceledit">Отмена</button>
              </div>
            </div>
          </div>`;
        } else {
          items += `<div class="rnps-cmt-item" data-cid="${c.id}">
            <div class="rnps-cmt-meta">
              <span class="rnps-cmt-author">${U.esc(c.author_name || '—')}</span>
              <span class="rnps-cmt-date">${U.esc(rnpsFmtDT(c.created_at))}${edited ? ' · изм.' : ''}</span>
            </div>
            <div class="rnps-cmt-body">${U.esc(c.body)}</div>
            ${canMod ? `<div class="rnps-cmt-actions">
              <button class="rnps-cmt-edit" data-cid="${c.id}">Изменить</button>
              <button class="rnps-cmt-del" data-cid="${c.id}">Удалить</button>
            </div>` : ''}
          </div>`;
        }
      });
    }
    return `
      <div class="rnps-cmt-head">
        <span class="rnps-cmt-title">${U.esc(sa)} · ${U.esc(fmtRu(date))}</span>
        <button class="rnps-cmt-close" title="Закрыть">×</button>
      </div>
      <div class="rnps-cmt-list">${items}</div>
      <div class="rnps-cmt-add">
        <textarea class="rnps-cmt-new-ta" placeholder="Добавить комментарий…" maxlength="2000"></textarea>
        <button class="btn-sm rnps-cmt-addbtn">Добавить</button>
      </div>`;
  }

  // Позиционирование popover относительно ячейки (с учётом краёв экрана).
  function rnpsPositionCmtPop() {
    const pop = rnpsCmtPop.el, anchor = rnpsCmtPop.anchor;
    if (!pop || !anchor || !anchor.isConnected) { rnpsCloseCmtPopover(); return; }
    const r = anchor.getBoundingClientRect();
    const pw = pop.offsetWidth, ph = pop.offsetHeight;
    let left = r.left;
    let top = r.bottom + 6;
    if (left + pw > window.innerWidth - 8) left = window.innerWidth - pw - 8;
    if (left < 8) left = 8;
    if (top + ph > window.innerHeight - 8) top = r.top - ph - 6; // открыть вверх
    if (top < 8) top = 8;
    pop.style.left = left + 'px';
    pop.style.top = top + 'px';
  }

  // Закрыть popover и снять слушатели.
  function rnpsCloseCmtPopover() {
    if (rnpsCmtPop.onDoc) { document.removeEventListener('mousedown', rnpsCmtPop.onDoc, true); rnpsCmtPop.onDoc = null; }
    if (rnpsCmtPop.onScroll) { window.removeEventListener('scroll', rnpsCmtPop.onScroll, true); window.removeEventListener('resize', rnpsCmtPop.onScroll, true); rnpsCmtPop.onScroll = null; }
    if (rnpsCmtPop.onEsc) { document.removeEventListener('keydown', rnpsCmtPop.onEsc); rnpsCmtPop.onEsc = null; }
    if (rnpsCmtPop.el && rnpsCmtPop.el.parentNode) rnpsCmtPop.el.parentNode.removeChild(rnpsCmtPop.el);
    rnpsCmtPop.el = null; rnpsCmtPop.sa = null; rnpsCmtPop.date = null; rnpsCmtPop.anchor = null; rnpsCmtPop.editingId = null;
  }

  // Перерисовать только внутренности popover (после CRUD) без потери позиции.
  function rnpsRefreshCmtPop() {
    if (!rnpsCmtPop.el) return;
    rnpsCmtPop.el.innerHTML = rnpsCmtPopHtml(rnpsCmtPop.sa, rnpsCmtPop.date);
    rnpsBindCmtPop();
    rnpsPositionCmtPop();
  }

  // Обновить маркер-уголок на конкретной ячейке без перерисовки всей матрицы.
  function rnpsUpdateCellMark(sa, date) {
    const host = document.getElementById('rnps-matrix');
    if (!host) return;
    const cell = host.querySelector(`.rnps-cell-cmt[data-sa="${(window.CSS && CSS.escape) ? CSS.escape(sa) : sa}"][data-date="${date}"]`);
    if (!cell) return;
    const has = rnpsCmtFor(sa, date).length > 0;
    const mark = cell.querySelector('.rnps-cmt-mark');
    if (has && !mark) {
      const m = document.createElement('span');
      m.className = 'rnps-cmt-mark';
      cell.insertBefore(m, cell.firstChild);
      cell.classList.add('has-cmt');
    } else if (!has && mark) {
      mark.remove();
      cell.classList.remove('has-cmt');
    }
  }

  // Навесить обработчики внутри popover.
  function rnpsBindCmtPop() {
    const pop = rnpsCmtPop.el;
    if (!pop) return;
    const sa = rnpsCmtPop.sa, date = rnpsCmtPop.date;
    // Клики внутри popover не закрывают его (обрабатывает onDoc).
    const close = pop.querySelector('.rnps-cmt-close');
    if (close) close.addEventListener('click', () => rnpsCloseCmtPopover());

    // Добавить.
    const addBtn = pop.querySelector('.rnps-cmt-addbtn');
    const newTa = pop.querySelector('.rnps-cmt-new-ta');
    if (addBtn && newTa) {
      addBtn.addEventListener('click', async () => {
        const body = (newTa.value || '').trim();
        if (!body) { newTa.focus(); return; }
        addBtn.disabled = true;
        try {
          const c = await API.rnpCommentCreate(sa, date, body);
          const k = rnpsCmtKey(sa, date);
          const arr = rnpSalesState.comments.get(k) || [];
          arr.unshift(c); // новые сверху
          rnpSalesState.comments.set(k, arr);
          rnpsUpdateCellMark(sa, date);
          rnpsRefreshCmtPop();
          App.toast('Комментарий добавлен', 'ok');
        } catch (err) {
          App.toast('Ошибка: ' + (err.message || 'не удалось добавить'), 'err');
          addBtn.disabled = false;
        }
      });
    }

    // Изменить (переключить в режим редактирования).
    pop.querySelectorAll('.rnps-cmt-edit').forEach(b => {
      b.addEventListener('click', () => {
        rnpsCmtPop.editingId = Number(b.getAttribute('data-cid'));
        rnpsRefreshCmtPop();
      });
    });
    // Отмена редактирования.
    const cancelEdit = pop.querySelector('.rnps-cmt-canceledit');
    if (cancelEdit) cancelEdit.addEventListener('click', () => { rnpsCmtPop.editingId = null; rnpsRefreshCmtPop(); });
    // Сохранить правку.
    const saveBtn = pop.querySelector('.rnps-cmt-save');
    const editTa = pop.querySelector('.rnps-cmt-edit-ta');
    if (saveBtn && editTa) {
      saveBtn.addEventListener('click', async () => {
        const cid = Number(saveBtn.getAttribute('data-cid'));
        const body = (editTa.value || '').trim();
        if (!body) { editTa.focus(); return; }
        saveBtn.disabled = true;
        try {
          const upd = await API.rnpCommentUpdate(cid, body);
          const k = rnpsCmtKey(sa, date);
          const arr = rnpSalesState.comments.get(k) || [];
          const idx = arr.findIndex(x => x.id === cid);
          if (idx >= 0) arr[idx] = upd;
          rnpSalesState.comments.set(k, arr);
          rnpsCmtPop.editingId = null;
          rnpsRefreshCmtPop();
          App.toast('Комментарий обновлён', 'ok');
        } catch (err) {
          App.toast('Ошибка: ' + (err.message || 'не удалось сохранить'), 'err');
          saveBtn.disabled = false;
        }
      });
    }
    // Удалить.
    pop.querySelectorAll('.rnps-cmt-del').forEach(b => {
      b.addEventListener('click', async () => {
        const cid = Number(b.getAttribute('data-cid'));
        if (!window.confirm('Удалить комментарий?')) return;
        b.disabled = true;
        try {
          await API.rnpCommentDelete(cid);
          const k = rnpsCmtKey(sa, date);
          let arr = rnpSalesState.comments.get(k) || [];
          arr = arr.filter(x => x.id !== cid);
          if (arr.length) rnpSalesState.comments.set(k, arr); else rnpSalesState.comments.delete(k);
          rnpsUpdateCellMark(sa, date);
          rnpsRefreshCmtPop();
          App.toast('Комментарий удалён', 'ok');
        } catch (err) {
          App.toast('Ошибка: ' + (err.message || 'не удалось удалить'), 'err');
          b.disabled = false;
        }
      });
    });
  }

  // Открыть popover комментариев для ячейки (sa, date).
  function rnpsOpenCmtPopover(anchor, sa, date) {
    // Повторный клик по той же ячейке — закрыть.
    if (rnpsCmtPop.el && rnpsCmtPop.sa === sa && rnpsCmtPop.date === date) { rnpsCloseCmtPopover(); return; }
    rnpsCloseCmtPopover();
    const pop = document.createElement('div');
    pop.className = 'rnps-cmt-pop';
    rnpsCmtPop.el = pop; rnpsCmtPop.sa = sa; rnpsCmtPop.date = date; rnpsCmtPop.anchor = anchor; rnpsCmtPop.editingId = null;
    pop.innerHTML = rnpsCmtPopHtml(sa, date);
    document.body.appendChild(pop);
    rnpsBindCmtPop();
    rnpsPositionCmtPop();
    // Фокус в поле добавления.
    const ta = pop.querySelector('.rnps-cmt-new-ta');
    if (ta) ta.focus();
    // Закрытие по клику вне и по Esc.
    rnpsCmtPop.onDoc = (e) => {
      if (pop.contains(e.target)) return;
      if (anchor && anchor.contains(e.target)) return; // повторный клик по ячейке обработает её handler
      rnpsCloseCmtPopover();
    };
    document.addEventListener('mousedown', rnpsCmtPop.onDoc, true);
    rnpsCmtPop.onEsc = (e) => { if (e.key === 'Escape') rnpsCloseCmtPopover(); };
    document.addEventListener('keydown', rnpsCmtPop.onEsc);
    // При скролле/ресайзе — перепозиционировать (или закрыть, если ячейка ушла).
    rnpsCmtPop.onScroll = () => rnpsPositionCmtPop();
    window.addEventListener('scroll', rnpsCmtPop.onScroll, true);
    window.addEventListener('resize', rnpsCmtPop.onScroll, true);
  }

  function rnpsSetLeafStatus(tree, sa, status) {
    const walk = (n) => {
      if (!n) return;
      if (n.leaf_info && n.leaf_info.seller_article === sa) n.leaf_info.status = status;
      (n.children || []).forEach(walk);
    };
    walk(tree);
  }

  // Экспорт текущего дерева (месячные итоги, «Заказы, шт») в Excel (.xls через HTML).
  function rnpsExport() {
    const data = rnpSalesState.data;
    if (!data || !data.tree) { App.toast('Нет данных для экспорта', 'err'); return; }
    const months = data.months || [];
    let head = '<tr><th>Уровень</th><th>Название</th>';
    months.forEach(m => { head += `<th>${U.esc(m.label)} — Заказы</th><th>План</th><th>Прогноз</th>`; });
    head += '</tr>';
    let body = '';
    const walk = (n) => {
      let row = `<tr><td>${n.level}</td><td>${U.esc(n.level === 4 && n.leaf_info ? n.leaf_info.seller_article : n.name)}</td>`;
      months.forEach(m => {
        const oq = ((n.cells || {})[m.key] || {}).orders_qty;
        const pl = (n.plan || {})[m.key];
        const fc = (n.forecast || {})[m.key];
        row += `<td>${oq == null ? '' : oq}</td><td>${pl == null ? '' : pl}</td><td>${fc == null ? '' : (fc * 100).toFixed(1) + '%'}</td>`;
      });
      row += '</tr>';
      body += row;
      (n.children || []).forEach(walk);
    };
    walk(data.tree);
    const html = `<html><head><meta charset="utf-8"></head><body><table border="1">${head}${body}</table></body></html>`;
    const blob = new Blob(['﻿' + html], { type: 'application/vnd.ms-excel' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = (rnpSalesState.mp === 'wb') ? 'rnp_sales_wb.xls' : (rnpSalesState.mp === 'yandex') ? 'rnp_sales_yandex.xls' : 'rnp_sales_ozon.xls';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function rnpsLoad() {
    // Фиксируем номер ЭТОГО запроса и целевой МП. Если к моменту прихода
    // ответа номер устарел (пользователь переключил МП / перезагрузил) —
    // ответ ИГНОРИРУЕТСЯ: не пишем в rnpSalesState.data, не сбрасываем
    // loading/error (ими управляет актуальный запрос). Возвращаем false —
    // вызывающий rnpSales() тоже прекратит рендер устаревшего результата.
    const mySeq = ++rnpSalesState._reqSeq;
    const myMp = (rnpSalesState.mp === 'wb') ? 'wb' : (rnpSalesState.mp === 'yandex') ? 'yandex' : 'ozon';
    const isStale = () => (mySeq !== rnpSalesState._reqSeq);
    rnpSalesState.loading = true;
    rnpSalesState.error = '';
    try {
      // Даты меняют набор данных/месяцев — идут на бэкенд. Статус/менеджер/поиск
      // фильтруем на клиенте, чтобы списки фильтров оставались полными.
      // Маркетплейс берём из state (ozon | wb) — бэкенд выбирает источник данных.
      const p = { marketplace: myMp };
      if (rnpSalesState.from) p.date_from = rnpSalesState.from;
      if (rnpSalesState.to) p.date_to = rnpSalesState.to;
      // Server checks persisted Pi revision, including another manager's imports.
      // Do not let the tab's indefinite promise cache bypass that check.
      if (myMp !== 'yandex' && API.cacheClear) API.cacheClear('/api/rnp_sales/tree');
      const data = await API.rnpSalesTree(p);
      // Пришёл поздний ответ уже неактуального запроса → выбросить, чтобы
      // не подменить данные текущего МП (причина «задвоения» сумм).
      if (isStale()) return false;
      rnpSalesState.data = data;
      // По умолчанию корень «Итоги» раскрыт до L1.
      if (data.tree && data.tree.key && Object.keys(rnpSalesState.expanded).length === 0) {
        rnpSalesState.expanded[data.tree.key] = true;
      }
      // Комментарии — отдельным запросом за видимый диапазон дат.
      await rnpsLoadComments();
      if (isStale()) return false;
    } catch (err) {
      if (isStale()) return false;
      rnpSalesState.error = err.message || 'Ошибка загрузки';
    } finally {
      // loading снимает только АКТУАЛЬНЫЙ запрос (устаревший мог бы
      // преждевременно погасить индикатор загрузки нового).
      if (!isStale()) rnpSalesState.loading = false;
    }
    return !isStale();
  }

  // Загрузка комментариев за видимый диапазон дат текущего ответа → в Map.
  // Диапазон берём по фактическим дням месяцев (первый..последний),
  // чтобы охватить все ячейки независимо от того, развёрнут ли месяц.
  async function rnpsLoadComments() {
    rnpSalesState.comments = new Map();
    const months = (rnpSalesState.data && rnpSalesState.data.months) || [];
    let dfrom = null, dto = null;
    months.forEach(m => {
      (m.days || []).forEach(d => {
        if (!dfrom || d.key < dfrom) dfrom = d.key;
        if (!dto || d.key > dto) dto = d.key;
      });
    });
    // Фоллбэк на явно выбранный период, если дни ещё не развёрнуты.
    if (!dfrom && rnpSalesState.from) dfrom = rnpSalesState.from;
    if (!dto && rnpSalesState.to) dto = rnpSalesState.to;
    if (!dfrom || !dto) return;
    try {
      const res = await API.rnpCommentsList(dfrom, dto);
      const list = (res && res.comments) || [];
      const map = new Map();
      list.forEach(c => {
        const k = rnpsCmtKey(c.seller_article, c.date);
        if (!map.has(k)) map.set(k, []);
        map.get(k).push(c);
      });
      // Сервер уже отдаёт created_at DESC внутри ячейки; сохраняем порядок.
      rnpSalesState.comments = map;
    } catch (err) {
      // Комментарии — некритичны: матрица отрисуется и без них.
      rnpSalesState.comments = new Map();
    }
  }

  // ===== Компонент выбора периода Ozon (аналог bindRnpPeriod) =====
  // Работает с дневным диапазоном rnpSalesState.from/to (ISO 'YYYY-MM-DD').
  // Переиспользует помощники isoDate/parseISO/fmtRu и CSS-классы .rnp-period*/.rnp-cal*.

  // Подпись поля: «Весь период» либо «дд.мм.гггг — дд.мм.гггг».
  function rnpsPeriodLabel() {
    const f = rnpSalesState.from, t = rnpSalesState.to;
    if (!f && !t) return 'Весь период';
    if (f && t) return f === t ? fmtRu(f) : `${fmtRu(f)} — ${fmtRu(t)}`;
    return fmtRu(f || t);
  }
  // Базовая дата для пресетов — самая поздняя дата с данными (последний день
  // последнего месяца ответа), иначе — сегодня.
  function rnpsPeriodBaseDate() {
    const months = (rnpSalesState.data && rnpSalesState.data.months) || [];
    for (let i = months.length - 1; i >= 0; i--) {
      const days = months[i] && months[i].days;
      if (days && days.length) {
        const last = days[days.length - 1].key;
        const d = parseISO(last);
        if (d) return d;
      }
    }
    return new Date();
  }
  // Диапазон «текущий месяц/квартал/год» относительно базовой даты данных.
  function rnpsPresetRange(kind) {
    const base = rnpsPeriodBaseDate();
    const y = base.getFullYear(), m = base.getMonth();
    let from, to;
    if (kind === 'month') { from = new Date(y, m, 1); to = new Date(y, m + 1, 0); }
    else if (kind === 'quarter') { const q = Math.floor(m / 3); from = new Date(y, q * 3, 1); to = new Date(y, q * 3 + 3, 0); }
    else { from = new Date(y, 0, 1); to = new Date(y, 11, 31); }
    return { from: isoDate(from), to: isoDate(to) };
  }
  // Привязка поля периода и поповера-календаря (2 месяца, выбор диапазона двумя
  // кликами, пресеты + «Весь период»). onApply вызывается при применении.
  function bindRnpsPeriod(wrap, onApply) {
    const periodWrap = wrap.querySelector('#rnps-period');
    const periodFieldEl = wrap.querySelector('#rnps-period-field');
    const calEl = wrap.querySelector('#rnps-cal');
    if (!periodFieldEl || !calEl) return;

    const MONTHS_RU = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
    const DOW_RU = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
    const cal = { from: '', to: '', view: null, open: false };

    function refreshLabel() {
      const el = periodWrap.querySelector('#rnps-period-text');
      if (el) el.textContent = rnpsPeriodLabel();
    }
    function openCal() {
      cal.from = rnpSalesState.from || '';
      cal.to = rnpSalesState.to || '';
      const b = cal.from ? parseISO(cal.from) : rnpsPeriodBaseDate();
      cal.view = new Date(b.getFullYear(), b.getMonth(), 1);
      cal.open = true;
      calEl.hidden = false;
      periodFieldEl.classList.add('open');
      drawCal();
    }
    function closeCal() {
      cal.open = false;
      calEl.hidden = true;
      periodFieldEl.classList.remove('open');
    }
    function applyPeriod() {
      rnpSalesState.from = cal.from || '';
      rnpSalesState.to = cal.to || '';
      refreshLabel();
      closeCal();
      if (onApply) onApply();
    }
    function monthHtml(first) {
      const y = first.getFullYear(), mo = first.getMonth();
      const title = `${MONTHS_RU[mo]} ${y}`;
      let lead = first.getDay() - 1; if (lead < 0) lead = 6;
      const daysIn = new Date(y, mo + 1, 0).getDate();
      const f = cal.from, t = cal.to;
      const lo = (f && t) ? (f <= t ? f : t) : f;
      const hi = (f && t) ? (f <= t ? t : f) : f;

      function dayCell(dt, adjacent, posInRow) {
        const iso = isoDate(dt);
        let c = 'rnp-cal-cell day';
        if (adjacent) c += ' adjacent';
        let inRange = false, isEnd = false;
        if (lo && hi) {
          if (iso === lo || iso === hi) { isEnd = true; }
          else if (iso > lo && iso < hi) { inRange = true; }
        } else if (f && iso === f) { isEnd = true; }
        if (inRange) c += ' in-range';
        if (isEnd) c += ' end';
        if (inRange || isEnd) {
          if (iso === lo || posInRow === 0) c += ' band-l';
          if (iso === hi || posInRow === 6) c += ' band-r';
        }
        return `<button type="button" class="${c}" data-cal-day="${iso}"><span class="d">${dt.getDate()}</span></button>`;
      }

      let cells = '', pos = 0;
      for (let i = lead; i > 0; i--) {
        cells += dayCell(new Date(y, mo, 1 - i), true, pos); pos = (pos + 1) % 7;
      }
      for (let d = 1; d <= daysIn; d++) {
        cells += dayCell(new Date(y, mo, d), false, pos); pos = (pos + 1) % 7;
      }
      const fill = 42 - (lead + daysIn);
      for (let i = 1; i <= fill; i++) {
        cells += dayCell(new Date(y, mo + 1, i), true, pos); pos = (pos + 1) % 7;
      }
      return `<div class="rnp-cal-month">
        <div class="rnp-cal-mtitle">${title}</div>
        <div class="rnp-cal-dow">${DOW_RU.map(x => `<span>${x}</span>`).join('')}</div>
        <div class="rnp-cal-grid">${cells}</div>
      </div>`;
    }
    function drawCal() {
      const m1 = new Date(cal.view.getFullYear(), cal.view.getMonth(), 1);
      const m2 = new Date(cal.view.getFullYear(), cal.view.getMonth() + 1, 1);
      calEl.innerHTML = `
        <div class="rnp-cal-nav">
          <button type="button" class="rnp-cal-arrow" data-cal-nav="-1" title="Предыдущий месяц">‹</button>
          <button type="button" class="rnp-cal-arrow" data-cal-nav="1" title="Следующий месяц">›</button>
        </div>
        <div class="rnp-cal-months">${monthHtml(m1)}${monthHtml(m2)}</div>
        <div class="rnp-cal-presets">
          <button type="button" class="rnp-cal-preset" data-cal-preset="month">Тек. месяц</button>
          <button type="button" class="rnp-cal-preset" data-cal-preset="quarter">Тек. квартал</button>
          <button type="button" class="rnp-cal-preset" data-cal-preset="year">Тек. год</button>
          <button type="button" class="rnp-cal-preset" data-cal-preset="all">Весь период</button>
        </div>`;
    }

    periodFieldEl.addEventListener('click', (e) => {
      e.stopPropagation();
      cal.open ? closeCal() : openCal();
    });
    calEl.addEventListener('click', (e) => {
      e.stopPropagation();
      const nav = e.target.closest('[data-cal-nav]');
      if (nav) {
        const delta = Number(nav.getAttribute('data-cal-nav'));
        cal.view = new Date(cal.view.getFullYear(), cal.view.getMonth() + delta, 1);
        drawCal();
        return;
      }
      const pre = e.target.closest('[data-cal-preset]');
      if (pre) {
        const kind = pre.getAttribute('data-cal-preset');
        if (kind === 'all') { cal.from = ''; cal.to = ''; }
        else { const r = rnpsPresetRange(kind); cal.from = r.from; cal.to = r.to; }
        applyPeriod();
        return;
      }
      const day = e.target.closest('[data-cal-day]');
      if (day) {
        const iso = day.getAttribute('data-cal-day');
        if (!cal.from || (cal.from && cal.to)) {
          cal.from = iso; cal.to = '';
          drawCal();
        } else {
          if (iso < cal.from) { cal.to = cal.from; cal.from = iso; }
          else { cal.to = iso; }
          applyPeriod();
        }
      }
    });
    document.addEventListener('click', (e) => {
      if (cal.open && periodWrap && !periodWrap.contains(e.target)) closeCal();
    });
  }

  function rnpsSubtabs(host) {
    if (!host) return;
    // Третий подтаб «Загрузка данных» — только для админов (эндпоинты загрузки
    // защищены require_admin; для обычных пользователей таб не показываем).
    const tabs = [['wb', 'Wildberries', 'wb'], ['ozon', 'OZON', 'ozon'], ['yandex', 'Yandex', 'yandex']];
    const __u = API.getUser && API.getUser();
    const __isAdmin = !!(__u && (__u.role || '').toLowerCase() === 'admin');
    // Нейтральный (серый) стиль 'cross' — не синий, как Ozon. Флаг true = прижать вправо.
    if (__isAdmin) tabs.push(['upload', 'Загрузка данных', 'cross', true]);
    tabs.forEach(([id, label, c, pushRight]) => {
      const b = document.createElement('button');
      b.className = 'subtab ' + c + (rnpSalesState.mp === id ? ' active' : '') + (pushRight ? ' subtab-right' : '');
      b.textContent = label;
      b.addEventListener('click', () => {
        if (id === rnpSalesState.mp) return;
        // Сохраняем снимок раскрытия+scroll ТЕКУЩЕГО МП перед уходом.
        rnpUiSnapSave(rnpSalesState, rnpSalesState.mp);
        rnpSalesState.mp = id;
        // Списки менеджеров/статусов у каждого МП свои — сбрасываем
        // фильтры, иначе фильтр по менеджеру из прошлого МП «повисает»
        // (dropdown показывает «Все», а таблица пустая).
        rnpSalesState.mgrFilter = ''; rnpSalesState.statusFilter = '';
        // Восстанавливаем снимок НОВОГО МП. Если снимка нет (первый
        // вход на этот МП) — раскрываем текущий месяц по дням, как при
        // первом входе в раздел (см. _periodInit). Если снимок есть —
        // восстанавливаем его как есть (месяц уже был раскрыт раньше).
        const _hadSnap = rnpUiSnapLoad(rnpSalesState, id);
        if (!_hadSnap && (id === 'ozon' || id === 'wb' || id === 'yandex')) {
          const _now = new Date();
          const _curKey = `${_now.getFullYear()}-${String(_now.getMonth() + 1).padStart(2, '0')}`;
          rnpSalesState.monthsOpen[_curKey] = true;
        }
        rnpSalesState.data = null; rnpSalesState.filterExpandSig = '';
        App.renderView();
      });
      host.appendChild(b);
    });
  }

  async function rnpSales(root, ctl, state) {
    ctl.innerHTML = '';
    // pricesOpen — per-node карта (совместимо со сбросом в {}).
    if (!rnpSalesState.pricesOpen || typeof rnpSalesState.pricesOpen !== 'object') rnpSalesState.pricesOpen = {};

    // Период по умолчанию (только при первом входе в раздел за сессию):
    // грузим текущий месяц + 2 предыдущих; текущий месяц раскрыт по дням,
    // предыдущие — свёрнуты. Пользовательский выбор периода это не переопределяет.
    if (!rnpSalesState._periodInit) {
      rnpSalesState._periodInit = true;
      const now = new Date();
      const curKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
      // Начало месяца, отстоящего на 2 назад от текущего.
      const fromD = new Date(now.getFullYear(), now.getMonth() - 2, 1);
      // Конец текущего месяца (последний день).
      const toD = new Date(now.getFullYear(), now.getMonth() + 1, 0);
      rnpSalesState.from = isoDate(fromD);
      rnpSalesState.to = isoDate(toD);
      // Раскрыть текущий месяц по дням (ключ формата YYYY-MM).
      rnpSalesState.monthsOpen[curKey] = true;
    }

    // ЗАГРУЗКА ДАННЫХ — подраздел (только админ): дневной API-файл Ozon + журнал (daily).
    if (rnpSalesState.mp === 'upload') {
      const upTab = rnpSalesState.uploadTab || 'ozon';
      const upBtn = (key, label) =>
        `<button class="subtab up-subtab${upTab === key ? ' active' : ''}" data-uptab="${key}">${label}</button>`;
      const show = (key) => upTab === key ? '' : 'display:none;';
      root.innerHTML = `
        <div class="card rnp-card mp-cross">
          <div class="rnp-toolbar"><div class="subtabs" id="rnps-subtabs"></div></div>
        </div>

        <div class="card" style="padding-top:14px;padding-bottom:14px;">
          <div class="subtabs up-subtabs" id="rnps-up-subtabs">
            ${upBtn('ozon', '📦 Данные для Ozon')}
            ${upBtn('wb', '📊 Данные для Wildberries')}
            ${upBtn('yandex', '🛒 Данные для Yandex')}
            ${upBtn('common', '📁 Общие данные')}
          </div>
        </div>

        <div class="up-pane" data-uppane="ozon" style="${show('ozon')}">
          <div class="card">
            <h3>Ozon — дневной API-файл (РНП заказы)</h3>
            <div class="upload-zone" id="uz-ozday">
              <div style="font-size:28px;">⬆️</div>
              <div>Перетащите Excel сюда или <a href="#" id="pick-ozday">выберите файл</a></div>
              <div class="muted" style="font-size:12px;margin-top:6px;">API-выгрузка Ozon, лист «Данные», .xlsx · дневные данные для раздела «РНП заказы»</div>
              <input type="file" id="file-ozday" accept=".xlsx,.xls" class="hidden">
            </div>
            <div class="muted fact-date-hint">Даты из файла перезаписываются целиком. Новые артикулы добавляются в справочник как нераспределённые.</div>
          </div>
          <div class="card">
            <h3>Ozon — отчёт рекламы (Аналитика продвижения)</h3>
            <div class="upload-zone" id="uz-ozads">
              <div style="font-size:28px;">⬆️</div>
              <div>Перетащите Excel сюда или <a href="#" id="pick-ozads">выберите файл</a></div>
              <div class="muted" style="font-size:12px;margin-top:6px;">Выгрузка «Аналитика продвижения», лист «Statistics», .xlsx · за один день · Расход и CTR</div>
              <input type="file" id="file-ozads" accept=".xlsx,.xls" class="hidden">
            </div>
            <div class="muted fact-date-hint">Где взять в Ozon: Продвижение → Аналитика продвижения → Скачать отчёт → выбираем период ОДИН день.</div>
            <div class="muted fact-date-hint">Расход и CTR дописываются к дневным продажам за эту дату (связь по SKU). Если у товара есть реклама, но нет продаж/заказов за этот день — строка создаётся автоматически (продажи=0), расход не теряется. ДРР рассчитывается автоматически.</div>
          </div>
          <div class="card">
            <h3>Рейтинг и отзывы — OZON</h3>
            <div class="stock-date-row" style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap;">
              <label for="date-ozreviews" style="font-size:13px;color:#28251d;font-weight:500;">Дата отчёта:</label>
              <input type="date" id="date-ozreviews" style="padding:6px 9px;border:1px solid #d4d1ca;border-radius:6px;font-size:13px;background:#fff;color:#28251d;">
              <span class="muted" style="font-size:12px;">— на какую дату действителен отчёт</span>
            </div>
            <div class="upload-zone" id="uz-ozreviews">
              <div style="font-size:28px;">⬆️</div>
              <div>Перетащите Excel сюда или <a href="#" id="pick-ozreviews">выберите файл</a></div>
              <div class="muted" style="font-size:12px;margin-top:6px;">Отчёт «Список товаров» Ozon, .xlsx (или .csv) · Рейтинг и Количество отзывов</div>
              <input type="file" id="file-ozreviews" accept=".xlsx,.xls,.csv" class="hidden">
            </div>
            <div class="muted fact-date-hint">Отчёт скачиваем из л/к Ozon: Товары → Список товаров → Скачать шаблон → скачиваем шаблон товаров в формате XLSX. При загрузке устанавливаем дату отчёта на нужную дату.</div>
            <div class="muted fact-date-hint">Даты в файле нет — укажите её выше перед загрузкой. Сопоставление по колонке «Артикул»; пишется только по товарам из справочника дашборда.</div>
          </div>
          <div class="card">
            <h3>Мониторинг цен конкурентов Ozon</h3>
            <div class="stock-date-row" style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap;">
              <label for="date-ozpriceva" style="font-size:13px;color:#28251d;font-weight:500;">Дата мониторинга:</label>
              <input type="date" id="date-ozpriceva" style="padding:6px 9px;border:1px solid #d4d1ca;border-radius:6px;font-size:13px;background:#fff;color:#28251d;">
              <span class="muted" style="font-size:12px;">— на какую дату загружаются цены конкурентов</span>
            </div>
            <div class="upload-zone" id="uz-ozpriceva">
              <div style="font-size:28px;">⬆️</div>
              <div>Перетащите Excel сюда или <a href="#" id="pick-ozpriceva">выберите файл</a></div>
              <div class="muted" style="font-size:12px;margin-top:6px;">Отчёт PriceVA, лист «Отчёт», .xlsx · мин./средняя цена конкурентов по артикулу</div>
              <input type="file" id="file-ozpriceva" accept=".xlsx,.xls" class="hidden">
            </div>
            <div class="muted fact-date-hint">Даты в файле нет — укажите её выше перед загрузкой. Колонка «Мин. цена руб.» → метрика «Минимальная цена конкурентов»; «Средняя цена руб.» → «Средняя цена конкурентов».</div>
            <div class="muted fact-date-hint">Сопоставление по колонке «Артикул» (разные пробелы/регистр не мешают). Пишется только по товарам из справочника дашборда; если за дату нет строки продаж — создаётся строка только с ценами.</div>
          </div>
          <div class="card">
            <div class="up-hist-head"><h3 style="margin:0;">Журнал загрузок — данные для Ozon</h3></div>
            <div id="uphist-ozon"><div class="loader"><span class="spinner"></span></div></div>
          </div>
        </div>

        <div class="up-pane" data-uppane="wb" style="${show('wb')}">
          <div class="card">
            <h3>Wildberries — Воронка продаж (РНП заказы)</h3>
            <div class="upload-zone" id="uz-wbday">
              <div style="font-size:28px;">⬆️</div>
              <div>Перетащите Excel сюда или <a href="#" id="pick-wbday">выберите файл</a></div>
              <div class="muted" style="font-size:12px;margin-top:6px;">Отчёт «Воронка продаж» WB, лист «TDSheet», .xlsx · дневные данные для раздела «РНП заказы»</div>
              <input type="file" id="file-wbday" accept=".xlsx,.xls" class="hidden">
            </div>
            <div class="muted fact-date-hint">Где взять: 1С → отчёт «Статистика рекламных кампаний Wildberries» → период (со вчерашнего дня и два предыдущих) → организация «ТД АВТОПРОФИ» → кнопка «Статистика по карточкам товаров». Откроются два отчёта — «Рейтинг товаров» и «Воронка». Сюда загружаем «Воронку».</div>
            <div class="muted fact-date-hint">Даты берутся из файла и перезаписываются целиком (WB иногда обновляет прошлые периоды). Новые артикулы добавляются в справочник как нераспределённые.</div>
          </div>
          <div class="card">
            <h3>Wildberries — Рейтинг и отзывы</h3>
            <div class="upload-zone" id="uz-wbreviews">
              <div style="font-size:28px;">⬆️</div>
              <div>Перетащите Excel сюда или <a href="#" id="pick-wbreviews">выберите файл</a></div>
              <div class="muted" style="font-size:12px;margin-top:6px;">Отчёт «Рейтинг и отзывы» WB, лист «TDSheet», .xlsx · Количество отзывов + СПП</div>
              <input type="file" id="file-wbreviews" accept=".xlsx,.xls" class="hidden">
            </div>
            <div class="muted fact-date-hint">Где взять: 1С → отчёт «Статистика рекламных кампаний Wildberries» → период (со вчерашнего дня и два предыдущих) → организация «ТД АВТОПРОФИ» → кнопка «Статистика по карточкам товаров». Откроются два отчёта — «Рейтинг товаров» и «Воронка». Сюда загружаем «Рейтинг товаров».</div>
            <div class="muted fact-date-hint">Дата берётся из колонки «Дата» самого отчёта (поддерживаются многодневные файлы) — указывать её не нужно. Сопоставление по артикулу; пишется только по товарам из справочника WB. СПП (колонка «ПроцентСПП») → метрика «Соинвест (СПП)». Рейтинг берётся из Воронки.</div>
          </div>
          <div class="card">
            <h3>Wildberries — отчёт рекламы (Статистика РК)</h3>
            <div class="upload-zone" id="uz-wbads">
              <div style="font-size:28px;">⬆️</div>
              <div>Перетащите Excel сюда или <a href="#" id="pick-wbads">выберите файл</a></div>
              <div class="muted" style="font-size:12px;margin-top:6px;">Отчёт «Статистика рекламных кампаний» WB, .xlsx · Расход и CTR</div>
              <input type="file" id="file-wbads" accept=".xlsx,.xls" class="hidden">
            </div>
            <div class="muted fact-date-hint">Где взять: 1С → отчёт «Статистика рекламных кампаний Wildberries» → период (со вчерашнего дня и два предыдущих) → организация «ТД АВТОПРОФИ» → кнопка «Сформировать» в поле отчёта → сохранить отчёт в XLSX.</div>
            <div class="muted fact-date-hint">Дата берётся из файла. Несколько кампаний по одному артикулу суммируются. Расход и CTR дописываются к дневным продажам за эту дату. Сначала загрузите Воронку за ту же дату. ДРР рассчитывается автоматически.</div>
          </div>
          <div class="card">
            <h3>Мониторинг цен конкурентов Wildberries</h3>
            <div class="stock-date-row" style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap;">
              <label for="date-wbpriceva" style="font-size:13px;color:#28251d;font-weight:500;">Дата мониторинга:</label>
              <input type="date" id="date-wbpriceva" style="padding:6px 9px;border:1px solid #d4d1ca;border-radius:6px;font-size:13px;background:#fff;color:#28251d;">
              <span class="muted" style="font-size:12px;">— на какую дату загружаются цены конкурентов</span>
            </div>
            <div class="upload-zone" id="uz-wbpriceva">
              <div style="font-size:28px;">⬆️</div>
              <div>Перетащите Excel сюда или <a href="#" id="pick-wbpriceva">выберите файл</a></div>
              <div class="muted" style="font-size:12px;margin-top:6px;">Отчёт PriceVA, лист «Отчёт», .xlsx · мин./средняя цена конкурентов по артикулу</div>
              <input type="file" id="file-wbpriceva" accept=".xlsx,.xls" class="hidden">
            </div>
            <div class="muted fact-date-hint">Формат файла тот же, что у Ozon, но данные — для WB. Даты в файле нет — укажите её выше перед загрузкой. Колонка «Мин. цена руб.» → метрика «Минимальная цена конкурентов»; «Средняя цена руб.» → «Средняя цена конкурентов».</div>
            <div class="muted fact-date-hint">Сопоставление по колонке «Артикул» (разные пробелы/регистр не мешают). Пишется только по товарам из справочника дашборда; если за дату нет строки продаж — создаётся строка только с ценами.</div>
          </div>
          <div class="card">
            <h3>Индекс цен Wildberries (Pi)</h3>
            <div class="stock-date-row" style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap;">
              <label for="date-wbpi" style="font-size:13px;color:#28251d;font-weight:500;">Дата выгрузки:</label>
              <input type="date" id="date-wbpi" style="padding:6px 9px;border:1px solid #d4d1ca;border-radius:6px;font-size:13px;background:#fff;color:#28251d;">
              <span class="muted" style="font-size:12px;">— на какую дату загружается индекс цен</span>
            </div>
            <div class="upload-zone" id="uz-wbpi">
              <div style="font-size:28px;">⬆️</div>
              <div>Перетащите Excel сюда или <a href="#" id="pick-wbpi">выберите файл</a></div>
              <div class="muted" style="font-size:12px;margin-top:6px;">Отчёт «Индекс цен» ЛК WB, лист «Отчет - индекс цен», .xlsx · Pi по артикулу</div>
              <input type="file" id="file-wbpi" accept=".xlsx,.xls" class="hidden">
            </div>
            <div class="muted fact-date-hint">Раздел ЛК WB «Товары и цены → Индекс цен». За день выгружаются ДВА файла («товары с выгодной ценой» и «товары с высокой ценой») — загрузите их по очереди, указав одну и ту же дату.</div>
            <div class="muted fact-date-hint">Даты в файле нет — укажите её выше перед загрузкой. Pi = «Цена товара на WB» / «Цена идентичного товара» (коэффициент, напр. 1,09) → метрика «Индекс цены, Pi». По товарам из справочника дашборда; остальные пропускаются.</div>
          </div>
          <div class="card">
            <div class="up-hist-head"><h3 style="margin:0;">Журнал загрузок — данные для Wildberries</h3></div>
            <div id="uphist-wb"><div class="loader"><span class="spinner"></span></div></div>
          </div>
        </div>

        <div class="up-pane" data-uppane="yandex" style="${show('yandex')}">
          <div class="card">
            <h3>Аналитика продаж Yandex</h3>
            <div class="upload-zone" id="uz-yaday">
              <div style="font-size:28px;">⬆️</div>
              <div>Перетащите Excel сюда или <a href="#" id="pick-yaday">выберите файл</a></div>
              <div class="muted" style="font-size:12px;margin-top:6px;">Отчёт Яндекс.Маркет, лист «Аналитика продаж», .xlsx · показы/переходы/заказы/отмены по дням</div>
              <input type="file" id="file-yaday" accept=".xlsx,.xls" class="hidden">
            </div>
            <div class="muted fact-date-hint">ЛК Яндекс.Маркет → Аналитика → «Аналитика продаж» (воронка по дням). Дата берётся из колонки «День» самого отчёта — указывать не нужно.</div>
            <div class="muted fact-date-hint">Сопоставление по колонке «Ваш SKU». Новые артикулы автоматически добавляются в Справочник (Yandex) как нераспределённые — заполните им группы/статус/менеджера. CTR считается (клики/показы).</div>
          </div>
          <div class="card">
            <h3>Остатки склада Яндекс (FBY)</h3>
            <div class="stock-date-row" style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap;">
              <label for="date-yastock" style="font-size:13px;color:#28251d;font-weight:500;">Дата остатков:</label>
              <input type="date" id="date-yastock" style="padding:6px 9px;border:1px solid #d4d1ca;border-radius:6px;font-size:13px;background:#fff;color:#28251d;">
              <span class="muted" style="font-size:12px;">— за какую дату загружаются остатки склада Яндекс</span>
            </div>
            <div class="upload-zone" id="uz-yastock">
              <div style="font-size:28px;">⬆️</div>
              <div>Перетащите Excel сюда или <a href="#" id="pick-yastock">выберите файл</a></div>
              <div class="muted" style="font-size:12px;margin-top:6px;">Выгрузка Яндекс.Маркет, лист «Остатки на складе», .xlsx · колонка «Доступно для заказа»</div>
              <input type="file" id="file-yastock" accept=".xlsx,.xls" class="hidden">
            </div>
            <div class="muted fact-date-hint">Отчет «Остатки на складах» — вариант отчета «Краткий, за конкретную дату». Даты в файле нет — укажите её выше перед загрузкой.</div>
            <div class="muted fact-date-hint">Остаток по SKU суммируется по всем складам → метрика «Остаток на складе Яндекс, шт» (столбец «Доступно для заказа») в РНП заказы Яндекс.</div>
          </div>
          <div class="card">
            <div class="up-hist-head"><h3 style="margin:0;">Журнал загрузок — данные для Yandex</h3></div>
            <div id="uphist-ya"><div class="loader"><span class="spinner"></span></div></div>
          </div>
        </div>

        <div class="up-pane" data-uppane="common" style="${show('common')}">
          <div class="card">
            <h3>Остатки склада АВТОПРОФИ (МСК)</h3>
            <div class="stock-date-row" style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap;">
              <label for="date-ozstock" style="font-size:13px;color:#28251d;font-weight:500;">Дата остатков:</label>
              <input type="date" id="date-ozstock" style="padding:6px 9px;border:1px solid #d4d1ca;border-radius:6px;font-size:13px;background:#fff;color:#28251d;">
              <span class="muted" style="font-size:12px;">— за какую дату загружаются остатки склада</span>
            </div>
            <div class="upload-zone" id="uz-ozstock">
              <div style="font-size:28px;">⬆️</div>
              <div>Перетащите Excel сюда или <a href="#" id="pick-ozstock">выберите файл</a></div>
              <div class="muted" style="font-size:12px;margin-top:6px;">Выгрузка 1С, лист «TDSheet», .xlsx · общий отчёт остатков по всем товарам (Ozon и Wildberries)</div>
              <input type="file" id="file-ozstock" accept=".xlsx,.xls" class="hidden">
            </div>
            <div class="muted fact-date-hint">1С → Настройки выгрузки остатков → Остатки МСК (Автопрофи) → выберите дату, за которую нужно вывести отчёт с остатками товаров.</div>
            <div class="muted fact-date-hint">Даты в файле нет — укажите её выше перед загрузкой. Остаток (колонка «Остаток») → метрика «Остаток на складе АВТОПРОФИ, шт».</div>
          </div>
          <div class="card">
            <div class="up-hist-head"><h3 style="margin:0;">Журнал загрузок — общие данные</h3></div>
            <div id="uphist-common"><div class="loader"><span class="spinner"></span></div></div>
          </div>
        </div>`;
      rnpsSubtabs(root.querySelector('#rnps-subtabs'));
      // Переключение вложенных вкладок «Загрузка данных».
      root.querySelectorAll('#rnps-up-subtabs .up-subtab').forEach(btn => {
        btn.addEventListener('click', () => {
          const key = btn.dataset.uptab;
          if (key === rnpSalesState.uploadTab) return;
          rnpSalesState.uploadTab = key;
          root.querySelectorAll('#rnps-up-subtabs .up-subtab').forEach(b =>
            b.classList.toggle('active', b.dataset.uptab === key));
          root.querySelectorAll('.up-pane').forEach(p =>
            p.style.display = (p.dataset.uppane === key) ? '' : 'none');
        });
      });
      setupOzDayZone(root);
      setupOzAdsZone(root);
      setupOzStockZone(root);
      setupOzReviewsZone(root);
      setupOzPricevaZone(root);
      setupWbDayZone(root);
      setupWbReviewsZone(root);
      setupWbAdsZone(root);
      setupWbPricevaZone(root);
      setupWbPiZone(root);
      setupYaDayZone(root);
      setupYaStockZone(root);
      await loadRnpsUploadHistory();
      return;
    }

    // РНП ЗАКАЗЫ — единый рендер для Ozon, Wildberries и Yandex.
    // Класс карточки задаёт бренд-акцент (синий Ozon / фиолетовый WB /
    // жёлтый Yandex) — весь дизайн/метрики/фильтры/группировки идентичны,
    // различаются лишь источник данных (бэкенд) и цвет (CSS .mp-*).
    const isWb = rnpSalesState.mp === 'wb';
    const isYa = rnpSalesState.mp === 'yandex';
    const mpCls = isWb ? 'mp-wb' : isYa ? 'mp-yandex' : 'mp-ozon';
    const mpTitle = isWb ? 'Заказы по дням Wildberries'
      : isYa ? 'Заказы по дням Yandex' : 'Заказы по дням Ozon';

    root.innerHTML = `<div class="card rnp-card ${mpCls}"><div class="rnp-toolbar"><div class="subtabs" id="rnps-subtabs"></div></div><div class="empty" style="padding:40px;">Загрузка…</div></div>`;
    rnpsSubtabs(root.querySelector('#rnps-subtabs'));

    // ЗАЩИТА ОТ ГОНКИ: rnpsLoad() вернёт false, если пока летел запрос,
    // пользователь переключил МП / перезапустил рендер. В этом случае
    // активен более новый rnpSales(), который сам отрисует актуальный МП.
    // Этот (устаревший) вызов должен МОЛЧА выйти, НЕ перезаписав
    // root данными/заголовком чужого МП (причина «задвоения» сумм).
    const _fresh = await rnpsLoad();
    if (!_fresh) return;

    if (rnpSalesState.error) {
      root.innerHTML = `<div class="card rnp-card ${mpCls}"><div class="rnp-toolbar"><div class="subtabs" id="rnps-subtabs"></div></div><div class="empty" style="padding:40px; color:#b00;">${U.esc(rnpSalesState.error)}</div></div>`;
      rnpsSubtabs(root.querySelector('#rnps-subtabs'));
      return;
    }

    const data = rnpSalesState.data || {};
    const undist = data.undistributed || { count: 0, articles: [] };

    // Плашка нераспределённых.
    let undistHtml = '';
    if (undist.count > 0) {
      undistHtml = `<div class="rnp-warn">
        <div class="rnp-warn-h">⚠️ Требуют распределения в справочнике: <a href="#" id="rnps-undist-link" class="rnp-warn-count">${undist.count}</a></div>
        <div class="rnp-warn-d">Эти товары есть в справочнике, но у них не заполнены группы (К1/К2/К3), статус или менеджер, поэтому не попадают в иерархию (их обороты учтены только в строке «Итоги»). Заполните данные в разделе «Справочник».</div>
      </div>`;
    }

    // Опции фильтров.
    const mgrOpts = ['<option value="">Все менеджеры</option>']
      .concat((data.managers || []).map(m => `<option value="${U.esc(m)}"${rnpSalesState.mgrFilter === m ? ' selected' : ''}>${U.esc(m)}</option>`)).join('');
    const stOpts = ['<option value="">Все статусы</option>']
      .concat(STATUS_PRESETS.map(s => `<option value="${U.esc(s)}"${rnpSalesState.statusFilter === s ? ' selected' : ''}>${U.esc(s)}</option>`)).join('');

    root.innerHTML = `
      ${undistHtml}
      <div class="card rnp-card ${mpCls}">
        <div class="rnp-toolbar">
          <div class="subtabs" id="rnps-subtabs"></div>
          <div class="rnp-filters-host">
            <input class="input-sm" id="rnps-search" placeholder="Поиск по артикулу…" value="${U.esc(rnpSalesState.searchArt || '')}">
            <select class="input-sm" id="rnps-mgr">${mgrOpts}</select>
            <select class="input-sm" id="rnps-status">${stOpts}</select>
            <button class="btn-sm" id="rnps-expand">Развернуть всё</button>
            <button class="btn-sm" id="rnps-collapse">Свернуть всё</button>
            <div class="rnp-anom" id="rnps-anom-host"></div>
            <div class="rnp-period" id="rnps-period">
              <button class="rnp-period-field" id="rnps-period-field" type="button" title="Выбрать период (показываются дни в выбранном диапазоне)">
                <svg class="rnp-period-ico" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/></svg>
                <span class="rnp-period-text" id="rnps-period-text">${U.esc(rnpsPeriodLabel())}</span>
              </button>
              <div class="rnp-cal" id="rnps-cal" hidden></div>
            </div>
          </div>
        </div>
        <div class="rnp-head-sticky">
          <div class="rnp-card-title">${mpTitle}</div>
          <div class="rnp-legend-row">
            <span class="rnp-legend-fill rnp-fixleft"></span>
            <span class="rnp-status-leg rnp-fixleft">Статус</span>
            <span class="rnp-legend-mspacer rnp-fixleft"></span>
            <div class="rnp-wk-scroll" data-rnp-scroll><div class="rnps-legend"></div></div>
          </div>
        </div>
        <div class="tbl-wrap rnp-matrix-wrap">
          <div id="rnps-matrix"></div>
        </div>
        <div class="rnp-hscroll-bar" data-rnp-scroll><div class="rnp-hscroll-phantom"></div></div>
      </div>`;

    rnpsSubtabs(root.querySelector('#rnps-subtabs'));

    // Переход в справочник по нераспределённым.
    const undistLink = root.querySelector('#rnps-undist-link');
    if (undistLink) {
      undistLink.addEventListener('click', (e) => {
        e.preventDefault();
        const arts = (undist.articles || []).map(a => a.seller_article).filter(Boolean);
        catState.search = ''; catState.only_unc = false;
        catState.undistArticles = arts;
        catState.mp = isWb ? 'wb' : isYa ? 'yandex' : 'ozon';
        catState.tree = null;
        App.setView('catalog');
      });
    }

    // Фильтры: поиск/статус/менеджер — локально; статус/менеджер + даты → перезагрузка.
    const searchEl = root.querySelector('#rnps-search');
    if (searchEl) {
      let t = null;
      searchEl.addEventListener('input', () => {
        clearTimeout(t);
        t = setTimeout(() => { rnpSalesState.searchArt = searchEl.value.trim(); rnpsRenderMatrix(); }, 250);
      });
    }
    const mgrEl = root.querySelector('#rnps-mgr');
    if (mgrEl) mgrEl.addEventListener('change', () => { rnpSalesState.mgrFilter = mgrEl.value; rnpsRenderMatrix(); });
    const statusEl = root.querySelector('#rnps-status');
    if (statusEl) statusEl.addEventListener('change', () => { rnpSalesState.statusFilter = statusEl.value; rnpsRenderMatrix(); });
    const reload = async () => {
      if (API.cacheClear) API.cacheClear('/api/rnp_sales/tree');
      // Если загрузка устарела (МП переключён во время запроса) — не
      // перерисовываем: актуальный rnpSales() сделает это сам.
      const _fresh = await rnpsLoad();
      if (_fresh) App.renderView();
    };
    // Единый компонент выбора периода (справа): даты уходят на бэкенд → reload.
    bindRnpsPeriod(root.querySelector('.rnp-filters-host'), reload);
    const expEl = root.querySelector('#rnps-expand');
    if (expEl) expEl.addEventListener('click', () => { if (rnpSalesState.data && rnpSalesState.data.tree) rnpsExpandAll(rnpSalesState.data.tree); rnpsRenderMatrix(); });
    const colEl = root.querySelector('#rnps-collapse');
    if (colEl) colEl.addEventListener('click', () => {
      const rootKey = rnpSalesState.data && rnpSalesState.data.tree ? rnpSalesState.data.tree.key : null;
      rnpSalesState.expanded = {}; rnpSalesState.metricsOpen = {};
      if (rootKey) rnpSalesState.expanded[rootKey] = true;
      rnpsRenderMatrix();
    });

    // Переключатель «Аномалии» (мультивыбор правил + период + пороги).
    rnpsRenderAnomHost();

    rnpsRenderMatrix();
    // Восстанавливаем вертикальную прокрутку окна на место, где были до ухода.
    rnpUiScrollApply(rnpSalesState, rnpSalesState.mp);
  }

  // ======================= СКЛАДЫ =======================
  // Раздел «Склады». Шапка идентична «РНП заказы»: табы Wildberries / OZON
  // и справа подтаб «Загрузка данных» (только для админа). Пока во всех
  // подтабах — заглушка; наполнение появится позже.
  // mp: 'wb'|'ozon'|'upload'; uploadTab: 'wb'|'ozon'; wbSub: 'general'|'bywh'|'catalog'
  // whFrom/whTo: ISO-границы периода «По складам» (пусто = весь период); whData — кэш ответа для пресетов;
  // whPriceMode: 'cost' (по умолчанию — себестоимость) | 'sale' (в ценах продажи)
  const whState = {
    mp: 'wb', uploadTab: 'wb', whFrom: '', whTo: '', whData: null, whPriceMode: 'cost', whCollapsed: {},
    // --- OZON (зеркало WB выше; отдельные поля, чтобы не конфликтовать с WB) ---
    ozSub: 'bywh', ozFrom: '', ozTo: '', ozData: null, ozPriceMode: 'cost', ozCollapsed: {},
  };

  // Зона загрузки остатков WB по складам (подтаб «Загрузка данных» → Wildberries).
  // Дата берётся из поля #date-wbstock (в файле даты нет).
  function setupWbStockZone(root) {
    const zone = root.querySelector('#uz-wbstock');
    if (!zone) return;
    const input = root.querySelector('#file-wbstock');
    const pick = root.querySelector('#pick-wbstock');
    const dateEl = root.querySelector('#date-wbstock');
    if (dateEl && !dateEl.value) {
      const now = new Date();
      dateEl.value = now.getFullYear() + '-' +
        String(now.getMonth() + 1).padStart(2, '0') + '-' +
        String(now.getDate()).padStart(2, '0');
    }
    if (pick) pick.addEventListener('click', (e) => { e.preventDefault(); input.click(); });
    input.addEventListener('change', () => { if (input.files[0]) doUploadWbStock(input.files[0], zone); });
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('drag'); }));
    zone.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) doUploadWbStock(f, zone); });
  }

  async function doUploadWbStock(file, zone) {
    zone.querySelectorAll('.up-warn, .note.err').forEach(el => el.remove());
    const dateEl = document.getElementById('date-wbstock');
    const stockDate = dateEl ? dateEl.value : '';
    if (!stockDate) {
      App.toast('Сначала укажите дату, за которую загружаются остатки.', 'err');
      return;
    }
    const orig = zone.innerHTML;
    let warnHtml = '', errHtml = '';
    zone.innerHTML = '<div class="loader"><span class="spinner"></span><div style="margin-top:8px">Загрузка ' + U.esc(file.name) + '…</div></div>';
    try {
      const res = await API.uploadWbStock(file, stockDate);
      const period = res.period_text || '';
      const prod = res.rows_products || 0;
      const cells = res.rows_stock_cells || 0;
      const whTotal = res.warehouses_total || 0;
      const whNew = res.warehouses_new || [];
      // Раньше показывали «пропущено вне справочника» (skipped_not_in_catalog).
      // Теперь загрузчик сам заводит новые артикулы автоматически — это
      // «нераспределённые» товары, которые нужно проверить в справочнике.
      const artsNew = res.arts_new || 0;
      const artsNewList = res.arts_new_list || [];
      App.toast('✓ Остатки WB по складам · товаров: ' + U.fmtNum(prod) + (period ? ' · ' + period : ''), 'ok');
      let extra = ' Ячеек склад×товар: ' + U.fmtNum(cells) + '. Складов: ' + U.fmtNum(whTotal) + '.';
      if (whNew.length) { extra += ' Новых складов: ' + U.fmtNum(whNew.length) + ' (' + whNew.map(U.esc).join(', ') + ').'; }
      if (artsNew) {
        extra += ' Заведено новых артикулов (нераспределённые): ' + U.fmtNum(artsNew)
          + (artsNewList.length ? ' (' + artsNewList.slice(0, 20).map(U.esc).join(', ') + (artsNewList.length > 20 ? '…' : '') + ')' : '') + '.';
      }
      warnHtml = '<div class="up-warn" style="background:#f0f6f0;border-color:#c5dcc5;color:#2f5a2f;">✓ Записано остатков WB по складам за ' + U.esc(period) + ': товаров ' + U.fmtNum(prod) + '.' + extra + '</div>';
    } catch (e) {
      App.toast('Ошибка загрузки: ' + e.message, 'err');
      errHtml = '<div class="note err" style="margin-top:10px;padding:9px 12px;font-size:12.5px;line-height:1.4">✖ Файл не загружен. ' + U.esc(e.message) + '</div>';
    } finally {
      zone.innerHTML = orig;
      setupWbStockZone(document.getElementById('view-root'));
      if (warnHtml || errHtml) { zone.insertAdjacentHTML('beforeend', warnHtml + errHtml); }
      if (document.getElementById('uphist-wbstock')) await loadWhUploadHistory();
    }
  }

  // Зона загрузки СЕБЕСТОИМОСТИ (подтаб «Загрузка данных» → Wildberries).
  // Даты нет — справочник, overwrite by seller_article.
  function setupCostZone(root) {
    const zone = root.querySelector('#uz-cost');
    if (!zone) return;
    const input = root.querySelector('#file-cost');
    const pick = root.querySelector('#pick-cost');
    const dateEl = root.querySelector('#date-cost');
    if (dateEl && !dateEl.value) {
      const now = new Date();
      dateEl.value = now.getFullYear() + '-' +
        String(now.getMonth() + 1).padStart(2, '0') + '-' +
        String(now.getDate()).padStart(2, '0');
    }
    if (pick) pick.addEventListener('click', (e) => { e.preventDefault(); input.click(); });
    input.addEventListener('change', () => { if (input.files[0]) doUploadCost(input.files[0], zone); });
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('drag'); }));
    zone.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) doUploadCost(f, zone); });
  }

  async function doUploadCost(file, zone) {
    zone.querySelectorAll('.up-warn, .note.err').forEach(el => el.remove());
    // Дата действия цен — обязательна (историчность).
    const dateEl = document.getElementById('date-cost');
    const startDate = dateEl ? (dateEl.value || '').trim() : '';
    if (!startDate) {
      App.toast('Укажите дату действия цен перед загрузкой', 'err');
      if (dateEl) { dateEl.focus(); dateEl.style.borderColor = '#A12C7B'; }
      return;
    }
    if (dateEl) dateEl.style.borderColor = '#d4d1ca';
    const orig = zone.innerHTML;
    let warnHtml = '', errHtml = '';
    zone.innerHTML = '<div class="loader"><span class="spinner"></span><div style="margin-top:8px">Загрузка ' + U.esc(file.name) + '…</div></div>';
    try {
      const res = await API.uploadCost(file, startDate);
      const loaded = res.rows_loaded || 0;
      const catTotal = res.catalog_total || 0;
      const catMissing = res.catalog_missing || 0;
      const missOnStock = res.missing_on_stock;
      const sd = res.start_date || startDate;
      App.toast('✓ Себестоимость · записано товаров: ' + U.fmtNum(loaded), 'ok');
      let extra = catTotal ? (' из ' + U.fmtNum(catTotal) + ' товаров справочника.') : '.';
      if (catMissing) {
        extra += ' Без себестоимости осталось: ' + U.fmtNum(catMissing) + ' (нет в файле или пустая с/с)';
        if (missOnStock != null) { extra += ', из них на остатках WB: ' + U.fmtNum(missOnStock); }
        extra += '.';
      }
      warnHtml = '<div class="up-warn" style="background:#f0f6f0;border-color:#c5dcc5;color:#2f5a2f;">✓ Записана себестоимость с ' + U.esc(sd) + ': товаров ' + U.fmtNum(loaded) + extra + '</div>';
    } catch (e) {
      App.toast('Ошибка загрузки: ' + e.message, 'err');
      errHtml = '<div class="note err" style="margin-top:10px;padding:9px 12px;font-size:12.5px;line-height:1.4">✖ Файл не загружен. ' + U.esc(e.message) + '</div>';
    } finally {
      zone.innerHTML = orig;
      setupCostZone(document.getElementById('view-root'));
      if (warnHtml || errHtml) { zone.insertAdjacentHTML('beforeend', warnHtml + errHtml); }
      if (document.getElementById('uphist-cost')) await loadOneUploadHistory('uphist-cost', 'cost');
    }
  }

  // Журналы загрузок: остатки WB (wb_stock) + себестоимость (cost) + остатки Ozon (ozon_stock).
  async function loadWhUploadHistory() {
    await loadOneUploadHistory('uphist-wbstock', 'wb_stock');
    if (document.getElementById('uphist-cost')) await loadOneUploadHistory('uphist-cost', 'cost');
    if (document.getElementById('wh-dq-body')) await whRenderDataQuality();
    if (document.getElementById('uphist-ozstock')) await loadOneUploadHistory('uphist-ozstock', 'ozon_stock');
    if (document.getElementById('oz-dq-body')) await ozRenderDataQuality();
  }

  // ===== Баннер достоверности стоимостной оценки остатков =====
  // Показывает по ПОСЛЕДНЕЙ дате остатков: сколько артикулов без с/с
  // или без цены (такие занижают KPI). Список — разворачиваемый, с выгрузкой.
  let _dqData = null;
  async function whRenderDataQuality() {
    const body = document.getElementById('wh-dq-body');
    if (!body) return;
    body.innerHTML = '<div class="loader"><span class="spinner"></span></div>';
    let res;
    try {
      res = await API.whDataQuality();
    } catch (e) {
      body.innerHTML = '<div class="note err" style="padding:9px 12px;font-size:12.5px;">✖ Не удалось получить данные достоверности. ' + U.esc(e.message) + '</div>';
      return;
    }
    _dqData = res;
    if (!res || !res.date) {
      body.innerHTML = '<div class="muted" style="padding:10px 2px;font-size:13px;">Нет загруженных остатков — проверять нечего.</div>';
      return;
    }
    const prob = res.problem_arts || 0;
    const totalArts = res.total_arts || 0;
    const dateStr = U.fmtDate ? U.fmtDate(res.date) : res.date;
    if (prob === 0) {
      body.innerHTML = '<div class="up-warn" style="background:#f0f6f0;border-color:#c5dcc5;color:#2f5a2f;">'
        + '✓ На ' + U.esc(dateStr) + ' все артикулы на остатках (' + U.fmtNum(totalArts) + ') имеют и себестоимость, и среднюю цену. Стоимостная оценка достоверна.'
        + '</div>';
      return;
    }
    // Счётчики: «без с/с» = no_cost + no_both; «без цены» = no_price + no_both.
    const noCostTot = (res.no_cost || 0) + (res.no_both || 0);
    const noPriceTot = (res.no_price || 0) + (res.no_both || 0);
    const parts = [];
    if (noCostTot) parts.push('без себестоимости: <b>' + U.fmtNum(noCostTot) + '</b>');
    if (noPriceTot) parts.push('без средней цены: <b>' + U.fmtNum(noPriceTot) + '</b>');
    const rows = (res.items || []).map(it => {
      const badges = [];
      if (!it.has_cost) badges.push('<span style="display:inline-block;padding:1px 7px;border-radius:9px;background:#f6e6f0;color:#A12C7B;font-size:11px;font-weight:600;">нет с/с</span>');
      if (!it.has_price) badges.push('<span style="display:inline-block;padding:1px 7px;border-radius:9px;background:#fdeede;color:#964219;font-size:11px;font-weight:600;">нет цены</span>');
      return '<tr>'
        + '<td style="padding:5px 10px;border-bottom:1px solid #eee;font-family:monospace;font-size:12.5px;">' + U.esc(it.article) + '</td>'
        + '<td style="padding:5px 10px;border-bottom:1px solid #eee;text-align:right;font-variant-numeric:tabular-nums;">' + U.fmtNum(it.qty) + '</td>'
        + '<td style="padding:5px 10px;border-bottom:1px solid #eee;">' + badges.join(' ') + '</td>'
        + '</tr>';
    }).join('');
    body.innerHTML =
      '<div class="up-warn" style="background:#f6e6f0;border-color:#e2b8d3;color:#7a1f5b;">'
      + '⚠ На ' + U.esc(dateStr) + ': <b>' + U.fmtNum(prob) + '</b> из ' + U.fmtNum(totalArts)
      + ' артикулов на остатках с неполными данными (' + parts.join(', ') + ').'
      + ' Стоимость таких товаров в расчёте занижается (=0).'
      + '</div>'
      + '<div style="display:flex;gap:10px;align-items:center;margin:10px 0 6px;">'
      + '<button class="btn btn-sm" id="dq-toggle" style="font-size:12.5px;">Показать список (' + U.fmtNum(prob) + ')</button>'
      + '<button class="btn btn-sm" id="dq-xlsx" style="font-size:12.5px;">⬇ Выгрузить Excel</button>'
      + '</div>'
      + '<div id="dq-list" style="display:none;max-height:340px;overflow:auto;border:1px solid #eee;border-radius:8px;">'
      + '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
      + '<thead><tr style="position:sticky;top:0;background:#faf9f6;">'
      + '<th style="padding:6px 10px;text-align:left;border-bottom:1px solid #ddd;">Артикул</th>'
      + '<th style="padding:6px 10px;text-align:right;border-bottom:1px solid #ddd;">Остаток, шт</th>'
      + '<th style="padding:6px 10px;text-align:left;border-bottom:1px solid #ddd;">Чего не хватает</th>'
      + '</tr></thead><tbody>' + rows + '</tbody></table>'
      + '</div>';
    const tg = document.getElementById('dq-toggle');
    const lst = document.getElementById('dq-list');
    if (tg && lst) tg.addEventListener('click', () => {
      const open = lst.style.display !== 'none';
      lst.style.display = open ? 'none' : 'block';
      tg.textContent = (open ? 'Показать' : 'Скрыть') + ' список (' + U.fmtNum(prob) + ')';
    });
    const xb = document.getElementById('dq-xlsx');
    if (xb) xb.addEventListener('click', () => dqExportXlsx());
  }

  // Выгрузка списка проблемных артикулов в Excel.
  // Паттерн проекта: HTML-таблица → Blob (application/vnd.ms-excel) → .xls.
  function dqExportXlsx() {
    if (!_dqData || !(_dqData.items || []).length) { App.toast('Нечего выгружать', 'err'); return; }
    let body = '';
    (_dqData.items || []).forEach(it => {
      body += '<tr>'
        + '<td>' + U.esc(it.article) + '</td>'
        + '<td>' + (it.qty == null ? '' : it.qty) + '</td>'
        + '<td>' + (it.has_cost ? 'есть' : 'нет') + '</td>'
        + '<td>' + (it.has_price ? 'есть' : 'нет') + '</td>'
        + '</tr>';
    });
    const head = '<tr><th>Артикул</th><th>Остаток, шт</th><th>Себестоимость</th><th>Средняя цена</th></tr>';
    const html = '<html><head><meta charset="utf-8"></head><body><table border="1">' + head + body + '</table></body></html>';
    const blob = new Blob(['\ufeff' + html], { type: 'application/vnd.ms-excel' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'достоверность_остатки_' + (_dqData.date || '') + '.xls';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function whSubtabs(host) {
    if (!host) return;
    const tabs = [['wb', 'Wildberries', 'wb'], ['ozon', 'OZON', 'ozon']];
    // Раздел «Склады» открыт ВСЕМ авторизованным (user+admin), включая
    // «Загрузку данных» (эндпоинты загрузки теперь get_current_user).
    // Нейтральный (серый) стиль 'cross' + прижать вправо — как «Загрузка данных» в РНП.
    tabs.push(['upload', 'Загрузка данных', 'cross', true]);
    tabs.forEach(([id, label, c, pushRight]) => {
      const b = document.createElement('button');
      b.className = 'subtab ' + c + (whState.mp === id ? ' active' : '') + (pushRight ? ' subtab-right' : '');
      b.textContent = label;
      b.addEventListener('click', () => {
        if (id === whState.mp) return;
        whState.mp = id;
        App.renderView();
      });
      host.appendChild(b);
    });
  }

  async function warehouses(root, ctl, state) {
    ctl.innerHTML = '';
    // Класс карточки задаёт бренд-акцент (синий Ozon / фиолетовый WB /
    // нейтральный серый для «Загрузка данных») — как в «РНП заказы».
    const mpCls = whState.mp === 'wb' ? 'mp-wb' : (whState.mp === 'ozon' ? 'mp-ozon' : 'mp-cross');

    if (whState.mp === 'upload') {
      // Подтаб «Загрузка данных»: вложенные вкладки Отчёты WB / Отчёты Ozon
      // (как в РНП заказы → Загрузка данных).
      const ut = whState.uploadTab || 'wb';
      const show = (t) => ut === t ? '' : 'display:none;';
      root.innerHTML = `<div class="card rnp-card ${mpCls}">
        <div class="rnp-toolbar"><div class="subtabs" id="wh-subtabs"></div></div>
        <div class="subtabs" id="wh-up-subtabs" style="margin:4px 0 16px;"></div>

        <div class="up-pane" data-whpane="wb" style="${show('wb')}">
          <div class="card">
            <h3>Wildberries — Остатки на складах</h3>
            <div class="stock-date-row" style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap;">
              <label for="date-wbstock" style="font-size:13px;color:#28251d;font-weight:500;">Дата остатков:</label>
              <input type="date" id="date-wbstock" style="padding:6px 9px;border:1px solid #d4d1ca;border-radius:6px;font-size:13px;background:#fff;color:#28251d;">
              <span class="muted" style="font-size:12px;">— на какую дату записываются остатки</span>
            </div>
            <div class="upload-zone" id="uz-wbstock">
              <div style="font-size:28px;">⬆️</div>
              <div>Перетащите Excel сюда или <a href="#" id="pick-wbstock">выберите файл</a></div>
              <div class="muted" style="font-size:12px;margin-top:6px;">Отчёт остатков WB, лист «Sheet1», .xlsx · остатки по складам + «в пути» + объём</div>
              <input type="file" id="file-wbstock" accept=".xlsx,.xls" class="hidden">
            </div>
            <div class="muted fact-date-hint">Где взять: Личный кабинет WB → Аналитика → Отчёты → Остатки на складах → скачать (.xlsx).</div>
            <div class="muted fact-date-hint">Даты в файле нет — укажите её выше перед загрузкой. Повторная загрузка за ту же дату полностью перезаписывает данные дня.</div>
            <div class="muted fact-date-hint">Сопоставление по «Артикул продавца»; пишется только по товарам из справочника. Набор/порядок колонок складов может меняться — склады определяются по заголовкам, новые добавляются в справочник автоматически.</div>
          </div>

          <!-- Баннер достоверности стоимостной оценки остатков -->
          <div class="card" id="wh-dq-card">
            <div class="up-hist-head"><h3 style="margin:0;">Достоверность стоимостной оценки</h3></div>
            <div id="wh-dq-body"><div class="loader"><span class="spinner"></span></div></div>
          </div>

          <div class="card">
            <div class="up-hist-head"><h3 style="margin:0;">Журнал загрузок — остатки Wildberries</h3></div>
            <div id="uphist-wbstock"><div class="loader"><span class="spinner"></span></div></div>
          </div>
        </div>

        <div class="up-pane" data-whpane="ozon" style="${show('ozon')}">
          <div class="card">
            <h3>Ozon — Остатки на складах</h3>
            <div class="stock-date-row" style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap;">
              <label for="date-whozstock" style="font-size:13px;color:#28251d;font-weight:500;">Дата остатков:</label>
              <input type="date" id="date-whozstock" style="padding:6px 9px;border:1px solid #d4d1ca;border-radius:6px;font-size:13px;background:#fff;color:#28251d;">
              <span class="muted" style="font-size:12px;">— на какую дату записываются остатки</span>
            </div>
            <div class="upload-zone" id="uz-whozstock">
              <div style="font-size:28px;">⬆️</div>
              <div>Перетащите Excel сюда или <a href="#" id="pick-whozstock">выберите файл</a></div>
              <div class="muted" style="font-size:12px;margin-top:6px;">Отчёт остатков Ozon по складам, лист «Товар-склад», .xlsx</div>
              <input type="file" id="file-whozstock" accept=".xlsx,.xls" class="hidden">
            </div>
            <div class="muted fact-date-hint">Где взять: Личный кабинет Ozon → отчёт остатков по складам, лист «Товар-склад» — скачать (.xlsx).</div>
            <div class="muted fact-date-hint">Даты в файле нет — укажите её выше перед загрузкой. Повторная загрузка за ту же дату полностью перезаписывает данные дня.</div>
            <div class="muted fact-date-hint">Склады, кластеры и артикулы, которых ещё нет в справочнике, заводятся автоматически.</div>
          </div>

          <!-- Баннер достоверности стоимостной оценки остатков Ozon -->
          <div class="card" id="oz-dq-card">
            <div class="up-hist-head"><h3 style="margin:0;">Достоверность стоимостной оценки</h3></div>
            <div id="oz-dq-body"><div class="loader"><span class="spinner"></span></div></div>
          </div>

          <div class="card">
            <div class="up-hist-head"><h3 style="margin:0;">Журнал загрузок — остатки Ozon</h3></div>
            <div id="uphist-ozstock"><div class="loader"><span class="spinner"></span></div></div>
          </div>
        </div>

        <div class="up-pane" data-whpane="gen" style="${show('gen')}">
          <div class="card">
            <h3>Себестоимость товаров</h3>
            <div class="stock-date-row" style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap;">
              <label for="date-cost" style="font-size:13px;color:#28251d;font-weight:500;">Дата действия цен:</label>
              <input type="date" id="date-cost" style="padding:6px 9px;border:1px solid #d4d1ca;border-radius:6px;font-size:13px;background:#fff;color:#28251d;">
              <span class="muted" style="font-size:12px;">— с этой даты действует себестоимость (до следующей загрузки)</span>
            </div>
            <div class="upload-zone" id="uz-cost">
              <div style="font-size:28px;">⬆️</div>
              <div>Перетащите Excel сюда или <a href="#" id="pick-cost">выберите файл</a></div>
              <div class="muted" style="font-size:12px;margin-top:6px;">Выгрузка с с/с, лист «TDSheet», .xlsx · колонки «Артикул» + «с/с расчетная»</div>
              <input type="file" id="file-cost" accept=".xlsx,.xls" class="hidden">
            </div>
            <div class="muted fact-date-hint">Укажите дату выше — себестоимость будет действовать с неё до даты следующей загрузки. Стоимость остатков за каждый день считается по срезу с/с, актуальному на дату остатка.</div>
            <div class="muted fact-date-hint">Повторная загрузка за ту же дату перезаписывает значения этого среза. Сопоставление по «Артикул»; с/с записывается только по товарам из справочника системы — прочие строки файла пропускаются.</div>
          </div>
          <div class="card">
            <div class="up-hist-head"><h3 style="margin:0;">Журнал загрузок — себестоимость</h3></div>
            <div id="uphist-cost"><div class="loader"><span class="spinner"></span></div></div>
          </div>
        </div>
      </div>`;
      whSubtabs(root.querySelector('#wh-subtabs'));
      // вложенные вкладки Отчёты WB / Отчёты Ozon
      const upHost = root.querySelector('#wh-up-subtabs');
      [['wb', 'Отчёты Wildberries', 'wb'], ['ozon', 'Отчёты Ozon', 'ozon'], ['gen', 'Отчёты общие', 'cross']].forEach(([id, label, c]) => {
        const b = document.createElement('button');
        b.className = 'subtab ' + c + (ut === id ? ' active' : '');
        b.textContent = label;
        b.addEventListener('click', () => {
          if (whState.uploadTab === id) return;
          whState.uploadTab = id;
          root.querySelectorAll('.up-pane[data-whpane]').forEach(p => {
            p.style.display = (p.getAttribute('data-whpane') === id) ? '' : 'none';
          });
          upHost.querySelectorAll('.subtab').forEach(bb => bb.classList.remove('active'));
          b.classList.add('active');
        });
        upHost.appendChild(b);
      });
      setupWbStockZone(root);
      setupCostZone(root);
      setupOzonWhStockZone(root);
      await loadWhUploadHistory();
      return;
    }

    // ===== Ozon: визуализация остатков (зеркало WB ниже + доп. уровень «Кластер») =====
    if (whState.mp === 'ozon') {
      await warehousesOzon(root, mpCls);
      return;
    }

    // ===== Wildberries: подтабы «Общие данные» / «По складам» / «Справочник» =====
    await warehousesWb(root, mpCls);
  }

  // --- Wildberries: контейнер с подтабами визуализации остатков ---
  async function warehousesWb(root, mpCls) {
    const sub = whState.wbSub || 'bywh';   // 'general' | 'bywh' | 'catalog'
    root.innerHTML = `<div class="card rnp-card ${mpCls}">
      <div class="rnp-toolbar"><div class="subtabs" id="wh-subtabs"></div></div>
      <div class="subtabs" id="wh-wb-subtabs" style="margin:4px 0 16px;"></div>
      <div id="wh-wb-body"><div class="loader"><span class="spinner"></span></div></div>
    </div>`;
    whSubtabs(root.querySelector('#wh-subtabs'));

    // Вложенные подтабы. «Справочник» — только владельцу (как весь раздел).
    const tabs = [['general', 'Общие данные', 'wb'], ['bywh', 'По складам', 'wb'], ['catalog', 'Справочник складов', 'wb']];
    const host = root.querySelector('#wh-wb-subtabs');
    tabs.forEach(([id, label, c]) => {
      const b = document.createElement('button');
      b.className = 'subtab ' + c + (sub === id ? ' active' : '');
      b.textContent = label;
      b.addEventListener('click', () => {
        if (whState.wbSub === id) return;
        whState.wbSub = id;
        warehousesWb(root, mpCls);
      });
      host.appendChild(b);
    });

    const body = root.querySelector('#wh-wb-body');
    if (sub === 'bywh') return whRenderByWh(body);
    if (sub === 'catalog') return whRenderCatalog(body);
    // «Общие данные» (В4) — следующий этап.
    body.innerHTML = `<div class="empty" style="padding:56px 40px; text-align:center; color:#7a7974;">
      Таблица общих остатков по группам и месяцам (шт / себестоимость / в ценах продажи) — в разработке, следующим шагом.</div>`;
  }

  // ============ Подтаб «По складам»: KPI-плашки + тепловая карта ============
  // Период — компонент как в «РНП заказы» (поле + календарь + пресеты, по дням).
  // Показатель: себестоимость (по умолчанию) ↔ цены продажи (галочка).
  async function whRenderByWh(body) {
    body.innerHTML = '<div class="loader"><span class="spinner"></span></div>';
    let d;
    try { d = await API.whByWh({ date_from: whState.whFrom, date_to: whState.whTo, days: 400 }); }
    catch (e) { body.innerHTML = `<div class="empty">Ошибка: ${U.esc(e.message)}</div>`; return; }
    whState.whData = d;  // для базовой даты пресетов
    if (!d.dates || !d.dates.length) {
      body.innerHTML = `
        <div class="wh-byrow-toolbar">
          <div class="rnp-card-title" style="margin:0;">Стоимость остатков по складам</div>
          <div class="wh-toolbar-right">${whPeriodFieldHtml()}</div>
        </div>
        <div class="empty" style="padding:48px;text-align:center;color:#7a7974;">Нет данных об остатках за выбранный период. Загрузите отчёт на подтабе «Загрузка данных» или расширьте период.</div>`;
      bindWhPeriod(body, () => whRenderByWh(body));
      return;
    }
    const dates = d.dates, last = d.last_date, prev = dates.length >= 2 ? dates[dates.length - 2] : null;
    // Ключ показателя в ячейках/итогах в зависимости от режима цены.
    const isSale = whState.whPriceMode === 'sale';
    const rubKey = isSale ? 'rub_sale' : 'rub_cost';
    const priceLbl = isSale ? 'в ценах продажи' : 'в ценах себестоимости';
    const rubOf = (o) => (o && o[rubKey] != null) ? o[rubKey] : null;

    const tot = d.totals[last] || {};
    const totPrev = prev ? (d.totals[prev] || {}) : null;
    const nMissing = (d.missing || []).length;

    // --- KPI-плашки: ДВЕ стоимости (себестоимость + в ценах продажи), всегда обе ---
    const costNow = (tot.rub_cost != null ? tot.rub_cost : 0);
    const costPrev = totPrev ? (totPrev.rub_cost != null ? totPrev.rub_cost : 0) : null;
    const dCost = costPrev != null ? (costNow - costPrev) : null;
    const saleNow = (tot.rub_sale != null ? tot.rub_sale : 0);
    const salePrev = totPrev ? (totPrev.rub_sale != null ? totPrev.rub_sale : 0) : null;
    const dSale = salePrev != null ? (saleNow - salePrev) : null;
    // Инцидент для ячейки по ИНТЕРВАЛАМ: dt попадает в любой интервал
    // [start .. end] (end=null — открыт, действует по текущий день включительно).
    const cellIncident = (w, dt) => {
      const ivs = w.incidents || [];
      for (const iv of ivs) {
        if (dt >= iv.start && (iv.end == null || dt <= iv.end)) return iv;
      }
      return null;
    };
    const isIncidentCell = (w, dt) => !!cellIncident(w, dt);
    // Активен ли инцидент на конкретную дату (по умолчанию — последнюю).
    const whHasIncidentOn = (w, dt) => isIncidentCell(w, dt);
    // Сумма «из них — инцидент» НА ПОСЛЕДНЮЮ дату (отдельно себестоимость и цены продажи).
    let incCost = 0, incSale = 0, incCount = 0;
    d.warehouses.forEach(w => {
      if (whHasIncidentOn(w, last)) {
        const cl = (d.cells[w.id] || {})[last];
        if (cl) { incCost += (cl.rub_cost || 0); incSale += (cl.rub_sale || 0); }
        incCount++;  // склад с активным инцидентом считается даже без остатка
      }
    });
    // Подстрока KPI «из них X — инцидент» (показываем только если есть такие склады).
    const incSub = (val) => incCount > 0
      ? `<div class="wh-kpi-inc" title="Остаток на складах с активным инцидентом (складов: ${incCount})">из них <b>${U.fmtMoneyShort(val)}</b> — инцидент</div>`
      : '';
    const deltaSub = (dv) => dv == null ? '<span class="wh-kpi-sub muted">на ' + whFmtDate(last) + '</span>'
      : `<span class="wh-kpi-sub ${dv < 0 ? 'neg' : 'pos'}">${dv < 0 ? '▼' : '▲'} ${U.fmtMoneyShort(Math.abs(dv))} за сутки</span>`;
    // KPI «Активных складов»: всего − склады с активным инцидентом на last.
    const whTotal = d.warehouses.length;
    const whActive = whTotal - incCount;
    const inclWord = (n) => {
      const n10 = n % 10, n100 = n % 100;
      if (n10 === 1 && n100 !== 11) return 'складе';
      if (n10 >= 2 && n10 <= 4 && (n100 < 10 || n100 >= 20)) return 'складах';
      return 'складах';
    };
    let actKpi;
    if (incCount > 0) {
      actKpi = `<div class="wh-kpi alert"><div class="wh-kpi-lbl">Активных складов</div><div class="wh-kpi-val neg">${whActive} / ${whTotal}</div><div class="wh-kpi-sub neg">инцидент на ${incCount} ${inclWord(incCount)}</div></div>`;
    } else if (nMissing > 0) {
      actKpi = `<div class="wh-kpi alert"><div class="wh-kpi-lbl">Активных складов</div><div class="wh-kpi-val neg">${tot.wh_count || 0} / ${whTotal}</div><div class="wh-kpi-sub neg">⚠ пропало за сутки: ${nMissing}</div></div>`;
    } else {
      actKpi = `<div class="wh-kpi"><div class="wh-kpi-lbl">Активных складов</div><div class="wh-kpi-val">${whActive} / ${whTotal}</div><div class="wh-kpi-sub pos">все на месте</div></div>`;
    }
    const missKpi = actKpi;
    const kpiRow = `<div class="wh-kpi-row">
      <div class="wh-kpi"><div class="wh-kpi-lbl">Стоимость остатков · себестоимость</div><div class="wh-kpi-val">${U.fmtMoneyShort(costNow)}</div>${deltaSub(dCost)}${incSub(incCost)}</div>
      <div class="wh-kpi"><div class="wh-kpi-lbl">Стоимость остатков · в ценах продажи</div><div class="wh-kpi-val">${U.fmtMoneyShort(saleNow)}</div>${deltaSub(dSale)}${incSub(incSale)}</div>
      <div class="wh-kpi"><div class="wh-kpi-lbl">Количество</div><div class="wh-kpi-val">${U.fmtNum(tot.qty || 0)}</div><div class="wh-kpi-sub muted">шт на ${whFmtDate(last)}</div></div>
      ${missKpi}
    </div>`;

    // --- Матрица склад × день (без тепловой заливки) ---
    // Вес шрифта = загруженность склада относительно ОСТАЛЬНЫХ складов В ЭТОТ ДЕНЬ.
    // Считаем максимум ПО КАЖДОМУ СТОЛБЦУ-ДНЮ (не по всей матрице), инцидент исключаем.
    const colMax = {};
    dates.forEach(dt => {
      let mx = 0;
      d.warehouses.forEach(w => { const c = d.cells[w.id] || {}; if (isIncidentCell(w, dt)) return; const v = rubOf(c[dt]) || 0; if (v > mx) mx = v; });
      colMax[dt] = mx;
    });
    // Класс веса шрифта по доле от максимума столбца-дня.
    const whWeightCls = (v, dt, inInc) => {
      // Жирность ТОЛЬКО по объёму (доля от максимума столбца-дня среди ВСЕХ складов),
      // независимо от групп И от статуса инцидента. Инцидент отмечается лишь рамкой/цветом,
      // а не жирностью (иначе мелкий склад в рамке выглядел бы «крупнее» большого без рамки).
      if (v == null) return 'wh-w-mid';
      const mx = colMax[dt] || 0;
      const frac = mx ? v / mx : 0;
      if (frac >= 0.55) return 'wh-w-hi';
      if (frac >= 0.18) return 'wh-w-mid';
      return 'wh-w-lo';
    };
    // Короткий формат БЕЗ знака ₽ (только для ячеек таблицы — снижаем визуальный шум).
    const whShortNoRub = (v) => {
      if (v == null || isNaN(v)) return '—';
      const a = Math.abs(v);
      if (a >= 1e9) return (v / 1e9).toFixed(2).replace('.', ',') + ' млрд';
      if (a >= 1e6) return (v / 1e6).toFixed(2).replace('.', ',') + ' млн';
      if (a >= 1e3) return Math.round(v / 1e3) + ' тыс';
      return U.fmtNum(v);
    };

    // Заголовки-даты: вторая sticky-строка шапки — «Итого за день» (вынесена наверх).
    const thDates = dates.map(dt => `<th class="wh-hm-d">${whFmtDate(dt)}</th>`).join('');

    _whReportDates = (d.dates || []).slice();  // даты отчёта (для мини-меню инцидентов)
    const INC_COL = '#A12C7B';  // цвет инцидента (историч.)
    // Рендер одной строки-склада. Ячейки-даты кликабельны (двойной клик → меню).
    const renderWhRow = (w, grpId, collapsed) => {
      const c = d.cells[w.id] || {};
      const cells = dates.map((dt, di) => {
        const v = rubOf(c[dt]);
        // Инцидент по интервалам.
        const iv = cellIncident(w, dt);
        const inInc = !!iv;
        // Первый/последний день интервала в пределах видимых дат.
        const prevDt = di > 0 ? dates[di - 1] : null;
        const nextDt = di < dates.length - 1 ? dates[di + 1] : null;
        const isIncStart = inInc && (!prevDt || !cellIncident(w, prevDt) || iv.start === dt);
        const isIncEnd = inInc && (!nextDt || !cellIncident(w, nextDt) || (iv.end != null && iv.end === dt));
        // data-атрибуты для двойного клика (открыть/закрыть инцидент).
        const dataAttr = ` data-wh-cell="1" data-wh-id="${w.id}" data-wh-name="${U.esc(w.name)}" data-dt="${dt}" data-inc="${inInc ? '1' : '0'}"`;
        if (v == null) {
          // Нет данных, но ячейка всё равно кликабельна (дата есть в отчёте).
          // В инциденте — те же классы, что и у ячеек с данными (единый период с правильными торцами).
          let ncls = 'wh-hm-cell wh-hm-clk', nextra = '';
          if (inInc) {
            ncls += ' wh-hm-instatus';
            nextra += `--wh-st-col:${INC_COL};`;
            if (isIncStart) ncls += ' wh-hm-ststart';
            if (isIncEnd) ncls += ' wh-hm-stend';
          }
          const nTip = inInc ? ` · инцидент (с ${whFmtDate(iv.start)}${iv.end ? ' по ' + whFmtDate(iv.end) : ', открыт'})` : '';
          return `<td class="${ncls}" style="background:#ffffff;${nextra}"${dataAttr} title="нет данных${nTip} · двойной клик — управление инцидентом"><span class="muted" style="font-size:10px;">—</span></td>`;
        }
        if (v === 0) return `<td class="wh-hm-cell wh-hm-zero wh-hm-clk"${dataAttr} title="склад пропал / нулевой остаток · двойной клик — инцидент"><span style="font-size:10px;">скрыт</span></td>`;
        // Фон всегда белый (тепловой заливки больше нет). Вес передаётся толщиной/цветом шрифта по столбцу-дню.
        let cls = 'wh-hm-cell wh-hm-clk ' + whWeightCls(v, dt, inInc), extra = '';
        if (inInc) {
          cls += ' wh-hm-instatus';
          extra += `--wh-st-col:${INC_COL};`;
          if (isIncStart) cls += ' wh-hm-ststart';  // жирный левый торец = начало
          if (isIncEnd) cls += ' wh-hm-stend';      // жирный правый торец = конец
        }
        const incTip = inInc ? ` · инцидент (с ${whFmtDate(iv.start)}${iv.end ? ' по ' + whFmtDate(iv.end) : ', открыт'})` : '';
        return `<td class="${cls}" style="background:#ffffff;${extra}"${dataAttr} title="${U.esc(w.name)} · ${whFmtDate(dt)}: ${U.fmtMoney(v)} (${priceLbl})${incTip} · двойной клик — управление инцидентом">${whShortNoRub(v)}</td>`;
      }).join('');
      // спарклайн + Δ день
      const seq = dates.map(dt => rubOf(c[dt]) || 0);
      const spark = whSparkline(seq);
      const d0 = seq.length >= 2 ? seq[seq.length - 2] : 0, d1 = seq[seq.length - 1];
      const delta = d1 - d0;
      let dTxt, dCls;
      if (d1 === 0 && d0 > 0) { dTxt = '▼ пропал'; dCls = 'neg'; }
      else if (delta > 0) { dTxt = '▲ ' + whShortNoRub(delta); dCls = 'pos'; }
      else if (delta < 0) { dTxt = '▼ ' + whShortNoRub(Math.abs(delta)); dCls = 'neg'; }
      else { dTxt = '—'; dCls = 'muted'; }
      const childAttr = grpId ? ` data-wh-grp-child="${U.esc(grpId)}"` : '';
      const hiddenCls = collapsed ? ' wh-hm-hidden' : '';
      return `<tr class="wh-hm-wrow${hiddenCls}"${childAttr}>`
        + `<td class="wh-hm-name" title="${U.esc(w.name)}">${U.esc(w.name)}</td>`
        + `${cells}<td class="wh-hm-spark">${spark}</td><td class="wh-hm-delta ${dCls}">${dTxt}</td></tr>`;
    };

    // --- ГРУППИРОВКА ПО ФЕДЕРАЛЬНЫМ ОКРУГАМ ---
    // Порядок групп — по удалению от ЦФО; «Не распределённые» — последней.
    // Внутри группы склады уже отсортированы по стоимости (backend), сохраним порядок.
    const groupsMap = new Map();  // fd (или null) -> [w, ...]
    d.warehouses.forEach(w => {
      const key = (w.federal_district && WH_FD_ORDER[w.federal_district] != null) ? w.federal_district : null;
      if (!groupsMap.has(key)) groupsMap.set(key, []);
      groupsMap.get(key).push(w);
    });
    const groupKeys = [...groupsMap.keys()].sort((a, b) => whFdOrder(a) - whFdOrder(b));
    // Подытог группы по столбцам-датам (в текущем показателе).
    const groupTotalsByDate = (list, dt) => list.reduce((s, w) => s + (rubOf((d.cells[w.id] || {})[dt]) || 0), 0);
    let rows = '';
    groupKeys.forEach(key => {
      const list = groupsMap.get(key);
      const gName = key || 'Не распределённые';
      // Строка-заголовок группы: название + подытоги по дням.
      const gCells = dates.map(dt => {
        const v = groupTotalsByDate(list, dt);
        return `<td class="wh-hm-gcell" title="${U.esc(gName)} · ${whFmtDate(dt)}: ${U.fmtMoney(v)} (${priceLbl})">${v ? whShortNoRub(v) : '—'}</td>`;
      }).join('');
      const gSeq = dates.map(dt => groupTotalsByDate(list, dt));
      const gSpark = whSparkline(gSeq);
      const g0 = gSeq.length >= 2 ? gSeq[gSeq.length - 2] : 0, g1 = gSeq[gSeq.length - 1];
      const gd = g1 - g0;
      let gTxt = '—', gCls = 'muted';
      if (gd > 0) { gTxt = '▲ ' + whShortNoRub(gd); gCls = 'pos'; }
      else if (gd < 0) { gTxt = '▼ ' + whShortNoRub(Math.abs(gd)); gCls = 'neg'; }
      const undCls = key ? '' : ' wh-hm-group-und';
      const grpId = key || '__none__';
      const collapsed = !!whState.whCollapsed[grpId];
      const tw = collapsed ? '▸' : '▾';  // как в «РНП заказы»
      rows += `<tr class="wh-hm-group${undCls}${collapsed ? ' is-collapsed' : ''}" data-wh-grp="${U.esc(grpId)}">`
        + `<td class="wh-hm-name l" title="${U.esc(gName)} — клик, чтобы свернуть/развернуть"><span class="wh-hm-tw" aria-hidden="true">${tw}</span>${U.esc(gName)} <span class="wh-hm-group-cnt">${list.length}</span></td>`
        + `${gCells}<td class="wh-hm-spark">${gSpark}</td><td class="wh-hm-delta ${gCls}">${gTxt}</td></tr>`;
      // Строки складов группы (скрыты, если группа свёрнута).
      list.forEach(w => { rows += renderWhRow(w, grpId, collapsed); });
    });

    // --- Итоговая строка «Итого за день» (сумма по столбцам-датам) — В ШАПКЕ ---
    const totalCells = dates.map(dt => {
      const v = rubOf(d.totals[dt]);
      if (v == null) return `<th class="wh-hm-cell"><span class="muted" style="font-size:10px;">—</span></th>`;
      return `<th class="wh-hm-cell" title="Итого ${whFmtDate(dt)}: ${U.fmtMoney(v)} (${priceLbl})">${whShortNoRub(v)}</th>`;
    }).join('');
    const totalSeq = dates.map(dt => rubOf(d.totals[dt]) || 0);
    const totalSpark = whSparkline(totalSeq);
    const t0 = totalSeq.length >= 2 ? totalSeq[totalSeq.length - 2] : 0, t1 = totalSeq[totalSeq.length - 1];
    const tDelta = t1 - t0;
    let tTxt = '—', tCls = 'muted';
    if (tDelta > 0) { tTxt = '▲ ' + whShortNoRub(tDelta); tCls = 'pos'; }
    else if (tDelta < 0) { tTxt = '▼ ' + whShortNoRub(Math.abs(tDelta)); tCls = 'neg'; }
    // Строка итогов в <thead> — sticky вторым рядом (под заголовками-датами).
    const totalHeadRow = `<tr class="wh-hm-total-head">`
      + `<th class="wh-hm-name l">Итого за день</th>`
      + `${totalCells}<th class="wh-hm-spark">${totalSpark}</th><th class="wh-hm-delta ${tCls}">${tTxt}</th></tr>`;

    const onCls = isSale ? ' on' : '';
    body.innerHTML = `
      <div class="wh-byrow-toolbar">
        <div class="rnp-card-title" style="margin:0;">Стоимость остатков по складам · ${priceLbl}</div>
        <div class="wh-toolbar-right">
          <button class="btn-sm" id="wh-expand-all" type="button" title="Развернуть все округа">Развернуть всё</button>
          <button class="btn-sm" id="wh-collapse-all" type="button" title="Свернуть все округа">Свернуть всё</button>
          <label class="wh-price-toggle${onCls}" id="wh-price-toggle" title="Снято — стоимость в ценах себестоимости; отмечено — в средних ценах продажи">
            <input type="checkbox" id="wh-price-cb"${isSale ? ' checked' : ''}> в ценах продажи
          </label>
          ${whPeriodFieldHtml()}
        </div>
      </div>
      ${kpiRow}
      ${nMissing > 0 ? `<div class="wh-alert-bar">⚠ Пропали из последней выгрузки: <b>${(d.missing || []).map(m => U.esc(m.name)).join(', ')}</b>. Проверьте — склад скрыт маркетплейсом или остатки утрачены.</div>` : ''}
      <div class="wh-hm-wrap">
        <table class="wh-hm wh-hm-status">
          <thead>
            <tr><th class="wh-hm-name l">Склад</th>${thDates}<th class="wh-hm-spark">Тренд</th><th class="wh-hm-delta">Δ день</th></tr>
            ${totalHeadRow}
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <div class="wh-legend">▪ Ячейка — стоимость остатка склада (${priceLbl}) · жирность значения = загруженность склада относительно других складов в этот день · «скрыт» = склад пропал из выгрузки (тревога) · <b style="color:#A12C7B;">Инцидент</b> — белые ячейки с рамкой; дважды кликните по ячейке-дате, чтобы открыть или закрыть инцидент с этой даты (историчность) · строка «Итого за день» (в шапке) — сумма по всем складам.</div>`;

    // Галочка режима цены
    const cb = body.querySelector('#wh-price-cb');
    if (cb) cb.addEventListener('change', () => {
      whState.whPriceMode = cb.checked ? 'sale' : 'cost';
      whRenderByWh(body);
    });
    // --- Сворачивание округов (как «РНП заказы»): клик по строке-группе / треугольнику ---
    const whTable = body.querySelector('table.wh-hm');
    const applyGrp = (grpId) => {
      const collapsed = !!whState.whCollapsed[grpId];
      const gr = whTable.querySelector(`tr.wh-hm-group[data-wh-grp="${CSS.escape(grpId)}"]`);
      if (gr) {
        gr.classList.toggle('is-collapsed', collapsed);
        const tw = gr.querySelector('.wh-hm-tw');
        if (tw) tw.textContent = collapsed ? '▸' : '▾';
      }
      whTable.querySelectorAll(`tr.wh-hm-wrow[data-wh-grp-child="${CSS.escape(grpId)}"]`)
        .forEach(tr => tr.classList.toggle('wh-hm-hidden', collapsed));
    };
    if (whTable) {
      whTable.addEventListener('click', (e) => {
        const gr = e.target.closest('tr.wh-hm-group');
        if (!gr || !whTable.contains(gr)) return;
        // Клик именно по имени/треугольнику группы (не по ячейкам-данным).
        if (!e.target.closest('.wh-hm-name')) return;
        const grpId = gr.getAttribute('data-wh-grp');
        if (!grpId) return;
        whState.whCollapsed[grpId] = !whState.whCollapsed[grpId];
        applyGrp(grpId);
      });
    }
    // Кнопки «Развернуть всё» / «Свернуть всё»
    const allGrpIds = () => [...whTable.querySelectorAll('tr.wh-hm-group[data-wh-grp]')]
      .map(tr => tr.getAttribute('data-wh-grp'));
    const btnExp = body.querySelector('#wh-expand-all');
    if (btnExp) btnExp.addEventListener('click', () => {
      allGrpIds().forEach(id => { whState.whCollapsed[id] = false; applyGrp(id); });
    });
    const btnCol = body.querySelector('#wh-collapse-all');
    if (btnCol) btnCol.addEventListener('click', () => {
      allGrpIds().forEach(id => { whState.whCollapsed[id] = true; applyGrp(id); });
    });
    // Двойной клик по ячейке-дате — мини-меню управления инцидентом.
    bindWhIncidentCells(body, () => whRenderByWh(body));
    // Компонент периода (как в РНП)
    bindWhPeriod(body, () => whRenderByWh(body));
  }

  // --- Поле периода складов (как в РНП): HTML-разметка ---
  function whPeriodFieldHtml() {
    return `<div class="rnp-period" id="wh-period">
      <button class="rnp-period-field" id="wh-period-field" type="button" title="Выбрать период остатков">
        <svg class="rnp-period-ico" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/></svg>
        <span class="rnp-period-text" id="wh-period-text">${whPeriodLabel()}</span>
      </button>
      <div class="rnp-cal" id="wh-cal" hidden></div>
    </div>`;
  }
  // Подпись поля периода складов.
  function whPeriodLabel() {
    const f = whState.whFrom, t = whState.whTo;
    if (!f && !t) return 'Весь период';
    if (f && t) return f === t ? fmtRu(f) : `${fmtRu(f)} — ${fmtRu(t)}`;
    return fmtRu(f || t);
  }
  // Базовая дата для пресетов — последняя дата с данными, иначе сегодня.
  function whPeriodBaseDate() {
    const dd = whState.whData && whState.whData.dates;
    if (dd && dd.length) { const d = parseISO(dd[dd.length - 1]); if (d) return d; }
    return new Date();
  }
  function whPresetRange(kind) {
    const base = whPeriodBaseDate();
    const y = base.getFullYear(), m = base.getMonth();
    let from, to;
    if (kind === 'month') { from = new Date(y, m, 1); to = new Date(y, m + 1, 0); }
    else if (kind === 'quarter') { const q = Math.floor(m / 3); from = new Date(y, q * 3, 1); to = new Date(y, q * 3 + 3, 0); }
    else { from = new Date(y, 0, 1); to = new Date(y, 11, 31); }
    return { from: isoDate(from), to: isoDate(to) };
  }
  // Привязка поля периода + поповера-календаря (2 месяца, диапазон, пресеты).
  function bindWhPeriod(wrap, onApply) {
    const periodWrap = wrap.querySelector('#wh-period');
    const periodFieldEl = wrap.querySelector('#wh-period-field');
    const calEl = wrap.querySelector('#wh-cal');
    if (!periodFieldEl || !calEl) return;
    const MONTHS_RU = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
    const DOW_RU = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
    const cal = { from: '', to: '', view: null, open: false };
    function refreshLabel() { const el = periodWrap.querySelector('#wh-period-text'); if (el) el.textContent = whPeriodLabel(); }
    function openCal() {
      cal.from = whState.whFrom || ''; cal.to = whState.whTo || '';
      const b = cal.from ? parseISO(cal.from) : whPeriodBaseDate();
      cal.view = new Date(b.getFullYear(), b.getMonth(), 1);
      cal.open = true; calEl.hidden = false; periodFieldEl.classList.add('open'); drawCal();
    }
    function closeCal() { cal.open = false; calEl.hidden = true; periodFieldEl.classList.remove('open'); }
    function applyPeriod() {
      whState.whFrom = cal.from || ''; whState.whTo = cal.to || '';
      refreshLabel(); closeCal(); if (onApply) onApply();
    }
    function monthHtml(first) {
      const y = first.getFullYear(), mo = first.getMonth();
      const title = `${MONTHS_RU[mo]} ${y}`;
      let lead = first.getDay() - 1; if (lead < 0) lead = 6;
      const daysIn = new Date(y, mo + 1, 0).getDate();
      const f = cal.from, t = cal.to;
      const lo = (f && t) ? (f <= t ? f : t) : f;
      const hi = (f && t) ? (f <= t ? t : f) : f;
      function dayCell(dt, adjacent, posInRow) {
        const iso = isoDate(dt);
        let c = 'rnp-cal-cell day';
        if (adjacent) c += ' adjacent';
        let inRange = false, isEnd = false;
        if (lo && hi) { if (iso === lo || iso === hi) { isEnd = true; } else if (iso > lo && iso < hi) { inRange = true; } }
        else if (f && iso === f) { isEnd = true; }
        if (inRange) c += ' in-range';
        if (isEnd) c += ' end';
        if (inRange || isEnd) { if (iso === lo || posInRow === 0) c += ' band-l'; if (iso === hi || posInRow === 6) c += ' band-r'; }
        return `<button type="button" class="${c}" data-cal-day="${iso}"><span class="d">${dt.getDate()}</span></button>`;
      }
      let cells = '', pos = 0;
      for (let i = lead; i > 0; i--) { cells += dayCell(new Date(y, mo, 1 - i), true, pos); pos = (pos + 1) % 7; }
      for (let dnum = 1; dnum <= daysIn; dnum++) { cells += dayCell(new Date(y, mo, dnum), false, pos); pos = (pos + 1) % 7; }
      const fill = 42 - (lead + daysIn);
      for (let i = 1; i <= fill; i++) { cells += dayCell(new Date(y, mo + 1, i), true, pos); pos = (pos + 1) % 7; }
      return `<div class="rnp-cal-month"><div class="rnp-cal-mtitle">${title}</div><div class="rnp-cal-dow">${DOW_RU.map(x => `<span>${x}</span>`).join('')}</div><div class="rnp-cal-grid">${cells}</div></div>`;
    }
    function drawCal() {
      const m1 = new Date(cal.view.getFullYear(), cal.view.getMonth(), 1);
      const m2 = new Date(cal.view.getFullYear(), cal.view.getMonth() + 1, 1);
      calEl.innerHTML = `
        <div class="rnp-cal-nav">
          <button type="button" class="rnp-cal-arrow" data-cal-nav="-1" title="Предыдущий месяц">‹</button>
          <button type="button" class="rnp-cal-arrow" data-cal-nav="1" title="Следующий месяц">›</button>
        </div>
        <div class="rnp-cal-months">${monthHtml(m1)}${monthHtml(m2)}</div>
        <div class="rnp-cal-presets">
          <button type="button" class="rnp-cal-preset" data-cal-preset="month">Тек. месяц</button>
          <button type="button" class="rnp-cal-preset" data-cal-preset="quarter">Тек. квартал</button>
          <button type="button" class="rnp-cal-preset" data-cal-preset="year">Тек. год</button>
          <button type="button" class="rnp-cal-preset" data-cal-preset="all">Весь период</button>
        </div>`;
    }
    periodFieldEl.addEventListener('click', (e) => { e.stopPropagation(); cal.open ? closeCal() : openCal(); });
    calEl.addEventListener('click', (e) => {
      e.stopPropagation();
      const nav = e.target.closest('[data-cal-nav]');
      if (nav) { const delta = Number(nav.getAttribute('data-cal-nav')); cal.view = new Date(cal.view.getFullYear(), cal.view.getMonth() + delta, 1); drawCal(); return; }
      const pre = e.target.closest('[data-cal-preset]');
      if (pre) { const kind = pre.getAttribute('data-cal-preset'); if (kind === 'all') { cal.from = ''; cal.to = ''; } else { const r = whPresetRange(kind); cal.from = r.from; cal.to = r.to; } applyPeriod(); return; }
      const day = e.target.closest('[data-cal-day]');
      if (day) {
        const iso = day.getAttribute('data-cal-day');
        if (!cal.from || (cal.from && cal.to)) { cal.from = iso; cal.to = ''; drawCal(); }
        else { if (iso < cal.from) { cal.to = cal.from; cal.from = iso; } else { cal.to = iso; } applyPeriod(); }
      }
    });
    document.addEventListener('click', (e) => { if (cal.open && periodWrap && !periodWrap.contains(e.target)) closeCal(); });
  }

  // --- helpers визуализации складов ---
  function whFmtDate(iso) { const p = (iso || '').split('-'); return p.length === 3 ? p[2] + '.' + p[1] : iso; }

  // ===== Статусы складов =====
  // Значение хранится строкой; пусто → статус не задан.
  // ===== Инциденты: двойной клик по ячейке-дате → мини-меню =====
  let _whIncPopEl = null;
  function closeWhIncPop() {
    if (_whIncPopEl) { _whIncPopEl.remove(); _whIncPopEl = null; document.removeEventListener('click', closeWhIncPop); }
  }
  // Мини-меню у ячейки: всегда обе кнопки (открыть/закрыть с этой даты).
  function openWhIncPop(anchorTd, whId, whName, dt, isInc, onOpen, onClose) {
    closeWhIncPop();
    const pop = document.createElement('div');
    pop.className = 'wh-incpop';
    pop.innerHTML = `
      <div class="wh-incpop-title">Инцидент — ${U.esc(whName)}</div>
      <div class="wh-incpop-hint">дата: <b>${whFmtDate(dt)}</b>${isInc ? ' · сейчас активен' : ''}</div>
      <div class="wh-incpop-row">
        <button type="button" class="wh-incpop-btn open" data-act="open">Открыть инцидент с этой даты</button>
        <button type="button" class="wh-incpop-btn close" data-act="close">Закрыть инцидент с этой даты</button>
      </div>`;
    document.body.appendChild(pop);
    const r = anchorTd.getBoundingClientRect();
    const popW = 240;
    let left = r.left + window.scrollX;
    if (left + popW > window.scrollX + document.documentElement.clientWidth - 8) left = window.scrollX + document.documentElement.clientWidth - popW - 8;
    if (left < window.scrollX + 8) left = window.scrollX + 8;
    pop.style.left = left + 'px';
    pop.style.top = (r.bottom + window.scrollY + 4) + 'px';
    _whIncPopEl = pop;
    pop.addEventListener('click', (e) => e.stopPropagation());
    pop.querySelector('[data-act="open"]').addEventListener('click', () => { closeWhIncPop(); onOpen(); });
    pop.querySelector('[data-act="close"]').addEventListener('click', () => { closeWhIncPop(); onClose(); });
    setTimeout(() => document.addEventListener('click', closeWhIncPop), 0);
  }
  // Привязка двойного клика по ячейкам-датам. onDone() — перерисовка.
  function bindWhIncidentCells(root, onDone) {
    async function doOpen(whId, dt) {
      try {
        await API.whIncidentOpen(whId, dt);
        if (API.cacheClear) API.cacheClear('/api/warehouses');
        onDone && onDone();
      } catch (e) { alert('Ошибка открытия инцидента: ' + e.message); }
    }
    async function doClose(whId, dt) {
      try {
        await API.whIncidentClose(whId, dt);
        if (API.cacheClear) API.cacheClear('/api/warehouses');
        onDone && onDone();
      } catch (e) { alert('Ошибка закрытия инцидента: ' + e.message); }
    }
    root.querySelectorAll('td.wh-hm-clk[data-wh-cell]').forEach(td => {
      td.addEventListener('dblclick', (e) => {
        e.stopPropagation();
        const whId = parseInt(td.getAttribute('data-wh-id'), 10);
        const whName = td.getAttribute('data-wh-name') || '';
        const dt = td.getAttribute('data-dt');
        const isInc = td.getAttribute('data-inc') === '1';
        if (!whId || !dt) return;
        openWhIncPop(td, whId, whName, dt, isInc,
          () => doOpen(whId, dt),
          () => doClose(whId, dt));
      });
    });
  }

  const WH_STATUS_LIST = [
    { v: 'incident',  label: 'инцидент',  color: '#A12C7B' },  // красный
    { v: 'no_ship',   label: 'не грузим', color: '#7A7974' },  // серый
    { v: 'closed',    label: 'закрыт',    color: '#28251d' },  // чёрный
    { v: 'attention', label: 'внимание',  color: '#964219' },  // оранжевый
    { v: 'working',   label: 'работаем',  color: '#437A22' },  // зелёный
  ];
  const WH_STATUS_MAP = Object.fromEntries(WH_STATUS_LIST.map(s => [s.v, s]));
  function whStColor(v) { const s = WH_STATUS_MAP[v]; return s ? s.color : null; }
  function whStLabel(v) { const s = WH_STATUS_MAP[v]; return s ? s.label : ''; }
  // Бейдж статуса (как в РНП/Справочнике) — цветная плашка.
  function whStatusBadge(v) {
    const s = WH_STATUS_MAP[v];
    if (!s) return '<span class="wh-st-badge wh-st-none">— нет —</span>';
    return `<span class="wh-st-badge wh-st-${s.v}">${U.esc(s.label)}</span>`;
  }
  // SVG-иконка календаря (currentColor — окрашивается через CSS).
  const WH_CAL_SVG = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/></svg>`;
  // Ячейка статуса: селектор-пилюля + иконка-календарь (дата во всплывающем окне).
  // Иконка: серая если дата не задана; цвет статуса — если дата есть.
  function whStatusCellHtml(id, cur, statusDate) {
    const opts = ['<option value="">— нет —</option>']
      .concat(WH_STATUS_LIST.map(s => `<option value="${s.v}"${s.v === cur ? ' selected' : ''}>${U.esc(s.label)}</option>`)).join('');
    const sel = `<select class="wh-st-sel wh-st-${cur || 'none'}" data-id="${id}">${opts}</select>`;
    const hasDate = !!(cur && statusDate);
    const col = whStColor(cur) || '#b7b6b1';
    const dis = cur ? '' : ' disabled';
    const cls = 'wh-st-datebtn' + (hasDate ? ' has-date' : '') + (cur ? '' : ' disabled');
    const title = cur
      ? (hasDate ? 'Дата статуса: ' + whFmtDate(statusDate) + ' — подкраска с этого дня' : 'Задать дату статуса')
      : 'Сначала выберите статус';
    const btn = `<button type="button" class="${cls}" data-datebtn="${id}" data-date="${U.esc(statusDate || '')}"${dis} style="--wh-st-col:${col}" title="${U.esc(title)}">${WH_CAL_SVG}</button>`;
    return `<span class="wh-st-cell">${sel}${btn}</span>`;
  }
  // Привязка обработчиков статус-селектов и иконок-дат в контейнере root.
  // onDone(id) вызывается после успешного сохранения (для перерисовки).
  function bindWhStatusControls(root, onDone) {
    // вспом: сохранить статус+дату, сбросить кэш складов, перерисовать.
    async function save(id, st, sd) {
      try {
        await API.whSetStatus(id, st, sd);
        if (API.cacheClear) API.cacheClear('/api/warehouses');  // сброс кэша by_wh/catalog
        onDone && onDone(id);
      } catch (e) { alert('Ошибка статуса: ' + e.message); }
    }
    // смена статуса — сохраняем сразу (дату сбрасываем при снятии статуса).
    root.querySelectorAll('.wh-st-sel').forEach(sel => {
      sel.addEventListener('change', () => {
        const id = parseInt(sel.getAttribute('data-id'), 10);
        const st = sel.value || null;
        const btn = root.querySelector(`.wh-st-datebtn[data-datebtn="${id}"]`);
        const curDate = btn ? (btn.getAttribute('data-date') || null) : null;
        // без статуса дата не имеет смысла — обнуляем
        save(id, st, st ? curDate : null);
      });
    });
    // клик по иконке-календарю — всплывающее окно выбора даты (только из дат отчёта)
    root.querySelectorAll('.wh-st-datebtn').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (btn.classList.contains('disabled')) return;
        const id = parseInt(btn.getAttribute('data-datebtn'), 10);
        // если даты отчёта ещё не загружены (напр. открыли сразу Справочник) — подтянем
        if (!_whReportDates.length) {
          try { const d = await API.whByWh(); _whReportDates = (d.dates || []).slice(); } catch (_) {}
        }
        openWhDatePop(btn, id, btn.getAttribute('data-date') || '', (newDate) => {
          const sel = root.querySelector(`.wh-st-sel[data-id="${id}"]`);
          const st = sel ? (sel.value || null) : null;
          if (!st) return;
          save(id, st, newDate || null);
        });
      });
    });
  }
  // Всплывающее окно выбора даты статуса у иконки (anchorBtn).
  // Выбор — ТОЛЬКО из дат отчёта (кнопки), чтобы исключить ошибку ввода (напр. неверный год).
  let _whReportDates = [];
  let _whDatePopEl = null;
  function closeWhDatePop() { if (_whDatePopEl) { _whDatePopEl.remove(); _whDatePopEl = null; document.removeEventListener('click', closeWhDatePop); } }
  function whFmtDatePop(iso) { const p = (iso || '').split('-'); return p.length === 3 ? p[2] + '.' + p[1] + '.' + p[0] : iso; }
  function openWhDatePop(anchorBtn, id, curVal, onPick) {
    closeWhDatePop();
    const dates = (_whReportDates || []).slice();
    const pop = document.createElement('div');
    pop.className = 'wh-datepop';
    let listHtml;
    if (dates.length) {
      listHtml = '<div class="wh-datepop-list">' + dates.map(dt =>
        `<button type="button" class="wh-datepop-day${dt === curVal ? ' sel' : ''}" data-date="${dt}">${whFmtDatePop(dt)}</button>`
      ).join('') + '</div>';
    } else {
      listHtml = '<div class="wh-datepop-empty">Нет дат отчёта</div>';
    }
    pop.innerHTML = `
      <div class="wh-datepop-title">Дата статуса</div>
      <div class="wh-datepop-hint">подкраска значений с этого дня</div>
      ${listHtml}
      <div class="wh-datepop-row">
        <button type="button" class="wh-datepop-clear">Убрать дату</button>
      </div>`;
    document.body.appendChild(pop);
    // позиционируем под иконкой
    const r = anchorBtn.getBoundingClientRect();
    const popW = 220;
    let left = r.left + window.scrollX;
    if (left + popW > window.scrollX + document.documentElement.clientWidth - 8) left = window.scrollX + document.documentElement.clientWidth - popW - 8;
    if (left < window.scrollX + 8) left = window.scrollX + 8;
    pop.style.left = left + 'px';
    pop.style.top = (r.bottom + window.scrollY + 4) + 'px';
    _whDatePopEl = pop;
    pop.addEventListener('click', (e) => e.stopPropagation());
    pop.querySelectorAll('.wh-datepop-day').forEach(btn => {
      btn.addEventListener('click', () => { const v = btn.getAttribute('data-date'); closeWhDatePop(); onPick(v); });
    });
    pop.querySelector('.wh-datepop-clear').addEventListener('click', () => { closeWhDatePop(); onPick(''); });
    setTimeout(() => document.addEventListener('click', closeWhDatePop), 0);
    // прокрутить к выбранной дате
    setTimeout(() => { try { const s = pop.querySelector('.wh-datepop-day.sel'); if (s) s.scrollIntoView({ block: 'nearest' }); } catch (_) {} }, 30);
  }
  // ===== Федеральные округа =====
  // Полные названия в порядке удаления от Центрального (он первый).
  const WH_FD_LIST = ['Центральный', 'Северо-Западный', 'Приволжский', 'Южный', 'Северо-Кавказский', 'Уральский', 'Сибирский', 'Дальневосточный'];
  const WH_FD_ORDER = Object.fromEntries(WH_FD_LIST.map((n, i) => [n, i]));
  // Сортировочный индекс округа: распределённые — по порядку, «Не распределённые» — в конец.
  function whFdOrder(fd) { return (fd && WH_FD_ORDER[fd] != null) ? WH_FD_ORDER[fd] : 999; }
  // Ячейка выбора округа (селект) — для справочника.
  function whDistrictCellHtml(id, cur) {
    const opts = ['<option value="">— не распределён —</option>']
      .concat(WH_FD_LIST.map(n => `<option value="${U.esc(n)}"${n === cur ? ' selected' : ''}>${U.esc(n)}</option>`)).join('');
    return `<select class="wh-fd-sel${cur ? '' : ' none'}" data-fd-id="${id}">${opts}</select>`;
  }
  // Привязка селекторов округа в контейнере root. onDone(id) — после сохранения.
  function bindWhDistrictControls(root, onDone) {
    root.querySelectorAll('.wh-fd-sel').forEach(sel => {
      sel.addEventListener('change', async () => {
        const id = parseInt(sel.getAttribute('data-fd-id'), 10);
        const fd = sel.value || null;
        try {
          await API.whSetDistrict(id, fd);
          if (API.cacheClear) API.cacheClear('/api/warehouses');
          onDone && onDone(id);
        } catch (e) { alert('Ошибка округа: ' + e.message); }
      });
    });
  }
  function whHeat(frac) {
    if (frac <= 0) return '#f7e9f0';
    const a = [247, 246, 242], b = [122, 57, 187];
    const r = Math.round(a[0] + (b[0] - a[0]) * frac), g = Math.round(a[1] + (b[1] - a[1]) * frac), bl = Math.round(a[2] + (b[2] - a[2]) * frac);
    return `rgb(${r},${g},${bl})`;
  }
  function whSparkline(vals, w = 96, h = 24, color = '#7A39BB') {
    if (!vals.length) return '';
    if (vals.length === 1) { // одна точка — просто маркер
      const dot = vals[0] === 0 ? '#A12C7B' : color;
      return `<svg width="${w}" height="${h}"><circle cx="${w / 2}" cy="${h / 2}" r="3" fill="${dot}"/></svg>`;
    }
    const mx = Math.max(...vals) || 1, mn = Math.min(...vals), rng = (mx - mn) || 1;
    const pts = vals.map((v, i) => `${(i * (w / (vals.length - 1))).toFixed(1)},${(h - 2 - ((v - mn) / rng) * (h - 4)).toFixed(1)}`);
    const lastZero = vals[vals.length - 1] === 0;
    const dot = lastZero ? '#A12C7B' : color;
    const [lx, ly] = pts[pts.length - 1].split(',');
    return `<svg width="${w}" height="${h}" class="wh-spark"><polyline fill="none" stroke="${color}" stroke-width="1.6" points="${pts.join(' ')}"/><circle cx="${lx}" cy="${ly}" r="2.6" fill="${dot}"/></svg>`;
  }

  // ============ Подтаб «Справочник складов» ============
  async function whRenderCatalog(body) {
    body.innerHTML = '<div class="loader"><span class="spinner"></span></div>';
    let d;
    try { d = await API.whCatalog(); }
    catch (e) { body.innerHTML = `<div class="empty">Ошибка: ${U.esc(e.message)}</div>`; return; }
    const whs = d.warehouses || [];
    // Список корневых (каноничных) складов для выпадашки склейки.
    const roots = whs.filter(w => w.canonical_id == null);
    const rootOpts = roots.map(r => `<option value="${r.id}">${U.esc(r.name)}</option>`).join('');

    const rows = whs.map(w => {
      const isSyn = w.canonical_id != null;
      let statusBadge;
      if (w.missing) statusBadge = '<span class="wh-badge drop">не поступал</span>';
      else if (!w.is_active) statusBadge = '<span class="wh-badge arch">архив</span>';
      else if (w.days_seen <= 1) statusBadge = '<span class="wh-badge new">новый</span>';
      else statusBadge = '<span class="wh-badge ok">активен</span>';
      const canonCell = isSyn
        ? `<span class="muted">→ ${U.esc(w.canonical_name)}</span> <button class="wh-mini-btn" data-act="unmerge" data-id="${w.id}">расклеить</button>`
        : `<select class="input-sm wh-merge-sel" data-id="${w.id}"><option value="">— самостоятельный —</option>${roots.filter(r => r.id !== w.id).map(r => `<option value="${r.id}">склеить → ${U.esc(r.name)}</option>`).join('')}</select>`;
      return `<tr${w.missing ? ' class="wh-row-drop"' : ''}>
        <td class="l"><span class="wh-cat-name" data-id="${w.id}">${U.esc(w.name)}</span></td>
        <td class="c wh-cat-fd">${whDistrictCellHtml(w.id, w.federal_district)}</td>
        <td class="l">${canonCell}</td>
        <td class="c">${w.first_seen_date ? whFmtDateFull(w.first_seen_date) : '—'}</td>
        <td class="c">${w.last_seen_date ? whFmtDateFull(w.last_seen_date) : '—'}</td>
        <td class="r">${U.fmtNum(w.tot_qty)}</td>
        <td class="c">${statusBadge}</td>
        <td class="c">
          <button class="wh-mini-btn" data-act="rename" data-id="${w.id}" data-name="${U.esc(w.name)}">переименовать</button>
          <button class="wh-mini-btn" data-act="toggle" data-id="${w.id}" data-active="${w.is_active ? 1 : 0}">${w.is_active ? 'в архив' : 'вернуть'}</button>
        </td>
      </tr>`;
    }).join('');

    body.innerHTML = `
      <div class="rnp-card-title">Справочник складов Wildberries</div>
      <div class="wh-legend" style="margin:0 0 14px;">Новые склады заводятся автоматически при загрузке. Здесь можно переименовать отображаемое имя, склеить синонимы (в отчётах — одна строка), отправить в архив. «Не поступал» = склад был, но пропал из последней выгрузки.</div>
      <div class="wh-hm-wrap">
        <table class="wh-cat-tbl">
          <thead><tr><th class="l">Название (из выгрузки WB)</th><th>Федеральный округ</th><th class="l">Сопоставление</th><th>Первое появление</th><th>Последняя выгрузка</th><th>Всего шт</th><th>Состояние</th><th>Действия</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;

    // --- обработчики федерального округа ---
    bindWhDistrictControls(body, () => whRenderCatalog(body));
    // --- обработчики склейки ---
    body.querySelectorAll('.wh-merge-sel').forEach(sel => {
      sel.addEventListener('change', async () => {
        const id = parseInt(sel.getAttribute('data-id'), 10), canon = parseInt(sel.value, 10);
        if (!canon) return;
        try { await API.whMerge(id, canon); if (API.cacheClear) API.cacheClear('/api/warehouses'); whRenderCatalog(body); }
        catch (e) { alert('Ошибка склейки: ' + e.message); }
      });
    });
    body.querySelectorAll('.wh-mini-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const act = btn.getAttribute('data-act'), id = parseInt(btn.getAttribute('data-id'), 10);
        try {
          if (act === 'unmerge') { await API.whUnmerge(id); }
          else if (act === 'toggle') { await API.whSetActive(id, btn.getAttribute('data-active') !== '1'); }
          else if (act === 'rename') {
            const cur = btn.getAttribute('data-name');
            const nn = prompt('Новое отображаемое имя склада:', cur);
            if (nn == null || nn.trim() === '' || nn.trim() === cur) return;
            await API.whRename(id, nn.trim());
          }
          if (API.cacheClear) API.cacheClear('/api/warehouses');
          whRenderCatalog(body);
        } catch (e) { alert('Ошибка: ' + e.message); }
      });
    });
  }
  function whFmtDateFull(iso) { const p = (iso || '').split('-'); return p.length === 3 ? p[2] + '.' + p[1] + '.' + p[0] : iso; }

  // ================================================================
  // ============ OZON: раздел «Склады → OZON» (зеркало WB) ========
  // ================================================================
  // Контейнер с подтабами. Аналог warehousesWb, но для Ozon —
  // отдельное состояние whState.ozSub (не пересекается с wbSub).
  async function warehousesOzon(root, mpCls) {
    const sub = whState.ozSub || 'bywh';   // 'general' | 'bywh' | 'catalog'
    root.innerHTML = `<div class="card rnp-card ${mpCls}">
      <div class="rnp-toolbar"><div class="subtabs" id="wh-subtabs"></div></div>
      <div class="subtabs" id="wh-oz-subtabs" style="margin:4px 0 16px;"></div>
      <div id="wh-oz-body"><div class="loader"><span class="spinner"></span></div></div>
    </div>`;
    whSubtabs(root.querySelector('#wh-subtabs'));

    const tabs = [['general', 'Общие данные', 'ozon'], ['bywh', 'По складам', 'ozon'], ['catalog', 'Справочник складов', 'ozon']];
    const host = root.querySelector('#wh-oz-subtabs');
    tabs.forEach(([id, label, c]) => {
      const b = document.createElement('button');
      b.className = 'subtab ' + c + (sub === id ? ' active' : '');
      b.textContent = label;
      b.addEventListener('click', () => {
        if (whState.ozSub === id) return;
        whState.ozSub = id;
        warehousesOzon(root, mpCls);
      });
      host.appendChild(b);
    });

    const body = root.querySelector('#wh-oz-body');
    if (sub === 'bywh') return ozRenderByWh(body);
    if (sub === 'catalog') return ozRenderCatalog(body);
    // «Общие данные» — следующий этап (как в WB).
    body.innerHTML = `<div class="empty" style="padding:56px 40px; text-align:center; color:#7a7974;">
      Таблица общих остатков по группам и месяцам (шт / себестоимость / в ценах продажи) — в разработке, следующим шагом.</div>`;
  }

  // --- Поле периода Ozon (отдельная копия whPeriodFieldHtml — не трогаем WB) ---
  function ozPeriodFieldHtml() {
    return `<div class="rnp-period" id="oz-period">
      <button class="rnp-period-field" id="oz-period-field" type="button" title="Выбрать период остатков">
        <svg class="rnp-period-ico" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/></svg>
        <span class="rnp-period-text" id="oz-period-text">${ozPeriodLabel()}</span>
      </button>
      <div class="rnp-cal" id="oz-cal" hidden></div>
    </div>`;
  }
  // Подпись поля периода Ozon.
  function ozPeriodLabel() {
    const f = whState.ozFrom, t = whState.ozTo;
    if (!f && !t) return 'Весь период';
    if (f && t) return f === t ? fmtRu(f) : `${fmtRu(f)} — ${fmtRu(t)}`;
    return fmtRu(f || t);
  }
  // Базовая дата для пресетов Ozon — последняя дата с данными, иначе сегодня.
  function ozPeriodBaseDate() {
    const dd = whState.ozData && whState.ozData.dates;
    if (dd && dd.length) { const d = parseISO(dd[dd.length - 1]); if (d) return d; }
    return new Date();
  }
  function ozPresetRange(kind) {
    const base = ozPeriodBaseDate();
    const y = base.getFullYear(), m = base.getMonth();
    let from, to;
    if (kind === 'month') { from = new Date(y, m, 1); to = new Date(y, m + 1, 0); }
    else if (kind === 'quarter') { const q = Math.floor(m / 3); from = new Date(y, q * 3, 1); to = new Date(y, q * 3 + 3, 0); }
    else { from = new Date(y, 0, 1); to = new Date(y, 11, 31); }
    return { from: isoDate(from), to: isoDate(to) };
  }
  // Привязка поля периода Ozon + поповера-календаря (копия bindWhPeriod, ozFrom/ozTo).
  function bindOzPeriod(wrap, onApply) {
    const periodWrap = wrap.querySelector('#oz-period');
    const periodFieldEl = wrap.querySelector('#oz-period-field');
    const calEl = wrap.querySelector('#oz-cal');
    if (!periodFieldEl || !calEl) return;
    const MONTHS_RU = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
    const DOW_RU = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
    const cal = { from: '', to: '', view: null, open: false };
    function refreshLabel() { const el = periodWrap.querySelector('#oz-period-text'); if (el) el.textContent = ozPeriodLabel(); }
    function openCal() {
      cal.from = whState.ozFrom || ''; cal.to = whState.ozTo || '';
      const b = cal.from ? parseISO(cal.from) : ozPeriodBaseDate();
      cal.view = new Date(b.getFullYear(), b.getMonth(), 1);
      cal.open = true; calEl.hidden = false; periodFieldEl.classList.add('open'); drawCal();
    }
    function closeCal() { cal.open = false; calEl.hidden = true; periodFieldEl.classList.remove('open'); }
    function applyPeriod() {
      whState.ozFrom = cal.from || ''; whState.ozTo = cal.to || '';
      refreshLabel(); closeCal(); if (onApply) onApply();
    }
    function monthHtml(first) {
      const y = first.getFullYear(), mo = first.getMonth();
      const title = `${MONTHS_RU[mo]} ${y}`;
      let lead = first.getDay() - 1; if (lead < 0) lead = 6;
      const daysIn = new Date(y, mo + 1, 0).getDate();
      const f = cal.from, t = cal.to;
      const lo = (f && t) ? (f <= t ? f : t) : f;
      const hi = (f && t) ? (f <= t ? t : f) : f;
      function dayCell(dt, adjacent, posInRow) {
        const iso = isoDate(dt);
        let c = 'rnp-cal-cell day';
        if (adjacent) c += ' adjacent';
        let inRange = false, isEnd = false;
        if (lo && hi) { if (iso === lo || iso === hi) { isEnd = true; } else if (iso > lo && iso < hi) { inRange = true; } }
        else if (f && iso === f) { isEnd = true; }
        if (inRange) c += ' in-range';
        if (isEnd) c += ' end';
        if (inRange || isEnd) { if (iso === lo || posInRow === 0) c += ' band-l'; if (iso === hi || posInRow === 6) c += ' band-r'; }
        return `<button type="button" class="${c}" data-cal-day="${iso}"><span class="d">${dt.getDate()}</span></button>`;
      }
      let cells = '', pos = 0;
      for (let i = lead; i > 0; i--) { cells += dayCell(new Date(y, mo, 1 - i), true, pos); pos = (pos + 1) % 7; }
      for (let dnum = 1; dnum <= daysIn; dnum++) { cells += dayCell(new Date(y, mo, dnum), false, pos); pos = (pos + 1) % 7; }
      const fill = 42 - (lead + daysIn);
      for (let i = 1; i <= fill; i++) { cells += dayCell(new Date(y, mo + 1, i), true, pos); pos = (pos + 1) % 7; }
      return `<div class="rnp-cal-month"><div class="rnp-cal-mtitle">${title}</div><div class="rnp-cal-dow">${DOW_RU.map(x => `<span>${x}</span>`).join('')}</div><div class="rnp-cal-grid">${cells}</div></div>`;
    }
    function drawCal() {
      const m1 = new Date(cal.view.getFullYear(), cal.view.getMonth(), 1);
      const m2 = new Date(cal.view.getFullYear(), cal.view.getMonth() + 1, 1);
      calEl.innerHTML = `
        <div class="rnp-cal-nav">
          <button type="button" class="rnp-cal-arrow" data-cal-nav="-1" title="Предыдущий месяц">‹</button>
          <button type="button" class="rnp-cal-arrow" data-cal-nav="1" title="Следующий месяц">›</button>
        </div>
        <div class="rnp-cal-months">${monthHtml(m1)}${monthHtml(m2)}</div>
        <div class="rnp-cal-presets">
          <button type="button" class="rnp-cal-preset" data-cal-preset="month">Тек. месяц</button>
          <button type="button" class="rnp-cal-preset" data-cal-preset="quarter">Тек. квартал</button>
          <button type="button" class="rnp-cal-preset" data-cal-preset="year">Тек. год</button>
          <button type="button" class="rnp-cal-preset" data-cal-preset="all">Весь период</button>
        </div>`;
    }
    periodFieldEl.addEventListener('click', (e) => { e.stopPropagation(); cal.open ? closeCal() : openCal(); });
    calEl.addEventListener('click', (e) => {
      e.stopPropagation();
      const nav = e.target.closest('[data-cal-nav]');
      if (nav) { const delta = Number(nav.getAttribute('data-cal-nav')); cal.view = new Date(cal.view.getFullYear(), cal.view.getMonth() + delta, 1); drawCal(); return; }
      const pre = e.target.closest('[data-cal-preset]');
      if (pre) { const kind = pre.getAttribute('data-cal-preset'); if (kind === 'all') { cal.from = ''; cal.to = ''; } else { const r = ozPresetRange(kind); cal.from = r.from; cal.to = r.to; } applyPeriod(); return; }
      const day = e.target.closest('[data-cal-day]');
      if (day) {
        const iso = day.getAttribute('data-cal-day');
        if (!cal.from || (cal.from && cal.to)) { cal.from = iso; cal.to = ''; drawCal(); }
        else { if (iso < cal.from) { cal.to = cal.from; cal.from = iso; } else { cal.to = iso; } applyPeriod(); }
      }
    });
    document.addEventListener('click', (e) => { if (cal.open && periodWrap && !periodWrap.contains(e.target)) closeCal(); });
  }

  // ===== Инциденты Ozon: двойной клик по ячейке-дате (копия bindWhIncidentCells) =====
  let _ozReportDates = [];
  function bindOzIncidentCells(root, onDone) {
    async function doOpen(whId, dt) {
      try {
        await API.ozIncidentOpen(whId, dt);
        if (API.cacheClear) API.cacheClear('/api/ozon_warehouses');
        onDone && onDone();
      } catch (e) { alert('Ошибка открытия инцидента: ' + e.message); }
    }
    async function doClose(whId, dt) {
      try {
        await API.ozIncidentClose(whId, dt);
        if (API.cacheClear) API.cacheClear('/api/ozon_warehouses');
        onDone && onDone();
      } catch (e) { alert('Ошибка закрытия инцидента: ' + e.message); }
    }
    root.querySelectorAll('td.wh-hm-clk[data-wh-cell]').forEach(td => {
      td.addEventListener('dblclick', (e) => {
        e.stopPropagation();
        const whId = parseInt(td.getAttribute('data-wh-id'), 10);
        const whName = td.getAttribute('data-wh-name') || '';
        const dt = td.getAttribute('data-dt');
        const isInc = td.getAttribute('data-inc') === '1';
        if (!whId || !dt) return;
        openWhIncPop(td, whId, whName, dt, isInc,
          () => doOpen(whId, dt),
          () => doClose(whId, dt));
      });
    });
  }

  // ============ Подтаб «По складам» — Ozon: ТРЁХУРОВНЕВОЕ дерево ФО → Кластер → Склад ============
  async function ozRenderByWh(body) {
    body.innerHTML = '<div class="loader"><span class="spinner"></span></div>';
    let d;
    try { d = await API.ozByWh({ date_from: whState.ozFrom, date_to: whState.ozTo, days: 400 }); }
    catch (e) { body.innerHTML = `<div class="empty">Ошибка: ${U.esc(e.message)}</div>`; return; }
    whState.ozData = d;  // для базовой даты пресетов
    if (!d.dates || !d.dates.length) {
      body.innerHTML = `
        <div class="wh-byrow-toolbar">
          <div class="rnp-card-title" style="margin:0;">Стоимость остатков по складам</div>
          <div class="wh-toolbar-right">${ozPeriodFieldHtml()}</div>
        </div>
        <div class="empty" style="padding:48px;text-align:center;color:#7a7974;">Нет данных об остатках за выбранный период. Загрузите отчёт на подтабе «Загрузка данных» или расширьте период.</div>`;
      bindOzPeriod(body, () => ozRenderByWh(body));
      return;
    }
    const dates = d.dates, last = d.last_date, prev = dates.length >= 2 ? dates[dates.length - 2] : null;
    const isSale = whState.ozPriceMode === 'sale';
    const rubKey = isSale ? 'rub_sale' : 'rub_cost';
    const priceLbl = isSale ? 'в ценах продажи' : 'в ценах себестоимости';
    const rubOf = (o) => (o && o[rubKey] != null) ? o[rubKey] : null;

    const tot = d.totals[last] || {};
    const totPrev = prev ? (d.totals[prev] || {}) : null;
    const nMissing = (d.missing || []).length;

    let costNow = (tot.rub_cost != null ? tot.rub_cost : 0);
    const costPrev = totPrev ? (totPrev.rub_cost != null ? totPrev.rub_cost : 0) : null;
    let saleNow = (tot.rub_sale != null ? tot.rub_sale : 0);
    const salePrev = totPrev ? (totPrev.rub_sale != null ? totPrev.rub_sale : 0) : null;
    // dCost/dSale вычисляются НИЖЕ — после поправки costNow/saleNow на заморозку.
    const cellIncident = (w, dt) => {
      const ivs = w.incidents || [];
      for (const iv of ivs) {
        if (dt >= iv.start && (iv.end == null || dt <= iv.end)) return iv;
      }
      return null;
    };
    const isIncidentCell = (w, dt) => !!cellIncident(w, dt);
    const whHasIncidentOn = (w, dt) => isIncidentCell(w, dt);
    // === «Заморозка» остатка при инциденте (как у Wildberries) ===
    // Ozon после инцидента продолжает отдавать склад, но обнуляет qty (v===0/пусто),
    // из-за чего ячейка показывала «скрыт» и рамка инцидента обрывалась, а KPI = 0.
    // Замораживаем: если на дату активен инцидент и реальное значение по ключу пусто/0,
    // берём ПОСЛЕДНЕЕ известное ненулевое значение по этому ключу ДО начала интервала.
    // Работает по каждому ключу отдельно (rub_cost / rub_sale). НЕ трогает данные в базе.
    const frozenByKey = (w, dt, key) => {
      const c = d.cells[w.id] || {};
      const cell = c[dt];
      const real = (cell && cell[key] != null) ? cell[key] : null;
      const iv = cellIncident(w, dt);
      if (!iv) return real;                 // нет инцидента — как есть
      if (real != null && real > 0) return real;  // есть реальный остаток — показываем его
      // остаток недоступен из-за инцидента → тянем последнее ненулевое ДО начала интервала
      const di = dates.indexOf(dt);
      for (let j = di - 1; j >= 0; j--) {
        const pdt = dates[j];
        if (pdt < iv.start) {               // вышли за левую границу интервала — это «до инцидента»
          const pc = c[pdt];
          const pv = (pc && pc[key] != null) ? pc[key] : null;
          if (pv != null && pv > 0) return pv;
        } else {
          // ещё внутри интервала: если там было реальное ненулевое — тоже годится как база заморозки
          const pc = c[pdt];
          const pv = (pc && pc[key] != null) ? pc[key] : null;
          if (pv != null && pv > 0) return pv;
        }
      }
      return real;                          // не нашли базу — оставляем как есть (0/пусто)
    };
    // Значение ячейки по текущему ценовому режиму С УЧЁТОМ заморозки.
    const frozenOf = (w, dt) => frozenByKey(w, dt, rubKey);
    // Признак: значение показано за счёт заморозки (реальный остаток отсутствует).
    const isFrozen = (w, dt) => {
      const iv = cellIncident(w, dt);
      if (!iv) return false;
      const c = d.cells[w.id] || {};
      const cell = c[dt];
      const real = (cell && cell[rubKey] != null) ? cell[rubKey] : null;
      return !(real != null && real > 0);
    };
    let incCost = 0, incSale = 0, incCount = 0;
    // Поправка общей стоимости: бэкенд посчитал tot по ОБНУЛЁННЫМ данным Ozon,
    // поэтому замороженные склады вошли в него как 0 — добавляем разницу.
    let freezeAddCost = 0, freezeAddSale = 0;
    d.warehouses.forEach(w => {
      if (whHasIncidentOn(w, last)) {
        // KPI считаем по ЗАМОРОЖЕННЫМ значениям (как WB), а не по обнулённой ячейке last.
        const fCost = frozenByKey(w, last, 'rub_cost') || 0;
        const fSale = frozenByKey(w, last, 'rub_sale') || 0;
        incCost += fCost;
        incSale += fSale;
        incCount++;
        // если реальный остаток обнулён — только тогда прибавляем заморозку к общей сумме
        const cl = (d.cells[w.id] || {})[last] || {};
        const realC = (cl.rub_cost != null ? cl.rub_cost : 0);
        const realS = (cl.rub_sale != null ? cl.rub_sale : 0);
        if (!(realC > 0)) freezeAddCost += fCost;
        if (!(realS > 0)) freezeAddSale += fSale;
      }
    });
    // Применяем поправку к общей стоимости остатков (обе плитки).
    costNow += freezeAddCost;
    saleNow += freezeAddSale;
    const dCost = costPrev != null ? (costNow - costPrev) : null;
    const dSale = salePrev != null ? (saleNow - salePrev) : null;
    const incSub = (val) => incCount > 0
      ? `<div class="wh-kpi-inc" title="Остаток на складах с активным инцидентом (складов: ${incCount})">из них <b>${U.fmtMoneyShort(val)}</b> — инцидент</div>`
      : '';
    const deltaSub = (dv) => dv == null ? '<span class="wh-kpi-sub muted">на ' + whFmtDate(last) + '</span>'
      : `<span class="wh-kpi-sub ${dv < 0 ? 'neg' : 'pos'}">${dv < 0 ? '▼' : '▲'} ${U.fmtMoneyShort(Math.abs(dv))} за сутки</span>`;
    const whTotal = d.warehouses.length;
    const whActive = whTotal - incCount;
    const inclWord = (n) => {
      const n10 = n % 10, n100 = n % 100;
      if (n10 === 1 && n100 !== 11) return 'складе';
      if (n10 >= 2 && n10 <= 4 && (n100 < 10 || n100 >= 20)) return 'складах';
      return 'складах';
    };
    let actKpi;
    if (incCount > 0) {
      actKpi = `<div class="wh-kpi alert"><div class="wh-kpi-lbl">Активных складов</div><div class="wh-kpi-val neg">${whActive} / ${whTotal}</div><div class="wh-kpi-sub neg">инцидент на ${incCount} ${inclWord(incCount)}</div></div>`;
    } else if (nMissing > 0) {
      actKpi = `<div class="wh-kpi alert"><div class="wh-kpi-lbl">Активных складов</div><div class="wh-kpi-val neg">${tot.wh_count || 0} / ${whTotal}</div><div class="wh-kpi-sub neg">⚠ пропало за сутки: ${nMissing}</div></div>`;
    } else {
      actKpi = `<div class="wh-kpi"><div class="wh-kpi-lbl">Активных складов</div><div class="wh-kpi-val">${whActive} / ${whTotal}</div><div class="wh-kpi-sub pos">все на месте</div></div>`;
    }
    const missKpi = actKpi;
    const kpiRow = `<div class="wh-kpi-row">
      <div class="wh-kpi"><div class="wh-kpi-lbl">Стоимость остатков · себестоимость</div><div class="wh-kpi-val">${U.fmtMoneyShort(costNow)}</div>${deltaSub(dCost)}${incSub(incCost)}</div>
      <div class="wh-kpi"><div class="wh-kpi-lbl">Стоимость остатков · в ценах продажи</div><div class="wh-kpi-val">${U.fmtMoneyShort(saleNow)}</div>${deltaSub(dSale)}${incSub(incSale)}</div>
      <div class="wh-kpi"><div class="wh-kpi-lbl">Количество</div><div class="wh-kpi-val">${U.fmtNum(tot.qty || 0)}</div><div class="wh-kpi-sub muted">шт на ${whFmtDate(last)}</div></div>
      ${missKpi}
    </div>`;

    const colMax = {};
    dates.forEach(dt => {
      let mx = 0;
      d.warehouses.forEach(w => { const c = d.cells[w.id] || {}; if (isIncidentCell(w, dt)) return; const v = rubOf(c[dt]) || 0; if (v > mx) mx = v; });
      colMax[dt] = mx;
    });
    const whWeightCls = (v, dt, inInc) => {
      // Жирность ТОЛЬКО по объёму (доля от максимума столбца-дня среди ВСЕХ складов),
      // независимо от групп И от статуса инцидента. Инцидент отмечается лишь рамкой/цветом,
      // а не жирностью (иначе мелкий склад в рамке выглядел бы «крупнее» большого без рамки).
      if (v == null) return 'wh-w-mid';
      const mx = colMax[dt] || 0;
      const frac = mx ? v / mx : 0;
      if (frac >= 0.55) return 'wh-w-hi';
      if (frac >= 0.18) return 'wh-w-mid';
      return 'wh-w-lo';
    };
    const whShortNoRub = (v) => {
      if (v == null || isNaN(v)) return '—';
      const a = Math.abs(v);
      if (a >= 1e9) return (v / 1e9).toFixed(2).replace('.', ',') + ' млрд';
      if (a >= 1e6) return (v / 1e6).toFixed(2).replace('.', ',') + ' млн';
      if (a >= 1e3) return Math.round(v / 1e3) + ' тыс';
      return U.fmtNum(v);
    };

    const thDates = dates.map(dt => `<th class="wh-hm-d">${whFmtDate(dt)}</th>`).join('');

    _ozReportDates = (d.dates || []).slice();
    const INC_COL = '#A12C7B';
    // Строка склада — с доп. отступом (третий уровень вложенности: ФО → Кластер → Склад).
    const renderWhRow = (w, grpId, collapsed, indentPl) => {
      const c = d.cells[w.id] || {};
      const cells = dates.map((dt, di) => {
        const v = rubOf(c[dt]);
        const iv = cellIncident(w, dt);
        const inInc = !!iv;
        const prevDt = di > 0 ? dates[di - 1] : null;
        const nextDt = di < dates.length - 1 ? dates[di + 1] : null;
        const isIncStart = inInc && (!prevDt || !cellIncident(w, prevDt) || iv.start === dt);
        const isIncEnd = inInc && (!nextDt || !cellIncident(w, nextDt) || (iv.end != null && iv.end === dt));
        const dataAttr = ` data-wh-cell="1" data-wh-id="${w.id}" data-wh-name="${U.esc(w.name)}" data-dt="${dt}" data-inc="${inInc ? '1' : '0'}"`;
        // Заморозка: внутри инцидента реальный остаток пуст/0, но есть замороженное значение.
        const fv = inInc ? frozenOf(w, dt) : v;
        const frozenHere = inInc && isFrozen(w, dt) && fv != null && fv > 0;
        if (frozenHere) {
          // Показываем ЗАМОРОЖЕННОЕ значение с рамкой инцидента (как WB), а не «скрыт».
          let fcls = 'wh-hm-cell wh-hm-clk wh-hm-frozen ' + whWeightCls(fv, dt, true) + ' wh-hm-instatus', fextra = `--wh-st-col:${INC_COL};`;
          if (isIncStart) fcls += ' wh-hm-ststart';
          if (isIncEnd) fcls += ' wh-hm-stend';
          const fTip = ` · инцидент (с ${whFmtDate(iv.start)}${iv.end ? ' по ' + whFmtDate(iv.end) : ', открыт'}) · остаток заморожен (Ozon обнулил выгрузку)`;
          return `<td class="${fcls}" style="background:#ffffff;${fextra}"${dataAttr} title="${U.esc(w.name)} · ${whFmtDate(dt)}: ${U.fmtMoney(fv)} (${priceLbl})${fTip} · двойной клик — управление инцидентом"><span class="wh-hm-frz">${whShortNoRub(fv)}</span></td>`;
        }
        if (v == null) {
          let ncls = 'wh-hm-cell wh-hm-clk', nextra = '';
          if (inInc) {
            ncls += ' wh-hm-instatus';
            nextra += `--wh-st-col:${INC_COL};`;
            if (isIncStart) ncls += ' wh-hm-ststart';
            if (isIncEnd) ncls += ' wh-hm-stend';
          }
          const nTip = inInc ? ` · инцидент (с ${whFmtDate(iv.start)}${iv.end ? ' по ' + whFmtDate(iv.end) : ', открыт'})` : '';
          return `<td class="${ncls}" style="background:#ffffff;${nextra}"${dataAttr} title="нет данных${nTip} · двойной клик — управление инцидентом"><span class="muted" style="font-size:10px;">—</span></td>`;
        }
        if (v === 0) {
          // Обнулённый остаток ВНЕ инцидента (или без базы для заморозки) — прежнее «скрыт».
          let zcls = 'wh-hm-cell wh-hm-zero wh-hm-clk', zextra = '';
          if (inInc) {
            zcls += ' wh-hm-instatus';
            zextra += `--wh-st-col:${INC_COL};`;
            if (isIncStart) zcls += ' wh-hm-ststart';
            if (isIncEnd) zcls += ' wh-hm-stend';
          }
          const zTip = inInc ? ` · инцидент (с ${whFmtDate(iv.start)}${iv.end ? ' по ' + whFmtDate(iv.end) : ', открыт'})` : '';
          return `<td class="${zcls}" style="background:#ffffff;${zextra}"${dataAttr} title="склад пропал / нулевой остаток${zTip} · двойной клик — инцидент"><span style="font-size:10px;">скрыт</span></td>`;
        }
        let cls = 'wh-hm-cell wh-hm-clk ' + whWeightCls(v, dt, inInc), extra = '';
        if (inInc) {
          cls += ' wh-hm-instatus';
          extra += `--wh-st-col:${INC_COL};`;
          if (isIncStart) cls += ' wh-hm-ststart';
          if (isIncEnd) cls += ' wh-hm-stend';
        }
        const incTip = inInc ? ` · инцидент (с ${whFmtDate(iv.start)}${iv.end ? ' по ' + whFmtDate(iv.end) : ', открыт'})` : '';
        return `<td class="${cls}" style="background:#ffffff;${extra}"${dataAttr} title="${U.esc(w.name)} · ${whFmtDate(dt)}: ${U.fmtMoney(v)} (${priceLbl})${incTip} · двойной клик — управление инцидентом">${whShortNoRub(v)}</td>`;
      }).join('');
      // Спарклайн/тренд — по ЗАМОРОЖЕННЫМ значениям, чтобы линия не падала в ноль в инциденте.
      const seq = dates.map(dt => (frozenOf(w, dt)) || 0);
      const spark = whSparkline(seq, 96, 24, '#2f6bff');
      const d0 = seq.length >= 2 ? seq[seq.length - 2] : 0, d1 = seq[seq.length - 1];
      const delta = d1 - d0;
      let dTxt, dCls;
      if (d1 === 0 && d0 > 0) { dTxt = '▼ пропал'; dCls = 'neg'; }
      else if (delta > 0) { dTxt = '▲ ' + whShortNoRub(delta); dCls = 'pos'; }
      else if (delta < 0) { dTxt = '▼ ' + whShortNoRub(Math.abs(delta)); dCls = 'neg'; }
      else { dTxt = '—'; dCls = 'muted'; }
      const childAttr = grpId ? ` data-wh-grp-child="${U.esc(grpId)}"` : '';
      const hiddenCls = collapsed ? ' wh-hm-hidden' : '';
      const nameStyle = indentPl ? ` style="padding-left:${indentPl}px;"` : '';
      return `<tr class="wh-hm-wrow${hiddenCls}"${childAttr}>`
        + `<td class="wh-hm-name" title="${U.esc(w.name)}"${nameStyle}>${U.esc(w.name)}</td>`
        + `${cells}<td class="wh-hm-spark">${spark}</td><td class="wh-hm-delta ${dCls}">${dTxt}</td></tr>`;
    };

    // --- ГРУППИРОВКА: уровень 1 — федеральный округ, уровень 2 — кластер ---
    // Подытоги групп (ФО/кластер) — С УЧЁТОМ заморозки (как KPI и ячейки).
    const groupTotalsByDate = (list, dt) => list.reduce((s, w) => s + (frozenOf(w, dt) || 0), 0);
    const fdMap = new Map();  // fd (или null) -> [w, ...]
    d.warehouses.forEach(w => {
      const key = (w.federal_district && WH_FD_ORDER[w.federal_district] != null) ? w.federal_district : null;
      if (!fdMap.has(key)) fdMap.set(key, []);
      fdMap.get(key).push(w);
    });
    const fdKeys = [...fdMap.keys()].sort((a, b) => whFdOrder(a) - whFdOrder(b));

    // Рендер строки кластера (уровень 2) + вложенных складов (уровень 3).
    const renderClusterGroup = (fdKey, clKey, list, collapsedCl, fdCollapsed) => {
      const clName = clKey || 'Без кластера';
      const clCells = dates.map(dt => {
        const v = groupTotalsByDate(list, dt);
        return `<td class="wh-hm-gcell" title="${U.esc(clName)} · ${whFmtDate(dt)}: ${U.fmtMoney(v)} (${priceLbl})">${v ? whShortNoRub(v) : '—'}</td>`;
      }).join('');
      const clSeq = dates.map(dt => groupTotalsByDate(list, dt));
      const c0 = clSeq.length >= 2 ? clSeq[clSeq.length - 2] : 0, c1 = clSeq[clSeq.length - 1];
      const cd = c1 - c0;
      let cTxt = '—', cCls = 'muted';
      if (cd > 0) { cTxt = '▲ ' + whShortNoRub(cd); cCls = 'pos'; }
      else if (cd < 0) { cTxt = '▼ ' + whShortNoRub(Math.abs(cd)); cCls = 'neg'; }
      const undCls = clKey ? '' : ' wh-hm-group-und';
      const fdId = fdKey || '__none__';
      const clId = clKey || '__none__';
      const clFullId = `cl::${fdId}::${clId}`;
      const tw = collapsedCl ? '▸' : '▾';
      const hiddenRow = fdCollapsed ? ' wh-hm-hidden' : '';
      let html = `<tr class="wh-hm-group wh-hm-group-lvl2${undCls}${collapsedCl ? ' is-collapsed' : ''}${hiddenRow}" data-oz-cl="${U.esc(clFullId)}" data-oz-fd-parent="${U.esc(fdId)}">`
        + `<td class="wh-hm-name l" style="padding-left:18px;" title="${U.esc(clName)} — клик, чтобы свернуть/развернуть"><span class="wh-hm-tw" aria-hidden="true">${tw}</span>${U.esc(clName)} <span class="wh-hm-group-cnt">${list.length}</span></td>`
        + `${clCells}<td class="wh-hm-spark"></td><td class="wh-hm-delta ${cCls}">${cTxt}</td></tr>`;
      list.forEach(w => { html += renderWhRow(w, clFullId, collapsedCl || fdCollapsed, 34); });
      return html;
    };

    let rows = '';
    fdKeys.forEach(fdKey => {
      const fdList = fdMap.get(fdKey);
      const fdName = fdKey || 'Не распределённые';
      const fdCells = dates.map(dt => {
        const v = groupTotalsByDate(fdList, dt);
        return `<td class="wh-hm-gcell" title="${U.esc(fdName)} · ${whFmtDate(dt)}: ${U.fmtMoney(v)} (${priceLbl})">${v ? whShortNoRub(v) : '—'}</td>`;
      }).join('');
      const fdSeq = dates.map(dt => groupTotalsByDate(fdList, dt));
      const fdSpark = whSparkline(fdSeq, 96, 24, '#2f6bff');
      const g0 = fdSeq.length >= 2 ? fdSeq[fdSeq.length - 2] : 0, g1 = fdSeq[fdSeq.length - 1];
      const gd = g1 - g0;
      let gTxt = '—', gCls = 'muted';
      if (gd > 0) { gTxt = '▲ ' + whShortNoRub(gd); gCls = 'pos'; }
      else if (gd < 0) { gTxt = '▼ ' + whShortNoRub(Math.abs(gd)); gCls = 'neg'; }
      const undCls = fdKey ? '' : ' wh-hm-group-und';
      const fdId = fdKey || '__none__';
      const fdFullId = `fd::${fdId}`;
      const fdCollapsed = !!whState.ozCollapsed[fdFullId];
      const tw = fdCollapsed ? '▸' : '▾';
      rows += `<tr class="wh-hm-group${undCls}${fdCollapsed ? ' is-collapsed' : ''}" data-oz-fd="${U.esc(fdFullId)}">`
        + `<td class="wh-hm-name l" title="${U.esc(fdName)} — клик, чтобы свернуть/развернуть"><span class="wh-hm-tw" aria-hidden="true">${tw}</span>${U.esc(fdName)} <span class="wh-hm-group-cnt">${fdList.length}</span></td>`
        + `${fdCells}<td class="wh-hm-spark">${fdSpark}</td><td class="wh-hm-delta ${gCls}">${gTxt}</td></tr>`;

      // Внутри округа — подгруппы по кластеру.
      const clMap = new Map();  // cluster (или null) -> [w, ...]
      fdList.forEach(w => {
        const ck = (w.cluster && String(w.cluster).trim()) ? String(w.cluster).trim() : null;
        if (!clMap.has(ck)) clMap.set(ck, []);
        clMap.get(ck).push(w);
      });
      const clKeys = [...clMap.keys()].sort((a, b) => {
        if (a == null) return 1;
        if (b == null) return -1;
        return a.localeCompare(b, 'ru');
      });
      clKeys.forEach(clKey => {
        const clFullId = `cl::${fdId}::${clKey || '__none__'}`;
        const collapsedCl = !!whState.ozCollapsed[clFullId];
        rows += renderClusterGroup(fdKey, clKey, clMap.get(clKey), collapsedCl, fdCollapsed);
      });
    });

    // «Итого за день» — С УЧЁТОМ заморозки (сумма по всем складам), чтобы
    // согласоваться с KPI и подытогами: бэкенд-тоталы занижены на инцидентные склады.
    const totalFrozenByDate = (dt) => d.warehouses.reduce((s, w) => s + (frozenOf(w, dt) || 0), 0);
    const totalCells = dates.map(dt => {
      const vRaw = rubOf(d.totals[dt]);
      const v = totalFrozenByDate(dt);
      if (vRaw == null && v === 0) return `<th class="wh-hm-cell"><span class="muted" style="font-size:10px;">—</span></th>`;
      return `<th class="wh-hm-cell" title="Итого ${whFmtDate(dt)}: ${U.fmtMoney(v)} (${priceLbl})">${whShortNoRub(v)}</th>`;
    }).join('');
    const totalSeq = dates.map(dt => totalFrozenByDate(dt) || 0);
    const totalSpark = whSparkline(totalSeq, 96, 24, '#2f6bff');
    const t0 = totalSeq.length >= 2 ? totalSeq[totalSeq.length - 2] : 0, t1 = totalSeq[totalSeq.length - 1];
    const tDelta = t1 - t0;
    let tTxt = '—', tCls = 'muted';
    if (tDelta > 0) { tTxt = '▲ ' + whShortNoRub(tDelta); tCls = 'pos'; }
    else if (tDelta < 0) { tTxt = '▼ ' + whShortNoRub(Math.abs(tDelta)); tCls = 'neg'; }
    const totalHeadRow = `<tr class="wh-hm-total-head">`
      + `<th class="wh-hm-name l">Итого за день</th>`
      + `${totalCells}<th class="wh-hm-spark">${totalSpark}</th><th class="wh-hm-delta ${tCls}">${tTxt}</th></tr>`;

    const onCls = isSale ? ' on' : '';
    body.innerHTML = `
      <div class="wh-byrow-toolbar">
        <div class="rnp-card-title" style="margin:0;">Стоимость остатков по складам · ${priceLbl}</div>
        <div class="wh-toolbar-right">
          <button class="btn-sm" id="oz-expand-all" type="button" title="Развернуть все округа и кластеры">Развернуть всё</button>
          <button class="btn-sm" id="oz-collapse-all" type="button" title="Свернуть все округа и кластеры">Свернуть всё</button>
          <label class="wh-price-toggle${onCls}" id="oz-price-toggle" title="Снято — стоимость в ценах себестоимости; отмечено — в средних ценах продажи">
            <input type="checkbox" id="oz-price-cb"${isSale ? ' checked' : ''}> в ценах продажи
          </label>
          ${ozPeriodFieldHtml()}
        </div>
      </div>
      ${kpiRow}
      ${nMissing > 0 ? `<div class="wh-alert-bar">⚠ Пропали из последней выгрузки: <b>${(d.missing || []).map(m => U.esc(m.name)).join(', ')}</b>. Проверьте — склад скрыт маркетплейсом или остатки утрачены.</div>` : ''}
      <div class="wh-hm-wrap">
        <table class="wh-hm wh-hm-status">
          <thead>
            <tr><th class="wh-hm-name l">Склад</th>${thDates}<th class="wh-hm-spark">Тренд</th><th class="wh-hm-delta">Δ день</th></tr>
            ${totalHeadRow}
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <div class="wh-legend">▪ Ячейка — стоимость остатка склада (${priceLbl}) · жирность значения = загруженность склада относительно других складов в этот день · «скрыт» = склад пропал из выгрузки (тревога) · <b style="color:#A12C7B;">Инцидент</b> — белые ячейки с рамкой; во время инцидента Ozon прячет остаток — мы <b style="color:#A12C7B;font-style:italic;">замораживаем</b> последнее известное значение (курсив, как у Wildberries) и учитываем его в KPI и итогах · дважды кликните по ячейке-дате, чтобы открыть или закрыть инцидент с этой даты (историчность) · дерево: федеральный округ → кластер → склад · строка «Итого за день» (в шапке) — сумма по всем складам.</div>`;

    const cb = body.querySelector('#oz-price-cb');
    if (cb) cb.addEventListener('change', () => {
      whState.ozPriceMode = cb.checked ? 'sale' : 'cost';
      ozRenderByWh(body);
    });

    // --- Сворачивание округов (уровень 1) и кластеров (уровень 2) ---
    const ozTable = body.querySelector('table.wh-hm');
    const applyFd = (fdId) => {
      const collapsed = !!whState.ozCollapsed[fdId];
      const gr = ozTable.querySelector(`tr.wh-hm-group[data-oz-fd="${CSS.escape(fdId)}"]`);
      if (gr) {
        gr.classList.toggle('is-collapsed', collapsed);
        const tw = gr.querySelector('.wh-hm-tw');
        if (tw) tw.textContent = collapsed ? '▸' : '▾';
      }
      // Все кластеры и склады этого округа скрываются/показываются вместе с ним
      // (склады остаются скрытыми, если сам кластер тоже свёрнут — учитываем при показе).
      ozTable.querySelectorAll(`tr.wh-hm-group-lvl2[data-oz-fd-parent="${CSS.escape(fdId)}"]`).forEach(clRow => {
        clRow.classList.toggle('wh-hm-hidden', collapsed);
        const clId = clRow.getAttribute('data-oz-cl');
        const clCollapsed = collapsed || !!whState.ozCollapsed[clId];
        ozTable.querySelectorAll(`tr.wh-hm-wrow[data-wh-grp-child="${CSS.escape(clId)}"]`)
          .forEach(tr => tr.classList.toggle('wh-hm-hidden', clCollapsed));
      });
    };
    const applyCl = (clId) => {
      const collapsed = !!whState.ozCollapsed[clId];
      const gr = ozTable.querySelector(`tr.wh-hm-group-lvl2[data-oz-cl="${CSS.escape(clId)}"]`);
      if (gr) {
        gr.classList.toggle('is-collapsed', collapsed);
        const tw = gr.querySelector('.wh-hm-tw');
        if (tw) tw.textContent = collapsed ? '▸' : '▾';
      }
      ozTable.querySelectorAll(`tr.wh-hm-wrow[data-wh-grp-child="${CSS.escape(clId)}"]`)
        .forEach(tr => tr.classList.toggle('wh-hm-hidden', collapsed));
    };
    if (ozTable) {
      ozTable.addEventListener('click', (e) => {
        if (!e.target.closest('.wh-hm-name')) return;
        const clRow = e.target.closest('tr.wh-hm-group-lvl2');
        if (clRow) {
          const clId = clRow.getAttribute('data-oz-cl');
          if (!clId) return;
          whState.ozCollapsed[clId] = !whState.ozCollapsed[clId];
          applyCl(clId);
          return;
        }
        const fdRow = e.target.closest('tr.wh-hm-group');
        if (fdRow && ozTable.contains(fdRow)) {
          const fdId = fdRow.getAttribute('data-oz-fd');
          if (!fdId) return;
          whState.ozCollapsed[fdId] = !whState.ozCollapsed[fdId];
          applyFd(fdId);
        }
      });
    }
    const allFdIds = () => [...ozTable.querySelectorAll('tr.wh-hm-group[data-oz-fd]')].map(tr => tr.getAttribute('data-oz-fd'));
    const allClIds = () => [...ozTable.querySelectorAll('tr.wh-hm-group-lvl2[data-oz-cl]')].map(tr => tr.getAttribute('data-oz-cl'));
    const btnExp = body.querySelector('#oz-expand-all');
    if (btnExp) btnExp.addEventListener('click', () => {
      allFdIds().forEach(id => { whState.ozCollapsed[id] = false; });
      allClIds().forEach(id => { whState.ozCollapsed[id] = false; });
      ozRenderByWh(body);
    });
    const btnCol = body.querySelector('#oz-collapse-all');
    if (btnCol) btnCol.addEventListener('click', () => {
      allFdIds().forEach(id => { whState.ozCollapsed[id] = true; });
      allClIds().forEach(id => { whState.ozCollapsed[id] = true; });
      ozRenderByWh(body);
    });

    bindOzIncidentCells(body, () => ozRenderByWh(body));
    bindOzPeriod(body, () => ozRenderByWh(body));
  }

  // Ячейка редактирования кластера (для справочника Ozon) — своё текстовое поле.
  function ozClusterCellHtml(id, cur) {
    return `<input type="text" class="input-sm oz-cl-inp" data-cl-id="${id}" value="${U.esc(cur || '')}" placeholder="—" title="Кластер (можно переопределить вручную)">`;
  }
  // Привязка редактирования кластера: сохранение по blur/Enter.
  function bindOzClusterControls(root, onDone) {
    root.querySelectorAll('.oz-cl-inp').forEach(inp => {
      const save = async () => {
        const id = parseInt(inp.getAttribute('data-cl-id'), 10);
        const val = inp.value.trim();
        try {
          await API.ozSetCluster(id, val || null);
          if (API.cacheClear) API.cacheClear('/api/ozon_warehouses');
          onDone && onDone(id);
        } catch (e) { alert('Ошибка кластера: ' + e.message); }
      };
      inp.addEventListener('blur', save);
      inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); inp.blur(); } });
    });
  }
  // Привязка селекторов округа (Ozon) — копия bindWhDistrictControls с ozSetDistrict.
  function bindOzDistrictControls(root, onDone) {
    root.querySelectorAll('.wh-fd-sel').forEach(sel => {
      sel.addEventListener('change', async () => {
        const id = parseInt(sel.getAttribute('data-fd-id'), 10);
        const fd = sel.value || null;
        try {
          await API.ozSetDistrict(id, fd);
          if (API.cacheClear) API.cacheClear('/api/ozon_warehouses');
          onDone && onDone(id);
        } catch (e) { alert('Ошибка округа: ' + e.message); }
      });
    });
  }

  // ============ Подтаб «Справочник складов» — Ozon (копия whRenderCatalog + колонка «Кластер») ============
  async function ozRenderCatalog(body) {
    body.innerHTML = '<div class="loader"><span class="spinner"></span></div>';
    let d;
    try { d = await API.ozCatalog(); }
    catch (e) { body.innerHTML = `<div class="empty">Ошибка: ${U.esc(e.message)}</div>`; return; }
    const whs = d.warehouses || [];
    const roots = whs.filter(w => w.canonical_id == null);
    const rootOpts = roots.map(r => `<option value="${r.id}">${U.esc(r.name)}</option>`).join('');

    const rows = whs.map(w => {
      const isSyn = w.canonical_id != null;
      let statusBadge;
      if (w.missing) statusBadge = '<span class="wh-badge drop">не поступал</span>';
      else if (!w.is_active) statusBadge = '<span class="wh-badge arch">архив</span>';
      else if (w.days_seen <= 1) statusBadge = '<span class="wh-badge new">новый</span>';
      else statusBadge = '<span class="wh-badge ok">активен</span>';
      const canonCell = isSyn
        ? `<span class="muted">→ ${U.esc(w.canonical_name)}</span> <button class="wh-mini-btn" data-act="unmerge" data-id="${w.id}">расклеить</button>`
        : `<select class="input-sm wh-merge-sel" data-id="${w.id}"><option value="">— самостоятельный —</option>${roots.filter(r => r.id !== w.id).map(r => `<option value="${r.id}">склеить → ${U.esc(r.name)}</option>`).join('')}</select>`;
      return `<tr${w.missing ? ' class="wh-row-drop"' : ''}>
        <td class="l"><span class="wh-cat-name" data-id="${w.id}">${U.esc(w.name)}</span></td>
        <td class="c">${ozClusterCellHtml(w.id, w.cluster)}</td>
        <td class="c wh-cat-fd">${whDistrictCellHtml(w.id, w.federal_district)}</td>
        <td class="l">${canonCell}</td>
        <td class="c">${w.first_seen_date ? whFmtDateFull(w.first_seen_date) : '—'}</td>
        <td class="c">${w.last_seen_date ? whFmtDateFull(w.last_seen_date) : '—'}</td>
        <td class="r">${U.fmtNum(w.tot_qty)}</td>
        <td class="c">${statusBadge}</td>
        <td class="c">
          <button class="wh-mini-btn" data-act="rename" data-id="${w.id}" data-name="${U.esc(w.name)}">переименовать</button>
          <button class="wh-mini-btn" data-act="toggle" data-id="${w.id}" data-active="${w.is_active ? 1 : 0}">${w.is_active ? 'в архив' : 'вернуть'}</button>
        </td>
      </tr>`;
    }).join('');

    body.innerHTML = `
      <div class="rnp-card-title">Справочник складов Ozon</div>
      <div class="wh-legend" style="margin:0 0 14px;">Новые склады заводятся автоматически при загрузке из выгрузки Ozon. Здесь можно переименовать отображаемое имя, задать/переопределить кластер, склеить синонимы (в отчётах — одна строка), отправить в архив. «Не поступал» = склад был, но пропал из последней выгрузки.</div>
      <div class="wh-hm-wrap">
        <table class="wh-cat-tbl">
          <thead><tr><th class="l">Название (из выгрузки Ozon)</th><th>Кластер</th><th>Федеральный округ</th><th class="l">Сопоставление</th><th>Первое появление</th><th>Последняя выгрузка</th><th>Всего шт</th><th>Состояние</th><th>Действия</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;

    bindOzDistrictControls(body, () => ozRenderCatalog(body));
    bindOzClusterControls(body, () => ozRenderCatalog(body));
    body.querySelectorAll('.wh-merge-sel').forEach(sel => {
      sel.addEventListener('change', async () => {
        const id = parseInt(sel.getAttribute('data-id'), 10), canon = parseInt(sel.value, 10);
        if (!canon) return;
        try { await API.ozMerge(id, canon); if (API.cacheClear) API.cacheClear('/api/ozon_warehouses'); ozRenderCatalog(body); }
        catch (e) { alert('Ошибка склейки: ' + e.message); }
      });
    });
    body.querySelectorAll('.wh-mini-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const act = btn.getAttribute('data-act'), id = parseInt(btn.getAttribute('data-id'), 10);
        try {
          if (act === 'unmerge') { await API.ozUnmerge(id); }
          else if (act === 'toggle') { await API.ozSetActive(id, btn.getAttribute('data-active') !== '1'); }
          else if (act === 'rename') {
            const cur = btn.getAttribute('data-name');
            const nn = prompt('Новое отображаемое имя склада:', cur);
            if (nn == null || nn.trim() === '' || nn.trim() === cur) return;
            await API.ozRename(id, nn.trim());
          }
          if (API.cacheClear) API.cacheClear('/api/ozon_warehouses');
          ozRenderCatalog(body);
        } catch (e) { alert('Ошибка: ' + e.message); }
      });
    });
  }

  // ===== Баннер достоверности стоимостной оценки остатков — Ozon (копия whRenderDataQuality) =====
  let _ozDqData = null;
  async function ozRenderDataQuality() {
    const body = document.getElementById('oz-dq-body');
    if (!body) return;
    body.innerHTML = '<div class="loader"><span class="spinner"></span></div>';
    let res;
    try {
      res = await API.ozDataQuality();
    } catch (e) {
      body.innerHTML = '<div class="note err" style="padding:9px 12px;font-size:12.5px;">✖ Не удалось получить данные достоверности. ' + U.esc(e.message) + '</div>';
      return;
    }
    _ozDqData = res;
    if (!res || !res.date) {
      body.innerHTML = '<div class="muted" style="padding:10px 2px;font-size:13px;">Нет загруженных остатков — проверять нечего.</div>';
      return;
    }
    const prob = res.problem_arts || 0;
    const totalArts = res.total_arts || 0;
    const dateStr = U.fmtDate ? U.fmtDate(res.date) : res.date;
    if (prob === 0) {
      body.innerHTML = '<div class="up-warn" style="background:#f0f6f0;border-color:#c5dcc5;color:#2f5a2f;">'
        + '✓ На ' + U.esc(dateStr) + ' все артикулы на остатках (' + U.fmtNum(totalArts) + ') имеют и себестоимость, и среднюю цену. Стоимостная оценка достоверна.'
        + '</div>';
      return;
    }
    const noCostTot = (res.no_cost || 0) + (res.no_both || 0);
    const noPriceTot = (res.no_price || 0) + (res.no_both || 0);
    const parts = [];
    if (noCostTot) parts.push('без себестоимости: <b>' + U.fmtNum(noCostTot) + '</b>');
    if (noPriceTot) parts.push('без средней цены: <b>' + U.fmtNum(noPriceTot) + '</b>');
    const rows = (res.items || []).map(it => {
      const badges = [];
      if (!it.has_cost) badges.push('<span style="display:inline-block;padding:1px 7px;border-radius:9px;background:#f6e6f0;color:#A12C7B;font-size:11px;font-weight:600;">нет с/с</span>');
      if (!it.has_price) badges.push('<span style="display:inline-block;padding:1px 7px;border-radius:9px;background:#fdeede;color:#964219;font-size:11px;font-weight:600;">нет цены</span>');
      return '<tr>'
        + '<td style="padding:5px 10px;border-bottom:1px solid #eee;font-family:monospace;font-size:12.5px;">' + U.esc(it.article) + '</td>'
        + '<td style="padding:5px 10px;border-bottom:1px solid #eee;text-align:right;font-variant-numeric:tabular-nums;">' + U.fmtNum(it.qty) + '</td>'
        + '<td style="padding:5px 10px;border-bottom:1px solid #eee;">' + badges.join(' ') + '</td>'
        + '</tr>';
    }).join('');
    body.innerHTML =
      '<div class="up-warn" style="background:#fdeede;border-color:#f0c9a8;color:#964219;">'
      + '⚠ На ' + U.esc(dateStr) + ': <b>' + U.fmtNum(prob) + '</b> из ' + U.fmtNum(totalArts)
      + ' артикулов на остатках с неполными данными (' + parts.join(', ') + ').'
      + ' Стоимость таких товаров в расчёте занижается (=0).'
      + '</div>'
      + '<div style="display:flex;gap:10px;align-items:center;margin:10px 0 6px;">'
      + '<button class="btn btn-sm" id="oz-dq-toggle" style="font-size:12.5px;">Показать список (' + U.fmtNum(prob) + ')</button>'
      + '<button class="btn btn-sm" id="oz-dq-xlsx" style="font-size:12.5px;">⬇ Выгрузить Excel</button>'
      + '</div>'
      + '<div id="oz-dq-list" style="display:none;max-height:340px;overflow:auto;border:1px solid #eee;border-radius:8px;">'
      + '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
      + '<thead><tr style="position:sticky;top:0;background:#faf9f6;">'
      + '<th style="padding:6px 10px;text-align:left;border-bottom:1px solid #ddd;">Артикул</th>'
      + '<th style="padding:6px 10px;text-align:right;border-bottom:1px solid #ddd;">Остаток, шт</th>'
      + '<th style="padding:6px 10px;text-align:left;border-bottom:1px solid #ddd;">Чего не хватает</th>'
      + '</tr></thead><tbody>' + rows + '</tbody></table>'
      + '</div>';
    const tg = document.getElementById('oz-dq-toggle');
    const lst = document.getElementById('oz-dq-list');
    if (tg && lst) tg.addEventListener('click', () => {
      const open = lst.style.display !== 'none';
      lst.style.display = open ? 'none' : 'block';
      tg.textContent = (open ? 'Показать' : 'Скрыть') + ' список (' + U.fmtNum(prob) + ')';
    });
    const xb = document.getElementById('oz-dq-xlsx');
    if (xb) xb.addEventListener('click', () => ozDqExportXlsx());
  }

  // Выгрузка списка проблемных артикулов Ozon в Excel (копия dqExportXlsx, свой _ozDqData).
  function ozDqExportXlsx() {
    if (!_ozDqData || !(_ozDqData.items || []).length) { App.toast('Нечего выгружать', 'err'); return; }
    let body = '';
    (_ozDqData.items || []).forEach(it => {
      body += '<tr>'
        + '<td>' + U.esc(it.article) + '</td>'
        + '<td>' + (it.qty == null ? '' : it.qty) + '</td>'
        + '<td>' + (it.has_cost ? 'есть' : 'нет') + '</td>'
        + '<td>' + (it.has_price ? 'есть' : 'нет') + '</td>'
        + '</tr>';
    });
    const head = '<tr><th>Артикул</th><th>Остаток, шт</th><th>Себестоимость</th><th>Средняя цена</th></tr>';
    const html = '<html><head><meta charset="utf-8"></head><body><table border="1">' + head + body + '</table></body></html>';
    const blob = new Blob(['\ufeff' + html], { type: 'application/vnd.ms-excel' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'достоверность_остатки_ozon_' + (_ozDqData.date || '') + '.xls';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // ===== Зона загрузки остатков Ozon по складам (копия setupWbStockZone/doUploadWbStock) =====
  function setupOzonWhStockZone(root) {
    const zone = root.querySelector('#uz-whozstock');
    if (!zone) return;
    const input = root.querySelector('#file-whozstock');
    const pick = root.querySelector('#pick-whozstock');
    const dateEl = root.querySelector('#date-whozstock');
    if (dateEl && !dateEl.value) {
      const now = new Date();
      dateEl.value = now.getFullYear() + '-' +
        String(now.getMonth() + 1).padStart(2, '0') + '-' +
        String(now.getDate()).padStart(2, '0');
    }
    if (pick) pick.addEventListener('click', (e) => { e.preventDefault(); input.click(); });
    input.addEventListener('change', () => { if (input.files[0]) doUploadOzonWhStock(input.files[0], zone); });
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('drag'); }));
    zone.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) doUploadOzonWhStock(f, zone); });
  }

  async function doUploadOzonWhStock(file, zone) {
    zone.querySelectorAll('.up-warn, .note.err').forEach(el => el.remove());
    const dateEl = document.getElementById('date-whozstock');
    const stockDate = dateEl ? dateEl.value : '';
    if (!stockDate) {
      App.toast('Сначала укажите дату, за которую загружаются остатки.', 'err');
      return;
    }
    const orig = zone.innerHTML;
    let warnHtml = '', errHtml = '';
    zone.innerHTML = '<div class="loader"><span class="spinner"></span><div style="margin-top:8px">Загрузка ' + U.esc(file.name) + '…</div></div>';
    try {
      const res = await API.uploadOzonWhStock(file, stockDate);
      const period = res.period_text || '';
      const prod = res.rows_products || 0;
      const cells = res.rows_stock_cells || 0;
      const whTotal = res.warehouses_total || 0;
      const whNew = res.warehouses_new || [];
      const clustersTotal = res.clusters_total || 0;
      const artsNew = res.arts_new || 0;
      const artsNewList = res.arts_new_list || [];
      App.toast('✓ Остатки Ozon по складам · товаров: ' + U.fmtNum(prod) + (period ? ' · ' + period : ''), 'ok');
      let extra = ' Ячеек склад×товар: ' + U.fmtNum(cells) + '. Складов: ' + U.fmtNum(whTotal) + '. Кластеров: ' + U.fmtNum(clustersTotal) + '.';
      if (whNew.length) { extra += ' Новых складов: ' + U.fmtNum(whNew.length) + ' (' + whNew.map(U.esc).join(', ') + ').'; }
      if (artsNew) {
        extra += ' Нераспределённые (новые) артикулы: ' + U.fmtNum(artsNew)
          + (artsNewList.length ? ' (' + artsNewList.slice(0, 20).map(U.esc).join(', ') + (artsNewList.length > 20 ? '…' : '') + ')' : '') + '.';
      }
      warnHtml = '<div class="up-warn" style="background:#f0f6f0;border-color:#c5dcc5;color:#2f5a2f;">✓ Записано остатков Ozon по складам за ' + U.esc(period) + ': товаров ' + U.fmtNum(prod) + '.' + extra + '</div>';
    } catch (e) {
      App.toast('Ошибка загрузки: ' + e.message, 'err');
      errHtml = '<div class="note err" style="margin-top:10px;padding:9px 12px;font-size:12.5px;line-height:1.4">✖ Файл не загружен. ' + U.esc(e.message) + '</div>';
    } finally {
      zone.innerHTML = orig;
      setupOzonWhStockZone(document.getElementById('view-root'));
      if (warnHtml || errHtml) { zone.insertAdjacentHTML('beforeend', warnHtml + errHtml); }
      if (document.getElementById('uphist-ozstock')) await loadWhUploadHistory();
    }
  }

  // ============================================================
  //  Раздел «Цены» → подраздел «Прайс-лист»
  // ============================================================
  //  Стилистика полностью повторяет сводный план продаж (mp-cross):
  //  каркас .sp-card.mp-cross, тулбар с .subtabs (подтаб «Загрузка данных»
  //  прижат вправо через .subtab-right), таблица .tbl.sp-tbl без месячной
  //  сетки. Колонки: «Иерархия / Артикул», (опц. «Актуальность» +
  //  «Себестоимость»), «Базовая WB», «Базовая OZON», «Базовая Yandex».
  //  В листе — только артикул (моно), название группы выводится в самой
  //  группе. Отступы уровня — padding-left = depth*15; лист = depth*15+16.
  //  Уровни узлов: L1=1, L2=2, L3=3 (чтобы применялись .sp-grp.lvlN).
  const pricesState = {
    sub: 'pricelist',           // 'pricelist' | 'upload' (заглушка)
    data: null,                 // ответ API.pricelist()
    expanded: {},               // key группы узла в дереве → true
    search: '',                 // поиск по артикулу/названию
    showCost: false,            // галочка «показать себестоимость» (колонка с/с)
    // Период (аналог РНП заказы): ISO 'YYYY-MM-DD'. Пусто = «весь период»
    // (обратная совместимость: универсум = весь catalog_marketplace).
    from: '', to: '',
    _periodInit: false,
    loading: false,
  };

  // ===== Компонент выбора периода прайс-листа (клон bindRnpsPeriod) =====
  // Работает с диапазоном pricesState.from/to (ISO 'YYYY-MM-DD'). Использует
  // те же CSS-классы .rnp-period*/.rnp-cal*, что и РНП заказы.
  function plPeriodLabel() {
    const f = pricesState.from, t = pricesState.to;
    if (!f && !t) return 'Весь период';
    if (f && t) return f === t ? fmtRu(f) : `${fmtRu(f)} — ${fmtRu(t)}`;
    return fmtRu(f || t);
  }
  function plPresetRange(kind) {
    const base = new Date();
    const y = base.getFullYear(), m = base.getMonth();
    let from, to;
    if (kind === 'month') { from = new Date(y, m, 1); to = new Date(y, m + 1, 0); }
    else if (kind === 'quarter') { const q = Math.floor(m / 3); from = new Date(y, q * 3, 1); to = new Date(y, q * 3 + 3, 0); }
    else { from = new Date(y, 0, 1); to = new Date(y, 11, 31); }
    return { from: isoDate(from), to: isoDate(to) };
  }
  function bindPlPeriod(wrap, onApply) {
    const periodWrap = wrap.querySelector('#pl-period');
    const periodFieldEl = wrap.querySelector('#pl-period-field');
    const calEl = wrap.querySelector('#pl-cal');
    if (!periodFieldEl || !calEl) return;

    const MONTHS_RU = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
    const DOW_RU = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
    const cal = { from: '', to: '', view: null, open: false };

    function refreshLabel() {
      const el = periodWrap.querySelector('#pl-period-text');
      if (el) el.textContent = plPeriodLabel();
    }
    function openCal() {
      cal.from = pricesState.from || '';
      cal.to = pricesState.to || '';
      const b = cal.from ? parseISO(cal.from) : new Date();
      cal.view = new Date(b.getFullYear(), b.getMonth(), 1);
      cal.open = true;
      calEl.hidden = false;
      periodFieldEl.classList.add('open');
      drawCal();
    }
    function closeCal() {
      cal.open = false;
      calEl.hidden = true;
      periodFieldEl.classList.remove('open');
    }
    function applyPeriod() {
      pricesState.from = cal.from || '';
      pricesState.to = cal.to || '';
      refreshLabel();
      closeCal();
      if (onApply) onApply();
    }
    function monthHtml(first) {
      const y = first.getFullYear(), mo = first.getMonth();
      const title = `${MONTHS_RU[mo]} ${y}`;
      let lead = first.getDay() - 1; if (lead < 0) lead = 6;
      const daysIn = new Date(y, mo + 1, 0).getDate();
      const f = cal.from, t = cal.to;
      const lo = (f && t) ? (f <= t ? f : t) : f;
      const hi = (f && t) ? (f <= t ? t : f) : f;

      function dayCell(dt, adjacent, posInRow) {
        const iso = isoDate(dt);
        let c = 'rnp-cal-cell day';
        if (adjacent) c += ' adjacent';
        let inRange = false, isEnd = false;
        if (lo && hi) {
          if (iso === lo || iso === hi) { isEnd = true; }
          else if (iso > lo && iso < hi) { inRange = true; }
        } else if (f && iso === f) { isEnd = true; }
        if (inRange) c += ' in-range';
        if (isEnd) c += ' end';
        if (inRange || isEnd) {
          if (iso === lo || posInRow === 0) c += ' band-l';
          if (iso === hi || posInRow === 6) c += ' band-r';
        }
        return `<button type="button" class="${c}" data-cal-day="${iso}"><span class="d">${dt.getDate()}</span></button>`;
      }

      let cells = '', pos = 0;
      for (let i = lead; i > 0; i--) {
        cells += dayCell(new Date(y, mo, 1 - i), true, pos); pos = (pos + 1) % 7;
      }
      for (let d = 1; d <= daysIn; d++) {
        cells += dayCell(new Date(y, mo, d), false, pos); pos = (pos + 1) % 7;
      }
      const fill = 42 - (lead + daysIn);
      for (let i = 1; i <= fill; i++) {
        cells += dayCell(new Date(y, mo + 1, i), true, pos); pos = (pos + 1) % 7;
      }
      return `<div class="rnp-cal-month">
        <div class="rnp-cal-mtitle">${title}</div>
        <div class="rnp-cal-dow">${DOW_RU.map(x => `<span>${x}</span>`).join('')}</div>
        <div class="rnp-cal-grid">${cells}</div>
      </div>`;
    }
    function drawCal() {
      const m1 = new Date(cal.view.getFullYear(), cal.view.getMonth(), 1);
      const m2 = new Date(cal.view.getFullYear(), cal.view.getMonth() + 1, 1);
      calEl.innerHTML = `
        <div class="rnp-cal-nav">
          <button type="button" class="rnp-cal-arrow" data-cal-nav="-1" title="Предыдущий месяц">‹</button>
          <button type="button" class="rnp-cal-arrow" data-cal-nav="1" title="Следующий месяц">›</button>
        </div>
        <div class="rnp-cal-months">${monthHtml(m1)}${monthHtml(m2)}</div>
        <div class="rnp-cal-presets">
          <button type="button" class="rnp-cal-preset" data-cal-preset="month">Тек. месяц</button>
          <button type="button" class="rnp-cal-preset" data-cal-preset="quarter">Тек. квартал</button>
          <button type="button" class="rnp-cal-preset" data-cal-preset="year">Тек. год</button>
          <button type="button" class="rnp-cal-preset" data-cal-preset="all">Весь период</button>
        </div>`;
    }

    periodFieldEl.addEventListener('click', (e) => {
      e.stopPropagation();
      cal.open ? closeCal() : openCal();
    });
    calEl.addEventListener('click', (e) => {
      e.stopPropagation();
      const nav = e.target.closest('[data-cal-nav]');
      if (nav) {
        const delta = Number(nav.getAttribute('data-cal-nav'));
        cal.view = new Date(cal.view.getFullYear(), cal.view.getMonth() + delta, 1);
        drawCal();
        return;
      }
      const pre = e.target.closest('[data-cal-preset]');
      if (pre) {
        const kind = pre.getAttribute('data-cal-preset');
        if (kind === 'all') { cal.from = ''; cal.to = ''; }
        else { const r = plPresetRange(kind); cal.from = r.from; cal.to = r.to; }
        applyPeriod();
        return;
      }
      const day = e.target.closest('[data-cal-day]');
      if (day) {
        const iso = day.getAttribute('data-cal-day');
        if (!cal.from || (cal.from && cal.to)) {
          cal.from = iso; cal.to = '';
          drawCal();
        } else {
          if (iso < cal.from) { cal.to = cal.from; cal.from = iso; }
          else { cal.to = iso; }
          applyPeriod();
        }
      }
    });
    document.addEventListener('click', (e) => {
      if (cal.open && periodWrap && !periodWrap.contains(e.target)) closeCal();
    });
  }

  // Формат целой цены без знака валюты (1234 → «1 234»).
  function pricesFmt(v) {
    if (v === null || v === undefined || v === '') return '';
    const n = Number(v);
    if (!isFinite(n)) return '';
    return U.fmtNum(Math.round(n));
  }

  // Строим дерево L1→L2→L3→товары из плоского items[].
  // Уровни: L1=1, L2=2, L3=3 — как в референсе «Продажи, шт» (там 1..3),
  // чтобы .sp-grp.lvl1/lvl2/lvl3 применялись напрямую.
  // Нераспределённые (l1===null) — в отдельной группе ключ __UNASSIGNED__
  // (уровень 1), которая рисуется в самом конце.
  // Единые правила порядка групп (зеркало app/util.py, согласовано 2026-09-11):
  //   L1 — фиксированный список: ECOM, затем ТД «АВТОПРОФИ», прочие — после
  //        них по алфавиту;
  //   L2/L3 — по алфавиту; артикулы — по seller_article.
  const PL_L1_FIXED_ORDER = ['ECOM', 'АВТОПРОФИ'];

  function plL1Rank(name) {
    const s = (name || '').trim().toUpperCase();
    for (let i = 0; i < PL_L1_FIXED_ORDER.length; i++) {
      if (s.indexOf(PL_L1_FIXED_ORDER[i]) !== -1) return i;
    }
    return PL_L1_FIXED_ORDER.length;
  }

  // Сравнение дочерних узлов: на первом уровне — фиксированный порядок,
  // глубже — алфавит (ru).
  function plCompareNodes(a, b) {
    if (a.level === 1 && b.level === 1) {
      const d = plL1Rank(a.label) - plL1Rank(b.label);
      if (d !== 0) return d;
    }
    return (a.label || '').localeCompare(b.label || '', 'ru');
  }

  function pricesBuildTree(items) {
    const root = { key: '__root__', label: 'Итоги', level: 0, children: {}, leaves: [] };
    const unassigned = { key: '__UNASSIGNED__', label: 'Не распределены по группам', level: 1, children: {}, leaves: [] };

    for (const it of items) {
      const l1 = it.l1, l2 = it.l2, l3 = it.l3;
      if (!l1) { unassigned.leaves.push(it); continue; }
      const k1 = l1;
      let n1 = root.children[k1];
      if (!n1) { n1 = { key: 'L1:' + k1, label: l1, level: 1, children: {}, leaves: [] }; root.children[k1] = n1; }
      if (!l2) { n1.leaves.push(it); continue; }
      const k2 = k1 + '|' + l2;
      let n2 = n1.children[k2];
      if (!n2) { n2 = { key: 'L2:' + k2, label: l2, level: 2, children: {}, leaves: [] }; n1.children[k2] = n2; }
      if (!l3) { n2.leaves.push(it); continue; }
      const k3 = k2 + '|' + l3;
      let n3 = n2.children[k3];
      if (!n3) { n3 = { key: 'L3:' + k3, label: l3, level: 3, children: {}, leaves: [] }; n2.children[k3] = n3; }
      n3.leaves.push(it);
    }
    return { root, unassigned };
  }

  // Сколько товаров в поддереве (рекурсивно).
  function pricesCountLeaves(node) {
    let n = node.leaves.length;
    for (const k in node.children) n += pricesCountLeaves(node.children[k]);
    return n;
  }

  // Фильтр по поисковой строке. Пустой q → все. Сравнение по артикулу или sample_name.
  function pricesFilterItems(items, q) {
    q = (q || '').trim().toLowerCase();
    if (!q) return items;
    return items.filter(it =>
      (it.seller_article || '').toLowerCase().includes(q) ||
      (it.sample_name    || '').toLowerCase().includes(q)
    );
  }

  // Ячейка редактируемой цены — оборачиваем в .sp-cell/.sp-plan, как в плане продаж:
  // пустая → бледный «–» с курсором cell; заполненная → синий полужирный.
  function pricesPriceCellHtml(art, mp, val) {
    const empty = (val === null || val === undefined || val === '');
    const dataVal = empty ? '' : String(val);
    let planCls = 'sp-plan sp-plan-edit';
    let planInner = empty ? '–' : pricesFmt(val);
    let title = empty ? 'Двойной клик — задать цену' : 'Двойной клик — изменить цену';
    if (empty) planCls += ' sp-plan-empty';
    return (
      '<td class="sp-mth">' +
        '<div class="sp-cell sp-cell-single">' +
          '<span class="' + planCls + '" title="' + title + '" ' +
                 'data-art="' + U.esc(art) + '" data-mp="' + mp + '" data-val="' + U.esc(dataVal) + '">' +
            planInner +
          '</span>' +
        '</div>' +
      '</td>'
    );
  }

  // Ячейка себестоимости — только чтение (с/с грузится в разделе «Склады» →
  // «Отчёты общие»). Под значением — дата актуальности среза: это start_date
  // записи в item_cost_hist, т.е. «дата действия цен», указанная при загрузке
  // файла себестоимости. Берётся срез, актуальный на конец периода отчёта.
  function pricesCostCellHtml(it) {
    const v = it.cost;
    const empty = (v === null || v === undefined || v === '');
    const val = empty ? '–' : pricesFmt(Math.round(v * 100) / 100);
    const dt = it.cost_date ? fmtRu(it.cost_date) : '';
    return (
      '<td class="sp-mth sp-cost-dt">' +
        '<span class="sp-cost-date"' + (dt ? ' title="Себестоимость действует с этой даты"' : '') + '>' +
          (dt ? U.esc(dt) : '–') +
        '</span>' +
      '</td>' +
      '<td class="sp-mth sp-cost">' +
        '<span class="sp-cost-val' + (empty ? ' sp-plan-empty' : '') + '">' + val + '</span>' +
      '</td>'
    );
  }

  // Ячейка Pi (прайс-индекс) — отношение базовой цены МП к базовой цене
  // Wildberries. 1,25 = цена на этом МП на 25% дороже, чем на WB.
  // Только чтение, считается на фронте из уже загруженных базовых цен.
  function pricesPiVal(base, val) {
    const b = Number(base), v = Number(val);
    if (!isFinite(b) || !isFinite(v) || b <= 0 || v <= 0) return null;
    return v / b;
  }

  function pricesPiFmt(k) {
    if (k === null) return '–';
    return (Math.round(k * 100) / 100).toFixed(2).replace('.', ',');
  }

  function pricesPiCellHtml(base, val) {
    const k = pricesPiVal(base, val);
    return (
      '<td class="sp-mth sp-pi">' +
        '<span class="sp-pi-val' + (k === null ? ' sp-plan-empty' : '') + '"' +
              (k === null ? '' : ' title="Прайс-индекс: отношение к базовой цене Wildberries"') + '>' +
          pricesPiFmt(k) +
        '</span>' +
      '</td>'
    );
  }

  // Пересчёт Pi в одной строке после inline-правки любой из базовых цен
  // (правка WB меняет оба индекса, поэтому обновляем строку целиком).
  function pricesRepaintRowPi(tblHost, art) {
    if (!tblHost || !pricesState.data || !pricesState.data.items) return;
    const it = pricesState.data.items.find(x => x.seller_article === art);
    if (!it) return;
    const tr = tblHost.querySelector('tr.sp-leaf[data-art="' + (window.CSS && CSS.escape ? CSS.escape(art) : art) + '"]');
    if (!tr) return;
    const cells = tr.querySelectorAll('td.sp-pi .sp-pi-val');
    const ks = [pricesPiVal(it.price_wb, it.price_ozon), pricesPiVal(it.price_wb, it.price_ya)];
    cells.forEach((el, i) => {
      el.textContent = pricesPiFmt(ks[i]);
      el.className = 'sp-pi-val' + (ks[i] === null ? ' sp-plan-empty' : '');
    });
  }

  // Строка-группа (L1/L2/L3 или «Не распределены»).
  // Стиль полностью повторяет sp-row.sp-group.lvlN из «Продажи, шт»:
  //   padding-left = (level-1) * 15 → 0, 15, 30 px для L1/L2/L3.
  function pricesRenderGroupRow(node) {
    const expanded = !!pricesState.expanded[node.key];
    const arrow = '<span class="sp-exp">' + (expanded ? '▾' : '▸') + '</span>';
    const indent = (node.level - 1) * 15;
    return (
      '<tr class="sp-row sp-group lvl' + node.level + ' sp-has" data-key="' + U.esc(node.key) + '" style="cursor:pointer">' +
        '<td class="sp-name sp-grp lvl' + node.level + '" style="padding-left:' + indent + 'px">' +
          arrow + U.esc(node.label) +
        '</td>' +
        (pricesState.showCost ? '<td class="sp-mth sp-cost-dt"></td><td class="sp-mth sp-cost"></td>' : '') +
        '<td class="sp-mth"></td>' +
        '<td class="sp-mth sp-pi"></td>' +
        '<td class="sp-mth"></td>' +
        '<td class="sp-mth sp-pi"></td>' +
        '<td class="sp-mth"></td>' +
      '</tr>'
    );
  }

  // Строка-товар (лист). Первая колонка — sp-name sp-art (моно артикул) с
  // отступом уровня; далее пять колонок: WB, Pi(Ozon), Ozon, Pi(Yandex), Yandex.
  // Pi ставится ПЕРЕД значением соответствующего МП — так лучше читается,
  // чем «цена | Pi | цена | Pi»: сначала индекс, следом сама цена.
  // parentLevel — уровень родителя (1/2/3); отступ листа = parentLevel*15 + 16.
  function pricesRenderLeafRow(it, parentLevel) {
    const indent = parentLevel * 15 + 16;
    return (
      '<tr class="sp-row sp-leaf" data-art="' + U.esc(it.seller_article) + '">' +
        '<td class="sp-name sp-art" style="padding-left:' + indent + 'px">' + U.esc(it.seller_article) + '</td>' +
        (pricesState.showCost ? pricesCostCellHtml(it) : '') +
        pricesPriceCellHtml(it.seller_article, 'wb',   it.price_wb) +
        pricesPiCellHtml(it.price_wb, it.price_ozon) +
        pricesPriceCellHtml(it.seller_article, 'ozon', it.price_ozon) +
        pricesPiCellHtml(it.price_wb, it.price_ya) +
        pricesPriceCellHtml(it.seller_article, 'ya',   it.price_ya) +
      '</tr>'
    );
  }

  // Рекурсивный вывод узла: сам узел + (если развёрнут) его потомки и листья.
  // Корень (level 0) не рендерится как строка — служит контейнером для L1.
  function pricesRenderNode(node, opts, out) {
    const renderGroup = (opts && opts.renderGroup) || pricesRenderGroupRow;
    const renderLeaf  = (opts && opts.renderLeaf)  || pricesRenderLeafRow;
    if (node.level >= 1) out.push(renderGroup(node));
    const expanded = (node.level === 0) ? true : (!!pricesState.expanded[node.key] || opts.forceExpanded);
    if (!expanded) return;
    const childNodes = Object.keys(node.children).map(k => node.children[k]).sort(plCompareNodes);
    for (const ch of childNodes) pricesRenderNode(ch, opts, out);
    const leaves = node.leaves.slice().sort((a, b) => (a.seller_article || '').localeCompare(b.seller_article || '', 'ru'));
    // parentLevel = 0 для листьев корня (не бывает), иначе level текущего узла.
    const parentLevel = Math.max(1, node.level);
    for (const it of leaves) out.push(renderLeaf(it, parentLevel));
  }

  // Перерисовка таблицы прайс-листа (без запроса к API).
  function pricesPaintTable(host) {
    if (!pricesState.data) return;
    const q = pricesState.search || '';
    const filtered = pricesFilterItems(pricesState.data.items || [], q);
    const { root, unassigned } = pricesBuildTree(filtered);

    // При активном поиске — все узлы развёрнуты.
    const opts = { forceExpanded: !!q.trim() };

    const out = [];
    pricesRenderNode(root, opts, out);
    if (pricesCountLeaves(unassigned) > 0) pricesRenderNode(unassigned, opts, out);

    if (!out.length) {
      host.innerHTML = '<div class="empty">Ничего не найдено' + (q ? ' по запросу «' + U.esc(q) + '»' : '') + '.</div>';
      return;
    }

    host.innerHTML =
      '<div class="tbl-wrap sp-wrap">' +
        '<table class="tbl sp-tbl">' +
          '<thead><tr>' +
            '<th class="sp-name" rowspan="2">Иерархия / Артикул</th>' +
            (pricesState.showCost
               ? '<th class="sp-mth sp-cost-dt" rowspan="2">Актуальность</th>' +
                 '<th class="sp-mth sp-cost" rowspan="2">Себестоимость</th>'
               : '') +
            '<th class="sp-mth sp-hgrp" colspan="5">Базовая цена</th>' +
          '</tr>' +
          '<tr class="sp-hrow2">' +
            '<th class="sp-mth sp-th-wb">Wildberries</th>' +
            '<th class="sp-mth sp-pi" title="Прайс-индекс: отношение базовой цены Ozon к базовой цене Wildberries">Pi</th>' +
            '<th class="sp-mth sp-th-ozon">Ozon</th>' +
            '<th class="sp-mth sp-pi" title="Прайс-индекс: отношение базовой цены Yandex к базовой цене Wildberries">Pi</th>' +
            '<th class="sp-mth sp-th-ya">Yandex</th>' +
          '</tr></thead>' +
          '<tbody>' + out.join('') + '</tbody>' +
        '</table>' +
      '</div>';
    pricesSyncToolbarHeight();
  }

  // ── Подраздел «Индекс цен» ────────────────────────────────────────────
  // Отдельный отчёт с той же структурой дерева и теми же товарами. Колонки
  // «Базовая цена» и Pi формируются иначе, чем в «Прайс-листе» — их логику
  // подключим отдельным шагом (сейчас — заглушки «–»). Инлайн-правки нет.
  function pricesIdxRenderGroupRow(node) {
    const expanded = !!pricesState.expanded[node.key];
    const arrow = '<span class="sp-exp">' + (expanded ? '▾' : '▸') + '</span>';
    const indent = (node.level - 1) * 15;
    return (
      '<tr class="sp-row sp-group lvl' + node.level + ' sp-has" data-key="' + U.esc(node.key) + '" style="cursor:pointer">' +
        '<td class="sp-name sp-grp lvl' + node.level + '" style="padding-left:' + indent + 'px">' +
          arrow + U.esc(node.label) +
        '</td>' +
        '<td class="sp-mth"></td>' +
        '<td class="sp-mth sp-pi"></td>' +
        '<td class="sp-mth"></td>' +
        '<td class="sp-mth"></td>' +
        '<td class="sp-mth"></td>' +
        '<td class="sp-mth sp-pibuy"></td>' +
        '<td class="sp-mth"></td>' +
        '<td class="sp-mth sp-pi"></td>' +
        '<td class="sp-mth"></td>' +
        '<td class="sp-mth"></td>' +
        '<td class="sp-mth"></td>' +
      '</tr>'
    );
  }
  // Вспомогалки блока WB в «Индексе цен».
  // Источник — mp_price_daily: последний известный срез (upload_price + spp_pct + buyer_price).
  function _idxWbAup(it) { return (it.wb_upload_price != null) ? it.wb_upload_price : null; }
  function _idxOzAup(it) { return (it.oz_upload_price != null) ? it.oz_upload_price : null; }
  function _idxOzBuyer(it) { return (it.oz_buyer_price != null) ? it.oz_buyer_price : null; }
  function _idxWbBuyer(it) { return (it.wb_buyer_price != null) ? it.wb_buyer_price : null; }
  // Признак принадлежности товара к группе «Продукция ТД "АВТОПРОФИ"».
  // Совпадает с правилом в app/util.py::l1_rank: подстрока «АВТОПРОФИ»
  // в верхнем регистре в названии L1 — устойчиво к разным кавычкам
  // («Продукция ТД "АВТОПРОФИ"» vs «Продукция ТД «АВТОПРОФИ»»).
  function _idxIsAvtoprofi(it) {
    const s = ((it && it.l1) || '').toUpperCase();
    return s.indexOf('АВТОПРОФИ') !== -1;
  }
  function _idxOzPi(it) {
    const base = it.price_ozon;
    if (base == null || !(base > 0)) return null;
    // Для группы «Продукция ТД "АВТОПРОФИ"» Pi считается по цене покупателя,
    // а не по загружаемой (согласовано 2026-09-14).
    const num = _idxIsAvtoprofi(it) ? _idxOzBuyer(it) : _idxOzAup(it);
    if (num == null) return null;
    return num / base;
  }
  function _idxWbPi(it) {
    const base = it.price_wb;
    if (base == null || !(base > 0)) return null;
    const num = _idxIsAvtoprofi(it) ? _idxWbBuyer(it) : _idxWbAup(it);
    if (num == null) return null;
    return num / base;
  }
  // Pi покупатель — отношение цены для покупателя Ozon к такой же цене WB.
  // Отдельный блок между WB и Ozon; раскраска не применяется.
  function _idxBuyerPi(it) {
    const oz = _idxOzBuyer(it), wb = _idxWbBuyer(it);
    if (oz == null || wb == null || !(wb > 0)) return null;
    return oz / wb;
  }
  function _idxRub(v) {
    if (v == null) return '<span class="sp-plan sp-plan-empty">–</span>';
    // Целые рубли, разделитель тысяч — тонкий пробел (как в «Прайс-листе»).
    return '<span class="sp-plan">' + U.fmtNum(Math.round(v)) + '</span>';
  }
  // Формат СПП: один знак после запятой, разделитель — запятая («34,2%»).
  function _fmtSppPct(v) {
    return (Math.round(v * 1000) / 10).toFixed(1).replace('.', ',');
  }
  function _idxPct(v) {
    if (v == null) return '<span class="sp-plan sp-plan-empty">–</span>';
    return '<span class="sp-plan">' + _fmtSppPct(v) + '%</span>';
  }
  // Аналогично _idxPct, но с маркером «≈», если СПП взята fallback'ом
  // (spp_is_estimated=true). Маркер — АБСОЛЮТНО позиционированный СЛЕВА:
  // число в столбце выровнено строго по правому краю, маркер не сдвигает число.
  function _idxPctSpp(v, isEst) {
    if (v == null) return '<span class="sp-plan sp-plan-empty">–</span>';
    const pctStr = _fmtSppPct(v);
    if (isEst) {
      return '<span class="sp-plan sp-plan-est" title="СПП оценочная: взята из ближайшего известного дня">' +
        '<span class="sp-spp-est" aria-hidden="true">≈</span>' + pctStr + '%</span>';
    }
    return '<span class="sp-plan">' + pctStr + '%</span>';
  }
  function _idxPi(v) {
    if (v == null) return '<span class="sp-pi-val sp-plan-empty">–</span>';
    // Пороги по отображаемому (округлённому) значению:
    // <1,00 — красный, 1,00..1,10 — чёрный, >1,10 — синий.
    const shown = Math.round(v * 100) / 100;
    var cls = 'sp-pi-val';
    if (shown < 1.00) cls += ' sp-pi-lo';
    else if (shown > 1.10) cls += ' sp-pi-hi';
    return '<span class="' + cls + '">' + shown.toFixed(2).replace('.', ',') + '</span>';
  }
  // Pi для группы «Продукция ТД "АВТОПРОФИ"». Формула другая
  // (Цена покупателя / Базовая), пороги другие:
  //   <1,15 — красный, >=1,15 — чёрный. Синего нет.
  // Тултип поясняет отличие от общей формулы Pi.
  function _idxPiAvtoprofi(v) {
    const tip = 'Pi = Цена для покупателя / Базовая. Для продукции ТД «АВТОПРОФИ» считается по цене покупателя, а не по загружаемой.';
    if (v == null) return '<span class="sp-pi-val sp-plan-empty" title="' + tip + '">–</span>';
    const shown = Math.round(v * 100) / 100;
    var cls = 'sp-pi-val';
    if (shown < 1.15) cls += ' sp-pi-lo';
    return '<span class="' + cls + '" title="' + tip + '">' + shown.toFixed(2).replace('.', ',') + '</span>';
  }
  // Формат Pi для столбца «Pi покупатель».
  // Порог: если коэффициент меньше 1,03 — выделяем цену красным (класс sp-pi-lo),
  // тот же, что использует _idxPi для Pi < 1,00. Порога сверху нет.
  function _idxPiPlain(v) {
    if (v == null) return '<span class="sp-pi-val sp-plan-empty">–</span>';
    const shown = Math.round(v * 100) / 100;
    var cls = 'sp-pi-val';
    if (shown < 1.03) cls += ' sp-pi-lo';
    return '<span class="' + cls + '">' + shown.toFixed(2).replace('.', ',') + '</span>';
  }

  function pricesIdxRenderLeafRow(it, parentLevel) {
    const indent = parentLevel * 15 + 16;
    return (
      '<tr class="sp-row sp-leaf" data-art="' + U.esc(it.seller_article) + '">' +
        '<td class="sp-name sp-art" style="padding-left:' + indent + 'px">' + U.esc(it.seller_article) + '</td>' +
        // ── Wildberries (слева) ──
        '<td class="sp-mth"><div class="sp-cell sp-cell-single">' + _idxRub(it.price_wb) + '</div></td>' +
        '<td class="sp-mth sp-pi">' + (_idxIsAvtoprofi(it) ? _idxPiAvtoprofi(_idxWbPi(it)) : _idxPi(_idxWbPi(it))) + '</td>' +
        '<td class="sp-mth"><div class="sp-cell sp-cell-single">' + _idxRub(_idxWbAup(it)) + '</div></td>' +
        '<td class="sp-mth"><div class="sp-cell sp-cell-single">' + _idxPctSpp(it.wb_spp_pct, it.wb_spp_is_estimated) + '</div></td>' +
        '<td class="sp-mth"><div class="sp-cell sp-cell-single">' + _idxRub(_idxWbBuyer(it)) + '</div></td>' +
        // ── Pi покупатель: отдельный блок-разделитель между WB и Ozon ──
        '<td class="sp-mth sp-pibuy">' + _idxPiPlain(_idxBuyerPi(it)) + '</td>' +
        // ── Ozon (справа) ──
        '<td class="sp-mth"><div class="sp-cell sp-cell-single">' + _idxRub(it.price_ozon) + '</div></td>' +
        '<td class="sp-mth sp-pi">' + (_idxIsAvtoprofi(it) ? _idxPiAvtoprofi(_idxOzPi(it)) : _idxPi(_idxOzPi(it))) + '</td>' +
        '<td class="sp-mth"><div class="sp-cell sp-cell-single">' + _idxRub(_idxOzAup(it)) + '</div></td>' +
        '<td class="sp-mth"><div class="sp-cell sp-cell-single">' + _idxPctSpp(it.oz_spp_pct, it.oz_spp_is_estimated) + '</div></td>' +
        '<td class="sp-mth"><div class="sp-cell sp-cell-single">' + _idxRub(_idxOzBuyer(it)) + '</div></td>' +
      '</tr>'
    );
  }
  function pricesIdxPaintTable(host) {
    if (!pricesState.data) return;
    const q = pricesState.search || '';
    const filtered = pricesFilterItems(pricesState.data.items || [], q);
    const { root, unassigned } = pricesBuildTree(filtered);
    const opts = {
      forceExpanded: !!q.trim(),
      renderGroup: pricesIdxRenderGroupRow,
      renderLeaf:  pricesIdxRenderLeafRow,
    };
    const out = [];
    pricesRenderNode(root, opts, out);
    if (pricesCountLeaves(unassigned) > 0) pricesRenderNode(unassigned, opts, out);
    if (!out.length) {
      host.innerHTML = '<div class="empty">Ничего не найдено' + (q ? ' по запросу «' + U.esc(q) + '»' : '') + '.</div>';
      return;
    }
    host.innerHTML =
      '<div class="tbl-wrap sp-wrap">' +
        '<table class="tbl sp-tbl">' +
          '<thead><tr>' +
            '<th class="sp-name" rowspan="2">Иерархия / Артикул</th>' +
            '<th class="sp-mth sp-hgrp sp-th-wb" colspan="5">Wildberries</th>' +
            '<th class="sp-mth sp-pibuy sp-pibuy-h" rowspan="2" title="Pi покупатель = Цена для покупателя Ozon / Цена для покупателя Wildberries">Pi покупатель</th>' +
            '<th class="sp-mth sp-hgrp sp-th-oz" colspan="5">Ozon</th>' +
          '</tr>' +
          '<tr class="sp-hrow2">' +
            '<th class="sp-mth" title="Базовая цена Wildberries из подраздела «Прайс-лист»">Базовая</th>' +
            '<th class="sp-mth sp-pi" title="Pi = Загружаемая Wildberries / Базовая Wildberries; для продукции ТД АВТОПРОФИ — Цена для покупателя / Базовая">Pi</th>' +
            '<th class="sp-mth" title="Цена со скидкой (загружаемая) из последнего отчёта WB на конец периода">Загружаемая</th>' +
            '<th class="sp-mth" title="СПП из отчёта WB; при нулевой или отсутствующей СПП товар отсутствует, показывается прочерк">СПП</th>' +
            '<th class="sp-mth" title="Цена на витрине (с СПП) из отчёта WB; при нулевой или отсутствующей СПП показывается прочерк">Для покупателя</th>' +
            '<th class="sp-mth sp-th-oz" title="Базовая цена Ozon из подраздела «Прайс-лист»">Базовая</th>' +
            '<th class="sp-mth sp-pi" title="Pi = Загружаемая Ozon / Базовая Ozon; для продукции ТД АВТОПРОФИ — Цена для покупателя / Базовая">Pi</th>' +
            '<th class="sp-mth" title="Цена продавца после акций из последнего отчёта Ozon на конец периода">Загружаемая</th>' +
            '<th class="sp-mth" title="СПП/соинвест из отчёта Ozon; при нулевом или отсутствующем соинвесте товар отсутствует, показывается прочерк">СПП</th>' +
            '<th class="sp-mth" title="Цена продавца с соинвестом из отчёта Ozon; при нулевом или отсутствующем соинвесте показывается прочерк">Для покупателя</th>' +
          '</tr></thead>' +
          '<tbody>' + out.join('') + '</tbody>' +
        '</table>' +
      '</div>';
    pricesSyncToolbarHeight();
  }

  // «Загрузка данных» в разделе «Цены»: две карточки загрузки (Ozon/WB) + журнал.
  // Стиль — точно как в «РНП заказы → Загрузка данных»: .card + .upload-zone,
  // драг&дроп, ссылка «выберите файл», журнал — .tbl-wrap > .tbl, mpPill.
  function pricesRenderUploadTab(host) {
    host.innerHTML = ''
      + '<div class="grid-2" style="padding:16px 22px 0;">'
      +   _plUploadCard('ozon', 'Ozon — загрузка цен',
            'Отчёт «Цены и соинвест», .xlsx · дата, артикул, цена продавца после акций, соинвест и цена с соинвестом',
            'Даты берутся из файла. Можно загрузить несколько дней: совпавшие дата и артикул будут перезаписаны. Разные значения одной даты и артикула внутри файла отклоняются. При нулевом или пустом соинвесте товар отсутствует: соинвест и цена для покупателя отображаются прочерками.')
      +   _plUploadCard('wb',   'Wildberries — загрузка цен',
            'Отчёт «Цены с СПП», .xlsx · дата, артикул продавца, загружаемая цена, цена на витрине и СПП',
            'Даты берутся из файла. Можно загрузить несколько дней: совпавшие дата и артикул будут перезаписаны. Внутри дня берётся самое позднее время. При нулевой или пустой СПП товар отсутствует: СПП и цена для покупателя отображаются прочерками.')
      + '</div>'
      + '<div class="card" style="margin:14px 22px 16px;">'
      +   '<h3 style="margin:0 0 10px;">Журнал загрузок</h3>'
      +   '<div id="pl-upload-log"><div class="loader"><span class="spinner"></span></div></div>'
      + '</div>';

    _plSetupUploadZone(host, 'ozon');
    _plSetupUploadZone(host, 'wb');
    _reloadPlUploadLog();
  }

  function _plUploadCard(mp, title, hint, where) {
    const sourceUrl = mp === 'ozon'
      ? 'https://docs.google.com/spreadsheets/d/1y36a1MvDWbGeVx5oU_zAvEqHxANQJwLx2zrQnhazK90/edit?gid=1920079832#gid=1920079832'
      : 'https://docs.google.com/spreadsheets/d/10DOSqL_q639vD6--XscYWqwrDAWXFTSuCHHjB3-wUoI/edit?pli=1&gid=781930537#gid=781930537';
    const sourceLabel = mp === 'ozon' ? 'Таблица Ozon' : 'Таблица Wildberries';
    return ''
      + '<div class="card">'
      +   '<h3>' + U.esc(title) + '</h3>'
      +   '<div class="upload-zone" id="pl-uz-' + mp + '">'
      +     '<div style="font-size:28px;">⬆️</div>'
      +     '<div>Перетащите Excel сюда или <a href="#" id="pl-pick-' + mp + '">выберите файл</a></div>'
      +     '<div class="muted" style="font-size:12px;margin-top:6px;">' + U.esc(hint) + '</div>'
      +     '<input type="file" id="pl-file-' + mp + '" accept=".xlsx" class="hidden">'
      +   '</div>'
      +   '<div class="muted fact-date-hint">' + U.esc(where) + '</div>'
      +   '<div class="pl-data-source" style="margin-top:10px;font-size:12px;line-height:1.5;">'
      +     'Где взять данные: <a href="' + U.esc(sourceUrl) + '" target="_blank" rel="noopener noreferrer"'
      +     ' title="Открыть в новой вкладке. Скачайте таблицу в формате Excel (.xlsx) и загрузите сюда.">'
      +     U.esc(sourceLabel) + '</a>'
      +   '</div>'
      + '</div>';
  }

  // Подвязка зоны: клик по ссылке → выбор файла; drag&drop; change → upload.
  function _plSetupUploadZone(root, mp) {
    const zone  = root.querySelector('#pl-uz-'   + mp);
    if (!zone) return;
    const input = root.querySelector('#pl-file-' + mp);
    const pick  = root.querySelector('#pl-pick-' + mp);
    if (pick)  pick.addEventListener('click', (e) => { e.preventDefault(); input.click(); });
    input.addEventListener('change', () => { if (input.files[0]) _plDoUpload(mp, input.files[0], zone); });
    // Элемент zone сохраняется при восстановлении innerHTML после импорта.
    // Связываем drop один раз, иначе повторная загрузка отправляет файл N раз.
    if (!zone.dataset.dropBound) {
      zone.dataset.dropBound = '1';
      ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('drag'); }));
      ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('drag'); }));
      zone.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) _plDoUpload(mp, f, zone); });
    }
  }

  async function _plDoUpload(mp, file, zone) {
    if (zone.dataset.busy === '1') return;
    zone.dataset.busy = '1';
    // Чистим плашки от предыдущей загрузки и сохраняем исходный HTML зоны.
    zone.parentElement.querySelectorAll('.up-warn, .note.err').forEach(el => el.remove());
    const orig = zone.innerHTML;
    zone.innerHTML = '<div class="loader"><span class="spinner"></span><div style="margin-top:8px">Загрузка ' + U.esc(file.name) + '…</div></div>';
    try {
      const fd  = new FormData(); fd.append('file', file);
      const url = '/api/prices/upload_' + (mp === 'wb' ? 'wb' : 'ozon');
      const res = await API.raw('POST', url, fd);
      const mpLbl = (mp === 'wb' ? 'Wildberries' : 'Ozon');
      const skipped = res.rows_skipped_unknown || res.skipped_unknown || 0;
      const dates = res.report_dates || [];
      const dateLabel = dates.length > 1
        ? fmtDatePl(dates[0]) + ' – ' + fmtDatePl(dates[dates.length - 1]) + ' · дней: ' + dates.length
        : fmtDatePl(dates[0]);
      const okBase = '✓ ' + mpLbl + ' · ' + dateLabel + ' · строк: ' + U.fmtNum(res.rows_upserted);
      if (skipped) {
        App.toast(okBase + ' · пропущено: ' + U.fmtNum(skipped), 'warn');
      } else {
        App.toast(okBase, 'ok');
      }
      // Новые данные — сброс кэша pricelist.
      pricesState.data = null;
      if (API.cacheClear) { API.cacheClear(); }
      // Восстанавливаем зону и подвязываем обработчики снова.
      zone.innerHTML = orig;
      _plSetupUploadZone(document, mp);
      if (skipped) {
        const sample = (res.unknown_sample || []).slice(0, 5).join(', ');
        const warn = document.createElement('div');
        warn.className = 'up-warn';
        warn.innerHTML = '⚠ Пропущено ' + U.fmtNum(skipped) + ' строк (нет в каталоге)' + (sample ? '. Примеры: ' + U.esc(sample) : '');
        zone.after(warn);
      }
      _reloadPlUploadLog();
    } catch (err) {
      zone.innerHTML = orig;
      _plSetupUploadZone(document, mp);
      const errBox = document.createElement('div');
      errBox.className = 'note err';
      errBox.textContent = 'Ошибка: ' + (err && err.message ? err.message : String(err));
      zone.after(errBox);
      App.toast('✗ Ошибка: ' + (err && err.message ? err.message : String(err)), 'err');
    } finally {
      delete zone.dataset.busy;
    }
  }

  function fmtDatePl(iso) {
    if (!iso) return '—';
    const s = String(iso);
    return s.length >= 10 ? (s.slice(8, 10) + '.' + s.slice(5, 7) + '.' + s.slice(0, 4)) : s;
  }

  async function _reloadPlUploadLog() {
    const el = document.getElementById('pl-upload-log');
    if (!el) return;
    el.innerHTML = '<div class="loader"><span class="spinner"></span></div>';
    try {
      const data  = await API.raw('GET', '/api/prices/upload_log?limit=50', null);
      const items = data.items || [];
      if (!items.length) { el.innerHTML = '<div class="empty">Загрузок нет.</div>'; return; }
      let h = '<div class="tbl-wrap"><table class="tbl">'
        + '<thead><tr>'
        +   '<th class="l">Дата</th>'
        +   '<th>МП</th>'
        +   '<th class="l">На дату</th>'
        +   '<th class="l">Файл</th>'
        +   '<th>Строк</th>'
        +   '<th>Записано</th>'
        +   '<th>Пропущено</th>'
        +   '<th>Статус</th>'
        +   '<th class="l">Пользователь</th>'
        + '</tr></thead><tbody>';
      items.forEach((it) => {
        const dt = it.loaded_at ? new Date(it.loaded_at).toLocaleString('ru-RU') : '—';
        const ok = (it.status === 'ok' || it.status === 'OK');
        const skipped = it.rows_skipped_unknown || 0;
        h += '<tr>'
          + '<td class="l muted">' + U.esc(dt) + '</td>'
          + '<td>' + U.mpPill(it.marketplace) + '</td>'
          + '<td class="l">' + fmtDatePl(it.report_date) + '</td>'
          + '<td class="l muted">' + U.esc(it.file_name || '—') + '</td>'
          + '<td class="num">' + (it.rows_in_file  != null ? U.fmtNum(it.rows_in_file)  : '—') + '</td>'
          + '<td class="num">' + (it.rows_upserted != null ? U.fmtNum(it.rows_upserted) : '—') + '</td>'
          + '<td class="num">' + (skipped ? U.fmtNum(skipped) : '0') + '</td>'
          + '<td><span class="pill ' + (ok ? 'up' : 'down') + '" title="' + U.esc(it.message || '') + '">' + U.esc(ok ? 'OK' : (it.status || 'ERR')) + '</span></td>'
          + '<td class="l muted">' + U.esc(it.loaded_by_name || it.loaded_by_email || '—') + '</td>'
          + '</tr>';
      });
      h += '</tbody></table></div>';
      el.innerHTML = h;
    } catch (err) {
      el.innerHTML = '<div class="empty">Ошибка загрузки журнала: ' + U.esc(err.message || String(err)) + '</div>';
    }
  }

  // Высота липкого тулбара раздела «Цены» (подтабы + фильтры) — панель может
  // переноситься на две строки на узком экране, поэтому меряем реальную высоту
  // и пишем в --pl-toolbar-h: шапка таблицы липнет ровно под ней (как в РНП).
  function pricesSyncToolbarHeight() {
    requestAnimationFrame(() => {
      const tb = document.querySelector('.mp-cross .rnp-toolbar');
      document.documentElement.style.setProperty(
        '--pl-toolbar-h', tb ? Math.round(tb.getBoundingClientRect().height) + 'px' : '0px');
      // Фактическая высота верхней строки шапки. Второй уровень липнет ровно
      // под ней; если взять «на глаз» (24px), на стыке остаётся щель в ~1px,
      // и при скролле в ней видны верхушки цифр проезжающих строк.
      const hr1 = document.querySelector('.mp-cross #pl-body table.sp-tbl thead tr:first-child');
      document.documentElement.style.setProperty(
        '--pl-hrow1-h', hr1 ? Math.ceil(hr1.getBoundingClientRect().height) + 'px' : '24px');
    });
  }
  if (!window.__plToolbarResizeBound) {
    window.__plToolbarResizeBound = true;
    window.addEventListener('resize', () => pricesSyncToolbarHeight());
  }

  // Сохранение одной ячейки прайс-листа; при успехе обновляет pricesState.data в памяти.
  function pricesApplyCellUpdate(art, mp, newPrice) {
    if (!pricesState.data || !pricesState.data.items) return;
    const norm = (newPrice === null || newPrice === undefined ? null : Number(newPrice));
    for (const it of pricesState.data.items) {
      if (it.seller_article === art) {
        if (mp === 'ozon' || mp === 'Ozon') it.price_ozon = norm;
        else if (mp === 'ya' || mp === 'yandex' || mp === 'Yandex') it.price_ya = norm;
        else                                 it.price_wb   = norm;
        return;
      }
    }
  }

  // Основная view-функция раздела «Цены».
  async function prices(root, ctl, state) {
    // Каркас раздела: карточка mp-cross + тулбар с подтабами и фильтрами.
    // Подтаб «Загрузка данных» прижат вправо (класс .subtab-right), как во
    // всех разделах, где он есть.
    const sub = pricesState.sub || 'pricelist';
    const subtabsHtml =
      '<button type="button" class="subtab cross' + (sub === 'pricelist' ? ' active' : '') + '" data-sub="pricelist">Прайс-лист</button>' +
      '<button type="button" class="subtab cross' + (sub === 'index' ? ' active' : '') + '" data-sub="index">Индекс цен</button>' +
      '<button type="button" class="subtab cross subtab-right' + (sub === 'upload' ? ' active' : '') + '" data-sub="upload">Загрузка данных</button>';

    // Дефолт периода (единожды за сессию): — как в РНП заказы (rnpSales):
    //   from = 1-е число (текущий месяц − 2), to = последний день текущего месяца.
    if (!pricesState._periodInit) {
      const now = new Date();
      const from = new Date(now.getFullYear(), now.getMonth() - 2, 1);
      const to = new Date(now.getFullYear(), now.getMonth() + 1, 0);
      pricesState.from = isoDate(from);
      pricesState.to = isoDate(to);
      pricesState._periodInit = true;
    }

    // Шапка — в точности как в РНП заказы (.rnp-toolbar + .rnp-filters-host).
    //   .subtabs   — подтабы слева («Прайс-лист»; «Загрузка данных» — внутри
    //                subtabs справа через .subtab-right).
    //   .rnp-filters-host — все фильтры сразу за подтабами; в конце — выбор периода.
    // Классы и теги — те же (input.input-sm, button.btn-sm, .rnp-period + SVG-иконка).
    const periodHtml =
      '<div class="rnp-period" id="pl-period">' +
        '<button class="rnp-period-field" id="pl-period-field" type="button" title="Выбрать период">' +
          '<svg class="rnp-period-ico" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/></svg>' +
          '<span class="rnp-period-text" id="pl-period-text">' + U.esc(plPeriodLabel()) + '</span>' +
        '</button>' +
        '<div class="rnp-cal" id="pl-cal" hidden></div>' +
      '</div>';

    // Массовая работа с ценами — теми же кнопками и в том же блоке .sp-actions,
    // что в «Продажи, шт.»: выгрузка шаблона доступна всем, загрузка — админу.
    const plIsAdmin = !!(App && App.state && App.state.user &&
                         (App.state.user.role || '').toLowerCase() === 'admin');
    // Галочка «показать себестоимость» живёт рядом с кнопками отчёта
    // «Прайс-лист»; также управляет столбцом себестоимости в Excel-выгрузках.
    const plActionsHtml =
      '<div class="sp-actions">' +
        '<label class="sp-fc-check" style="cursor:pointer;" title="Показать себестоимость в прайс-листе и включить её в Excel-выгрузки прайс-листа и индекса цен">' +
          '<input type="checkbox" id="pl-show-cost"' + (pricesState.showCost ? ' checked' : '') + '> показать себестоимость' +
        '</label>' +
        '<button class="btn" id="pl-export" type="button" title="Выгрузить базовые цены в Excel: артикул и три колонки цен (OZON, WB, Yandex)">⬇️ Выгрузить</button>' +
        (plIsAdmin
          ? '<button class="btn" id="pl-import-btn" type="button" title="Загрузить базовые цены из Excel">⬆️ Загрузить</button>' +
            '<input type="file" id="pl-import-file" accept=".xlsx,.xls" style="display:none">'
          : '') +
      '</div>';

    const filtersHtml = (sub === 'pricelist' || sub === 'index')
      ? ('<div class="rnp-filters-host">' +
           '<input class="input-sm" id="pl-search" placeholder="Поиск по артикулу или названию…" value="' + U.esc(pricesState.search || '') + '">' +
           '<button class="btn-sm" id="pl-expand" type="button" title="Развернуть все группировки">Развернуть всё</button>' +
           '<button class="btn-sm" id="pl-collapse" type="button" title="Свернуть до L1">Свернуть всё</button>' +
           periodHtml +
         '</div>')
      : '';

    // Под шапкой — панель с заголовком отчёта и счётчиком (справа).
    const cardTitleHtml = (sub === 'pricelist')
      ? ('<div class="rnp-card-title" style="display:flex;align-items:center;justify-content:space-between;gap:12px;">' +
           '<span>Отчет по ценообразованию</span>' +
           '<span style="display:flex;align-items:center;gap:12px;">' +
             '<span class="muted" id="pl-cnt" style="font-size:12px;font-weight:400;"></span>' +
             plActionsHtml +
           '</span>' +
         '</div>')
      : (sub === 'index')
      ? ('<div class="rnp-card-title" style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;">' +
           '<span>Индекс цен</span>' +
           '<span style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;">' +
             // Актуальность данных: последний день с заказами (макс. по всем
             // товарам). Берётся из wb_last_date / oz_last_date, приходящих
             // из prices_router. Форматируется в refreshCnt().
             '<span class="muted" id="pl-idx-freshness" style="font-size:12px;font-weight:400;" title="Последний загруженный день заказов из РНП по каждой площадке"></span>' +
             '<span class="muted" id="pl-cnt" style="font-size:12px;font-weight:400;"></span>' +
             '<span class="sp-actions"><button class="btn" id="pl-index-export" type="button" title="Выгрузить индекс цен в Excel за выбранный период с учётом поиска. Себестоимость включается галочкой в прайс-листе.">Выгрузить отчёт</button></span>' +
           '</span>' +
         '</div>')
      : '';

    root.innerHTML =
      '<div class="card rnp-card mp-cross">' +
        '<div class="rnp-toolbar">' +
          '<div class="subtabs" id="pl-subtabs">' + subtabsHtml + '</div>' +
          filtersHtml +
        '</div>' +
        cardTitleHtml +
        '<div id="pl-imp-result"></div>' +
        '<div id="pl-body" data-sub="' + sub + '"></div>' +
      '</div>';

    // Переключение подтабов.
    root.querySelector('#pl-subtabs').addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-sub]');
      if (!btn) return;
      pricesState.sub = btn.dataset.sub;
      prices(root, ctl, state);
    });

    const body = root.querySelector('#pl-body');

    if (sub === 'upload') {
      pricesRenderUploadTab(body);
      return;
    }

    body.innerHTML = '<div id="pl-tbl"><div class="loader"><span class="spinner"></span></div></div>';
    const tblHost = body.querySelector('#pl-tbl');
    const paintFn = (sub === 'index') ? pricesIdxPaintTable : pricesPaintTable;

    // Загружаем данные (если ещё не загружены в сессии). С учётом периода.
    async function reloadData() {
      pricesState.loading = true;
      tblHost.innerHTML = '<div class="loader"><span class="spinner"></span></div>';
      try {
        pricesState.data = await API.pricelist({
          date_from: pricesState.from || '',
          date_to:   pricesState.to   || '',
        });
      } catch (err) {
        pricesState.loading = false;
        tblHost.innerHTML = '<div class="empty">Ошибка загрузки: ' + U.esc(err.message) + '</div>';
        throw err;
      }
      pricesState.loading = false;
    }
    try {
      if (!pricesState.data) {
        await reloadData();
      }
    } catch (err) {
      return;
    }

    // Первый рендер: все L1-группы развёрнуты; L2/L3 — свёрнуты.
    function initExpanded() {
      pricesState.expanded = {};
      const tree = pricesBuildTree(pricesState.data.items || []);
      for (const k in tree.root.children) pricesState.expanded[tree.root.children[k].key] = true;
      pricesState.expanded[tree.unassigned.key] = true;
    }
    if (Object.keys(pricesState.expanded).length === 0) initExpanded();
    paintFn(tblHost);

    // Компонент периода — при смене диапазона перегружаем данные и сбрасываем дерево.
    bindPlPeriod(root, async () => {
      try {
        await reloadData();
        initExpanded();
        paintFn(tblHost);
        refreshCnt();
      } catch (e) { /* ошибка уже показана в tblHost */ }
    });

    // Обновить счётчик товаров в правом углу тулбара.
    function refreshCnt() {
      const cntEl = root.querySelector('#pl-cnt');
      if (cntEl) {
        const total = (pricesState.data.items || []).length;
        const q = (pricesState.search || '').trim();
        if (!q) cntEl.textContent = 'Всего товаров: ' + U.fmtNum(total);
        else {
          const filtered = pricesFilterItems(pricesState.data.items || [], q);
          cntEl.textContent = 'Найдено: ' + U.fmtNum(filtered.length) + ' / ' + U.fmtNum(total);
        }
      }
      // Актуальность данных (только «Индекс цен»): последний день с заказами
      // по WB и Ozon. Считаем как максимум wb_last_date / oz_last_date по всем
      // товарам в текущей выдаче — эти даты приходят с бэкенда (см. LATERAL
      // last-day-with-orders в prices_router). Даты у WB и Ozon могут
      // отличаться, поэтому показываем обе.
      const frEl = root.querySelector('#pl-idx-freshness');
      if (frEl) {
        const items = pricesState.data.items || [];
        let wbMax = null, ozMax = null;
        for (let i = 0; i < items.length; i++) {
          const w = items[i].wb_last_date, o = items[i].oz_last_date;
          if (w && (!wbMax || w > wbMax)) wbMax = w;
          if (o && (!ozMax || o > ozMax)) ozMax = o;
        }
        const fmt = (iso) => {
          if (!iso) return '—';
          const s = String(iso); // ожидаемый формат YYYY-MM-DD
          if (s.length >= 10 && s[4] === '-' && s[7] === '-') {
            return s.slice(8, 10) + '.' + s.slice(5, 7) + '.' + s.slice(0, 4);
          }
          return s;
        };
        frEl.textContent = 'Данные на: WB — ' + fmt(wbMax) + ' · Ozon — ' + fmt(ozMax);
      }
    }
    refreshCnt();

    // --- Поиск: перерисовка без запроса к API (debounce 180мс).
    let searchTimer = null;
    const searchEl = root.querySelector('#pl-search');
    searchEl.addEventListener('input', () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        pricesState.search = searchEl.value || '';
        paintFn(tblHost);
        refreshCnt();
      }, 180);
    });

    // --- Развернуть всё.
    root.querySelector('#pl-expand').addEventListener('click', () => {
      const walk = (n) => {
        if (n.level >= 1) pricesState.expanded[n.key] = true;
        for (const k in n.children) walk(n.children[k]);
      };
      const tree = pricesBuildTree(pricesState.data.items || []);
      walk(tree.root); walk(tree.unassigned);
      paintFn(tblHost);
    });

    // --- Свернуть всё (оставляем только L1-группы видимыми).
    root.querySelector('#pl-collapse').addEventListener('click', () => {
      pricesState.expanded = {};
      const tree = pricesBuildTree(pricesState.data.items || []);
      for (const k in tree.root.children) pricesState.expanded[tree.root.children[k].key] = true;
      pricesState.expanded[tree.unassigned.key] = true;
      paintFn(tblHost);
    });

    // --- Галочка «показать себестоимость»: только перерисовка таблицы,
    //     данные с/с приходят вместе с прайс-листом (запрос не повторяем).
    const plCostChk = root.querySelector('#pl-show-cost');
    if (plCostChk) {
      plCostChk.addEventListener('change', () => {
        pricesState.showCost = plCostChk.checked;
        pricesPaintTable(tblHost);
      });
    }

    // --- Excel: шаблон базовых цен или оформленный индекс цен ---
    const plExp = root.querySelector('#pl-export, #pl-index-export');
    if (plExp) {
      plExp.addEventListener('click', async () => {
        const prev = plExp.innerHTML;
        plExp.disabled = true; plExp.innerHTML = '<span class="btn-spin"></span>Выгружаю…';
        try {
          // Состав строк = ровно то, что видно в таблице за выбранный период.
          const isIndex = plExp.id === 'pl-index-export';
          const url = (isIndex ? API.pricesIndexExportUrl : API.pricesExportUrl)({
            date_from: pricesState.from || '', date_to: pricesState.to || '',
            show_cost: pricesState.showCost ? 'true' : 'false',
            search: searchEl.value || '',
          });
          const resp = await fetch(url, { headers: { Authorization: 'Bearer ' + API.getToken() } });
          if (!resp.ok) throw new Error('HTTP ' + resp.status);
          const cd = resp.headers.get('Content-Disposition') || '';
          let fname = isIndex ? 'price_index.xlsx' : 'base_prices.xlsx';
          const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
          if (m) { try { fname = decodeURIComponent(m[1]); } catch (e) { fname = m[1]; } }
          const blob = await resp.blob();
          const dlUrl = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = dlUrl; a.download = fname;
          document.body.appendChild(a); a.click();
          setTimeout(() => { URL.revokeObjectURL(dlUrl); a.remove(); }, 1500);
          App.toast(isIndex ? 'Индекс цен выгружен' : 'Базовые цены выгружены', 'ok');
        } catch (e) { App.toast('Ошибка выгрузки: ' + e.message, 'err'); }
        finally { plExp.disabled = false; plExp.innerHTML = prev; }
      });
    }

    // --- Загрузка заполненного файла базовых цен (только админ) ---
    const plImpBtn = root.querySelector('#pl-import-btn');
    const plImpFile = root.querySelector('#pl-import-file');
    const plImpRes = root.querySelector('#pl-imp-result');
    if (plImpBtn && plImpFile) {
      plImpBtn.addEventListener('click', () => plImpFile.click());
      plImpFile.addEventListener('change', async () => {
        const f = plImpFile.files && plImpFile.files[0];
        if (!f) return;
        const prev = plImpBtn.innerHTML;
        plImpBtn.disabled = true; plImpBtn.innerHTML = '<span class="btn-spin"></span>Загружаю…';
        if (plImpRes) plImpRes.innerHTML = '';
        try {
          const r = await API.pricesImport(f);
          if (API.cacheClear) API.cacheClear('/api/prices');
          const unk = r.skipped_unknown
            ? ' Пропущено неизвестных артикулов: <b>' + r.skipped_unknown + '</b> (' +
              U.esc((r.unknown_sample || []).join(', ')) + ').'
            : '';
          if (plImpRes) plImpRes.innerHTML = '<div class="note ok" style="margin:0 0 12px; padding:10px 14px; border-radius:8px; background:rgba(46,204,113,.12); border:1px solid rgba(46,204,113,.4);">' +
            '<b>Цены загружены.</b> Артикулов: <b>' + (r.articles || 0) + '</b>, задано цен: <b>' +
            (r.prices_set || 0) + '</b>, очищено: <b>' + (r.prices_cleared || 0) + '</b>.' + unk + '</div>';
          App.toast('Цены загружены: ' + (r.prices_set || 0), 'ok');
          pricesState.data = null;
          await reloadData();
          // ВАЖНО: reloadData() только грузит данные и показывает спиннер —
          // без перерисовки спиннер оставался крутиться навсегда.
          // Состояние раскрытия дерева не сбрасываем: состав товаров не изменился.
          pricesPaintTable(tblHost);
          refreshCnt();
        } catch (e) {
          if (plImpRes) plImpRes.innerHTML = '<div class="note err" style="margin:0 0 12px; padding:10px 14px; border-radius:8px; background:rgba(192,57,43,.12); border:1px solid rgba(192,57,43,.4); color:var(--danger,#c0392b);">Ошибка загрузки цен: ' + U.esc(e.message) + '</div>';
          App.toast('Ошибка загрузки цен: ' + e.message, 'err');
        } finally { plImpBtn.disabled = false; plImpBtn.innerHTML = prev; plImpFile.value = ''; }
      });
    }

    // --- Клик по строке-группе: развернуть/свернуть.
    tblHost.addEventListener('click', (e) => {
      // Не срабатывает, если кликнули по ячейке цены.
      if (e.target.closest('td.sp-mth') && e.target.closest('tr.sp-leaf')) return;
      const tr = e.target.closest('tr.sp-has');
      if (!tr) return;
      const key = tr.dataset.key;
      pricesState.expanded[key] = !pricesState.expanded[key];
      pricesPaintTable(tblHost);
    });

    // --- Двойной клик по ячейке цены (span.sp-plan-edit) → инлайн-input.
    tblHost.addEventListener('dblclick', (e) => {
      const span = e.target.closest('.sp-plan-edit');
      if (!span || span.querySelector('input')) return;
      const art = span.dataset.art;
      const mp  = span.dataset.mp;
      const cur = span.dataset.val;
      const oldHtml = span.innerHTML;
      const oldCls  = span.className;
      const wasEmpty = span.classList.contains('sp-plan-empty');

      const inp = document.createElement('input');
      inp.type = 'number'; inp.min = '0'; inp.step = '1';
      inp.className = 'sp-plan-input';
      inp.value = wasEmpty ? '' : cur;
      span.innerHTML = '';
      span.appendChild(inp);
      inp.focus(); inp.select();

      let done = false;
      const restore = () => { if (done) return; done = true; span.innerHTML = oldHtml; span.className = oldCls; };
      const save = async () => {
        if (done) return;
        const raw = (inp.value || '').trim();
        let val = null;
        if (raw !== '') {
          const n = Number(raw);
          if (!isFinite(n) || n < 0 || Math.floor(n) !== n) {
            App.toast('Цена — целое неотрицательное число (рубли)', 'err');
            restore(); return;
          }
          val = Math.round(n);
        }
        const curNum = (cur === '' ? null : Number(cur));
        if (val === curNum) { restore(); return; }
        done = true;
        try {
          const res = await API.priceCell({ seller_article: art, marketplace: mp, base_price: val });
          pricesApplyCellUpdate(art, mp, res.base_price);
          const empty = (res.base_price === null || res.base_price === undefined);
          span.className = 'sp-plan sp-plan-edit' + (empty ? ' sp-plan-empty' : '');
          span.dataset.val = (empty ? '' : String(res.base_price));
          span.title = empty ? 'Двойной клик — задать цену' : 'Двойной клик — изменить цену';
          span.innerHTML = empty ? '–' : pricesFmt(res.base_price);
          pricesRepaintRowPi(tblHost, art);
          if (App && App.toast) App.toast('Цена сохранена', 'ok');
        } catch (err) {
          if (App && App.toast) App.toast(err.message || 'Не удалось сохранить цену', 'err');
          span.innerHTML = oldHtml; span.className = oldCls;
        }
      };
      inp.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') { ev.preventDefault(); inp.blur(); }
        else if (ev.key === 'Escape') { ev.preventDefault(); restore(); }
      });
      inp.addEventListener('blur', save);
    });
  }

  Object.assign(api, { rnp_sales: rnpSales, rnp, dynamics, compare, categories, skus, catalog, upload, users, abc, warehouses, prices });

  // Сохранить вертикальную прокрутку окна для активного РНП-отчёта перед
  // уходом на другой раздел (вызывается из App.setView). Снимок раскрытия
  // уже синхронизируется при каждом рендере; тут только scroll.
  api.saveRnpScroll = function (view) {
    try {
      if (view === 'rnp' && rnpState && rnpState.uiSnap) {
        const s = rnpState.uiSnap[rnpState.mp] || (rnpState.uiSnap[rnpState.mp] = {});
        s.winScrollY = window.scrollY || window.pageYOffset || 0;
      } else if (view === 'rnp_sales' && rnpSalesState && rnpSalesState.uiSnap) {
        const s = rnpSalesState.uiSnap[rnpSalesState.mp] || (rnpSalesState.uiSnap[rnpSalesState.mp] = {});
        s.winScrollY = window.scrollY || window.pageYOffset || 0;
      }
    } catch (e) { /* некритично */ }
  };

  return api;
})();
