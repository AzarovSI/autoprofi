# -*- coding: utf-8 -*-
"""ABC → «Ревизия матрицы»: решения и заметки по товарам (таблица abc_revision, решение владельца 20.11.2026).

Решение — рабочая пометка ревизии, НЕ статус площадки: в catalog_marketplace ничего не пишется,
статусы сотрудники ставят сами. Истории нет — одна текущая запись на товар.
Цифры вкладки считаются на фронте (static/js/abc_module.js, renderRevision); отсюда — хранение
пометок и Excel-выгрузка того, что показано на экране. Правила — docs/business_rules.md, раздел «ABC».
"""
import io
from pathlib import Path

from . import db

MIGRATION = "2026_11_20_abc_revision"

DECISIONS = ("Усилить", "Оставить", "Только Ozon", "Только WB", "Вывести", "Подумать")
NOTE_MAX = 500


def migrate():
    """Таблица решений ревизии: один раз, атомарно, безопасно для нескольких воркеров."""
    with db.transaction() as tx:
        tx.execute("SELECT pg_advisory_xact_lock(20112026, 0)")
        tx.execute("""CREATE TABLE IF NOT EXISTS app_schema_migrations (
            name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())""")
        if tx.query_one("SELECT name FROM app_schema_migrations WHERE name=%s", (MIGRATION,)):
            return
        path = Path(__file__).resolve().parent.parent / "migrations" / (MIGRATION + ".sql")
        tx.conn.execute(path.read_text(encoding="utf-8"))
        tx.execute("INSERT INTO app_schema_migrations(name) VALUES (%s)", (MIGRATION,))


def load():
    """{АРТИКУЛ: {"decision", "note", "at", "by"}} — все пометки ревизии."""
    rows = db.query_all(
        """SELECT r.seller_article, r.decision, r.note, r.updated_at, COALESCE(NULLIF(u.display_name, ''), u.email) AS by_name
           FROM abc_revision r LEFT JOIN app_users u ON u.id = r.updated_by_user_id""")
    return {r["seller_article"]: {"decision": r["decision"] or "", "note": r["note"] or "",
                                  "at": r["updated_at"].isoformat() if r["updated_at"] else "",
                                  "by": r["by_name"] or ""}
            for r in rows}


def _clean(payload):
    """(артикул, поля) из запроса; ValueError с понятным текстом."""
    # Ключ — как у строк ABC (UPPER(seller_article) в abc_router), иначе пометка не найдёт товар на вкладке.
    sku = str((payload or {}).get("seller_article") or "").strip().upper()
    if not sku:
        raise ValueError("Не указан артикул")
    fields = {}
    if "decision" in payload:
        d = str(payload["decision"] or "").strip()
        if d and d not in DECISIONS:
            raise ValueError("Решение должно быть одним из: " + ", ".join(DECISIONS))
        fields["decision"] = d or None
    if "note" in payload:
        n = str(payload["note"] or "").strip()
        if len(n) > NOTE_MAX:
            raise ValueError(f"Заметка длиннее {NOTE_MAX} символов")
        fields["note"] = n or None
    if not fields:
        raise ValueError("Нет изменений: передайте решение или заметку")
    return sku, fields


def save(payload, user_id):
    """Записать решение и/или заметку товара; обе пустые — удалить строку. → запись товара или None."""
    sku, fields = _clean(payload)
    with db.transaction() as tx:
        tx.execute("SELECT pg_advisory_xact_lock(20112026, 1)")
        cur = tx.query_one("SELECT decision, note FROM abc_revision WHERE seller_article=%s", (sku,)) or {}
        row = {"decision": cur.get("decision"), "note": cur.get("note")}
        row.update(fields)
        if not row["decision"] and not row["note"]:
            tx.execute("DELETE FROM abc_revision WHERE seller_article=%s", (sku,))
            return sku, None
        tx.execute(
            """INSERT INTO abc_revision (seller_article, decision, note, updated_at, updated_by_user_id)
               VALUES (%s, %s, %s, now(), %s)
               ON CONFLICT (seller_article) DO UPDATE
               SET decision = EXCLUDED.decision, note = EXCLUDED.note,
                   updated_at = now(), updated_by_user_id = EXCLUDED.updated_by_user_id""",
            (sku, row["decision"], row["note"], user_id))
    return sku, {"decision": row["decision"] or "", "note": row["note"] or ""}


# --- Excel: то, что на экране (строки посчитаны клиентом) -------------------
_LVL_FILL = {0: "BDD7EE", 1: "DDEBF7", 2: "F2F7FC"}
_LVL_SIZE = {0: 12, 1: 11, 2: 10}
_CLS_FONT = {"A": "1E7D34", "B": "0563C1", "C": "7F7F7F", "Z": "7F7F7F", "D": "9C0006"}


