// Утилиты: форматирование, тема, ECharts-хелперы.
const U = (() => {

  function fmtNum(v, digits = 0) {
    if (v === null || v === undefined || isNaN(v)) return '—';
    return Number(v).toLocaleString('ru-RU', {
      minimumFractionDigits: digits, maximumFractionDigits: digits,
    });
  }
  function fmtMoney(v, digits = 0) {
    if (v === null || v === undefined || isNaN(v)) return '—';
    return fmtNum(v, digits) + ' ₽';
  }
  // компактные деньги: 1.2 млн ₽, 845 тыс ₽
  function fmtMoneyShort(v) {
    if (v === null || v === undefined || isNaN(v)) return '—';
    const a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(2).replace('.', ',') + ' млрд ₽';
    if (a >= 1e6) return (v / 1e6).toFixed(2).replace('.', ',') + ' млн ₽';
    if (a >= 1e3) return Math.round(v / 1e3) + ' тыс ₽';
    return fmtNum(v) + ' ₽';
  }
  function fmtPct(v, digits = 1) {
    if (v === null || v === undefined || isNaN(v)) return '—';
    return Number(v).toFixed(digits).replace('.', ',') + '%';
  }

  // цвет маржи: зелёный/оранжевый/красный
  function marginClass(p) {
    if (p === null || p === undefined) return 'muted';
    if (p >= 20) return 'm-good';
    if (p >= 10) return 'm-warn';
    return 'm-bad';
  }

  // WoW-бейдж: абсолют + % + стрелка
  function wowBadge(wow, opts = {}) {
    if (!wow) return '<span class="muted">—</span>';
    const dir = wow.direction;
    const arrow = dir === 'up' ? '▲' : dir === 'down' ? '▼' : '▬';
    const pct = wow.delta_pct === null ? '' : (wow.delta_pct > 0 ? '+' : '') + fmtPct(wow.delta_pct);
    let abs = '';
    if (opts.money) abs = (wow.delta > 0 ? '+' : '') + fmtMoneyShort(wow.delta);
    else if (opts.pp) abs = (wow.delta > 0 ? '+' : '') + fmtPct(wow.delta) + ' п.п.';
    else abs = (wow.delta > 0 ? '+' : '') + fmtNum(wow.delta);
    return `<span class="wow ${dir}"><span class="arrow">${arrow}</span>${pct}<span class="abs">${abs}</span></span>`;
  }
  function wowPill(wow) {
    if (!wow) return '<span class="pill flat">—</span>';
    const dir = wow.direction;
    const arrow = dir === 'up' ? '▲' : dir === 'down' ? '▼' : '▬';
    const pct = wow.delta_pct === null ? 'нов.' : (wow.delta_pct > 0 ? '+' : '') + fmtPct(wow.delta_pct);
    return `<span class="pill ${dir}">${arrow} ${pct}</span>`;
  }

  function mpPill(mp) {
    if (mp === 'Ozon') return '<span class="pill oz">Ozon</span>';
    if (mp === 'Wildberries') return '<span class="pill wb">WB</span>';
    if (mp === 'Yandex') return '<span class="pill ya">Yandex</span>';
    return '';
  }

  function esc(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  // -------- ECharts тема (только светлая) --------
  function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  function chartTheme() {
    return {
      text: cssVar('--text'),
      dim: cssVar('--text-dim'),
      axis: '#cdd5df',
      split: '#eaeef3',
      bg: 'transparent',
      tooltipBg: cssVar('--bg-elev'),
      tooltipBorder: cssVar('--border'),
      accent: cssVar('--accent'),
      ozon: cssVar('--ozon'),
      wb: cssVar('--wb'),
      good: cssVar('--good'),
      bad: cssVar('--bad'),
      palette: ['#eb1414', '#2f6bff', '#16a360', '#8b3bff', '#e0a200', '#16191d', '#0891b2', '#65a30d'],
    };
  }

  function baseOption() {
    const t = chartTheme();
    return {
      color: t.palette,
      textStyle: { color: t.text, fontFamily: getComputedStyle(document.body).fontFamily },
      grid: { left: 8, right: 16, top: 30, bottom: 8, containLabel: true },
      tooltip: {
        trigger: 'axis',
        backgroundColor: t.tooltipBg,
        borderColor: t.tooltipBorder,
        textStyle: { color: t.text, fontSize: 12 },
        confine: true,
      },
      legend: { top: 0, textStyle: { color: t.dim, fontSize: 12 }, icon: 'roundRect' },
    };
  }

  // регистр созданных графиков (для ресайза и перерисовки при смене темы)
  const _charts = new Map();
  function makeChart(el) {
    if (!el) return null;
    let inst = _charts.get(el);
    if (inst) { inst.dispose(); }
    inst = echarts.init(el);
    _charts.set(el, inst);
    return inst;
  }
  function disposeAll() {
    _charts.forEach(c => c.dispose());
    _charts.clear();
  }
  function resizeAll() { _charts.forEach(c => c.resize()); }
  window.addEventListener('resize', () => resizeAll());

  return {
    fmtNum, fmtMoney, fmtMoneyShort, fmtPct, marginClass,
    wowBadge, wowPill, mpPill, esc,
    chartTheme, baseOption, makeChart, disposeAll, resizeAll,
  };
})();
