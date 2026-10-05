# -*- coding: utf-8 -*-
"""РНП продажи → Ozon: дерево дневных продаж, сгруппированных по месяцам.

Дерево: Итоги (root) → L1 → L2 → L3 → Товар. Источник — ozon_daily_sales
(дневные строки), справочник — catalog_items (категории/имя) + catalog_marketplace
(статус/менеджер по Ozon). JOIN защищён UPPER() (артикулы канонизируются при
записи, но UPPER на всякий случай — чтобы не плодить нераспределённые).

Колонки времени: дни группируются в МЕСЯЦЫ. Для каждого месяца — блок дней +
месячный итог + План продаж + Прогноз. Показываются только месяцы с данными.

Наборы метрик (см. ozon_daily_schema.md B2):
  • ГРУППА и Итоги — 8 метрик (orders_qty в главной строке + 7 в развороте... по
    факту разворот = 8 строк, orders_qty дополнительно показывается сверху).
  • ТОВАР — 21 метрика (главная orders_qty + 20 в развороте), включая
    вложенную подгруппу «Цены» и snapshot (рейтинг/остатки).

Агрегация день→месяц→группы:
  • SUM: orders_qty, orders_rub, cancels_qty, card_visits, ads_expense_rub, spp_rub
  • Среднее по значениям >0: cr_cart_pct, cr_order_pct, avg_position, ctr_pct,
    delivery_time_hours, price_index_pi, spp_pct
  • Snapshot (последнее значение на последнюю дату): rating, stock_ozon_qty,
    stock_ap_qty. Для группы остаток = SUM остатков товаров на последнюю дату;
    rating у групп не показывается.
  • Расчёт: cancel_pct = cancels/orders; drr = ads_expense/orders_rub.
"""
import calendar
import datetime
import threading

import json as _json

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from .. import db, auth
from .. import cache
from .. import daily_pi
from ..util import l1_sort_key, cat_sort_key
from . import rnp_router

router = APIRouter(prefix="/api/rnp_sales", tags=["rnp_sales"])

# Пространство имён кэша дерева РНП-продаж. bump() вызывается из upload_router
# и правок справочника — тогда все закэшированные деревья инвалидируются.
CACHE_NS = "rnp_sales_tree"
# Отдельное пространство имён кэша для Wildberries (источник — отдельная
# таблица wb_daily_sales; должно совпадать с upload_router.RNP_TREE_CACHE_NS_WB).
CACHE_NS_WB = "rnp_sales_tree_wb"
# Отдельное пространство имён кэша для Яндекс (источник — отдельная
# таблица ya_daily_sales; должно совпадать с upload_router.RNP_TREE_CACHE_NS_YA).
CACHE_NS_YA = "rnp_sales_tree_ya"
CACHE_TTL = 3600.0  # подстраховка: значение протухнет за час даже без bump()
_pi_cache_revision = None
_pi_cache_lock = threading.Lock()


def _price_revision():
    global _pi_cache_revision
    revision = str(daily_pi.revision())
    with _pi_cache_lock:
        if revision != _pi_cache_revision:
            cache.discard_namespace(CACHE_NS)
            cache.discard_namespace(CACHE_NS_WB)
            _pi_cache_revision = revision
    return revision

MP_MAP = {"ozon": "Ozon", "wildberries": "Wildberries", "wb": "Wildberries",
          "yandex": "Yandex", "ya": "Yandex"}
RU_MONTHS = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
             "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

# Текущая дата (для расчёта «прошедших дней» в прогнозе текущего месяца).
# Берём реальную дату по Москве (данные и планы — в московском времени).
# РАНЬШЕ здесь была захардкоженная дата — из-за неё делитель «прошедших дней»
# отставал от реальности и прогноз выполнения плана сильно ЗАВЫШАЛСЯ
# (напр. факт за 20 дней делился на 10 → проекция ×2).
_MSK_TZ = datetime.timezone(datetime.timedelta(hours=3))
def _today():
    return datetime.datetime.now(_MSK_TZ).date()

# --- Наборы метрик (ключ, подпись, тип, цвет HEX, жирный) ---
# kind: qty | rub | price | pct | num
GROUP_METRIC_DEFS = [
    ("orders_rub",          "Сумма заказов, руб",           "rub", "000000", False),
    ("drr_total_pct",       "ДРР, общая",                   "pct", "674EA7", False),
    ("ads_expense_rub",     "Расходы на рекламу",           "rub", "674EA7", False),
    ("cancel_pct",          "Процент отмен",                "pct", "990000", False),
    ("ctr_pct",             "CTR, с рк",                    "pct", "1155CC", False),
    ("cr_cart_pct",         "CR, корзина общий",            "pct", "000000", False),
    ("cr_order_pct",        "CR, в заказ общий",            "pct", "000000", False),
    # «Количество отзывов» (delivery_time_hours) убрано из групповых метрик —
    # показывается только на уровне товара (см. PRODUCT_METRIC_DEFS).
]

