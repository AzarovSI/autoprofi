/* ================================================================
   ABC · Анализ товарной матрицы (ОЗОН × Wildberries)
   Порт офлайн-дашборда в модуль SPA. Namespace: window.ABCDash.
   Данные грузятся из авторизованного API /api/abc/data.
   ABC и рекомендации считаются на лету при смене периода
   (Парето 80/15/5 раздельно по выручке и прибыли).
   ================================================================ */
window.ABCDash = (function () {
  'use strict';

  const STATE = {
    raw: null,            // { ozon:[], vb:[], yandex:[], holds:[], dashOzon:[], dashVb:[], dashYa:[] }
    allMonths: [],        // все месяцы периода (уникальные ключи «YYYY-MM»)
    selectedMonths: [],   // выбранные месяцы (уникальные ключи «YYYY-MM»)
    computed: null,       // { ozon, vb, cross, summary } после агрегации
    charts: {},
    source: null,
    rootEl: null,         // корневой .abc-dash
    monthMeta: [],        // [{ key:'2026-01', start:'2026-01-01', end:'2026-01-31', label:'Янв 2026' }]
    period: { from: '', to: '' },
    loaded: false,        // данные уже получены с API в этой сессии страницы (кэш)
    activeTab: 'overview', // активная под-вкладка — восстанавливаем при возврате
  };


  const fmtRub = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
  const fmtPct = (v) => (v*100).toFixed(1) + '%';
  const rub = (v) => fmtRub.format(Math.round(v)) + ' ₽';
  const num = (v) => fmtRub.format(Math.round(v));

  /* ============ Статусы товара (как в РНП) ============
     Логика повторяет static/js/views.js: пусто/NULL → «-», '-' → null на запись.
     Хелперы продублированы здесь намеренно — оригиналы в замыкании views.js
     недоступны извне; дублирование изолирует ABC и не трогает РНП. */
  const STATUS_PRESETS = ['CORE', 'NEW', 'Closeout', 'Sale', '?', '-'];
  function stDisplay(v) { return (v && String(v).trim()) ? String(v).trim() : '-'; }
  // Сохраняем РОВНО выбранное значение (включая '-' и '?'); только реально
  // пустое → null. Так '-' (выведен из продаж) отличается в БД от NULL (не задан).
  function stToApi(v) { const s = (v == null) ? '' : String(v).trim(); return s === '' ? null : s; }
  function stClass(v) {
    const s = stDisplay(v);
    if (s === 'CORE') return 'st-core';
    if (s === 'NEW') return 'st-new';
    if (s === 'Closeout') return 'st-closeout';
    if (s === 'Sale') return 'st-sale';
    if (s === '?') return 'st-question';
    return 'st-none';
  }
  // <select> статусов для строки ABC. mp: 'ozon' | 'vb' — куда писать статус.
  function statusSelectHtml(sa, current, mp) {
    const cur = stDisplay(current);
    const opts = STATUS_PRESETS.map(s =>
      `<option value="${escapeAttr(s)}"${s === cur ? ' selected' : ''}>${escapeHtml(s)}</option>`).join('');
    return `<select class="abc-status-sel ${stClass(cur)}" data-sa="${escapeAttr(sa)}" data-mp="${escapeAttr(mp)}" data-prev="${escapeAttr(cur)}" title="Статус товара">${opts}</select>`;
  }
  // Товар со статусом «-» (выведен из продаж) без движения за выбранный период
  // скрывается из всех таблиц ABC. «Движение» = любое ненулевое из
  // revenue/sales_qty/orders_qty/returns_qty/profit по агрегату периода.
  // Прочие статусы (в т.ч. «?») и «-» с движением — показываются всегда.
  function isDashNoMove(s) {
    if (!s || stToApi(s.status) !== '-') return false;
    const moved = (s.revenue || 0) !== 0 || (s.sales_qty || 0) !== 0
      || (s.orders_qty || 0) !== 0 || (s.returns_qty || 0) !== 0
      || (s.profit || 0) !== 0;
    return !moved;
  }
  // Обновить статус товара в STATE.computed, чтобы ре-рендер не откатил значение.
  function updateStatusInMemory(sa, mp, apiVal) {
    const val = apiVal == null ? '' : apiVal;
    // Ключ в STATE.computed: 'ozon' | 'vb' | 'ya' (атрибут data-mp у Яндекса = 'yandex').
    const ck = mp === 'yandex' ? 'ya' : mp;
    const arr = STATE.computed && STATE.computed[ck];
    if (arr) { const row = arr.find(x => x.sku === sa); if (row) row.status = val; }
    const cross = STATE.computed && STATE.computed.cross;
    if (cross) {
      const cr = cross.find(x => x.sku === sa);
      if (cr) {
        if (mp === 'vb') cr.vb_status = val;
        else if (mp === 'yandex') cr.ya_status = val;
        else cr.ozon_status = val;
      }
    }
  }
  // Навесить обработчики смены статуса на все .abc-status-sel внутри container.
  function bindStatusSelects(container) {
    if (!container) return;
    container.querySelectorAll('.abc-status-sel').forEach(sel => {
      // клик по селектору не должен открывать модалку строки
      sel.addEventListener('click', (e) => e.stopPropagation());
      sel.addEventListener('change', async (e) => {
        e.stopPropagation();
        const sa = sel.getAttribute('data-sa');
        const mp = sel.getAttribute('data-mp');          // 'ozon' | 'vb'
        const newVal = sel.value;                         // '-' = без статуса
        const prevVal = sel.getAttribute('data-prev') || '-';
        if (!sa) return;
        sel.className = 'abc-status-sel ' + stClass(newVal);
        sel.disabled = true;
        const api = window.API || API;
        try {
          await api.catalogUpdate(sa, {
            marketplace: mp === 'vb' ? 'Wildberries' : (mp === 'yandex' ? 'Yandex' : 'Ozon'),
            status: stToApi(newVal),
          });
          updateStatusInMemory(sa, mp, stToApi(newVal));
          sel.setAttribute('data-prev', stDisplay(newVal));
          if (api.cacheClear) { api.cacheClear('/api/catalog'); api.cacheClear('/api/abc'); }
          App.toast('Статус обновлён: ' + sa + ' → ' + newVal, 'ok');
        } catch (err) {
          sel.value = prevVal;
          sel.className = 'abc-status-sel ' + stClass(prevVal);
          App.toast('Ошибка сохранения статуса: ' + ((err && err.message) || err), 'err');
        } finally {
          sel.disabled = false;
        }
      });
    });
  }

  const REC_LABELS = {
    KEEP_STAR: '⭐ Звезда',
    KEEP: 'Оставить',
    REVIEW: 'Пересмотреть',
    REMOVE_HERE: 'Убрать с этого МП',
    CONSIDER_REMOVE_HERE: 'Рассмотреть перенос',
    REMOVE: 'Вывести совсем',
    '-': '—',
  };

  const MONTH_ORDER = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
  const MONTH_SHORT = {'Январь':'Янв','Февраль':'Фев','Март':'Мар','Апрель':'Апр','Май':'Май','Июнь':'Июн','Июль':'Июл','Август':'Авг','Сентябрь':'Сен','Октябрь':'Окт','Ноябрь':'Ноя','Декабрь':'Дек'};
  function periodLabel(month, year) { return year ? `${MONTH_SHORT[month] || month} ${year}` : month; }
  function periodSortKey(label) {
    // "Янв 2025" → 2025 * 100 + monthIndex
    const parts = String(label).trim().split(/\s+/);
    if (parts.length < 2) {
      const idx = MONTH_ORDER.indexOf(label);
      return idx >= 0 ? idx : 9999;
    }
    const [m, y] = parts;
    const monthIdx = Object.entries(MONTH_SHORT).find(([_, sh]) => sh === m)?.[0];
    const fullIdx = monthIdx ? MONTH_ORDER.indexOf(monthIdx) : MONTH_ORDER.indexOf(m);
    return parseInt(y, 10) * 100 + (fullIdx >= 0 ? fullIdx : 0);
  }

  /* ============ Compute pipeline ============ */
  function recomputeAndRender() {
    STATE.computed = computeAll(STATE.raw, STATE.selectedMonths);
    renderOverview();
    renderTable('ozon', STATE.computed.ozon);
    renderTable('vb', STATE.computed.vb);
    renderTable('ya', STATE.computed.ya);
    renderCross();
    renderActions();
  }

  function computeAll(raw, months) {
    const monthsSet = new Set(months);
    // Фильтруем по уникальному ключу r.ym (YYYY-MM), а не по названию
    // месяца — иначе выбор «Апр-Июн 2026» тянул бы и те же месяцы 2024/2025.
    const ozAgg = aggregate(raw.ozon.filter(r => monthsSet.has(r.ym)), 'OZON', months);
    const vbAgg = aggregate(raw.vb.filter(r => monthsSet.has(r.ym)), 'VB', months);
    // Яндекс — независимая агрегация (не связана с Venn/action).
    const yaAgg = aggregate((raw.yandex || []).filter(r => monthsSet.has(r.ym)), 'YA', months);

    // Rebuild full SKU set from raw (so empty SKUs are kept if user removes their only month)
    // But we want to show only SKUs that have data in selected months
    // ABC works on present SKUs.
    const ozRevAbc = abcClassify(Object.values(ozAgg).map(s => [s.sku, s.revenue]));
    const ozProfAbc = abcClassify(Object.values(ozAgg).map(s => [s.sku, s.profit]));
    const vbRevAbc = abcClassify(Object.values(vbAgg).map(s => [s.sku, s.revenue]));
    const vbProfAbc = abcClassify(Object.values(vbAgg).map(s => [s.sku, s.profit]));
    const yaRevAbc = abcClassify(Object.values(yaAgg).map(s => [s.sku, s.revenue]));
    const yaProfAbc = abcClassify(Object.values(yaAgg).map(s => [s.sku, s.profit]));

    Object.values(ozAgg).forEach(s => {
      s.abc_revenue = ozRevAbc[s.sku];
      s.abc_profit = ozProfAbc[s.sku];
      s.abc_combined = s.abc_revenue + s.abc_profit;
    });
    Object.values(vbAgg).forEach(s => {
      s.abc_revenue = vbRevAbc[s.sku];
      s.abc_profit = vbProfAbc[s.sku];
      s.abc_combined = s.abc_revenue + s.abc_profit;
    });
    Object.values(yaAgg).forEach(s => {
      s.abc_revenue = yaRevAbc[s.sku];
      s.abc_profit = yaProfAbc[s.sku];
      s.abc_combined = s.abc_revenue + s.abc_profit;
    });

    Object.values(ozAgg).forEach(s => {
      const other = vbAgg[s.sku];
      const [code, reason] = makeRecommendation(s, other);
      s.rec_code = code; s.rec_reason = reason;
    });
    Object.values(vbAgg).forEach(s => {
      const other = ozAgg[s.sku];
      const [code, reason] = makeRecommendation(s, other);
      s.rec_code = code; s.rec_reason = reason;
    });
    // Рекомендации Яндекса — только по собственной ABC (other=null): МП не
    // участвует в логике переноса/разделения ассортимента между ОЗОН и ВБ.
    Object.values(yaAgg).forEach(s => {
      const [code, reason] = makeRecommendation(s, null);
      s.rec_code = code; s.rec_reason = reason;
    });

    // Скрываем «-» без движения за период (см. isDashNoMove). В «Сравнении МП»
    // товар исключается автоматически, если скрыт на ОБОИХ МП: allSkus строится
    // из уже отфильтрованных массивов, поэтому sku, видимый хотя бы на одном МП,
    // остаётся в cross (с полными данными из ozAgg/vbAgg).
    const ozonArr = Object.values(ozAgg).filter(s => !isDashNoMove(s));
    const vbArr = Object.values(vbAgg).filter(s => !isDashNoMove(s));
    const yaArr = Object.values(yaAgg).filter(s => !isDashNoMove(s));

    // cross
    // Матрица «Сравнение МП» — это разделение ассортимента ОЗОН↔ВБ; СКУ
    // строятся только из этих двух МП (Яндекс НЕ добавляет свои СКУ в матрицу).
    // Опциональные ya_* поля — справочно, для тех СКУ, что есть и на Яндексе.
    const allSkus = new Set([...ozonArr.map(s=>s.sku), ...vbArr.map(s=>s.sku)]);
    const cross = [...allSkus].map(sku => {
      const oz = ozAgg[sku], vb = vbAgg[sku], ya = yaAgg[sku];
      return {
        sku,
        name: (oz?.name) || (vb?.name) || '',
        on_ozon: !!oz, on_vb: !!vb,
        ozon_status: oz?.status || '',
        vb_status: vb?.status || '',
        ozon_revenue: oz?.revenue || 0,
        ozon_profit: oz?.profit || 0,
        ozon_sales: oz?.sales_qty || 0,
        ozon_aov: (oz && oz.sales_qty > 0) ? oz.revenue / oz.sales_qty : 0,
        ozon_abc: oz?.abc_combined || '-',
        ozon_margin: oz?.margin || 0,
        ozon_drr: oz?.drr || 0,
        ozon_ad_spend: oz?.ad_spend || 0,
        ozon_rec: oz?.rec_code || '-',
        vb_revenue: vb?.revenue || 0,
        vb_profit: vb?.profit || 0,
        vb_sales: vb?.sales_qty || 0,
        vb_aov: (vb && vb.sales_qty > 0) ? vb.revenue / vb.sales_qty : 0,
        vb_abc: vb?.abc_combined || '-',
        vb_margin: vb?.margin || 0,
        vb_drr: vb?.drr || 0,
        vb_ad_spend: vb?.ad_spend || 0,
        vb_rec: vb?.rec_code || '-',
        // Яндекс — только справочные поля (НЕ участвует в action/Решении).
        on_ya: !!ya,
        ya_status: ya?.status || '',
        ya_revenue: ya?.revenue || 0,
        ya_profit: ya?.profit || 0,
        ya_sales: ya?.sales_qty || 0,
        ya_aov: (ya && ya.sales_qty > 0) ? ya.revenue / ya.sales_qty : 0,
        ya_abc: ya?.abc_combined || '-',
        ya_margin: ya?.margin || 0,
        ya_drr: ya?.drr || 0,
      };
    }).sort((a,b) => (b.ozon_revenue+b.vb_revenue) - (a.ozon_revenue+a.vb_revenue));

    // Прогноз матрицы после применения «Плана действий».
    // Правила: рекомендации этих типов фактически снимают SKU с маркетплейса.
    const REMOVE_CODES = new Set(['REMOVE', 'REMOVE_HERE', 'CONSIDER_REMOVE_HERE']);
    let fc_total = 0, fc_both = 0, fc_only_oz = 0, fc_only_vb = 0;
    let cnt_removed_full = 0;          // SKU убран отовсюду
    let cnt_oz_removed = 0;            // SKU остался только на ВБ (раньше был на обоих или только ОЗОН → нет)
    let cnt_vb_removed = 0;            // SKU остался только на ОЗОН
    let cnt_unchanged = 0;
    // Финансовый эффект по выбранному периоду: сколько выручки/прибыли уйдёт
    let rev_off_oz = 0, prof_off_oz = 0;
    let rev_off_vb = 0, prof_off_vb = 0;
    cross.forEach(c => {
      const ozRem = c.on_ozon && REMOVE_CODES.has(c.ozon_rec);
      const vbRem = c.on_vb   && REMOVE_CODES.has(c.vb_rec);
      const ozStays = c.on_ozon && !ozRem;
      const vbStays = c.on_vb   && !vbRem;
      const wasBoth = c.on_ozon && c.on_vb;
      if (ozRem) { rev_off_oz += (c.ozon_revenue || 0); prof_off_oz += (c.ozon_profit || 0); }
      if (vbRem) { rev_off_vb += (c.vb_revenue   || 0); prof_off_vb += (c.vb_profit   || 0); }
      // Снапшот после применения рекомендаций
      if (ozStays && vbStays) { fc_total++; fc_both++; }
      else if (ozStays && !vbStays) { fc_total++; fc_only_oz++; }
      else if (!ozStays && vbStays) { fc_total++; fc_only_vb++; }
      // Классификация изменения
      if (!ozStays && !vbStays) cnt_removed_full++;
      else if (wasBoth && !ozStays && vbStays) cnt_oz_removed++;
      else if (wasBoth && ozStays && !vbStays) cnt_vb_removed++;
      else cnt_unchanged++;
    });

    // summary
    const summary = {
      months,
      ozon: kpi(ozonArr),
      vb: kpi(vbArr),
      ya: kpi(yaArr),
      cross: {
        total_skus: cross.length,
        on_both: cross.filter(c => c.on_ozon && c.on_vb).length,
        only_ozon: cross.filter(c => c.on_ozon && !c.on_vb).length,
        only_vb: cross.filter(c => c.on_vb && !c.on_ozon).length,
        forecast: {
          total_skus: fc_total,
          on_both: fc_both,
          only_ozon: fc_only_oz,
          only_vb: fc_only_vb,
          removed_full: cnt_removed_full,
          moved_off_ozon: cnt_oz_removed,
          moved_off_vb: cnt_vb_removed,
          unchanged: cnt_unchanged,
          rev_off_ozon: rev_off_oz,
          prof_off_ozon: prof_off_oz,
          rev_off_vb: rev_off_vb,
          prof_off_vb: prof_off_vb,
        },
      }
    };

    return { ozon: ozonArr, vb: vbArr, ya: yaArr, cross, summary };
  }

  function aggregate(rows, mp, allMonths) {
    const bySku = {};
    rows.forEach(r => {
      if (!r.sku) return;
      if (!bySku[r.sku]) {
        bySku[r.sku] = {
          sku: r.sku, name: r.name, marketplace: mp,
          status: r.status || '',
          revenue: 0, profit: 0, sales_qty: 0, orders_qty: 0, returns_qty: 0,
          ad_spend: 0,
          months_data: {}, months_active: 0,
        };
      }
      const s = bySku[r.sku];
      if (!s.name && r.name) s.name = r.name;
      if (!s.status && r.status) s.status = r.status;
      s.revenue += r.revenue;
      s.profit += r.profit;
      s.sales_qty += r.sales_qty;
      s.orders_qty += r.orders_qty;
      s.returns_qty += r.returns_qty;
      s.ad_spend += (r.ad_spend || 0);
      // Ключуем months_data по r.ym (уникально по году+месяцу),
      // чтобы одинаковые месяцы разных лет не схлопывались (months_active).
      const mk = r.ym || r.month;
      if (!s.months_data[mk]) {
        s.months_data[mk] = { revenue: 0, profit: 0, sales_qty: 0, ad_spend: 0 };
      }
      s.months_data[mk].revenue += r.revenue;
      s.months_data[mk].profit += r.profit;
      s.months_data[mk].sales_qty += r.sales_qty;
      s.months_data[mk].ad_spend += (r.ad_spend || 0);
    });
    Object.values(bySku).forEach(s => {
      s.margin = s.revenue > 0 ? s.profit / s.revenue : 0;
      // ДРР (доля рекламных расходов) — реклама / выручка
      s.drr = s.revenue > 0 ? s.ad_spend / s.revenue : 0;
      s.months_active = Object.values(s.months_data).filter(m => m.sales_qty > 0 || m.revenue > 0).length;
    });
    return bySku;
  }

  function abcClassify(pairs) {
    const sorted = [...pairs].sort((a,b) => b[1] - a[1]);
    const total = sorted.filter(p => p[1] > 0).reduce((s,p) => s+p[1], 0);
    const result = {};
    let cum = 0;
    for (const [sku, v] of sorted) {
      if (v <= 0) { result[sku] = v < 0 ? 'D' : 'Z'; continue; }
      cum += v;
      const share = total > 0 ? cum / total : 0;
      if (share <= 0.80) result[sku] = 'A';
      else if (share <= 0.95) result[sku] = 'B';
      else result[sku] = 'C';
    }
    return result;
  }

  function makeRecommendation(rec, other) {
    const rev = rec.revenue, prof = rec.profit;
    const abc_r = rec.abc_revenue, abc_p = rec.abc_profit;
    const margin = rec.margin || 0;

    if (abc_r === 'A' && abc_p === 'A') return ['KEEP_STAR', 'Звёздный товар (AA): высокая выручка и прибыль — защищать и развивать'];
    if (abc_r === 'Z') return ['REMOVE', 'Нет продаж за период — вывести из матрицы'];

    if (prof < 0) {
      if (other && other.profit > 0 && (other.abc_revenue === 'A' || other.abc_revenue === 'B')) {
        return ['REMOVE_HERE', `Убыточен на этом МП (${rub(prof)}), но прибылен на другом — оставить только на другом МП`];
      }
      return ['REMOVE', `Убыточный товар (${rub(Math.abs(prof))} убытка) — кандидат на вывод`];
    }

    if (other && other.profit > 0) {
      if ((abc_r === 'B' || abc_r === 'C') && other.abc_revenue === 'A' && other.profit >= prof * 5) {
        if (rev < 100000) return ['REMOVE_HERE', `Здесь только ${rub(rev)} выручки против ${rub(other.revenue)} на другом МП — сосредоточить там`];
      }
      if (abc_r === 'C' && (other.abc_revenue === 'A' || other.abc_revenue === 'B') && other.profit >= prof * 3) {
        return ['CONSIDER_REMOVE_HERE', 'Товар лучше работает на другом МП — рассмотреть концентрацию там'];
      }
    }

    if (abc_r === 'C' && abc_p === 'C') {
      if (rev < 30000) return ['REMOVE', 'Длинный хвост (CC) с очень низкой выручкой — кандидат на вывод'];
      return ['REVIEW', 'Длинный хвост (CC) — пересмотреть целесообразность'];
    }
    if (margin < 0.05 && prof < 30000 && abc_r !== 'A') {
      return ['REVIEW', `Низкая маржа (${(margin*100).toFixed(1)}%) и небольшая прибыль — рассмотреть вывод`];
    }
    if (abc_r === 'A') return ['KEEP', 'Топ по выручке (A) — оставить'];
    if (abc_p === 'A') return ['KEEP', 'Топ по прибыли (A) — оставить'];
    return ['KEEP', `Стабильный товар (${abc_r}${abc_p}) — оставить`];
  }

  function kpi(rows) {
    const totalRev = rows.reduce((s,r)=>s+r.revenue,0);
    const totalProf = rows.reduce((s,r)=>s+r.profit,0);
    const totalAd = rows.reduce((s,r)=>s+(r.ad_spend||0),0);
    return {
      total_revenue: totalRev,
      total_profit: totalProf,
      total_ad_spend: totalAd,
      avg_drr: totalRev > 0 ? totalAd / totalRev : 0,
      total_sales: rows.reduce((s,r)=>s+r.sales_qty,0),
      sku_count: rows.length,
      avg_margin: totalRev > 0 ? totalProf/totalRev : 0,
      sku_remove: rows.filter(r=>r.rec_code==='REMOVE').length,
      sku_remove_here: rows.filter(r=>r.rec_code==='REMOVE_HERE').length,
      sku_review: rows.filter(r=>r.rec_code==='REVIEW').length,
      sku_keep_star: rows.filter(r=>r.rec_code==='KEEP_STAR').length,
      sku_keep: rows.filter(r=>r.rec_code==='KEEP').length,
      revenue_at_risk_remove: rows.filter(r=>r.rec_code==='REMOVE').reduce((s,r)=>s+r.revenue,0),
      profit_at_risk_remove: rows.filter(r=>r.rec_code==='REMOVE').reduce((s,r)=>s+r.profit,0),
    };
  }

  /* ============ Rendering ============ */
  /* Вставляет шаблон легенды рекомендаций во все слоты .recs-legend-slot.
     Шаблон один (#recs-legend-template), клонируется в каждый слот.
     Вызывается один раз на boot — слотов всего 3 (ОЗОН, ВБ, сравнение МП). */
  function populateRecsLegend() {
    const tpl = document.getElementById('recs-legend-template');
    if (!tpl) return;
    document.querySelectorAll('.recs-legend-slot').forEach(slot => {
      if (slot.dataset.filled === '1') return;
      slot.appendChild(tpl.content.cloneNode(true));
      slot.dataset.filled = '1';
    });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c]));
  }

  function destroyChart(key) {
    if (STATE.charts[key]) { STATE.charts[key].destroy(); STATE.charts[key] = null; }
  }

  /* ============ Блок «Пересечение ассортимента» ============ */
  function renderVennBlock(cross) {
    const total    = cross.total_skus || 0;
    const both     = cross.on_both    || 0;
    const onlyOz   = cross.only_ozon  || 0;
    const onlyVb   = cross.only_vb    || 0;
    const onOz     = onlyOz + both;
    const onVb     = onlyVb + both;
    const pct = (n) => total > 0 ? Math.round((n / total) * 100) + '%' : '0%';

    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('venn-total', num(total));
    set('venn-oz', num(onOz));
    set('venn-oz-pct', pct(onOz));
    set('venn-vb', num(onVb));
    set('venn-vb-pct', pct(onVb));
    set('venn-both', num(both));
    set('venn-both-pct', pct(both));
    set('venn-only-oz', num(onlyOz));
    set('venn-only-oz-pct', pct(onlyOz));
    set('venn-only-vb', num(onlyVb));
    set('venn-only-vb-pct', pct(onlyVb));

    // Полоса-стэк (визуальные доли)
    const segOz   = document.getElementById('vb-seg-only-oz');
    const segBoth = document.getElementById('vb-seg-both');
    const segVb   = document.getElementById('vb-seg-only-vb');
    if (segOz && segBoth && segVb) {
      if (total > 0) {
        segOz.style.width   = (onlyOz / total * 100).toFixed(2) + '%';
        segBoth.style.width = (both   / total * 100).toFixed(2) + '%';
        segVb.style.width   = (onlyVb / total * 100).toFixed(2) + '%';
      } else {
        segOz.style.width = segBoth.style.width = segVb.style.width = '0%';
      }
    }

    // Диаграмма Венна на Canvas
    drawVenn(onOz, onVb, both, onlyOz, onlyVb);
  }

  function drawVenn(onOz, onVb, both, onlyOz, onlyVb) {
    const canvas = document.getElementById('venn-canvas');
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const W = 460, H = 260;
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    // Радиусы пропорциональны √(N) (площадь ≅ N), но с разумными min/max.
    const max = Math.max(onOz, onVb, 1);
    const Rmax = 92, Rmin = 60;
    const rOz = onOz <= 0 ? 0 : Math.max(Rmin, Math.min(Rmax, Rmax * Math.sqrt(onOz / max)));
    const rVb = onVb <= 0 ? 0 : Math.max(Rmin, Math.min(Rmax, Rmax * Math.sqrt(onVb / max)));

    // Расстояние между центрами: чем больше доля «обоих» — тем ближе
    const total = onOz + onVb - both;
    const overlapShare = total > 0 ? both / total : 0;
    const minDist = Math.abs(rOz - rVb) + 18;       // круги не полностью совпадают
    const maxDist = rOz + rVb - 8;                  // и всегда пересекаются, если both>0
    let dist;
    if (both <= 0) dist = rOz + rVb + 14;            // разведёны
    else dist = maxDist - (maxDist - minDist) * Math.min(1, overlapShare * 1.5);

    const cy = H / 2;
    const cxOz = (W - dist) / 2;
    const cxVb = cxOz + dist;

    // Фон
    ctx.save();
    // ОЗОН
    ctx.fillStyle = 'rgba(59, 130, 246, 0.40)';
    ctx.beginPath(); ctx.arc(cxOz, cy, rOz, 0, Math.PI * 2); ctx.fill();
    // ВБ
    ctx.fillStyle = 'rgba(168, 85, 247, 0.40)';
    ctx.beginPath(); ctx.arc(cxVb, cy, rVb, 0, Math.PI * 2); ctx.fill();
    // Обводки
    ctx.lineWidth = 2;
    ctx.strokeStyle = '#2f6bff';
    ctx.beginPath(); ctx.arc(cxOz, cy, rOz, 0, Math.PI * 2); ctx.stroke();
    ctx.strokeStyle = '#cb11ab';
    ctx.beginPath(); ctx.arc(cxVb, cy, rVb, 0, Math.PI * 2); ctx.stroke();
    ctx.restore();

    // Подписи
    ctx.fillStyle = '#1f2933';
    ctx.font = '600 12px -apple-system, "Segoe UI", system-ui, sans-serif';
    ctx.textAlign = 'center';

    // Надписи ОЗОН и ВБ сверху
    ctx.fillStyle = '#2f6bff';
    ctx.fillText('ОЗОН', cxOz - rOz * 0.15, cy - rOz - 10);
    ctx.fillStyle = '#cb11ab';
    ctx.fillText('Wildberries', cxVb + rVb * 0.15, cy - rVb - 10);

    // Цифры в секциях: только ОЗОН / оба / только ВБ
    ctx.font = '700 18px -apple-system, "Segoe UI", system-ui, sans-serif';
    // ОЗОН-онли — в левой части левого круга
    ctx.fillStyle = '#2f6bff';
    if (onlyOz > 0) ctx.fillText(num(onlyOz), cxOz - rOz * 0.55, cy + 5);
    // Центр пересечения
    ctx.fillStyle = '#b8860b';
    if (both > 0 && dist < rOz + rVb) {
      const cxBoth = (cxOz + cxVb) / 2;
      ctx.fillText(num(both), cxBoth, cy + 5);
    }
    // ВБ-онли
    ctx.fillStyle = '#cb11ab';
    if (onlyVb > 0) ctx.fillText(num(onlyVb), cxVb + rVb * 0.55, cy + 5);

    // Мелкие подписи под цифрами
    ctx.font = '500 10px -apple-system, "Segoe UI", system-ui, sans-serif';
    ctx.fillStyle = 'rgba(90,100,112,0.8)';
    if (onlyOz > 0) ctx.fillText('только ОЗОН', cxOz - rOz * 0.55, cy + 22);
    if (both > 0 && dist < rOz + rVb) {
      const cxBoth = (cxOz + cxVb) / 2;
      ctx.fillText('оба МП', cxBoth, cy + 22);
    }
    if (onlyVb > 0) ctx.fillText('только ВБ', cxVb + rVb * 0.55, cy + 22);
  }

  /* ============ Блок «Прогноз пересечения после плана действий» ============ */
  function renderForecastVennBlock(cross) {
    const fc = cross.forecast || {};
    const total    = fc.total_skus || 0;
    const both     = fc.on_both    || 0;
    const onlyOz   = fc.only_ozon  || 0;
    const onlyVb   = fc.only_vb    || 0;
    const onOz     = onlyOz + both;
    const onVb     = onlyVb + both;
    const pct = (n) => total > 0 ? Math.round((n / total) * 100) + '%' : '0%';

    // Текущие (фактические) числа для дельты
    const curTotal  = cross.total_skus || 0;
    const curBoth   = cross.on_both    || 0;
    const curOnlyOz = cross.only_ozon  || 0;
    const curOnlyVb = cross.only_vb    || 0;
    const curOnOz   = curOnlyOz + curBoth;
    const curOnVb   = curOnlyVb + curBoth;

    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    const setDelta = (id, fromV, toV, opts={}) => {
      const el = document.getElementById(id); if (!el) return;
      const d = toV - fromV;
      el.classList.remove('pos','neg','zero');
      if (d === 0) { el.textContent = '±0'; el.classList.add('zero'); return; }
      const goalIsLess = opts.goalIsLess !== false;
      const isGood = goalIsLess ? d < 0 : d > 0;
      el.classList.add(isGood ? 'pos' : 'neg');
      el.textContent = (d > 0 ? '+' : '') + num(d);
    };
    const setDeltaNeutral = (id, fromV, toV) => {
      const el = document.getElementById(id); if (!el) return;
      const d = toV - fromV;
      el.classList.remove('pos','neg','zero');
      if (d === 0) { el.textContent = '±0'; el.classList.add('zero'); return; }
      el.classList.add('zero');
      el.textContent = (d > 0 ? '+' : '') + num(d);
    };

    set('fc-venn-total', num(total));
    set('fc-venn-oz', num(onOz));
    set('fc-venn-oz-pct', pct(onOz));
    set('fc-venn-vb', num(onVb));
    set('fc-venn-vb-pct', pct(onVb));
    set('fc-venn-both', num(both));
    set('fc-venn-both-pct', pct(both));
    set('fc-venn-only-oz', num(onlyOz));
    set('fc-venn-only-oz-pct', pct(onlyOz));
    set('fc-venn-only-vb', num(onlyVb));
    set('fc-venn-only-vb-pct', pct(onlyVb));

    // Дельты (изменение от текущего к прогнозу)
    setDelta('fc-venn-total-d', curTotal, total, {goalIsLess: true});
    setDelta('fc-venn-oz-d',   curOnOz,  onOz,  {goalIsLess: true});
    setDelta('fc-venn-vb-d',   curOnVb,  onVb,  {goalIsLess: true});
    setDelta('fc-venn-both-d', curBoth,  both,  {goalIsLess: true}); // меньше пересечения — хорошо
    // Для «только ОЗОН/ВБ» дельта нейтральная: их уменьшение не «плохо» —
    // часть из этих SKU выводится из-за нерентабельности, это здоровая чистка.
    setDeltaNeutral('fc-venn-only-oz-d', curOnlyOz, onlyOz);
    setDeltaNeutral('fc-venn-only-vb-d', curOnlyVb, onlyVb);

    // Сводка «Что уйдёт»
    set('fc-removed-full',   num(fc.removed_full   || 0));
    set('fc-moved-off-ozon', num(fc.moved_off_ozon || 0));
    set('fc-moved-off-vb',   num(fc.moved_off_vb   || 0));
    set('fc-unchanged',      num(fc.unchanged      || 0));

    // Финансовый эффект: выручка и прибыль, которые уйдут с каждого МП.
    // Обратимся к STATE.computed.summary, чтобы взять базы по выбранному периоду для долей.
    const ozRevBase  = (STATE.computed?.summary?.ozon?.total_revenue) || 0;
    const ozProfBase = (STATE.computed?.summary?.ozon?.total_profit)  || 0;
    const vbRevBase  = (STATE.computed?.summary?.vb?.total_revenue)   || 0;
    const vbProfBase = (STATE.computed?.summary?.vb?.total_profit)    || 0;

    const revOffOz  = fc.rev_off_ozon  || 0;
    const profOffOz = fc.prof_off_ozon || 0;
    const revOffVb  = fc.rev_off_vb    || 0;
    const profOffVb = fc.prof_off_vb   || 0;

    // Помощники форматирования
    // Абсолютная доля в % (без знака), знак проставляется в вызывающем коде
    const shareTxtAbs = (part, total) => {
      if (!total || total === 0) return '—';
      return (Math.abs(part) / Math.abs(total) * 100).toFixed(1) + '%';
    };
    // Для прибыли: положительная величина в prof_off значит уйдёт прибыль (плохо),
    // отрицательная — уйдёт убыток (хорошо).
    const setProfImpact = (id, value) => {
      const el = document.getElementById(id); if (!el) return;
      el.textContent = (value >= 0 ? '−' : '+') + rub(Math.abs(value)).replace(' ₽', '') + ' ₽';
      el.classList.remove('pos','neg','zero');
      if (value > 0) el.classList.add('neg');         // уходит прибыль — красный
      else if (value < 0) el.classList.add('pos');    // уходит убыток — зелёный (эффект положительный)
      else el.classList.add('zero');
    };

    // Сигнал для прибыли: > 0 — уходит прибыль («−X%» красным в значении),
    // < 0 — уходит убыток («+X%» к прибыли — эффект положительный). Для «доли» берём абсолюты.
    const profSign = (v) => v > 0 ? '−' : (v < 0 ? '+' : '±');

    set('fc-rev-off-oz',  '−' + rub(revOffOz).replace(' ₽', '') + ' ₽');
    set('fc-rev-off-oz-pct', '−' + shareTxtAbs(revOffOz, ozRevBase) + ' от выручки ОЗОН');
    setProfImpact('fc-prof-off-oz', profOffOz);
    set('fc-prof-off-oz-pct', profSign(profOffOz) + shareTxtAbs(profOffOz, ozProfBase) + ' к прибыли ОЗОН');

    set('fc-rev-off-vb',  '−' + rub(revOffVb).replace(' ₽', '') + ' ₽');
    set('fc-rev-off-vb-pct', '−' + shareTxtAbs(revOffVb, vbRevBase) + ' от выручки ВБ');
    setProfImpact('fc-prof-off-vb', profOffVb);
    set('fc-prof-off-vb-pct', profSign(profOffVb) + shareTxtAbs(profOffVb, vbProfBase) + ' к прибыли ВБ');

    // Итого
    const revOffAll  = revOffOz  + revOffVb;
    const profOffAll = profOffOz + profOffVb;
    const revBaseAll  = ozRevBase  + vbRevBase;
    const profBaseAll = ozProfBase + vbProfBase;
    set('fc-rev-off-all',  '−' + rub(revOffAll).replace(' ₽', '') + ' ₽');
    set('fc-rev-off-all-pct', '−' + shareTxtAbs(revOffAll, revBaseAll) + ' от общего оборота');
    setProfImpact('fc-prof-off-all', profOffAll);
    set('fc-prof-off-all-pct', profSign(profOffAll) + shareTxtAbs(profOffAll, profBaseAll) + ' к общей прибыли');

    // Ключевой итог: насколько уменьшится «оба МП» в % от текущего
    const overlapDropEl = document.getElementById('fc-overlap-drop');
    if (overlapDropEl) {
      if (curBoth > 0) {
        const drop = Math.round((curBoth - both) / curBoth * 100);
        overlapDropEl.textContent = (drop >= 0 ? '−' : '+') + Math.abs(drop) + '%';
      } else {
        overlapDropEl.textContent = '—';
      }
    }

    // Полоса-стэк
    const segOz   = document.getElementById('fc-vb-seg-only-oz');
    const segBoth = document.getElementById('fc-vb-seg-both');
    const segVb   = document.getElementById('fc-vb-seg-only-vb');
    if (segOz && segBoth && segVb) {
      if (total > 0) {
        segOz.style.width   = (onlyOz / total * 100).toFixed(2) + '%';
        segBoth.style.width = (both   / total * 100).toFixed(2) + '%';
        segVb.style.width   = (onlyVb / total * 100).toFixed(2) + '%';
      } else {
        segOz.style.width = segBoth.style.width = segVb.style.width = '0%';
      }
    }

    // Venn-диаграмма на canvas
    drawVennGeneric('fc-venn-canvas', onOz, onVb, both, onlyOz, onlyVb);
  }

  // Рефактор-обёртка: drawVenn переносим в общую drawVennGeneric (используется и для текущего блока, и для прогноза).
  function drawVennGeneric(canvasId, onOz, onVb, both, onlyOz, onlyVb) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const W = 460, H = 260;
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const max = Math.max(onOz, onVb, 1);
    const Rmax = 92, Rmin = 60;
    const rOz = onOz <= 0 ? 0 : Math.max(Rmin, Math.min(Rmax, Rmax * Math.sqrt(onOz / max)));
    const rVb = onVb <= 0 ? 0 : Math.max(Rmin, Math.min(Rmax, Rmax * Math.sqrt(onVb / max)));
    const totalUnion = onOz + onVb - both;
    const overlapShare = totalUnion > 0 ? both / totalUnion : 0;
    const minDist = Math.abs(rOz - rVb) + 18;
    const maxDist = rOz + rVb - 8;
    let dist;
    if (both <= 0) dist = rOz + rVb + 14;
    else dist = maxDist - (maxDist - minDist) * Math.min(1, overlapShare * 1.5);
    const cy = H / 2;
    const cxOz = (W - dist) / 2;
    const cxVb = cxOz + dist;
    ctx.save();
    ctx.fillStyle = 'rgba(59, 130, 246, 0.40)';
    ctx.beginPath(); ctx.arc(cxOz, cy, rOz, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = 'rgba(168, 85, 247, 0.40)';
    ctx.beginPath(); ctx.arc(cxVb, cy, rVb, 0, Math.PI * 2); ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = '#2f6bff';
    ctx.beginPath(); ctx.arc(cxOz, cy, rOz, 0, Math.PI * 2); ctx.stroke();
    ctx.strokeStyle = '#cb11ab';
    ctx.beginPath(); ctx.arc(cxVb, cy, rVb, 0, Math.PI * 2); ctx.stroke();
    ctx.restore();
    ctx.fillStyle = '#1f2933';
    ctx.font = '600 12px -apple-system, "Segoe UI", system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillStyle = '#2f6bff';
    ctx.fillText('ОЗОН', cxOz - rOz * 0.15, cy - rOz - 10);
    ctx.fillStyle = '#cb11ab';
    ctx.fillText('Wildberries', cxVb + rVb * 0.15, cy - rVb - 10);
    ctx.font = '700 18px -apple-system, "Segoe UI", system-ui, sans-serif';
    ctx.fillStyle = '#2f6bff';
    if (onlyOz > 0) ctx.fillText(num(onlyOz), cxOz - rOz * 0.55, cy + 5);
    ctx.fillStyle = '#b8860b';
    if (both > 0 && dist < rOz + rVb) {
      const cxBoth = (cxOz + cxVb) / 2;
      ctx.fillText(num(both), cxBoth, cy + 5);
    }
    ctx.fillStyle = '#cb11ab';
    if (onlyVb > 0) ctx.fillText(num(onlyVb), cxVb + rVb * 0.55, cy + 5);
    ctx.font = '500 10px -apple-system, "Segoe UI", system-ui, sans-serif';
    ctx.fillStyle = 'rgba(90,100,112,0.8)';
    if (onlyOz > 0) ctx.fillText('только ОЗОН', cxOz - rOz * 0.55, cy + 22);
    if (both > 0 && dist < rOz + rVb) {
      const cxBoth = (cxOz + cxVb) / 2;
      ctx.fillText('оба МП', cxBoth, cy + 22);
    }
    if (onlyVb > 0) ctx.fillText('только ВБ', cxVb + rVb * 0.55, cy + 22);
  }

  /* ============ Блок «Год к году» ============ */
  // Цвет баров по году. Возвращает функцию (i) => css-color, где i — индекс года (0 = самый старый).
  // Последний год — яркий цвет МП; предыдущие — приглушённые оттенки того же цвета,
  // разной насыщенности/прозрачности — чем старше год, тем бледнее и серее.
  function yoyYearColor(baseHex, n) {
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(baseHex || '#5a6470'));
    const br = m ? parseInt(m[1], 16) : 90;
    const bg = m ? parseInt(m[2], 16) : 100;
    const bb = m ? parseInt(m[3], 16) : 112;
    // Нейтральный серый — к нему смешиваем старые годы.
    const gr = 150, gg = 160, gb = 170;
    return (i) => {
      const last = n - 1;
      if (i === last) return baseHex; // текущий год — яркий цвет МП
      // prevRank: 1 = предыдущий год (ближе к цвету), больше = старее (бледнее)
      const prevCount = Math.max(1, last); // сколько предыдущих лет
      const prevRank = last - i; // 1..prevCount
      // t ∈ (0..1]: 0 — почти как цвет МП, 1 — полностью серый
      const t = prevCount === 1 ? 0.55 : (0.35 + 0.55 * (prevRank - 1) / (prevCount - 1));
      const rr = Math.round(br + (gr - br) * t);
      const rg = Math.round(bg + (gg - bg) * t);
      const rb = Math.round(bb + (gb - bb) * t);
      // Прозрачность тоже снижаем для старых лет — мягче на фоне.
      const alpha = (0.85 - 0.25 * t).toFixed(2);
      return `rgba(${rr},${rg},${rb},${alpha})`;
    };
  }

  // Не зависит от выбранного периода — всегда берёт все raw-данные.
  function renderYoYBlock(metric = 'revenue', selector = '.yoy-grid') {
    // «margin» — отношение сумм, не сумма. Для неё собираем profit/revenue помесячно и считаем отношение внутри ячеек/сумм.
    const isMargin = metric === 'margin';
    // Собираем выбранную метрику по (месяц-число, год) для каждого МП и общего
    const buildByYearMonth = (rows, fld = (isMargin ? 'revenue' : metric)) => {
      // map: year -> Array(12) revenue
      const map = {};
      for (const r of rows) {
        const lbl = r.month;
        if (!lbl) continue;
        // Ярлык вида «Янв 2025»
        const parts = String(lbl).trim().split(/\s+/);
        let mIdx = -1, yr = null;
        if (parts.length === 2) {
          const sh = parts[0];
          // Ищем по MONTH_SHORT (значение → индекс в MONTH_ORDER)
          const fullName = Object.entries(MONTH_SHORT).find(([_, s]) => s === sh)?.[0];
          mIdx = fullName ? MONTH_ORDER.indexOf(fullName) : -1;
          yr = parseInt(parts[1], 10);
        } else if (parts.length === 1 && MONTH_ORDER.includes(parts[0])) {
          mIdx = MONTH_ORDER.indexOf(parts[0]);
          yr = r.year || null;
        }
        if (mIdx < 0 || !yr) continue;
        if (!map[yr]) map[yr] = new Array(12).fill(0);
        map[yr][mIdx] += (r[fld] || 0);
      }
      return map;
    };

    // Источник YoY: если в файле есть листы «...дашборд» — берём оттуда прямые итоги по МП.
    // Это фактические цифры МП, которые могут отличаться от агрегатов по SKU (листы «сводный»).
    const dashOz = (STATE.raw.dashOzon && STATE.raw.dashOzon.length) ? STATE.raw.dashOzon : null;
    const dashVb = (STATE.raw.dashVb   && STATE.raw.dashVb.length)   ? STATE.raw.dashVb   : null;
    const dashYa = (STATE.raw.dashYa   && STATE.raw.dashYa.length)   ? STATE.raw.dashYa   : null;
    const srcOz = dashOz || (STATE.raw.ozon   || []);
    const srcVb = dashVb || (STATE.raw.vb     || []);
    const srcYa = dashYa || (STATE.raw.yandex || []);

    // Для margin собираем и выручку, и прибыль помесячно
    const ozRevMap = buildByYearMonth(srcOz, 'revenue');
    const vbRevMap = buildByYearMonth(srcVb, 'revenue');
    const yaRevMap = buildByYearMonth(srcYa, 'revenue');
    const ozProfMap = isMargin ? buildByYearMonth(srcOz, 'profit') : null;
    const vbProfMap = isMargin ? buildByYearMonth(srcVb, 'profit') : null;
    const yaProfMap = isMargin ? buildByYearMonth(srcYa, 'profit') : null;

    const ozMap  = isMargin ? ozRevMap : buildByYearMonth(srcOz);
    const vbMap  = isMargin ? vbRevMap : buildByYearMonth(srcVb);
    const yaMap  = isMargin ? yaRevMap : buildByYearMonth(srcYa);

    // ОБЩИЕ удержания МП (нераспределённые) — помесячно. В товарных «Прибылях»
    // они не учтены, поэтому прибавляем их к прибыли/числителю маржи.
    // Оборот (revenue) НЕ трогаем — у удержаний нет выручки.
    const allHolds = (STATE.raw.holds || []);
    const ozHoldMap = buildByYearMonth(allHolds.filter(h => h.mp === 'ozon'), 'profit');
    const vbHoldMap = buildByYearMonth(allHolds.filter(h => h.mp === 'vb'),   'profit');
    const yaHoldMap = buildByYearMonth(allHolds.filter(h => h.mp === 'ya'),   'profit');
    const addHolds = (target, holdMap) => {
      for (const y of Object.keys(holdMap)) {
        if (!target[y]) target[y] = new Array(12).fill(0);
        for (let i = 0; i < 12; i++) target[y][i] += (holdMap[y][i] || 0);
      }
    };
    if (isMargin) {
      // Маржа: удержания идут в числитель (прибыль), знаменатель (выручка) без изменений.
      addHolds(ozProfMap, ozHoldMap);
      addHolds(vbProfMap, vbHoldMap);
      addHolds(yaProfMap, yaHoldMap);
    } else if (metric === 'profit') {
      // Прибыль: ozMap/vbMap здесь и есть помесячная прибыль товаров.
      addHolds(ozMap, ozHoldMap);
      addHolds(vbMap, vbHoldMap);
      addHolds(yaMap, yaHoldMap);
    }
    // Для revenue ничего не прибавляем.

    // Общий = сумма помесячных массивов (для margin это будет сумма выручки)
    const yearsSet = new Set([...Object.keys(ozMap), ...Object.keys(vbMap), ...Object.keys(yaMap)]);
    const allMap = {}, allRevMap = {}, allProfMap = {};
    for (const y of yearsSet) {
      const arr = new Array(12).fill(0);
      for (let i = 0; i < 12; i++) arr[i] = (ozMap[y]?.[i] || 0) + (vbMap[y]?.[i] || 0) + (yaMap[y]?.[i] || 0);
      allMap[y] = arr;
      if (isMargin) {
        const r = new Array(12).fill(0), p = new Array(12).fill(0);
        for (let i = 0; i < 12; i++) {
          r[i] = (ozRevMap[y]?.[i] || 0) + (vbRevMap[y]?.[i] || 0) + (yaRevMap[y]?.[i] || 0);
          p[i] = (ozProfMap[y]?.[i] || 0) + (vbProfMap[y]?.[i] || 0) + (yaProfMap[y]?.[i] || 0);
        }
        allRevMap[y] = r; allProfMap[y] = p;
      }
    }

    // «Присутствие» месяца определяем по выручке — чтобы YoY по прибыли/марже не выпадал в 0/отрицательных месяцах.
    let ozPresence = null, vbPresence = null, yaPresence = null, allPresence = null;
    if (metric !== 'revenue') {
      ozPresence = ozRevMap; vbPresence = vbRevMap; yaPresence = yaRevMap;
      allPresence = {};
      for (const y of yearsSet) {
        const arr = new Array(12).fill(0);
        for (let i = 0; i < 12; i++) arr[i] = (ozRevMap[y]?.[i] || 0) + (vbRevMap[y]?.[i] || 0) + (yaRevMap[y]?.[i] || 0);
        allPresence[y] = arr;
      }
    }

    const cards = [
      { selector: '.yoy-card[data-mp="oz"]',  map: ozMap,  rev: ozRevMap,  prof: ozProfMap,  presence: ozPresence,  color: '#2f6bff', name: 'ОЗОН' },
      { selector: '.yoy-card[data-mp="vb"]',  map: vbMap,  rev: vbRevMap,  prof: vbProfMap,  presence: vbPresence,  color: '#cb11ab', name: 'Wildberries' },
      { selector: '.yoy-card[data-mp="ya"]',  map: yaMap,  rev: yaRevMap,  prof: yaProfMap,  presence: yaPresence,  color: '#ffcc00', name: 'Yandex' },
      { selector: '.yoy-card[data-mp="all"]', map: allMap, rev: allRevMap, prof: allProfMap, presence: allPresence, color: '#16a360', name: 'Общий (ОЗОН+ВБ+Яндекс)' },
    ];

    // Годы для сравнения — отсортированы по возрастанию
    const allYears = [...yearsSet].map(Number).sort();

    // Корневой элемент блока (для выборки карточек только в этом блоке)
    const block = document.querySelector(selector);
    if (!block) return;

    // Форматтер значения (рубли или проценты)
    const fmtVal = (v) => isMargin ? ((v == null || !isFinite(v)) ? '—' : (v.toFixed(1) + '%')) : rub(v);

    cards.forEach(card => {
      const root = block.querySelector(card.selector);
      if (!root) return;

      // Итоги по всем годам — для строк
      // Для margin: total = sum(profit)/sum(revenue)*100; arr[i] = profit[i]/revenue[i]*100 (или null)
      const yearTotals = allYears.map(y => {
        if (isMargin) {
          const r = card.rev[y] || new Array(12).fill(0);
          const p = card.prof[y] || new Array(12).fill(0);
          const sumR = r.reduce((a,b)=>a+b,0);
          const sumP = p.reduce((a,b)=>a+b,0);
          const total = sumR > 0 ? (sumP / sumR * 100) : null;
          const arr = r.map((rv, i) => rv > 0 ? (p[i] / rv * 100) : null);
          return { year: y, total, arr };
        }
        const arr = card.map[y] || new Array(12).fill(0);
        return { year: y, total: arr.reduce((a,b)=>a+b,0), arr };
      });

      // Сопоставимый YoY — берём два последних года и их пересечение по месяцам.
      // Для прибыли/маржи в месяце может быть 0/отрицательным — считаем «присутствует» по выручке.
      let comparable = null;
      if (allYears.length >= 2) {
        const prev = allYears[allYears.length - 2];
        const curr = allYears[allYears.length - 1];
        const a = card.map[prev] || new Array(12).fill(0);
        const b = card.map[curr] || new Array(12).fill(0);
        const aPresent = card.presence ? (card.presence[prev] || new Array(12).fill(0)) : a;
        const bPresent = card.presence ? (card.presence[curr] || new Array(12).fill(0)) : b;
        const monthsBoth = [];
        if (isMargin) {
          // Для маржи: отдельно суммы prev/curr по revenue и profit в сопоставимых месяцах
          const aRev = card.rev[prev] || new Array(12).fill(0);
          const bRev = card.rev[curr] || new Array(12).fill(0);
          const aProf = card.prof[prev] || new Array(12).fill(0);
          const bProf = card.prof[curr] || new Array(12).fill(0);
          let prevR = 0, currR = 0, prevP = 0, currP = 0;
          for (let i = 0; i < 12; i++) {
            if (aPresent[i] > 0 && bPresent[i] > 0) {
              monthsBoth.push(MONTH_SHORT[MONTH_ORDER[i]]);
              prevR += aRev[i]; currR += bRev[i];
              prevP += aProf[i]; currP += bProf[i];
            }
          }
          const prevSum = prevR > 0 ? (prevP / prevR * 100) : null;
          const currSum = currR > 0 ? (currP / currR * 100) : null;
          comparable = { prev, curr, prevSum, currSum, monthsBoth };
        } else {
          let prevSum = 0, currSum = 0;
          for (let i = 0; i < 12; i++) {
            if (aPresent[i] > 0 && bPresent[i] > 0) {
              monthsBoth.push(MONTH_SHORT[MONTH_ORDER[i]]);
              prevSum += a[i];
              currSum += b[i];
            }
          }
          comparable = { prev, curr, prevSum, currSum, monthsBoth };
        }
      }

      // Рендер строк
      const rowsEl = root.querySelector('.yoy-rows');
      let html = yearTotals.map(it => `<div class="yoy-row"><span class="y">${it.year}</span><span class="v">${fmtVal(it.total)}</span></div>`).join('');

      // Строка с дельтой (сопоставимый период)
      let footTxt = '';
      if (comparable && comparable.monthsBoth.length > 0) {
        let dPct, dAbs, deltaTxt, absTxt;
        if (isMargin) {
          // Для маржи дельта — в пп (процентных пунктах), не в %
          const pp = (comparable.prevSum != null && comparable.currSum != null) ? (comparable.currSum - comparable.prevSum) : null;
          dPct = pp; // повторно используем для выбора цвета
          const sign = pp == null ? '' : (pp > 0 ? '+' : (pp < 0 ? '−' : ''));
          deltaTxt = pp == null ? '—' : (sign + Math.abs(pp).toFixed(1) + ' п.п.');
          absTxt = (comparable.prevSum == null ? '—' : comparable.prevSum.toFixed(1) + '%') + ' → ' + (comparable.currSum == null ? '—' : comparable.currSum.toFixed(1) + '%');
        } else {
          dPct = comparable.prevSum > 0 ? ((comparable.currSum - comparable.prevSum) / comparable.prevSum * 100) : null;
          dAbs = comparable.currSum - comparable.prevSum;
          const sign = dPct == null ? '' : (dPct > 0 ? '+' : '');
          deltaTxt = dPct == null ? '—' : (Math.abs(dPct) >= 1000 ? (sign + '>1000%') : sign + dPct.toFixed(1) + '%');
          absTxt = (dAbs >= 0 ? '+' : '−') + rub(Math.abs(dAbs));
        }
        const cls = dPct == null ? 'flat' : (dPct > 0.05 ? 'pos' : (dPct < -0.05 ? 'neg' : 'flat'));
        const arrow = cls === 'pos' ? '▲' : cls === 'neg' ? '▼' : '•';
        html += `<div class="yoy-row delta ${cls}"><span class="y">${comparable.curr} к ${comparable.prev}</span><span class="v">${arrow} ${deltaTxt}</span></div>`;
        footTxt = isMargin
          ? `Сравнение по сопоставимым месяцам (${comparable.monthsBoth.join(', ')}): ${absTxt}. Не равно разнице годовых итогов.`
          : `Сопоставимо: ${comparable.monthsBoth.join(', ')} · ${absTxt}`;
      } else {
        footTxt = 'Данных для YoY-сравнения пока недостаточно';
      }
      rowsEl.innerHTML = html;
      root.querySelector('.yoy-foot').textContent = footTxt;

      // Мини-график: везде — бары (для margin тоже, как у выручки/прибыли; только формат оси/тултипа в %)
      const canvas = root.querySelector('.yoy-chart canvas');
      if (canvas && window.Chart) {
        const key = 'yoy_' + metric + '_' + root.dataset.mp;
        destroyChart(key);
        const labels = MONTH_ORDER.map(m => MONTH_SHORT[m]);
        // Цвет по году: последний (текущий) — яркий цвет МП; предыдущие годы — различимые приглушённые оттенки.
        const yearColor = yoyYearColor(card.color, yearTotals.length);
        const datasets = yearTotals.map((it, i) => ({
          label: String(it.year),
          data: it.arr, // для margin null-месяцы Chart.js просто не рисует
          backgroundColor: yearColor(i),
          borderRadius: 3,
        }));
        const fmtTip = isMargin
          ? ((v) => v == null ? '—' : v.toFixed(1) + '%')
          : ((v) => rub(v));
        STATE.charts[key] = new Chart(canvas, {
          type: 'bar',
          data: { labels, datasets },
          options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
              legend: { display: true, position: 'bottom', labels: { boxWidth: 10, boxHeight: 10, padding: 8, font: { size: 10 } } },
              tooltip: { callbacks: { label: (ctx) => ctx.dataset.label + ': ' + fmtTip(ctx.raw) } }
            },
            scales: {
              x: { grid: { display: false }, ticks: { font: { size: 10 }, color: '#96a5af' } },
              y: isMargin
                ? { grid: { color: 'rgba(20,30,46,0.06)' }, ticks: { font: { size: 10 }, color: '#96a5af', callback: (v) => v + '%' }, beginAtZero: true }
                : { display: false, beginAtZero: true }
            }
          }
        });
      }
    });
  }

  function renderOverview() {
    const c = STATE.computed;
    const s = c.summary;

    document.getElementById('kpi-total-skus').textContent = num(s.cross.total_skus);
    document.getElementById('kpi-on-both').textContent = s.cross.on_both;
    document.getElementById('kpi-only-oz').textContent = s.cross.only_ozon;
    document.getElementById('kpi-only-vb').textContent = s.cross.only_vb;

    document.getElementById('kpi-oz-rev').textContent = rub(s.ozon.total_revenue);
    document.getElementById('kpi-oz-profit').textContent = rub(s.ozon.total_profit);
    document.getElementById('kpi-oz-margin').textContent = fmtPct(s.ozon.avg_margin);
    const ozDrrEl = document.getElementById('kpi-oz-drr'); if (ozDrrEl) ozDrrEl.textContent = fmtPct(s.ozon.avg_drr || 0);

    document.getElementById('kpi-vb-rev').textContent = rub(s.vb.total_revenue);
    document.getElementById('kpi-vb-profit').textContent = rub(s.vb.total_profit);
    document.getElementById('kpi-vb-margin').textContent = fmtPct(s.vb.avg_margin);
    const vbDrrEl = document.getElementById('kpi-vb-drr'); if (vbDrrEl) vbDrrEl.textContent = fmtPct(s.vb.avg_drr || 0);

    // KPI Яндекс — такая же карточка, как ОЗОН/ВБ (без участия в пересечениях).
    const yaRev = document.getElementById('kpi-ya-rev'); if (yaRev) yaRev.textContent = rub(s.ya.total_revenue);
    const yaProfit = document.getElementById('kpi-ya-profit'); if (yaProfit) yaProfit.textContent = rub(s.ya.total_profit);
    const yaMargin = document.getElementById('kpi-ya-margin'); if (yaMargin) yaMargin.textContent = fmtPct(s.ya.avg_margin);
    const yaDrrEl = document.getElementById('kpi-ya-drr'); if (yaDrrEl) yaDrrEl.textContent = fmtPct(s.ya.avg_drr || 0);

    // «Пересечение ассортимента» — блок Venn
    renderVennBlock(s.cross);
    // «Прогноз после плана действий» — второй блок Venn с прогнозом
    renderForecastVennBlock(s.cross);

    // «Год к году» — всегда по всем данным, без фильтра периода
    renderYoYBlock('revenue', '.yoy-grid:not(.yoy-grid-profit):not(.yoy-grid-margin)');
    renderYoYBlock('profit', '.yoy-grid-profit');
    renderYoYBlock('margin', '.yoy-grid-margin');

    // Period label. selectedMonths/allMonths — ключи «YYYY-MM»; для
    // отображения переводим в monthMeta.label («Июн 2026»).
    const k2l = new Map(STATE.monthMeta.map(m => [m.key, m.label || m.key]));
    const lab = (k) => k2l.get(k) || k;
    const selLab = STATE.selectedMonths.map(lab);
    const allLab = STATE.allMonths.map(lab);
    const periodTxt = STATE.selectedMonths.length === STATE.allMonths.length
      ? `все периоды (${allLab[0]} — ${allLab[allLab.length-1]})`
      : (selLab.length <= 4 ? selLab.join(', ') : `${selLab.length} периодов: ${selLab[0]} … ${selLab[selLab.length-1]}`);
    document.querySelectorAll('.period-label').forEach(el => el.textContent = periodTxt);

    // ABC dist
    const cats = ['A','B','C','D','Z'];
    destroyChart('abc-rev');
    STATE.charts['abc-rev'] = new Chart(document.getElementById('chart-abc-rev'), {
      type: 'bar',
      data: {
        labels: cats,
        datasets: [
          { label: 'ОЗОН', backgroundColor: '#2f6bff', data: cats.map(cat => c.ozon.filter(r=>r.abc_revenue===cat).length) },
          { label: 'Wildberries', backgroundColor: '#cb11ab', data: cats.map(cat => c.vb.filter(r=>r.abc_revenue===cat).length) },
        ]
      },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'top', align: 'end' } }, scales: { y: { beginAtZero: true, grid: { color: 'rgba(20,30,46,0.05)' } }, x: { grid: { display: false } } } }
    });

    destroyChart('abc-prof');
    STATE.charts['abc-prof'] = new Chart(document.getElementById('chart-abc-prof'), {
      type: 'bar',
      data: {
        labels: cats,
        datasets: [
          { label: 'ОЗОН', backgroundColor: '#2f6bff', data: cats.map(cat => c.ozon.filter(r=>r.abc_profit===cat).length) },
          { label: 'Wildberries', backgroundColor: '#cb11ab', data: cats.map(cat => c.vb.filter(r=>r.abc_profit===cat).length) },
        ]
      },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'top', align: 'end' } }, scales: { y: { beginAtZero: true, grid: { color: 'rgba(20,30,46,0.05)' } }, x: { grid: { display: false } } } }
    });

    destroyChart('recs');
    const recOrder = ['KEEP_STAR','KEEP','REVIEW','REMOVE_HERE','CONSIDER_REMOVE_HERE','REMOVE'];
    STATE.charts.recs = new Chart(document.getElementById('chart-recs'), {
      type: 'bar',
      data: {
        labels: recOrder.map(r => REC_LABELS[r]),
        datasets: [
          { label: 'ОЗОН', backgroundColor: '#2f6bff', data: recOrder.map(rc => c.ozon.filter(r=>r.rec_code===rc).length) },
          { label: 'Wildberries', backgroundColor: '#cb11ab', data: recOrder.map(rc => c.vb.filter(r=>r.rec_code===rc).length) },
        ]
      },
      options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'top', align: 'end' } }, scales: { x: { beginAtZero: true, grid: { color: 'rgba(20,30,46,0.05)' } }, y: { grid: { display: false } } } }
    });
  }

  /* ============ Tables (single MP) ============ */
  const MP_FILTERS = { ozon: { search:'', abc_rev:'', abc_prof:'', rec:'', sortKey:'revenue', sortDir:'desc' },
                      vb:   { search:'', abc_rev:'', abc_prof:'', rec:'', sortKey:'revenue', sortDir:'desc' },
                      ya:   { search:'', abc_rev:'', abc_prof:'', rec:'', sortKey:'revenue', sortDir:'desc' } };

  function bindFilters() {
    ['ozon','vb','ya'].forEach(mp => {
      const a = document.querySelector('.filters[data-mp="'+mp+'"]');
      if (!a || a.dataset.bound) return;
      a.dataset.bound = '1';
      a.querySelector('.search').addEventListener('input', e => { MP_FILTERS[mp].search = e.target.value.toLowerCase(); renderTable(mp, STATE.computed[mp]); });
      a.querySelector('.f-abc-rev').addEventListener('change', e => { MP_FILTERS[mp].abc_rev = e.target.value; renderTable(mp, STATE.computed[mp]); });
      a.querySelector('.f-abc-prof').addEventListener('change', e => { MP_FILTERS[mp].abc_prof = e.target.value; renderTable(mp, STATE.computed[mp]); });
      a.querySelector('.f-rec').addEventListener('change', e => { MP_FILTERS[mp].rec = e.target.value; renderTable(mp, STATE.computed[mp]); });
    });
    // Cross
    const cf = document.querySelector('.filters[data-mp="cross"]');
    if (cf && !cf.dataset.bound) {
      cf.dataset.bound = '1';
      cf.querySelector('.search').addEventListener('input', e => { CROSS_F.search = e.target.value.toLowerCase(); renderCross(); });
      cf.querySelector('.f-presence').addEventListener('change', e => { CROSS_F.presence = e.target.value; renderCross(); });
      cf.querySelector('.f-action').addEventListener('change', e => { CROSS_F.action = e.target.value; renderCross(); });
    }
  }

  const TABLE_COLS_MP = [
    { k:'sku', label:'Артикул', cls:'sku-cell' },
    { k:'name', label:'Наименование', cls:'name-cell' },
    { k:'status', label:'Статус', cls:'status-col', format:(v, r) => statusSelectHtml(r.sku, v, r.marketplace === 'VB' ? 'vb' : (r.marketplace === 'YA' ? 'yandex' : 'ozon')) },
    { k:'revenue', label:'Выручка', cls:'num', format:rub },
    { k:'profit', label:'Прибыль', cls:'num', format:rub, color:true },
    { k:'margin', label:'Маржа', cls:'num', format:fmtPct },
    { k:'drr', label:'ДРР', cls:'num', format:(v)=>fmtPct(v||0) },
    { k:'sales_qty', label:'Шт.', cls:'num', format:num },
    { k:'months_active', label:'Мес. с продаж.', cls:'num', format:(v)=>v },
    { k:'abc_revenue', label:'ABC выр.', format:(v)=>`<span class="pill ${v}">${v}</span>` },
    { k:'abc_profit', label:'ABC приб.', format:(v)=>`<span class="pill ${v}">${v}</span>` },
    { k:'rec_code', label:'Рекомендация', format:(v)=>`<span class="pill rec-${v}">${REC_LABELS[v]||v}</span>` },
  ];

  function renderTable(mp, rows) {
    bindFilters();
    const f = MP_FILTERS[mp];
    const table = document.getElementById('table-' + mp);
    const filterArea = document.querySelector('.filters[data-mp="'+mp+'"]');

    let filtered = rows.filter(r => {
      if (f.search) { const q = f.search; if (!r.sku.toLowerCase().includes(q) && !(r.name||'').toLowerCase().includes(q)) return false; }
      if (f.abc_rev && r.abc_revenue !== f.abc_rev) return false;
      if (f.abc_prof && r.abc_profit !== f.abc_prof) return false;
      if (f.rec && r.rec_code !== f.rec) return false;
      return true;
    });
    filtered.sort((a,b) => {
      const va = a[f.sortKey], vb = b[f.sortKey];
      if (typeof va === 'number') return f.sortDir==='asc' ? va-vb : vb-va;
      return f.sortDir==='asc' ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
    });

    filterArea.querySelector('.count').textContent = filtered.length;

    const thead = `<thead><tr>${TABLE_COLS_MP.map(c => {
      const sc = f.sortKey===c.k ? (f.sortDir==='asc'?'sort-asc':'sort-desc') : '';
      return `<th data-key="${c.k}" class="${sc}">${c.label}</th>`;
    }).join('')}</tr></thead>`;

    const tbody = `<tbody>${filtered.map(r => {
      return `<tr data-sku="${escapeAttr(r.sku)}" data-mp="${mp}">${TABLE_COLS_MP.map(c => {
        let v = r[c.k];
        let cls = c.cls || '';
        if (c.color && typeof v === 'number') cls += v < 0 ? ' neg' : (v > 0 ? ' pos' : '');
        const display = c.format ? c.format(v, r) : (v ?? '');
        return `<td class="${cls}">${display}</td>`;
      }).join('')}</tr>`;
    }).join('')}</tbody>`;

    table.innerHTML = thead + tbody;

    table.querySelectorAll('thead th').forEach(th => {
      th.addEventListener('click', () => {
        const k = th.dataset.key;
        if (f.sortKey === k) f.sortDir = f.sortDir==='asc'?'desc':'asc';
        else { f.sortKey = k; f.sortDir = 'desc'; }
        renderTable(mp, rows);
      });
    });
    table.querySelectorAll('tbody tr').forEach(tr => {
      tr.addEventListener('click', () => openModal(tr.dataset.sku, tr.dataset.mp));
    });
    bindStatusSelects(table);
  }

  /* ============ Cross-MP table ============ */
  const CROSS_F = { search:'', presence:'', action:'', sortKey:'_total_rev', sortDir:'desc' };

  function actionFor(r) {
    const isRm = (rec) => rec === 'REMOVE' || rec === 'REMOVE_HERE' || rec === 'CONSIDER_REMOVE_HERE';
    const ozRm = isRm(r.ozon_rec), vbRm = isRm(r.vb_rec);
    if (r.on_ozon && r.on_vb) {
      if (r.ozon_rec === 'KEEP_STAR' && r.vb_rec === 'KEEP_STAR') return 'STAR_BOTH';
      if (ozRm && vbRm) return 'REMOVE_BOTH';
      if (ozRm && !vbRm) return 'REMOVE_FROM_OZON';
      if (vbRm && !ozRm) return 'REMOVE_FROM_VB';
      return 'KEEP_BOTH';
    }
    if (r.on_ozon && !r.on_vb) {
      if (r.ozon_rec === 'REMOVE') return 'REMOVE_FROM_OZON';
      return 'OZON_ONLY';
    }
    if (r.on_vb && !r.on_ozon) {
      if (r.vb_rec === 'REMOVE') return 'REMOVE_FROM_VB';
      return 'VB_ONLY';
    }
    return '-';
  }

  /* ============ Определение колонок Сравнения МП и переключатель видимости ============ */
  // Колонки без «Наименования» — артикула достаточно. Группа (oz/vb/sys) нужна для пресетов.
  const CROSS_COLS = [
    { k:'sku',          label:'Артикул',       short:'Артикул',     group:'sys', cls:'sku-cell',   required:true },
    { k:'ozon_status',  label:'Статус Озон',   short:'Статус',     group:'oz',  cls:'status-col oz-col' },
    { k:'ozon_revenue', label:'ОЗОН: выручка', short:'Выручка',    group:'oz',  cls:'num oz-col' },
    { k:'ozon_profit',  label:'ОЗОН: прибыль', short:'Прибыль',    group:'oz',  cls:'num oz-col' },
    { k:'ozon_margin',  label:'ОЗОН: маржа',  short:'Маржа',      group:'oz',  cls:'num oz-col' },
    { k:'ozon_drr',     label:'ОЗОН: ДРР',     short:'ДРР',         group:'oz',  cls:'num oz-col' },
    { k:'ozon_sales',   label:'ОЗОН: шт.',     short:'Шт.',         group:'oz',  cls:'num oz-col' },
    { k:'ozon_aov',     label:'ОЗОН: ср. чек',  short:'Ср. чек',     group:'oz',  cls:'num oz-col' },
    { k:'ozon_abc',     label:'ОЗОН: ABC',     short:'ABC',         group:'oz',  cls:'oz-col' },
    { k:'vb_status',    label:'Статус ВБ',     short:'Статус',     group:'vb',  cls:'status-col vb-col' },
    { k:'vb_revenue',   label:'ВБ: выручка',   short:'Выручка',    group:'vb',  cls:'num vb-col' },
    { k:'vb_profit',    label:'ВБ: прибыль',   short:'Прибыль',    group:'vb',  cls:'num vb-col' },
    { k:'vb_margin',    label:'ВБ: маржа',    short:'Маржа',      group:'vb',  cls:'num vb-col' },
    { k:'vb_drr',       label:'ВБ: ДРР',       short:'ДРР',         group:'vb',  cls:'num vb-col' },
    { k:'vb_sales',     label:'ВБ: шт.',       short:'Шт.',         group:'vb',  cls:'num vb-col' },
    { k:'vb_aov',       label:'ВБ: ср. чек',    short:'Ср. чек',     group:'vb',  cls:'num vb-col' },
    { k:'vb_abc',       label:'ВБ: ABC',       short:'ABC',         group:'vb',  cls:'vb-col' },
    // Яндекс — опциональные колонки (скрыты по умолчанию). НЕ участвуют в «Решении».
    { k:'ya_status',    label:'Статус Яндекс', short:'Статус', group:'ya',  cls:'status-col ya-col' },
    { k:'ya_revenue',   label:'Яндекс: выручка', short:'Выручка', group:'ya', cls:'num ya-col' },
    { k:'ya_profit',    label:'Яндекс: прибыль', short:'Прибыль', group:'ya', cls:'num ya-col' },
    { k:'ya_margin',    label:'Яндекс: маржа',  short:'Маржа',   group:'ya', cls:'num ya-col' },
    { k:'ya_drr',       label:'Яндекс: ДРР',     short:'ДРР',      group:'ya', cls:'num ya-col' },
    { k:'ya_sales',     label:'Яндекс: шт.',     short:'Шт.',      group:'ya', cls:'num ya-col' },
    { k:'ya_aov',       label:'Яндекс: ср. чек',  short:'Ср. чек',  group:'ya', cls:'num ya-col' },
    { k:'ya_abc',       label:'Яндекс: ABC',     short:'ABC',      group:'ya', cls:'ya-col' },
    { k:'action',       label:'Решение',      short:'Решение',     group:'sys' },
  ];

  const CROSS_PRESETS = {
    all: () => CROSS_COLS.map(c => c.k),
    min: () => ['sku','ozon_revenue','vb_revenue','action'],
    oz:  () => ['sku', ...CROSS_COLS.filter(c=>c.group==='oz').map(c=>c.k), 'action'],
    vb:  () => ['sku', ...CROSS_COLS.filter(c=>c.group==='vb').map(c=>c.k), 'action'],
    ya:  () => ['sku', ...CROSS_COLS.filter(c=>c.group==='ya').map(c=>c.k)],
  };

  // Видимость колонок «Сравнение МП» хранится в памяти модуля (на время сессии).
  // Браузерное персистентное хранилище недоступно в preview-iframe прода,
  // поэтому состояние не персистим — по перезагрузке возвращаются все колонки.
  // По умолчанию скрыты: средний чек Озон и ВБ («ОЗОН: ср. чек» = ozon_aov,
  // «ВБ: ср. чек» = vb_aov) и обе колонки выручки («ОЗОН: выручка» = ozon_revenue,
  // «ВБ: выручка» = vb_revenue) — чтобы таблица помещалась в рабочую область.
  // Остальные колонки (включая «Статус Озон»/«Статус ВБ») — видны.
  // Включить скрытые можно через кнопку «Колонки».
  // Все колонки Яндекса скрыты по умолчанию — показ по галочке в «Колонках» (пригодится не часто).
  const CROSS_HIDDEN_DEFAULT = new Set(['ozon_aov', 'vb_aov', 'ozon_revenue', 'vb_revenue',
    'ya_status','ya_revenue','ya_profit','ya_margin','ya_drr','ya_sales','ya_aov','ya_abc']);
  function defaultCrossVisible() {
    return new Set(CROSS_COLS.map(c => c.k).filter(k => !CROSS_HIDDEN_DEFAULT.has(k)));
  }

  function loadCrossVisible() {
    return defaultCrossVisible();
  }

  function saveCrossVisible() {
    // no-op: персистентное хранилище недоступно в sandbox-iframe
  }

  let CROSS_VISIBLE = loadCrossVisible();

  function renderCrossColsPanel() {
    const list = document.getElementById('cross-cols-list');
    if (!list) return;
    list.innerHTML = CROSS_COLS.map(c => {
      const groupCls = c.group === 'oz' ? 'gr-oz' : (c.group === 'vb' ? 'gr-vb' : (c.group === 'ya' ? 'gr-ya' : ''));
      const checked = CROSS_VISIBLE.has(c.k) ? 'checked' : '';
      const disabled = c.required ? 'disabled' : '';
      return `<label class="${groupCls}">
        <input type="checkbox" data-col="${c.k}" ${checked} ${disabled}>
        <span>${c.label}</span>
      </label>`;
    }).join('');
    list.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.addEventListener('change', e => {
        const k = e.target.dataset.col;
        if (e.target.checked) CROSS_VISIBLE.add(k); else CROSS_VISIBLE.delete(k);
        // Обязательные не выключаем
        CROSS_COLS.filter(c => c.required).forEach(c => CROSS_VISIBLE.add(c.k));
        saveCrossVisible();
        renderCross();
      });
    });
  }

  function bindCrossColsPanel() {
    const btn = document.getElementById('cross-cols-btn');
    const panel = document.getElementById('cross-cols-panel');
    const wrap = btn?.parentElement;
    if (!btn || !panel || !wrap) return;
    // Защита от повторной привязки — на самом ЭЛЕМЕНТЕ, а не в переменной модуля:
    // при ре-рендере/повторном монтировании создаётся новая кнопка без флага,
    // и обработчик навешивается заново (раньше модульный флаг оставался true и
    // новая кнопка не реагировала — это и был баг «Колонки не нажимается»).
    if (btn.dataset.bound) return;
    btn.dataset.bound = '1';

    const closePanel = () => { panel.hidden = true; wrap.classList.remove('open'); btn.setAttribute('aria-expanded','false'); };
    const openPanel  = () => { panel.hidden = false; wrap.classList.add('open');  btn.setAttribute('aria-expanded','true'); };

    btn.addEventListener('click', e => {
      e.stopPropagation();
      if (panel.hidden) openPanel(); else closePanel();
    });
    document.addEventListener('click', e => {
      if (!panel.hidden && !wrap.contains(e.target)) closePanel();
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && !panel.hidden) closePanel(); });

    panel.querySelectorAll('[data-cols-preset]').forEach(b => {
      b.addEventListener('click', () => {
        const preset = b.dataset.colsPreset;
        const fn = CROSS_PRESETS[preset];
        if (!fn) return;
        CROSS_VISIBLE = new Set(fn());
        CROSS_COLS.filter(c => c.required).forEach(c => CROSS_VISIBLE.add(c.k));
        saveCrossVisible();
        renderCrossColsPanel();
        renderCross();
      });
    });
  }

  function renderCross() {
    bindFilters();
    bindCrossColsPanel();
    renderCrossColsPanel();
    const table = document.getElementById('table-cross');
    const cross = STATE.computed.cross;
    cross.forEach(r => { r.action = actionFor(r); r._total_rev = r.ozon_revenue + r.vb_revenue; });

    const ACTION_LABELS = {
      STAR_BOTH: '⭐ Звезда на обоих', KEEP_BOTH: 'Оставить на обоих',
      REMOVE_FROM_OZON: 'Убрать с ОЗОН', REMOVE_FROM_VB: 'Убрать с ВБ',
      REMOVE_BOTH: 'Удалить с обоих', OZON_ONLY: 'Только ОЗОН', VB_ONLY: 'Только ВБ',
    };
    const ACTION_PILL = {
      STAR_BOTH: 'rec-KEEP_STAR', KEEP_BOTH: 'rec-KEEP',
      REMOVE_FROM_OZON: 'rec-REMOVE_HERE', REMOVE_FROM_VB: 'rec-REMOVE_HERE',
      REMOVE_BOTH: 'rec-REMOVE', OZON_ONLY: 'rec-KEEP', VB_ONLY: 'rec-KEEP',
    };

    let filtered = cross.filter(r => {
      if (CROSS_F.search) {
        const q = CROSS_F.search;
        // Поиск по артикулу (нечувствительно к регистру) и наименованию сохраняем — name в объекте остаётся.
        if (!r.sku.toLowerCase().includes(q) && !(r.name||'').toLowerCase().includes(q)) return false;
      }
      if (CROSS_F.presence === 'both' && !(r.on_ozon && r.on_vb)) return false;
      if (CROSS_F.presence === 'only_ozon' && !(r.on_ozon && !r.on_vb)) return false;
      if (CROSS_F.presence === 'only_vb' && !(r.on_vb && !r.on_ozon)) return false;
      if (CROSS_F.action && r.action !== CROSS_F.action) return false;
      return true;
    });
    filtered.sort((a,b) => {
      const va = a[CROSS_F.sortKey], vb = b[CROSS_F.sortKey];
      if (typeof va === 'number') return CROSS_F.sortDir==='asc' ? va-vb : vb-va;
      return CROSS_F.sortDir==='asc' ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
    });

    document.querySelector('.filters[data-mp="cross"] .count').textContent = filtered.length;

    const noCell = '<span class="empty-cell">—</span>';

    // Рендер ячейки по имени колонки и текущей строке.
    function renderCell(c, r) {
      const baseCls = c.cls || '';
      switch (c.k) {
        case 'sku': return `<td class="sku-cell">${r.sku}</td>`;
        case 'ozon_status':  return `<td class="${baseCls}">${r.on_ozon ? statusSelectHtml(r.sku, r.ozon_status, 'ozon') : ''}</td>`;
        case 'vb_status':    return `<td class="${baseCls}">${r.on_vb ? statusSelectHtml(r.sku, r.vb_status, 'vb') : ''}</td>`;
        case 'ozon_revenue': return `<td class="${baseCls}">${r.on_ozon ? rub(r.ozon_revenue) : noCell}</td>`;
        case 'ozon_profit':  return `<td class="${baseCls} ${r.on_ozon ? (r.ozon_profit<0?'neg':'pos') : ''}">${r.on_ozon ? rub(r.ozon_profit) : ''}</td>`;
        case 'ozon_margin':  return `<td class="${baseCls}">${r.on_ozon ? fmtPct(r.ozon_margin) : ''}</td>`;
        case 'ozon_drr':     return `<td class="${baseCls}">${r.on_ozon ? fmtPct(r.ozon_drr) : ''}</td>`;
        case 'ozon_sales':   return `<td class="${baseCls}">${r.on_ozon ? num(r.ozon_sales) : ''}</td>`;
        case 'ozon_aov':     return `<td class="${baseCls}">${r.on_ozon && r.ozon_sales>0 ? rub(r.ozon_aov) : ''}</td>`;
        case 'ozon_abc':     return `<td class="${baseCls}">${r.on_ozon ? `<span class="pill ${r.ozon_abc[0]}">${r.ozon_abc}</span>` : ''}</td>`;
        case 'vb_revenue':   return `<td class="${baseCls}">${r.on_vb ? rub(r.vb_revenue) : noCell}</td>`;
        case 'vb_profit':    return `<td class="${baseCls} ${r.on_vb ? (r.vb_profit<0?'neg':'pos') : ''}">${r.on_vb ? rub(r.vb_profit) : ''}</td>`;
        case 'vb_margin':    return `<td class="${baseCls}">${r.on_vb ? fmtPct(r.vb_margin) : ''}</td>`;
        case 'vb_drr':       return `<td class="${baseCls}">${r.on_vb ? fmtPct(r.vb_drr) : ''}</td>`;
        case 'vb_sales':     return `<td class="${baseCls}">${r.on_vb ? num(r.vb_sales) : ''}</td>`;
        case 'vb_aov':       return `<td class="${baseCls}">${r.on_vb && r.vb_sales>0 ? rub(r.vb_aov) : ''}</td>`;
        case 'vb_abc':       return `<td class="${baseCls}">${r.on_vb ? `<span class="pill ${r.vb_abc[0]}">${r.vb_abc}</span>` : ''}</td>`;
        // Яндекс — справочные колонки (без редактируемого статус-селекта: МП не в логике разделения).
        case 'ya_status':    return `<td class="${baseCls}">${r.on_ya ? statusSelectHtml(r.sku, r.ya_status, 'yandex') : ''}</td>`;
        case 'ya_revenue':   return `<td class="${baseCls}">${r.on_ya ? rub(r.ya_revenue) : noCell}</td>`;
        case 'ya_profit':    return `<td class="${baseCls} ${r.on_ya ? (r.ya_profit<0?'neg':'pos') : ''}">${r.on_ya ? rub(r.ya_profit) : ''}</td>`;
        case 'ya_margin':    return `<td class="${baseCls}">${r.on_ya ? fmtPct(r.ya_margin) : ''}</td>`;
        case 'ya_drr':       return `<td class="${baseCls}">${r.on_ya ? fmtPct(r.ya_drr) : ''}</td>`;
        case 'ya_sales':     return `<td class="${baseCls}">${r.on_ya ? num(r.ya_sales) : ''}</td>`;
        case 'ya_aov':       return `<td class="${baseCls}">${r.on_ya && r.ya_sales>0 ? rub(r.ya_aov) : ''}</td>`;
        case 'ya_abc':       return `<td class="${baseCls}">${r.on_ya ? `<span class="pill ${r.ya_abc[0]}">${r.ya_abc}</span>` : ''}</td>`;
        case 'action':       return `<td><span class="pill ${ACTION_PILL[r.action]||''}">${ACTION_LABELS[r.action]||'—'}</span></td>`;
        default: return '<td></td>';
      }
    }

    const visibleCols = CROSS_COLS.filter(c => CROSS_VISIBLE.has(c.k));

    table.innerHTML = `
      <thead><tr>${visibleCols.map(c => {
        const sc = CROSS_F.sortKey===c.k ? (CROSS_F.sortDir==='asc'?'sort-asc':'sort-desc') : '';
        return `<th data-key="${c.k}" class="${[c.cls||'', sc].filter(Boolean).join(' ')}">${c.label}</th>`;
      }).join('')}</tr></thead>
      <tbody>${filtered.map(r => `
        <tr data-sku="${escapeAttr(r.sku)}" title="${escapeAttr(r.name||'')}">${visibleCols.map(c => renderCell(c, r)).join('')}</tr>`).join('')}
      </tbody>
    `;

    table.querySelectorAll('thead th').forEach(th => {
      th.addEventListener('click', () => {
        const k = th.dataset.key;
        if (CROSS_F.sortKey === k) CROSS_F.sortDir = CROSS_F.sortDir==='asc'?'desc':'asc';
        else { CROSS_F.sortKey = k; CROSS_F.sortDir = 'desc'; }
        renderCross();
      });
    });
    table.querySelectorAll('tbody tr').forEach(tr => {
      tr.addEventListener('click', () => {
        const sku = tr.dataset.sku;
        const cr = STATE.computed.cross.find(c => c.sku === sku);
        const mp = (cr.ozon_revenue >= cr.vb_revenue && cr.on_ozon) ? 'ozon' : (cr.on_vb ? 'vb' : 'ozon');
        openModal(sku, mp);
      });
    });
    bindStatusSelects(table);
  }

  /* ============ Actions tab ============ */
  function renderActions() {
    const cross = STATE.computed.cross;
    const isRm = (rec) => rec === 'REMOVE' || rec === 'REMOVE_HERE' || rec === 'CONSIDER_REMOVE_HERE';

    const groups = { REMOVE_BOTH: [], REMOVE_FROM_OZON: [], REMOVE_FROM_VB: [], REVIEW: [], STAR: [] };

    cross.forEach(r => {
      const ozRm = r.on_ozon && isRm(r.ozon_rec);
      const vbRm = r.on_vb && isRm(r.vb_rec);
      if (r.on_ozon && r.on_vb) {
        if (ozRm && vbRm) groups.REMOVE_BOTH.push(r);
        else if (ozRm) groups.REMOVE_FROM_OZON.push(r);
        else if (vbRm) groups.REMOVE_FROM_VB.push(r);
      } else if (r.on_ozon && !r.on_vb && r.ozon_rec === 'REMOVE') {
        groups.REMOVE_BOTH.push(r);
      } else if (r.on_vb && !r.on_ozon && r.vb_rec === 'REMOVE') {
        groups.REMOVE_BOTH.push(r);
      }
      if (r.ozon_rec === 'REVIEW' || r.vb_rec === 'REVIEW') groups.REVIEW.push(r);
      if (r.ozon_rec === 'KEEP_STAR' || r.vb_rec === 'KEEP_STAR') groups.STAR.push(r);
    });

    document.getElementById('ac-remove-both').textContent = groups.REMOVE_BOTH.length;
    document.getElementById('ac-remove-ozon').textContent = groups.REMOVE_FROM_OZON.length;
    document.getElementById('ac-remove-vb').textContent = groups.REMOVE_FROM_VB.length;
    document.getElementById('ac-review').textContent = groups.REVIEW.length;
    document.getElementById('ac-star').textContent = groups.STAR.length;

    function fillList(id, rows, mpField) {
      const el = document.getElementById(id);
      el.innerHTML = rows.slice(0, 200).map(r => {
        const rev = mpField === 'ozon' ? r.ozon_revenue : (mpField === 'vb' ? r.vb_revenue : (r.ozon_revenue + r.vb_revenue));
        const prof = mpField === 'ozon' ? r.ozon_profit : (mpField === 'vb' ? r.vb_profit : (r.ozon_profit + r.vb_profit));
        const profCls = prof < 0 ? 'item-prof neg' : (prof > 0 ? 'item-prof pos' : 'item-prof');
        return `<div class="sku-item"><span class="item-sku">${escapeHtml(r.sku)}</span><span class="item-nums"><span class="item-rev">${rub(rev)}</span><span class="${profCls}">${rub(prof)}</span></span></div>`;
      }).join('') + (rows.length > 200 ? `<div class="sku-item"><span class="item-sku">+ ещё ${rows.length-200}</span></div>` : '');
    }
    fillList('list-remove-both', groups.REMOVE_BOTH, 'both');
    fillList('list-remove-ozon', groups.REMOVE_FROM_OZON, 'ozon');
    fillList('list-remove-vb', groups.REMOVE_FROM_VB, 'vb');
    fillList('list-review', groups.REVIEW, 'both');
    fillList('list-star', groups.STAR.sort((a,b)=>(b.ozon_revenue+b.vb_revenue)-(a.ozon_revenue+a.vb_revenue)), 'both');

    const impact = document.getElementById('impact');
    const ozRemoveLost = groups.REMOVE_FROM_OZON.reduce((s,r)=>s+(r.ozon_revenue||0),0);
    const ozRemoveSavedLoss = groups.REMOVE_FROM_OZON.reduce((s,r)=>s+(r.ozon_profit<0?-r.ozon_profit:0),0);
    const vbRemoveLost = groups.REMOVE_FROM_VB.reduce((s,r)=>s+(r.vb_revenue||0),0);
    const vbRemoveSavedLoss = groups.REMOVE_FROM_VB.reduce((s,r)=>s+(r.vb_profit<0?-r.vb_profit:0),0);
    const bothRemoveLost = groups.REMOVE_BOTH.reduce((s,r)=>s+(r.ozon_revenue||0)+(r.vb_revenue||0),0);
    const bothRemoveSavedLoss = groups.REMOVE_BOTH.reduce((s,r)=>{
      const ozL = r.ozon_profit<0?-r.ozon_profit:0;
      const vbL = r.vb_profit<0?-r.vb_profit:0;
      return s + ozL + vbL;
    },0);
    const ozRecords = STATE.computed.ozon.length;
    const vbRecords = STATE.computed.vb.length;
    const removedSku = groups.REMOVE_BOTH.length + groups.REMOVE_FROM_OZON.length + groups.REMOVE_FROM_VB.length;
    const reductionPct = (ozRecords + vbRecords) > 0 ? (removedSku/(ozRecords+vbRecords))*100 : 0;

    impact.innerHTML = `
      <div class="impact-row"><span class="label">Записей в матрице (ОЗОН + ВБ)</span><span class="val">${ozRecords + vbRecords}</span></div>
      <div class="impact-row"><span class="label">К сокращению</span><span class="val">${removedSku} (−${reductionPct.toFixed(1)}%)</span></div>
      <div class="impact-row"><span class="label">Выручка, теряемая при выводе с обоих МП</span><span class="val">${rub(bothRemoveLost)}</span></div>
      <div class="impact-row"><span class="label">Прекращаемый убыток (полное удаление)</span><span class="val good">+${rub(bothRemoveSavedLoss)}</span></div>
      <div class="impact-row"><span class="label">Выручка, переносимая с ОЗОН на ВБ</span><span class="val">${rub(ozRemoveLost)}</span></div>
      <div class="impact-row"><span class="label">Прекращаемый убыток (вывод с ОЗОН)</span><span class="val good">+${rub(ozRemoveSavedLoss)}</span></div>
      <div class="impact-row"><span class="label">Выручка, переносимая с ВБ на ОЗОН</span><span class="val">${rub(vbRemoveLost)}</span></div>
      <div class="impact-row"><span class="label">Прекращаемый убыток (вывод с ВБ)</span><span class="val good">+${rub(vbRemoveSavedLoss)}</span></div>
    `;

    // Bind export buttons (re-bind every time because group data changes)
    document.querySelectorAll('.btn-export').forEach(btn => {
      const newBtn = btn.cloneNode(true);
      btn.parentNode.replaceChild(newBtn, btn);
      newBtn.addEventListener('click', () => exportCsv(newBtn.dataset.export, groups));
    });
  }

  function exportCsv(type, groups) {
    let rows = [], filename = '';
    if (type === 'REMOVE_BOTH') { rows = groups.REMOVE_BOTH; filename = 'remove_both.csv'; }
    else if (type === 'REMOVE_FROM_OZON') { rows = groups.REMOVE_FROM_OZON; filename = 'remove_from_ozon.csv'; }
    else if (type === 'REMOVE_FROM_VB') { rows = groups.REMOVE_FROM_VB; filename = 'remove_from_vb.csv'; }
    else if (type === 'REVIEW') { rows = groups.REVIEW; filename = 'review.csv'; }
    else if (type === 'STAR') { rows = groups.STAR; filename = 'stars.csv'; }

    const headers = ['Артикул','Наименование','На ОЗОН','ОЗОН выручка','ОЗОН прибыль','ОЗОН ABC','ОЗОН рекомендация','На ВБ','ВБ выручка','ВБ прибыль','ВБ ABC','ВБ рекомендация'];
    const lines = [headers.join(';')];
    rows.forEach(r => {
      lines.push([
        csvEsc(r.sku), csvEsc(r.name),
        r.on_ozon ? 'Да' : 'Нет',
        r.on_ozon ? Math.round(r.ozon_revenue) : '',
        r.on_ozon ? Math.round(r.ozon_profit) : '',
        r.on_ozon ? r.ozon_abc : '',
        r.on_ozon ? (REC_LABELS[r.ozon_rec]||r.ozon_rec) : '',
        r.on_vb ? 'Да' : 'Нет',
        r.on_vb ? Math.round(r.vb_revenue) : '',
        r.on_vb ? Math.round(r.vb_profit) : '',
        r.on_vb ? r.vb_abc : '',
        r.on_vb ? (REC_LABELS[r.vb_rec]||r.vb_rec) : '',
      ].join(';'));
    });
    const blob = new Blob(['\uFEFF' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename; a.click();
  }

  function csvEsc(v) {
    if (v == null) return '';
    const s = String(v).replace(/"/g, '""');
    return /[;\n"]/.test(s) ? `"${s}"` : s;
  }
  function escapeAttr(s) { return String(s||'').replace(/"/g, '&quot;'); }

  /* ============ Modal ============ */
  function bindModal() {
    document.getElementById('modal-close').addEventListener('click', closeModal);
    document.getElementById('modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
  }
  function closeModal() {
    document.getElementById('modal').setAttribute('hidden','');
    if (STATE.charts.modal) { STATE.charts.modal.destroy(); STATE.charts.modal = null; }
  }
  function openModal(sku, mp) {
    const oz = STATE.computed.ozon.find(r => r.sku === sku);
    const vb = STATE.computed.vb.find(r => r.sku === sku);
    const ya = (STATE.computed.ya || []).find(r => r.sku === sku);
    // Яндекс — самостоятельная модалка (МП не в логике разделения OZ/ВБ).
    if (mp === 'ya') { openModalYa(sku, ya); return; }
    const primary = mp === 'ozon' ? oz : vb;
    const name = (primary && primary.name) || (oz && oz.name) || (vb && vb.name) || sku;

    function block(rec, mpName, mpClass) {
      if (!rec) return `<div class="modal-section"><h4><span class="mp-tag ${mpClass}">${mpName}</span></h4><p class="muted">SKU не представлен на этом маркетплейсе.</p></div>`;
      return `
        <div class="modal-section">
          <h4><span class="mp-tag ${mpClass}">${mpName}</span></h4>
          <div class="stat-row"><span class="l">Выручка</span><span class="v">${rub(rec.revenue)}</span></div>
          <div class="stat-row"><span class="l">Реклама (факт)</span><span class="v">${rub(rec.ad_spend || 0)}</span></div>
          <div class="stat-row"><span class="l">ДРР</span><span class="v">${fmtPct(rec.drr || 0)}</span></div>
          <div class="stat-row"><span class="l">Прибыль <span class="hint">(с учётом рекламы)</span></span><span class="v ${rec.profit<0?'neg':'pos'}">${rub(rec.profit)}</span></div>
          <div class="stat-row"><span class="l">Маржа <span class="hint">(с учётом рекламы)</span></span><span class="v">${fmtPct(rec.margin)}</span></div>
          <div class="stat-row"><span class="l">Продажи, шт.</span><span class="v">${num(rec.sales_qty)}</span></div>
          <div class="stat-row"><span class="l">Возвраты, шт.</span><span class="v">${num(rec.returns_qty)}</span></div>
          <div class="stat-row"><span class="l">Активных месяцев</span><span class="v">${rec.months_active}/${STATE.selectedMonths.length}</span></div>
          <div class="stat-row"><span class="l">ABC выручка / прибыль</span><span class="v"><span class="pill ${rec.abc_revenue}">${rec.abc_revenue}</span> <span class="pill ${rec.abc_profit}">${rec.abc_profit}</span></span></div>
          <div class="reco-block">
            <div class="reco-head"><span class="pill rec-${rec.rec_code}">${REC_LABELS[rec.rec_code]||rec.rec_code}</span></div>
            <p class="muted">${escapeHtml(rec.rec_reason)}</p>
          </div>
        </div>
      `;
    }

    document.getElementById('modal-body').innerHTML = `
      <h3>${escapeHtml(name)}</h3>
      <div class="modal-sku">${escapeHtml(sku)} · период: <span class="period-label"></span></div>
      <div class="modal-grid">${block(oz, 'ОЗОН', 'oz')}${block(vb, 'Wildberries', 'vb')}</div>
      <div class="modal-section modal-dynamics" style="margin-top:24px;">
        <div class="dyn-head">
          <h4>Динамика по месяцам <span class="muted">(все месяцы)</span></h4>
          <div class="dyn-tabs" role="tablist">
            <button type="button" class="dyn-tab active" data-metric="revenue" role="tab" aria-selected="true">Выручка</button>
            <button type="button" class="dyn-tab" data-metric="profit" role="tab" aria-selected="false">Прибыль</button>
            <button type="button" class="dyn-tab" data-metric="margin" role="tab" aria-selected="false">Маржа</button>
          </div>
        </div>
        <div class="dyn-sub muted" id="dyn-sub"></div>
        <div class="modal-chart"><canvas id="modal-chart"></canvas></div>
      </div>
    `;
    // Update period label inside modal. selectedMonths — ключи «YYYY-MM»,
    // поэтому для читаемости переводим в monthMeta.label («Июн 2026»).
    const keyToLabel = new Map(STATE.monthMeta.map(m => [m.key, m.label || m.key]));
    const selLabels = STATE.selectedMonths.map(k => keyToLabel.get(k) || k);
    const periodTxt = STATE.selectedMonths.length === STATE.allMonths.length
      ? `все периоды` : (selLabels.length <= 4 ? selLabels.join(', ') : `${selLabels.length} периодов`);
    document.querySelector('#modal-body .period-label').textContent = periodTxt;

    document.getElementById('modal').removeAttribute('hidden');

    // Собираем месячные ряды выручки/прибыли/маржи по SKU для обоих МП.
    // Маржа за месяц = profit/revenue*100 (если выручка пустая — null, чтобы разрыв линии, а не 0%).
    // months = уникальные ключи «YYYY-MM»; сопоставляем по r.ym (не по названию
    // месяца, иначе ряды пустые). Подписи оси X берём из monthMeta.label.
    const months = STATE.allMonths;
    const monthLabels = STATE.monthMeta.map(m => m.label || m.key);
    const sel = new Set(STATE.selectedMonths);
    function series(rawArr, field) {
      return months.map(m => {
        const rows = rawArr.filter(r => r.sku===sku && r.ym===m);
        if (!rows.length) return 0;
        return rows.reduce((s,r) => s + (r[field]||0), 0);
      });
    }
    function marginSeries(rawArr) {
      return months.map(m => {
        const rows = rawArr.filter(r => r.sku===sku && r.ym===m);
        if (!rows.length) return null;
        const rev = rows.reduce((s,r) => s + (r.revenue||0), 0);
        const pr  = rows.reduce((s,r) => s + (r.profit||0), 0);
        if (!rev) return null;
        // Возвращаем долю (0.275), чтобы fmtPct() и tick callback сами перевели в %.
        return +(pr/rev).toFixed(4);
      });
    }
    const data = {
      revenue: { oz: series(STATE.raw.ozon, 'revenue'), vb: series(STATE.raw.vb, 'revenue') },
      profit:  { oz: series(STATE.raw.ozon, 'profit'),  vb: series(STATE.raw.vb, 'profit')  },
      margin:  { oz: marginSeries(STATE.raw.ozon),       vb: marginSeries(STATE.raw.vb)       },
    };

    function renderDynChart(metric) {
      if (STATE.charts.modal) { STATE.charts.modal.destroy(); STATE.charts.modal = null; }
      const cfg = {
        revenue: { fmt: v => v==null?'—':rub(v), tickFmt: v => num(v/1000)+' к', sub: 'Выручка по месяцам, ₽ · сумма продаж из Excel', isCurrency:true },
        profit:  { fmt: v => v==null?'—':rub(v), tickFmt: v => num(v/1000)+' к', sub: 'Прибыль по месяцам, ₽ · выручка − себестоимость − комиссии − реклама', isCurrency:true, zeroLine:true },
        margin:  { fmt: v => v==null?'—':fmtPct(v), tickFmt: v => (v*100).toFixed(0)+'%', sub: 'Маржа по месяцам, % · прибыль / выручка', isCurrency:false, zeroLine:true },
      }[metric];
      document.getElementById('dyn-sub').textContent = cfg.sub;
      const ozData = data[metric].oz;
      const vbData = data[metric].vb;
      STATE.charts.modal = new Chart(document.getElementById('modal-chart'), {
        type: 'line',
        data: {
          labels: monthLabels,
          datasets: [
            ...(oz ? [{ label:'ОЗОН', borderColor:'#2f6bff', backgroundColor:'rgba(47,107,255,0.12)', data: ozData, tension:0.35, fill:true, borderWidth:2.5, spanGaps:false, pointRadius: months.map(m => sel.has(m) ? 5 : 3), pointHoverRadius: 6 }] : []),
            ...(vb ? [{ label:'Wildberries', borderColor:'#cb11ab', backgroundColor:'rgba(203,17,171,0.10)', data: vbData, tension:0.35, fill:true, borderWidth:2.5, spanGaps:false, pointRadius: months.map(m => sel.has(m) ? 5 : 3), pointHoverRadius: 6 }] : []),
          ],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          plugins: {
            legend: { position: 'top', align: 'end', labels: { boxWidth: 12, boxHeight: 12 } },
            tooltip: { callbacks: { label: (ctx) => ctx.dataset.label + ': ' + cfg.fmt(ctx.raw) } }
          },
          scales: {
            y: {
              ticks: { callback: (v) => cfg.tickFmt(v) },
              grid: {
                color: (ctx) => (cfg.zeroLine && ctx.tick.value === 0) ? 'rgba(90,100,112,0.35)' : 'rgba(20,30,46,0.06)',
                lineWidth: (ctx) => (cfg.zeroLine && ctx.tick.value === 0) ? 1.5 : 1,
              }
            },
            x: { grid: { display: false } }
          }
        }
      });
    }

    renderDynChart('revenue');
    // Переключение табов
    document.querySelectorAll('#modal-body .dyn-tab').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('#modal-body .dyn-tab').forEach(b => {
          b.classList.remove('active');
          b.setAttribute('aria-selected','false');
        });
        btn.classList.add('active');
        btn.setAttribute('aria-selected','true');
        renderDynChart(btn.dataset.metric);
      });
    });
  }

  // Отдельная модалка для Яндекса: один блок + динамика по месяцам по Яндексу.
  function openModalYa(sku, rec) {
    const name = (rec && rec.name) || sku;
    function block(r) {
      if (!r) return `<div class="modal-section"><h4><span class="mp-tag yandex">Яндекс</span></h4><p class="muted">SKU не представлен на Яндексе.</p></div>`;
      return `
        <div class="modal-section">
          <h4><span class="mp-tag yandex">Яндекс</span></h4>
          <div class="stat-row"><span class="l">Выручка</span><span class="v">${rub(r.revenue)}</span></div>
          <div class="stat-row"><span class="l">Прибыль</span><span class="v ${r.profit<0?'neg':'pos'}">${rub(r.profit)}</span></div>
          <div class="stat-row"><span class="l">Маржа</span><span class="v">${fmtPct(r.margin)}</span></div>
          <div class="stat-row"><span class="l">Продажи, шт.</span><span class="v">${num(r.sales_qty)}</span></div>
          <div class="stat-row"><span class="l">Возвраты, шт.</span><span class="v">${num(r.returns_qty)}</span></div>
          <div class="stat-row"><span class="l">Активных месяцев</span><span class="v">${r.months_active}/${STATE.selectedMonths.length}</span></div>
          <div class="stat-row"><span class="l">ABC выручка / прибыль</span><span class="v"><span class="pill ${r.abc_revenue}">${r.abc_revenue}</span> <span class="pill ${r.abc_profit}">${r.abc_profit}</span></span></div>
          <div class="reco-block">
            <div class="reco-head"><span class="pill rec-${r.rec_code}">${REC_LABELS[r.rec_code]||r.rec_code}</span></div>
            <p class="muted">${escapeHtml(r.rec_reason||'')}</p>
          </div>
        </div>
      `;
    }
    document.getElementById('modal-body').innerHTML = `
      <h3>${escapeHtml(name)}</h3>
      <div class="modal-sku">${escapeHtml(sku)} · период: <span class="period-label"></span></div>
      <div class="modal-grid modal-grid-1">${block(rec)}</div>
      <div class="modal-section modal-dynamics" style="margin-top:24px;">
        <div class="dyn-head">
          <h4>Динамика по месяцам <span class="muted">(все месяцы)</span></h4>
          <div class="dyn-tabs" role="tablist">
            <button type="button" class="dyn-tab active" data-metric="revenue" role="tab" aria-selected="true">Выручка</button>
            <button type="button" class="dyn-tab" data-metric="profit" role="tab" aria-selected="false">Прибыль</button>
            <button type="button" class="dyn-tab" data-metric="margin" role="tab" aria-selected="false">Маржа</button>
          </div>
        </div>
        <div class="dyn-sub muted" id="dyn-sub"></div>
        <div class="modal-chart"><canvas id="modal-chart"></canvas></div>
      </div>
    `;
    const keyToLabel = new Map(STATE.monthMeta.map(m => [m.key, m.label || m.key]));
    const selLabels = STATE.selectedMonths.map(k => keyToLabel.get(k) || k);
    const periodTxt = STATE.selectedMonths.length === STATE.allMonths.length
      ? `все периоды` : (selLabels.length <= 4 ? selLabels.join(', ') : `${selLabels.length} периодов`);
    document.querySelector('#modal-body .period-label').textContent = periodTxt;
    document.getElementById('modal').removeAttribute('hidden');

    const months = STATE.allMonths;
    const monthLabels = STATE.monthMeta.map(m => m.label || m.key);
    const sel = new Set(STATE.selectedMonths);
    const src = STATE.raw.yandex || [];
    function series(field) {
      return months.map(m => {
        const rows = src.filter(r => r.sku===sku && r.ym===m);
        if (!rows.length) return 0;
        return rows.reduce((s,r) => s + (r[field]||0), 0);
      });
    }
    function marginSeries() {
      return months.map(m => {
        const rows = src.filter(r => r.sku===sku && r.ym===m);
        if (!rows.length) return null;
        const rev = rows.reduce((s,r) => s + (r.revenue||0), 0);
        const pr  = rows.reduce((s,r) => s + (r.profit||0), 0);
        if (!rev) return null;
        return +(pr/rev).toFixed(4);
      });
    }
    const data = { revenue: series('revenue'), profit: series('profit'), margin: marginSeries() };
    function renderDynChart(metric) {
      if (STATE.charts.modal) { STATE.charts.modal.destroy(); STATE.charts.modal = null; }
      const cfg = {
        revenue: { fmt: v => v==null?'—':rub(v), tickFmt: v => num(v/1000)+' к', sub: 'Выручка по месяцам, ₽', zeroLine:false },
        profit:  { fmt: v => v==null?'—':rub(v), tickFmt: v => num(v/1000)+' к', sub: 'Прибыль по месяцам, ₽', zeroLine:true },
        margin:  { fmt: v => v==null?'—':fmtPct(v), tickFmt: v => (v*100).toFixed(0)+'%', sub: 'Маржа по месяцам, %', zeroLine:true },
      }[metric];
      document.getElementById('dyn-sub').textContent = cfg.sub;
      STATE.charts.modal = new Chart(document.getElementById('modal-chart'), {
        type: 'line',
        data: { labels: monthLabels, datasets: [{ label:'Яндекс', borderColor:'#ffcc00', backgroundColor:'rgba(255,204,0,0.14)', data: data[metric], tension:0.35, fill:true, borderWidth:2.5, spanGaps:false, pointRadius: months.map(m => sel.has(m) ? 5 : 3), pointHoverRadius: 6 }] },
        options: {
          responsive: true, maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          plugins: {
            legend: { position: 'top', align: 'end', labels: { boxWidth: 12, boxHeight: 12 } },
            tooltip: { callbacks: { label: (ctx) => ctx.dataset.label + ': ' + cfg.fmt(ctx.raw) } }
          },
          scales: {
            y: { ticks: { callback: (v) => cfg.tickFmt(v) },
              grid: { color: (ctx) => (cfg.zeroLine && ctx.tick.value === 0) ? 'rgba(90,100,112,0.35)' : 'rgba(20,30,46,0.06)',
                lineWidth: (ctx) => (cfg.zeroLine && ctx.tick.value === 0) ? 1.5 : 1 } },
            x: { grid: { display: false } }
          }
        }
      });
    }
    renderDynChart('revenue');
    document.querySelectorAll('#modal-body .dyn-tab').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('#modal-body .dyn-tab').forEach(b => { b.classList.remove('active'); b.setAttribute('aria-selected','false'); });
        btn.classList.add('active'); btn.setAttribute('aria-selected','true');
        renderDynChart(btn.dataset.metric);
      });
    });
  }

  /* ============ Загрузка данных из API ============ */
  function transformApi(payload) {
    const upper = (s) => String(s || '').trim().toUpperCase();
    // Уникальный ключ месяца «YYYY-MM» — чтобы периодный фильтр не путал
    // один и тот же месяц РАЗНЫХ лет (Июнь 2024 ≠ Июнь 2025 ≠ Июнь 2026).
    const ymKey = (month, year) => {
      const idx = MONTH_ORDER.indexOf(month);
      return (year && idx >= 0) ? `${year}-${String(idx + 1).padStart(2, '0')}` : String(month || '');
    };
    const norm = (r) => ({
      sku: upper(r.sku), name: r.name || '',
      month: r.month, year: r.year || null, ym: ymKey(r.month, r.year || null),
      revenue: +r.revenue || 0, profit: +r.profit || 0,
      sales_qty: +r.sales_qty || 0, orders_qty: +r.orders_qty || 0,
      returns_qty: +r.returns_qty || 0, margin: +r.margin || 0,
      ad_spend: +r.ad_spend || 0,
      status: r.status || '',
    });
    const rows = payload && payload.rows ? payload.rows : [];
    const ozon = rows.filter((r) => r.mp === 'ozon').map(norm);
    const vb = rows.filter((r) => r.mp === 'vb').map(norm);
    // Яндекс.Маркет — самостоятельный МП, НЕ участвует в логике разделения
    // ассортимента (Venn / Решение / План действий). Нужен только для
    // отдельной вкладки, KPI/YoY на «Обзоре» и опциональных колонок в «Сравнении».
    const yandex = rows.filter((r) => r.mp === 'ya').map(norm);
    // Общие (нераспределённые) удержания МП — для итоговой прибыли/маржи в YoY.
    const holdRows = payload && payload.holds ? payload.holds : [];
    const holds = holdRows.map((h) => ({
      mp: h.mp, month: h.month, year: h.year || null, profit: +h.profit || 0,
    }));
    const meta = (payload && payload.months ? payload.months : [])
      .slice()
      .sort((a, b) => (a.year - b.year) || (a.month - b.month));
    STATE.monthMeta = meta.map((m) => ({
      // Ключ — уникальный «YYYY-MM» (совпадает с r.ym), чтобы периодный
      // фильтр различал годы. Отображение по-прежнему через label.
      key: (m.year ? `${m.year}-${String(m.month).padStart(2, '0')}` : (MONTH_ORDER[m.month - 1] || String(m.month))),
      start: m.period_start, end: m.period_end, label: m.label,
    }));
    STATE.allMonths = STATE.monthMeta.map((m) => m.key);
    return { ozon, vb, yandex, holds, dashOzon: [], dashVb: [], dashYa: [] };
  }

  function destroyAllCharts() {
    Object.keys(STATE.charts || {}).forEach((k) => {
      try { if (STATE.charts[k]) STATE.charts[k].destroy(); } catch (_) {}
      STATE.charts[k] = null;
    });
    STATE.charts = {};
  }

  /* ============ Фильтр периода (календарь, стиль RNP) ============ */
  function isoDate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${dd}`;
  }
  function parseISO(s) {
    if (!s) return null;
    const [y, m, d] = s.split('-').map(Number);
    return new Date(y, m - 1, d);
  }
  function fmtRu(iso) {
    if (!iso) return '';
    const [y, m, d] = iso.split('-');
    return `${d}.${m}.${y}`;
  }
  function abcBaseDate() {
    const mm = STATE.monthMeta;
    if (mm.length && mm[mm.length - 1].end) return parseISO(mm[mm.length - 1].end);
    return new Date();
  }
  function abcPresetRange(kind) {
    const base = abcBaseDate();
    const y = base.getFullYear(), m = base.getMonth();
    let from, to;
    if (kind === 'month') { from = new Date(y, m, 1); to = new Date(y, m + 1, 0); }
    else if (kind === 'quarter') { const q = Math.floor(m / 3); from = new Date(y, q * 3, 1); to = new Date(y, q * 3 + 3, 0); }
    else { from = new Date(y, 0, 1); to = new Date(y, 11, 31); }
    return { from: isoDate(from), to: isoDate(to) };
  }
  // Диапазон дат → набор месяцев (пересечение с period_start/period_end месяца).
  function selectedMonthsFromPeriod() {
    const f = STATE.period.from, t = STATE.period.to;
    if (!f && !t) return STATE.allMonths.slice();
    const lo = f || '0000-01-01', hi = t || '9999-12-31';
    const got = STATE.monthMeta.filter((m) => {
      if (!m.start || !m.end) return true;
      return m.start <= hi && m.end >= lo;
    }).map((m) => m.key);
    return got.length ? got : STATE.allMonths.slice();
  }
  function abcPeriodLabel() {
    const f = STATE.period.from, t = STATE.period.to;
    if (!f && !t) return `Все месяцы (${STATE.allMonths.length})`;
    if (f && t) return f === t ? fmtRu(f) : `${fmtRu(f)} — ${fmtRu(t)}`;
    return fmtRu(f || t);
  }
  function refreshAbcPeriodLabel() {
    const el = document.getElementById('abc-period-text');
    if (el) el.textContent = abcPeriodLabel();
  }

  function bindAbcPeriod() {
    const periodWrap = document.getElementById('abc-period');
    const fieldEl = document.getElementById('abc-period-field');
    const calEl = document.getElementById('abc-cal');
    if (!fieldEl || !calEl) return;

    const MONTHS_RU = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
    const DOW_RU = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
    const cal = { from: '', to: '', view: null, open: false };

    function openCal() {
      cal.from = STATE.period.from || '';
      cal.to = STATE.period.to || '';
      const b = cal.from ? parseISO(cal.from) : abcBaseDate();
      cal.view = new Date(b.getFullYear(), b.getMonth(), 1);
      cal.open = true;
      calEl.hidden = false;
      fieldEl.classList.add('open');
      drawCal();
    }
    function closeCal() {
      cal.open = false;
      calEl.hidden = true;
      fieldEl.classList.remove('open');
    }
    function applyPeriod() {
      STATE.period.from = cal.from || '';
      STATE.period.to = cal.to || '';
      STATE.selectedMonths = selectedMonthsFromPeriod();
      refreshAbcPeriodLabel();
      closeCal();
      recomputeAndRender();
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
      for (let i = lead; i > 0; i--) { cells += dayCell(new Date(y, mo, 1 - i), true, pos); pos = (pos + 1) % 7; }
      for (let d = 1; d <= daysIn; d++) { cells += dayCell(new Date(y, mo, d), false, pos); pos = (pos + 1) % 7; }
      const fill = 42 - (lead + daysIn);
      for (let i = 1; i <= fill; i++) { cells += dayCell(new Date(y, mo + 1, i), true, pos); pos = (pos + 1) % 7; }
      return `<div class="rnp-cal-month">
        <div class="rnp-cal-mtitle">${title}</div>
        <div class="rnp-cal-dow">${DOW_RU.map((x) => `<span>${x}</span>`).join('')}</div>
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
          <button type="button" class="rnp-cal-preset" data-cal-preset="all">Все месяцы</button>
          <button type="button" class="rnp-cal-preset" data-cal-preset="month">Тек. месяц</button>
          <button type="button" class="rnp-cal-preset" data-cal-preset="quarter">Тек. квартал</button>
          <button type="button" class="rnp-cal-preset" data-cal-preset="year">Тек. год</button>
        </div>`;
    }

    fieldEl.addEventListener('click', (e) => { e.stopPropagation(); cal.open ? closeCal() : openCal(); });
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
        else { const r = abcPresetRange(kind); cal.from = r.from; cal.to = r.to; }
        applyPeriod();
        return;
      }
      const day = e.target.closest('[data-cal-day]');
      if (day) {
        const iso = day.getAttribute('data-cal-day');
        if (!cal.from || (cal.from && cal.to)) { cal.from = iso; cal.to = ''; drawCal(); }
        else {
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

  /* ============ Под-навигация вкладок внутри ABC ============ */
  function bindSubNav() {
    const root = STATE.rootEl || document;
    const tabs = root.querySelectorAll('.abc-subnav .tab');
    const activate = (name) => {
      STATE.activeTab = name;               // запоминаем выбор для следующего возврата
      tabs.forEach((b) => { b.classList.toggle('active', b.dataset.tab === name); });
      root.querySelectorAll('.tab-panel').forEach((p) => {
        p.classList.toggle('active', p.id === 'tab-' + name);
      });
    };
    tabs.forEach((btn) => {
      btn.addEventListener('click', () => activate(btn.dataset.tab));
    });
    // Восстановить ранее выбранную под-вкладку (после перерисовки DOM).
    if (STATE.activeTab && STATE.activeTab !== 'overview') activate(STATE.activeTab);
  }

  /* ============ Разметка дашборда (5 вкладок, без «Данные») ============ */
  function dashboardHTML() {
    return `<div class="abc-dash">
  <div class="abc-head">
    <div class="abc-head-titles">
      <h2 class="abc-title">Анализ товарной матрицы</h2>
      <p class="abc-subtitle">ОЗОН × Wildberries × Яндекс · <span class="period-label">—</span></p>
    </div>
  </div>

  <div class="abc-toolbar">
    <nav class="abc-subnav tabs">
      <button class="tab active" data-tab="overview">Обзор</button>
      <button class="tab" data-tab="ozon">ОЗОН</button>
      <button class="tab" data-tab="vb">Wildberries</button>
      <button class="tab" data-tab="yandex">Яндекс</button>
      <button class="tab" data-tab="cross">Сравнение МП</button>
      <button class="tab" data-tab="actions">План действий</button>
    </nav>
    <div class="abc-period rnp-period" id="abc-period">
      <button class="abc-period-field rnp-period-field" id="abc-period-field" type="button">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
        <span class="abc-period-text rnp-period-text" id="abc-period-text">Все месяцы</span>
        <span class="abc-period-chev">▾</span>
      </button>
      <div class="abc-cal rnp-cal" id="abc-cal" hidden></div>
    </div>
  </div>

  <main class="abc-main">

  <section id="tab-overview" class="tab-panel active">
    <div class="page-intro">
      <h2>Сводка по товарной матрице</h2>
      <p class="lede">Помесячная агрегация продаж по двум маркетплейсам. Каждый товар классифицирован по ABC (выручка и прибыль) и получил рекомендацию: оставить, пересмотреть, вывести с конкретного МП или удалить совсем.</p>
    </div>

    <div class="kpi-grid kpi-grid-4">
      <div class="kpi-card">
        <div class="kpi-label">Всего уникальных SKU</div>
        <div class="kpi-value" id="kpi-total-skus">—</div>
        <div class="kpi-foot"><span id="kpi-on-both">—</span> на обоих МП · <span id="kpi-only-oz">—</span> только ОЗОН · <span id="kpi-only-vb">—</span> только ВБ</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Выручка ОЗОН</div>
        <div class="kpi-value oz" id="kpi-oz-rev">—</div>
        <div class="kpi-foot">Прибыль: <span id="kpi-oz-profit">—</span> · Маржа <span id="kpi-oz-margin">—</span> · ДРР <span id="kpi-oz-drr">—</span></div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Выручка Wildberries</div>
        <div class="kpi-value vb" id="kpi-vb-rev">—</div>
        <div class="kpi-foot">Прибыль: <span id="kpi-vb-profit">—</span> · Маржа <span id="kpi-vb-margin">—</span> · ДРР <span id="kpi-vb-drr">—</span></div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Выручка Яндекс</div>
        <div class="kpi-value ya" id="kpi-ya-rev">—</div>
        <div class="kpi-foot">Прибыль: <span id="kpi-ya-profit">—</span> · Маржа <span id="kpi-ya-margin">—</span> · ДРР <span id="kpi-ya-drr">—</span></div>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <h3>Год к году — выручка</h3>
        <p class="muted">Сравнение за <b>весь период</b>, независимо от выбранного выше фильтра. Для честного YoY показана «сопоставимая» выручка — только те месяцы, которые есть в обоих годах.</p>
      </div>
      <div class="yoy-grid yoy-grid-4">
        <div class="yoy-card" data-mp="oz"><div class="yoy-head">ОЗОН</div><div class="yoy-rows"></div><div class="yoy-foot muted"></div><div class="yoy-chart"><canvas></canvas></div></div>
        <div class="yoy-card" data-mp="vb"><div class="yoy-head">Wildberries</div><div class="yoy-rows"></div><div class="yoy-foot muted"></div><div class="yoy-chart"><canvas></canvas></div></div>
        <div class="yoy-card" data-mp="ya"><div class="yoy-head">Яндекс</div><div class="yoy-rows"></div><div class="yoy-foot muted"></div><div class="yoy-chart"><canvas></canvas></div></div>
        <div class="yoy-card" data-mp="all"><div class="yoy-head">ОБЩИЙ (ОЗОН + ВБ + Яндекс)</div><div class="yoy-rows"></div><div class="yoy-foot muted"></div><div class="yoy-chart"><canvas></canvas></div></div>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <h3>Год к году — прибыль</h3>
        <p class="muted">То же сравнение за <b>весь период</b>, но по прибыли. Сопоставимая прибыль считается только по тем месяцам, которые были активны в обоих годах (по выручке).</p>
      </div>
      <div class="yoy-grid yoy-grid-profit yoy-grid-4">
        <div class="yoy-card" data-mp="oz"><div class="yoy-head">ОЗОН</div><div class="yoy-rows"></div><div class="yoy-foot muted"></div><div class="yoy-chart"><canvas></canvas></div></div>
        <div class="yoy-card" data-mp="vb"><div class="yoy-head">Wildberries</div><div class="yoy-rows"></div><div class="yoy-foot muted"></div><div class="yoy-chart"><canvas></canvas></div></div>
        <div class="yoy-card" data-mp="ya"><div class="yoy-head">Яндекс</div><div class="yoy-rows"></div><div class="yoy-foot muted"></div><div class="yoy-chart"><canvas></canvas></div></div>
        <div class="yoy-card" data-mp="all"><div class="yoy-head">ОБЩИЙ (ОЗОН + ВБ + Яндекс)</div><div class="yoy-rows"></div><div class="yoy-foot muted"></div><div class="yoy-chart"><canvas></canvas></div></div>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <h3>Год к году — маржинальность</h3>
        <p class="muted">Отношение прибыли к выручке за <b>весь период</b>. Сопоставимая маржа считается по суммам обеих величин в месяцах, активных в обоих годах; дельта — в процентных пунктах (pp).</p>
      </div>
      <div class="yoy-grid yoy-grid-margin yoy-grid-4">
        <div class="yoy-card" data-mp="oz"><div class="yoy-head">ОЗОН</div><div class="yoy-rows"></div><div class="yoy-foot muted"></div><div class="yoy-chart"><canvas></canvas></div></div>
        <div class="yoy-card" data-mp="vb"><div class="yoy-head">Wildberries</div><div class="yoy-rows"></div><div class="yoy-foot muted"></div><div class="yoy-chart"><canvas></canvas></div></div>
        <div class="yoy-card" data-mp="ya"><div class="yoy-head">Яндекс</div><div class="yoy-rows"></div><div class="yoy-foot muted"></div><div class="yoy-chart"><canvas></canvas></div></div>
        <div class="yoy-card" data-mp="all"><div class="yoy-head">ОБЩИЙ (ОЗОН + ВБ + Яндекс)</div><div class="yoy-rows"></div><div class="yoy-foot muted"></div><div class="yoy-chart"><canvas></canvas></div></div>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <h3>Пересечение ассортимента</h3>
        <p class="muted">Сколько SKU представлены на ОЗОН, на ВБ, на обоих одновременно и только на одном. Цель — минимизировать пересечение.</p>
      </div>
      <div class="venn-row">
        <div class="venn-stats">
          <div class="venn-stat"><div class="vs-label">Всего SKU</div><div class="vs-value" id="venn-total">—</div></div>
          <div class="venn-stat oz"><div class="vs-label">На ОЗОН <span class="vs-sub" id="venn-oz-pct">—</span></div><div class="vs-value" id="venn-oz">—</div></div>
          <div class="venn-stat vb"><div class="vs-label">На Wildberries <span class="vs-sub" id="venn-vb-pct">—</span></div><div class="vs-value" id="venn-vb">—</div></div>
          <div class="venn-stat both"><div class="vs-label">На обоих МП <span class="vs-sub" id="venn-both-pct">—</span></div><div class="vs-value" id="venn-both">—</div></div>
          <div class="venn-stat only-oz"><div class="vs-label">Только ОЗОН <span class="vs-sub" id="venn-only-oz-pct">—</span></div><div class="vs-value" id="venn-only-oz">—</div></div>
          <div class="venn-stat only-vb"><div class="vs-label">Только ВБ <span class="vs-sub" id="venn-only-vb-pct">—</span></div><div class="vs-value" id="venn-only-vb">—</div></div>
        </div>
        <div class="venn-canvas-wrap"><canvas id="venn-canvas" width="460" height="260"></canvas></div>
      </div>
      <div class="venn-bar">
        <div class="vb-seg only-oz" id="vb-seg-only-oz" title="Только ОЗОН"></div>
        <div class="vb-seg both" id="vb-seg-both" title="На обоих"></div>
        <div class="vb-seg only-vb" id="vb-seg-only-vb" title="Только ВБ"></div>
      </div>
      <div class="venn-bar-legend">
        <span><i class="sw oz"></i>Только ОЗОН</span>
        <span><i class="sw both"></i>На обоих МП</span>
        <span><i class="sw vb"></i>Только Wildberries</span>
      </div>
    </div>

    <div class="card forecast-card">
      <div class="card-head">
        <h3>Прогноз: пересечение после плана действий <span class="badge-forecast">ПРОГНОЗ</span></h3>
        <p class="muted">Как будет выглядеть матрица после выполнения всех рекомендаций «Вывести» / «Убрать с этого МП» / «Рассмотреть перенос» из «Плана действий». Цель — максимально разойти матрицы ОЗОН и ВБ.</p>
      </div>
      <div class="venn-row">
        <div class="venn-stats">
          <div class="venn-stat"><div class="vs-label">Всего SKU <span class="vs-delta" id="fc-venn-total-d">—</span></div><div class="vs-value" id="fc-venn-total">—</div></div>
          <div class="venn-stat oz"><div class="vs-label">На ОЗОН <span class="vs-sub" id="fc-venn-oz-pct">—</span> <span class="vs-delta" id="fc-venn-oz-d">—</span></div><div class="vs-value" id="fc-venn-oz">—</div></div>
          <div class="venn-stat vb"><div class="vs-label">На Wildberries <span class="vs-sub" id="fc-venn-vb-pct">—</span> <span class="vs-delta" id="fc-venn-vb-d">—</span></div><div class="vs-value" id="fc-venn-vb">—</div></div>
          <div class="venn-stat both"><div class="vs-label">На обоих МП <span class="vs-sub" id="fc-venn-both-pct">—</span> <span class="vs-delta" id="fc-venn-both-d">—</span></div><div class="vs-value" id="fc-venn-both">—</div></div>
          <div class="venn-stat only-oz"><div class="vs-label">Только ОЗОН <span class="vs-sub" id="fc-venn-only-oz-pct">—</span> <span class="vs-delta" id="fc-venn-only-oz-d">—</span></div><div class="vs-value" id="fc-venn-only-oz">—</div></div>
          <div class="venn-stat only-vb"><div class="vs-label">Только ВБ <span class="vs-sub" id="fc-venn-only-vb-pct">—</span> <span class="vs-delta" id="fc-venn-only-vb-d">—</span></div><div class="vs-value" id="fc-venn-only-vb">—</div></div>
        </div>
        <div class="venn-canvas-wrap"><canvas id="fc-venn-canvas" width="460" height="260"></canvas></div>
      </div>
      <div class="venn-bar">
        <div class="vb-seg only-oz" id="fc-vb-seg-only-oz" title="Только ОЗОН"></div>
        <div class="vb-seg both" id="fc-vb-seg-both" title="На обоих"></div>
        <div class="vb-seg only-vb" id="fc-vb-seg-only-vb" title="Только ВБ"></div>
      </div>
      <div class="venn-bar-legend">
        <span><i class="sw oz"></i>Только ОЗОН</span>
        <span><i class="sw both"></i>На обоих МП</span>
        <span><i class="sw vb"></i>Только Wildberries</span>
      </div>
      <div class="forecast-summary">
        <div class="fs-cell highlight"><div class="fs-label">Пересечение сократится</div><div class="fs-value" id="fc-overlap-drop">—</div><div class="fs-sub">от текущего «на обоих МП»</div></div>
        <div class="fs-cell danger"><div class="fs-label">Вывести совсем</div><div class="fs-value" id="fc-removed-full">—</div><div class="fs-sub">SKU уходят и с ОЗОН, и с ВБ</div></div>
        <div class="fs-cell oz"><div class="fs-label">Убрать с ОЗОН</div><div class="fs-value" id="fc-moved-off-ozon">—</div><div class="fs-sub">остаются только на ВБ</div></div>
        <div class="fs-cell vb"><div class="fs-label">Убрать с ВБ</div><div class="fs-value" id="fc-moved-off-vb">—</div><div class="fs-sub">остаются только на ОЗОН</div></div>
        <div class="fs-cell ok"><div class="fs-label">Останутся как есть</div><div class="fs-value" id="fc-unchanged">—</div><div class="fs-sub">KEEP, KEEP_STAR, REVIEW</div></div>
      </div>
      <div class="forecast-kpi-head">
        <h4>Что уйдёт с каждого МП за выбранный период</h4>
        <p class="muted">Сумма выручки и прибыли, которые будут потеряны, если выполнить весь «План действий». Положительная дельта прибыли («+» зелёным) означает, что вместе с выручкой уйдёт и убыток — чистый эффект в плюс.</p>
      </div>
      <div class="forecast-kpi-grid">
        <div class="fkpi oz">
          <div class="fkpi-head"><span class="fkpi-dot"></span><span class="fkpi-title">Уйдёт с ОЗОН</span><span class="fkpi-help" tabindex="0" aria-label="Как считается" data-tip="Финансовый эффект вывода товаров с OZON за выбранный период.\n\nВыручка — сумма выручки на OZON по всем SKU, которым «План действий» присвоил вывод (рекомендации REMOVE / REMOVE_HERE). Показывается со знаком минус — эта выручка будет потеряна.\n\nПрибыль — сумма фактической прибыли (выручка минус себестоимость и все удержания МП) тех же выводимых товаров на OZON.\n• Красный «−» — вместе с товарами уходит прибыль (теряем).\n• Зелёный «+» — выводимые товары были убыточны, уходит убыток (эффект в плюс).\n\n% под суммой — доля этого эффекта от общей прибыли/выручки OZON за период.">?</span></div>
          <div class="fkpi-pair">
            <div class="fkpi-cell"><div class="fkpi-label">Выручка</div><div class="fkpi-value neg" id="fc-rev-off-oz">—</div><div class="fkpi-sub" id="fc-rev-off-oz-pct">—</div></div>
            <div class="fkpi-cell"><div class="fkpi-label">Прибыль</div><div class="fkpi-value" id="fc-prof-off-oz">—</div><div class="fkpi-sub" id="fc-prof-off-oz-pct">—</div></div>
          </div>
        </div>
        <div class="fkpi vb">
          <div class="fkpi-head"><span class="fkpi-dot"></span><span class="fkpi-title">Уйдёт с ВБ</span><span class="fkpi-help" tabindex="0" aria-label="Как считается" data-tip="Финансовый эффект вывода товаров с Wildberries за выбранный период.\n\nВыручка — сумма выручки на ВБ по всем SKU, которым «План действий» присвоил вывод (рекомендации REMOVE / REMOVE_HERE). Показывается со знаком минус — эта выручка будет потеряна.\n\nПрибыль — сумма фактической прибыли (выручка минус себестоимость и все удержания МП) тех же выводимых товаров на ВБ.\n• Красный «−» — вместе с товарами уходит прибыль (теряем).\n• Зелёный «+» — выводимые товары были убыточны, уходит убыток (эффект в плюс).\n\n% под суммой — доля этого эффекта от общей прибыли/выручки ВБ за период.">?</span></div>
          <div class="fkpi-pair">
            <div class="fkpi-cell"><div class="fkpi-label">Выручка</div><div class="fkpi-value neg" id="fc-rev-off-vb">—</div><div class="fkpi-sub" id="fc-rev-off-vb-pct">—</div></div>
            <div class="fkpi-cell"><div class="fkpi-label">Прибыль</div><div class="fkpi-value" id="fc-prof-off-vb">—</div><div class="fkpi-sub" id="fc-prof-off-vb-pct">—</div></div>
          </div>
        </div>
        <div class="fkpi total">
          <div class="fkpi-head"><span class="fkpi-dot"></span><span class="fkpi-title">Итого</span><span class="fkpi-help" tabindex="0" aria-label="Как считается" data-tip="Суммарный эффект «Плана действий» по обоим маркетплейсам.\n\nВыручка — сумма потерянной выручки: OZON + ВБ (со знаком минус).\n\nПрибыль — сумма эффекта по прибыли OZON и ВБ.\n• Красный «−» — в сумме теряем прибыль.\n• Зелёный «+» — в сумме уходит больше убытка, чем прибыли, — чистый эффект положительный.\n\n% под суммой — доля от общего оборота и общей прибыли за период.">?</span></div>
          <div class="fkpi-pair">
            <div class="fkpi-cell"><div class="fkpi-label">Выручка</div><div class="fkpi-value neg" id="fc-rev-off-all">—</div><div class="fkpi-sub" id="fc-rev-off-all-pct">—</div></div>
            <div class="fkpi-cell"><div class="fkpi-label">Прибыль</div><div class="fkpi-value" id="fc-prof-off-all">—</div><div class="fkpi-sub" id="fc-prof-off-all-pct">—</div></div>
          </div>
        </div>
      </div>
    </div>

    <div class="row-2">
      <div class="card">
        <div class="card-head"><h3>ABC-распределение по выручке</h3><p class="muted">A — 80% выручки, B — следующие 15%, C — последние 5%, Z — нет продаж, D — убыток.</p></div>
        <div class="chart-wrap"><canvas id="chart-abc-rev"></canvas></div>
      </div>
      <div class="card">
        <div class="card-head"><h3>ABC-распределение по прибыли</h3><p class="muted">D-категория означает убыточный товар — обязательный кандидат на вывод.</p></div>
        <div class="chart-wrap"><canvas id="chart-abc-prof"></canvas></div>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><h3>Структура рекомендаций</h3><p class="muted">Каждый SKU получил один из 5 типов рекомендации. Цвета совпадают на всех экранах.</p></div>
      <div class="chart-wrap tall"><canvas id="chart-recs"></canvas></div>
    </div>
  </section>

  <section id="tab-ozon" class="tab-panel">
    <div class="page-intro">
      <h2>ОЗОН — детальный анализ</h2>
      <p class="lede">Все товары на ОЗОН с ABC-классификацией и рекомендациями. Используйте фильтры и поиск.</p>
    </div>
    <div class="filters" data-mp="ozon">
      <input type="search" class="search" placeholder="Поиск по артикулу или названию…" />
      <select class="f-abc-rev"><option value="">Все ABC выручка</option><option>A</option><option>B</option><option>C</option><option>D</option><option>Z</option></select>
      <select class="f-abc-prof"><option value="">Все ABC прибыль</option><option>A</option><option>B</option><option>C</option><option>D</option><option>Z</option></select>
      <select class="f-rec">
        <option value="">Все рекомендации</option>
        <option value="KEEP_STAR">⭐ Звёзды</option>
        <option value="KEEP">Оставить</option>
        <option value="REVIEW">Пересмотреть</option>
        <option value="REMOVE_HERE">Убрать с этого МП</option>
        <option value="CONSIDER_REMOVE_HERE">Рассмотреть перенос</option>
        <option value="REMOVE">Вывести совсем</option>
      </select>
      <div class="result-count"><span class="count">0</span> SKU</div>
    </div>
    <div class="table-wrap"><table class="data-table" id="table-ozon"></table></div>
    <div class="recs-legend-slot"></div>
  </section>

  <section id="tab-vb" class="tab-panel">
    <div class="page-intro">
      <h2>Wildberries — детальный анализ</h2>
      <p class="lede">Матрица ВБ примерно в 2.3× шире ОЗОН — там основной потенциал для сокращения.</p>
    </div>
    <div class="filters" data-mp="vb">
      <input type="search" class="search" placeholder="Поиск по артикулу или названию…" />
      <select class="f-abc-rev"><option value="">Все ABC выручка</option><option>A</option><option>B</option><option>C</option><option>D</option><option>Z</option></select>
      <select class="f-abc-prof"><option value="">Все ABC прибыль</option><option>A</option><option>B</option><option>C</option><option>D</option><option>Z</option></select>
      <select class="f-rec">
        <option value="">Все рекомендации</option>
        <option value="KEEP_STAR">⭐ Звёзды</option>
        <option value="KEEP">Оставить</option>
        <option value="REVIEW">Пересмотреть</option>
        <option value="REMOVE_HERE">Убрать с этого МП</option>
        <option value="CONSIDER_REMOVE_HERE">Рассмотреть перенос</option>
        <option value="REMOVE">Вывести совсем</option>
      </select>
      <div class="result-count"><span class="count">0</span> SKU</div>
    </div>
    <div class="table-wrap"><table class="data-table" id="table-vb"></table></div>
    <div class="recs-legend-slot"></div>
  </section>

  <section id="tab-yandex" class="tab-panel">
    <div class="page-intro">
      <h2>Яндекс — детальный анализ</h2>
      <p class="lede">Все товары на Яндекс Маркете с ABC-классификацией и рекомендациями. Анализ по Яндексу ведётся отдельно и не влияет на логику разделения ассортимента ОЗОН/ВБ.</p>
    </div>
    <div class="filters" data-mp="ya">
      <input type="search" class="search" placeholder="Поиск по артикулу или названию…" />
      <select class="f-abc-rev"><option value="">Все ABC выручка</option><option>A</option><option>B</option><option>C</option><option>D</option><option>Z</option></select>
      <select class="f-abc-prof"><option value="">Все ABC прибыль</option><option>A</option><option>B</option><option>C</option><option>D</option><option>Z</option></select>
      <select class="f-rec">
        <option value="">Все рекомендации</option>
        <option value="KEEP_STAR">⭐ Звёзды</option>
        <option value="KEEP">Оставить</option>
        <option value="REVIEW">Пересмотреть</option>
        <option value="REMOVE">Вывести совсем</option>
      </select>
      <div class="result-count"><span class="count">0</span> SKU</div>
    </div>
    <div class="table-wrap"><table class="data-table" id="table-ya"></table></div>
    <div class="recs-legend-slot"></div>
  </section>

  <section id="tab-cross" class="tab-panel">
    <div class="page-intro">
      <h2>Сравнение матриц по артикулам</h2>
      <p class="lede">SKU сопоставлены по «Артикулу продавца». Видно, где товар работает на обоих МП, где — только на одном, и где имеет смысл сосредоточить продажи на одном маркетплейсе.</p>
      <p class="note-ya">Яндекс в данном анализе не участвует. Данные анализируются по статусам только для ОЗОН и ВБ. Колонки Яндекса можно включить в «Колонках» (по галочке) — они справочные.</p>
    </div>
    <div class="filters" data-mp="cross">
      <input type="search" class="search" placeholder="Поиск по артикулу или названию…" />
      <select class="f-presence">
        <option value="">Все</option>
        <option value="both">На обоих МП</option>
        <option value="only_ozon">Только ОЗОН</option>
        <option value="only_vb">Только ВБ</option>
      </select>
      <select class="f-action">
        <option value="">Все статусы</option>
        <option value="REMOVE_FROM_OZON">Убрать с ОЗОН</option>
        <option value="REMOVE_FROM_VB">Убрать с ВБ</option>
        <option value="REMOVE_BOTH">Убрать с обоих</option>
        <option value="STAR_BOTH">Топ на обоих</option>
      </select>
      <div class="result-count"><span class="count">0</span> SKU</div>
      <div class="cols-toggle">
        <button type="button" class="cols-toggle-btn" id="cross-cols-btn" aria-haspopup="true" aria-expanded="false">Колонки <span class="chev">▾</span></button>
        <div class="cols-panel" id="cross-cols-panel" hidden>
          <div class="cols-panel-head">
            <span>Показывать колонки</span>
            <div class="cols-panel-actions">
              <button type="button" data-cols-preset="all">Все</button>
              <button type="button" data-cols-preset="min">Минимум</button>
              <button type="button" data-cols-preset="oz">Только ОЗОН</button>
              <button type="button" data-cols-preset="vb">Только ВБ</button>
              <button type="button" data-cols-preset="ya">Только Яндекс</button>
            </div>
          </div>
          <div class="cols-panel-body" id="cross-cols-list"></div>
        </div>
      </div>
    </div>
    <div class="table-wrap"><table class="data-table cross" id="table-cross"></table></div>
    <div class="recs-legend-slot"></div>
  </section>

  <section id="tab-actions" class="tab-panel">
    <div class="page-intro">
      <h2>План действий</h2>
      <p class="lede">Готовые списки SKU для команд закупок и менеджеров категорий. Каждый список можно отфильтровать в соседних вкладках и выгрузить в CSV.</p>
    </div>
    <div class="actions-grid">
      <div class="action-card danger">
        <div class="action-head"><h3>1. Удалить с обоих маркетплейсов</h3><span class="action-count" id="ac-remove-both">—</span></div>
        <p class="action-desc">Товары без продаж или с убытком на обоих МП. Отвлекают ресурс менеджеров и не приносят результата. Безопасно выводить из ассортимента.</p>
        <button class="btn-export" data-export="REMOVE_BOTH">Экспорт CSV</button>
        <details><summary>Показать список</summary><div class="sku-list" id="list-remove-both"></div></details>
      </div>
      <div class="action-card warn">
        <div class="action-head"><h3>2. Убрать с ОЗОН (оставить на ВБ)</h3><span class="action-count" id="ac-remove-ozon">—</span></div>
        <p class="action-desc">Хорошо продаются на ВБ, плохо — на ОЗОН. Сокращает административную нагрузку на ОЗОН и устраняет внутреннюю конкуренцию.</p>
        <button class="btn-export" data-export="REMOVE_FROM_OZON">Экспорт CSV</button>
        <details><summary>Показать список</summary><div class="sku-list" id="list-remove-ozon"></div></details>
      </div>
      <div class="action-card warn">
        <div class="action-head"><h3>3. Убрать с ВБ (оставить на ОЗОН)</h3><span class="action-count" id="ac-remove-vb">—</span></div>
        <p class="action-desc">Хорошо продаются на ОЗОН, плохо — на ВБ. Снимут "длинный хвост" с витрины ВБ и сэкономят на хранении/логистике.</p>
        <button class="btn-export" data-export="REMOVE_FROM_VB">Экспорт CSV</button>
        <details><summary>Показать список</summary><div class="sku-list" id="list-remove-vb"></div></details>
      </div>
      <div class="action-card review">
        <div class="action-head"><h3>4. Пересмотреть</h3><span class="action-count" id="ac-review">—</span></div>
        <p class="action-desc">Низкая маржа или CC по обоим показателям. Решить — улучшить экономику (цена, себестоимость, реклама) или вывести.</p>
        <button class="btn-export" data-export="REVIEW">Экспорт CSV</button>
        <details><summary>Показать список</summary><div class="sku-list" id="list-review"></div></details>
      </div>
      <div class="action-card star">
        <div class="action-head"><h3>5. Звёзды — защищать и развивать</h3><span class="action-count" id="ac-star">—</span></div>
        <p class="action-desc">AA по выручке и прибыли. Контролировать остатки, цены, рекламу, негативные отзывы. Это ядро бизнеса.</p>
        <button class="btn-export" data-export="STAR">Экспорт CSV</button>
        <details><summary>Показать список</summary><div class="sku-list" id="list-star"></div></details>
      </div>
      <div class="action-card info">
        <div class="action-head"><h3>Эффект от сокращения матрицы</h3></div>
        <div class="impact-list" id="impact"></div>
      </div>
    </div>
  </section>

  </main>

  <div class="modal-overlay" id="modal" hidden>
    <div class="modal">
      <button class="modal-close" id="modal-close" aria-label="Закрыть">×</button>
      <div id="modal-body"></div>
    </div>
  </div>

  <template id="recs-legend-template">
    <div class="card recs-legend">
      <div class="card-head">
        <div>
          <h3>Как читать рекомендации</h3>
          <p class="muted">Каждому SKU автоматически присваивается одна из 6 рекомендаций. Логика — ниже: что это значит и как рассчитано.</p>
        </div>
      </div>
      <div class="recs-legend-grid">
        <div class="recs-legend-item">
          <span class="pill rec-KEEP_STAR">⭐ Звезда</span>
          <div class="rl-text">
            <p class="rl-meaning">Звёздный товар — высокая выручка <em>и</em> высокая прибыль. Защищать ассортиментом, остатками, рекламой.</p>
            <p class="rl-criteria"><span class="rl-label">Критерий:</span> ABC по выручке = <b>A</b> <span class="rl-and">И</span> ABC по прибыли = <b>A</b> (группа AA)</p>
          </div>
        </div>
        <div class="recs-legend-item">
          <span class="pill rec-KEEP">Оставить</span>
          <div class="rl-text">
            <p class="rl-meaning">Рабочий SKU — приносит выручку или прибыль. Менять что-либо в нём не нужно.</p>
            <p class="rl-criteria"><span class="rl-label">Критерий:</span> ABC по выручке = <b>A</b> (топ по выручке) <span class="rl-or">ИЛИ</span> ABC по прибыли = <b>A</b> (топ по прибыли) <span class="rl-or">ИЛИ</span> стабильные сочетания (AB, BA, BB и т. п.), не подпадающие под другие правила</p>
          </div>
        </div>
        <div class="recs-legend-item">
          <span class="pill rec-REVIEW">Пересмотреть</span>
          <div class="rl-text">
            <p class="rl-meaning">Серая зона — не звезда и не аутсайдер. Стоит вручную проверить: ассортиментная роль, цена, себестоимость, реклама.</p>
            <p class="rl-criteria"><span class="rl-label">Критерий:</span> «Длинный хвост» CC с выручкой ≥ 30 000 ₽ <span class="rl-or">ИЛИ</span> маржа &lt; 5% при прибыли &lt; 30 000 ₽ и ABC по выручке ≠ A</p>
          </div>
        </div>
        <div class="recs-legend-item">
          <span class="pill rec-CONSIDER_REMOVE_HERE">Рассмотреть перенос</span>
          <div class="rl-text">
            <p class="rl-meaning">Здесь товар продаётся слабо, а на другом маркетплейсе ощутимо лучше. Имеет смысл сконцентрировать продажи там, а здесь — постепенно сворачивать.</p>
            <p class="rl-criteria"><span class="rl-label">Критерий:</span> ABC по выручке = <b>C</b> на этом МП, при этом на другом МП — <b>A</b> или <b>B</b> и прибыль другого МП ≥ <b>3×</b> прибыли здесь</p>
          </div>
        </div>
        <div class="recs-legend-item">
          <span class="pill rec-REMOVE_HERE">Убрать с этого МП</span>
          <div class="rl-text">
            <p class="rl-meaning">Здесь товар тянет вниз, а на другом маркетплейсе нормально работает. Снять с этого МП, оставить только на втором.</p>
            <p class="rl-criteria"><span class="rl-label">Критерий:</span> Убыток здесь <span class="rl-and">И</span> прибыль на другом МП с ABC по выручке = A или B <span class="rl-or">ИЛИ</span> ABC = B/C здесь, A на другом МП, прибыль другого ≥ <b>5×</b> прибыли здесь и выручка здесь &lt; 100 000 ₽</p>
          </div>
        </div>
        <div class="recs-legend-item">
          <span class="pill rec-REMOVE">Вывести совсем</span>
          <div class="rl-text">
            <p class="rl-meaning">SKU не приносит ни выручки, ни прибыли — и нет смысла держать его ни на одном маркетплейсе. Вывести из матрицы.</p>
            <p class="rl-criteria"><span class="rl-label">Критерий:</span> ABC по выручке = <b>Z</b> (нет продаж за период) <span class="rl-or">ИЛИ</span> убыток без рабочей альтернативы на другом МП <span class="rl-or">ИЛИ</span> «длинный хвост» CC с выручкой &lt; 30 000 ₽</p>
          </div>
        </div>
      </div>
      <p class="recs-legend-foot muted">ABC-классификация по принципу 80/15/5: <b>A</b> — топ-80% выручки/прибыли, <b>B</b> — следующие 15%, <b>C</b> — оставшиеся 5%, <b>D</b> — единичные продажи, <b>Z</b> — нет продаж. Все рекомендации пересчитываются в браузере при смене периода.</p>
    </div>
  </template>

  <footer class="footer">
    <p>ABC-методология (80/15/5) · данные за <span class="period-label">—</span></p>
  </footer>
