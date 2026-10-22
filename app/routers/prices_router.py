# -*- coding: utf-8 -*-
"""Раздел «Цены» → подраздел «Прайс-лист».

Модель:
  • Таблица catalog_base_prices (seller_article, marketplace, base_price INT, ...).
  • Универсум товаров = SKU, активные в выбранном ПЕРИОДЕ (as in «РНП заказы»):
      – есть хоть одна строка в fact_weekly для Ozon/Wildberries, чья неделя
        (period_start..period_end) пересекается с [date_from..date_to];
      – по этому пересечению есть движение (revenue/sales_qty/orders_qty/
        returns_qty/profit/holds_total > 0).
      – правило статуса «−» из РНП: если catalog_marketplace.status = '-' и в
        периоде движений нет — SKU в списке не показываем (даже если он есть
        в catalog_marketplace); «?» и прочие статусы не скрываем.
  • Иерархия L1/L2/L3 — общая, из catalog_items. Товары без L1 показываются
    в специальной секции «Не распределены по группам».
  • Цены (base_price) — берутся из catalog_base_prices с фильтром
    «updated_at <= date_to» (актуальность на конец периода). Если цена была
    заведена/изменена ПОСЛЕ конца периода — на конец периода её ещё не было,
    отдаём NULL (фронт покажет «—»).
  • Правка ячеек: функция «Изменить базовую цену в ячейке». Значения — целые рубли,
    неотрицательные. UPSERT.
"""
import collections
import datetime
from zoneinfo import ZoneInfo
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from .. import db, auth, price_model, util

router = APIRouter(prefix="/api/prices", tags=["prices"])

# Маркетплейсы, которые обслуживает прайс-лист.
MP_MAP = {"ozon": "Ozon", "wb": "Wildberries", "wildberries": "Wildberries",
          "ya": "Yandex", "yandex": "Yandex"}
MP_ORDER = ("Ozon", "Wildberries", "Yandex")


def _norm_str(v):
    """None/пробелы → None; иначе — strip'нутая строка."""
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _parse_iso_date(s: Optional[str]) -> Optional[datetime.date]:
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(s)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail=f"Некорректная дата: {s!r}")


