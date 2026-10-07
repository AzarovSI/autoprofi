# -*- coding: utf-8 -*-
"""Загрузка отчётов MPPROFIT (Ozon/WB) и журнал загрузок."""
import os
import tempfile

import psycopg
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException

from .. import db, auth
from .. import cache
from .. import loader_core

# Пространство имён кэша дерева РНП-продаж (должно совпадать с rnp_sales_router.CACHE_NS).
# После любой загрузки, меняющей ozon_daily_sales, бампаем версию — тогда
# закэшированные деревья пересчитаются при следующем запросе.
RNP_TREE_CACHE_NS = "rnp_sales_tree"
# Отдельное пространство имён кэша дерева РНП-заказов Wildberries
# (данные WB в отдельной таблице wb_daily_sales; должно совпадать с
# rnp_sales_router.CACHE_NS_WB).
RNP_TREE_CACHE_NS_WB = "rnp_sales_tree_wb"
# Отдельное пространство имён кэша дерева РНП-заказов Яндекс
# (данные в отдельной таблице ya_daily_sales; должно совпадать с
# rnp_sales_router.CACHE_NS_YA).
RNP_TREE_CACHE_NS_YA = "rnp_sales_tree_ya"
from .. import ozon_daily_loader
from .. import ozon_ads_loader
from .. import ozon_stock_loader          # общий склад АВТОПРОФИ 1С (TDSheet -> stock_daily, РНП)
from .. import ozon_wh_stock_loader       # остатки Ozon по складам (Товар-склад -> ozon_stock_daily, стоим.оценка)
from .. import ozon_reviews_loader
from .. import ozon_priceva_loader
from .. import wb_daily_loader
from .. import wb_reviews_loader
from .. import wb_ads_loader
from .. import wb_priceva_loader
from .. import wb_pi_loader
from .. import wb_stock_loader
from .. import ya_daily_loader
from .. import ya_stock_loader          # ежедневные остатки склада Яндекс (FBY) -> ya_daily_sales.stock_ya_qty
from .. import cost_loader

router = APIRouter(prefix="/api/upload", tags=["upload"])

MP_KEY = {"ozon": "ozon", "wildberries": "wb", "wb": "wb",
          "yandex": "yandex", "ya": "yandex"}


def _set_uploader(conn, user):
    """Проставить sessionную переменную PostgreSQL app.uploader_id для DEFAULT-выражения
    колонки report_uploads.uploaded_by_user_id.

    Все INSERT'ы в report_uploads (в 16 loader-модулях) НЕ трогаем: колонка
    uploaded_by_user_id имеет DEFAULT = NULLIF(current_setting('app.uploader_id', true), '')::int,
    поэтому автоматически подтянется user_id, установленный здесь.

    Ошибка при установке — молча игнорируем: журнал загрузки должен работать даже
    если что-то с сессионной переменной не так (uploaded_by_user_id останется NULL).
    """
    if not user or not isinstance(user, dict):
        return
    uid = user.get("id")
    if uid is None:
        return
    try:
        with conn.cursor() as cur:
            # set_config(name, value, is_local=false) — как SET SESSION, живёт до
            # закрытия соединения. Соединение здесь одноразовое (создаётся в endpoint'е
            # и закрывается в finally), так что LOCAL/SESSION эквивалентны.
            cur.execute("SELECT set_config('app.uploader_id', %s, false)", (str(int(uid)),))
    except Exception:
        # Не мешаем основной загрузке, если что-то пошло не так с сессионной переменной.
        pass



