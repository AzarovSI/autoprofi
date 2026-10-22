/*************************************************************************************************
 * OZON → GOOGLE ТАБЛИЦА (Apps Script) — v6, оптимизировано под лимит 6 минут
 * -----------------------------------------------------------------------------------------------
 * Ежедневная выгрузка метрик по каждому товару из Ozon Seller API. Подписка: Premium Plus.
 *
 * ЧТО ИЗМЕНИЛОСЬ В v6:
 *   1. СВЕЖИЕ ДАННЫЕ СВЕРХУ: строки нового дня вставляются сразу под шапкой, а не в конец
 *      листа — при открытии отчёта видно, обновился ли он.
 *   2. ХРАНЕНИЕ ТОЛЬКО ПОСЛЕДНИХ 5 ДНЕЙ (CONFIG.KEEP_DAYS): в начале утреннего запуска
 *      (runBase) удаляются строки старше 5 календарных дней. При данных за 21.10
 *      остаются 17–21.10. Если порядок «свежие сверху» нарушен (лист из v5, где дни шли
 *      сверху вниз по возрастанию, или ручная сортировка) — лист один раз сортируется по дате.
 *
 * ЧТО БЫЛО В v5:
 *   1. ПЕРЕХОДЫ В КАРТОЧКУ: раньше бралась метрика hits_view = «Показы, всего»
 *      (сколько раз товар показали) — это сильно завышало число. Теперь берём session_view_pdp
 *      = «Посетители с просмотром карточки товара» (уникальные сессии = реальные переходы).
 *   2. CR в корзину, CR в заказ и СПП/соинвест, % теперь хранятся как доля (0.011)
 *      и показываются в настоящем процентном формате ячейки (0.0%).
 *      CR в корзину берётся из conv_tocart_pdp = «Конверсия в корзину из карточки товара»
 *      (отчёт «Продажи моих товаров»), а не из общей conv_tocart.
 *   3. ИНДЕКС ЦЕНЫ (Pi): теперь берём МАКСИМАЛЬНЫЙ из трёх сравнений
 *      (ozon / external / self) — это соответствует итоговому показателю отчёта
 *      «Ценовой индекс товара на рынке» в кабинете.
 *   4. Добавлена колонка «Рейтинг (отзывы)» — заполняется ВРУЧНУЮ из XLSX-отчёта
 *      кабинета (Аналитика → Рейтинги → Товары и цены). Скрипт эту колонку НЕ трогает
 *      (/v1/review/list для аккаунта закрыт, HTTP 403).
 *   5. НАСТРАИВАЕМЫЙ ЧАС АВТОЗАПУСКА: час ежедневного триггера теперь задаётся через меню
 *      «🕐 Задать час автозапуска» (0–23) и хранится в Script Properties. Раньше был жёстко 09:00.
 *
 * ЧТО БЫЛО В v4:
 *   • Убран блок оценки товара и догоняющий триггер autoContinue (метод /v1/review/list → 403).
 *
 * ЧТО БЫЛО В v3:
 *   • Добавлена СКИДКА ЗА СЧЁТ OZON (СПП / соинвест) — в рублях и процентах.
 *     Конечная цена покупателя восстанавливается из price_indexes (external→ozon→self)
 *     по price_index_value и min_price — без marketing_price (Ozon убрал его в 11.2025).
 *   • Убран рекламный блок (Performance API): расход и CTR больше не собираются.
 *   • Каждый блок данных пишется в лист СРАЗУ и НЕЗАВИСИМО.
 *   • Настоящие частичные кнопки: каждая делает ТОЛЬКО свой блок. Запись по SKU.
 *
 * ЛОГИКА ЛИСТА: один лист «Данные» с колонкой Дата. Новые строки вставляются под шапку
 * (свежие сверху), старые не перезатираются. Повтор за ту же дату не плодит дубли.
 * Хранятся только последние CONFIG.KEEP_DAYS дней — более старые удаляет runBase.
 *
 * КЛЮЧИ: хранятся в Script Properties (Настройки проекта → Свойства скрипта):
 *   OZON_CLIENT_ID, OZON_API_KEY
 *************************************************************************************************/

// ============================ НАСТРОЙКИ ============================
const CONFIG = {
  TIMEZONE: 'Europe/Moscow',
  SHEET_DATA: 'Данные',

  SELLER_HOST: 'https://api-seller.ozon.ru',

  TRIGGER_HOUR: 9,          // час запуска по умолчанию (если пользователь ещё не задал свой)

  KEEP_DAYS: 5              // сколько последних календарных дней хранить в листе (считая «вчера»)
};

