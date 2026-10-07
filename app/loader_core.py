# -*- coding: utf-8 -*-
"""
ТД АВТОПРОФИ — загрузчик отчётов MPPROFIT (Ozon + WB) в PostgreSQL.

Переносит логику нормализации из process_reports.py, но вместо xlsx
пишет в таблицу fact_weekly, подставляя 3 уровня категории и статус
из catalog_items. Перезапись недели = DELETE по (year, week, marketplace)
+ INSERT. Каждая загрузка фиксируется в report_uploads.

Запуск:
    pip install openpyxl "psycopg[binary]"
    export DB_DSN="postgresql://user:pass@host:6432/dbname?sslmode=require"
    python load_to_db.py --mp ozon --file oz-01-07.06.2026.xlsx
    python load_to_db.py --mp wb   --file wb-01-07.06.2026.xlsx
"""
import openpyxl, datetime, re, sys, os, argparse
import psycopg
import psycopg.errors

# Канонизация артикула — единый источник правды (app/util.py).
# Поддержка запуска и как пакета (app.loader_core), и как скрипта (CLI).
try:
    from .util import canon_article
except ImportError:  # запуск файла напрямую (python load_to_db.py ...)
    from util import canon_article

# ---------------------------------------------------------------------------
# 1. ЭТАЛОННЫЕ КАРТЫ КОЛОНОК (проверка формата). Подзаголовок -> ожидаемый № колонки.
# ---------------------------------------------------------------------------
OZ_EXPECT = {
 "Артикул продавца":1,"SKU":2,"Наименование":3,"Заказы шт.":4,"Выкупы шт.":5,
 "Возвраты шт.":6,"Невыкупы/отмены шт.":7,"Выкупы, руб.":8,"Возвраты руб.":9,
 "Продажи руб.":10,"Продажи шт.":11,"Доля выручки %":12,"Средняя цена продажи":13,
 "% выкупа":14,"Себестоимость":15,"% себест.от выручки":16,"Комиссия":17,"% комиссии":18,
 "Эквайринг":19,"% эквайринга":20,"Прямая логистика":21,"Доставка до места выдачи":22,
 "Выдача товара":23,"Обработка возвратов и невыкупов":24,"Обратная логистика":25,
 "Вся логистика":26,"% логистики":27,"Логистика на ед.":28,"Оплата за заказ":29,
 "% Оплаты за заказ":30,"Оплата за клики":31,"% Оплата за клики":32,"Спецразмещение":33,
 "% Спецразмещения":34,"Оплата за заказ - все товары":35,"% Оплаты за заказ - все товары":36,
 "Звёздные товары":37,"% звёздных товаров":38,"Подписка Управление отзывами":39,
 "Реклама в сети Интернет на Сайте":40,"Бонусы продавца":41,"Баллы за отзывы":42,
 "Продвижение бренда":43,"Бейдж Оригинал":44,"Продвижение всего":45,"% Всего продвижения":46,
 "Ozon-Рассрочка":47,"Хранение":48,"Утилизация":49,"Утилизация кол-во":50,
 "Все удержания OZON":51,"% всех удержаний":52,"Налоговая база":53,"Налог":54,
 "Внешние расходы/доходы":55,"Внеш.расх/дох. в % от выр.":56,"Выплата на р/с":57,
 "Прибыль":58,"Прибыль на ед.товара":59,"Марж-ность":60,"ROI":61,
 "По выручке":62,"По продажам, шт.":63,"По прибыли":64,
}
# Алиасы заголовков Ozon: КАНОНИЧЕСКОЕ имя (как в OZ_EXPECT/METRIC_MAP/ID_MAP) ->
# список ВОЗМОЖНЫХ реальных заголовков в файле (старый + новый формат MPPROFIT).
# В июне 2026 MPPROFIT выпустил новую версию формата Ozon: часть колонок
# переименована, добавлены новые. Чтобы поддержать обе версии БЕЗ привязки к
# позициям, после build_colidx обогащаем idx этими алиасами (см. apply_oz_aliases).
OZ_NAME_ALIASES = {
    "Заказы шт.":              ["Заказы шт.", "Продажи"],
    "Комиссия":               ["Комиссия", "Комиссии"],
    "Прямая логистика":       ["Прямая логистика", "Логистика"],
    "Оплата за заказ":        ["Оплата за заказ", "Продвижение"],
    "Внешние расходы/доходы": ["Внешние расходы/доходы", "Внешние расходы"],
    "По выручке":             ["По выручке", "ABC"],
    # переименований ниже нет, но перечисляем для устойчивости к будущим правкам
    "Все удержания OZON":     ["Все удержания OZON"],
    "Продажи руб.":           ["Продажи руб."],
    "Продажи шт.":            ["Продажи шт."],
    "Прибыль":                ["Прибыль"],
    "Марж-ность":             ["Марж-ность"],
}

# Ключевые заголовки Ozon, без которых расчёт невозможен (проверка формата по
# ИМЕНИ, а не по позиции). Для каждого достаточно, чтобы в файле присутствовал
# хотя бы один из алиасов.
OZ_REQUIRED = [
    "Артикул продавца", "Наименование", "Продажи руб.", "Продажи шт.",
    "Себестоимость", "Комиссия", "Вся логистика", "Все удержания OZON",
    "Прибыль", "Марж-ность",
]

# Ключевые заголовки Wildberries — по образцу OZ_REQUIRED: проверка формата по
# ИМЕНИ (наличию заголовка), а не по жёсткой позиции. Устойчиво к добавлению или
# перестановке колонок MPPROFIT (напр. новая «Заказы, руб.» сдвигает позиции —
# позиционная проверка падала, проверка по именам — нет). Имена ДОЛЖНЫ дословно
# совпадать с wb-полями METRIC_MAP/ID_MAP, т.к. значения читаются через idx.get().
WB_REQUIRED = [
    "Артикул продавца", "Наименование", "Заказы шт.", "Продажи, шт.",
    "Возвраты шт.", "Отмены шт.", "Итого продаж шт.", "Выручка",
    "Продажи", "Возвраты", "Себестоимость", "Комиссия",
    "Все удержания", "Выплата на р/с", "Прибыль",
]

