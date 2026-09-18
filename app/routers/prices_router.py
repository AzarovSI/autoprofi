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
  • Правка ячеек: любой авторизованный пользователь. Значения — целые рубли,
    неотрицательные. UPSERT.
"""
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from .. import db, auth

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
    user=Depends(auth.get_current_user),
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
              -- берём запись с самым свежим valid_from <= конец периода.
              LEFT JOIN LATERAL (
                  SELECT h.base_price
                    FROM catalog_base_prices_hist h
                   WHERE h.seller_article = u.seller_article
                     AND h.marketplace = 'Ozon'
                     AND h.valid_from <= (%s::date + INTERVAL '1 day')
                   ORDER BY h.valid_from DESC LIMIT 1
              ) po ON TRUE
              LEFT JOIN LATERAL (
                  SELECT h.base_price
                    FROM catalog_base_prices_hist h
                   WHERE h.seller_article = u.seller_article
                     AND h.marketplace = 'Wildberries'
                     AND h.valid_from <= (%s::date + INTERVAL '1 day')
                   ORDER BY h.valid_from DESC LIMIT 1
              ) pw ON TRUE
              LEFT JOIN LATERAL (
                  SELECT h.base_price
                    FROM catalog_base_prices_hist h
                   WHERE h.seller_article = u.seller_article
                     AND h.marketplace = 'Yandex'
                     AND h.valid_from <= (%s::date + INTERVAL '1 day')
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
def price_cell(body: PriceCellIn, user=Depends(auth.get_current_user)):
    """Инлайн-правка одной ячейки прайс-листа. Любой авторизованный
    пользователь. UPSERT: если строка есть — UPDATE, иначе INSERT.

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


def _export_rows(date_from: Optional[str], date_to: Optional[str], user):
    """Строки для шаблона = ответ /pricelist, отсортированный по правилам
    порядка групп проекта (L1 фиксированно: ECOM → ТД «АВТОПРОФИ» → прочие
    по алфавиту; L2/L3 — алфавит; внутри L3 — по артикулу)."""
    from .. import util
    data = pricelist(date_from=date_from, date_to=date_to, user=user)
    items = list(data.get("items") or [])
    items.sort(key=lambda r: (
        # Товары без L1 («Не распределены по группам») — в конец, как во фронте.
        1 if not r.get("l1") else 0,
        util.l1_sort_key(r.get("l1")),
        util.cat_sort_key(r.get("l2")),
        util.cat_sort_key(r.get("l3")),
        (r.get("seller_article") or ""),
    ))
    return items, data.get("period")