const PROP = {
  CLIENT_ID: 'OZON_CLIENT_ID',
  API_KEY: 'OZON_API_KEY',
  TRIGGER_HOUR: 'OZON_TRIGGER_HOUR'   // час ежедневного триггера (0–23), задаётся через меню
};

// Колонки листа (порядок = порядок в таблице). key — внутреннее имя, title — заголовок.
const COLUMNS = [
  { key: 'date',          title: 'Дата' },
  { key: 'offer_id',      title: 'Артикул' },
  { key: 'name',          title: 'Наименование' },
  { key: 'sku',           title: 'SKU' },
  { key: 'ordered_units', title: 'Заказы, шт' },
  { key: 'revenue',       title: 'Сумма заказов, ₽' },
  { key: 'cancellations', title: 'Отменено, шт' },
  { key: 'price_index',   title: 'Индекс цены (Pi)' },
  { key: 'spp_rub',       title: 'Скидка Ozon (СПП/соинвест), ₽' },
  { key: 'spp_pct',       title: 'СПП/соинвест, %' },
  { key: 'hits_view',     title: 'Переходы в карточку' },
  { key: 'conv_tocart',   title: 'CR в корзину, %' },
  { key: 'cr_to_order',   title: 'CR в заказ, %' },
  { key: 'position',      title: 'Ср. позиция' },
  { key: 'rating',        title: 'Рейтинг (отзывы)' },
  { key: 'stock',         title: 'Остаток, шт' }
];

// ============================ КЛЮЧИ ============================
function getProp_(key) {
  const v = PropertiesService.getScriptProperties().getProperty(key);
  if (!v) throw new Error('Не задан ключ: ' + key + '. Внесите его в Настройки проекта → Свойства скрипта.');
  return v;
}

/**
 * Час ежедневного автозапуска (0–23). Берётся из Script Properties (задаётся через меню),
 * при отсутствии/некорректном значении используется CONFIG.TRIGGER_HOUR по умолчанию.
 */
function getTriggerHour_() {
  const raw = PropertiesService.getScriptProperties().getProperty(PROP.TRIGGER_HOUR);
  const h = parseInt(raw, 10);
  if (!isNaN(h) && h >= 0 && h <= 23) return h;
  return CONFIG.TRIGGER_HOUR;
}

// ============================ МЕНЮ ============================
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Ozon')
    .addItem('▶ Обновить всё сейчас', 'runAll')
    .addSeparator()
    .addItem('1. Заказы / выручка / воронка', 'updateAnalytics')
    .addItem('2. Остатки', 'updateStocks')
    .addItem('3. Цены / индекс Pi / СПП', 'updatePrices')
    .addSeparator()
    .addItem('🔑 Ввести API-ключи Ozon', 'setupKeys')
    .addSeparator()
    .addItem('🕐 Задать час автозапуска', 'setupTriggerHour')
    .addItem('⏰ Установить ежедневные триггеры', 'installDailyTriggers')
    .addItem('✖ Удалить триггеры', 'removeTriggers')
    .addToUi();
}

// ============================ ВВОД КЛЮЧЕЙ ЧЕРЕЗ МЕНЮ ============================
/**
 * Диалоговый ввод ключей Ozon Seller API прямо из таблицы (без захода в Настройки проекта).
 * Ключи кладутся в Script Properties — в ячейках и в коде они НЕ хранятся.
 * Пустой ввод оставляет текущее значение без изменений.
 */
function setupKeys() {
  const ui = SpreadsheetApp.getUi();
  const props = PropertiesService.getScriptProperties();

  const curId = props.getProperty(PROP.CLIENT_ID) || '';
  const idResp = ui.prompt(
    'Ключи Ozon (1 из 2)',
    'Client-Id (число).' + (curId ? ' Сейчас: ' + curId + '. Пусто = оставить.' : ''),
    ui.ButtonSet.OK_CANCEL
  );
  if (idResp.getSelectedButton() !== ui.Button.OK) { ui.alert('Отменено.'); return; }
  const clientId = idResp.getResponseText().trim();

  const curKey = props.getProperty(PROP.API_KEY) || '';
  const keyResp = ui.prompt(
    'Ключи Ozon (2 из 2)',
    'Api-Key (длинная строка вида 0a1b2c3d-…).' + (curKey ? ' Ключ уже задан. Пусто = оставить.' : ''),
    ui.ButtonSet.OK_CANCEL
  );
  if (keyResp.getSelectedButton() !== ui.Button.OK) { ui.alert('Отменено.'); return; }
  const apiKey = keyResp.getResponseText().trim();

  if (clientId) props.setProperty(PROP.CLIENT_ID, clientId);
  if (apiKey) props.setProperty(PROP.API_KEY, apiKey);

  const haveId = !!props.getProperty(PROP.CLIENT_ID);
  const haveKey = !!props.getProperty(PROP.API_KEY);
  ui.alert(
    'Ключи сохранены',
    'Client-Id: ' + (haveId ? '✓ задан' : '✗ не задан') + '\n' +
    'Api-Key: ' + (haveKey ? '✓ задан' : '✗ не задан') + '\n\n' +
    'Ключи хранятся в защищённом хранилище проекта, не в ячейках.',
    ui.ButtonSet.OK
  );
}

