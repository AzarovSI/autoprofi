"""Дневные отчёты цен WB и Ozon с датами и прямыми скидками из файла.

WB выбирает последнее время внутри дня. Ozon не содержит времени:
конфликтующие значения одной даты/артикула отклоняются.
Нулевая/пустая скидка для обоих МП означает отсутствие товара.
"""
import datetime as dt
import os
import zipfile
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from python_calamine import CalamineWorkbook

from . import util


OZON_COLUMNS = {
    "date": ("Дата",),
    "article": ("Артикул",),
    "upload_price": ("Цена продавца после акций, ₽",),
    "buyer_price": ("Цена продавца с соинвестом, ₽",),
    "spp": ("СПП/соинвест, %",),
}

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
class PriceReportRow:
    date: dt.date
    article: str
    upload_price: Decimal
    buyer_price: Decimal | None
    spp_pct: Decimal | None
    time: dt.time


@dataclass
class PriceReport:
    rows: list[PriceReportRow]
    rows_by_date: dict
    duplicates_by_date: dict

    @property
    def rows_in_file(self):
        return sum(self.rows_by_date.values())


def parse_ozon(path: str) -> PriceReport:
    """Цена после акций (G), соинвест (H), покупатель (I); дата в файле."""
    return _parse_daily_report(path, "Ozon")


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


def _validate_wb_container(path, label="WB"):
    """Ограничить ресурсы XLSX до его распаковки и построения таблиц в памяти."""
    if os.path.getsize(path) > WB_MAX_FILE_BYTES:
        raise ValueError(f"Файл {label} слишком большой: максимум 25 МБ.")
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 4096 or sum(e.file_size for e in entries) > WB_MAX_XML_BYTES:
                raise ValueError(f"Файл {label} слишком большой после распаковки. Разделите отчёт на несколько файлов.")
            if any(e.flag_bits & 1 for e in entries):
                raise ValueError("Защищённый паролем Excel не поддерживается.")
    except zipfile.BadZipFile:
        raise ValueError(f"Не удалось прочитать Excel. Проверьте файл отчёта {label}.") from None


def parse_wb(path: str) -> PriceReport:
    """Разобрать весь отчёт до записи. Любая ошибочная строка отменяет импорт.

    Ключ: дата + канонический артикул. Внутри дня выбираем самое позднее время,
    не порядок строк. Нулевая/пустая СПП означает отсутствие товара:
    buyer_price и spp_pct = NULL, загружаемая цена сохраняется.
    """
    return _parse_daily_report(path, "WB")


def _parse_daily_report(path, label):
    is_wb = label == "WB"
    required = WB_COLUMNS if is_wb else OZON_COLUMNS
    _validate_wb_container(path, label)
    wb = CalamineWorkbook.from_path(path)
    if len(wb.sheet_names) > 20:
        raise ValueError(f"Слишком много листов в файле {label}: максимум 20.")
    candidates = []
    cell_count = 0
    for sheet_name in wb.sheet_names:
        sheet = wb.get_sheet_by_name(sheet_name)
        cell_count += sheet.height * sheet.width
        if sheet.height > WB_MAX_ROWS + 1 or sheet.width > 64 or cell_count > WB_MAX_CELLS:
            raise ValueError(f"Слишком большой лист {label}. Разделите отчёт на файлы до 200 000 строк и 64 колонок.")
        data = sheet.to_python()
        if not data:
            continue
        header = {" ".join(str(h).split()): i for i, h in enumerate(data[0]) if h}
        columns = {
            key: next((header[h] for h in aliases if h in header), None)
            for key, aliases in required.items()
        }
        if all(i is not None for i in columns.values()):
            columns.update(time=header.get("Время") if is_wb else None,
                           region=header.get("Регион") if is_wb else None)
            candidates.append((sheet_name, data, columns))
    if len(candidates) != 1:
        if not is_wb:
            raise ValueError(
                "Нужен один лист отчёта Ozon с колонками: Дата, Артикул, "
                "Цена продавца после акций, ₽, СПП/соинвест, %, "
                "Цена продавца с соинвестом, ₽. Старый шаблон «Товары и цены» больше не используется."
            )
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
            buyer = _wb_price(value("buyer_price"), required["buyer_price"][0]) if spp is not None else None
            row = PriceReportRow(date, article, upload_price, buyer, spp, _wb_time(value("time")))
            key = (date, article)
            region = str(value("region") or "").strip().casefold()
            if region:
                if key in regions and regions[key] != region:
                    raise ValueError(f"несколько регионов для {article} на {date:%d.%m.%Y}; загрузите один регион")
                regions[key] = region
            old = selected.get(key)
            if old and old.time == row.time and old != row:
                suffix = " и время" if is_wb else ""
                raise ValueError(f"разные значения для {article} на одну дату{suffix}")
            if old is None or row.time > old.time:
                selected[key] = row
            rows_by_date[date] += 1
        except ValueError as exc:
            errors.append(f"строка {line}: {exc}")
    if errors:
        details = "; ".join(errors[:8])
        raise ValueError(f"Лист «{sheet_name}»: {details}. Ошибочных строк: {len(errors)}. Данные не записаны.")
    if not selected:
        raise ValueError(f"Файл {label} пустой: нет строк данных.")
    rows = sorted(selected.values(), key=lambda r: (r.date, r.article))
    selected_counts = Counter(r.date for r in rows)
    duplicates = {date: count - selected_counts[date] for date, count in rows_by_date.items()}
    return PriceReport(rows, dict(rows_by_date), duplicates)
