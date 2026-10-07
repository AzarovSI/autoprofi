# -*- coding: utf-8 -*-
"""Загрузчик остатков Ozon по складам (и кластерам).

Источник — выгрузка из ЛК Ozon «Отчёт по остаткам» (.xlsx). Берём лист
«Товар-склад» — он в разрезе Артикул × Кластер × Склад. В файле даты НЕТ —
дата остатков передаётся при загрузке (поле в UI), как у WB и склада АВТОПРОФИ.

Особенности формата (в отличие от WB):
  * шапка ДВУХУРОВНЕВАЯ: строка 0 — верхний заголовок групп, строка 1 —
    подзаголовки; строка 2 — «Нередактируемое», строка 3 — текстовые описания.
    Данные начинаются со СТРОКИ 4 (индекс 4).
  * склады идут СВЕРХУ ВНИЗ (в строках), а не столбцами: у каждой строки
    свои Кластер и Склад. Один товар = несколько строк (по одной на склад).
  * структура складов двухуровневая: Кластер → Склад. Плюс мы поверх строим
    Федеральный округ → Кластер → Склад (ФО проставляется в справочнике).

Что считаем остатком (qty): СУММА столбцов H–P (индексы 7..15) —
    H  Доступно к продаже
    I  Готовим к продаже
    J  Маркируемые товары, ожидающие вывоз
    K  Маркируемые товары, ожидающие УПД
    L  Истекает срок годности
    M  Брак, доступный к вывозу с поставки
    N  Брак, доступный к вывозу со стока
    O  Излишки с поставки, доступные к вывозу
    P  Проходят проверку
Отдельно сохраняем «Доступно к продаже» (H) как ключевую подметрику,
а также В пути (Q+R), Возвраты (S), Готовим к вывозу (T) — в ozon_stock_meta
(агрегат по товару за дату, не в разрезе склада).

Артикулы: сопоставление со справочником catalog_items по канону
(upper+trim+схлопывание пробелов). ОТСУТСТВУЮЩИЕ в справочнике артикулы
АВТОМАТИЧЕСКИ ЗАВОДЯТСЯ (как в РНП): остатки учитываем по ВСЕМ товарам
(для стоимостной оценки распределение не важно), но в отчёте о загрузке
показываем счётчик «нераспределённых» — их надо распределить/дозаполнить.

Склады: справочник ozon_warehouses пополняется автоматически при первой
встрече нового имени склада. Кластер склада берётся из отчёта и
обновляется, если в отчёте он изменился (переезд склада в другой кластер).

Идемпотентность: повторная загрузка за ту же дату ПОЛНОСТЬЮ перезаписывает
данные этого дня (DELETE по date в ozon_stock_daily и ozon_stock_meta,
затем INSERT).

Файл читаем через python_calamine (как выгрузки WB/Ozon).
"""

import os
import datetime

from python_calamine import CalamineWorkbook

MP_NAME = "Ozon"
PERIOD_KIND = "ozon_stock"

SHEET_NAME = "Товар-склад"

# Позиции колонок на листе «Товар-склад» (0-based). Формат стабильный —
# берём по индексу, но валидируем шапку по названиям ниже.
COL_ARTICLE = 0   # A  Артикул
COL_NAME = 1      # B  Название товара
COL_SKU = 2       # C  SKU
COL_ZONE = 4      # E  Зона размещения
COL_CLUSTER = 5   # F  Кластер
COL_WAREHOUSE = 6  # G  Склад
# H–P — остатки на складах Ozon (суммируем всё это в qty).
HP_FROM = 7       # H
HP_TO = 15        # P  (включительно)
COL_TRANSIT_1 = 16  # Q  В заявках на поставку
COL_TRANSIT_2 = 17  # R  В поставках в пути
COL_RETURNS = 18    # S  Возвращаются от покупателей
COL_REMOVAL = 19    # T  Готовим к вывозу по вашей заявке
COL_AVAIL = 7       # H  Доступно к продаже (ключевая подметрика)

DATA_START_ROW = 4  # строки 0-1 шапка, 2 «Нередактируемое», 3 описания