// ============================ ГЛАВНАЯ (по кнопке) ============================
/**
 * ВНИМАНИЕ: при большом каталоге (у вас ~180 товаров) прогнать ВСЕ блоки за один
 * запуск невозможно — Apps Script жёстко обрывает выполнение на 6-й минуте, а один
 * только блок аналитики /v1/analytics/data ограничен ~1 запросом в минуту и с
 * ретраями сам по себе может занять почти всё окно. Поэтому «Обновить всё» больше
 * НЕ пытается впихнуть всё подряд (это и вызывало «Превышено максимальное время»).
 *
 * Правильный способ для большого каталога:
 *   • Нажать один раз «⏰ Установить ежедневный триггер» — дальше всё собирается
 *     само каждое утро в 09:00.
 *   • Либо запускать блоки по отдельности кнопками 1–3, с паузой между ними.
 *
 * Эта функция запускает только быстрые и безопасные блоки (строки за дату, остатки,
 * цены/СПП). Самый медленный блок — аналитику — оставляем на отдельную
 * кнопку «1», чтобы гарантированно не упереться в лимит.
 */
function runAll() {
  const ss = SpreadsheetApp.getActive();
  ss.toast('Старт: строки, остатки, цены/СПП…', 'Ozon', 5);

  ensureRowsForToday_();     // гарантируем строки за дату (по списку товаров)
  updateStocks();            // остатки (быстро)
  updatePrices();            // цены + индекс + СПП (быстро)

  ss.toast('Готово. Теперь нажмите «1. Заказы/воронка» отдельно.', 'Ozon', 10);
  SpreadsheetApp.getUi().alert(
    'Базовое собрано',
    'Собраны: остатки, индекс цены, СПП/соинвест.\n\n' +
    'Остался один шаг (его нельзя было делать в этом же запуске из-за лимита времени):\n' +
    '  • «1. Заказы / выручка / воронка» — заказы, выручка, отмены, переходы, CR, позиция.\n\n' +
    'Совет: нажмите «⏰ Установить ежедневный триггер» — и всё будет собираться само каждое утро.',
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}

// ============================ SELLER API: HTTP ============================
/**
 * Запрос к Seller API с автоматическим повтором при 429 (rate limit) и 5xx.
 * Ozon ограничивает частоту (особенно /v1/analytics/data — примерно 1 запрос/мин).
 * При 429 ждём с нарастающей паузой и повторяем, а не падаем.
 */
function sellerFetch_(path, payload) {
  const maxAttempts = 6;
  let wait = 8000; // стартовая пауза 8 сек (analytics лимит ~1/мин)
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const resp = UrlFetchApp.fetch(CONFIG.SELLER_HOST + path, {
      method: 'post',
      contentType: 'application/json',
      headers: { 'Client-Id': getProp_(PROP.CLIENT_ID), 'Api-Key': getProp_(PROP.API_KEY) },
      payload: JSON.stringify(payload || {}),
      muteHttpExceptions: true
    });
    const code = resp.getResponseCode();
    if (code >= 200 && code < 300) return JSON.parse(resp.getContentText());

    // 429 (rate limit) или временные 5xx — ждём и повторяем
    if ((code === 429 || code >= 500) && attempt < maxAttempts) {
      Utilities.sleep(wait);
      wait = Math.min(wait * 1.6, 60000); // увеличиваем, но не больше 60 сек
      continue;
    }
    throw new Error('Seller API ' + path + ' → HTTP ' + code + ': ' + resp.getContentText());
  }
}

// ============================ РАБОТА С ЛИСТОМ ============================
function getSheet_() {
  const ss = SpreadsheetApp.getActive();
  let sheet = ss.getSheetByName(CONFIG.SHEET_DATA);
  if (!sheet) {
    sheet = ss.insertSheet(CONFIG.SHEET_DATA);
    sheet.setFrozenRows(1);
  }
  syncHeader_(sheet);
  return sheet;
}

/**
 * Приводит шапку листа (строка 1) к текущему COLUMNS. Нужно, когда лист был
 * создан со старым набором колонок (напр. без «Рейтинг (отзывы)») — без этого
 * заголовки и данные расходятся («Остаток» съезжает). Данные НЕ трогаем — только строку 1.
 */