@router.get("/history")
def history(kind: str = "all", user=Depends(auth.get_current_user)):
    """Журнал загрузок (бывший лист «Лог»).
    kind: 'all' (все) | 'unit_econ' (только MPPROFIT: weekly+monthly — журнал юнит-экономики)
          | 'weekly' | 'monthly' | 'daily' (дневной API-файл Ozon)
          | 'ads' (отчёт рекламы Ozon) | 'stock' (отчёт остатков АВТОПРОФИ)
          | 'reviews' | 'competitors'.
    Групповые наборы для трёх блоков «Загрузка данных» (РНП заказы):
          | 'rnps_ozon'   — отчёты для РНП заказы Ozon (daily, ads, reviews, competitors)
          | 'rnps_common' — общие отчёты (stock — остатки АВТОПРОФИ, общие по Ozon и WB)
          | 'rnps_wb'     — отчёты для РНП заказы Wildberries (wb_daily, wb_ads, wb_reviews, wb_stock, wb_pi)
          | 'rnps'        — старый объединённый набор (оставлен для совместимости)."""
    # Журналы раздела «Склады» (kind='wb_stock' и kind='cost') — доступны ВСЕМ
    # авторизованным (user+admin), т.к. раздел «Склады» открыт всем.
    # ВСЕ ОСТАЛЬНЫЕ kind (Журналы РНП, недельные/месячные и т.д.) — только admin.
    if kind not in ("wb_stock", "cost"):
        auth.require_admin(user)
    where = ""
    params = ()
    # Набор period_kind для РНП заказы WB (Воронка/Реклама/Рейтинг/Pi).
    # wb_stock (остатки по складам) относится к разделу «Склады» и в журнал
    # РНП заказы НЕ входит — у него отдельный фильтр kind='wb_stock'.
    WB_KINDS = ('wb_daily', 'wb_ads', 'wb_reviews', 'wb_pi')
    # Набор period_kind для РНП заказы Яндекс (пока только «Аналитика продаж»).
    YA_KINDS = ('ya_daily', 'ya_stock')
    if kind == "weekly":
        where = "WHERE period_kind = 'weekly'"
    elif kind == "monthly":
        where = "WHERE period_kind = 'monthly'"
    elif kind == "daily":
        where = "WHERE period_kind = 'daily'"
    elif kind == "ads":
        where = "WHERE period_kind = 'ads'"
    elif kind == "stock":
        where = "WHERE period_kind = 'stock'"
    elif kind == "competitors":
        where = "WHERE period_kind = 'competitors'"
    elif kind == "reviews":
        where = "WHERE period_kind = 'reviews'"
    elif kind == "wb_stock":
        where = "WHERE period_kind = 'wb_stock'"
    elif kind == "cost":
        where = "WHERE period_kind = 'cost'"
    elif kind == "rnps_ozon":
        where = "WHERE period_kind IN ('daily','ads','reviews','competitors')"
    elif kind == "rnps_common":
        where = "WHERE period_kind = 'stock'"
    elif kind == "rnps_wb":
        where = "WHERE period_kind = ANY(%s)"
        params = (list(WB_KINDS),)
    elif kind == "rnps_ya":
        where = "WHERE period_kind = ANY(%s)"
        params = (list(YA_KINDS),)
    elif kind == "rnps":
        where = "WHERE period_kind IN ('daily','ads','stock','reviews','competitors')"
    elif kind == "unit_econ":
        # Раздел «РНП юнит-экономика» грузит ТОЛЬКО отчёты MPPROFIT
        # (недельные и месячные). Дневные/рекламные/остатки и т.п. относятся
        # к другим разделам и в этот журнал попадать не должны.
        where = "WHERE period_kind IN ('weekly','monthly')"
    rows = db.query_all(
        f"""
        SELECT ru.id, ru.marketplace, ru.week, ru.month, ru.year, ru.period_text,
               ru.period_kind, ru.source_file, ru.rows_loaded, ru.status, ru.message,
               ru.uploaded_at,
               u.display_name AS uploaded_by_name
        FROM report_uploads ru
        LEFT JOIN app_users u ON u.id = ru.uploaded_by_user_id
        {where.replace('WHERE period_kind', 'WHERE ru.period_kind') if where else where}
        ORDER BY ru.id DESC
        LIMIT 100
        """,
        params
    )
    for r in rows:
        if r.get("uploaded_at"):
            r["uploaded_at"] = r["uploaded_at"].isoformat()
    return rows