# Ключевые заголовки для валидации формата (проверяем в строке 0/1).
HEADER_MARKERS_TOP = ["Артикул", "Кластер", "Склад"]


def _canon(s):
    """Канон артикула: trim, схлопывание пробелов, upper."""
    if s is None:
        return ""
    return " ".join(str(s).strip().split()).upper()


def _to_num(v):
    """Число из ячейки; пусто/«-»/нечисло → 0.0 (для суммирования H–P)."""
    if v is None or v == "" or v == "-":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_date_arg(date_str):
    """Разобрать дату остатков (YYYY-MM-DD из <input type=date> или ДД.ММ.ГГГГ)."""
    if not date_str:
        return None
    if isinstance(date_str, datetime.date):
        return date_str
    s = str(date_str).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _journal(conn, path, *, status, message="", rows_loaded=0,
             period_start=None, period_end=None, period_text="",
             upload_id=None):
    """Создать/обновить запись в report_uploads (period_kind='ozon_stock')."""
    try:
        with conn.cursor() as cur:
            if upload_id is None:
                cur.execute(
                    "INSERT INTO report_uploads (marketplace, year, period_text, "
                    "period_start, period_end, source_file, rows_loaded, status, "
                    "message, period_kind) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (MP_NAME,
                     (period_start.year if period_start else None),
                     period_text, period_start, period_end,
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


def _read_sheet(path):
    """Прочитать лист «Товар-склад» как список списков (calamine)."""
    wb = CalamineWorkbook.from_path(path)
    names = wb.sheet_names
    sn = SHEET_NAME if SHEET_NAME in names else names[0]
    return wb.get_sheet_by_name(sn).to_python()


def _validate_header(rows):
    """Проверить, что это отчёт остатков Ozon (по маркерам в шапке)."""
    if not rows or len(rows) <= DATA_START_ROW:
        return False
    top = [str(x).strip() if x is not None else "" for x in rows[0]]
    joined = " | ".join(top)
    return all(m in joined for m in HEADER_MARKERS_TOP)


def _resolve_warehouses(cur, wh_cluster, seen_date):
    """{имя_склада -> id}, создавая недостающие; обновляя кластер при смене.

    wh_cluster — {имя_склада: кластер}. Сопоставление по точному имени склада.
    Новый склад заносится в ozon_warehouses (first_seen_date, cluster).
    Если у существующего склада в отчёте изменился кластер — обновляем.
    Возвращает (mapping, new_names).
    """
    mapping = {}
    if not wh_cluster:
        return mapping, []
    cur.execute("SELECT id, name, cluster FROM ozon_warehouses")
    existing = {name: (wid, cl) for (wid, name, cl) in cur.fetchall()}
    new_names = []
    for name, cluster in wh_cluster.items():
        if name in existing:
            wid, old_cl = existing[name]
            mapping[name] = wid
            if cluster and cluster != old_cl:
                cur.execute(
                    "UPDATE ozon_warehouses SET cluster=%s WHERE id=%s",
                    (cluster, wid))
        else:
            new_names.append((name, cluster))
    for name, cluster in new_names:
        cur.execute(
            "INSERT INTO ozon_warehouses (name, cluster, first_seen_date, is_active) "
            "VALUES (%s,%s,%s,true) "
            "ON CONFLICT (name) DO UPDATE SET is_active=true, "
            "cluster=COALESCE(EXCLUDED.cluster, ozon_warehouses.cluster) "
            "RETURNING id",
            (name, cluster, seen_date))
        mapping[name] = cur.fetchone()[0]
    return mapping, [n for (n, _c) in new_names]


def load_ozon_wh_stock(conn, path, stock_date=None):
    """Загрузить остатки Ozon по складам за указанную дату.

    conn — psycopg-соединение (без автокоммита).
    stock_date — дата остатков (обязательна; в файле её нет).

    Возвращает dict {ok, stock_date, rows_products, rows_stock_cells,
    warehouses_total, warehouses_new, clusters_total, arts_total,
    arts_new (нераспределённые), arts_new_list, file_rows, ...}.

    Идемпотентно: удаляет данные за дату из ozon_stock_daily и
    ozon_stock_meta, затем вставляет заново.
    """
    d = _parse_date_arg(stock_date)
    if d is None:
        msg = ("Не указана дата остатков. Выберите дату, за которую "
               "загружаются остатки складов Ozon.")
        _journal(conn, path, status="ОШИБКА", message=msg)
        return {"ok": False, "error": msg}

    period_text = d.strftime("%d.%m.%Y")

    # --- чтение файла ---
    try:
        rows = _read_sheet(path)
    except Exception as e:  # noqa: BLE001
        msg = f"Не удалось прочитать файл: {e}"
        _journal(conn, path, status="ОШИБКА", message=msg,
                 period_start=d, period_end=d, period_text=period_text)
        return {"ok": False, "error": msg}

    if not _validate_header(rows):
        msg = ("ОШИБКА ФОРМАТА: это не похоже на отчёт остатков Ozon. "
               "Нужен лист «Товар-склад» с колонками Артикул / Кластер / Склад.")
        _journal(conn, path, status="ОШИБКА ФОРМАТА", message=msg,
                 period_start=d, period_end=d, period_text=period_text)
        return {"ok": False, "error": msg}

    # --- разбор строк данных (с DATA_START_ROW) ---
    # По каждой строке: артикул, кластер, склад, qty = сумма H–P.
    # meta по товару (агрегат за дату): avail(H), total_hp, transit, returns, removal.
    # cells: (art_canon, warehouse_name) -> qty (суммируем дубли).
    from collections import defaultdict
    art_orig = {}          # canon -> исходный артикул (первое вхождение)
    art_name = {}          # canon -> название товара (для автозавода карточки)
    wh_cluster = {}        # имя_склада -> кластер (последнее вхождение)
    cluster_set = set()
    cells = defaultdict(float)   # (canon, wh_name) -> qty
    meta_acc = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0.0])
    # meta_acc[canon] = [avail(H), total_hp, transit(Q+R), returns(S), removal(T)]
    file_rows = 0

    for r in rows[DATA_START_ROW:]:
        if COL_ARTICLE >= len(r):
            continue
        art_raw = r[COL_ARTICLE]
        if art_raw is None or not str(art_raw).strip():
            continue
        canon = _canon(art_raw)
        if not canon:
            continue
        file_rows += 1
        art_orig.setdefault(canon, str(art_raw).strip())
        if COL_NAME < len(r) and r[COL_NAME] not in (None, ""):
            art_name.setdefault(canon, str(r[COL_NAME]).strip())

        cluster = ""
        if COL_CLUSTER < len(r) and r[COL_CLUSTER] not in (None, ""):
            cluster = str(r[COL_CLUSTER]).strip()
        wname = ""
        if COL_WAREHOUSE < len(r) and r[COL_WAREHOUSE] not in (None, ""):
            wname = str(r[COL_WAREHOUSE]).strip()
        if cluster:
            cluster_set.add(cluster)
        if wname:
            wh_cluster[wname] = cluster  # последнее вхождение — актуальный кластер

        # qty = сумма H–P
        qty = 0.0
        for ci in range(HP_FROM, HP_TO + 1):
            if ci < len(r):
                qty += _to_num(r[ci])
        if wname:
            cells[(canon, wname)] += qty

        # мета-агрегаты по товару за дату
        acc = meta_acc[canon]
        acc[0] += _to_num(r[COL_AVAIL]) if COL_AVAIL < len(r) else 0.0
        acc[1] += qty
        tr = 0.0
        if COL_TRANSIT_1 < len(r):
            tr += _to_num(r[COL_TRANSIT_1])
        if COL_TRANSIT_2 < len(r):
            tr += _to_num(r[COL_TRANSIT_2])
        acc[2] += tr
        acc[3] += _to_num(r[COL_RETURNS]) if COL_RETURNS < len(r) else 0.0
        acc[4] += _to_num(r[COL_REMOVAL]) if COL_REMOVAL < len(r) else 0.0

    if not art_orig:
        msg = "В файле нет строк с артикулами."
        _journal(conn, path, status="ОШИБКА", message=msg,
                 period_start=d, period_end=d, period_text=period_text)
        return {"ok": False, "error": msg}

    upload_id = _journal(conn, path, status="ЗАГРУЗКА",
                         period_start=d, period_end=d,
                         period_text=period_text)

    try:
        with conn.cursor() as cur:
            # 1) справочник артикулов: автозаводим отсутствующие (как в РНП).
            #    Канон в БД — upper(seller_article); наш _canon дополнительно
            #    схлопывает пробелы, но в catalog_items пробелы уже нормализованы.
            cur.execute("SELECT upper(seller_article) FROM catalog_items")
            catalog = {row[0] for row in cur.fetchall()}
            arts_new_list = []
            new_rows = []
            for canon, orig in art_orig.items():
                if canon not in catalog:
                    arts_new_list.append(orig)
                    new_rows.append((orig, art_name.get(canon)))
            if new_rows:
                cur.executemany(
                    "INSERT INTO catalog_items (seller_article, sample_name) "
                    "VALUES (%s,%s) ON CONFLICT (seller_article) DO NOTHING",
                    new_rows)
            arts_new = len(arts_new_list)

            # 2) справочник складов — создаём недостающие, обновляем кластер.
            cur.execute("SELECT name FROM ozon_warehouses")
            wh_before = {row[0] for row in cur.fetchall()}
            wh_map, _new = _resolve_warehouses(cur, wh_cluster, d)
            warehouses_new = [n for n in wh_cluster if n not in wh_before]

            # 3) идемпотентность: удаляем данные за дату
            cur.execute("DELETE FROM ozon_stock_daily WHERE date=%s", (d,))
            cur.execute("DELETE FROM ozon_stock_meta WHERE date=%s", (d,))

            # 4) факты по складам (длинная модель). Пишем ВСЕ товары.
            cell_rows = []
            for (canon, wname), qty in cells.items():
                wid = wh_map.get(wname)
                if wid is None:
                    continue
                orig = art_orig.get(canon, canon)
                cell_rows.append((d, orig, wid, qty))
            if cell_rows:
                cur.executemany(
                    "INSERT INTO ozon_stock_daily "
                    "(date, seller_article, warehouse_id, qty) "
                    "VALUES (%s,%s,%s,%s)",
                    cell_rows)

            # 5) мета по товару за дату
            meta_rows = []
            for canon, acc in meta_acc.items():
                orig = art_orig.get(canon, canon)
                meta_rows.append((d, orig, acc[0], acc[1], acc[2], acc[3], acc[4]))
            if meta_rows:
                cur.executemany(
                    "INSERT INTO ozon_stock_meta "
                    "(date, seller_article, avail_qty, total_hp, in_transit, "
                    " returns_qty, to_removal) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    meta_rows)

            rows_stock_cells = len(cell_rows)
            rows_products = len(meta_rows)
            clusters_total = len(cluster_set)
            warehouses_total = len(wh_cluster)

            msg = (f"Остатки Ozon за {period_text}: товаров {rows_products}, "
                   f"ячеек склад×товар {rows_stock_cells}, "
                   f"складов {warehouses_total}, кластеров {clusters_total}")
            if warehouses_new:
                msg += f"; новых складов: {len(warehouses_new)} ({', '.join(warehouses_new)})"
            if arts_new:
                msg += (f"; заведено новых артикулов (нераспределённые): {arts_new}")

            cur.execute(
                "UPDATE report_uploads SET rows_loaded=%s, status='OK', "
                "message=%s WHERE id=%s",
                (rows_products, msg[:500], upload_id))
        conn.commit()
    except Exception as e:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            pass
        emsg = f"Ошибка записи в БД: {e}"
        _journal(conn, path, status="ОШИБКА", message=emsg, upload_id=upload_id)
        return {"ok": False, "error": emsg}

    return {
        "ok": True,
        "stock_date": d.isoformat(),
        "period_text": period_text,
        "rows_products": rows_products,
        "rows_loaded": rows_products,
        "rows_stock_cells": rows_stock_cells,
        "warehouses_total": warehouses_total,
        "warehouses_new": warehouses_new,
        "clusters_total": clusters_total,
        "arts_total": len(art_orig),
        "arts_new": arts_new,
        "arts_new_list": arts_new_list[:200],
        "file_rows": file_rows,
    }