function syncHeader_(sheet) {
  const header = COLUMNS.map(function (c) { return c.title; });
  const cur = sheet.getRange(1, 1, 1, COLUMNS.length).getValues()[0];
  let same = true;
  for (let i = 0; i < header.length; i++) {
    if (String(cur[i]).trim() !== header[i]) { same = false; break; }
  }
  if (same) return;
  sheet.getRange(1, 1, 1, header.length).setValues([header])
    .setFontWeight('bold').setBackground('#005bff').setFontColor('#ffffff');
  sheet.setFrozenRows(1);
}

function colIndex_(key) {
  for (let i = 0; i < COLUMNS.length; i++) if (COLUMNS[i].key === key) return i + 1; // 1-based
  return -1;
}

function dataDate_() { return dateStr_(daysAgo_(1)); } // данные «за вчера»

/**
 * Гарантирует, что за текущую дату в листе есть строка на каждый товар.
 * Если строки за эту дату уже есть — ничего не делает (не дублирует).
 * Новые строки вставляются сразу под шапкой (свежие сверху).
 * Возвращает карту sku → номер строки.
 */
function ensureRowsForToday_() {
  const sheet = getSheet_();
  const date = dataDate_();
  const map = rowMapForDate_(sheet, date);
  if (Object.keys(map).length > 0) return map; // строки уже созданы

  // создаём строки по списку товаров
  const products = fetchProductList_();
  const info = fetchProductInfo_(products);
  const skus = Object.keys(info);
  const cDate = colIndex_('date'), cOffer = colIndex_('offer_id'), cName = colIndex_('name'), cSku = colIndex_('sku');
  const width = COLUMNS.length;
  const rows = skus.map(function (sku) {
    const row = new Array(width).fill('');
    row[cDate - 1] = date;
    row[cOffer - 1] = info[sku].offer_id;
    row[cName - 1] = info[sku].name;
    row[cSku - 1] = sku;
    return row;
  });
  if (rows.length) {
    // Вставляем под шапку. Новые строки наследуют её оформление (синий фон, белый жирный
    // шрифт) — сбрасываем его; процентный формат колонок проставляет applyPercentFormat_.
    sheet.insertRowsAfter(1, rows.length);
    const block = sheet.getRange(2, 1, rows.length, width);
    block.clearFormat();
    block.setValues(rows);
  }
  return rowMapForDate_(sheet, date);
}

/** Дата ячейки как 'yyyy-MM-dd' (ячейка-дата или текст); пусто — ''. */
function cellDateStr_(d) {
  if (d instanceof Date) return Utilities.formatDate(d, CONFIG.TIMEZONE, 'yyyy-MM-dd');
  return String(d === null || d === undefined ? '' : d).trim();
}

/**
 * Оставляет в листе только последние CONFIG.KEEP_DAYS календарных дней, считая «вчера»
 * (дату данных): при KEEP_DAYS = 5 и данных за 21.10 остаются 17–21.10.
 * Заодно держит порядок «свежие сверху»: если он нарушен — сортирует лист по дате
 * по убыванию (шапка не участвует). Строки с пустой или нераспознанной датой не удаляются.
 */
function trimOldDays_() {
  const sheet = getSheet_();
  if (sheet.getLastRow() < 2) return;
  const width = COLUMNS.length;
  const cDate = colIndex_('date');
  const isDate = function (s) { return /^\d{4}-\d{2}-\d{2}$/.test(s); };
  const readDates = function () {
    return sheet.getRange(2, cDate, sheet.getLastRow() - 1, 1).getValues()
      .map(function (r) { return cellDateStr_(r[0]); });
  };

  // 1. Порядок: даты (без пустых) должны идти по убыванию.
  let dates = readDates();
  let prev = null, sorted = true;
  for (let i = 0; i < dates.length; i++) {
    if (!isDate(dates[i])) continue;
    if (prev !== null && dates[i] > prev) { sorted = false; break; }
    prev = dates[i];
  }
  if (!sorted) {
    sheet.getRange(2, 1, sheet.getLastRow() - 1, width).sort({ column: cDate, ascending: false });
    dates = readDates();
  }

  // 2. Удаляем дни раньше окна. После сортировки они лежат внизу одним блоком —
  //    удаляем группами подряд идущих строк, снизу вверх (номера выше не сдвигаются).
  const keepFrom = dateStr_(daysAgo_(CONFIG.KEEP_DAYS));
  const old = [];
  for (let i = 0; i < dates.length; i++) {
    if (isDate(dates[i]) && dates[i] < keepFrom) old.push(i + 2); // 1-based + шапка
  }
  // Google не даёт удалить все незакреплённые строки листа (скрипт не запускался дольше
  // KEEP_DAYS дней) — оставляем одну пустую строку в конце.
  if (old.length && old.length >= sheet.getMaxRows() - sheet.getFrozenRows()) {
    sheet.insertRowsAfter(sheet.getMaxRows(), 1);
  }
  let removed = 0;
  for (let j = old.length - 1; j >= 0; ) {
    let start = j;
    while (start > 0 && old[start - 1] === old[start] - 1) start--;
    sheet.deleteRows(old[start], j - start + 1);
    removed += j - start + 1;
    j = start - 1;
  }
  if (removed) console.log('Удалено строк старше ' + keepFrom + ': ' + removed);
}