def apply_oz_aliases(idx):
    """Обогащает idx (заголовок->колонка): для каждого канонического имени из
    OZ_NAME_ALIASES, если его ещё нет в idx, но есть один из его алиасов —
    добавляет каноническое имя, указывающее на ту же колонку. Так весь
    существующий маппинг (по старым именам) продолжает работать на новом файле."""
    for canon, aliases in OZ_NAME_ALIASES.items():
        if canon in idx:
            continue
        for a in aliases:
            if a in idx:
                idx[canon] = idx[a]
                break
    return idx

WB_EXPECT = {
 "Брэнд":1,"Наименование":2,"Артикул продавца":3,"Штрихкод":4,"Размер":5,
 "Заказы шт.":6,"Продажи, шт.":7,"Возвраты шт.":8,"Отмены шт.":9,"Продажи":10,
 "Возвраты":11,"Выручка":12,"Итого продаж шт.":13,"Средняя цена продажи":14,"СПП %":15,
 "% выкупа":16,"Себестоимость":17,"% себест. от выручки":18,"Комиссия":19,"% комиссии":20,
 "Прямая логистика":21,"Обратная логистика":22,"Полная логистика":23,"Логистика на ед.":24,
 "% логистики":25,"Хранение":26,"% хранения":27,"Прочие удержания":28,"Включая продвижение":29,
 "% прочих удержаний":30,"Приемка":31,"% приемки":32,"Компенсация брака":33,
 "Количество брака шт.":34,"Компенсация потеряшек":35,"Количество потеряшек шт.":36,
 "Компенсация подмен":37,"Количество подмен шт.":38,"Компенсация за платную доставку":39,
 "Кол-во компенсаций за платную доставку шт.":40,"Штрафы":41,"Участие в ПЛ":42,
 "Стоимость участия в ПЛ":43,"Изменение срока перечисления":44,"Доплаты":45,"Все удержания":46,
 "% всех удержаний":47,"Внешние расходы/доходы":48,"Внеш.расх/дох. в % от выр.":49,
 "Налоговая база":50,"Налог":51,"Выплата на р/с":52,"Прибыль":53,"Прибыль на ед.товара":54,
 "Марж-ность":55,"ROI":56,"Вся реклама":57,"Баланс":58,"Промо бонусы":59,"ДДР от выручки":60,
 "Прибыль (с рекл.)":61,"Прибыль на ед.товара (с рекл.)":62,"Марж-ность (с рекл.)":63,
 "ROI (с рекл.)":64,"ABC по выручке":65,"ABC по продажам, шт.":66,"ABC по прибыли":67,
}

# ---------------------------------------------------------------------------
# 2. МАППИНГ: колонка БД -> (поле_Ozon, поле_WB). None = метрики нет у МП.
#    Спец-поля (__week__ и т.п.) подставляются отдельно.
# ---------------------------------------------------------------------------
METRIC_MAP = [
    # (db_col, oz_field, wb_field)
    ("orders_qty",            "Заказы шт.",            "Заказы шт."),
    ("orders_rub",            None,                    "Заказы, руб."),
    ("sales_qty",             "Продажи шт.",           "Продажи, шт."),
    ("returns_qty",           "Возвраты шт.",          "Возвраты шт."),
    ("cancels_qty",           "Невыкупы/отмены шт.",   "Отмены шт."),
    ("total_sales_qty",       "Выкупы шт.",            "Итого продаж шт."),
    ("revenue",               "Продажи руб.",          "Выручка"),
    ("sales_gross",           "Выкупы, руб.",          "Продажи"),
    ("returns_rub",           "Возвраты руб.",         "Возвраты"),
    ("revenue_share_pct",     "Доля выручки %",        None),
    ("avg_sale_price",        "Средняя цена продажи",  "Средняя цена продажи"),
    ("spp_pct",               None,                    "СПП %"),
    ("buyout_pct",            "% выкупа",              "% выкупа"),
    ("cogs",                  "Себестоимость",         "Себестоимость"),
    ("cogs_pct",              "% себест.от выручки",   "% себест. от выручки"),
    ("commission",            "Комиссия",              "Комиссия"),
    ("commission_pct",        "% комиссии",            "% комиссии"),
    ("acquiring",             "Эквайринг",             None),
    ("acquiring_pct",         "% эквайринга",          None),
    ("logistics_direct",      "Прямая логистика",      "Прямая логистика"),
    ("logistics_reverse",     "Обратная логистика",    "Обратная логистика"),
    ("logistics_total",       "Вся логистика",         "Полная логистика"),
    ("logistics_per_unit",    "Логистика на ед.",      "Логистика на ед."),
    ("logistics_pct",         "% логистики",           "% логистики"),
    ("oz_delivery_to_point",  "Доставка до места выдачи", None),
    ("oz_item_handout",       "Выдача товара",         None),
    ("oz_returns_processing", "Обработка возвратов и невыкупов", None),
    ("storage",               "Хранение",              "Хранение"),
    ("storage_pct",           None,                    "% хранения"),
    ("wb_acceptance",         None,                    "Приемка"),
    ("oz_disposal",           "Утилизация",            None),
    ("promo_total",           "Продвижение всего",     "Включая продвижение"),
    ("promo_pct",             "% Всего продвижения",   None),
    ("wb_ads_total",          None,                    "Вся реклама"),
    ("wb_drr_pct",            None,                    "ДДР от выручки"),
    ("oz_pay_per_order",      "Оплата за заказ",       None),
    ("oz_pay_per_click",      "Оплата за клики",       None),
    ("oz_special_placement",  "Спецразмещение",        None),
    ("wb_other_holds",        None,                    "Прочие удержания"),
    ("wb_fines",              None,                    "Штрафы"),
    ("wb_defect_compensation",None,                    "Компенсация брака"),
    ("holds_total",           "Все удержания OZON",    "Все удержания"),
    ("holds_total_pct",       "% всех удержаний",      "% всех удержаний"),
    ("tax_base",              "Налоговая база",        "Налоговая база"),
    ("tax",                   "Налог",                 "Налог"),
    ("external_exp_inc",      "Внешние расходы/доходы","Внешние расходы/доходы"),
    ("external_exp_inc_pct",  "Внеш.расх/дох. в % от выр.","Внеш.расх/дох. в % от выр."),
    ("payout",                "Выплата на р/с",        "Выплата на р/с"),
    ("profit",                "Прибыль",               "Прибыль"),
    ("profit_per_unit",       "Прибыль на ед.товара",  "Прибыль на ед.товара"),
    ("margin_pct",            "Марж-ность",            "Марж-ность"),
    ("roi",                   "ROI",                   "ROI"),
    ("wb_profit_with_ads",    None,                    "Прибыль (с рекл.)"),
    ("wb_margin_with_ads_pct",None,                    "Марж-ность (с рекл.)"),
    ("wb_roi_with_ads",       None,                    "ROI (с рекл.)"),
    ("abc_revenue",           "По выручке",            "ABC по выручке"),
    ("abc_sales_qty",         "По продажам, шт.",      "ABC по продажам, шт."),
    ("abc_profit",            "По прибыли",            "ABC по прибыли"),
]