@router.get("/pricelist")
def pricelist(
    date_from: Optional[str] = Query(None, description="ISO YYYY-MM-DD, начало периода"),
    date_to:   Optional[str] = Query(None, description="ISO YYYY-MM-DD, конец периода"),
    user=Depends(auth.require("prices.list.view", "prices.index.view", "prices.model.view")),
):
    """Возвращает плоский список товаров с ценами и иерархией.

    Правило универсума («как в РНП заказы»):
      • Если задан период [date_from..date_to] — универсум = SKU, у которых в
        этом периоде было движение по Ozon/Wildberries в fact_weekly (неделя
        пересекается с периодом и суммарные метрики > 0). Статус «−» без
        движения = скрыть.
      • Если период пуст — универсум = все SKU из catalog_marketplace для
        Ozon/Wildberries (обратная совместимость), правило статуса «−» не
        применяется (нет периода для проверки движений).

    Ответ: {
      items: [
        { seller_article, sample_name, l1, l2, l3, status,
          price_ozon: int|null, price_wb: int|null }
      ],
      updated_at_max: str|null,
      period: { date_from, date_to } | null
    }
    """
    df = _parse_iso_date(date_from)
    dt = _parse_iso_date(date_to)
    if df and dt and df > dt:
        df, dt = dt, df

    if df and dt:
        # Универсум по периоду: SKU с движением в fact_weekly.
        # Пересечение недели [period_start..period_end] с [df..dt] считаем через
        # НЕ ПУСТОЕ пересечение: period_end >= df AND period_start <= dt.
        rows = db.query_all(
            """
            WITH moved AS (
                SELECT f.seller_article
                  FROM fact_weekly f
                 WHERE f.marketplace IN ('Ozon','Wildberries')
                   AND f.seller_article IS NOT NULL AND f.seller_article <> ''
                   AND f.period_end   >= %s
                   AND f.period_start <= %s
                 GROUP BY f.seller_article
                HAVING (COALESCE(SUM(f.revenue),0)
                      + COALESCE(SUM(f.sales_qty),0)
                      + COALESCE(SUM(f.orders_qty),0)
                      + COALESCE(SUM(f.returns_qty),0)
                      + COALESCE(SUM(f.profit),0)
                      + COALESCE(SUM(f.holds_total),0)) > 0
            ),
            statuses AS (
                -- Худший статус SKU по МП: если хоть где НЕ '-' — то NOT '-'.
                SELECT seller_article,
                       MIN(CASE WHEN btrim(COALESCE(status,'')) = '-' THEN 1 ELSE 0 END) AS all_minus
                  FROM catalog_marketplace
                 WHERE marketplace IN ('Ozon','Wildberries')
                   AND seller_article IS NOT NULL AND seller_article <> ''
                 GROUP BY seller_article
            ),
            universe AS (
                -- Активные в периоде.
                SELECT seller_article FROM moved
                UNION
                -- И плюс те, что есть в каталоге и НЕ имеют статуса '-' по всем МП:
                -- они могут не иметь движения, но не должны исчезать (не '-' и
                -- активность мы не проверяли; правило РНП скрывает ТОЛЬКО '-').
                -- В нашем случае «неактивные без движения» = SKU НЕ в moved.
                -- По ТЗ пользователя (аналог РНП заказы) прайс-лист показывает
                -- ТОЛЬКО активных в периоде → берём только moved.
                SELECT NULL::text WHERE false
            )
            SELECT u.seller_article,
                   ci.sample_name,
                   ci.category_l1 AS l1,
                   ci.category_l2 AS l2,
                   ci.category_l3 AS l3,
                   s.all_minus,
                   po.base_price  AS price_ozon,
                   pw.base_price  AS price_wb,
                   py.base_price  AS price_ya,
                   po.vf AS from_ozon, pw.vf AS from_wb, py.vf AS from_ya,
                   co.cost_calc   AS cost,
                   co.start_date  AS cost_date,
                   -- WB: всё из mp_price_daily (загружаемая, СПП, для покупателя).
                   wbp.date              AS wb_last_date,
                   wbp.upload_price      AS wb_upload_price,
                   wbp.spp_pct           AS wb_spp_pct,
                   wbp.spp_is_estimated  AS wb_spp_is_estimated,
                   wbp.buyer_price       AS wb_buyer_price,
                   -- Ozon: то же самое из mp_price_daily.
                   ozp.date              AS oz_last_date,
                   ozp.upload_price      AS oz_upload_price,
                   ozp.spp_pct           AS oz_spp_pct,
                   ozp.spp_is_estimated  AS oz_spp_is_estimated,
                   ozp.buyer_price       AS oz_buyer_price
              FROM universe u
              LEFT JOIN catalog_items ci
                     ON ci.seller_article = u.seller_article
              LEFT JOIN statuses s
                     ON s.seller_article = u.seller_article
              -- Базовые цены — историчный срез из catalog_base_prices_hist:
              -- запись, действующая на конец последнего дня периода по Москве
              -- (то же правило, что у daily Pi).
              LEFT JOIN LATERAL (
                  SELECT h.base_price, (h.valid_from AT TIME ZONE 'Europe/Moscow')::date AS vf
                    FROM catalog_base_prices_hist h
                   WHERE h.seller_article = u.seller_article
                     AND h.marketplace = 'Ozon'
                     AND h.valid_from < ((%s::date + 1)::timestamp AT TIME ZONE 'Europe/Moscow')
                   ORDER BY h.valid_from DESC LIMIT 1
              ) po ON TRUE
              LEFT JOIN LATERAL (
                  SELECT h.base_price, (h.valid_from AT TIME ZONE 'Europe/Moscow')::date AS vf
                    FROM catalog_base_prices_hist h
                   WHERE h.seller_article = u.seller_article
                     AND h.marketplace = 'Wildberries'
                     AND h.valid_from < ((%s::date + 1)::timestamp AT TIME ZONE 'Europe/Moscow')
                   ORDER BY h.valid_from DESC LIMIT 1
              ) pw ON TRUE
              LEFT JOIN LATERAL (
                  SELECT h.base_price, (h.valid_from AT TIME ZONE 'Europe/Moscow')::date AS vf
                    FROM catalog_base_prices_hist h
                   WHERE h.seller_article = u.seller_article
                     AND h.marketplace = 'Yandex'
                     AND h.valid_from < ((%s::date + 1)::timestamp AT TIME ZONE 'Europe/Moscow')
                   ORDER BY h.valid_from DESC LIMIT 1
              ) py ON TRUE
              -- Себестоимость — историчный срез (item_cost_hist), как было.
              LEFT JOIN LATERAL (
                  SELECT ich.cost_calc, ich.start_date
                    FROM item_cost_hist ich
                   WHERE upper(ich.seller_article) = upper(u.seller_article)
                     AND ich.start_date <= %s::date
                     AND ich.cost_calc IS NOT NULL AND ich.cost_calc > 0
                   ORDER BY ich.start_date DESC LIMIT 1
              ) co ON TRUE
              -- WB: последний известный срез цен/СПП <= конец периода.
              -- Загружаемая цена, СПП (с флагом is_estimated) и вычисленная
              -- buyer_price — всё из mp_price_daily, одним чтением.
              LEFT JOIN LATERAL (
                  SELECT m.date, m.upload_price, m.spp_pct,
                         m.spp_is_estimated, m.buyer_price
                    FROM mp_price_daily m
                   WHERE m.seller_article = u.seller_article
                     AND m.marketplace = 'Wildberries'
                     AND m.date <= %s::date
                   ORDER BY m.date DESC LIMIT 1
              ) wbp ON TRUE
              -- Ozon: аналогично.
              LEFT JOIN LATERAL (
                  SELECT m.date, m.upload_price, m.spp_pct,
                         m.spp_is_estimated, m.buyer_price
                    FROM mp_price_daily m
                   WHERE m.seller_article = u.seller_article
                     AND m.marketplace = 'Ozon'
                     AND m.date <= %s::date
                   ORDER BY m.date DESC LIMIT 1
              ) ozp ON TRUE
             ORDER BY
                 (ci.category_l1 IS NULL) ASC,
                 COALESCE(ci.category_l1, ''),
                 COALESCE(ci.category_l2, ''),
                 COALESCE(ci.category_l3, ''),
                 u.seller_article
            """,
            (df, dt, dt, dt, dt, dt, dt, dt),
        )
    else:
        # Fallback без периода — старый универсум (полный catalog_marketplace).
        rows = db.query_all(
            """
            WITH universe AS (
                SELECT DISTINCT cm.seller_article
                  FROM catalog_marketplace cm
                 WHERE cm.marketplace IN ('Ozon', 'Wildberries')
                   AND cm.seller_article IS NOT NULL
                   AND cm.seller_article <> ''
            )
            SELECT u.seller_article,
                   ci.sample_name,
                   ci.category_l1 AS l1,
                   ci.category_l2 AS l2,
                   ci.category_l3 AS l3,
                   NULL::int AS all_minus,
                   po.base_price  AS price_ozon,
                   pw.base_price  AS price_wb,
                   py.base_price  AS price_ya,
                   (po.valid_from AT TIME ZONE 'Europe/Moscow')::date AS from_ozon,
                   (pw.valid_from AT TIME ZONE 'Europe/Moscow')::date AS from_wb,
                   (py.valid_from AT TIME ZONE 'Europe/Moscow')::date AS from_ya,
                   co.cost_calc   AS cost,
                   co.start_date  AS cost_date,
                   wbp.date              AS wb_last_date,
                   wbp.upload_price      AS wb_upload_price,
                   wbp.spp_pct           AS wb_spp_pct,
                   wbp.spp_is_estimated  AS wb_spp_is_estimated,
                   wbp.buyer_price       AS wb_buyer_price,
                   ozp.date              AS oz_last_date,
                   ozp.upload_price      AS oz_upload_price,
                   ozp.spp_pct           AS oz_spp_pct,
                   ozp.spp_is_estimated  AS oz_spp_is_estimated,
                   ozp.buyer_price       AS oz_buyer_price
              FROM universe u
              LEFT JOIN catalog_items ci
                     ON ci.seller_article = u.seller_article
              LEFT JOIN LATERAL (
                  SELECT ich.cost_calc, ich.start_date
                    FROM item_cost_hist ich
                   WHERE upper(ich.seller_article) = upper(u.seller_article)
                     AND ich.cost_calc IS NOT NULL AND ich.cost_calc > 0
                   ORDER BY ich.start_date DESC LIMIT 1
              ) co ON TRUE
              -- Fallback без периода: последний известный срез цен из mp_price_daily.
              LEFT JOIN LATERAL (
                  SELECT m.date, m.upload_price, m.spp_pct,
                         m.spp_is_estimated, m.buyer_price
                    FROM mp_price_daily m
                   WHERE m.seller_article = u.seller_article
                     AND m.marketplace = 'Wildberries'
                   ORDER BY m.date DESC LIMIT 1
              ) wbp ON TRUE
              LEFT JOIN LATERAL (
                  SELECT m.date, m.upload_price, m.spp_pct,
                         m.spp_is_estimated, m.buyer_price
                    FROM mp_price_daily m
                   WHERE m.seller_article = u.seller_article
                     AND m.marketplace = 'Ozon'
                   ORDER BY m.date DESC LIMIT 1
              ) ozp ON TRUE
              -- Без периода — актуальные (открытые) записи из истории.
              LEFT JOIN catalog_base_prices_hist po
                     ON po.seller_article = u.seller_article AND po.marketplace = 'Ozon' AND po.valid_to IS NULL
              LEFT JOIN catalog_base_prices_hist pw
                     ON pw.seller_article = u.seller_article AND pw.marketplace = 'Wildberries' AND pw.valid_to IS NULL
              LEFT JOIN catalog_base_prices_hist py
                     ON py.seller_article = u.seller_article AND py.marketplace = 'Yandex' AND py.valid_to IS NULL
             ORDER BY
                 (ci.category_l1 IS NULL) ASC,
                 COALESCE(ci.category_l1, ''),
                 COALESCE(ci.category_l2, ''),
                 COALESCE(ci.category_l3, ''),
                 u.seller_article
            """
        )

    items = []
    for r in rows:
        items.append({
            "seller_article": r["seller_article"],
            "sample_name": r.get("sample_name"),
            "l1": _norm_str(r.get("l1")),
            "l2": _norm_str(r.get("l2")),
            "l3": _norm_str(r.get("l3")),
            "price_ozon": (int(r["price_ozon"]) if r.get("price_ozon") is not None else None),
            "price_wb":   (int(r["price_wb"])   if r.get("price_wb")   is not None else None),
            "price_ya":   (int(r["price_ya"])   if r.get("price_ya")   is not None else None),
            # Дата (по Москве), с которой действует эта базовая цена.
            "from_ozon": (r["from_ozon"].isoformat() if r.get("price_ozon") is not None and r.get("from_ozon") else None),
            "from_wb":   (r["from_wb"].isoformat()   if r.get("price_wb")   is not None and r.get("from_wb")   else None),
            "from_ya":   (r["from_ya"].isoformat()   if r.get("price_ya")   is not None and r.get("from_ya")   else None),
            # Себестоимость среза + дата, с которой этот срез действует.
            "cost":      (float(r["cost"]) if r.get("cost") is not None else None),
            "cost_date": (r["cost_date"].isoformat() if r.get("cost_date") else None),
            # ── Индекс цен, блок Wildberries ─────────────────────────────
            # Всё берём одним чтением из mp_price_daily: последний срез
            # (загружаемая цена, СПП с флагом «оценочная», для покупателя).
            "wb_last_date":        (r["wb_last_date"].isoformat() if r.get("wb_last_date") else None),
            "wb_upload_price":     (float(r["wb_upload_price"]) if r.get("wb_upload_price") is not None else None),
            "wb_spp_pct":          (float(r["wb_spp_pct"])       if r.get("wb_spp_pct")       is not None else None),
            "wb_spp_is_estimated": bool(r.get("wb_spp_is_estimated")) if r.get("wb_spp_is_estimated") is not None else None,
            "wb_buyer_price":      (float(r["wb_buyer_price"])   if r.get("wb_buyer_price")   is not None else None),
            # ── Индекс цен, блок Ozon ───────────────────────────────────
            # Аналогично WB — единый источник mp_price_daily.
            "oz_last_date":        (r["oz_last_date"].isoformat() if r.get("oz_last_date") else None),
            "oz_upload_price":     (float(r["oz_upload_price"]) if r.get("oz_upload_price") is not None else None),
            "oz_spp_pct":          (float(r["oz_spp_pct"])       if r.get("oz_spp_pct")       is not None else None),
            "oz_spp_is_estimated": bool(r.get("oz_spp_is_estimated")) if r.get("oz_spp_is_estimated") is not None else None,
            "oz_buyer_price":      (float(r["oz_buyer_price"])   if r.get("oz_buyer_price")   is not None else None),
        })

    # Себестоимость — только ролям с функцией «Показать себестоимость» (и в выгрузках:
    # они берут строки отсюда).
    if not auth.can(user, "prices.list.cost"):
        for it in items:
            it["cost"] = it["cost_date"] = None

    upd = db.query_one("SELECT max(updated_at) AS mx FROM catalog_base_prices")
    upd_max = upd.get("mx") if upd else None

    return {
        "items": items,
        "count": len(items),
        "updated_at_max": (upd_max.isoformat() if upd_max else None),
        "period": (
            {"date_from": df.isoformat(), "date_to": dt.isoformat()}
            if (df and dt) else None
        ),
    }