/** Карта sku → номер строки для заданной даты. */
function rowMapForDate_(sheet, date) {
  const map = {};
  const last = sheet.getLastRow();
  if (last < 2) return map;
  const cDate = colIndex_('date'), cSku = colIndex_('sku');
  const values = sheet.getRange(2, 1, last - 1, COLUMNS.length).getValues();
  for (let i = 0; i < values.length; i++) {
    const d = values[i][cDate - 1];
    const dStr = (d instanceof Date) ? Utilities.formatDate(d, CONFIG.TIMEZONE, 'yyyy-MM-dd') : String(d).trim();
    if (dStr === date) map[String(values[i][cSku - 1]).trim()] = i + 2; // 1-based + шапка
  }
  return map;
}

/**
 * Записывает значения одной колонки для набора SKU (батчем).
 * fields: { sku: value }. Пишет только в строки текущей даты.
 */
function writeColumn_(colKey, fields) {
  const sheet = getSheet_();
  const map = ensureRowsForToday_();
  const col = colIndex_(colKey);
  // группируем в один диапазон по всей колонке (читаем текущее, меняем нужные строки, пишем назад)
  const last = sheet.getLastRow();
  if (last < 2) return;
  const range = sheet.getRange(2, col, last - 1, 1);
  const cur = range.getValues();
  Object.keys(map).forEach(function (sku) {
    if (fields[sku] !== undefined && fields[sku] !== null) {
      cur[map[sku] - 2][0] = fields[sku]; // -2: сдвиг на шапку и на старт диапазона
    }
  });
  range.setValues(cur);
}

/** Записать несколько колонок сразу из карты sku → {colKey: value}. */
function writeColumns_(dataBySku, colKeys) {
  colKeys.forEach(function (key) {
    const fields = {};
    Object.keys(dataBySku).forEach(function (sku) {
      if (dataBySku[sku][key] !== undefined) fields[sku] = dataBySku[sku][key];
    });
    writeColumn_(key, fields);
  });
}

// ============================ СПИСОК ТОВАРОВ ============================
function fetchProductList_() {
  const result = [];
  let lastId = '';
  do {
    const r = sellerFetch_('/v3/product/list', { filter: { visibility: 'ALL' }, last_id: lastId, limit: 1000 });
    const items = (r.result && r.result.items) || [];
    items.forEach(function (it) { result.push({ product_id: it.product_id, offer_id: it.offer_id }); });
    lastId = (r.result && r.result.last_id) || '';
    if (items.length === 0) break;
  } while (lastId);
  return result;
}

function fetchProductInfo_(products) {
  const bySku = {};
  const offerIds = products.map(function (p) { return p.offer_id; }).filter(String);
  for (let i = 0; i < offerIds.length; i += 1000) {
    const chunk = offerIds.slice(i, i + 1000);
    const r = sellerFetch_('/v3/product/info/list', { offer_id: chunk });
    const items = (r.items) || (r.result && r.result.items) || [];
    items.forEach(function (it) {
      const sku = String(it.sku || it.fbo_sku || it.fbs_sku || it.id);
      bySku[sku] = { sku: sku, offer_id: it.offer_id || '', name: it.name || '', product_id: it.id || '' };
    });
  }
  return bySku;
}