# Товар (20 метрик разворота). sub: 0=обычная, 1=заголовок подгруппы «Цены»,
# 2=вложенная метрика подгруппы «Цены».
PRODUCT_METRIC_DEFS = [
    ("orders_rub",          "Сумма заказов, руб",            "rub",   "000000", False, 0),
    ("avg_upload_price",    "Ср. загружаемая цена",          "rub",   "000000", True,  1),
    ("price_for_buyer",     "Цена для покупателя",           "rub",   "333333", False, 2),
    ("comp_price_avg",      "Средняя цена конкурентов",      "rub",   "999999", False, 2),
    ("comp_price_min",      "Минимальная цена конкурентов",  "rub",   "999999", False, 2),
    ("cancels_qty",         "Отменено заказов",              "qty",   "990000", False, 0),
    ("cancel_pct",          "Процент отмен",                 "pct",   "990000", False, 0),
    ("spp_pct",             "Соинвест (СПП)",                "pct",   "38761D", False, 0),
    ("price_index_pi",      "Индекс цены, Pi",               "pi",    "000000", True,  0),
    ("drr_total_pct",       "ДРР, общая",                    "pct",   "674EA7", False, 0),
    ("ads_expense_rub",     "Расходы на рекламу",            "rub",   "674EA7", False, 0),
    ("card_visits",         "Переходы в карточку",           "qty",   "000000", False, 0),
    ("ctr_pct",             "CTR, с рк",                     "pct",   "1155CC", False, 0),
    ("cr_cart_pct",         "CR, корзина общий",             "pct",   "000000", False, 0),
    ("cr_order_pct",        "CR, в заказ общий",             "pct",   "000000", False, 0),
    ("avg_position",        "Средняя позиция в поиске",      "num",   "000000", True,  0),
    ("delivery_time_hours", "Количество отзывов",             "num",   "7F6000", True,  0),
    ("rating",              "Рейтинг товара",                "num",   "38761D", True,  0),
    ("stock_ozon_qty",      "Остаток на складе ОЗОН, шт",    "qty",   "000000", True,  0),
    ("stock_ap_qty",        "Остаток на складе АВТОПРОФИ, шт", "qty",  "000000", False, 0),
]

# Колонки-суммы и колонки-средние (по значениям >0) в агрегате.
SUM_COLS = ["orders_qty", "orders_rub", "cancels_qty", "card_visits",
            "shows_qty", "ads_expense_rub", "spp_rub"]
AVG_COLS = ["cr_cart_pct", "cr_order_pct", "avg_position", "ctr_pct",
            "price_index_pi", "spp_pct",
            "comp_price_avg", "comp_price_min"]
# Snapshot — считаются отдельно (не сумма/среднее по дням, а значение «на дату»).
# delivery_time_hours переиспользован под «Количество отзывов» (накопительное
# число на карточке) → тоже snapshot, а не среднее по дням.
SNAP_COLS = ["rating", "delivery_time_hours", "stock_ozon_qty", "stock_ap_qty"]
# Две РАЗНЫЕ политики месячного snapshot:
#  • SNAP_LAST_NONEMPTY — последнее НЕПУСТОЕ значение (rating/отзывы грузятся
#    отдельным отчётом и на последней дате могут быть пустыми — тогда нельзя
#    показывать «—», нужно последнее известное значение карточки).
#  • SNAP_LAST_DAY — значение СТРОГО на последний загруженный день (остатки
#    складов: если на последний день выгрузки остатка нет, в итоге месяца
#    должен быть прочерк «—», а не «зависшее» последнее ненулевое значение).
SNAP_LAST_NONEMPTY = ["rating", "delivery_time_hours"]
SNAP_LAST_DAY = ["stock_ozon_qty", "stock_ap_qty"]


def _f(v):
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _div(a, b):
    return (a / b) if b else None


class Agg:
    """Аккумулятор метрик одной ячейки (узел × колонка)."""
    __slots__ = ("s", "sc", "av", "snap")

    def __init__(self):
        self.s = {c: 0.0 for c in SUM_COLS}     # суммы
        self.sc = {c: 0 for c in SUM_COLS}       # число ненулевых вкладов (для «тире»)
        self.av = {c: [0.0, 0] for c in AVG_COLS}  # [сумма, число>0]
        # snapshot для ДНЕВНОЙ ячейки = сумма значений этого дня (по товарам узла).
        self.snap = {c: [0.0, 0] for c in SNAP_COLS}  # [сумма, число ненулевых]

    def add(self, rec):
        for c in SUM_COLS:
            v = rec.get(c)
            if v is not None:
                self.s[c] += _f(v)
                self.sc[c] += 1
        for c in AVG_COLS:
            v = rec.get(c)
            if v is not None and _f(v) > 0:
                self.av[c][0] += _f(v)
                self.av[c][1] += 1
        for c in SNAP_COLS:
            v = rec.get(c)
            if v is not None:
                self.snap[c][0] += _f(v)
                self.snap[c][1] += 1


def _cell_from_agg(agg, snap_override=None):
    """Собрать словарь метрик ячейки из агрегата.

    snap_override — если задан (для МЕСЯЧНЫХ ячеек), snapshot берётся оттуда
    (последнее значение на последнюю дату), иначе из agg.snap (сумма дня).
    """
    out = {}
    for c in SUM_COLS:
        out[c] = (agg.s[c] if agg.sc[c] else None)
    for c in AVG_COLS:
        s, n = agg.av[c]
        out[c] = (s / n) if n else None
    if snap_override is not None:
        for c in SNAP_COLS:
            out[c] = snap_override.get(c)
    else:
        for c in SNAP_COLS:
            s, n = agg.snap[c]
            out[c] = (s if n else None)
    # Расчётные.
    orders = out.get("orders_qty")
    cancels = out.get("cancels_qty")
    orders_rub = out.get("orders_rub")
    ads = out.get("ads_expense_rub")
    out["cancel_pct"] = (cancels / orders) if (orders and orders > 0 and cancels is not None) else None
    out["drr_total_pct"] = (ads / orders_rub) if (ads is not None and orders_rub and orders_rub > 0) else None
    # Ср. загружаемая цена = Сумма заказов / Кол-во заказов (взвеш. по суммам на агрегатах).
    orders_qty = out.get("orders_qty")
    aup = (orders_rub / orders_qty) if (orders_rub is not None and orders_qty and orders_qty > 0) else None
    out["avg_upload_price"] = aup
    # Цена для покупателя = Ср. загружаемая цена × (1 − Соинвест(СПП)). spp_pct — доля.
    spp = out.get("spp_pct")
    out["price_for_buyer"] = (aup * (1.0 - spp)) if (aup is not None and spp is not None) else None
    return out