class PriceCellIn(BaseModel):
    seller_article: str
    marketplace: str            # 'ozon' | 'wb' | 'wildberries'
    base_price: Optional[int]   # None или '' → удалить цену (сбросить в NULL)


@router.post("/cell")
def price_cell(body: PriceCellIn, user=Depends(auth.require("prices.list.edit"))):
    """Инлайн-правка одной ячейки прайс-листа (функция «Изменить базовую цену
    в ячейке»). UPSERT: если строка есть — UPDATE, иначе INSERT.

    base_price:
      • неотрицательное целое → сохранить;
      • None → сбросить цену (в БД останется строка с base_price=NULL,
        фронт покажет «—» и позволит ввести заново).
    """
    mp_key = (body.marketplace or "").strip().lower()
    mp = MP_MAP.get(mp_key)
    if not mp:
        raise HTTPException(status_code=400, detail=f"Неизвестный маркетплейс: {body.marketplace!r}")

    sa = _norm_str(body.seller_article)
    if not sa:
        raise HTTPException(status_code=400, detail="Пустой seller_article")

    bp = body.base_price
    if bp is not None:
        try:
            bp = int(bp)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="base_price должен быть целым числом или null")
        if bp < 0:
            raise HTTPException(status_code=400, detail="base_price не может быть отрицательным")

    uid = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    # Двойная запись в одной транзакции: витрина + история.
    # В истории — версии: закрываем активный ряд (valid_to = now())
    # и вставляем новый (valid_from = now(), valid_to = NULL).
    with db.transaction() as tx:
        tx.execute(
            """INSERT INTO catalog_base_prices (seller_article, marketplace, base_price, updated_at, updated_by_user_id)
                    VALUES (%s, %s, %s, now(), %s)
               ON CONFLICT (seller_article, marketplace) DO UPDATE SET
                    base_price = EXCLUDED.base_price,
                    updated_at = now(),
                    updated_by_user_id = EXCLUDED.updated_by_user_id""",
            (sa, mp, bp, uid),
        )
        _append_base_price_history(tx, sa, mp, bp, uid)
    return {"ok": True, "seller_article": sa, "marketplace": mp, "base_price": bp}