@router.post("/report")
def upload_report(marketplace: str = Form(...), file: UploadFile = File(...),
                  fact_through_date: str = Form(None),
                  user=Depends(auth.require_admin)):
    """Приём Excel-отчёта, нормализация и запись в fact_weekly (перезапись недели).

    fact_through_date («Факт по дату», YYYY-MM-DD) — необязательное поле, имеет
    смысл ТОЛЬКО для месячного отчёта: до какого числа месяца собран факт. Влияет
    на прогноз выполнения плана в разделе «Продажи, шт.». Для недельных отчётов
    игнорируется. Пустое/невалидное значение — мягко пропускается.
    """
    mp_key = MP_KEY.get(marketplace.lower())
    if not mp_key:
        raise HTTPException(status_code=400, detail="Неизвестный маркетплейс")

    # Сохраняем во временный файл (loader читает по пути).
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file.file.read())
        # autocommit не задаём: loader_core.load_report сам управляет транзакцией
        # (commit/rollback), как и при psycopg2 (там connect тоже без автокоммита).
        conn = psycopg.connect(db.DB_DSN, connect_timeout=15)
        try:
            _set_uploader(conn, user)
            res = loader_core.load_report(conn, tmp_path, mp_key)
        finally:
            conn.close()
        # Совместимость: load_report теперь возвращает dict.
        ok = res.get("ok") if isinstance(res, dict) else bool(res)
        if not ok:
            # Причина — из возвращённого dict или из последней записи журнала.
            detail = (res.get("error") if isinstance(res, dict) else None)
            if not detail:
                last = db.query_one(
                    "SELECT status, message FROM report_uploads ORDER BY id DESC LIMIT 1")
                detail = (last or {}).get("message") or "Ошибка формата отчёта"
            raise HTTPException(status_code=422, detail=detail)
        # «Факт по дату»: для месячного отчёта записываем дату в журнал загрузок.
        # Недельные отчёты — игнорируем. Невалидную дату молча пропускаем.
        upload_id = res.get("upload_id")
        if res.get("period_kind") == "monthly" and upload_id and fact_through_date:
            try:
                import datetime as _dt
                d = _dt.date.fromisoformat(fact_through_date.strip())
                db.execute(
                    "UPDATE report_uploads SET fact_through_date = %s WHERE id = %s",
                    (d, upload_id),
                )
            except (ValueError, TypeError):
                pass
        # Успех: возвращаем распознанный тип и человекочитаемый период.
        return {
            "ok": True,
            "marketplace": res.get("marketplace"),
            "period_kind": res.get("period_kind"),
            "kind_label": res.get("kind_label"),
            "period_human": res.get("period_human"),
            "period_text": res.get("period_text"),
            "week": res.get("week"),
            "month": res.get("month"),
            "year": res.get("year"),
            "rows_loaded": res.get("rows_loaded"),
            "deleted": res.get("deleted"),
            "upload_id": res.get("upload_id"),
            "skipped": res.get("skipped"),
            "warning": res.get("warning"),
        }
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@router.post("/ozon_daily")
def upload_ozon_daily(file: UploadFile = File(...),
                      user=Depends(auth.require_admin)):
    """Приём дневного API-файла Ozon (лист «Данные», 16 колонок) и запись в
    ozon_daily_sales (перезапись диапазона дат из файла).

    Формат отличается от MPPROFIT-отчётов (дневной, не недельный/месячный),
    поэтому обрабатывается отдельным парсером ozon_daily_loader.
    Возвращает {ok, rows_loaded, dates, unmatched, ...}.
    """
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file.file.read())
        conn = psycopg.connect(db.DB_DSN, connect_timeout=15)
        try:
            _set_uploader(conn, user)
            res = ozon_daily_loader.load_ozon_daily(conn, tmp_path)
        finally:
            conn.close()
        if not res.get("ok"):
            detail = res.get("error") or "Ошибка формата файла Ozon"
            raise HTTPException(status_code=422, detail=detail)
        cache.bump(RNP_TREE_CACHE_NS)  # данные Ozon изменились → сбросить кэш дерева
        # Хук: пересчитать СПП/buyer в mp_price_daily для всех дат >= min(dates).
        # Даты в res отсортированы (ISO-строки).
        try:
            from .. import price_history
            dts = res.get("dates") or []
            if dts:
                price_history.recalc_spp_and_buyer_from("Ozon", dts[0])
        except Exception as e:
            # Не роняем загрузку РНП из-за проблемы пересчёта. Логируем.
            import logging; logging.getLogger("prices").warning("recalc SPP Ozon failed: %s", e)
        return {
            "ok": True,
            "marketplace": res.get("marketplace"),
            "rows_loaded": res.get("rows_loaded"),
            "deleted": res.get("deleted"),
            "dates": res.get("dates"),
            "period_text": res.get("period_text"),
            "unmatched": res.get("unmatched"),
            "new_articles": res.get("new_articles"),
            "upload_id": res.get("upload_id"),
        }
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@router.post("/ozon_stock")
def upload_ozon_stock(file: UploadFile = File(...),
                      stock_date: str = Form(...),
                      user=Depends(auth.require_admin)):
    """Приём ОБЩЕГО отчёта ОСТАТКОВ склада АВТОПРОФИ (МСК)
    из 1С (лист «TDSheet»: Артикул / Наименование / Остаток).

    Даты в файле нет — она передаётся полем stock_date (YYYY-MM-DD).
    Остатки пишутся в общую таблицу stock_daily (единый источник
    для всех отчётов) и синхронизируются в ozon_daily_sales.stock_ap_qty
    для существующих строк продаж за эту дату.
    """
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file.file.read())
        conn = psycopg.connect(db.DB_DSN, connect_timeout=15)
        try:
            _set_uploader(conn, user)
            res = ozon_stock_loader.load_ozon_stock(conn, tmp_path, stock_date)
        finally:
            conn.close()
        if not res.get("ok"):
            detail = res.get("error") or "Ошибка формата отчёта остатков"
            raise HTTPException(status_code=422, detail=detail)
        # Остаток склада АВТОПРОФИ (stock_daily) — ОБЩИЙ источник для всех трёх
        # МП: Ozon берёт его из синхронизированной ozon_daily_sales.stock_ap_qty,
        # а WB и Яндекс — через LEFT JOIN stock_daily по дате+артикулу. Поэтому
        # сбрасываем кэш деревьев ВСЕХ трёх МП, иначе у WB/Яндекса остаток
        # АВТОПРОФИ «замерзает» на дате последней загрузки их продаж (баг: у
        # Яндекса пропадали остатки за последние дни, пока дерево не пересоберётся).
        cache.bump(RNP_TREE_CACHE_NS)     # Ozon
        cache.bump(RNP_TREE_CACHE_NS_WB)  # Wildberries
        cache.bump(RNP_TREE_CACHE_NS_YA)  # Yandex
        return {
            "ok": True,
            "marketplace": res.get("marketplace"),
            "rows_loaded": res.get("rows_loaded"),
            "updated": res.get("updated"),
            "synced_ozon": res.get("synced_ozon"),
            "sku_total": res.get("sku_total"),
            "file_total": res.get("file_total"),
            "skipped_not_in_catalog": res.get("skipped_not_in_catalog"),
            "stock_date": res.get("stock_date"),
            "dates": res.get("dates"),
            "period_text": res.get("period_text"),
            "upload_id": res.get("upload_id"),
        }
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@router.post("/ozon_wh_stock")
def upload_ozon_wh_stock(file: UploadFile = File(...),
                         stock_date: str = Form(...),
                         user=Depends(auth.get_current_user)):
    """Приём отчёта ОСТАТКОВ Ozon ПО СКЛАДАМ (лист «Товар-склад»)
    для раздела «Склады → Озон» (СТОИМОСТНАЯ ОЦЕНКА).

    ОТДЕЛЬНО от общего склада 1С: пишет в ozon_stock_daily / ozon_stock_meta
    и справочник ozon_warehouses. Даты в файле нет — она передаётся полем
    stock_date. НЕ трогает stock_daily и РНП (stock_ap_qty).
    Доступ: все авторизованные (раздел «Склады» открыт всем).
    """
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file.file.read())
        conn = psycopg.connect(db.DB_DSN, connect_timeout=15)
        try:
            _set_uploader(conn, user)
            res = ozon_wh_stock_loader.load_ozon_wh_stock(conn, tmp_path, stock_date)
        finally:
            conn.close()
        if not res.get("ok"):
            detail = res.get("error") or "Ошибка формата отчёта остатков Ozon по складам"
            raise HTTPException(status_code=422, detail=detail)
        return {
            "ok": True,
            "stock_date": res.get("stock_date"),
            "period_text": res.get("period_text"),
            "rows_products": res.get("rows_products"),
            "rows_loaded": res.get("rows_loaded"),
            "rows_stock_cells": res.get("rows_stock_cells"),
            "warehouses_total": res.get("warehouses_total"),
            "warehouses_new": res.get("warehouses_new"),
            "clusters_total": res.get("clusters_total"),
            "arts_total": res.get("arts_total"),
            "arts_new": res.get("arts_new"),
            "arts_new_list": res.get("arts_new_list"),
            "file_rows": res.get("file_rows"),
        }
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@router.post("/wb_stock")
def upload_wb_stock(file: UploadFile = File(...),
                    stock_date: str = Form(...),
                    user=Depends(auth.get_current_user)):
    """Приём отчёта ОСТАТКОВ Wildberries по складам (.xlsx, лист Sheet1).

    Доступ: все авторизованные (раздел «Склады» открыт всем: user+admin).

    Даты в файле нет — она передаётся полем stock_date (YYYY-MM-DD).
    Пишет в «длинную» модель wb_stock_daily (date, seller_article,
    warehouse_id, qty) + wb_stock_meta (в пути, объём, всего).
    Набор колонок складов плавающий: фиксированные ищем по названию,
    всё правее «Всего находится на складах» — склады (справочник
    wb_warehouses пополняется автоматически). Повторная загрузка за ту
    же дату полностью перезаписывает данные дня.
    """
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file.file.read())
        conn = psycopg.connect(db.DB_DSN, connect_timeout=15)
        try:
            _set_uploader(conn, user)
            res = wb_stock_loader.load_wb_stock(conn, tmp_path, stock_date)
        finally:
            conn.close()
        if not res.get("ok"):
            detail = res.get("error") or "Ошибка формата отчёта остатков WB"
            raise HTTPException(status_code=422, detail=detail)
        return {
            "ok": True,
            "stock_date": res.get("stock_date"),
            "period_text": res.get("period_text"),
            "rows_products": res.get("rows_products"),
            "rows_loaded": res.get("rows_loaded"),
            "rows_stock_cells": res.get("rows_stock_cells"),
            "warehouses_total": res.get("warehouses_total"),
            "warehouses_new": res.get("warehouses_new"),
            "skipped_not_in_catalog": res.get("skipped_not_in_catalog"),
            "arts_new": res.get("arts_new"),
            "arts_new_list": res.get("arts_new_list"),
            "file_total": res.get("file_total"),
        }
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@router.post("/ya_stock")
def upload_ya_stock(file: UploadFile = File(...),
                    stock_date: str = Form(...),
                    user=Depends(auth.require_admin)):
    """Приём ежедневных ОСТАТКОВ склада Яндекс.Маркет (FBY).

    Источник — выгрузка «Остатки на складе» (stocks_on_warehouses_*.xlsx).
    Даты в файле нет — она передаётся полем stock_date (YYYY-MM-DD).
    Колонка «Доступно для заказа» (сумма по SKU) → метрика
    «Остаток на складе Яндекс, шт» в «РНП заказы — Yandex»
    (ya_daily_sales.stock_ya_qty). Товары с остатком без строки
    продаж за дату — заводятся строкой-заготовкой.
    НЕ трогает Ozon/WB и остаток склада АВТОПРОФИ (stock_ap_qty).
    """
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file.file.read())
        conn = psycopg.connect(db.DB_DSN, connect_timeout=15)
        try:
            _set_uploader(conn, user)
            res = ya_stock_loader.load_ya_stock(conn, tmp_path, stock_date)
        finally:
            conn.close()
        if not res.get("ok"):
            detail = res.get("error") or "Ошибка формата отчёта остатков Яндекс"
            raise HTTPException(status_code=422, detail=detail)
        # Остаток Яндекс → только дерево Yandex. Сбрасываем его кэш.
        cache.bump(RNP_TREE_CACHE_NS_YA)
        return {
            "ok": True,
            "marketplace": res.get("marketplace"),
            "rows_loaded": res.get("rows_loaded"),
            "updated": res.get("updated"),
            "inserted": res.get("inserted"),
            "sku_total": res.get("sku_total"),
            "file_total": res.get("file_total"),
            "arts_new": res.get("arts_new"),
            "stock_date": res.get("stock_date"),
            "period_text": res.get("period_text"),
            "upload_id": res.get("upload_id"),
        }
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@router.post("/ozon_reviews")
def upload_ozon_reviews(file: UploadFile = File(...),
                        report_date: str = Form(...),
                        user=Depends(auth.require_admin)):
    """Прием отчета ОЗОН «Список товаров» (CSV): рейтинг + отзывы.

    Даты в файле нет — она передается полем report_date (YYYY-MM-DD).
    Обновляет rating и delivery_time_hours (=«Количество отзывов»)
    в ozon_daily_sales для существующих строк продаж за эту дату,
    только по товарам из справочника дашборда (catalog_items).
    """
    suffix = os.path.splitext(file.filename or "")[1] or ".csv"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file.file.read())
        conn = psycopg.connect(db.DB_DSN, connect_timeout=15)
        try:
            _set_uploader(conn, user)
            res = ozon_reviews_loader.load_ozon_reviews(conn, tmp_path, report_date)
        finally:
            conn.close()
        if not res.get("ok"):
            detail = res.get("error") or "Ошибка формата отчета Ozon"
            raise HTTPException(status_code=422, detail=detail)
        cache.bump(RNP_TREE_CACHE_NS)  # рейтинг/отзывы обновились в ozon_daily_sales → сброс кэша
        return {
            "ok": True,
            "marketplace": res.get("marketplace"),
            "rows_loaded": res.get("rows_loaded"),
            "synced_ozon": res.get("synced_ozon"),
            "matched": res.get("matched"),
            "file_total": res.get("file_total"),
            "skipped_not_in_catalog": res.get("skipped_not_in_catalog"),
            "report_date": res.get("report_date"),
            "period_text": res.get("period_text"),
            "upload_id": res.get("upload_id"),
        }
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@router.post("/ozon_priceva")
def upload_ozon_priceva(file: UploadFile = File(...),
                        report_date: str = Form(...),
                        user=Depends(auth.require_admin)):
    """Приём отчёта МОНИТОРИНГА ЦЕН КОНКУРЕНТОВ (PriceVA) для Ozon (.xlsx).

    Даты в файле нет — она передаётся полем report_date (YYYY-MM-DD).
    Пишет comp_price_avg (Средняя цена руб.) и comp_price_min (Мин. цена руб.)
    в ozon_daily_sales за эту дату, по товарам из справочника дашборда
    (catalog_items). Для товаров без строки продаж за дату создаёт
    строку-заглушку (только цены конкурентов).
    """
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file.file.read())
        conn = psycopg.connect(db.DB_DSN, connect_timeout=15)
        try:
            _set_uploader(conn, user)
            res = ozon_priceva_loader.load_ozon_priceva(conn, tmp_path, report_date)
        finally:
            conn.close()
        if not res.get("ok"):
            detail = res.get("error") or "Ошибка формата отчёта цен конкурентов"
            raise HTTPException(status_code=422, detail=detail)
        cache.bump(RNP_TREE_CACHE_NS)  # цены конкурентов обновились → сброс кэша
        return {
            "ok": True,
            "marketplace": res.get("marketplace"),
            "rows_loaded": res.get("rows_loaded"),
            "updated": res.get("updated"),
            "inserted": res.get("inserted"),
            "file_total": res.get("file_total"),
            "skipped_not_in_catalog": res.get("skipped_not_in_catalog"),
            "report_date": res.get("report_date"),
            "dates": res.get("dates"),
            "period_text": res.get("period_text"),
            "upload_id": res.get("upload_id"),
        }
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@router.post("/ozon_ads")
def upload_ozon_ads(file: UploadFile = File(...),
                    user=Depends(auth.require_admin)):
    """Приём отчёта РЕКЛАМЫ Ozon («Аналитика продвижения», лист
    «Statistics») и обновление ads_expense_rub/ctr_pct в ozon_daily_sales
    по ключу (date, sku_ozon).

    Расход суммируется по SKU; CTR агрегируется как клики/показы.
    ДРР из отчёта НЕ берётся (считается на лету в матрице).
    Строки продаж за эту дату должны быть уже загружены дневным
    API-файлом Ozon.
    """
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file.file.read())
        conn = psycopg.connect(db.DB_DSN, connect_timeout=15)
        try:
            _set_uploader(conn, user)
            res = ozon_ads_loader.load_ozon_ads(conn, tmp_path)
        finally:
            conn.close()
        if not res.get("ok"):
            detail = res.get("error") or "Ошибка формата отчёта рекламы Ozon"
            raise HTTPException(status_code=422, detail=detail)
        cache.bump(RNP_TREE_CACHE_NS)  # расходы на рекламу обновились → сброс кэша
        return {
            "ok": True,
            "marketplace": res.get("marketplace"),
            "rows_loaded": res.get("rows_loaded"),
            "updated": res.get("updated"),
            "created": res.get("created"),
            "sku_total": res.get("sku_total"),
            "unmatched": res.get("unmatched"),
            "dates": res.get("dates"),
            "period_text": res.get("period_text"),
            "upload_id": res.get("upload_id"),
        }
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# ============================================================
# WILDBERRIES — отчёты для раздела «РНП заказы».
# Источник — отдельная таблица wb_daily_sales. После любой загрузки
# бампаем RNP_TREE_CACHE_NS_WB (дерево WB кэшируется отдельно).
# ============================================================