// ============================ БЛОК 1: АНАЛИТИКА (воронка) ============================
function updateAnalytics() {
  const dateTo = dataDate_(), dateFrom = dataDate_();
  // ВНИМАНИЕ по метрикам воронки:
  //   • session_view_pdp = «Посетители с просмотром карточки товара» (уник. сессии) =
  //     реальные «Переходы в карточку». Раньше ошибочно брался hits_view = «Показы, всего».
  //   • CR В КОРЗИНУ: берём conv_tocart_pdp = «Конверсия в корзину ИЗ КАРТОЧКИ товара»
  //     (как в отчёте «Продажи моих товаров»), а НЕ conv_tocart (общая к показам).
  //     Резерв: если conv_tocart_pdp пуст, считаем hits_tocart_pdp / session_view_pdp.
  //   • Метрики conv_* Ozon отдаёт в «процентах» (напр. 1.12 = 1.12%). Для настоящего
  //     процентного формата ячейки (0.0%) храним долю — делим на 100.
  const metrics = ['ordered_units', 'revenue', 'cancellations', 'session_view_pdp', 'hits_tocart_pdp', 'conv_tocart_pdp', 'position_category'];
  const bySku = {};
  let offset = 0; const limit = 1000;
  do {
    const r = sellerFetch_('/v1/analytics/data', {
      date_from: dateFrom, date_to: dateTo, metrics: metrics,
      dimension: ['sku'], filters: [], sort: [{ key: 'ordered_units', order: 'DESC' }],
      limit: limit, offset: offset
    });
    const rows = (r.result && r.result.data) || [];
    rows.forEach(function (row) {
      const sku = String(row.dimensions[0].id);
      const m = row.metrics;
      const ordered = num_(m[0]), pdpViews = num_(m[3]), tocartPdp = num_(m[4]);
      // CR в корзину из карточки: сначала готовая метрика conv_tocart_pdp,
      // если она пустая/0 — считаем сами как добавления_в_корзину / переходы_в_карточку.
      const convPdp = num_(m[5]);
      const crToCart = convPdp > 0
        ? round4_(convPdp / 100)                               // «%» Ozon → доля (10.5 → 0.105)
        : (pdpViews > 0 ? round4_(tocartPdp / pdpViews) : 0);  // резервный расчёт
      bySku[sku] = {
        ordered_units: ordered, revenue: num_(m[1]), cancellations: num_(m[2]),
        hits_view: pdpViews,                                    // session_view_pdp → переходы в карточку
        conv_tocart: crToCart,                                  // CR в корзину из карточки (доля)
        cr_to_order: tocartPdp > 0 ? round4_(ordered / tocartPdp) : 0, // доля (заказано / в корзину)
        position: Math.round(num_(m[6]))
      };
    });
    if (rows.length < limit) break;
    offset += limit;
  } while (offset < 100000);

  writeColumns_(bySku, ['ordered_units', 'revenue', 'cancellations', 'hits_view', 'conv_tocart', 'cr_to_order', 'position']);
  applyPercentFormat_(['conv_tocart', 'cr_to_order']);
  SpreadsheetApp.getActive().toast('Заказы/воронка обновлены.', 'Ozon', 3);
}

// ============================ БЛОК 2: ОСТАТКИ ============================
function updateStocks() {
  const bySku = {};
  let offset = 0; const limit = 1000;
  do {
    const r = sellerFetch_('/v2/analytics/stock_on_warehouses', { limit: limit, offset: offset, warehouse_type: 'ALL' });
    const rows = (r.result && r.result.rows) || [];
    rows.forEach(function (row) {
      const sku = String(row.sku);
      if (!bySku[sku]) bySku[sku] = { stock: 0 };
      bySku[sku].stock += num_(row.free_to_sell_amount);
    });
    if (rows.length < limit) break;
    offset += limit;
  } while (offset < 100000);
  writeColumns_(bySku, ['stock']);
  SpreadsheetApp.getActive().toast('Остатки обновлены.', 'Ozon', 3);
}

// ============================ БЛОК 3: ЦЕНЫ + ИНДЕКС + СПП ============================
function updatePrices() {
  // /v5/product/info/prices возвращает offer_id и product_id, НО НЕ sku.
  // Строки в листе привязаны к SKU, поэтому собираем данные по offer_id,
  // а потом переводим offer_id → sku по сопоставлению из самого листа.
  // Из ОДНОГО ответа берём и индекс цены (Pi), и СПП/соинвест (скидка за счёт Ozon).
  const byOffer = {};
  let cursor = '';
  do {
    const r = sellerFetch_('/v5/product/info/prices', { cursor: cursor, filter: { visibility: 'ALL' }, limit: 1000 });
    const items = (r.items) || (r.result && r.result.items) || [];
    items.forEach(function (it) {
      const offer = String(it.offer_id || '');
      if (!offer) return;
      const rec = { price_index: extractPriceIndex_(it) };

      // --- СПП / соинвест (скидка за счёт Ozon) ---
      // Ozon убрал marketing_price из API (11.2025). Конечную цену покупателя
      // восстанавливаем из price_indexes по price_index_value и min_price.
      const price = it.price || {};
      const sellerPrice = num_(price.marketing_seller_price) || num_(price.price);
      const buyerPrice = calculateOzonBuyerPrice_(it.price_indexes);
      if (sellerPrice > 0 && buyerPrice !== null && buyerPrice < sellerPrice) {
        rec.spp_rub = round2_(sellerPrice - buyerPrice);
        rec.spp_pct = round4_((sellerPrice - buyerPrice) / sellerPrice); // доля → процентный формат ячейки (0.0%)
      } else {
        rec.spp_rub = 0;
        rec.spp_pct = 0;
      }

      byOffer[offer] = rec;
    });
    cursor = r.cursor || (r.result && r.result.cursor) || '';
    if (items.length === 0) break;
  } while (cursor);

  // offer_id → sku из текущих строк листа
  const bySku = {};
  const offerToSku = offerToSkuMapForToday_();
  Object.keys(byOffer).forEach(function (offer) {
    const sku = offerToSku[offer];
    if (sku) bySku[sku] = byOffer[offer];
  });

  writeColumns_(bySku, ['price_index', 'spp_rub', 'spp_pct']);
  applyPercentFormat_(['spp_pct']);
  SpreadsheetApp.getActive().toast('Цены / индекс / СПП обновлены.', 'Ozon', 3);
}