@router.get("/export")
def prices_export(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    user=Depends(auth.get_current_user),
):
    """Excel-шаблон базовых цен по товарам прайс-листа за период."""
    import io
    import openpyxl
    from openpyxl.styles import Font, Alignment
    from fastapi.responses import StreamingResponse

    items, period = _export_rows(date_from, date_to, user)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Базовые цены"
    # Только артикул и три цены: справочные столбцы в файле не нужны.
    header = ["Артикул", COL_OZON, COL_WB, COL_YA]
    ws.append(header)
    for c in range(1, len(header) + 1):
        ws.cell(row=1, column=c).font = Font(bold=True)
        ws.cell(row=1, column=c).alignment = Alignment(horizontal="center")
    for r in items:
        ws.append([
            r.get("seller_article"),
            r.get("price_ozon"), r.get("price_wb"), r.get("price_ya"),
        ])
    # Цены — целые рубли, выравнивание по правому краю (как в остальных отчётах).
    for row in ws.iter_rows(min_row=2, min_col=2, max_col=4):
        for cell in row:
            cell.number_format = "#,##0"
    for col, w in zip("ABCD", (24, 16, 16, 16)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"

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


@router.post("/import")
def prices_import(
    file: UploadFile = File(...),
    user=Depends(auth.require_admin),
):
    """Импорт базовых цен из Excel. Только администратор.

    Столбцы цен определяются по шапке (первая строка): заголовок, содержащий
    «ozon» → Ozon, «yandex» → Yandex, «wb»/«wildberries» → Wildberries.
    Артикул — первый столбец. Прочие столбцы не читаются, поэтому файл с
    дополнительными справочными колонками тоже грузится.

    Пустая ячейка цены ⇒ цена по этому МП удаляется (base_price = NULL) —
    та же семантика, что у пустой ячейки месяца в плане продаж.

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
    col_mp = {}          # индекс столбца -> 'Ozon' | 'Wildberries'
    for ci, h in enumerate(header):
        if ci == 0 or h is None:
            continue
        s = str(h).strip().lower()
        if "ozon" in s or "озон" in s:
            col_mp.setdefault(ci, "Ozon")
        elif "yandex" in s or "яндекс" in s:
            col_mp.setdefault(ci, "Yandex")
        elif "wb" in s or "wildberries" in s or "вб" in s:
            col_mp.setdefault(ci, "Wildberries")
    if not col_mp:
        raise HTTPException(
            status_code=422,
            detail="В шапке файла не найдены столбцы цен «%s», «%s», «%s»" % (COL_OZON, COL_WB, COL_YA),
        )

    def _price(v):
        """'' / None → None (удалить). Иначе целое ≥ 0 или ошибка."""
        if v is None:
            return None
        s = str(v).strip()
        if s == "" or s == "-" or s == "—":
            return None
        s = s.replace("\u00a0", "").replace(" ", "").replace(",", ".")
        try:
            f = float(s)
        except (ValueError, TypeError):
            raise ValueError(s)
        if f < 0 or f != f:
            raise ValueError(s)
        return int(round(f))

    # Известные артикулы: новые SKU через прайс-лист не создаём.
    known = {r["seller_article"] for r in db.query_all(
        """SELECT seller_article FROM catalog_items
            WHERE seller_article IS NOT NULL AND seller_article <> ''
           UNION
           SELECT seller_article FROM catalog_marketplace
            WHERE seller_article IS NOT NULL AND seller_article <> ''"""
    )}

    uid = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    upserts = []          # (art, mp, price, uid)
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
            try:
                p = _price(raw)
            except ValueError as e:
                if len(errors) < 50:
                    errors.append("%s / %s: %s" % (art, mp, e))
                continue
            upserts.append((art, mp, p, uid))

    if errors:
        raise HTTPException(
            status_code=422,
            detail="Некорректные значения цен (%d): %s" % (len(errors), "; ".join(errors[:10])),
        )

    if upserts:
        # Всё в одной транзакции: витрина + история. История пополняется только
        # для тех строк, где значение реально меняется (иначе — шум на тысячи повторов).
        with db.transaction() as tx:
            # 1) Витрина — пакетный UPSERT.
            tx.execute_values(
                """INSERT INTO catalog_base_prices
                       (seller_article, marketplace, base_price, updated_at, updated_by_user_id)
                   VALUES %s
                   ON CONFLICT (seller_article, marketplace) DO UPDATE SET
                       base_price = EXCLUDED.base_price,
                       updated_at = now(),
                       updated_by_user_id = EXCLUDED.updated_by_user_id""",
                upserts,
                template="(%s,%s,%s,now(),%s)",
            )
            # 2) История. Сначала читаем активные ряды по (SKU, MP), чтобы отфильтровать
            # неизменённые значения.
            keys = list({(u[0], u[1]) for u in upserts})
            active = tx.query_all(
                """SELECT seller_article, marketplace, base_price
                     FROM catalog_base_prices_hist
                    WHERE valid_to IS NULL
                      AND (seller_article, marketplace) IN (SELECT unnest(%s::text[]), unnest(%s::text[]))""",
                ([k[0] for k in keys], [k[1] for k in keys]),
            )
            active_map = {(r["seller_article"], r["marketplace"]): r["base_price"] for r in active}
            changed = [u for u in upserts if active_map.get((u[0], u[1])) != u[2]]
            if changed:
                # Закрыть текущие ряды одним запросом.
                tx.execute(
                    """UPDATE catalog_base_prices_hist SET valid_to = now()
                        WHERE valid_to IS NULL
                          AND (seller_article, marketplace) IN (
                              SELECT unnest(%s::text[]), unnest(%s::text[]))""",
                    ([c[0] for c in changed], [c[1] for c in changed]),
                )
                # Вставить новые ряды пачкой.
                tx.execute_values(
                    """INSERT INTO catalog_base_prices_hist
                           (seller_article, marketplace, base_price, valid_from, valid_to, updated_by_user_id)
                       VALUES %s""",
                    [(c[0], c[1], c[2], c[3]) for c in changed],
                    template="(%s,%s,%s,now(),NULL,%s)",
                )

    filled = sum(1 for u in upserts if u[2] is not None)
    return {
        "ok": True,
        "articles": len(seen),
        "prices_set": filled,
        "prices_cleared": len(upserts) - filled,
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
    user=Depends(auth.get_current_user),
):
    """Отчёт Ozon: даты, цена после акций, соинвест и покупатель из файла."""
    return _do_dated_price_upload("Ozon", file, user)


@router.post("/upload_wb")
def upload_wb_prices(
    file: UploadFile = File(...),
    user=Depends(auth.get_current_user),
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
    user=Depends(auth.get_current_user),
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