def _append_base_price_history(tx, sa: str, mp: str, base_price, uid):
    """Закрыть активный ряд (valid_to = NULL) и вставить новый.

    Вызывается ВНУТРИ transaction() — вместе с UPSERT в витрину, чтобы история
    и текущее значение не расходились. Не вставляем новый ряд, если активное
    значение не меняется (не засоряем историю повторами)."""
    cur = tx.query_one(
        """SELECT base_price FROM catalog_base_prices_hist
            WHERE seller_article=%s AND marketplace=%s AND valid_to IS NULL
            LIMIT 1""",
        (sa, mp),
    )
    cur_val = cur["base_price"] if cur else None
    if cur_val == base_price:
        return  # ничего не меняется — история не пополняется
    tx.execute(
        """UPDATE catalog_base_prices_hist
              SET valid_to = now()
            WHERE seller_article=%s AND marketplace=%s AND valid_to IS NULL""",
        (sa, mp),
    )
    tx.execute(
        """INSERT INTO catalog_base_prices_hist
               (seller_article, marketplace, base_price, valid_from, valid_to, updated_by_user_id)
            VALUES (%s, %s, %s, now(), NULL, %s)""",
        (sa, mp, base_price, uid),
    )


# ---------------------------------------------------------------------------
# Массовая загрузка базовых цен: выгрузка шаблона Excel → заполнение → загрузка.
# Логика намеренно повторяет план продаж («Продажи, шт.» → plan_export /
# plan_import), чтобы у пользователя был один и тот же сценарий работы:
#   • состав строк шаблона = ровно то, что видно в прайс-листе за выбранный
#     период (тот же универсум, тот же порядок групп);
#   • справочные столбцы (иерархия, название) при загрузке НЕ читаются;
#   • столбцы цен ищутся ПО ШАПКЕ, а не по позиции — файл грузится независимо
#     от числа и порядка справочных столбцов;
#   • ПУСТАЯ ячейка цены = цена удаляется (как пустая ячейка месяца в плане).
# ---------------------------------------------------------------------------

# Заголовки столбцов цен в шаблоне. При загрузке ищем по первому слову
# «базовая» + маркетплейс, поэтому переименование в шаблоне не ломает импорт.
COL_OZON = "Базовая OZON"
COL_WB = "Базовая WB"
COL_YA = "Базовая Yandex"
# Дата начала действия цены — своя колонка у каждого МП (с 12.10.2026). Пустая дата
# при загрузке = «с текущего момента», как до появления колонок; будущие даты не принимаются.
COL_OZON_FROM = "Действует с (OZON)"
COL_WB_FROM = "Действует с (WB)"
COL_YA_FROM = "Действует с (Yandex)"
MSK = ZoneInfo("Europe/Moscow")


def _export_rows(date_from: Optional[str], date_to: Optional[str], user, search=""):
    """Строки для шаблона = ответ /pricelist, отсортированный по правилам
    порядка групп проекта (L1 фиксированно: ECOM → ТД «АВТОПРОФИ» → прочие
    по алфавиту; L2/L3 — алфавит; внутри L3 — по артикулу)."""
    from .. import util
    data = pricelist(date_from=date_from, date_to=date_to, user=user)
    items = list(data.get("items") or [])
    q = (search or "").strip().lower()
    if q:
        items = [r for r in items if q in (r.get("seller_article") or "").lower()
                 or q in (r.get("sample_name") or "").lower()]
    items.sort(key=lambda r: (
        # Товары без L1 («Не распределены по группам») — в конец, как во фронте.
        1 if not r.get("l1") else 0,
        util.l1_sort_key(r.get("l1")),
        util.cat_sort_key(r.get("l2")),
        util.cat_sort_key(r.get("l3")),
        (r.get("seller_article") or ""),
    ))
    return items, data.get("period")


# ── «Ценовая модель»: вводимые параметры с историей (price_model_hist) ──────
# Цена категории (₽), плановый ДРР и плановая маржа (доли). Одна запись на
# артикул + площадку + параметр + день по Москве; правка в тот же день
# перезаписывает значение дня. NULL — значение удалено с этой даты.


@router.get("/model_params")
def model_params(
    date_to: Optional[str] = Query(None, description="ISO YYYY-MM-DD, конец периода; пусто — сегодня"),
    user=Depends(auth.require("prices.model.view", "prices.index.view")),
):
    """Срез параметров на конец периода (price_model.load_slice) и пороги сигнала.
    С правом только на «Индекс цен» — одни цены категории (колонки по галочке «цена
    категории», 22.10.2026); ДРР и маржа — только с правом на «Ценовую модель»."""
    dt = _parse_iso_date(date_to) or datetime.datetime.now(MSK).date()
    items = price_model.load_slice(dt)
    if not auth.can(user, "prices.model.view"):
        items = [r for r in items if r["param"] == "category_price"]
    return {"items": items, "date_to": dt.isoformat(),
            "rules": price_model.load_rules(), "rules_defaults": price_model.DEFAULT_RULES}