/**
 * Восстанавливает КОНЕЧНУЮ ЦЕНУ ПОКУПАТЕЛЯ из price_indexes.
 * Ozon отключил marketing_price в Seller API (12.11.2025), поэтому цену «со скидкой
 * Ozon» (СПП/соинвест) считаем обратно из индексов цены:
 *   price_index_value = наша_цена / конечная_цена  (по конкретному источнику сравнения)
 * Отсюда: если index < 1 → покупатель платит МЕНЬШЕ (есть скидка Ozon),
 *         buyerPrice = index * min_price;
 *         если index > 1 → buyerPrice = min_price / (2 - index);
 *         если index = 1 → buyerPrice = min_price.
 * Порядок источников: external → ozon → self (как в парсере пользователя).
 * Возвращает округлённую до 2 знаков цену или null, если данных нет.
 */
function calculateOzonBuyerPrice_(priceIndexes) {
  if (!priceIndexes) return null;
  const sources = [
    priceIndexes.external_index_data,
    priceIndexes.ozon_index_data,
    priceIndexes.self_marketplaces_index_data
  ];
  for (let i = 0; i < sources.length; i++) {
    const src = sources[i];
    if (!src) continue;
    const indexValue = num_(src.price_index_value);
    const minPrice = num_(src.min_price);
    if (indexValue > 0 && minPrice > 0) {
      let buyerPrice;
      if (indexValue < 1) buyerPrice = indexValue * minPrice;
      else if (indexValue > 1) buyerPrice = minPrice / (2 - indexValue);
      else buyerPrice = minPrice;
      return round2_(buyerPrice);
    }
  }
  return null;
}

/** Карта offer_id → sku по строкам текущей даты (читаем из листа). */
function offerToSkuMapForToday_() {
  const sheet = getSheet_();
  const date = dataDate_();
  const map = {};
  const last = sheet.getLastRow();
  if (last < 2) return map;
  const cDate = colIndex_('date'), cOffer = colIndex_('offer_id'), cSku = colIndex_('sku');
  const values = sheet.getRange(2, 1, last - 1, COLUMNS.length).getValues();
  for (let i = 0; i < values.length; i++) {
    const d = values[i][cDate - 1];
    const dStr = (d instanceof Date) ? Utilities.formatDate(d, CONFIG.TIMEZONE, 'yyyy-MM-dd') : String(d).trim();
    if (dStr === date) {
      const offer = String(values[i][cOffer - 1]).trim();
      const sku = String(values[i][cSku - 1]).trim();
      if (offer) map[offer] = sku;
    }
  }
  return map;
}

/**
 * Извлекает индекс цены (Pi) из ответа /v5/product/info/prices.
 * В price_indexes Озон отдаёт ТРИ отдельных сравнения:
 *   • ozon_index_data          — ваша цена vs другие продавцы на самом Ozon;
 *   • external_index_data      — vs тот же товар на других площадках (WB, Я.Маркет…);
 *   • self_marketplaces_index_data — vs ваши же цены на других площадках.
 * В кабинете (отчёт «Ценовой индекс товара на рынке») показывается ОДИН итоговый
 * индекс = самое НЕВЫГОДНОЕ (максимальное) из трёх сравнений. Поэтому берём MAX.
 * Если числовых значений нет — возвращаем цветовой индекс текстом либо пусто.
 */