# Метрика «Показы, общие» (только Яндекс) — вставляется ПЕРЕД «Переходы
# в карточку». Формат как у card_visits (qty). Ключ shows_qty — SUM_COL.
_YA_SHOWS_PRODUCT = ("shows_qty", "Показы, общие", "qty", "000000", False, 0)


def _metric_payload(defs, product=False, is_wb=False, is_ya=False):
    # Метка склада маркетплейса (единый ключ stock_ozon_qty, разная
    # подпись): Ozon — «...ОЗОН...», WB — «...WB...», Яндекс — «...Яндекс...».
    # Яндекс также переименовывает CTR: «CTR, с рк» → «CTR, общий».
    stock_labels = {}
    ctr_label = None
    if is_wb:
        stock_labels = {"stock_ozon_qty": "Остаток на складе WB, шт"}
    elif is_ya:
        stock_labels = {"stock_ozon_qty": "Остаток на складе Яндекс, шт"}
        ctr_label = "CTR, общий"
    out = []
    for row in defs:
        if product:
            k, lbl, kind, color, bold, sub = row
            if k in stock_labels:
                lbl = stock_labels[k]
            if ctr_label and k == "ctr_pct":
                lbl = ctr_label
            # Яндекс: перед «Переходы в карточку» добавляем «Показы, общие».
            if is_ya and k == "card_visits":
                sk, sl, skind, scolor, sbold, ssub = _YA_SHOWS_PRODUCT
                out.append({"key": sk, "label": sl, "kind": skind,
                            "color": scolor, "bold": sbold, "sub": ssub})
            out.append({"key": k, "label": lbl, "kind": kind,
                        "color": color, "bold": bold, "sub": sub})
            if k == "price_index_pi" and not is_ya:
                out[-1]["sub"] = 3
                out.extend([
                    {"key": "base_price_pi", "label": "Базовая цена, Pi",
                     "kind": "pi", "color": "333333", "bold": False, "sub": 4},
                    {"key": "buyer_price_pi", "label": "Цена покупателя Ozon / WB, Pi",
                     "kind": "pi", "color": "333333", "bold": False, "sub": 4},
                ])
        else:
            k, lbl, kind, color, bold = row
            if k in stock_labels:
                lbl = stock_labels[k]
            if ctr_label and k == "ctr_pct":
                lbl = ctr_label
            out.append({"key": k, "label": lbl, "kind": kind,
                        "color": color, "bold": bold})
    return out


@router.get("/tree")
def rnp_sales_tree(
    marketplace: str = Query("ozon"),
    date_from: str = Query(None),
    date_to: str = Query(None),
    status: str = Query(None),
    manager: str = Query(None),
    user=Depends(auth.get_current_user),
):
    """Дерево дневных продаж Ozon по месяцам (с кэшем ответа).

    Сборка дерева тяжёлая (~8 с, ~13 МБ JSON). Результат зависит только от
    маркетплейса, периода и фильтров status/manager — все они в ключе кэша.
    При загрузке новых данных кэш инвалидируется через cache.bump(CACHE_NS)
    (см. upload_router), поэтому устаревшие цифры не отдаются.
    """
    # Ключ кэша строим по НОРМАЛИЗОВАННОМУ mp (Ozon|Wildberries), А НЕ по сырому
    # алиасу: иначе "wb" и "wildberries" — два разных ключа на одно и то же
    # дерево, и один может отдавать устаревший результат (фронт шлёт "wb").
    mp_norm = MP_MAP.get((marketplace or "").lower(), "Ozon")
    # v2 в ключе — версия ПРАВИЛ ПОСТРОЕНИЯ дерева (порядок групп изменён
    # 2026-09-11). Инкремент версии обесценивает старые записи кэша, иначе
    # после деплоя отдавался бы прежний порядок до истечения TTL.
    pi_revision = _price_revision() if mp_norm != "Yandex" else ""
    ck = "rnps_tree|v6|" + "|".join([
        mp_norm,
        _today().isoformat(),
        pi_revision,
        date_from or "", date_to or "",
        status or "", manager or "",
    ])
    # Для WB — своё пространство имён кэша (данные в отдельной таблице,
    # инвалидация через CACHE_NS_WB).
    ns = (CACHE_NS_WB if mp_norm == "Wildberries"
          else CACHE_NS_YA if mp_norm == "Yandex"
          else CACHE_NS)
    # В кэше храним УЖЕ СЕРИАЛИЗОВАННЫЕ JSON-байты, а не dict.
    # Причина: дерево ~9.5 МБ, и повторная сериализация dict→JSON
    # в FastAPI (jsonable_encoder + json.dumps) стоит ~2 с НА КАЖДЫЙ ответ —
    # даже когда данные взяты из кэша. Отдавая готовые байты через Response,
    # минуем эту сериализацию и HIT становится почти мгновенным.
    cached = cache.get(ns, ck)
    if cached is not None:
        return Response(content=cached, media_type="application/json")
    result = _build_rnp_sales_tree(marketplace, date_from, date_to, status, manager)
    payload = _json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")
    cache.set(ns, ck, payload, ttl=CACHE_TTL)
    return Response(content=payload, media_type="application/json")