# Идентификаторы товара (не метрики): db_col -> (oz_field, wb_field)
ID_MAP = [
    ("seller_article", "Артикул продавца", "Артикул продавца"),
    ("sku_ozon",       "SKU",              None),
    ("barcode_wb",     None,               "Штрихкод"),
    ("item_name",      "Наименование",     "Наименование"),
    ("brand",          None,               "Брэнд"),
]

# Текстовые ABC-колонки — их не приводим к числу
TEXT_COLS = {"abc_revenue", "abc_sales_qty", "abc_profit",
             "seller_article", "sku_ozon", "barcode_wb", "item_name", "brand"}

# ---------------------------------------------------------------------------
# 2-Я. ЯНДЕКС МАРКЕТ — отдельная карта колонок.
#
# Отчёт Яндекса (лист «ABC») имеет ДВУХУРОВНЕВУЮ шапку: строка 4 = СЕКЦИИ
# (Продажи / Комиссии / Платежи / Логистика / Продвижение / Другие удержания /
# Все удержания ЯМ / Налог / Внешние расходы / Финансы / ABC), строка 5 =
# МЕТРИКИ. Внутри разных секций встречаются ОДИНАКОВЫЕ имена метрик
# («Итого» у Платежей и у Продвижения, «Комиссия» и т.п.), поэтому обычный
# индекс по одному имени (build_colidx) для Яндекса НЕ годится. Используем
# СОСТАВНОЙ ключ «Секция :: Метрика» — он уникален для всех нужных колонок
# (проверено на реальном отчёте 03–09.08.2026). Секции строки 4 «протягиваем»
# вперёд (forward-fill), т.к. в файле они объединены (merge) и заданы только
# в первой колонке секции.
#
# Раскладка строк как у Ozon/WB: период — строка 2, шапка — строки 4/5,
# ИТОГО — строка 6, товары — с 7-й. Это позволяет переиспользовать
# get_period_and_week, detect_period_kind, load_catalog и общий INSERT.
#
# Семантика прибыли/рекламы совпадает с OZON: в отчёте Яндекса «Прибыль»
# (col78) уже посчитана НЕТТО (после комиссии, логистики, продвижения,
# удержаний, налога, внешних). Продвижение кладём В promo_total
# ОТРИЦАТЕЛЬНЫМ (как у Ozon), чтобы фронтовые формулы РНП (mp-ветка Ozon)
# работали один-в-один: -promo = расход на рекламу, а прибыль дважды не
# уменьшается. Общие (неразносимые по SKU) расходы Яндекса кладём в строку
# «Общие удержания» как нераспределённый остаток «Все удержания» из ИТОГО
# (тот же механизм, что у Ozon) — см. load_report ниже.
# ---------------------------------------------------------------------------
YA_MAP = [
    # (db_col, "Секция :: Метрика")
    ("orders_qty",        "Продажи :: Заказы шт."),
    ("orders_rub",        "Продажи :: Заказы, руб."),
    ("sales_qty",         "Продажи :: Продажи шт."),
    ("returns_qty",       "Продажи :: Возвраты шт."),
    ("cancels_qty",       "Продажи :: Невыкупы/отмены шт."),
    ("total_sales_qty",   "Продажи :: Выкупы шт."),
    ("revenue",           "Продажи :: Продажи руб."),
    ("sales_gross",       "Продажи :: Выкупы, руб."),
    ("returns_rub",       "Продажи :: Возвраты руб."),
    ("revenue_share_pct", "Продажи :: Доля выручки %"),
    ("avg_sale_price",    "Продажи :: Средняя цена продажи"),
    ("spp_pct",           "Продажи :: Скидка ЯМ для покупателя"),
    ("buyout_pct",        "Продажи :: % выкупа"),
    ("cogs",              "Себестоимость :: Себестоимость"),
    ("cogs_pct",          "Себестоимость :: % себестоимости"),
    ("commission",        "Комиссии :: Комиссия"),
    ("commission_pct",    "Комиссии :: % комиссии"),
    ("acquiring",         "Платежи :: Итого"),
    ("acquiring_pct",     "Платежи :: % от выручки"),
    ("logistics_direct",  "Логистика :: Доставка покупателю"),
    ("logistics_reverse", "Логистика :: Доставка возвратов"),
    ("logistics_total",   "Логистика :: Вся логистика"),
    ("logistics_pct",     "Логистика :: % логистики"),
    ("logistics_per_unit","Логистика :: Логистика на ед."),
    # Хранение — в секции «Другие удержания». Ключевая метрика для FBY.
    ("storage",           "Другие удержания :: Хранение"),
    ("promo_total",       "Продвижение :: Итого"),
    ("promo_pct",         "Продвижение :: % от выручки"),
    ("holds_total",       "Все удержания ЯМ :: Все удержания"),
    ("holds_total_pct",   "Все удержания ЯМ :: % всех удержаний"),
    ("tax_base",          "Налог :: Налоговая база"),
    ("tax",               "Налог :: Налог"),
    ("external_exp_inc",  "Внешние расходы :: Внешние расходы"),
    ("payout",            "Финансы :: Выплата на р/с"),
    ("profit",            "Финансы :: Прибыль"),
    ("profit_per_unit",   "Финансы :: Прибыль на ед."),
    ("margin_pct",        "Финансы :: Марж-ность"),
    ("roi",               "Финансы :: ROI"),
    ("abc_revenue",       "ABC :: По выручке"),
    ("abc_sales_qty",     "ABC :: По продажам, шт."),
    ("abc_profit",        "ABC :: По прибыли"),
]