@router.post("/wb_daily")
def upload_wb_daily(file: UploadFile = File(...),
                    user=Depends(auth.require_admin)):
    """Приём отчёта «Воронка» Wildberries (лист «TDSheet», 16 колонок)
    и запись в wb_daily_sales (перезапись диапазона дат из файла).

    Заказы/отмены/выкуп/переходы/корзина/конверсии/рейтинг/остаток WB.
    Новые артикулы добавляются в справочник (Wildberries) как нераспределённые.
    """
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file.file.read())
        conn = psycopg.connect(db.DB_DSN, connect_timeout=15)
        try:
            _set_uploader(conn, user)
            res = wb_daily_loader.load_wb_daily(conn, tmp_path)
        finally:
            conn.close()
        if not res.get("ok"):
            detail = res.get("error") or "Ошибка формата отчёта «Воронка» WB"
            raise HTTPException(status_code=422, detail=detail)
        cache.bump(RNP_TREE_CACHE_NS_WB)  # данные WB изменились → сброс кэша дерева WB
        # Хук: пересчитать СПП/buyer в mp_price_daily для всех дат >= min(dates).
        try:
            from .. import price_history
            dts = res.get("dates") or []
            if dts:
                price_history.recalc_spp_and_buyer_from("Wildberries", dts[0])
        except Exception as e:
            import logging; logging.getLogger("prices").warning("recalc SPP WB failed: %s", e)
        return {
            "ok": True,
            "marketplace": res.get("marketplace"),
            "rows_loaded": res.get("rows_loaded"),
            "deleted": res.get("deleted"),
            "dates": res.get("dates"),
            "period_text": res.get("period_text"),
            "unmatched": res.get("unmatched"),
            "new_articles": res.get("new_articles"),
            "upload_id": res.get("upload_id"),
        }
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@router.post("/wb_reviews")
def upload_wb_reviews(file: UploadFile = File(...),
                      user=Depends(auth.require_admin)):
    """Приём отчёта «Рейтинг» Wildberries (лист «TDSheet») → Количество отзывов.

    Дата берётся из колонки «Дата» самого отчёта (она есть в каждой
    строке), поэтому поле даты в форме не требуется.
    Пишет reviews_qty в wb_daily_sales по (date, seller_article).
    Рейтинг из этого отчёта НЕ берём (он уже из «Воронки»).
    По товарам из справочника дашборда.
    """
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file.file.read())
        conn = psycopg.connect(db.DB_DSN, connect_timeout=15)
        try:
            _set_uploader(conn, user)
            res = wb_reviews_loader.load_wb_reviews(conn, tmp_path)
        finally:
            conn.close()
        if not res.get("ok"):
            detail = res.get("error") or "Ошибка формата отчёта «Рейтинг» WB"
            raise HTTPException(status_code=422, detail=detail)
        cache.bump(RNP_TREE_CACHE_NS_WB)  # отзывы обновились → сброс кэша WB
        return {
            "ok": True,
            "marketplace": res.get("marketplace"),
            "rows_loaded": res.get("rows_loaded"),
            "updated": res.get("updated"),
            "inserted": res.get("inserted"),
            "file_total": res.get("file_total"),
            "skipped_not_in_catalog": res.get("skipped_not_in_catalog"),
            "report_date": res.get("report_date"),
            "period_text": res.get("period_text"),
            "upload_id": res.get("upload_id"),
        }
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@router.post("/wb_ads")
def upload_wb_ads(file: UploadFile = File(...),
                  user=Depends(auth.require_admin)):
    """Приём отчёта «Статистика рекламных кампаний» Wildberries.

    Несколько строк на (дата+артикул) = разные кампании; агрегируем по дню:
    ads_expense_rub = ΣЗатраты; ctr_pct = Σclicks/Σviews (ДОЛЯ 0..1; ПЕРЕСЧЁТ, не сумма).
    Обновляет строки wb_daily_sales по (date, seller_article).
    """
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file.file.read())
        conn = psycopg.connect(db.DB_DSN, connect_timeout=15)
        try:
            _set_uploader(conn, user)
            res = wb_ads_loader.load_wb_ads(conn, tmp_path)
        finally:
            conn.close()
        if not res.get("ok"):
            detail = res.get("error") or "Ошибка формата отчёта рекламы WB"
            raise HTTPException(status_code=422, detail=detail)
        cache.bump(RNP_TREE_CACHE_NS_WB)  # расходы/CTR обновились → сброс кэша WB
        return {
            "ok": True,
            "marketplace": res.get("marketplace"),
            "rows_loaded": res.get("rows_loaded"),
            "updated": res.get("updated"),
            "inserted": res.get("inserted"),
            "sku_total": res.get("sku_total"),
            "skipped_not_in_catalog": res.get("skipped_not_in_catalog"),
            "dates": res.get("dates"),
            "period_text": res.get("period_text"),
            "upload_id": res.get("upload_id"),
        }
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@router.post("/wb_priceva")
def upload_wb_priceva(file: UploadFile = File(...),
                      report_date: str = Form(...),
                      user=Depends(auth.require_admin)):
    """Приём отчёта МОНИТОРИНГА ЦЕН КОНКУРЕНТОВ (PriceVA) для Wildberries (.xlsx).

    Отчёт по формату ТОТ ЖЕ, что и у Ozon, но ДАННЫЕ (цены конкурентов) —
    для площадки WB. Даты в файле нет — она передаётся полем report_date
    (YYYY-MM-DD). Пишет comp_price_avg (Средняя цена руб.) и comp_price_min
    (Мин. цена руб.) в wb_daily_sales за эту дату, по товарам из справочника
    дашборда (catalog_items). Для товаров без строки продаж за дату создаёт
    строку-заглушку (только цены конкурентов).
    """
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file.file.read())
        conn = psycopg.connect(db.DB_DSN, connect_timeout=15)
        try:
            _set_uploader(conn, user)
            res = wb_priceva_loader.load_wb_priceva(conn, tmp_path, report_date)
        finally:
            conn.close()
        if not res.get("ok"):
            detail = res.get("error") or "Ошибка формата отчёта цен конкурентов WB"
            raise HTTPException(status_code=422, detail=detail)
        cache.bump(RNP_TREE_CACHE_NS_WB)  # цены конкурентов обновились → сброс кэша WB
        return {
            "ok": True,
            "marketplace": res.get("marketplace"),
            "rows_loaded": res.get("rows_loaded"),
            "updated": res.get("updated"),
            "inserted": res.get("inserted"),
            "file_total": res.get("file_total"),
            "skipped_not_in_catalog": res.get("skipped_not_in_catalog"),
            "report_date": res.get("report_date"),
            "dates": res.get("dates"),
            "period_text": res.get("period_text"),
            "upload_id": res.get("upload_id"),
        }
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@router.post("/wb_pi")
def upload_wb_pi(file: UploadFile = File(...),
                 report_date: str = Form(...),
                 user=Depends(auth.require_admin)):
    """Приём отчёта «ИНДЕКС ЦЕН» для Wildberries (.xlsx).

    Раздел ЛК WB «Товары и цены → Индекс цен». За день выгружаются ДВА
    файла («товары с выгодной ценой» и «товары с высокой ценой») —
    грузятся ОТДЕЛЬНО за одну дату. Даты в файле нет — она передаётся
    полем report_date (YYYY-MM-DD). Пишет price_index_pi в wb_daily_sales
    (Pi = Цена товара на WB / Цена идентичного товара, коэффициент),
    по товарам из справочника дашборда. Для товаров без строки продаж
    за дату создаёт строку-заглушку (только Pi).
    """
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file.file.read())
        conn = psycopg.connect(db.DB_DSN, connect_timeout=15)
        try:
            _set_uploader(conn, user)
            res = wb_pi_loader.load_wb_pi(conn, tmp_path, report_date)
        finally:
            conn.close()
        if not res.get("ok"):
            detail = res.get("error") or "Ошибка формата отчёта «Индекс цен» WB"
            raise HTTPException(status_code=422, detail=detail)
        cache.bump(RNP_TREE_CACHE_NS_WB)  # индекс цен обновился → сброс кэша WB
        return {
            "ok": True,
            "marketplace": res.get("marketplace"),
            "rows_loaded": res.get("rows_loaded"),
            "updated": res.get("updated"),
            "inserted": res.get("inserted"),
            "file_total": res.get("file_total"),
            "skipped_not_in_catalog": res.get("skipped_not_in_catalog"),
            "skipped_no_pi": res.get("skipped_no_pi"),
            "report_date": res.get("report_date"),
            "dates": res.get("dates"),
            "period_text": res.get("period_text"),
            "upload_id": res.get("upload_id"),
        }
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# ============================================================
# ЗАГРУЗКА ОТЧЁТОВ ЯНДЕКС.МАРКЕТ (РНП заказы Яндекс)
# Источник — отдельная таблица ya_daily_sales. После любой загрузки
# бампаем RNP_TREE_CACHE_NS_YA (дерево Яндекс кэшируется отдельно).
# ============================================================

