"""Парсеры пользовательских шаблонов цен Ozon/WB.

Ozon: пары (seller_article, upload_price), дата задаётся при загрузке.
WB: дневные срезы из отчёта «Цены с СПП», даты и время берутся из строк.

Форматы (проверены на реальных примерах пользователя от 14.09.2026):
  • Ozon: лист «Товары и цены», row1 — заголовки, данные с row4.
    Артикул = col0 «Артикул», upload_price = col18 «Предельная цена, руб.».
  • WB: лист определяется по заголовкам (перед ним может быть пустой лист).
    Дата, артикул продавца, загружаемая цена, цена витрины и СПП.

Оба читаем через python_calamine — openpyxl на Ozon-шаблонах падает из-за
глюка стилей (style="none" в границах).
"""
import datetime as dt
import os
import zipfile
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import List, Tuple

from python_calamine import CalamineWorkbook

from . import util


OZON_SHEET = "Товары и цены"
OZON_HEADER_ROW = 1        # 0-based
OZON_DATA_START = 4        # 0-based
OZON_ART_COL = 0
OZON_PRICE_COL = 18        # «Предельная цена, руб.»
OZON_PRICE_HEADER = "Предельная цена, руб."

WB_COLUMNS = {
    "date": ("Дата",),
    "article": ("Артикул продавца",),
    # В исходном файле именно «со скидки». Принимаем и исправленное название.
    "upload_price": ("Цена со скидки (загружаемая)", "Цена со скидкой (загружаемая)"),
    "buyer_price": ("Цена на витрине (с СПП)",),
    "spp": ("СПП, %",),
}
WB_MAX_FILE_BYTES = 25 * 1024 * 1024
WB_MAX_XML_BYTES = 150 * 1024 * 1024
WB_MAX_ROWS = 200_000
WB_MAX_CELLS = 2_000_000


@dataclass(frozen=True)
class WbPriceRow:
    date: dt.date
    article: str
    upload_price: Decimal
    buyer_price: Decimal | None
    spp_pct: Decimal | None
    time: dt.time


@dataclass
class WbPriceReport:
    rows: list[WbPriceRow]
    rows_by_date: dict
    duplicates_by_date: dict

    @property
    def rows_in_file(self):
        return sum(self.rows_by_date.values())


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


def _wb_number(value, name, optional=False):
    if value is None or (isinstance(value, str) and value.strip() in ("", "-", "—", "–")):
        if optional:
            return None
        raise ValueError(f"не заполнено поле «{name}»")
    text = str(value).strip().replace("\u00a0", "").replace("\u202f", "").replace(" ", "").replace(",", ".")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError(f"некорректное число в поле «{name}»") from None
    if not number.is_finite():
        raise ValueError(f"некорректное число в поле «{name}»")
    return number


def _wb_date(value):
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError("некорректная дата (ожидается дата Excel, ДД.ММ.ГГГГ или ГГГГ-ММ-ДД)")