_LEVELS = {"total", "item", "0", "1", "2"}


def check_rows(rows):
    """ValueError, если строки выгрузки не того вида (иначе workbook упадёт с 500)."""
    if not isinstance(rows, list) or not rows or len(rows) > 20000:
        raise ValueError("Нет строк для выгрузки")
    for row in rows:
        if not isinstance(row, dict) or str(row.get("level")) not in _LEVELS:
            raise ValueError("Неверная строка выгрузки")
        for k in ("q", "rev", "share", "prof", "prof_share", "margin", "q_m", "rev_m", "prof_m"):
            v = row.get(k)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
                raise ValueError("Неверное число в строке выгрузки")


def workbook(period, rows):
    """XLSX «Ревизия матрицы»: иерархия группировкой строк Excel, итог сверху. → bytes."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Ревизия матрицы"
    thin = Side(style="thin", color="BFC9D4")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill("solid", fgColor="2E5A87")
    cols = ["Группа / артикул", "Наименование", "Продажи, шт", "Выручка, руб", "Доля выручки", "Прибыль, руб", "Доля прибыли", "Маржа",
            "Продажи в мес., шт", "Выручка в мес., руб", "Прибыль в мес., руб", "ABC выручка", "ABC прибыль",
            "Статус Ozon", "Статус WB", "Статус Яндекс", "Решение", "Заметка"]
    widths = [44, 46, 12, 15, 9, 15, 9, 9, 12, 14, 14, 8, 9, 11, 11, 11, 13, 46]
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(cols))
    t = ws.cell(row=1, column=1, value=f"Ревизия матрицы · {period}")
    t.font = Font(name="Calibri", size=15, bold=True, color="FFFFFF")
    t.fill = PatternFill("solid", fgColor="1F3B57")
    t.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 24
    for i, (name, w) in enumerate(zip(cols, widths), start=1):
        c = ws.cell(row=3, column=i, value=name)
        c.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        c.fill = head_fill
        c.border = border
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[3].height = 30

    r = 4
    for row in rows or []:
        lvl = row.get("level")
        is_item = lvl == "item"
        vals = [row.get("name") or "", row.get("title") or "",
                row.get("q"), row.get("rev"), row.get("share"), row.get("prof"), row.get("prof_share"), row.get("margin"),
                row.get("q_m"), row.get("rev_m"), row.get("prof_m"),
                row.get("cr") or "", row.get("cp") or "",
                row.get("st_ozon") or "", row.get("st_vb") or "", row.get("st_ya") or "",
                row.get("decision") or "", row.get("note") or ""]
        depth = None if is_item else (-1 if lvl == "total" else int(lvl))
        size = 10 if is_item else (13 if depth < 0 else _LVL_SIZE.get(depth, 10))
        for i, v in enumerate(vals, start=1):
            c = ws.cell(row=r, column=i, value=v)
            if isinstance(v, str) and v:
                c.data_type = "s"   # заметка «=…» — текст, а не формула
            c.border = border
            c.font = Font(name="Calibri", size=10, color="333333")
            if 3 <= i <= 11:
                c.number_format = "0.0%" if i in (5, 7, 8) else "#,##0"
                c.alignment = Alignment(horizontal="right")
            elif 12 <= i <= 16:
                c.alignment = Alignment(horizontal="center")
        if is_item:
            # depth — уровень группы товара + 1 (товар под L3 — 3; под «Не распределены по группам» — 1)
            d = row.get("depth")
            d = d if isinstance(d, int) and not isinstance(d, bool) and 1 <= d <= 3 else 3
            ws.cell(row=r, column=1).alignment = Alignment(indent=d * 2)
            for i in (12, 13):
                cls = vals[i - 1]
                if cls in _CLS_FONT:
                    ws.cell(row=r, column=i).font = Font(name="Calibri", size=10, bold=True, color=_CLS_FONT[cls])
            ws.row_dimensions[r].outlineLevel = d + 1
        else:
            fill = PatternFill("solid", fgColor=_LVL_FILL.get(depth, "BDD7EE"))
            for i in range(1, len(cols) + 1):
                c = ws.cell(row=r, column=i)
                c.fill = fill
                c.font = Font(name="Calibri", size=size, bold=True, color="1F3B57")
            ws.cell(row=r, column=1).alignment = Alignment(indent=max(0, depth) * 2)
            if depth >= 0:
                ws.row_dimensions[r].outlineLevel = depth + 1
        for i in (6, 11):
            v = vals[i - 1]
            if isinstance(v, (int, float)) and v < 0:
                ws.cell(row=r, column=i).font = Font(name="Calibri", size=size, bold=not is_item, color="9C0006")
        r += 1
    ws.sheet_properties.outlinePr.summaryBelow = False
    ws.freeze_panes = "C4"
    ws.sheet_view.showGridLines = False
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