@router.post("/ya_daily")
def upload_ya_daily(file: UploadFile = File(...),
                    user=Depends(auth.require_admin)):
    """Приём отчёта «Аналитика продаж» Яндекс.Маркета (лист
    «Аналитика продаж», 29 колонок) и запись в ya_daily_sales
    (перезапись диапазона дат из файла).

    Показы/клики/корзина/заказы/отмены/конверсии; CTR считается (клики/показы).
    Новые артикулы добавляются в справочник (Yandex) как нераспределённые.
    """
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file.file.read())
        conn = psycopg.connect(db.DB_DSN, connect_timeout=15)
        try:
            _set_uploader(conn, user)
            res = ya_daily_loader.load_ya_daily(conn, tmp_path)
        finally:
            conn.close()
        if not res.get("ok"):
            detail = res.get("error") or "Ошибка формата отчёта «Аналитика продаж» Яндекс"
            raise HTTPException(status_code=422, detail=detail)
        cache.bump(RNP_TREE_CACHE_NS_YA)  # данные Яндекс изменились → сброс кэша дерева Яндекс
        return {
            "ok": True,
            "marketplace": res.get("marketplace"),
            "rows_loaded": res.get("rows_loaded"),
            "deleted": res.get("deleted"),
            "dates": res.get("dates"),
            "period_text": res.get("period_text"),
            "unmatched": res.get("unmatched"),
            "new_articles": res.get("new_articles"),
            "upload_id": res.get("upload_id"),
        }
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@router.post("/cost")
def upload_cost(file: UploadFile = File(...),
                start_date: str = Form(None),
                user=Depends(auth.get_current_user)):
    """Приём справочника СЕБЕСТОИМОСТИ товаров (.xlsx, лист TDSheet).

    Выгрузка из учётной системы: колонки «Артикул» и «с/с расчетная»
    (это и есть себестоимость). start_date (ISO YYYY-MM-DD) — дата, С
    КОТОРОЙ действует эта себестоимость (до следующей загрузки).
    Запись историчная (item_cost_hist), idempotent overwrite по (артикул,
    дата). Себестоимость пишется ТОЛЬКО по товарам из
    справочника системы (catalog_items). Сопоставление — по
    canon_article (гомоглифы + UPPER).
    """
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file.file.read())
        conn = psycopg.connect(db.DB_DSN, connect_timeout=15)
        try:
            _set_uploader(conn, user)
            res = cost_loader.load_cost(conn, tmp_path, start_date=start_date)
        finally:
            conn.close()
        if not res.get("ok"):
            detail = res.get("error") or "Ошибка формата файла себестоимости"
            raise HTTPException(status_code=422, detail=detail)
        return {
            "ok": True,
            "marketplace": res.get("marketplace"),
            "rows_loaded": res.get("rows_loaded"),
            "file_total": res.get("file_total"),
            "skipped_not_in_catalog": res.get("skipped_not_in_catalog"),
            "skipped_no_cost": res.get("skipped_no_cost"),
            "skipped_empty": res.get("skipped_empty"),
            "start_date": res.get("start_date"),
            "upload_id": res.get("upload_id"),
        }
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