@router.get("/spp_history")
def spp_history(
    seller_article: str = Query(..., max_length=200),
    marketplace: str = Query(...),
    date_to: str = Query(..., description="ISO YYYY-MM-DD — дата отчёта, из которого СПП в ячейке"),
    days: int = Query(14, ge=1, le=60),
    user=Depends(auth.require("prices.index.view", "prices.model.view")),
):
    """История СПП товара по дням из ценовых отчётов (mp_price_daily) — подсказка у СПП
    в «Индексе цен» и «Ценовой модели». Дни — от date_to назад, свежие первыми;
    spp None — за день нет отчёта или СПП пустая (товара не было в продаже)."""
    mp = MP_MAP.get((marketplace or "").strip().lower())
    if mp not in ("Wildberries", "Ozon"):
        raise HTTPException(status_code=400, detail=f"Неизвестный маркетплейс: {marketplace!r}")
    dt = _parse_iso_date(date_to)
    if not dt:
        raise HTTPException(status_code=400, detail="Не указана дата")
    start = dt - datetime.timedelta(days=days - 1)
    rows = {r["date"]: r for r in db.query_all(
        """SELECT date, spp_pct, spp_is_estimated FROM mp_price_daily
            WHERE seller_article = %s AND marketplace = %s AND date BETWEEN %s AND %s""",
        (seller_article, mp, start, dt))}
    out = []
    for i in range(days):
        d = dt - datetime.timedelta(days=i)
        r = rows.get(d)
        spp = float(r["spp_pct"]) if r and r["spp_pct"] is not None else None
        out.append({"date": d.isoformat(), "spp": spp, "est": bool(r and r["spp_is_estimated"] and spp is not None)})
    return {"seller_article": seller_article, "marketplace": mp, "date_to": dt.isoformat(), "days": out}


@router.put("/model_rules")
def model_rules_save(payload: dict = Body(...), user=Depends(auth.require("prices.model.settings"))):
    """Пороги сигнала корректировки (кнопка ⚙): {"rules": {площадка: доля}} → все пороги после записи."""
    try:
        rules = price_model.save_rules(payload.get("rules"), user.get("id"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"rules": rules, "defaults": price_model.DEFAULT_RULES}


class ModelParamIn(BaseModel):
    seller_article: str
    marketplace: str            # 'wb' | 'wildberries' | 'ozon' | 'ya' | 'yandex'
    param: str                  # 'category_price' | 'drr_pct' | 'margin_pct'
    value: Optional[float]      # None → удалить значение


@router.post("/model_params")
def model_param_set(body: ModelParamIn, user=Depends(auth.require(
        "prices.model.edit", "prices.model.drr", "prices.model.margin"))):
    """Задать или удалить (None) один параметр артикула на площадке с сегодняшнего дня.
    Право — своё у каждого параметра (price_model.PARAMS): цена категории, ДРР, маржа."""
    mp = MP_MAP.get((body.marketplace or "").strip().lower())
    if mp not in MP_ORDER:
        raise HTTPException(status_code=400, detail=f"Неизвестный маркетплейс: {body.marketplace!r}")
    spec = price_model.PARAMS.get(body.param)
    if not spec:
        raise HTTPException(status_code=400, detail=f"Неизвестный параметр: {body.param!r}")
    sa = util.canon_article(body.seller_article or "")
    if not sa:
        raise HTTPException(status_code=400, detail="Пустой артикул")
    auth.check(user, spec[3])
    try:
        value = price_model.check_value(body.param, body.value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Значение: {e}")
    today = datetime.datetime.now(MSK).date()
    price_model.write_values([(sa, mp, body.param, value)], user.get("id"), today)
    return {"ok": True, "seller_article": sa, "marketplace": mp, "param": body.param,
            "value": value, "date": today.isoformat()}


class ModelExportIn(BaseModel):
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    articles: Optional[list[str]] = None   # видимые на странице строки (поиск, фильтр сигналов); None — все


@router.post("/model_export")
def model_export(body: ModelExportIn, user=Depends(auth.require("prices.model.export"))):
    """Excel-шаблон «Ценовой модели»: строки — как в отчёте (тот же состав и порядок, что
    в прайс-листе за период; если страница передала артикулы — только они), значения —
    срез на конец периода. Заполняется и загружается обратно (model_import)."""
    from fastapi.responses import StreamingResponse
    from urllib.parse import quote
    items, period = _export_rows(body.date_from, body.date_to, user)
    if body.articles is not None:
        keep = set(body.articles)
        items = [r for r in items if r.get("seller_article") in keep]
    dt = _parse_iso_date(body.date_to) or datetime.datetime.now(MSK).date()
    values = {(r["seller_article"], r["marketplace"], r["param"]): r["value"] for r in price_model.load_slice(dt)}
    buf = price_model.export_workbook(items, values)
    filename = f"Ценовая модель {datetime.datetime.now(MSK):%d.%m.%Y}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
                 "Cache-Control": "no-cache, no-store, must-revalidate, private",
                 "Pragma": "no-cache", "Expires": "0"},
    )


@router.post("/model_import")
def model_import(file: UploadFile = File(...), user=Depends(auth.require("prices.model.import"))):
    """Загрузка заполненного шаблона «Ценовой модели» (решения владельца 21.10.2026).

    • Колонки ищутся по шапке; читаются только те, на которые у пользователя есть право
      (цена категории — №101, ДРР — №106, маржа — №107), остальные пропускаются.
    • Артикулы сверяются со справочником своей площадки (catalog_marketplace) и не заводятся:
      значения площадки, где артикула нет, пропускаются; артикулы, которых нет ни на одной, —
      в списке неизвестных.
    • Пустая ячейка — удалить значение. Ошибочное значение отменяет весь файл.
    • Запись — как ручная правка: на сегодня, с историей по дням (price_model.write_values)."""
    try:
        parsed = price_model.parse_workbook(file.file.read(), lambda p: auth.can(user, price_model.PARAMS[p][3]))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if parsed["errors"]:
        errs = parsed["errors"]
        raise HTTPException(status_code=422, detail="Файл не загружен — ошибки (%d): %s%s" % (
            len(errs), "; ".join(errs[:15]), "; …" if len(errs) > 15 else ""))
    known = {mp: _known_articles_for(mp) for mp, _ in price_model.EXCEL_MPS}
    entries, unknown, skipped_mp, articles = [], [], [], set()
    for _, art, vals in parsed["rows"]:
        if not any(art in known[mp] for mp in known):
            unknown.append(art)
            continue
        for (mp, param), value in vals.items():
            if art not in known[mp]:
                if value is not None and (art, mp) not in skipped_mp:
                    skipped_mp.append((art, mp))
                continue
            entries.append((art, mp, param, value))
            articles.add(art)
    counts = price_model.write_values(entries, user.get("id"), datetime.datetime.now(MSK).date())
    return {"ok": True, "articles": len(articles), "set": counts["set"], "cleared": counts["cleared"],
            "skipped_columns": parsed["skipped_columns"],
            "skipped_unknown": len(unknown), "unknown_sample": unknown[:10],
            "skipped_mp": len(skipped_mp), "skipped_mp_sample": [f"{a} ({mp})" for a, mp in skipped_mp[:10]]}