def _build_rnp_sales_tree(marketplace, date_from, date_to, status, manager):
    """Чистая сборка дерева (без кэша и без Depends). Логика без изменений."""
    mp = MP_MAP.get((marketplace or "").lower(), "Ozon")
    today = _today()

    # Диапазон дат (необязательный).
    def _pd(s):
        try:
            return datetime.date.fromisoformat(s.strip()) if s else None
        except (ValueError, AttributeError):
            return None
    d_from = _pd(date_from)
    d_to = _pd(date_to)

    where = ["1=1"]
    params = []
    if d_from:
        where.append("ods.date >= %s"); params.append(d_from)
    if d_to:
        where.append("ods.date <= %s"); params.append(d_to)
    where_sql = " AND ".join(where)

    is_wb = mp == "Wildberries"
    is_ya = mp == "Yandex"

    # Источник данных зависит от МП: Ozon → ozon_daily_sales, WB → wb_daily_sales.
    # Строки приводятся к ЕДИНОМУ набору ключей (как у Ozon): вся дальнейшая
    # логика дерева/агрегации работает по именам колонок, не по таблице.
    # У WB нет части метрик Ozon (spp_rub, sku_ozon) — отдаём NULL,
    # в отчёте будет «—». Соинвест (СПП) теперь ЕСТЬ у WB
    # (wb_daily_sales.spp_pct — грузится из отчёта «Рейтинг и отзывы» ЛК WB,
    # колонка «ПроцентСПП»; хранится как доля 0..1). Ср. позиция в поиске теперь ЕСТЬ
    # (wb_daily_sales.avg_search_position → общий ключ avg_position).
    # Индекс цены Pi теперь ЕСТЬ (wb_daily_sales.price_index_pi — грузится из
    # отчёта «Индекс цен» ЛК WB, два файла в день; Pi=ЦенаWB/ЦенаИдент).
    # Показывается ТОЛЬКО на уровне товара (в GROUP_METRIC_DEFS его нет).
    # WB stock → stock_ozon_qty (общий ключ «остаток МП»); reviews_qty →
    # delivery_time_hours (общий ключ «Количество отзывов»).
    if is_ya:
        # Яндекс: источник ya_daily_sales. Метрики с данными: показы
        # (shows_qty — отдельный ключ), переходы (card_visits), заказы/сумма,
        # отмены, CR корзина/заказ, CTR (счётный). Остальные метрики
        # (СПП, Pi, ДРР, реклама, ср.позиция, отзывы, рейтинг, остаток Яндекс)
        # — ЗАГОТОВКИ: отдаём текущее значение колонки (пока NULL → фронт
        # покажет «—»). Остаток Яндекс → общий ключ stock_ozon_qty («остаток
        # МП», метка меняется в _metric_payload). Кол-во отзывов →
        # delivery_time_hours (общий ключ «Количество отзывов»).
        rows = db.query_all(
            f"""
            SELECT ods.date, ods.seller_article,
                   ods.ya_sku AS sku_ozon, ods.item_name,
                   ci.category_l1, ci.category_l2, ci.category_l3,
                   cm.status, cm.manager, cm.product_url,
                   ods.orders_qty, ods.orders_rub, ods.cancels_qty, ods.card_visits,
                   ods.shows_qty,
                   ods.ads_expense_rub,
                   ods.spp_rub, ods.spp_pct,
                   ods.cr_cart_pct, ods.cr_order_pct,
                   ods.avg_position, ods.ctr_pct,
                   ods.reviews_qty AS delivery_time_hours,
                   ods.price_index_pi,
                   NULL::numeric AS avg_upload_price, NULL::numeric AS price_for_buyer,
                   NULL::numeric AS comp_price_avg, NULL::numeric AS comp_price_min,
                   ods.rating, ods.stock_ya_qty AS stock_ozon_qty,
                   sd.qty AS stock_ap_qty
            FROM ya_daily_sales ods
            LEFT JOIN catalog_items ci
                   ON UPPER(ci.seller_article) = UPPER(ods.seller_article)
            LEFT JOIN catalog_marketplace cm
                   ON UPPER(cm.seller_article) = UPPER(ods.seller_article)
                  AND cm.marketplace = %s
            LEFT JOIN stock_daily sd
                   ON sd.date = ods.date
                  AND UPPER(sd.seller_article) = UPPER(ods.seller_article)
            WHERE {where_sql}
            ORDER BY ods.date, ods.seller_article
            """,
            tuple([mp] + params),
        )
    elif is_wb:
        rows = db.query_all(
            f"""
            SELECT ods.date, ods.seller_article,
                   NULL::text AS sku_ozon, ods.item_name,
                   ci.category_l1, ci.category_l2, ci.category_l3,
                   cm.status, cm.manager, cm.product_url,
                   ods.orders_qty, ods.orders_rub, ods.cancels_qty, ods.card_visits,
                   ods.ads_expense_rub,
                   NULL::numeric AS spp_rub, ods.spp_pct,
                   ods.cr_cart_pct, ods.cr_order_pct,
                   ods.avg_search_position AS avg_position, ods.ctr_pct,
                   ods.reviews_qty AS delivery_time_hours,
                   ods.price_index_pi,
                   NULL::numeric AS avg_upload_price, NULL::numeric AS price_for_buyer,
                   ods.comp_price_avg, ods.comp_price_min,
                   ods.rating, ods.stock_wb_qty AS stock_ozon_qty,
                   sd.qty AS stock_ap_qty
            FROM wb_daily_sales ods
            LEFT JOIN catalog_items ci
                   ON UPPER(ci.seller_article) = UPPER(ods.seller_article)
            LEFT JOIN catalog_marketplace cm
                   ON UPPER(cm.seller_article) = UPPER(ods.seller_article)
                  AND cm.marketplace = %s
            LEFT JOIN stock_daily sd
                   ON sd.date = ods.date
                  AND UPPER(sd.seller_article) = UPPER(ods.seller_article)
            WHERE {where_sql}
            ORDER BY ods.date, ods.seller_article
            """,
            tuple([mp] + params),
        )
    else:
        # Дневные строки + справочник. JOIN защищён UPPER() (артикулы канонические,
        # но так надёжнее — не плодим нераспределённые из-за регистра).
        rows = db.query_all(
            f"""
            SELECT ods.date, ods.seller_article, ods.sku_ozon, ods.item_name,
                   ci.category_l1, ci.category_l2, ci.category_l3,
                   cm.status, cm.manager, cm.product_url,
                   ods.orders_qty, ods.orders_rub, ods.cancels_qty, ods.card_visits,
                   ods.ads_expense_rub, ods.spp_rub, ods.spp_pct,
                   ods.cr_cart_pct, ods.cr_order_pct, ods.avg_position, ods.ctr_pct,
                   ods.delivery_time_hours, ods.price_index_pi,
                   ods.avg_upload_price, ods.price_for_buyer,
                   ods.comp_price_avg, ods.comp_price_min,
                   ods.rating, ods.stock_ozon_qty, ods.stock_ap_qty
            FROM ozon_daily_sales ods
            LEFT JOIN catalog_items ci
                   ON UPPER(ci.seller_article) = UPPER(ods.seller_article)
            LEFT JOIN catalog_marketplace cm
                   ON UPPER(cm.seller_article) = UPPER(ods.seller_article)
                  AND cm.marketplace = %s
            WHERE {where_sql}
            ORDER BY ods.date, ods.seller_article
            """,
            tuple([mp] + params),
        )

    if not is_ya:
        # Общий склад 1С доступен независимо от файла заказов.
        # Состав товаров задают ТОЛЬКО дневные строки РНП выбранного периода.
        # Склад дополняет даты этих товаров, но не расширяет состав отчёта.
        # Только точная дата; никаких переносов вчерашних значений
        # и искусственных нулей по заказам.
        eligible_articles = {r["seller_article"].upper() for r in rows}
        stock_where, stock_params = [], [mp]
        if d_from:
            stock_where.append("sd.date >= %s")
            stock_params.append(d_from)
        if d_to:
            stock_where.append("sd.date <= %s")
            stock_params.append(d_to)
        stock_rows = db.query_all(
            """SELECT sd.date, ci.seller_article, ci.sample_name AS item_name,
                      ci.category_l1, ci.category_l2, ci.category_l3,
                      cm.status, cm.manager, cm.product_url, sd.qty AS stock_ap_qty
               FROM stock_daily sd
               JOIN catalog_items ci ON upper(ci.seller_article)=upper(sd.seller_article)
               JOIN catalog_marketplace cm
                 ON upper(cm.seller_article)=upper(ci.seller_article)
                AND cm.marketplace=%s
               WHERE """ + (" AND ".join(stock_where) or "TRUE"),
            tuple(stock_params))
        by_key = {(r["date"], r["seller_article"].upper()): r for r in rows}
        for stock in stock_rows:
            key = (stock["date"], stock["seller_article"].upper())
            if key[1] not in eligible_articles:
                continue
            if key in by_key:
                by_key[key]["stock_ap_qty"] = stock["stock_ap_qty"]
            else:
                rows.append(stock)
                by_key[key] = stock

    # Дни заказов отдельно от дней с отзывами/ценами/остатками.
    order_dates = {r["date"] for r in rows if r.get("orders_qty") is not None}
    # Месяцы и дни, реально присутствующие в данных.
    months = {}  # month_key -> {year, month, days:set}
    for r in rows:
        d = r["date"]
        mk = f"{d.year}-{d.month:02d}"
        m = months.setdefault(mk, {"year": d.year, "month": d.month, "days": set()})
        m["days"].add(d)

    # План продаж (помесячно) по Ozon: (art, year, month) -> plan_qty.
    plan_rows = db.query_all(
        """SELECT seller_article, year, month, SUM(plan_qty) AS q
           FROM sales_plan WHERE marketplace = %s
           GROUP BY seller_article, year, month""",
        (mp,),
    )
    plan_by_art = {}  # art_upper -> {(y,m): qty}
    for r in plan_rows:
        a = (r.get("seller_article") or "").upper()
        y = int(r["year"] or 0); mm = int(r["month"] or 0)
        if a and 1 <= mm <= 12:
            plan_by_art.setdefault(a, {})[(y, mm)] = _f(r.get("q"))

    # Фильтр по статусу/менеджеру (на уровне артикула).
    def passes(st, mgr):
        if status and (st or "").strip() != status:
            return False
        if manager and (mgr or "") != manager:
            return False
        return True

    # --- snapshot по (art, month_key) — ДВЕ разные политики ---
    #  • SNAP_LAST_NONEMPTY (rating/отзывы): берём значение на самую позднюю дату,
    #    НА КОТОРОЙ ЭТО ПОЛЕ ЗАПОЛНЕНО (они грузятся отдельным отчётом и
    #    на последней дате могут быть пусты — тогда берём последнее известное).
    #  • SNAP_LAST_DAY (остатки складов): берём значение СТРОГО на ПОСЛЕДНИЙ
    #    ЗАГРУЖЕННЫЙ ДЕНЬ артикула в месяце (макс. дата строки). Если на этот
    #    день остаток NULL → в итоге месяца прочерк (None), а не «зависшее»
    #    последнее ненулевое значение (Ozon/WB после инцидента/вывода из продаж).
    art_month_snap = {}   # (art_up, mk) -> {col -> value}
    art_month_snap_dt = {}  # (art_up, mk) -> {col -> date} — только для LAST_NONEMPTY
    art_month_lastday = {}  # (art_up, mk) -> max(date) строки артикула в месяце
    for r in rows:
        d = r["date"]
        mk = f"{d.year}-{d.month:02d}"
        a = (r.get("seller_article") or "").upper()
        key = (a, mk)
        snap = art_month_snap.get(key)
        if snap is None:
            snap = {c: None for c in SNAP_COLS}
            art_month_snap[key] = snap
            art_month_snap_dt[key] = {c: None for c in SNAP_LAST_NONEMPTY}
            art_month_lastday[key] = d
        if d > art_month_lastday[key]:
            art_month_lastday[key] = d
        dts = art_month_snap_dt[key]
        # Рейтинг/отзывы — последнее непустое.
        for c in SNAP_LAST_NONEMPTY:
            v = r.get(c)
            if v is None:
                continue
            if dts[c] is None or d >= dts[c]:
                snap[c] = v
                dts[c] = d
    # Остатки — вторым проходом: значение СТРОГО на последний день артикула
    # в месяце (включая NULL → остаётся None → прочерк).
    for r in rows:
        d = r["date"]
        mk = f"{d.year}-{d.month:02d}"
        a = (r.get("seller_article") or "").upper()
        key = (a, mk)
        if d != art_month_lastday.get(key):
            continue
        snap = art_month_snap[key]
        for c in SNAP_LAST_DAY:
            snap[c] = r.get(c)

    # --- построение дерева ---
    def new_node(key, name, level):
        return {"key": key, "name": name, "level": level,
                "day": {}, "month": {},   # col_key -> Agg
                "arts": set(),            # артикулы узла (для snapshot/плана)
                "children": {}, "leaf_info": None,
                "_name_len": 0, "_sku": None}

    root = new_node("__root__", "Итоги", 0)
    undistributed = {}  # art -> item_name

    def acc(node, r, day_key, mk):
        node["arts"].add((r.get("seller_article") or "").upper())
        a = node["day"].get(day_key)
        if a is None:
            a = Agg(); node["day"][day_key] = a
        a.add(r)
        am = node["month"].get(mk)
        if am is None:
            am = Agg(); node["month"][mk] = am
        am.add(r)

    # Предпроход: артикулы, у которых за ВЫБРАННЫЙ ПЕРИОД (фильтр дат уже применён
    # в SELECT) есть «активность» = заказы (шт или руб), расходы на рекламу или
    # остаток на складе OZON. Нужно, чтобы скрывать товары со статусом «-»
    # (выведен из продаж) без активности, но оставлять их, если активность была.
    # stock_ozon_qty — snapshot (значение на дату): достаточно >0 в любой строке.
    active = set()
    for r in rows:
        art0 = r.get("seller_article") or ""
        if not art0 or art0 in active:
            continue
        if (_f(r.get("orders_qty")) or _f(r.get("orders_rub"))
                or _f(r.get("ads_expense_rub")) or _f(r.get("stock_ozon_qty"))):
            active.add(art0)

    managers = set()
    statuses = set()
    for r in rows:
        art = r.get("seller_article") or ""
        st, mgr = r.get("status"), r.get("manager")
        if mgr:
            managers.add(mgr)
        if st:
            statuses.add(st)
        if not passes(st, mgr):
            continue

        # Статус «-» (выведен из продаж): скрываем товар из отчёта целиком, если
        # за выбранный период у него НЕ было активности (заказы/реклама/остаток
        # OZON). Исключение — если пользователь ЯВНО отфильтровал по статусу «-»
        # (status задан): тогда показываем такие товары всегда. «?» и прочие
        # статусы по этому правилу не скрываются.
        st_norm = (st or "").strip()
        if st_norm == "-" and not status and art and art not in active:
            continue
        # WB: пустой статус не делает старый товар активным. Одни отзывы,
        # цены или общий склад без заказов/рекламы/остатка WB не являются
        # основанием включать его в РНП и список требующих распределения.
        # Данные остаются в источнике; любая активность за выбранный период
        # возвращает товар в отчёт с обычной проверкой распределения.
        if is_wb and not st_norm and art and art not in active:
            continue
        l1, l2, l3 = r.get("category_l1"), r.get("category_l2"), r.get("category_l3")
        d = r["date"]
        day_key = d.isoformat()
        mk = f"{d.year}-{d.month:02d}"

        missing_status = not st_norm if is_wb else not st
        is_undist = (not l1 or not l2 or not l3 or missing_status or not mgr)
        if is_undist and art and art not in undistributed:
            undistributed[art] = r.get("item_name")

        # Итоги включают все товары (в т.ч. нераспределённые).
        acc(root, r, day_key, mk)
        if is_undist:
            continue

        k1 = l1
        n1 = root["children"].get(k1)
        if not n1:
            n1 = new_node(k1, l1, 1); root["children"][k1] = n1
        acc(n1, r, day_key, mk)
        k2 = f"{l1}|{l2}"
        n2 = n1["children"].get(k2)
        if not n2:
            n2 = new_node(k2, l2, 2); n1["children"][k2] = n2
        acc(n2, r, day_key, mk)
        k3 = f"{l1}|{l2}|{l3}"
        n3 = n2["children"].get(k3)
        if not n3:
            n3 = new_node(k3, l3, 3); n2["children"][k3] = n3
        acc(n3, r, day_key, mk)
        k4 = f"{l1}|{l2}|{l3}|{art}"
        n4 = n3["children"].get(k4)
        if not n4:
            n4 = new_node(k4, art, 4)
            n4["leaf_info"] = {"seller_article": art, "sku_ozon": r.get("sku_ozon"),
                               "status": st, "manager": mgr,
                               "product_url": r.get("product_url")}
            n3["children"][k4] = n4
        acc(n4, r, day_key, mk)
        if not n4["leaf_info"].get("sku_ozon") and r.get("sku_ozon"):
            n4["leaf_info"]["sku_ozon"] = r.get("sku_ozon")

    # Список месяцев (по возрастанию), с днями.
    month_list = []
    for mk in sorted(months.keys()):
        info = months[mk]
        days_sorted = sorted(info["days"])
        month_list.append({
            "key": mk, "year": info["year"], "month": info["month"],
            "label": f"{RU_MONTHS[info['month']]} {info['year']}",
            "days": [{"key": d.isoformat(), "label": f"{d.day:02d}"} for d in days_sorted],
        })

    def month_snapshot(node, mk):
        """Snapshot месяца для узла: сумма последних значений по товарам узла."""
        out = {c: None for c in SNAP_COLS}
        acc_snap = {c: [0.0, 0] for c in SNAP_COLS}
        for a in node["arts"]:
            s = art_month_snap.get((a, mk))
            if not s:
                continue
            for c in SNAP_COLS:
                v = s.get(c)
                if v is not None:
                    acc_snap[c][0] += _f(v)
                    acc_snap[c][1] += 1
        for c in SNAP_COLS:
            tot, n = acc_snap[c]
            out[c] = (tot if n else None)
        return out

    def node_turnover(node, mk, snap):
        """Оборачиваемость узла за месяц mk в ДНЯХ ЗАПАСА.

        turnover_days = Остаток Ozon (snapshot, посл. непустое значение)
                        / (Σ заказов за ПОСЛЕДНИЕ 30 ДНЕЙ
                           / число дней с данными в этом 30-дневном окне).

        • Остаток берём Ozon (stock_ozon_qty) из month_snapshot.
        • Среднедневные заказы считаем НЕ за календарный месяц mk, а за
          СКОЛЬЗЯЩЕЕ ОКНО последних 30 дней (turn_window_days) относительно
          последней даты с данными (turn_anchor). Это убирает искажение в
          начале месяца: когда текущий месяц только начался, за пару его дней
          среднедневное было бы занижено (→ огромный запас в днях). Окно 30
          дней захватывает хвост предыдущего месяца и даёт устойчивую оценку.
        • Σ orders_qty — по дневным агрегатам узла (node["day"]), попавшим в окно.
        • Знаменатель — число дней в окне, где есть данные ГЛОБАЛЬНО
          (turn_window_days_n), как и раньше делили на дни с данными, а не на
          календарные дни.
        • Заказов нет, но остаток > 0 → "inf" (∞ — товар не расходуется).
          Строкой, а не float('inf'): бесконечность невалидна в стандартном JSON.
        • Остатка нет (0/None) → 0 дней.
        • Нет данных вовсе → None (не показываем).
        """
        stock = snap.get("stock_ozon_qty") if snap else None
        # Σ заказов узла за скользящее окно 30 дней (turn_window — set дат-isoformat).
        orders_sum = 0.0
        for dk in turn_window:
            a = node["day"].get(dk)
            if a is not None:
                orders_sum += _f(a.s["orders_qty"])
        days_n = turn_window_days_n
        if stock is None:
            return None
        stock = _f(stock)
        if stock <= 0:
            return 0.0
        if orders_sum <= 0 or days_n <= 0:
            return "inf"   # остаток есть, продаж нет → бесконечный запас
        avg_daily = orders_sum / days_n
        return stock / avg_daily

    def node_plan(node, mk):
        info = months[mk]
        y, m = info["year"], info["month"]
        total = 0.0
        has = False
        for a in node["arts"]:
            q = plan_by_art.get(a, {}).get((y, m))
            if q:
                total += q; has = True
        return (total if has else None)

    def node_forecast(node, mk, plan_val):
        if not plan_val:
            return None
        info = months[mk]
        y, m = info["year"], info["month"]
        am = node["month"].get(mk)
        fact = _f(am.s["orders_qty"]) if am else 0.0
        dim = calendar.monthrange(y, m)[1]
        # Прошедшие дни (база экстраполяции):
        #  • ЗАКРЫТЫЙ месяц (раньше текущего) → passed = dim → projected = fact,
        #    т.е. столбец показывает ФАКТИЧЕСКОЕ выполнение плана (факт/план), не прогноз.
        #  • ТЕКУЩИЙ месяц → passed = ЧИСЛО ДНЕЙ МЕСЯЦА С ФАКТИЧЕСКИМИ ДАННЫМИ
        #    (не календарное число TODAY.day!). Данные МП приходят с задержкой,
        #    поэтому делим факт ровно на те дни, за которые он есть — иначе прогноз
        #    искажается (пустые хвостовые дни занижали бы, а отставшая дата — завышала).
        #  • БУДУЩИЙ месяц → 0 → прогноза нет.
        if (y, m) < (today.year, today.month):
            passed = dim
        elif (y, m) == (today.year, today.month):
            passed = len(info["days"] if is_ya else info["days"] & order_dates)
        else:
            passed = 0
        if passed <= 0:
            return None
        if passed >= dim:
            projected = fact
        else:
            projected = fact / passed * dim
        return _div(projected, plan_val)

    # Ключ ПОСЛЕДНЕГО месяца в данных (оборачиваемость считаем только для него:
    # остаток — актуальный snapshot, для прошлых месяцев исторический остаток недоступен).
    last_mk = sorted(months.keys())[-1] if months else None

    # СКОЛЬЗЯЩЕЕ ОКНО последних 30 дней для среднедневных заказов оборачиваемости.
    # Окно = [anchor-29; anchor], где anchor — последняя дата с данными (НЕ
    # больше сегодня по МСК). Данные МП приходят с задержкой, поэтому
    # anchor по последней дате данных, а не по календарному сегодня (иначе пустой
    # хвост занижал бы среднее). turn_window — set дат-isoformat с данными в окне;
    # turn_window_days_n — число таких дней (знаменатель среднедневного).
    TURN_WINDOW_DAYS = 30
    _all_data_dates = set()
    for _m in months.values():
        _all_data_dates |= _m["days"] if is_ya else _m["days"] & order_dates
    turn_window = set()
    turn_window_days_n = 0
    if _all_data_dates:
        _anchor = max(_all_data_dates)
        if _anchor > today:
            _anchor = today
        _win_start = _anchor - datetime.timedelta(days=TURN_WINDOW_DAYS - 1)
        _win_dates = {d for d in _all_data_dates if _win_start <= d <= _anchor}
        turn_window = {d.isoformat() for d in _win_dates}
        turn_window_days_n = len(_win_dates)

    pi_by_day, pi_month_dates = {}, {}
    if not is_ya and months:
        pi_month_dates = {
            mk: daily_pi.month_end(info["year"], info["month"], info["days"], d_to)
            for mk, info in months.items()
        }
        all_dates = set().union(*(info["days"] for info in months.values()))
        all_dates.update(pi_month_dates.values())
        articles = {r["seller_article"] for r in rows if r.get("seller_article")}
        pi_by_day = daily_pi.load(articles, min(all_dates), max(all_dates), mp)

    def finalize(node):
        cells = {}
        # дневные ячейки
        for dk, agg in node["day"].items():
            cells[dk] = _cell_from_agg(agg)
        # месячные ячейки (snapshot — последнее значение)
        plan = {}
        forecast = {}
        turnover = {}   # mk -> дни запаса | "inf" | 0 (только last_mk)
        for mk in months:
            am = node["month"].get(mk)
            snap = month_snapshot(node, mk)
            cells[mk] = _cell_from_agg(am if am else Agg(), snap_override=snap)
            pv = node_plan(node, mk)
            plan[mk] = pv
            forecast[mk] = node_forecast(node, mk, pv)
            if mk == last_mk:
                turnover[mk] = node_turnover(node, mk, snap)
        if node["level"] == 4 and not is_ya:
            article = node["leaf_info"]["seller_article"]
            # Price-only dates still appear in an existing RNP day column.
            # Never forward-fill and never aggregate ratios across days.
            for info in months.values():
                for day in info["days"]:
                    dk = day.isoformat()
                    cells.setdefault(dk, {}).update(
                        pi_by_day.get((article, dk), daily_pi.EMPTY))
            for mk, end in pi_month_dates.items():
                cells[mk].update(pi_by_day.get((article, end.isoformat()), daily_pi.EMPTY))
        children = [finalize(c) for c in node["children"].values()]
        if children:
            # Единые правила порядка (app/util.py): L1 — фиксированный
            # список (ECOM, затем ТД «АВТОПРОФИ»), L2/L3 — алфавит,
            # L4 — по артикулу. Метрика в сортировке не участвует.
            if all(c["level"] == 4 for c in children):
                children.sort(key=lambda c: (c["leaf_info"] or {}).get("seller_article") or "")
            elif node["level"] == 0:
                children.sort(key=lambda c: l1_sort_key(c["name"]))
            else:
                children.sort(key=lambda c: cat_sort_key(c["name"]))
        return {
            "key": node["key"], "name": node["name"], "level": node["level"],
            "cells": cells, "plan": plan, "forecast": forecast,
            "turnover": turnover,
            "leaf_info": node["leaf_info"], "children": children,
        }

    # Множество артикулов с ОТРИЦАТЕЛЬНОЙ маржинальностью за последнюю
    # неделю (те же цифры, что в popup юнит-экономики). Используется
    # во фронте для красной окраски иконки-монеток. Отказоустойчиво:
    # при любой ошибке — пустое множество (иконки останутся как сейчас).
    try:
        neg_margin_arts = rnp_router.negative_margin_articles(mp)
    except Exception:
        neg_margin_arts = set()

    def _mark_neg(node):
        li = node.get("leaf_info")
        if li:
            art = (li.get("seller_article") or "").strip().upper()
            li["ue_neg"] = bool(art) and art in neg_margin_arts
        for ch in node.get("children", []):
            _mark_neg(ch)

    tree = finalize(root)
    _mark_neg(tree)

    undist_list = [{"seller_article": a, "item_name": n} for a, n in sorted(undistributed.items())]

    # Пороги светофора оборачиваемости из anomaly_rules (настраиваются в шестерёнке).
    # red — недостаточно (< threshold), blue — излишек (> threshold2), между — зелёный.
    turn_red, turn_blue, turn_on = 20.0, 60.0, True
    try:
        tr = db.query_one(
            "SELECT threshold, threshold2, enabled FROM anomaly_rules "
            "WHERE rule_key='turnover_days'")
        if tr:
            if tr.get("threshold") is not None:
                turn_red = float(tr["threshold"])
            if tr.get("threshold2") is not None:
                turn_blue = float(tr["threshold2"])
            turn_on = bool(tr.get("enabled"))
    except Exception:
        pass

    return {
        "marketplace": mp,
        "months": month_list,
        "last_month": last_mk,
        "group_metrics": _metric_payload(GROUP_METRIC_DEFS, is_wb=is_wb, is_ya=is_ya),
        "product_metrics": _metric_payload(PRODUCT_METRIC_DEFS, product=True, is_wb=is_wb, is_ya=is_ya),
        "top_metric": {"key": "orders_qty", "label": "Заказы, шт", "kind": "qty"},
        "turnover_cfg": {"red": turn_red, "blue": turn_blue, "enabled": turn_on},
        "tree": tree,
        "undistributed": {"count": len(undist_list), "articles": undist_list},
        "managers": sorted(managers),
        "statuses": sorted(statuses),
    }