# Идентификаторы товара Яндекса (составные ключи шапки).
YA_ID_MAP = [
    ("seller_article", "Продажи :: Артикул продавца"),  # фактически row4 col1
    ("item_name",      "Продажи :: Наименование"),      # фактически row4 col2
]

# Ключевые составные заголовки Яндекса, без которых расчёт невозможен.
YA_REQUIRED = [
    "Себестоимость :: Себестоимость",
    "Комиссии :: Комиссия",
    "Логистика :: Вся логистика",
    "Продвижение :: Итого",
    "Все удержания ЯМ :: Все удержания",
    "Финансы :: Прибыль",
    "Финансы :: Марж-ность",
    "Продажи :: Продажи руб.",
]

# Метрики, которые для Яндекса храним ОТРИЦАТЕЛЬНЫМИ (как у Ozon), чтобы
# фронтовые формулы (-promo = расход на рекламу) работали единообразно.
YA_NEGATE = {"promo_total"}

def build_ya_colidx(ws):
    """Составной индекс «Секция :: Метрика» -> № колонки для отчёта Яндекса.
    Секции (строка 4) объединены merge — протягиваем вперёд. Метрики — строка 5.
    Для колонок 1-2 (Артикул/Наименование) метрика в строке 5 пустая — тогда
    ключ = «Продажи :: <текст строки 4>», а также кладём алиасы под секцию
    «Продажи», чтобы YA_ID_MAP находил их по единому шаблону."""
    idx = {}
    section = ""
    for c in range(1, ws.max_column + 1):
        s4 = ws.cell(4, c).value
        if s4 is not None and str(s4).strip():
            section = str(s4).strip()
        m5 = ws.cell(5, c).value
        m5 = str(m5).strip() if m5 is not None else ""
        if m5:
            idx.setdefault(f"{section} :: {m5}", c)
        else:
            # колонка без метрики в стр.5 — это заголовок в стр.4 (напр. Артикул,
            # Наименование). Кладём под фактической секцией и под «Продажи»
            # (id-колонки Яндекса физически стоят в начале, до секций-метрик).
            label = str(s4).strip() if s4 is not None else ""
            if label:
                idx.setdefault(f"{section} :: {label}", c)
                idx.setdefault(f"Продажи :: {label}", c)
    return idx

# Полный порядок колонок для INSERT.
# Период-колонка («week» для недельных / «month» для месячных) подставляется
# в начало динамически в load_report — она тут НЕ перечислена.
DIM_COLS = ["year","period_text","period_start","period_end",
            "marketplace","row_type"]
CAT_COLS = ["category_l1","category_l2","category_l3","status"]
ID_COLS  = [m[0] for m in ID_MAP]
METRIC_COLS = [m[0] for m in METRIC_MAP]
# Без периода-колонки и без upload_id (они добавляются в load_report)
BASE_COLS = DIM_COLS + ID_COLS + CAT_COLS + METRIC_COLS

# Названия месяцев (рус.) для человекочитаемого вывода типа отчёта
RU_MONTHS = ["", "Январь","Февраль","Март","Апрель","Май","Июнь",
             "Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"]

# ---------------------------------------------------------------------------
# 3. Вспомогательные функции (повторяют process_reports.py)
# ---------------------------------------------------------------------------
def check_format(ws, expect, mp):
    errors = []
    for name, col in expect.items():
        v4 = ws.cell(4, col).value
        v5 = ws.cell(5, col).value
        v4 = str(v4).strip() if v4 is not None else ""
        v5 = str(v5).strip() if v5 is not None else ""
        if name not in (v4, v5):
            errors.append(f"[{mp}] колонка {col}: ожидалось «{name}», получено «{v4 or v5}»")
    return errors

def detect_period_kind(start, end):
    """Определяет тип отчёта по длине периода (в днях).
    Неделя: 6-7 дней (включительный диапазон пон-вск → (end-start).days == 6).
    Месяц: 27-31 день (любой календарный месяц: февраль=27..28, остальные=29..30).
    Иначе — ОТКЛОНЯЕМ (защита от «тихой» ошибки произвольного периода).
    Возвращает 'weekly' или 'monthly'."""
    days = (end - start).days
    if 5 <= days <= 7:
        return "weekly"
    if 27 <= days <= 31:
        return "monthly"
    raise ValueError(
        f"Не удалось распознать тип отчёта: период {days+1} дн. "
        f"({start.strftime('%d.%m.%Y')} — {end.strftime('%d.%m.%Y')}). "
        "Поддерживаются только еженедельные (6-7 дн.) и ежемесячные (28-31 дн.) отчёты.")

def get_period_and_week(ws):
    txt = ""
    for c in range(1, 6):
        v = ws.cell(2, c).value
        if v and "Период" in str(v):
            txt = str(v); break
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*-\s*(\d{4}-\d{2}-\d{2})", txt)
    if not m:
        raise ValueError("Не найден период в строке 2 отчёта")
    start = datetime.date.fromisoformat(m.group(1))
    end = datetime.date.fromisoformat(m.group(2))
    iso = start.isocalendar()
    period_str = f"{start.strftime('%d.%m.%Y')} - {end.strftime('%d.%m.%Y')}"
    # Месяц/год берём по дате НАЧАЛА периода (для месячных отчётов).
    # Год для недельных — ISO-год (iso[0]); для месячных — календарный start.year.
    return {
        "period": period_str, "start": start, "end": end,
        "week": iso[1], "iso_year": iso[0],
        "month": start.month, "cal_year": start.year,
    }