@router.get("/index_export")
def prices_index_export(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    show_cost: bool = Query(False),
    search: str = Query("", max_length=500),
    user=Depends(auth.require("prices.index.export")),
):
    """Read-only Excel report; same dates, article search and source as the UI."""
    if show_cost:
        auth.check(user, "prices.list.cost")
    from fastapi.responses import StreamingResponse
    from urllib.parse import quote
    from ..price_export import REPORT_TITLE, export_time, index_workbook
    items, period = _export_rows(date_from, date_to, user, search)
    generated_at = export_time()
    buf = index_workbook(items, period, show_cost, search.strip(), generated_at=generated_at)
    filename = f"{REPORT_TITLE} {generated_at:%d.%m.%Y}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
                 "Cache-Control": "no-cache, no-store, must-revalidate, private",
                 "Pragma": "no-cache", "Expires": "0"},
    )


@router.get("/export")
def prices_export(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    show_cost: bool = Query(False),
    search: str = Query("", max_length=500),
    user=Depends(auth.require("prices.list.export")),
):
    """Excel-шаблон базовых цен по товарам прайс-листа за период."""
    if show_cost:
        auth.check(user, "prices.list.cost")
    import io
    import openpyxl
    from openpyxl.styles import Font, Alignment
    from fastapi.responses import StreamingResponse

    from ..price_export import text_cell
    items, period = _export_rows(date_from, date_to, user, search)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Базовые цены"
    # Артикул, три цены и у каждой — дата, с которой она действует.
    header = ["Артикул", COL_OZON, COL_OZON_FROM, COL_WB, COL_WB_FROM, COL_YA, COL_YA_FROM]
    if show_cost:
        header.append("Себестоимость")
    ws.append(header)
    for c in range(1, len(header) + 1):
        ws.cell(row=1, column=c).font = Font(bold=True)
        ws.cell(row=1, column=c).alignment = Alignment(horizontal="center", wrap_text=True)

    def _d(v):
        return datetime.date.fromisoformat(v) if v else None
    for r in items:
        ws.append([
            r.get("seller_article"),
            r.get("price_ozon"), _d(r.get("from_ozon")),
            r.get("price_wb"), _d(r.get("from_wb")),
            r.get("price_ya"), _d(r.get("from_ya")),
        ] + ([r.get("cost")] if show_cost else []))
        text_cell(ws.cell(ws.max_row, 1), r.get("seller_article"))
        if show_cost:
            ws.cell(ws.max_row, 8).number_format = "#,##0.00"
    # Цены — целые рубли, даты — ДД.ММ.ГГГГ.
    for row in ws.iter_rows(min_row=2, min_col=2, max_col=7):
        for cell in row:
            cell.number_format = "DD.MM.YYYY" if cell.column in (3, 5, 7) else "#,##0"
    for col, w in zip("ABCDEFG", (24, 14, 14, 14, 14, 14, 14)):
        ws.column_dimensions[col].width = w
    if show_cost:
        ws.column_dimensions["H"].width = 18
    ws.freeze_panes = "B2"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    suffix = ""
    if period:
        suffix = "_%s_%s" % (period["date_from"], period["date_to"])
    fname = "base_prices%s.xlsx" % suffix
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename={fname}",
            # НЕ кешируем файл: иначе Cloudflare/браузер отдаёт старую версию.
            "Cache-Control": "no-cache, no-store, must-revalidate, private",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def _price_cell(v):
    """'' / None → None (удалить). Иначе целое ≥ 0 или ValueError."""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "-", "—"):
        return None
    s = s.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        f = float(s)
    except (ValueError, TypeError):
        raise ValueError(s)
    if f < 0 or f != f:
        raise ValueError(s)
    return int(round(f))


def _date_cell(v, today):
    """Дата начала действия цены: дата Excel, «ДД.ММ.ГГГГ» или «ГГГГ-ММ-ДД».
    Пусто → None («с текущего момента»). Будущая дата или мусор → ValueError."""
    if v is None or (isinstance(v, str) and v.strip() in ("", "-", "—")):
        return None
    if isinstance(v, datetime.datetime):
        d = v.date()
    elif isinstance(v, datetime.date):
        d = v
    elif isinstance(v, (int, float)) and 20000 < v < 80000:   # дата Excel без формата
        from openpyxl.utils.datetime import from_excel
        d = from_excel(v).date()
    else:
        s = str(v).strip()
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
            try:
                d = datetime.datetime.strptime(s, fmt).date()
                break
            except ValueError:
                continue
        else:
            raise ValueError(s)
    if d > today:
        raise ValueError(f"{d:%d.%m.%Y} — дата в будущем")
    if d.year < 2020:
        raise ValueError(f"{d:%d.%m.%Y}")
    return d


def _apply_from(versions, price, start, uid):
    """История одной пары (артикул, МП) после «цена price действует с момента start».

    versions — версии по возрастанию valid_from: dict(base_price, valid_from, valid_to,
    updated_by_user_id). Правило владельца (12.10.2026, вариант А): цена действует
    с start и дальше — версии, начавшиеся в start или позже, заменяются; версия,
    действовавшая до start, закрывается в start. Та же цена, что до start, —
    продолжение этой версии (без новой строки). Цены не было и price пустая — ничего."""
    out = [dict(v) for v in versions if v["valid_from"] < start]
    if out:
        last = out[-1]
        if last["valid_to"] is None or last["valid_to"] > start:
            last["valid_to"] = start
        if last["base_price"] == price and last["valid_to"] == start:
            last["valid_to"] = None
            return out
    elif price is None:
        return []
    out.append({"base_price": price, "valid_from": start, "valid_to": None,
                "updated_by_user_id": uid})
    return out


def _hist_key(versions):
    return [(v["base_price"], v["valid_from"], v["valid_to"]) for v in versions]


