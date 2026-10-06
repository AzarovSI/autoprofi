// API-слой: хранение токена и обёртка над fetch.
const API = (() => {
  const TOKEN_KEY = 'ap_token';
  const USER_KEY = 'ap_user';

  // Базовый префикс API.
  // Локально фронтенд и бэкенд на одном порту -> префикс пустой.
  // На опубликованном *.pplx.app статика берётся из S3, а бэкенд
  // доступен через прокси по пути /port/8000 относительно текущего пути.
  const API_BASE = (() => {
    if (location.hostname.endsWith('.pplx.app')) {
      const base = location.pathname.replace(/\/[^/]*$/, '');
      return base + '/port/8000';
    }
    return '';
  })();

  function getToken() { return window.APStore.get(TOKEN_KEY); }
  function setToken(t) { window.APStore.set(TOKEN_KEY, t); }
  function clearToken() { window.APStore.del(TOKEN_KEY); window.APStore.del(USER_KEY); }
  function getUser() { try { return JSON.parse(window.APStore.get(USER_KEY)); } catch (e) { return null; } }
  function setUser(u) { window.APStore.set(USER_KEY, JSON.stringify(u)); }

  async function request(path, opts = {}) {
    const headers = opts.headers || {};
    const t = getToken();
    if (t) headers['Authorization'] = 'Bearer ' + t;
    const resp = await fetch(API_BASE + path, { ...opts, headers });
    if (resp.status === 401) {
      clearToken();
      window.dispatchEvent(new CustomEvent('ap:unauthorized'));
      throw new Error('Не авторизовано');
    }
    if (!resp.ok) {
      let msg = 'Ошибка ' + resp.status;
      try { const j = await resp.json(); if (j.detail) msg = j.detail; } catch (e) {}
      throw new Error(msg);
    }
    const ct = resp.headers.get('content-type') || '';
    return ct.includes('application/json') ? resp.json() : resp.text();
  }

  // Кэш GET-запросов в памяти. Ключ — полный path (включая query).
  // Храним Promise, чтобы одновременные вызовы шли одним запросом (dedupe).
  const _cache = new Map();
  function cget(path) {
    if (_cache.has(path)) return _cache.get(path);
    const pr = request(path).catch(err => { _cache.delete(path); throw err; });
    _cache.set(path, pr);
    return pr;
  }
  // Сброс кэша. Без аргумента — весь; с подстрокой — только подходящие ключи.
  function cacheClear(substr) {
    if (!substr) { _cache.clear(); return; }
    for (const k of Array.from(_cache.keys())) {
      if (k.indexOf(substr) !== -1) _cache.delete(k);
    }
  }

  function qs(params) {
    const p = new URLSearchParams();
    Object.entries(params || {}).forEach(([k, v]) => {
      if (v !== null && v !== undefined && v !== '') p.append(k, v);
    });
    const s = p.toString();
    return s ? '?' + s : '';
  }

  // Пробуждаем бэкенд (прод засыпает при простое). Возвращает true, когда сервер готов.
  async function wakeBackend(maxTries) {
    maxTries = maxTries || 6;
    for (let i = 0; i < maxTries; i++) {
      try {
        const r = await fetch(API_BASE + '/health', { method: 'GET' });
        if (r.ok) return true;
      } catch (e) { /* сеть ещё недоступна */ }
      await new Promise(res => setTimeout(res, 2500));
    }
    return false;
  }

  async function login(email, password) {
    const body = new URLSearchParams();
    body.append('username', email);
    body.append('password', password);
    let resp = null;
    // До пяти попыток: если бэкенд просыпается (502/503/504/сеть) — ждём и повторяем.
    for (let i = 0; i < 5; i++) {
      try {
        resp = await fetch(API_BASE + '/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body,
        });
      } catch (e) {
        if (i < 4) { await new Promise(res => setTimeout(res, 2500)); continue; }
        throw new Error('Сервер недоступен. Повторите через несколько секунд.');
      }
      if ([502, 503, 504].includes(resp.status) && i < 4) {
        await new Promise(res => setTimeout(res, 2500));
        continue;
      }
      break;
    }
    if (!resp.ok) {
      let msg;
      if ([502, 503, 504].includes(resp.status)) {
        msg = 'Сервер просыпается, повторите через несколько секунд.';
      } else {
        msg = 'Неверный email или пароль';
        try { const j = await resp.json(); if (j.detail) msg = j.detail; } catch (e) {}
      }
      throw new Error(msg);
    }
    const data = await resp.json();
    setToken(data.access_token);
    setUser(data.user);
    return data;
  }

  // Сырой вызов любого endpoint (мимо cget-кэша). Для multipart-загрузок
  // передаём FormData как body без Content-Type — браузер сам поставит boundary.
  async function raw(method, path, body) {
    const opts = { method };
    if (body instanceof FormData) {
      opts.body = body;
    } else if (body != null) {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body);
    }
    return request(path, opts);
  }

  return {
    getToken, setToken, clearToken, getUser, setUser, login, wakeBackend,
    cacheClear, API_BASE, raw,
    // metrics (кэшируемые GET)
    weeks: () => cget('/api/metrics/weeks'),
    summary: (p) => cget('/api/metrics/summary' + qs(p)),
    byCategory: (p) => cget('/api/metrics/by_category' + qs(p)),
    bySku: (p) => cget('/api/metrics/by_sku' + qs(p)),
    mpCompare: (p) => cget('/api/metrics/mp_compare' + qs(p)),
    // trend (кэшируемые GET)
    summarySeries: (p) => cget('/api/trend/summary_series' + qs(p)),
    categoryMatrix: (p) => cget('/api/trend/category_matrix' + qs(p)),
    skuMatrix: (p) => cget('/api/trend/sku_matrix' + qs(p)),
    skuDrivers: (p) => cget('/api/trend/sku_drivers' + qs(p)),
    // РНП UNIT (юнит-экономика по неделям)
    rnpWeeks: (p) => cget('/api/rnp/weeks' + qs(p)),
    rnpSummary: (p) => cget('/api/rnp/summary' + qs(p)),
    rnpTree: (p) => cget('/api/rnp/tree' + qs(p)),
    rnpTreeCross: (p) => cget('/api/rnp/tree_cross' + qs(p)),
    rnpTrend: (p) => cget('/api/rnp/trend' + qs(p)),
    // ABC (анализ товарной матрицы)
    abcData: () => cget('/api/abc/data'),
    // catalog
    catalogItems: (p) => cget('/api/catalog/items' + qs(p)),
    // дерево/справочники фильтров: статусы/менеджеры зависят от marketplace
    catalogTree: (p) => cget('/api/catalog/tree' + qs(p)),
    catalogUpdate: (sa, body) => request('/api/catalog/items', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ seller_article: sa }, body)),
    }).then(res => {
      // Справочник изменён (в т.ч. ссылка на товар) — сбрасываем кэш дерева РНП
      // и самого справочника, чтобы при следующем открытии подтянулись свежие данные.
      cacheClear('/api/rnp');
      cacheClear('/api/catalog');
      return res;
    }),
    // экспорт справочника в Excel (скачивание файла)
    catalogExportUrl: (p) => API_BASE + '/api/catalog/export' + qs(p),
    // импорт справочника из Excel (с указанием маркетплейса)
    catalogImport: (file, p) => {
      const fd = new FormData();
      fd.append('file', file);
      return request('/api/catalog/import' + qs(p), { method: 'POST', body: fd });
    },
    // users (управление пользователями — только админ)
    usersList: () => request('/api/users'),
    userCreate: (body) => request('/api/users', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
    userUpdate: (id, body) => request('/api/users/' + id, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
    userDelete: (id) => request('/api/users/' + id, { method: 'DELETE' }),
    // upload
    // kind: 'all' | 'weekly' | 'monthly' — фильтр журнала по типу отчёта
    uploadHistory: (kind) => request('/api/upload/history' + qs(kind && kind !== 'all' ? { kind } : null)),
    uploadReport: (mp, file, factThroughDate) => {
      const fd = new FormData();
      fd.append('marketplace', mp);
      fd.append('file', file);
      // «Факт по дату» (YYYY-MM-DD) — только для месячного отчёта; бэкенд сам решит.
      if (factThroughDate) fd.append('fact_through_date', factThroughDate);
      return request('/api/upload/report', { method: 'POST', body: fd });
    },
    // РНП продажи → Ozon (дерево дневных продаж по месяцам)
    rnpSalesTree: (p) => cget('/api/rnp_sales/tree' + qs(p)),
    // Лёгкая юнит-экономика ОДНОГО артикула за N последних недель (popup в РНП заказы).
    rnpProductUe: (p) => cget('/api/rnp/product_ue' + qs(p)),
    // Аномалии РНП заказы: список правил (пороги) и расчёт сработавших по SKU.
    anomalyRules: () => cget('/api/rnp_sales/anomaly_rules'),
    anomalyRulesUpdate: (rules) => request('/api/rnp_sales/anomaly_rules', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rules: rules }),
    }),
    anomalies: (p) => cget('/api/rnp_sales/anomalies' + qs(p)),
    // Загрузка дневного API-файла Ozon (лист «Данные»)
    uploadOzonDaily: (file) => {
      const fd = new FormData();
      fd.append('file', file);
      return request('/api/upload/ozon_daily', { method: 'POST', body: fd });
    },
    // Загрузка отчёта рекламы Ozon («Аналитика продвижения», лист Statistics)
    uploadOzonAds: (file) => {
      const fd = new FormData();
      fd.append('file', file);
      return request('/api/upload/ozon_ads', { method: 'POST', body: fd });
    },
    // Загрузка общего отчёта остатков склада АВТОПРОФИ (МСК),
    // выгрузка 1С (лист TDSheet). Даты в файле нет — передаём отдельно.
    uploadOzonStock: (file, stockDate) => {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('stock_date', stockDate);
      return request('/api/upload/ozon_stock', { method: 'POST', body: fd });
    },
    // Загрузка отчёта Ozon «Список товаров» (CSV): рейтинг + отзывы.
    // Даты в файле нет — передаём отдельно (дата актуальности отчёта).
    uploadOzonReviews: (file, reportDate) => {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('report_date', reportDate);
      return request('/api/upload/ozon_reviews', { method: 'POST', body: fd });
    },
    // ── Wildberries: загрузка отчётов для раздела «РНП заказы» ──
    // «Воронка продаж» WB (лист «TDSheet»): дневные заказы/отмены/выкуп/
    // переходы/корзина/конверсии/рейтинг/остаток. Даты берутся из файла.
    uploadWbDaily: (file) => {
      const fd = new FormData();
      fd.append('file', file);
      return request('/api/upload/wb_daily', { method: 'POST', body: fd });
    },
    // «Аналитика продаж» Яндекс.Маркет (лист «Аналитика продаж»):
    // показы/клики/корзина/заказы/отмены/конверсии. Дата есть в файле
    // (колонка «День», формат ДД-ММ-ГГГГ) — отдельно не передаём.
    uploadYaDaily: (file) => {
      const fd = new FormData();
      fd.append('file', file);
      return request('/api/upload/ya_daily', { method: 'POST', body: fd });
    },
    // Ежедневные остатки склада Яндекс (FBY, лист «Остатки на складе»):
    // колонка «Доступно для заказа» (сумма по SKU) → stock_ya_qty.
    // Даты в файле нет — передаём отдельно.
    uploadYaStock: (file, stockDate) => {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('stock_date', stockDate);
      return request('/api/upload/ya_stock', { method: 'POST', body: fd });
    },
    // «Рейтинг» WB (лист «TDSheet»): Количество отзывов.
    // Дата есть в самом файле (колонка «Дата») — отдельно не передаём.
    uploadWbReviews: (file) => {
      // Дата берётся из колонки «Дата» самого отчёта — report_date не шлём.
      const fd = new FormData();
      fd.append('file', file);
      return request('/api/upload/wb_reviews', { method: 'POST', body: fd });
    },
    // «Статистика рекламных кампаний» WB: Расход и CTR. Дата берётся из файла.
    uploadWbAds: (file) => {
      const fd = new FormData();
      fd.append('file', file);
      return request('/api/upload/wb_ads', { method: 'POST', body: fd });
    },
    // Загрузка отчёта мониторинга цен конкурентов Ozon (PriceVA, .xlsx).
    // Даты в файле нет — передаём отдельно (дата мониторинга).
    uploadOzonPriceva: (file, reportDate) => {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('report_date', reportDate);
      return request('/api/upload/ozon_priceva', { method: 'POST', body: fd });
    },
    // Загрузка отчёта мониторинга цен конкурентов Wildberries (PriceVA, .xlsx).
    // Формат файла тот же, что у Ozon; данные — для WB. Даты в файле нет —
    // передаём отдельно (дата мониторинга).
    uploadWbPriceva: (file, reportDate) => {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('report_date', reportDate);
      return request('/api/upload/wb_priceva', { method: 'POST', body: fd });
    },
    // Отчёт «Индекс цен» ЛК WB. Даты в файле нет — передаём отдельно.
    // Pi = Цена товара на WB / Цена идентичного товара.
    uploadWbPi: (file, reportDate) => {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('report_date', reportDate);
      return request('/api/upload/wb_pi', { method: 'POST', body: fd });
    },
    // Остатки WB по складам (.xlsx). Даты в файле нет — передаём
    // отдельно (дата остатков). Раздел «Склады».
    uploadWbStock: (file, stockDate) => {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('stock_date', stockDate);
      return request('/api/upload/wb_stock', { method: 'POST', body: fd });
    },
    // Себестоимость товаров (.xlsx). start_date — дата, С КОТОРОЙ
    // действует себестоимость (до следующей загрузки); исторично.
    uploadCost: (file, startDate) => {
      const fd = new FormData();
      fd.append('file', file);
      if (startDate) fd.append('start_date', startDate);
      return request('/api/upload/cost', { method: 'POST', body: fd });
    },
    // Достоверность стоимостной оценки остатков WB на дату
    // (артикулы без с/с или без цены). пусто = последняя дата.
    whDataQuality: (date) => cget('/api/warehouses/data_quality' + qs(date ? { date } : {})),
    // Продажи, шт. (план/факт)
    salesPlanFact: (p) => cget('/api/sales/plan_fact' + qs(p)),
    // Инлайн-правка одной ячейки плана (ozon/wb). Тело — JSON.
    salesPlanCell: (body) => request('/api/sales/plan_cell', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
    salesPlanExportUrl: (p) => API_BASE + '/api/sales/plan_export' + qs(p),
    // Прайс-лист: список товаров с базовыми ценами по Ozon/WB.
    // p: { date_from, date_to } (ISO). Универсум — активные в периоде SKU
    // (аналог правила РНП заказы), цены — актуальные на конец периода.
    pricelist: (p) => cget('/api/prices/pricelist' + qs(p || {})),
    // Инлайн-правка одной ячейки прайс-листа. base_price=null сбрасывает цену.
    priceCell: (body) => request('/api/prices/cell', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
    // Массовая работа с базовыми ценами: шаблон Excel за период и загрузка
    // заполненного файла (только администратор).
    pricesExportUrl: (p) => API_BASE + '/api/prices/export' + qs(p || {}),
    pricesIndexExportUrl: (p) => API_BASE + '/api/prices/index_export' + qs(p || {}),
    pricesImport: (file) => {
      const fd = new FormData();
      fd.append('file', file);
      return request('/api/prices/import', { method: 'POST', body: fd });
    },
    // Красивый сводный отчёт (иерархия+outline, план/факт помесячно, прогноз тек. месяца).
    salesReportExportUrl: (p) => API_BASE + '/api/sales/report_export' + qs(p),
    salesPlanImport: (file, p) => {
      const fd = new FormData();
      fd.append('file', file);
      if (p && p.marketplace) fd.append('marketplace', p.marketplace);
      if (p && p.year) fd.append('year', p.year);
      return request('/api/sales/plan_import', { method: 'POST', body: fd });
    },
    // Комментарии к дневным ячейкам товара в матрице «РНП заказы Озон».
    // Список — за видимый диапазон дат (date_from..date_to, YYYY-MM-DD).
    rnpCommentsList: (dateFrom, dateTo) =>
      request('/api/rnp-comments' + qs({ date_from: dateFrom, date_to: dateTo })),
    rnpCommentCreate: (sellerArticle, date, body) => request('/api/rnp-comments', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seller_article: sellerArticle, date: date, body: body }),
    }),
    rnpCommentUpdate: (id, body) => request('/api/rnp-comments/' + id, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body: body }),
    }),
    rnpCommentDelete: (id) => request('/api/rnp-comments/' + id, { method: 'DELETE' }),
    // Склады — стоимостная оценка по складам/дням (тепловая карта + KPI).
    whByWh: (p) => cget('/api/warehouses/by_wh' + qs(p)),
    // Справочник складов (owner-only).
    whCatalog: () => request('/api/warehouses/catalog'),
    whRename: (id, name) => request('/api/warehouses/rename', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id, name: name }),
    }),
    whMerge: (id, canonicalId) => request('/api/warehouses/merge', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id, canonical_id: canonicalId }),
    }),
    whUnmerge: (id) => request('/api/warehouses/unmerge', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id }),
    }),
    whSetActive: (id, isActive) => request('/api/warehouses/active', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id, is_active: isActive }),
    }),
    // Инциденты складов (историчность): открыть/закрыть/удалить интервал.
    whIncidentOpen: (id, date) => request('/api/warehouses/incident/open', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id, date: date }),
    }),
    whIncidentClose: (id, date) => request('/api/warehouses/incident/close', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id, date: date }),
    }),
    whIncidentDelete: (incidentId) => request('/api/warehouses/incident/delete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ incident_id: incidentId }),
    }),
    // Установить/снять федеральный округ склада.
    whSetDistrict: (id, fd) => request('/api/warehouses/district', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id, federal_district: fd || null }),
    }),

    // ── OZON: Склады — стоимостная оценка (зеркало WB-обёрток выше, префикс oz) ──
    // Раздел открыт всем авторизованным (get_current_user) — и просмотр, и правки.
    ozByWh: (p) => cget('/api/ozon_warehouses/by_wh' + qs(p)),
    ozCatalog: () => request('/api/ozon_warehouses/catalog'),
    ozRename: (id, name) => request('/api/ozon_warehouses/rename', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id, name: name }),
    }),
    ozMerge: (id, canonicalId) => request('/api/ozon_warehouses/merge', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id, canonical_id: canonicalId }),
    }),
    ozUnmerge: (id) => request('/api/ozon_warehouses/unmerge', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id }),
    }),
    ozSetActive: (id, isActive) => request('/api/ozon_warehouses/active', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id, is_active: isActive }),
    }),
    // Инциденты складов Ozon (историчность): открыть/закрыть/удалить интервал.
    ozIncidentOpen: (id, date) => request('/api/ozon_warehouses/incident/open', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id, date: date }),
    }),
    ozIncidentClose: (id, date) => request('/api/ozon_warehouses/incident/close', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id, date: date }),
    }),
    ozIncidentDelete: (incidentId) => request('/api/ozon_warehouses/incident/delete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ incident_id: incidentId }),
    }),
    // Установить/снять федеральный округ склада Ozon.
    ozSetDistrict: (id, fd) => request('/api/ozon_warehouses/district', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id, federal_district: fd || null }),
    }),
    // Переопределить кластер склада Ozon вручную (доп. уровень группировки, отличие от WB).
    ozSetCluster: (id, cluster) => request('/api/ozon_warehouses/cluster', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id, cluster: cluster || null }),
    }),
    // Достоверность стоимостной оценки остатков Ozon на дату (пусто = последняя дата).
    ozDataQuality: (date) => cget('/api/ozon_warehouses/data_quality' + qs(date ? { date } : {})),
    // Остатки Ozon по складам (.xlsx, лист «Товар-склад»). Даты в файле нет —
    // передаём отдельно (дата остатков). Раздел «Склады → OZON».
    uploadOzonWhStock: (file, stockDate) => {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('stock_date', stockDate);
      return request('/api/upload/ozon_wh_stock', { method: 'POST', body: fd });
    },
  };
})();
