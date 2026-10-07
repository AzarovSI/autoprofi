# -*- coding: utf-8 -*-
"""Загрузчик СЕБЕСТОИМОСТИ товаров (справочник «с/с расчетная»).

Источник — выгрузка из учётной системы ТД «АВТОПРОФИ» (лист TDSheet).
Даты в модели нет: себестоимость — статичный справочник, каждая новая
загрузка ПЕРЕЗАПИСЫВАЕТ значения по совпавшим артикулам (overwrite by
seller_article).

Формат файла (заголовки в строке 1, поиск колонок ПО ИМЕНИ):
    • «Артикул»        → seller_article;
    • «с/с расчетная»  → cost_calc (это и есть себестоимость);
    • «с/с РФ»         → cost_rf   (доп. колонка, если заполнена).

Особенность: WB/1С-выгрузки часто пишут служебные имена в zip с иным
регистром (SharedStrings.xml). openpyxl на таких падает — читаем через
python_calamine (как wb_daily_loader / wb_ads_loader).

Сопоставление артикулов — util.canon_article (trim + схлоп пробелов +
гомоглифы + UPPER). Пишем себестоимость ТОЛЬКО по товарам, которые есть
в общем справочнике дашборда (catalog_items). Прочие строки файла —
пропускаем (skipped_not_in_catalog).

Запись в item_cost (UPSERT по seller_article): пишем оригинальный
(канонический) артикул из справочника, cost_calc, cost_rf, updated_at.

Каждая загрузка фиксируется в report_uploads (marketplace='ТД АВТОПРОФИ',
period_kind='cost').
"""
import os
import datetime

from python_calamine import CalamineWorkbook

from . import util

MP_NAME = "ТД АВТОПРОФИ"
PERIOD_KIND = "cost"
SHEET_NAME = "TDSheet"
HEADER_ROW = 0  # calamine — 0-based индекс строки заголовков

# Заголовки колонок — проверка формата ПО ИМЕНИ (не по позиции).
ART_HEADERS = ["Артикул", "Артикул продавца", "Артикул поставщика"]
COST_HEADERS = ["с/с расчетная", "с/с расчётная", "Расчетная с/с",
                "Расчётная с/с", "Себестоимость расчетная", "Себестоимость"]
COSTRF_HEADERS = ["с/с РФ", "с/с рф", "Себестоимость РФ"]


def _to_num(v):
    """Число из ячейки; пусто/«-»/«·»/нечисло → None."""
    if v is None or v == "" or v in ("-", "·", "—"):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    if s in ("", "-", "·", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _read_rows(path):
    """Прочитать лист (TDSheet или первый) через calamine → list[list]."""
    wb = CalamineWorkbook.from_path(path)
    names = wb.sheet_names
    name = SHEET_NAME if SHEET_NAME in names else names[0]
    return wb.get_sheet_by_name(name).to_python()


def _find_col(header, candidates):
    """Найти индекс колонки по списку допустимых названий (первое вхождение)."""
    norm = {}
    for i, h in enumerate(header):
        if h is not None and str(h).strip():
            norm.setdefault(str(h).strip(), i)
    for name in candidates:
        if name in norm:
            return norm[name]
    return None


def _journal(conn, path, *, status, message="", rows_loaded=0, upload_id=None,
             start_date=None):
    """Создать/обновить запись в report_uploads (period_kind='cost')."""
    period_text = "справочник с/с"
    if start_date is not None:
        try:
            period_text = "с/с с " + (start_date.isoformat()
                                     if hasattr(start_date, "isoformat")
                                     else str(start_date)[:10])
        except Exception:
            pass
    try:
        with conn.cursor() as cur:
            if upload_id is None:
                cur.execute(
                    "INSERT INTO report_uploads (marketplace, year, period_text, "
                    "source_file, rows_loaded, status, message, period_kind) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (MP_NAME, datetime.date.today().year, period_text,
                     os.path.basename(path), rows_loaded, status,
                     (message or "")[:500], PERIOD_KIND))
                upload_id = cur.fetchone()[0]
            else:
                cur.execute(
                    "UPDATE report_uploads SET rows_loaded=%s, status=%s, "
                    "message=%s WHERE id=%s",
                    (rows_loaded, status, (message or "")[:500], upload_id))
        conn.commit()
        return upload_id
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return upload_id