def _wb_time(value):
    if value is None or value == "":
        return dt.time.min
    if isinstance(value, dt.datetime):
        value = value.time()
    if isinstance(value, dt.time) and value.tzinfo is None:
        return value
    if isinstance(value, (int, float)) and 0 <= value < 1:
        seconds = min(86399, round(value * 86400))
        return dt.time(seconds // 3600, (seconds % 3600) // 60, seconds % 60)
    try:
        parsed = dt.time.fromisoformat(str(value).strip())
        if parsed.tzinfo is None:
            return parsed
    except ValueError:
        pass
    raise ValueError("некорректное время (ожидается ЧЧ:ММ или ЧЧ:ММ:СС)")


def _wb_price(value, name):
    number = _wb_number(value, name)
    if not 0 < number < Decimal("9999999999.995"):
        raise ValueError(f"«{name}» должна быть положительной ценой")
    result = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if result <= 0:
        raise ValueError(f"«{name}» должна быть не меньше 0,01")
    return result


def _wb_spp(value):
    # Настоящий Excel-процент хранится долей (0.353... = 35.3%).
    # Текст «35,3%» и проценты числом 35.3 тоже допустимы.
    is_percent = isinstance(value, str) and value.strip().endswith("%")
    if is_percent:
        value = value.strip()[:-1]
    number = _wb_number(value, "СПП, %", optional=True)
    if number is None or number == 0:
        return None
    if is_percent or number > 1:
        number /= 100
    if not 0 < number < 1:
        raise ValueError("СПП должна быть от 0% включительно до 100% не включительно")
    return number


def _validate_wb_container(path):
    """Ограничить ресурсы XLSX до его распаковки и построения таблиц в памяти."""
    if os.path.getsize(path) > WB_MAX_FILE_BYTES:
        raise ValueError("Файл WB слишком большой: максимум 25 МБ.")
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 4096 or sum(e.file_size for e in entries) > WB_MAX_XML_BYTES:
                raise ValueError("Файл WB слишком большой после распаковки. Разделите отчёт на несколько файлов.")
            if any(e.flag_bits & 1 for e in entries):
                raise ValueError("Защищённый паролем Excel не поддерживается.")
    except zipfile.BadZipFile:
        raise ValueError("Не удалось прочитать Excel. Проверьте файл отчёта «Цены с СПП».") from None


def parse_wb(path: str) -> WbPriceReport:
    """Разобрать весь отчёт до записи. Любая ошибочная строка отменяет импорт.

    Ключ: дата + канонический артикул. Внутри дня выбираем самое позднее время,
    не порядок строк. Нулевая/пустая СПП означает отсутствие товара:
    buyer_price и spp_pct = NULL, загружаемая цена сохраняется.
    """
    _validate_wb_container(path)
    wb = CalamineWorkbook.from_path(path)
    if len(wb.sheet_names) > 20:
        raise ValueError("Слишком много листов в файле WB: максимум 20.")
    candidates = []
    cell_count = 0
    for sheet_name in wb.sheet_names:
        sheet = wb.get_sheet_by_name(sheet_name)
        cell_count += sheet.height * sheet.width
        if sheet.height > WB_MAX_ROWS + 1 or sheet.width > 64 or cell_count > WB_MAX_CELLS:
            raise ValueError("Слишком большой лист WB. Разделите отчёт на файлы до 200 000 строк и 64 колонок.")
        data = sheet.to_python()
        if not data:
            continue
        header = {" ".join(str(h).split()): i for i, h in enumerate(data[0]) if h}
        columns = {
            key: next((header[h] for h in aliases if h in header), None)
            for key, aliases in WB_COLUMNS.items()
        }
        if all(i is not None for i in columns.values()):
            columns.update(time=header.get("Время"), region=header.get("Регион"))
            candidates.append((sheet_name, data, columns))
    if len(candidates) != 1:
        raise ValueError(
            "Нужен один лист отчёта «Цены с СПП» с колонками: Дата, Артикул продавца, "
            "Цена со скидки (загружаемая), Цена на витрине (с СПП), СПП, %. "
            "Старый шаблон «Цены и скидки» больше не используется для WB."
        )

    sheet_name, data, columns = candidates[0]
    selected, regions, rows_by_date, errors = {}, {}, Counter(), []
    for line, raw in enumerate(data[1:], 2):
        if all(v is None or v == "" for v in raw):
            continue

        def value(key):
            i = columns[key]
            return raw[i] if i is not None and i < len(raw) else None

        try:
            date = _wb_date(value("date"))
            article = util.canon_article(value("article"))
            if not article:
                raise ValueError("не заполнен артикул продавца")
            upload_price = _wb_price(value("upload_price"), "Загружаемая цена")
            spp = _wb_spp(value("spp"))
            buyer = _wb_price(value("buyer_price"), "Цена на витрине (с СПП)") if spp is not None else None
            row = WbPriceRow(date, article, upload_price, buyer, spp, _wb_time(value("time")))
            key = (date, article)
            region = str(value("region") or "").strip().casefold()
            if region:
                if key in regions and regions[key] != region:
                    raise ValueError(f"несколько регионов для {article} на {date:%d.%m.%Y}; загрузите один регион")
                regions[key] = region
            old = selected.get(key)
            if old and old.time == row.time and old != row:
                raise ValueError(f"разные значения для {article} на одну дату и время")
            if old is None or row.time > old.time:
                selected[key] = row
            rows_by_date[date] += 1
        except ValueError as exc:
            errors.append(f"строка {line}: {exc}")
    if errors:
        details = "; ".join(errors[:8])
        raise ValueError(f"Лист «{sheet_name}»: {details}. Ошибочных строк: {len(errors)}. Данные не записаны.")
    if not selected:
        raise ValueError("Файл WB пустой: нет строк данных.")
    rows = sorted(selected.values(), key=lambda r: (r.date, r.article))
    selected_counts = Counter(r.date for r in rows)
    duplicates = {date: count - selected_counts[date] for date, count in rows_by_date.items()}
    return WbPriceReport(rows, dict(rows_by_date), duplicates)