</div>`;
  }

  /* ============ Точка входа ============ */
  async function render(root, state) {
    destroyAllCharts();

    // КЭШ: если данные уже загружены в этой сессии страницы — не дёргаем API
    // заново и НЕ сбрасываем период/под-вкладку. Перерисовываем из STATE.
    // (Кэш живёт до перезагрузки страницы; обновление статуса товара
    //  внутри ABC сбрасывает флаг loaded — см. ниже в обработчике статуса.)
    if (STATE.loaded && STATE.raw && STATE.allMonths.length
        && (STATE.raw.ozon.length || STATE.raw.vb.length || (STATE.raw.yandex||[]).length)) {
      root.innerHTML = dashboardHTML();
      STATE.rootEl = root.querySelector('.abc-dash');
      if (window.Chart) {
        Chart.defaults.color = '#96a5af';
        Chart.defaults.borderColor = 'rgba(20,30,46,0.06)';
        Chart.defaults.font.family = '-apple-system, "Segoe UI", system-ui, sans-serif';
        Chart.defaults.font.size = 12;
      }
      bindSubNav();
      bindAbcPeriod();
      bindModal();
      populateRecsLegend();
      refreshAbcPeriodLabel();
      recomputeAndRender();
      return;
    }

    root.innerHTML = '<div class="abc-dash"><div class="loader"><span class="spinner"></span></div></div>';

    let payload;
    try {
      payload = await (window.API || API).abcData();
    } catch (e) {
      root.innerHTML = '<div class="abc-dash"><div class="empty">Не удалось загрузить данные ABC: ' + escapeHtml((e && e.message) || e) + '</div></div>';
      return;
    }

    STATE.raw = transformApi(payload);
    if (!STATE.allMonths.length || (!STATE.raw.ozon.length && !STATE.raw.vb.length && !(STATE.raw.yandex||[]).length)) {
      root.innerHTML = '<div class="abc-dash"><div class="empty">Нет данных за выбранный период.</div></div>';
      return;
    }

    // По умолчанию — последние три месяца из доступных (только при первой загрузке)
    var mm = STATE.monthMeta;
    if (mm.length >= 3) {
      var last3 = mm.slice(-3);
      STATE.period = { from: last3[0].start || '', to: last3[last3.length - 1].end || '' };
    } else {
      STATE.period = { from: '', to: '' };
    }
    STATE.selectedMonths = selectedMonthsFromPeriod();
    STATE.loaded = true;   // данные получены — следующие заходы пойдут из кэша

    root.innerHTML = dashboardHTML();
    STATE.rootEl = root.querySelector('.abc-dash');

    if (window.Chart) {
      Chart.defaults.color = '#96a5af';
      Chart.defaults.borderColor = 'rgba(20,30,46,0.06)';
      Chart.defaults.font.family = '-apple-system, "Segoe UI", system-ui, sans-serif';
      Chart.defaults.font.size = 12;
    }

    bindSubNav();
    bindAbcPeriod();
    bindModal();
    populateRecsLegend();
    refreshAbcPeriodLabel();

    recomputeAndRender();
  }

  function destroy() {
    destroyAllCharts();
    STATE.rootEl = null;
  }

  // Сброс кэша данных ABC: следующий render() снова запросит API.
  // Вызывать после загрузки новых данных/себестоимости в других разделах.
  function invalidate() {
    STATE.loaded = false;
    const api = window.API || API;
    if (api && api.cacheClear) api.cacheClear('/api/abc');
  }

  return { render, destroy, invalidate };
})();