def build_colidx(ws):
    idx = {}
    for c in range(1, ws.max_column+1):
        for hr in (4, 5):
            v = ws.cell(hr, c).value
            if v and str(v).strip():
                idx.setdefault(str(v).strip(), c)
    return idx

def is_hold_row(art, name):
    a = ("" if art is None else str(art).strip()).lower()
    n = ("" if name is None else str(name).strip()).lower()
    return a.startswith("удержания") or n.startswith("удержания")

def is_empty_row(art, name):
    a = "" if art is None else str(art).strip()
    n = "" if name is None else str(name).strip()
    return a in ("", "-") and n == ""

def to_num(v):
    if v is None or v == "" or v == "-":
        return None
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    s = s.replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None

def _humanize_db_error(exc):
    """Превращает техническую ошибку БД в понятное сообщение для пользователя."""
    name = exc.__class__.__name__
    if name == "UniqueViolation" or isinstance(exc, getattr(psycopg.errors, "UniqueViolation", ())):
        return ("В файле есть повторяющиеся строки товаров (одинаковые артикул и штрихкод в одном периоде), "
                "поэтому отчёт не удалось загрузить. Проверьте исходный файл на дубликаты товаров.")
    if name in ("NotNullViolation", "ForeignKeyViolation", "CheckViolation"):
        return "Данные в файле не прошли проверку целостности базы. Проверьте корректность строк отчёта."
    if name in ("DataError", "InvalidTextRepresentation", "NumericValueOutOfRange"):
        return "В одной из ячеек некорректный формат значения (ожидалось число/дата). Проверьте данные в файле."
    return "Не удалось сохранить отчёт в базу данных. Попробуйте повторить загрузку позже или проверьте файл."

# ---------------------------------------------------------------------------
# 4. catalog_items: загрузка справочника в память для подстановки категорий
# ---------------------------------------------------------------------------
def load_catalog(conn):
    cat = {}
    with conn.cursor() as cur:
        cur.execute("SELECT seller_article, category_l1, category_l2, category_l3, status "
                    "FROM catalog_items")
        for art, l1, l2, l3, st in cur.fetchall():
            # Ключ — КАНОНИЧЕСКИЙ артикул, чтобы подстановка категорий/статуса
            # по строкам отчёта совпадала независимо от регистра/пробелов.
            cat[canon_article(art)] = (l1, l2, l3, st)
    return cat