function extractPriceIndex_(it) {
  const pix = it.price_indexes || (it.price_index && typeof it.price_index === 'object' ? it.price_index : null);
  if (pix) {
    const candidates = [
      pix.ozon_index_data, pix.external_index_data, pix.self_marketplaces_index_data
    ];
    let maxIndex = 0;
    for (let i = 0; i < candidates.length; i++) {
      const v = candidates[i] && num_(candidates[i].price_index_value);
      if (v && v > 0 && v > maxIndex) maxIndex = v;
    }
    if (maxIndex > 0) return maxIndex;
    // числового индекса нет — отдаём цветовой (текст) либо пусто
    const color = pix.color_index || '';
    return (color && color !== 'WITHOUT_INDEX' && color !== 'COLOR_INDEX_WITHOUT_INDEX' && color !== 'COLOR_INDEX_UNSPECIFIED') ? color : '';
  }
  // устаревшее поле (на случай старого ответа)
  return it.price_index || '';
}

// ============================ ТРИГГЕРЫ ============================
/**
 * Триггер:
 *   • runBase — ежедневно в 09:00: собирает все базовые метрики за вчера
 *     (заказы/воронка, остатки, цены/индекс/СПП).
 *
 * Каталог ~180 товаров укладывается в лимит Apps Script (6 минут) за один утренний
 * запуск, поэтому дополнительный «догоняющий» триггер больше не нужен.
 */
function installDailyTriggers() {
  removeTriggers();
  const hour = getTriggerHour_();
  ScriptApp.newTrigger('runBase').timeBased().atHour(hour).everyDays(1).inTimezone(CONFIG.TIMEZONE).create();
  SpreadsheetApp.getActive().toast('Триггер установлен: ежедневный сбор в ' + hour + ':00.', 'Ozon', 8);
}

/**
 * Диалог задания часа ежедневного автозапуска (0–23).
 * Значение сохраняется в Script Properties. После сохранения, если триггер уже
 * установлен, он автоматически пересоздаётся на новый час.
 */
function setupTriggerHour() {
  const ui = SpreadsheetApp.getUi();
  const cur = getTriggerHour_();
  const resp = ui.prompt(
    'Час автозапуска',
    'Введите час ежедневного обновления (0–23). Сейчас: ' + cur + ':00.',
    ui.ButtonSet.OK_CANCEL
  );
  if (resp.getSelectedButton() !== ui.Button.OK) return;
  const txt = resp.getResponseText().trim();
  const h = parseInt(txt, 10);
  if (isNaN(h) || h < 0 || h > 23) {
    ui.alert('Некорректное значение. Введите целое число от 0 до 23.');
    return;
  }
  PropertiesService.getScriptProperties().setProperty(PROP.TRIGGER_HOUR, String(h));

  // Если ежедневный триггер уже есть — пересоздаём на новый час.
  const hasTrigger = ScriptApp.getProjectTriggers().some(function (t) {
    return t.getHandlerFunction() === 'runBase';
  });
  if (hasTrigger) {
    installDailyTriggers();
  } else {
    SpreadsheetApp.getActive().toast('Час автозапуска сохранён: ' + h + ':00. Нажмите «Установить ежедневные триггеры».', 'Ozon', 8);
  }
}

/**
 * Триггерная функция утра: очистка старых дней, затем базовые метрики (заказы, остатки, цены/СПП).
 * Очистка идёт первой, чтобы сбой сбора (лимит Ozon) её не отменил; её собственная ошибка
 * сбор не останавливает.
 */
function runBase() {
  try {
    trimOldDays_();
  } catch (e) {
    console.error('Очистка старых дней не выполнена: ' + e);
  }
  ensureRowsForToday_();
  updateAnalytics();
  updateStocks();
  updatePrices();
}

function removeTriggers() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    const fn = t.getHandlerFunction();
    if (fn === 'runBase' || fn === 'autoContinue' || fn === 'runAll') ScriptApp.deleteTrigger(t);
  });
}

/**
 * Назначает колонкам настоящий процентный формат ячейки (0.0%).
 * Значения хранятся как доля (0.011), Google показывает «1,1%».
 */
function applyPercentFormat_(colKeys) {
  const sheet = getSheet_();
  const last = sheet.getLastRow();
  if (last < 2) return;
  colKeys.forEach(function (key) {
    const col = colIndex_(key);
    if (col > 0) sheet.getRange(2, col, last - 1, 1).setNumberFormat('0.0%');
  });
}

// ============================ УТИЛИТЫ ============================
function daysAgo_(n) { const d = new Date(); d.setDate(d.getDate() - n); return d; }
function dateStr_(d) { return Utilities.formatDate(d, CONFIG.TIMEZONE, 'yyyy-MM-dd'); }
function num_(v) {
  if (v === '' || v === null || v === undefined) return 0;
  const n = parseFloat(String(v).replace(',', '.').replace(/\s/g, ''));
  return isNaN(n) ? 0 : n;
}
function round2_(n) { return Math.round(n * 100) / 100; }
function round4_(n) { return Math.round(n * 10000) / 10000; }