@router.post("/import")
def prices_import(
    file: UploadFile = File(...),
    user=Depends(auth.require("prices.list.import")),
):
    """Импорт базовых цен из Excel. Только администратор.

    Столбцы определяются по шапке (первая строка): заголовок с «ozon» → Ozon,
    «yandex» → Yandex, «wb»/«wildberries» → Wildberries; если в нём есть
    «действует»/«дата» — это дата начала действия цены этого МП, иначе — цена.
    Артикул — первый столбец. Прочие столбцы не читаются.

    Пустая ячейка цены ⇒ цена по этому МП удаляется (base_price = NULL).
    Дата: пусто ⇒ с текущего момента (как раньше); иначе цена действует
    с 00:00 по Москве этой даты и дальше (см. _apply_from). Будущие даты и
    некорректные значения отклоняют файл целиком.

    Артикулы, которых нет в catalog_items/catalog_marketplace, не создаются:
    они возвращаются в поле skipped_unknown.
    """
    import io
    import openpyxl

    try:
        data = file.file.read()
        wbk = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wbk.active
        rows_iter = list(ws.iter_rows(values_only=True))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=422, detail="Не удалось прочитать Excel-файл")

    if not rows_iter:
        raise HTTPException(status_code=422, detail="Файл пустой")

    header = rows_iter[0] or ()
    col_mp = {}          # индекс столбца цены -> 'Ozon' | 'Wildberries' | 'Yandex'
    date_col = {}        # МП -> индекс столбца даты
    for ci, h in enumerate(header):
        if ci == 0 or h is None:
            continue
        s = str(h).strip().lower()
        if "ozon" in s or "озон" in s:
            mp = "Ozon"
        elif "yandex" in s or "яндекс" in s:
            mp = "Yandex"
        elif "wb" in s or "wildberries" in s or "вб" in s:
            mp = "Wildberries"
        else:
            continue
        if "действ" in s or "дата" in s:
            date_col.setdefault(mp, ci)
        elif mp not in col_mp.values():
            col_mp[ci] = mp
    if not col_mp:
        raise HTTPException(
            status_code=422,
            detail="В шапке файла не найдены столбцы цен «%s», «%s», «%s»" % (COL_OZON, COL_WB, COL_YA),
        )

    # Известные артикулы: новые SKU через прайс-лист не создаём.
    known = {r["seller_article"] for r in db.query_all(
        """SELECT seller_article FROM catalog_items
            WHERE seller_article IS NOT NULL AND seller_article <> ''
           UNION
           SELECT seller_article FROM catalog_marketplace
            WHERE seller_article IS NOT NULL AND seller_article <> ''"""
    )}

    uid = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    now = datetime.datetime.now(datetime.timezone.utc)
    today = now.astimezone(MSK).date()
    entries = []          # (art, mp, price, date|None)
    errors = []           # 'артикул / МП: значение'
    unknown = []
    seen = set()
    for i, row in enumerate(rows_iter):
        if i == 0 or not row or row[0] is None:
            continue
        art = str(row[0]).strip()
        if not art:
            continue
        if art not in known:
            if art not in unknown:
                unknown.append(art)
            continue
        seen.add(art)
        for ci, mp in col_mp.items():
            raw = row[ci] if ci < len(row) else None
            dci = date_col.get(mp)
            raw_d = row[dci] if dci is not None and dci < len(row) else None
            try:
                p = _price_cell(raw)
            except ValueError as e:
                if len(errors) < 50:
                    errors.append("%s / %s: цена «%s»" % (art, mp, e))
                continue
            try:
                d = _date_cell(raw_d, today)
            except ValueError as e:
                if len(errors) < 50:
                    errors.append("%s / %s: дата «%s»" % (art, mp, e))
                continue
            entries.append((art, mp, p, d))

    if errors:
        raise HTTPException(
            status_code=422,
            detail="Некорректные значения (%d): %s" % (len(errors), "; ".join(errors[:10])),
        )

    changed_keys = []
    cleared = 0
    if entries:
        # Всё в одной транзакции: витрина + история.
        with db.transaction() as tx:
            # 1) Витрина — текущая цена (дата не позже сегодня ⇒ загруженная цена и есть текущая).
            tx.execute_values(
                """INSERT INTO catalog_base_prices
                       (seller_article, marketplace, base_price, updated_at, updated_by_user_id)
                   VALUES %s
                   ON CONFLICT (seller_article, marketplace) DO UPDATE SET
                       base_price = EXCLUDED.base_price,
                       updated_at = now(),
                       updated_by_user_id = EXCLUDED.updated_by_user_id""",
                [(a, mp, p, uid) for a, mp, p, _ in entries],
                template="(%s,%s,%s,now(),%s)",
            )
            # 2) История: читаем версии затронутых пар, пересчитываем в памяти по
            # правилу _apply_from и перезаписываем только изменившиеся пары —
            # одним DELETE и одной пачкой INSERT (триггеры daily Pi — на оператор).
            keys = sorted({(a, mp) for a, mp, _, _ in entries})
            hist = collections.defaultdict(list)
            for r in tx.query_all(
                """SELECT seller_article, marketplace, base_price, valid_from, valid_to,
                          updated_by_user_id
                     FROM catalog_base_prices_hist
                    WHERE (seller_article, marketplace) IN (
                          SELECT unnest(%s::text[]), unnest(%s::text[]))
                    ORDER BY valid_from""",
                ([k[0] for k in keys], [k[1] for k in keys]),
            ):
                hist[(r["seller_article"], r["marketplace"])].append(dict(r))
            # «Очищено» — только цены, которые реально были (пустая ячейка без цены — не очистка).
            was_set = {k for k, v in hist.items() if v and v[-1]["valid_to"] is None
                       and v[-1]["base_price"] is not None}
            cleared = len({(a, mp) for a, mp, p, _ in entries if p is None and (a, mp) in was_set})
            new_hist = {k: list(v) for k, v in hist.items()}
            for a, mp, p, d in entries:
                start = now if d is None else datetime.datetime(d.year, d.month, d.day, tzinfo=MSK)
                new_hist[(a, mp)] = _apply_from(new_hist.get((a, mp), []), p, start, uid)
            changed_keys = [k for k in keys if _hist_key(new_hist.get(k, [])) != _hist_key(hist.get(k, []))]
            if changed_keys:
                tx.execute(
                    """DELETE FROM catalog_base_prices_hist
                        WHERE (seller_article, marketplace) IN (
                              SELECT unnest(%s::text[]), unnest(%s::text[]))""",
                    ([k[0] for k in changed_keys], [k[1] for k in changed_keys]),
                )
                rows_new = [(k[0], k[1], v["base_price"], v["valid_from"], v["valid_to"],
                             v["updated_by_user_id"])
                            for k in changed_keys for v in new_hist[k]]
                if rows_new:
                    tx.execute_values(
                        """INSERT INTO catalog_base_prices_hist
                               (seller_article, marketplace, base_price, valid_from, valid_to, updated_by_user_id)
                           VALUES %s""",
                        rows_new,
                    )

    filled = sum(1 for e in entries if e[2] is not None)
    return {
        "ok": True,
        "articles": len(seen),
        "prices_set": filled,
        "prices_cleared": cleared,
        "dated": sum(1 for e in entries if e[3] is not None),
        "history_changed": len(changed_keys),
        "skipped_unknown": len(unknown),
        "unknown_sample": unknown[:10],
    }