def load_cost(conn, path, start_date=None):
    """Загрузить справочник себестоимости.

    conn — psycopg-соединение (без автокоммита).
    start_date — дата (date или ISO-строка), С КОТОРОЙ действует эта себестоимость
      (до следующей загрузки). Пишется в item_cost_hist с этой датой (историчность).
      Если None — берётся сегодняшняя дата.
    Возвращает dict {ok, rows_loaded, file_total, skipped_not_in_catalog,
    skipped_empty, skipped_no_cost, ...}. Пишет в item_cost_hist (overwrite by
    (seller_article, start_date)) И в item_cost (последнее актуальное значение
    для обратной совместимости) только по товарам из catalog_items.
    """
    # Нормализация даты действия.
    if start_date is None:
        start_date = datetime.date.today()
    elif isinstance(start_date, str):
        try:
            start_date = datetime.date.fromisoformat(start_date.strip()[:10])
        except ValueError:
            start_date = datetime.date.today()
    try:
        rows = _read_rows(path)
    except Exception as exc:
        msg = "Не удалось прочитать файл себестоимости (проверьте формат .xlsx)."
        _journal(conn, path, status="ОШИБКА", message=str(exc))
        return {"ok": False, "error": msg}

    if not rows:
        msg = "Файл пуст."
        _journal(conn, path, status="ОШИБКА ФОРМАТА", message=msg)
        return {"ok": False, "error": msg}

    header = rows[HEADER_ROW]
    c_art = _find_col(header, ART_HEADERS)
    c_cost = _find_col(header, COST_HEADERS)
    c_rf = _find_col(header, COSTRF_HEADERS)
    missing = []
    if c_art is None:
        missing.append("Артикул")
    if c_cost is None:
        missing.append("с/с расчетная")
    if missing:
        msg = ("ОШИБКА ФОРМАТА: не найдены колонки в строке 1: "
               + ", ".join(missing))
        _journal(conn, path, status="ОШИБКА ФОРМАТА", message=msg)
        return {"ok": False, "error": msg}

    # --- сбор canon(article) -> (cost_calc, cost_rf); последнее при дублях ---
    data = {}  # art_canon -> (cost_calc float|None, cost_rf float|None)
    skipped_empty = 0
    skipped_no_cost = 0
    for r in rows[HEADER_ROW + 1:]:
        if c_art >= len(r):
            skipped_empty += 1
            continue
        art_raw = r[c_art]
        if art_raw is None or not str(art_raw).strip():
            skipped_empty += 1
            continue
        art_c = util.canon_article(art_raw)
        if not art_c:
            skipped_empty += 1
            continue
        cost = _to_num(r[c_cost]) if c_cost < len(r) else None
        cost_rf = _to_num(r[c_rf]) if (c_rf is not None and c_rf < len(r)) else None
        if cost is None and cost_rf is None:
            skipped_no_cost += 1
            continue
        data[art_c] = (cost, cost_rf)

    if not data:
        msg = "В файле нет ни одной валидной строки с себестоимостью."
        _journal(conn, path, status="ОШИБКА ФОРМАТА", message=msg)
        return {"ok": False, "error": msg}

    upload_id = _journal(conn, path, status="В ПРОЦЕССЕ", start_date=start_date)

    try:
        with conn.cursor() as cur:
            # Фильтр по ОБЩЕМУ СПРАВОЧНИКУ (catalog_items): пишем себестоимость
            # ТОЛЬКО по товарам, которые есть в справочнике системы.
            cur.execute("SELECT seller_article FROM catalog_items")
            catalog = {}  # art_canon -> art_orig (из справочника)
            for (orig,) in cur.fetchall():
                catalog[util.canon_article(orig)] = orig

            filtered = {ac: v for ac, v in data.items() if ac in catalog}
            skipped_not_in_catalog = len(data) - len(filtered)
            # Артикулы СПРАВОЧНИКА, оставшиеся БЕЗ себестоимости в этой
            # загрузке: их не было в файле ЛИБО с/с в файле была пустой.
            # Считаем со стороны системы (то, что реально важно), а не файла.
            missing_canon = {ac for ac in catalog if ac not in filtered}
            catalog_missing = len(missing_canon)
            # Из них — сколько СЕЙЧАС НА ОСТАТКАХ WB (критично:
            # занижают стоимостную оценку). Текущие остатки = последняя
            # дата wb_stock_daily, qty>0. Сопоставление по canon-артикулу.
            missing_on_stock = 0
            try:
                cur.execute(
                    "SELECT d.seller_article FROM wb_stock_daily d "
                    "WHERE d.date = (SELECT MAX(date) FROM wb_stock_daily) "
                    "GROUP BY d.seller_article HAVING SUM(d.qty) > 0")
                stock_canon = {util.canon_article(a) for (a,) in cur.fetchall()}
                missing_on_stock = len(missing_canon & stock_canon)
            except Exception:
                missing_on_stock = None  # нет таблицы/ошибка — не показываем разбивку

            if not filtered:
                msg = (f"В файле: {len(data)}; ни один артикул не найден в "
                       f"справочнике дашборда — ничего не записано.")
                cur.execute(
                    "UPDATE report_uploads SET rows_loaded=0, status='OK', "
                    "message=%s WHERE id=%s", (msg[:500], upload_id))
                conn.commit()
                return {
                    "ok": True, "marketplace": MP_NAME, "rows_loaded": 0,
                    "file_total": len(data),
                    "skipped_not_in_catalog": skipped_not_in_catalog,
                    "skipped_empty": skipped_empty,
                    "skipped_no_cost": skipped_no_cost,
                    "upload_id": upload_id,
                }

            # UPSERT по seller_article (overwrite by article). Пишем
            # оригинальный (канонический) артикул из справочника.
            up_rows = []
            for ac, (cost, cost_rf) in filtered.items():
                up_rows.append((catalog[ac], cost, cost_rf))

            # (а) ИСТОРИЧНОСТЬ: item_cost_hist — idempotent overwrite по (артикул, дата).
            #     Повторная загрузка за ту же дату перезапишет значения этого среза.
            hist_rows = [(art, cost, cost_rf, start_date) for (art, cost, cost_rf) in up_rows]
            cur.executemany(
                "INSERT INTO item_cost_hist (seller_article, cost_calc, cost_rf, "
                "start_date, created_at) VALUES (%s,%s,%s,%s, now()) "
                "ON CONFLICT (seller_article, start_date) DO UPDATE SET "
                "cost_calc = EXCLUDED.cost_calc, "
                "cost_rf = EXCLUDED.cost_rf, created_at = now()",
                hist_rows)

            # (б) ОБРАТНАЯ СОВМЕСТИМОСТЬ: item_cost — «последнее актуальное».
            #     Обновляем ТОЛЬКО если загружаемый срез не старше текущего максимума
            #     — т.е. загрузка задним числом НЕ портит актуальное. За актуальность
            #     теперь отвечает item_cost_hist; item_cost — лишь кэш «на сейчас».
            cur.execute(
                "SELECT COALESCE(MAX(start_date), DATE '1900-01-01') FROM item_cost_hist")
            max_start = cur.fetchone()[0]
            if start_date >= max_start:
                cur.executemany(
                    "INSERT INTO item_cost (seller_article, cost_calc, cost_rf, "
                    "updated_at) VALUES (%s,%s,%s, now()) "
                    "ON CONFLICT (seller_article) DO UPDATE SET "
                    "cost_calc = EXCLUDED.cost_calc, "
                    "cost_rf = EXCLUDED.cost_rf, updated_at = now()",
                    up_rows)

            total = len(up_rows)
            msg = (f"С {start_date.isoformat()} · записано с/с: {total} из "
                   f"{len(catalog)} товаров справочника")
            if catalog_missing:
                msg += (f"; без себестоимости осталось: {catalog_missing} "
                        f"(нет в файле или пустая с/с)")
                if missing_on_stock is not None:
                    msg += (f", из них на остатках WB: {missing_on_stock}")
            cur.execute(
                "UPDATE report_uploads SET rows_loaded=%s, status='OK', "
                "message=%s WHERE id=%s", (total, msg[:500], upload_id))
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        user_msg = "Не удалось сохранить справочник себестоимости в базу."
        _journal(conn, path, status="ОШИБКА", message=str(exc),
                 upload_id=upload_id)
        return {"ok": False, "error": user_msg, "upload_id": upload_id}

    return {
        "ok": True,
        "marketplace": MP_NAME,
        "rows_loaded": total,
        "file_total": len(data),
        "catalog_total": len(catalog),
        "catalog_missing": catalog_missing,
        "missing_on_stock": missing_on_stock,
        "skipped_not_in_catalog": skipped_not_in_catalog,
        "skipped_empty": skipped_empty,
        "skipped_no_cost": skipped_no_cost,
        "start_date": start_date.isoformat(),
        "upload_id": upload_id,
    }