# ---------------------------------------------------------------------------
# 5. Основная загрузка одного отчёта
# ---------------------------------------------------------------------------
def load_report(conn, path, mp_key):
    # ya = True → отчёт Яндекса (двухуровневая шапка, составной индекс колонок).
    ya = (mp_key == "yandex")
    if mp_key == "ozon":
        expect, mp_name, oz = OZ_EXPECT, "Ozon", True
    elif mp_key == "wb":
        expect, mp_name, oz = WB_EXPECT, "Wildberries", False
    elif ya:
        # У Яндекса нет позиционного expect — проверка формата по составным
        # именам (YA_REQUIRED). oz=False, чтобы не сработала oz-only спец-логика
        # общих удержаний (для Яндекса — своя ниже).
        expect, mp_name, oz = {}, "Yandex", False
    else:
        raise ValueError("mp должен быть 'ozon', 'wb' или 'yandex'")

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active

    # --- извлечение периода + определение типа отчёта ---
    pinfo = get_period_and_week(ws)
    period = pinfo["period"]; start = pinfo["start"]; end = pinfo["end"]
    # Тип определяем ДО проверки формата: нераспознанный период → ОТКЛОНИТЬ.
    try:
        period_kind = detect_period_kind(start, end)  # 'weekly' | 'monthly'
    except ValueError as ex:
        # Нераспознанный период — фиксируем в журнале и отклоняем.
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO report_uploads (marketplace, week, year, period_text, "
                "period_start, period_end, source_file, rows_loaded, status, message, period_kind) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (mp_name, None, pinfo["cal_year"], period, start, end,
                 os.path.basename(path), 0, "ОШИБКА ПЕРИОДА", str(ex), None))
        conn.commit()
        print("!!! НЕРАСПОЗНАННЫЙ ПЕРИОД — загрузка отклонена:", ex)
        return {"ok": False, "error": str(ex), "period_kind": None,
                "marketplace": mp_name, "period_text": period}

    # Период-ключи в зависимости от типа
    if period_kind == "weekly":
        target_table = "fact_weekly"
        period_col   = "week"
        period_val   = pinfo["week"]
        year         = pinfo["iso_year"]
        period_human = f"неделя {period_val}/{year}"
        kind_label   = "недельный отчёт"
    else:  # monthly
        target_table = "fact_monthly"
        period_col   = "month"
        period_val   = pinfo["month"]
        year         = pinfo["cal_year"]
        period_human = f"{RU_MONTHS[period_val]} {year}"
        kind_label   = "месячный отчёт"

    # --- индекс заголовков (по ИМЕНАМ) ---
    # Яндекс: СОСТАВНОЙ индекс «Секция :: Метрика» (двухуровневая шапка).
    # Ozon/WB: обычный индекс по одному имени.
    idx = build_ya_colidx(ws) if ya else build_colidx(ws)
    # И Ozon, И WB проверяются ПО НАЛИЧИЮ ключевых заголовков (по именам), а не по
    # жёстким позициям. Это устойчиво к добавлению/перестановке колонок MPPROFIT
    # (напр. новая колонка «Заказы, руб.» у WB сдвигала позиции — прежняя
    # позиционная check_format падала). Для Ozon предварительно обогащаем idx
    # алиасами имён (поддержка старого и нового формата). Значения затем читаются
    # через idx.get() по именам, поэтому лишние/сдвинутые колонки не мешают.
    if ya:
        missing = [name for name in YA_REQUIRED if name not in idx]
        errs = ([f"[YANDEX] не найдены ключевые колонки: " + ", ".join(missing)]
                if missing else [])
    elif oz:
        apply_oz_aliases(idx)
        missing = [name for name in OZ_REQUIRED if name not in idx]
        errs = ([f"[OZON] не найдены ключевые колонки: " + ", ".join(missing)]
                if missing else [])
    else:
        missing = [name for name in WB_REQUIRED if name not in idx]
        errs = ([f"[WILDBERRIES] не найдены ключевые колонки: " + ", ".join(missing)]
                if missing else [])
    if errs:
        msg = "ОШИБКА ФОРМАТА: " + "; ".join(errs[:5])
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO report_uploads (marketplace, week, month, year, period_text, "
                "period_start, period_end, source_file, rows_loaded, status, message, period_kind) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (mp_name,
                 period_val if period_kind == "weekly" else None,
                 period_val if period_kind == "monthly" else None,
                 year, period, start, end,
                 os.path.basename(path), 0, "ОШИБКА ФОРМАТА", msg, period_kind))
        conn.commit()
        print("!!! СТРУКТУРА ОТЧЁТА ИЗМЕНИЛАСЬ — загрузка отклонена:")
        for e in errs: print("   -", e)
        return {"ok": False, "error": msg, "period_kind": period_kind,
                "marketplace": mp_name, "period_text": period}

    cat = load_catalog(conn)

    # Автодобавление новых артикулов в справочник (без категорий/статуса) —
    # чтобы они попали в раздел «нераспределённые» в дереве РНП.
    # Артикул/наименование — по ИМЕНИ из idx (устойчиво к сдвигу колонок);
    # fallback к позиции из expect для полной совместимости (в т.ч. Ozon).
    # Артикул/Наименование: у Яндекса — по составному ключу (build_ya_colidx
    # положил алиасы «Продажи :: Артикул продавца/Наименование»); у Ozon/WB
    # — по обычному имени с fallback к expect.
    if ya:
        art_col0  = idx.get("Продажи :: Артикул продавца") or 1
        name_col0 = idx.get("Продажи :: Наименование") or 2
    else:
        art_col0  = idx.get("Артикул продавца") or expect["Артикул продавца"]
        name_col0 = idx.get("Наименование") or expect["Наименование"]
    new_arts = {}
    for r in range(7, ws.max_row + 1):
        a = ws.cell(r, art_col0).value
        nm = ws.cell(r, name_col0).value
        if is_hold_row(a, nm) or is_empty_row(a, nm):
            continue
        # Канонизируем артикул ПЕРЕД сравнением со справочником и записью.
        a_s = canon_article(a)
        if a_s in ("", "-"):
            continue
        if a_s not in cat and a_s not in new_arts:
            new_arts[a_s] = (str(nm).strip() if nm is not None else None)
    if new_arts:
        with conn.cursor() as cur:
            for a_s, nm in new_arts.items():
                # Общие поля (наименование + категории) — в catalog_items.
                cur.execute(
                    "INSERT INTO catalog_items (seller_article, sample_name) "
                    "VALUES (%s,%s) ON CONFLICT (seller_article) DO NOTHING",
                    (a_s, nm))
        conn.commit()
        # перечитываем справочник, чтобы новые артикулы были учтены
        cat = load_catalog(conn)

    # Независимо от «новизны» артикула: гарантируем, что каждый артикул,
    # встречающийся в отчёте этого МП, имеет строку в catalog_marketplace
    # для данного маркетплейса (статус/менеджер = NULL — заполнит пользователь).
    # Без этого новый товар не появится в справочнике соответствующего МП.
    # Собираем все артикулы этого отчёта и upsert в catalog_marketplace.
    report_arts = set()
    for r in range(7, ws.max_row + 1):
        a = ws.cell(r, art_col0).value
        nm = ws.cell(r, name_col0).value
        if is_hold_row(a, nm) or is_empty_row(a, nm):
            continue
        # Канонический артикул — именно он пишется в catalog_marketplace.
        a_s = canon_article(a)
        if a_s in ("", "-"):
            continue
        report_arts.add(a_s)
    if report_arts:
        with conn.cursor() as cur:
            for a_s in report_arts:
                cur.execute(
                    "INSERT INTO catalog_marketplace (seller_article, marketplace) "
                    "VALUES (%s,%s) ON CONFLICT (seller_article, marketplace) DO NOTHING",
                    (a_s, mp_name))
        conn.commit()

    # Динамический порядок колонок INSERT: период-колонка впереди.
    insert_cols = [period_col] + BASE_COLS + ["upload_id"]

    # --- журнал: создаём запись загрузки заранее (получаем upload_id) ---
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO report_uploads (marketplace, week, month, year, period_text, "
            "period_start, period_end, source_file, status, period_kind) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (mp_name,
             period_val if period_kind == "weekly" else None,
             period_val if period_kind == "monthly" else None,
             year, period, start, end,
             os.path.basename(path), "В ПРОЦЕССЕ", period_kind))
        upload_id = cur.fetchone()[0]

    if ya:
        art_col  = idx.get("Продажи :: Артикул продавца") or 1
        name_col = idx.get("Продажи :: Наименование") or 2
    else:
        art_col  = idx.get("Артикул продавца") or expect["Артикул продавца"]
        name_col = idx.get("Наименование") or expect["Наименование"]
    # колонка идентификатора-резерва: штрихкод (WB) или SKU (Ozon).
    # Если у строки-товара нет ни артикула продавца, ни этого идентификатора —
    # такие «мусорные» строки пропускаем (они схлопываются в один и тот же
    # уникальный ключ и приводят к конфликту при вставке). У Яндекса
    # резервного id нет — идентификация только по артикулу продавца.
    id_field = "SKU" if oz else "Штрихкод"
    id_col   = None if ya else idx.get(id_field)
    rows = []
    skipped_no_id = 0  # пропущено строк без артикула и без штрихкода/SKU
    for r in range(7, ws.max_row + 1):  # данные с 7 строки
        art  = ws.cell(r, art_col).value
        name = ws.cell(r, name_col).value
        hold = is_hold_row(art, name)
        if not hold and is_empty_row(art, name):
            continue
        # Канонизируем артикул ДО записи в fact-таблицу и до подстановки
        # категорий/статуса из справочника (ключ cat[] тоже канонический).
        art_s = canon_article(art)
        if art_s == "-":
            art_s = ""
        row_type = "Общие удержания" if hold else "Товар"

        # пропускаем мусорные строки-товары: нет ни артикула, ни штрихкода/SKU.
        # Строки удержаний не трогаем — у них артикул/штрихкод законно пустые.
        if not hold and not art_s:
            id_val = ws.cell(r, id_col).value if id_col else None
            id_s = "" if id_val is None else str(id_val).strip()
            if id_s in ("", "-"):
                skipped_no_id += 1
                continue

        # категории/статус из справочника (для удержаний — пусто)
        if hold or not art_s:
            l1 = l2 = l3 = st = None
        else:
            l1, l2, l3, st = cat.get(art_s, (None, None, None, None))

        rec = {
            period_col: period_val, "year": year, "period_text": period,
            "period_start": start, "period_end": end,
            "marketplace": mp_name, "row_type": row_type,
            "category_l1": l1, "category_l2": l2, "category_l3": l3, "status": st,
            "upload_id": upload_id,
        }
        if ya:
            # === ЯНДЕКС: чтение по СОСТАВНОМУ ключу (YA_ID_MAP/YA_MAP) ===
            for db_col, key in YA_ID_MAP:
                if db_col == "seller_article":
                    rec[db_col] = art_s or None
                else:
                    c = idx.get(key)
                    v = ws.cell(r, c).value if c else None
                    rec[db_col] = (str(v).strip() if v is not None else None)
            for db_col, key in YA_MAP:
                c = idx.get(key)
                v = ws.cell(r, c).value if c else None
                if db_col in TEXT_COLS:
                    rec[db_col] = v
                else:
                    num = to_num(v)
                    # promo_total храним ОТРИЦАТЕЛЬНЫМ (как Ozon): в отчёте
                    # «Продвижение :: Итого» положительное, но фронтовая
                    # формула (-promo = расход) ждёт отрицательное, как у Ozon.
                    if db_col in YA_NEGATE and num:
                        num = -abs(num)
                    rec[db_col] = num
            # Колонки, которых нет в YA_MAP/YA_ID_MAP (напр. sku_ozon,
            # barcode_wb, fines, brand — у Яндекса их нет), заполняем None,
            # чтобы tuple(rec[col] ...) не упал по KeyError.
            for col in BASE_COLS:
                if col not in rec:
                    rec[col] = None
        else:
            # идентификаторы (Ozon/WB)
            for db_col, ozf, wbf in ID_MAP:
                f = ozf if oz else wbf
                if db_col == "seller_article":
                    rec[db_col] = art_s or None
                elif f is None:
                    rec[db_col] = None
                else:
                    c = idx.get(f)
                    v = ws.cell(r, c).value if c else None
                    rec[db_col] = (str(v).strip() if v is not None else None)
            # метрики (Ozon/WB)
            for db_col, ozf, wbf in METRIC_MAP:
                f = ozf if oz else wbf
                if f is None:
                    rec[db_col] = None
                    continue
                c = idx.get(f)
                v = ws.cell(r, c).value if c else None
                rec[db_col] = v if db_col in TEXT_COLS else to_num(v)

        rows.append(tuple(rec[col] for col in insert_cols))

    # --- Корректировка ОБЩИХ удержаний Ozon (универсальная) -------------------
    # Ozon частично РАЗНОСИТ «общие удержания» по товарным строкам (их holds_total
    # уже включает свою долю удержаний). Строка «Общие удержания» должна нести
    # ТОЛЬКО НЕРАСПРЕДЕЛЁННЫЙ ОСТАТОК, иначе разнесённая часть вычитается дважды и
    # маржа занижается (реальный случай: нед.25/2026 → 9,5% вместо 16,08%).
    # Остаток = «Все удержания OZON» из строки ИТОГО (стр.6) − Σ(holds_total
    # товарных строк). Эта формула работает на ЛЮБОЙ неделе независимо от того,
    # какую долю Ozon разнёс по товарам. Если остаток < 0 — обнуляем (вся сумма
    # уже разнесена). Срабатывает ТОЛЬКО на Ozon.
    holds_dup_warning = None
    if oz:
        ix_rt   = insert_cols.index("row_type")
        ix_hold = insert_cols.index("holds_total")
        ix_prof = insert_cols.index("profit")
        sum_holds_items = sum(
            (r[ix_hold] or 0) for r in rows if r[ix_rt] == "Товар")
        c_total_holds = idx.get("Все удержания OZON")
        total_holds_report = to_num(ws.cell(6, c_total_holds).value) if c_total_holds else 0
        total_holds_report = total_holds_report or 0
        remainder = round(total_holds_report - sum_holds_items, 2)
        if remainder < 0:
            remainder = 0.0
        new_rows = []
        for r in rows:
            if r[ix_rt] == "Общие удержания":
                old_h = r[ix_hold] or 0
                if abs(old_h - remainder) > 1:
                    holds_dup_warning = (
                        f"общие удержания Ozon скорректированы: было "
                        f"{old_h:,.0f} ₽ → нераспределённый остаток {remainder:,.0f} ₽ "
                        f"(всего по отчёту {total_holds_report:,.0f} ₽, из них "
                        f"{sum_holds_items:,.0f} ₽ уже разнесено по товарам)"
                        .replace(",", " "))
                lr = list(r)
                lr[ix_hold] = remainder
                lr[ix_prof] = -remainder
                r = tuple(lr)
            new_rows.append(r)
        rows = new_rows

    # --- Общие удержания ЯНДЕКСА (аналог Ozon) -----------------------------
    # У Яндекса основная часть удержаний разнесена по товарам (holds_total
    # товарных строк), но часть — общая (напр. общий логистический сбор
    # 165₽ в отчёте 03-09.08). Строка «Общие удержания» должна нести только
    # НЕРАСПРЕДЕЛЁННЫЙ ОСТАТОК = «Все удержания» из ИТОГО (стр.6) −
    # Σ(holds_total товарных). Но у Яндекса в файле чаще НЕТ отдельной
    # строки-удержания — поэтому если остаток > 0, а строки нет —
    # СОЗДАЁМ её. Логика profit = −remainder (как Ozon).
    if ya:
        ix_rt   = insert_cols.index("row_type")
        ix_hold = insert_cols.index("holds_total")
        ix_prof = insert_cols.index("profit")
        sum_holds_items = sum(
            (r[ix_hold] or 0) for r in rows if r[ix_rt] == "Товар")
        c_total_holds = idx.get("Все удержания ЯМ :: Все удержания")
        total_holds_report = to_num(ws.cell(6, c_total_holds).value) if c_total_holds else 0
        total_holds_report = total_holds_report or 0
        remainder = round(total_holds_report - sum_holds_items, 2)
        if remainder < 0:
            remainder = 0.0
        has_hold_row = any(r[ix_rt] == "Общие удержания" for r in rows)
        new_rows = []
        for r in rows:
            if r[ix_rt] == "Общие удержания":
                lr = list(r)
                lr[ix_hold] = remainder
                lr[ix_prof] = -remainder
                r = tuple(lr)
            new_rows.append(r)
        rows = new_rows
        # Строки «Общие удержания» в файле нет, но есть нераспределённый
        # остаток — создаём строку сами (все метрики None, кроме holds/profit).
        if not has_hold_row and remainder > 0:
            base = {
                period_col: period_val, "year": year, "period_text": period,
                "period_start": start, "period_end": end,
                "marketplace": mp_name, "row_type": "Общие удержания",
                "category_l1": None, "category_l2": None, "category_l3": None,
                "status": None, "upload_id": upload_id,
            }
            for col in BASE_COLS:
                base.setdefault(col, None)
            base["holds_total"] = remainder
            base["profit"] = -remainder
            rows.append(tuple(base[col] for col in insert_cols))

    # --- перезапись периода: удаляем прежние строки этого МП/периода/года ---
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {target_table} WHERE year=%s AND {period_col}=%s AND marketplace=%s",
                (year, period_val, mp_name))
            deleted = cur.rowcount
            # --- вставка ---
            # psycopg3 не имеет execute_values: собираем VALUES вручную и шлём
            # батчами, чтобы не упереться в лимит параметров PostgreSQL (65535).
            cols_sql = ",".join(insert_cols)
            row_tmpl = "(" + ",".join(["%s"] * len(insert_cols)) + ")"
            PAGE = 100
            for i in range(0, len(rows), PAGE):
                batch = rows[i:i + PAGE]
                values_sql = ",".join([row_tmpl] * len(batch))
                flat = [v for rr in batch for v in rr]
                cur.execute(
                    f"INSERT INTO {target_table} ({cols_sql}) VALUES {values_sql}",
                    flat)
            # --- финал журнала ---
            msg = f"Удалено старых: {deleted}; вставлено: {len(rows)}"
            if skipped_no_id:
                msg += f"; пропущено без артикула/штрихкода: {skipped_no_id}"
            if holds_dup_warning:
                msg += f"; ⚠ {holds_dup_warning}"
            cur.execute(
                "UPDATE report_uploads SET rows_loaded=%s, status='OK', "
                "message=%s WHERE id=%s",
                (len(rows), msg, upload_id))
        conn.commit()
    except Exception as exc:
        # Откатываем неудавшуюся транзакцию и формируем понятное сообщение.
        try:
            conn.rollback()
        except Exception:
            pass
        user_msg = _humanize_db_error(exc)
        # Отмечаем запись в журнале как ОШИБКУ (отдельной транзакцией).
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE report_uploads SET status='ОШИБКА', message=%s WHERE id=%s",
                    (user_msg[:500], upload_id))
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"{mp_name}: ОШИБКА вставки (upload_id={upload_id}): {exc}")
        return {
            "ok": False, "error": user_msg,
            "marketplace": mp_name, "period_text": period, "period_human": period_human,
            "period_kind": period_kind, "kind_label": kind_label,
            "month": pinfo["month"] if period_kind == "monthly" else None,
            "week": pinfo["week"] if period_kind == "weekly" else None,
            "year": year, "upload_id": upload_id, "skipped": skipped_no_id,
        }

    print(f"{mp_name}: {period_human} | {period} | "
          f"удалено {deleted}, вставлено {len(rows)} строк, "
          f"пропущено {skipped_no_id} (upload_id={upload_id})")
    warning = None
    if skipped_no_id:
        warning = (f"Пропущено строк без артикула и штрихкода: {skipped_no_id}. "
                   f"Такие строки невозможно однозначно идентифицировать, они не загружены.")
    return {
        "ok": True, "period_kind": period_kind, "kind_label": kind_label,
        "marketplace": mp_name, "period_text": period, "period_human": period_human,
        "month": pinfo["month"] if period_kind == "monthly" else None,
        "week": pinfo["week"] if period_kind == "weekly" else None,
        "year": year, "rows_loaded": len(rows), "deleted": deleted,
        "upload_id": upload_id, "skipped": skipped_no_id, "warning": warning,
    }

# ---------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Загрузка отчёта MPPROFIT в PostgreSQL")
    ap.add_argument("--mp", required=True, choices=["ozon", "wb", "yandex"], help="маркетплейс")
    ap.add_argument("--file", required=True, help="путь к Excel отчёту")
    ap.add_argument("--dsn", default=os.environ.get("DB_DSN"),
                    help="строка подключения (или переменная окружения DB_DSN)")
    args = ap.parse_args()
    if not args.dsn:
        print("Нужна строка подключения: --dsn или переменная DB_DSN"); sys.exit(2)
    conn = psycopg.connect(args.dsn)
    try:
        res = load_report(conn, args.file, args.mp)
        ok = res.get("ok") if isinstance(res, dict) else bool(res)
        sys.exit(0 if ok else 1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