# ---------------------------------------------------------------------------
# Загрузка отчётов цен из ЛК маркетплейсов (Ozon, WB) в mp_price_daily.
# Пользователь указывает «дату отчёта» (когда снят срез в ЛК). Файл несёт
# ТОЛЬКО upload_price; СПП/buyer_price вычисляются на месте (см. price_history).
# Пропущенные артикулы (нет в справочнике catalog_marketplace) — в блок
# «пропущено», как в РНП-загрузках. Каждая загрузка пишется в mp_price_upload_log.
# ---------------------------------------------------------------------------
import os as _os
import tempfile as _tempfile


def _known_articles_for(marketplace: str) -> set:
    """Множество известных артикулов данного МП. Артикул считаем «известным»,
    если он есть в catalog_marketplace для этого МП (там же живёт весь ассортимент
    других модулей)."""
    rows = db.query_all(
        "SELECT seller_article FROM catalog_marketplace WHERE marketplace=%s",
        (marketplace,),
    )
    return {r["seller_article"] for r in rows}


@router.post("/upload_ozon")
def upload_ozon_prices(
    file: UploadFile = File(...),
    user=Depends(auth.require("prices.upload.ozon")),
):
    """Отчёт Ozon: даты, цена после акций, соинвест и покупатель из файла."""
    return _do_dated_price_upload("Ozon", file, user)


@router.post("/upload_wb")
def upload_wb_prices(
    file: UploadFile = File(...),
    user=Depends(auth.require("prices.upload.wb")),
):
    """Отчёт WB «Цены с СПП»: даты из файла, одна атомарная загрузка."""
    return _do_dated_price_upload("Wildberries", file, user)


def _do_dated_price_upload(marketplace: str, file: UploadFile, user):
    """Атомарный импорт всех дней файла и дневных записей журнала."""
    from .. import price_history, price_upload
    parser = {"Wildberries": price_upload.parse_wb, "Ozon": price_upload.parse_ozon}[marketplace]
    label = "WB" if marketplace == "Wildberries" else "Ozon"

    fd, tmp_path = _tempfile.mkstemp(suffix=".xlsx")
    try:
        with _os.fdopen(fd, "wb") as f:
            content = file.file.read(price_upload.WB_MAX_FILE_BYTES + 1)
            if len(content) > price_upload.WB_MAX_FILE_BYTES:
                raise HTTPException(status_code=413, detail=f"Файл {label} слишком большой: максимум 25 МБ.")
            f.write(content)
        del content
        try:
            report = parser(tmp_path)
        except Exception as exc:
            # Ошибки чтения Excel/валидации не должны превращаться в 500.
            detail = str(exc) if isinstance(exc, ValueError) else f"Не удалось прочитать Excel. Проверьте файл отчёта {label}."
            raise HTTPException(status_code=422, detail=detail) from exc

        known = _known_articles_for(marketplace)
        good, unknown = [], []
        per_date = {
            date: {"written": 0, "unknown": []}
            for date in sorted(report.rows_by_date)
        }
        for row in report.rows:
            if row.article in known:
                good.append(row)
                per_date[row.date]["written"] += 1
            else:
                unknown.append(row.article)
                per_date[row.date]["unknown"].append(row.article)

        uid = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
        with db.transaction() as tx:
            result = price_history.sync_direct_report(tx, marketplace, good, uid)
            # Существующий журнал хранит одну дату: строка на каждый день
            # исходного файла, вместе с данными в той же транзакции.
            logs = []
            for date, stats in per_date.items():
                duplicate_count = report.duplicates_by_date[date]
                message = (
                    f"Повторы за день: {duplicate_count}; "
                    + ("выбрано самое позднее время." if marketplace == "Wildberries"
                       else "одинаковые записи объединены.")
                    if duplicate_count else None
                )
                logs.append((
                    uid, marketplace, date, file.filename,
                    report.rows_by_date[date], stats["written"], len(stats["unknown"]),
                    ";".join(stats["unknown"][:20]) or None, "ok", message,
                ))
            tx.execute_values(
                """INSERT INTO mp_price_upload_log
                     (loaded_by_user_id, marketplace, report_date, file_name,
                      rows_in_file, rows_upserted, rows_skipped_unknown,
                      skipped_sample, status, message) VALUES %s""",
                logs,
            )
        return {
            "ok": True,
            "marketplace": marketplace,
            "report_dates": [d.isoformat() for d in sorted(per_date)],
            "rows_in_file": report.rows_in_file,
            "rows_upserted": result["upserted"],
            "skipped_unknown": len(unknown),
            "unknown_sample": list(dict.fromkeys(unknown))[:10],
            "duplicates_resolved": sum(report.duplicates_by_date.values()),
        }
    finally:
        try:
            _os.remove(tmp_path)
        except OSError:
            pass


@router.get("/upload_log")
def price_upload_log(
    limit: int = Query(50, ge=1, le=500),
    user=Depends(auth.require("prices.uploads.log")),
):
    """Журнал загрузок цен (последние N записей). Формат зеркалит стиль журнала
    РНП-загрузок: дата, кто, тип, МП, дата отчёта, строк, статус, сообщение."""
    rows = db.query_all(
        """SELECT l.id, l.loaded_at, l.marketplace, l.report_date, l.file_name,
                  l.rows_in_file, l.rows_upserted, l.rows_skipped_unknown,
                  l.skipped_sample, l.status, l.message,
                  u.email AS loaded_by_email,
                  COALESCE(NULLIF(u.display_name,''), u.email) AS loaded_by_name
             FROM mp_price_upload_log l
        LEFT JOIN app_users u ON u.id = l.loaded_by_user_id
            ORDER BY l.loaded_at DESC
            LIMIT %s""",
        (limit,),
    )
    return {"items": rows}
