"""Парсеры пользовательских шаблонов цен Ozon/WB.

Задача — вытащить из ЛК-выгрузок только пары (seller_article, upload_price),
всё остальное игнорируется. Данные пишутся в mp_price_daily через
price_history.sync_uploaded_prices.

Форматы (проверены на реальных примерах пользователя от 14.09.2026):
  • Ozon: лист «Товары и цены», row1 — заголовки, данные с row4.
    Артикул = col0 «Артикул», upload_price = col18 «Предельная цена, руб.».
  • WB: лист Sheet1, row0 — заголовки, данные с row1.
    Артикул = «Артикул продавца», upload_price = «Текущая цена».

Оба читаем через python_calamine — openpyxl на Ozon-шаблонах падает из-за
глюка стилей (style="none" в границах).
"""
from decimal import Decimal, InvalidOperation
from typing import List, Tuple

from python_calamine import CalamineWorkbook

from . import util


OZON_SHEET = "Товары и цены"
OZON_HEADER_ROW = 1        # 0-based
OZON_DATA_START = 4        # 0-based
OZON_ART_COL = 0
OZON_PRICE_COL = 18        # «Предельная цена, руб.»
OZON_PRICE_HEADER = "Предельная цена, руб."

WB_HEADER_ROW = 0
WB_DATA_START = 1
WB_ART_HEADER = "Артикул продавца"
# upload_price для WB = Цена со скидкой (колонка M).
# В шаблоне WB это формула:
#   M = ROUND(I*(1-K/100), 2), если J пустая
#   M = ROUND(J*(1-L/100), 2), если J заполнена (L пустая → берётся K)
# Наш рабочий флоу: выгрузка без новых J/L, т.е. всегда I*(1-K/100)
# (согласовано 2026-09-15). Само значение M в xlsx почти всегда
# пустое (формула не пересчитана в файле), поэтому считаем сами.
WB_CUR_PRICE_HEADER = "Текущая цена"
WB_CUR_DISCOUNT_HEADER = "Текущая скидка"


def _to_price(v):
    """Число → Decimal (2 знака) или None. Пустое/некорректное → None."""
    if v is None or v == "":
        return None
    if isinstance(v, str):
        v = v.strip().replace(" ", "").replace(",", ".")
        if v == "" or v == "-":
            return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    if d <= 0:
        return None
    return d.quantize(Decimal("0.01"))


def parse_ozon(path: str) -> List[Tuple[str, Decimal]]:
    """Вернуть список [(seller_article, upload_price), ...] из шаблона Ozon.

    Проверяем заголовки, чтобы не молча съесть чужой файл: если row1[0]
    не «Артикул» или row1[18] не «Предельная цена, руб.» — ValueError.
    """
    wb = CalamineWorkbook.from_path(path)
    if OZON_SHEET not in wb.sheet_names:
        raise ValueError(f"В файле Ozon нет листа «{OZON_SHEET}». Проверьте, что это шаблон цен из ЛК.")
    ws = wb.get_sheet_by_name(OZON_SHEET)
    data = ws.to_python()
    if len(data) < OZON_DATA_START + 1:
        raise ValueError("Файл Ozon пустой — нет строк данных.")
    header = data[OZON_HEADER_ROW]
    if len(header) <= OZON_PRICE_COL:
        raise ValueError("В файле Ozon недостаточно колонок — не тот шаблон.")
    if str(header[OZON_ART_COL]).strip() != "Артикул":
        raise ValueError(f"Колонка A должна называться «Артикул», а не {header[OZON_ART_COL]!r}")
    if str(header[OZON_PRICE_COL]).strip() != OZON_PRICE_HEADER:
        raise ValueError(f"Колонка S должна называться «{OZON_PRICE_HEADER}», а не {header[OZON_PRICE_COL]!r}")

    rows = []
    for r in data[OZON_DATA_START:]:
        if len(r) <= OZON_PRICE_COL:
            continue
        art_raw = r[OZON_ART_COL]
        if not art_raw:
            continue
        art = util.canon_article(str(art_raw))
        if not art:
            continue
        price = _to_price(r[OZON_PRICE_COL])
        if price is None:
            continue
        rows.append((art, price))
    return rows


def _to_discount_pct(v):
    """Скидка в процентах → Decimal. Пустое → Decimal('0').

    В шаблоне WB скидка — целое число 0..95 (может прийти
    как int/float/str). Отрицательные и >95 отбрасываем — битая строка.
    """
    if v is None or v == "":
        return Decimal("0")
    if isinstance(v, str):
        v = v.strip().replace(" ", "").replace(",", ".")
        if v == "" or v == "-":
            return Decimal("0")
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    if d < 0 or d > Decimal("95"):
        return None
    return d


def parse_wb(path: str) -> List[Tuple[str, Decimal]]:
    """Вернуть список [(seller_article, upload_price), ...] из шаблона WB.

    upload_price = «Цена со скидкой» (колонка M в шаблоне).
    Формула: ROUND(«Текущая цена» * (1 − «Текущая скидка»/100), 2).
    Колонка M в xlsx почти всегда хранит формулу без кеша результата,
    поэтому считаем сами из I и K — согласовано 2026-09-15.

    Ищем колонки по заголовку — устойчиво к любому порядку/числу колонок.
    """
    wb = CalamineWorkbook.from_path(path)
    ws = wb.get_sheet_by_name(wb.sheet_names[0])
    data = ws.to_python()
    if len(data) < WB_DATA_START + 1:
        raise ValueError("Файл WB пустой — нет строк данных.")
    header = data[WB_HEADER_ROW]
    header_map = {str(h).strip(): i for i, h in enumerate(header) if h}
    if WB_ART_HEADER not in header_map:
        raise ValueError(f"В файле WB нет колонки «{WB_ART_HEADER}». Не тот шаблон?")
    if WB_CUR_PRICE_HEADER not in header_map:
        raise ValueError(f"В файле WB нет колонки «{WB_CUR_PRICE_HEADER}». Не тот шаблон?")
    if WB_CUR_DISCOUNT_HEADER not in header_map:
        raise ValueError(f"В файле WB нет колонки «{WB_CUR_DISCOUNT_HEADER}». Не тот шаблон?")
    ci_art = header_map[WB_ART_HEADER]
    ci_price = header_map[WB_CUR_PRICE_HEADER]
    ci_disc = header_map[WB_CUR_DISCOUNT_HEADER]

    rows = []
    for r in data[WB_DATA_START:]:
        maxc = max(ci_art, ci_price, ci_disc)
        if len(r) <= maxc:
            continue
        art_raw = r[ci_art]
        if not art_raw:
            continue
        art = util.canon_article(str(art_raw))
        if not art:
            continue
        base_price = _to_price(r[ci_price])
        if base_price is None:
            continue
        disc = _to_discount_pct(r[ci_disc])
        if disc is None:
            # битая скидка — пропускаем строку, лучше None чем неверное число
            continue
        final = (base_price * (Decimal("1") - disc / Decimal("100"))).quantize(Decimal("0.01"))
        if final <= 0:
            continue
        rows.append((art, final))
    return rows
