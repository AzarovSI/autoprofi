"""Синхронизация posуточной истории цен маркетплейсов (mp_price_daily).

Операции:
  • recalc_spp_and_buyer_from(marketplace, from_date) — пересчитать колонки
    spp_pct / spp_source_date / spp_is_estimated / buyer_price для всех
    строк mp_price_daily указанного маркетплейса, где date >= from_date.
    Вызывается после ЛЮБОЙ загрузки РНП (WB или Ozon) на диапазон [D1..D2]:
    from_date = D1. Даже если новые данные РНП пришли за один день D,
    пересчёт нужен и для более поздних строк — потому что раньше СПП там
    бралась через fallback от более раннего дня, а теперь может обновиться.
  • sync_uploaded_prices(tx, marketplace, date, rows) — вызывается ПРИ импорте
    отчёта цен Ozon (ранее также WB): вставляет/обновляет upload_price для (SKU, MP, date)
    и заодно проставляет актуальные spp_pct/buyer_price по правилу ниже.
  • sync_direct_report(tx, marketplace, rows, uploaded_by) — прямые цены
    WB/Ozon. Источники upload_wb_spp / upload_ozon_coinvest исключены из
    пересчёта РНП, включая отсутствующую СПП. Старые источники сохраняются.

Правило заполнения СПП для строки (SKU, MP, D):
  1) Если в *_daily_sales есть запись за (SKU, MP, D) с непустой spp_pct —
     берём её как факт (spp_is_estimated = false, spp_source_date = D).
  2) Иначе — последняя известная СПП за d < D по (SKU, MP)
     (spp_is_estimated = true, spp_source_date = d).
  3) Иначе — всё NULL, buyer_price = NULL.
buyer_price = round(upload_price * (1 - spp_pct), 2), если оба не NULL.
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Tuple

from . import db

WB_REPORT_SOURCE = "upload_wb_spp"
OZON_REPORT_SOURCE = "upload_ozon_coinvest"
DIRECT_REPORT_SOURCES = {"Wildberries": WB_REPORT_SOURCE, "Ozon": OZON_REPORT_SOURCE}

MP_DAILY_TABLE = {
    "Wildberries": "wb_daily_sales",
    "Ozon":        "ozon_daily_sales",
}


def _buyer_price(upload_price, spp_pct):
    """upload × (1 − spp), округление до 2 знаков ROUND_HALF_UP.

    Возвращает Decimal или None. Пустые значения → None.
    """
    if upload_price is None or spp_pct is None:
        return None
    up = Decimal(str(upload_price))
    sp = Decimal(str(spp_pct))
    val = up * (Decimal("1") - sp)
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def recalc_spp_and_buyer_from(marketplace: str, from_date):
    """Пересчитать СПП/buyer_price в mp_price_daily для marketplace, начиная с
    from_date включительно. Вызывать после импорта РНП.

    Все строки этого МП с date >= from_date переприменяют правило:
      1) СПП за тот же день из *_daily_sales (spp_is_estimated=false);
      2) fallback на последнюю известную СПП до этого дня (is_estimated=true);
      3) иначе NULL.
    Всё одним SQL-выражением — быстро (в тесте: обновление сотен строк без ORM).
    """
    src = MP_DAILY_TABLE.get(marketplace)
    if not src:
        raise ValueError(f"Неизвестный marketplace: {marketplace!r}")

    # ВНИМАНИЕ: имя таблицы РНП подставляем через f-строку, потому что
    # psycopg не умеет параметризовать identifier'ы. Значение берётся из
    # константного словаря MP_DAILY_TABLE — SQL-инъекция невозможна.
    sql = f"""
    WITH targets AS (
      -- строки, которые нужно пересчитать
      SELECT seller_article, marketplace, date, upload_price
        FROM mp_price_daily
       WHERE marketplace = %s AND date >= %s
         -- Прямые данные нового WB-отчёта, включая NULL при отсутствии
         -- товара, РНП не имеет права заменять расчётными значениями.
         AND NOT (marketplace = 'Wildberries'
                  AND upload_source IS NOT DISTINCT FROM 'upload_wb_spp')
         AND NOT (marketplace = 'Ozon'
                  AND upload_source IS NOT DISTINCT FROM 'upload_ozon_coinvest')
    ),
    resolved AS (
      SELECT
        t.seller_article, t.marketplace, t.date, t.upload_price,
        -- Пытаемся сначала «точную» СПП за тот же день, иначе fallback
        -- на последнюю известную СПП до этого дня.
        COALESCE(exact.spp_pct, fb.spp_pct)                     AS spp_pct,
        COALESCE(exact.spp_date, fb.spp_date)                   AS spp_source_date,
        (exact.spp_pct IS NULL AND fb.spp_pct IS NOT NULL)      AS spp_is_estimated
      FROM targets t
      LEFT JOIN LATERAL (
        SELECT s.spp_pct, s.date AS spp_date
          FROM {src} s
         WHERE s.seller_article = t.seller_article
           AND s.date = t.date
           AND s.spp_pct IS NOT NULL
         LIMIT 1
      ) exact ON TRUE
      LEFT JOIN LATERAL (
        SELECT s.spp_pct, s.date AS spp_date
          FROM {src} s
         WHERE s.seller_article = t.seller_article
           AND s.date < t.date
           AND s.spp_pct IS NOT NULL
         ORDER BY s.date DESC
         LIMIT 1
      ) fb ON exact.spp_pct IS NULL
    )
    UPDATE mp_price_daily m
       SET spp_pct          = r.spp_pct,
           spp_source_date  = r.spp_source_date,
           spp_is_estimated = COALESCE(r.spp_is_estimated, false),
           buyer_price      = CASE
               WHEN r.upload_price IS NULL OR r.spp_pct IS NULL THEN NULL
               ELSE round(r.upload_price * (1 - r.spp_pct), 2)
             END,
           updated_at       = now()
      FROM resolved r
     WHERE m.seller_article = r.seller_article
       AND m.marketplace    = r.marketplace
       AND m.date           = r.date
       -- Повторная проверка на целевой строке защищает от конкурентного
       -- импорта WB, завершившегося после чтения CTE targets.
       AND NOT (m.marketplace = 'Wildberries'
                AND m.upload_source IS NOT DISTINCT FROM 'upload_wb_spp')
       AND NOT (m.marketplace = 'Ozon'
                AND m.upload_source IS NOT DISTINCT FROM 'upload_ozon_coinvest')
    """
    return db.execute(sql, (marketplace, from_date))


def resolve_spp_for(tx, marketplace: str, seller_article: str, date) -> Tuple:
    """Достаёт кортеж (spp_pct, spp_source_date, spp_is_estimated) для одной
    (SKU, MP, date). Возвращает (None, None, False), если СПП вообще нет.

    Используется в импорте цен, чтобы сразу проставить buyer_price для только
    что загруженной строки. Работает ВНУТРИ транзакции (tx.query_one).
    """
    src = MP_DAILY_TABLE.get(marketplace)
    if not src:
        raise ValueError(f"Неизвестный marketplace: {marketplace!r}")

    # exact
    row = tx.query_one(
        f"""SELECT spp_pct FROM {src}
             WHERE seller_article=%s AND date=%s AND spp_pct IS NOT NULL
             LIMIT 1""",
        (seller_article, date),
    )
    if row and row["spp_pct"] is not None:
        return (row["spp_pct"], date, False)
    # fallback
    row = tx.query_one(
        f"""SELECT spp_pct, date FROM {src}
             WHERE seller_article=%s AND date<%s AND spp_pct IS NOT NULL
             ORDER BY date DESC LIMIT 1""",
        (seller_article, date),
    )
    if row and row["spp_pct"] is not None:
        return (row["spp_pct"], row["date"], True)
    return (None, None, False)


def sync_uploaded_prices(tx, marketplace: str, date, rows: Iterable[Tuple[str, float]],
                          upload_source: str, uploaded_by: int):
    """Записать пачку (seller_article, upload_price) как срез upload_price на дату.

    Делается ОДНИМ серверным запросом (INSERT ... SELECT ... ON CONFLICT):
      • VALUES(...) — входные (SKU, upload_price);
      • LATERAL SELECT из <mp>_daily_sales — точная СПП за день D,
        fallback — последняя известная СПП < D;
      • buyer_price = round(upload_price * (1 - spp), 2) — в SQL.

    Работает ВНУТРИ транзакции. Не пишет в mp_price_upload_log — это делает
    вызывающий (роутер импорта), чтобы иметь доступ к file_name и подсчётам.

    Возвращает {"upserted": N}.
    """
    rows = list(rows) if not isinstance(rows, list) else rows
    if not rows:
        return {"upserted": 0}

    src = MP_DAILY_TABLE.get(marketplace)
    if not src:
        raise ValueError(f"Неизвестный marketplace: {marketplace!r}")

    # Имя таблицы РНП берём из константного словаря MP_DAILY_TABLE —
    # SQL-инъекция невозможна.
    sql = f"""
    WITH input(seller_article, upload_price) AS (
      VALUES %s
    ),
    resolved AS (
      SELECT
        i.seller_article,
        i.upload_price::numeric AS upload_price,
        COALESCE(exact.spp_pct, fb.spp_pct)                AS spp_pct,
        COALESCE(exact.spp_date, fb.spp_date)              AS spp_source_date,
        (exact.spp_pct IS NULL AND fb.spp_pct IS NOT NULL) AS spp_is_estimated
      FROM input i
      LEFT JOIN LATERAL (
        SELECT s.spp_pct, s.date AS spp_date
          FROM {src} s
         WHERE s.seller_article = i.seller_article
           AND s.date           = %s::date
           AND s.spp_pct IS NOT NULL
         LIMIT 1
      ) exact ON TRUE
      LEFT JOIN LATERAL (
        SELECT s.spp_pct, s.date AS spp_date
          FROM {src} s
         WHERE s.seller_article = i.seller_article
           AND s.date           < %s::date
           AND s.spp_pct IS NOT NULL
         ORDER BY s.date DESC
         LIMIT 1
      ) fb ON exact.spp_pct IS NULL
    ),
    ins AS (
      INSERT INTO mp_price_daily
        (seller_article, marketplace, date, upload_price, upload_source,
         upload_loaded_at, upload_loaded_by, spp_pct, spp_source_date,
         spp_is_estimated, buyer_price, updated_at)
      SELECT
        r.seller_article,
        %s::text          AS marketplace,
        %s::date          AS date,
        r.upload_price,
        %s::text          AS upload_source,
        now()             AS upload_loaded_at,
        %s::int           AS upload_loaded_by,
        r.spp_pct,
        r.spp_source_date,
        COALESCE(r.spp_is_estimated, false),
        CASE WHEN r.spp_pct IS NULL THEN NULL
             ELSE round(r.upload_price * (1 - r.spp_pct), 2) END,
        now()             AS updated_at
      FROM resolved r
      ON CONFLICT (seller_article, marketplace, date) DO UPDATE SET
        upload_price     = EXCLUDED.upload_price,
        upload_source    = EXCLUDED.upload_source,
        upload_loaded_at = now(),
        upload_loaded_by = EXCLUDED.upload_loaded_by,
        spp_pct          = EXCLUDED.spp_pct,
        spp_source_date  = EXCLUDED.spp_source_date,
        spp_is_estimated = EXCLUDED.spp_is_estimated,
        buyer_price      = EXCLUDED.buyer_price,
        updated_at       = now()
      RETURNING 1
    )
    SELECT count(*) FROM ins
    """

    # tx.execute_values подставит все строки в первый %s (в VALUES).
    # Остальные %s (дата exact, дата fallback, marketplace, date, upload_source, uploaded_by)
    # тут же объявляются в шаблоне для всего батча через константы —
    # execute_values переписывает только ПЕРВый %s. Чтобы передать остальные,
    # встраиваем их как литералы — не подходит (инъекция). Поэтому берём
    # другой путь: строим сами SQL через mogrify-подобный код, и выполняем
    # одним cur.execute() с обобщённым списком параметров.
    cur = tx.conn.cursor()

    # Собираем VALUES как "(%s,%s),(%s,%s),..." и плоский список параметров.
    tmpl = "(%s,%s::numeric)"
    values_sql = ",".join([tmpl] * len(rows))
    full_sql = sql.replace("%s", values_sql, 1)

    flat = []
    for sa, up in rows:
        flat.append(sa)
        flat.append(up)
    # Остальные 6 %s: date(exact), date(fb), marketplace, date, upload_source, uploaded_by
    flat.extend([date, date, marketplace, date, upload_source, uploaded_by])

    cur.execute(full_sql, flat)
    row = cur.fetchone()
    upserted = int(row[0] if row else 0)
    return {"upserted": upserted}


def sync_wb_report(tx, rows, uploaded_by):
    """Совместимый вход WB; общая логика прямых дневных отчётов ниже."""
    return sync_direct_report(tx, "Wildberries", rows, uploaded_by)


def sync_direct_report(tx, marketplace, rows, uploaded_by):
    """Прямые дневные срезы WB/Ozon без РНП и пересчёта цены покупателя.

    Пакеты внутри транзакции вызывающего. Перезаписываем только совпавшие
    артикул + marketplace + дата, остальные записи остаются нетронутыми.
    """
    source = DIRECT_REPORT_SOURCES[marketplace]
    values = [
        (r.article, marketplace, r.date, r.upload_price, source,
         uploaded_by, r.spp_pct, r.date if r.spp_pct is not None else None, r.buyer_price)
        for r in rows
    ]
    count = tx.execute_values(
        """INSERT INTO mp_price_daily
             (seller_article, marketplace, date, upload_price, upload_source,
              upload_loaded_by, spp_pct, spp_source_date, buyer_price,
              spp_is_estimated, upload_loaded_at, updated_at)
           VALUES %s
           ON CONFLICT (seller_article, marketplace, date) DO UPDATE SET
             upload_price = EXCLUDED.upload_price,
             upload_source = EXCLUDED.upload_source,
             upload_loaded_by = EXCLUDED.upload_loaded_by,
             upload_loaded_at = now(),
             spp_pct = EXCLUDED.spp_pct,
             spp_source_date = EXCLUDED.spp_source_date,
             spp_is_estimated = false,
             buyer_price = EXCLUDED.buyer_price,
             updated_at = now()""",
        values,
        template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,false,now(),now())",
        page_size=1000,
    )
    return {"upserted": count}
