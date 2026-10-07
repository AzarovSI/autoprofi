# -*- coding: utf-8 -*-
"""Продажи, шт. — план/факт по иерархии товаров и месяцам.

Модель данных:
  • Иерархия L1/L2/L3 — ОБЩАЯ, из catalog_items (одна строка на артикул).
  • Статус/менеджер — РАЗДЕЛЬНЫЕ по маркетплейсу (catalog_marketplace).
  • Факт (продано шт, средняя цена) — из fact_monthly (row_type='Товар').
  • План (шт) — из sales_plan (по seller_article/marketplace/year/month).

Прогноз выполнения (для незавершённого месяца):
  прогноз_шт = факт_шт ÷ дней_факта × дней_в_месяце,
  где дней_факта = число дня из report_uploads.fact_through_date последнего
  месячного отчёта (marketplace, year, month); если даты нет — месяц считается
  ПОЛНЫМ и прогноз = факт. Для «Сводной» прогноз считается по каждому МП
  отдельно и суммируется.
"""
import calendar
import datetime
import io

from fastapi import APIRouter, Depends, Query, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import db, auth
from ..util import l1_sort_key, cat_sort_key

router = APIRouter(prefix="/api/sales", tags=["sales"])

# Сопоставление ключа из URL → значение маркетплейса в БД (как в fact_monthly).
MP_MAP = {"ozon": "Ozon", "wb": "Wildberries", "wildberries": "Wildberries",
          "yandex": "Yandex", "ya": "Yandex"}
RU_MONTHS = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
             "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]


def _f(v):
    """numeric/None → float (None ⇒ 0.0)."""
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _mps_for(marketplace: str):
    """Список значений МП в БД для запрошенного режима."""
    m = (marketplace or "").strip().lower()
    if m == "cross":
        return ["Ozon", "Wildberries", "Yandex"]
    if m in ("wb", "wildberries"):
        return ["Wildberries"]
    if m in ("yandex", "ya"):
        return ["Yandex"]
    return ["Ozon"]


def _st_norm(s):
    """Нормализация статуса: пусто/NULL → '-' (как stDisplay на фронте)."""
    s = (s or "").strip()
    return s if s else "-"


@router.get("/plan_fact")
def plan_fact(
    marketplace: str = Query("ozon"),
    year: int = Query(...),
    status: str = Query(None),
    manager: str = Query(None),
    user=Depends(auth.get_current_user),
):
    """Дерево план/факт по месяцам выбранного года для одного МП или «Сводной»."""
    return _compute_plan_fact(marketplace, year, status, manager)


def _compute_plan_fact(marketplace, year, status=None, manager=None):
    """Построение дерева план/факт (вынесено из эндпоинта для переиспользования
    в выгрузке сводного отчёта). Параметры — обычные значения, без Query."""
    mps = _mps_for(marketplace)
    is_cross = len(mps) > 1

    # 4 независимых запроса (факт, план, иерархия, статусы/менеджеры) —
    # последовательно на тёплом пуле; построение дерева ниже тоже последовательное.
    fact_rows = db.query_all(
        """
        SELECT seller_article, marketplace, month,
               SUM(sales_qty) AS qty,
               SUM(sales_qty * COALESCE(avg_sale_price, 0)) AS rub
        FROM fact_monthly
        WHERE year = %s AND marketplace = ANY(%s) AND row_type = 'Товар'
        GROUP BY seller_article, marketplace, month
        """,
        (year, mps),
    )
    plan_rows = db.query_all(
        """
        SELECT seller_article, marketplace, month, plan_qty, is_manual, prev_qty
        FROM sales_plan
        WHERE year = %s AND marketplace = ANY(%s)
        """,
        (year, mps),
    )
    # Факт прошлого года (year-1) — для дорожки YoY в ячейках месяцев.
    # Тянем только количество (шт), помесячно; иерархию/статусы берём из текущего набора.
    fact_prev_rows = db.query_all(
        """
        SELECT seller_article, marketplace, month, SUM(sales_qty) AS qty
        FROM fact_monthly
        WHERE year = %s AND marketplace = ANY(%s) AND row_type = 'Товар'
        GROUP BY seller_article, marketplace, month
        """,
        (year - 1, mps),
    )
    cat_rows = db.query_all(
        """SELECT seller_article, category_l1, category_l2, category_l3
           FROM catalog_items""",
    )
    cm_rows = db.query_all(
        """SELECT seller_article, marketplace, status, manager
           FROM catalog_marketplace WHERE marketplace = ANY(%s)""",
        (mps,),
    )

    # 1) Факт: продажи шт и средняя цена по месяцам (только товарные строки).
    # fact[(art, mp)][month] = {"qty":..., "rub":...}
    fact = {}
    arts = set()
    for r in fact_rows:
        art = r.get("seller_article") or ""
        if not art:
            continue
        mp = r["marketplace"]
        m = int(r["month"] or 0)
        if not (1 <= m <= 12):
            continue
        arts.add(art)
        fact.setdefault((art, mp), {})[m] = {"qty": _f(r["qty"]), "rub": _f(r["rub"])}

    # 2) План шт по месяцам.
    plan = {}
    # Метаданные ручной правки плана per (art, mp)[month] — нужны ТОЛЬКО на листе
    # одиночного МП (не агрегируются в группы). Храним отдельно от plan.
    plan_meta = {}
    for r in plan_rows:
        art = r.get("seller_article") or ""
        if not art:
            continue
        m = int(r["month"] or 0)
        if not (1 <= m <= 12):
            continue
        arts.add(art)
        plan.setdefault((art, r["marketplace"]), {})[m] = _f(r["plan_qty"])
        prev = r.get("prev_qty")
        plan_meta.setdefault((art, r["marketplace"]), {})[m] = {
            "is_manual": bool(r.get("is_manual")),
            "prev_qty": (_f(prev) if prev is not None else None),
        }

    # 2b) Факт прошлого года по месяцам: fact_prev[(art, mp)][month] = qty.
    # Артикулы, которые есть ТОЛЬКО в прошлом году (новых продаж/плана нет), в дерево
    # не добавляем — показываем YoY лишь для существующих в текущем году позиций.
    fact_prev = {}
    for r in fact_prev_rows:
        art = r.get("seller_article") or ""
        if not art:
            continue
        m = int(r["month"] or 0)
        if not (1 <= m <= 12):
            continue
        fact_prev.setdefault((art, r["marketplace"]), {})[m] = _f(r["qty"])

    # 3) Иерархия (общая) + статус/менеджер по МП.
    cat = {r["seller_article"]: r for r in cat_rows}
    cm = {}  # (art, mp) -> {status, manager}
    for r in cm_rows:
        cm[(r["seller_article"], r["marketplace"])] = {
            "status": r.get("status"), "manager": r.get("manager"),
        }

    # 3b) Справочник как доп. источник списка товаров: артикулы, заведённые
    # вручную в catalog_marketplace по выбранному(ым) МП, показываем сразу —
    # ещё до загрузки факта/плана. Месячные ячейки у них будут нулевыми
    # (article_months вернёт нули), а факт/план «приклеятся» автоматически
    # по seller_article, как только появятся в загруженном отчёте/плане.
    #
    # ВАЖНО: добавляем ТОЛЬКО полностью распределённые карточки
    # (K1/K2/K3 из catalog_items + статус + менеджер по МП). Иначе сотни
    # старых записей catalog_marketplace без распределения сыпались бы
    # в «(без категории)» и засоряли бы дерево продаж.
    for r in cm_rows:
        a = r.get("seller_article") or ""
        if not a:
            continue
        ci = cat.get(a) or {}
        cmv = cm.get((a, r.get("marketplace"))) or {}
        if (not ci.get("category_l1") or not ci.get("category_l2")
                or not ci.get("category_l3") or not cmv.get("status")
                or not cmv.get("manager")):
            continue
        arts.add(a)

    # 4) «Факт по дату»: день месяца последнего месячного отчёта по (mp, month).
    df_rows = db.query_all(
        """SELECT marketplace, month, fact_through_date
           FROM report_uploads
           WHERE period_kind = 'monthly' AND year = %s AND marketplace = ANY(%s)
                 AND fact_through_date IS NOT NULL
           ORDER BY id""",
        (year, mps),
    )
    days_fact = {mp: {} for mp in mps}  # mp -> month -> day
    for r in df_rows:
        mp = r["marketplace"]
        m = int(r["month"] or 0)
        d = r["fact_through_date"]
        if mp in days_fact and 1 <= m <= 12 and d is not None:
            days_fact[mp][m] = d.day  # последний по id перезаписывает

    # days_fact в ответ (по ключам ozon/wb/yandex для фронта).
    _MP_KEY = {"Ozon": "ozon", "Wildberries": "wb", "Yandex": "yandex"}
    df_out = {}
    for mp in mps:
        key = _MP_KEY.get(mp, "ozon")
        df_out[key] = {str(m): days_fact[mp][m] for m in days_fact[mp]}

    # 4b) Данные для рублёвого плана (галочка «продажи в руб.»):
    #   (а) закрыт ли месяц — по последней загрузке fact_monthly за период;
    #   (б) последняя недельная цена продажи SKU — для незакрытых месяцев.
    # Закрытость определяется по строке report_uploads, на которую ссылается
    # текущий fact_monthly за период (fact_monthly хранит только последнюю
    # загрузку). fact_through_date IS NULL → месяц ЗАКРЫТ (закрывающий отчёт),
    # задана → НЕ закрыт (промежуточный). Нет строк fact_monthly → НЕ закрыт.
    closed_rows = db.query_all(
        """SELECT DISTINCT fm.marketplace, fm.month, ru.fact_through_date
           FROM fact_monthly fm
           JOIN report_uploads ru ON ru.id = fm.upload_id
           WHERE fm.year = %s AND fm.marketplace = ANY(%s)""",
        (year, mps),
    )
    month_closed = {}  # (mp, month) -> bool
    for r in closed_rows:
        mp = r["marketplace"]
        m = int(r["month"] or 0)
        if 1 <= m <= 12:
            # Несколько upload_id за период не ожидается (хранится только
            # последняя загрузка), но на всякий случай: закрыт, если хотя бы
            # одна строка периода = закрывающая (through=NULL).
            prev = month_closed.get((mp, m), False)
            month_closed[(mp, m)] = prev or (r["fact_through_date"] is None)

    # Последняя недельная цена продажи по каждому SKU: avg_sale_price из
    # ПОСЛЕДНЕГО недельного отчёта (max year, week), где у товара были продажи.
    # Применяется к НЕзакрытым месяцам (промежуточным/текущему/будущим).
    weekly_price_rows = db.query_all(
        """SELECT DISTINCT ON (seller_article, marketplace)
                  seller_article, marketplace, avg_sale_price
           FROM fact_weekly
           WHERE marketplace = ANY(%s) AND row_type = 'Товар'
                 AND COALESCE(sales_qty, 0) > 0
                 AND COALESCE(avg_sale_price, 0) > 0
           ORDER BY seller_article, marketplace, year DESC, week DESC""",
        (mps,),
    )
    last_weekly_price = {}  # (art, mp) -> price
    for r in weekly_price_rows:
        art = r.get("seller_article") or ""
        if art:
            last_weekly_price[(art, r["marketplace"])] = _f(r["avg_sale_price"])

    # 5) Фильтры по статусу/менеджеру (per-MP; для cross — хотя бы на одном МП).
    def passes_filters(art):
        if not status and not manager:
            return True
        ok_status = not status
        ok_manager = not manager
        for mp in mps:
            info = cm.get((art, mp))
            if info:
                if status and _st_norm(info.get("status")) == status:
                    ok_status = True
                if manager and (info.get("manager") or "") == manager:
                    ok_manager = True
        return ok_status and ok_manager

    # 6) Месячные данные по каждому артикулу.
    def empty_month():
        return {"plan": 0.0, "fact": 0.0, "fact_prev": 0.0, "sum_plan": 0.0,
                "sum_fact": 0.0, "fc": 0.0, "plan_rub": 0.0, "partial": False}

    def article_months(art):
        months = [empty_month() for _ in range(12)]
        tot_sp = tot_sf = 0.0
        for m in range(1, 13):
            dim = calendar.monthrange(year, m)[1]
            cell = months[m - 1]
            for mp in mps:
                pl = plan.get((art, mp), {}).get(m, 0.0)
                fc_cell = fact.get((art, mp), {}).get(m)
                fq = fc_cell["qty"] if fc_cell else 0.0
                frub = fc_cell["rub"] if fc_cell else 0.0
                # средняя цена месяца по МП (для суммы плана)
                price = (frub / fq) if fq else 0.0
                # Цена для рублёвого ПЛАНА (галочка «продажи в руб.»):
                #  закрытый месяц → цена из fact_monthly (= frub/fq);
                #  незакрытый (промежуточный/текущий/будущий, либо нет
                #  месячного отчёта) → последняя недельная цена с продажами.
                if month_closed.get((mp, m), False):
                    plan_price = price
                else:
                    plan_price = last_weekly_price.get((art, mp), 0.0)
                cell["plan"] += pl
                cell["fact"] += fq
                cell["fact_prev"] += fact_prev.get((art, mp), {}).get(m, 0.0)
                cell["sum_plan"] += pl * price
                cell["sum_fact"] += frub
                # План_руб считаем ОТДЕЛЬНО по каждому МП (в cross цены разные)
                # и суммируем — по правилам спеки.
                cell["plan_rub"] += pl * plan_price
                # прогноз по этому МП
                day = days_fact.get(mp, {}).get(m)
                if day and day < dim:
                    cell["fc"] += (fq / day * dim) if day else fq
                    cell["partial"] = True
                else:
                    cell["fc"] += fq
            tot_sp += cell["sum_plan"]
            tot_sf += cell["sum_fact"]
        return months, tot_sp, tot_sf

    # 7) Сбор дерева L1→L2→L3→Товар.
    def new_node(key, name, level):
        return {"key": key, "name": name, "level": level,
                "months": [empty_month() for _ in range(12)],
                "total_sum_plan": 0.0, "total_sum_fact": 0.0,
                "children": {}, "leaf_info": None}

    def add_into(node, months, tsp, tsf):
        for i in range(12):
            for k in ("plan", "fact", "fact_prev", "sum_plan", "sum_fact", "fc", "plan_rub"):
                node["months"][i][k] += months[i][k]
            if months[i]["partial"]:
                node["months"][i]["partial"] = True
        node["total_sum_plan"] += tsp
        node["total_sum_fact"] += tsf

    root = new_node("__root__", "Итоги", 0)
    for art in sorted(arts):
        if not passes_filters(art):
            continue
        months, tsp, tsf = article_months(art)
        ci = cat.get(art) or {}
        l1 = ci.get("category_l1") or "(без категории)"
        l2 = ci.get("category_l2") or "(без категории)"
        l3 = ci.get("category_l3") or "(без категории)"

        add_into(root, months, tsp, tsf)
        # L1
        n1 = root["children"].get(l1) or new_node(l1, l1, 1)
        root["children"][l1] = n1
        add_into(n1, months, tsp, tsf)
        # L2
        k2 = f"{l1}|{l2}"
        n2 = n1["children"].get(k2) or new_node(k2, l2, 2)
        n1["children"][k2] = n2
        add_into(n2, months, tsp, tsf)
        # L3
        k3 = f"{l1}|{l2}|{l3}"
        n3 = n2["children"].get(k3) or new_node(k3, l3, 3)
        n2["children"][k3] = n3
        add_into(n3, months, tsp, tsf)
        # Товар
        k4 = f"{k3}|{art}"
        n4 = new_node(k4, art, 4)
        n4["months"] = months
        n4["total_sum_plan"] = tsp
        n4["total_sum_fact"] = tsf
        # is_manual/prev_qty осмысленны только для одиночного МП (ozon/wb):
        # кладём прямо в месяцы листа, минуя add_into (группы план не редактируют).
        if not is_cross:
            meta_mp = plan_meta.get((art, mps[0]), {})
            for mo in range(1, 13):
                mv = meta_mp.get(mo)
                if mv:
                    n4["months"][mo - 1]["is_manual"] = mv["is_manual"]
                    n4["months"][mo - 1]["prev_qty"] = mv["prev_qty"]
        oz = cm.get((art, "Ozon")) or {}
        wb = cm.get((art, "Wildberries")) or {}
        ya = cm.get((art, "Yandex")) or {}
        single = cm.get((art, mps[0])) or {}
        n4["leaf_info"] = {
            "seller_article": art,
            "status": single.get("status"),
            "manager": single.get("manager"),
            "ozon_status": oz.get("status"),
            "vb_status": wb.get("status"),
            "ya_status": ya.get("status"),
        }
        n3["children"][k4] = n4

    def finalize(node):
        children = [finalize(c) for c in node["children"].values()]
        if children:
            # Единые правила порядка (app/util.py): L1 — фиксированный список
            # (ECOM, затем ТД «АВТОПРОФИ»), L2/L3 — алфавит, L4 — по артикулу.
            if all(c["level"] == 4 for c in children):
                children.sort(key=lambda c: (c["leaf_info"] or {}).get("seller_article") or "")
            elif node["level"] == 0:
                children.sort(key=lambda c: l1_sort_key(c["name"]))
            else:
                children.sort(key=lambda c: cat_sort_key(c["name"]))
        return {
            "key": node["key"], "name": node["name"], "level": node["level"],
            "months": [_round_month(mm) for mm in node["months"]],
            "total_sum_plan": round(node["total_sum_plan"], 2),
            "total_sum_fact": round(node["total_sum_fact"], 2),
            "leaf_info": node["leaf_info"], "children": children,
        }

    def _round_month(mm):
        # is_manual/prev_qty есть только на листьях одиночного МП; на группах и
        # в cross отдаём безопасные значения по умолчанию (False/None).
        prev = mm.get("prev_qty")
        return {
            "plan": round(mm["plan"], 3),
            "fact": round(mm["fact"], 3),
            "fact_prev": round(mm["fact_prev"], 3),
            "sum_plan": round(mm["sum_plan"], 2),
            "sum_fact": round(mm["sum_fact"], 2),
            "fc": round(mm["fc"], 3),
            "plan_rub": round(mm["plan_rub"], 2),
            "partial": mm["partial"],
            "is_manual": bool(mm.get("is_manual", False)),
            "prev_qty": (round(prev, 3) if prev is not None else None),
        }

    # 8) Списки для фильтров и доступные годы.
    statuses = sorted({_st_norm(v["status"]) for v in cm.values()})
    managers = sorted({v["manager"] for v in cm.values() if v.get("manager")})
    yrs = [r["year"] for r in db.query_all(
        """SELECT DISTINCT year FROM fact_monthly
           UNION SELECT DISTINCT year FROM sales_plan ORDER BY 1""")]
    # Следующий год (текущий + 1) всегда доступен для планирования, даже
    # если факта/плана за него ещё нет — иначе план на новый год не выгрузить
    # и не загрузить через UI (год берётся из фильтра, а не из шапки файла).
    next_year = datetime.date.today().year + 1
    if next_year not in yrs:
        yrs = sorted(set(yrs) | {next_year})

    return {
        "marketplace": marketplace, "year": year, "prev_year": year - 1, "is_cross": is_cross,
        "years": yrs, "months": list(range(1, 13)),
        "statuses": statuses, "managers": managers,
        "days_fact": df_out,
        "tree": finalize(root),
    }


class PlanCellIn(BaseModel):
    seller_article: str
    marketplace: str
    year: int
    month: int
    plan_qty: float


@router.post("/plan_cell")
def plan_cell(body: PlanCellIn, user=Depends(auth.get_current_user)):
    """Инлайн-правка одной ячейки плана (шт) на вкладке ozon/wb.

    Правит ЛЮБОЙ авторизованный пользователь. UPSERT: если строка есть — UPDATE
    plan_qty + is_manual=true (prev_qty НЕ трогаем, оно хранит значение из
    последнего Excel-импорта); если нет — INSERT с is_manual=true, prev_qty=NULL.
    """
    m = (body.marketplace or "").strip().lower()
    if m == "cross" or m not in MP_MAP:
        raise HTTPException(status_code=400, detail="План редактируется по одному маркетплейсу (ozon или wb)")
    mp_db = MP_MAP[m]
    if not (1 <= body.month <= 12):
        raise HTTPException(status_code=400, detail="Некорректный месяц")
    if body.plan_qty is None or body.plan_qty < 0:
        raise HTTPException(status_code=400, detail="Некорректное значение плана")
    art = (body.seller_article or "").strip()
    if not art:
        raise HTTPException(status_code=400, detail="Не указан артикул")

    existing = db.query_one(
        "SELECT prev_qty FROM sales_plan "
        "WHERE seller_article=%s AND marketplace=%s AND year=%s AND month=%s",
        (art, mp_db, body.year, body.month),
    )
    if existing:
        db.execute(
            "UPDATE sales_plan SET plan_qty=%s, is_manual=true, updated_at=now() "
            "WHERE seller_article=%s AND marketplace=%s AND year=%s AND month=%s",
            (body.plan_qty, art, mp_db, body.year, body.month),
        )
        prev = existing.get("prev_qty")
    else:
        db.execute(
            "INSERT INTO sales_plan "
            "(seller_article, marketplace, year, month, plan_qty, is_manual, prev_qty, updated_at) "
            "VALUES (%s,%s,%s,%s,%s, true, NULL, now())",
            (art, mp_db, body.year, body.month, body.plan_qty),
        )
        prev = None

    return {
        "ok": True,
        "plan_qty": float(body.plan_qty),
        "is_manual": True,
        "prev_qty": (float(prev) if prev is not None else None),
    }


def _plan_universe(mp_db: str, year: int):
    """Артикулы для шаблона плана (есть факт или план за год по этому МП).

    Текущий статус берём из catalog_marketplace по этому маркетплейсу —
    он добавляется в шаблон СПРАВОЧНО (для анализа), при загрузке файла
    статус не читается и не перезаписывает данные в системе.

    Если за запрошенный год данных ещё нет (например, планируем будущий год —
    факта/плана нет), берём список артикулов за последний доступный год с
    данными по этому МП, чтобы шаблон не вышел пустым.
    """
    def _fetch(yr):
        return db.query_all(
            """
            SELECT DISTINCT t.seller_article, cm.status AS status
            FROM (
                SELECT seller_article FROM fact_monthly
                  WHERE year = %s AND marketplace = %s AND row_type = 'Товар'
                UNION
                SELECT seller_article FROM sales_plan
                  WHERE year = %s AND marketplace = %s
            ) t
            LEFT JOIN catalog_marketplace cm
                   ON cm.seller_article = t.seller_article AND cm.marketplace = %s
            WHERE t.seller_article IS NOT NULL AND t.seller_article <> ''
            ORDER BY 1
            """,
            (yr, mp_db, yr, mp_db, mp_db),
        )

    rows = _fetch(year)
    if not rows:
        # последний год с фактом/планом по этому МП (≤ запрошенного)
        fb = db.query_one(
            """
            SELECT MAX(year) AS y FROM (
                SELECT year FROM fact_monthly
                  WHERE marketplace = %s AND row_type = 'Товар' AND year <= %s
                UNION
                SELECT year FROM sales_plan
                  WHERE marketplace = %s AND year <= %s
            ) z
            """,
            (mp_db, year, mp_db, year),
        )
        fb_year = fb.get("y") if fb else None
        if fb_year and fb_year != year:
            rows = _fetch(fb_year)
    return rows


@router.get("/plan_export")
def plan_export(
    marketplace: str = Query("ozon"),
    year: int = Query(...),
    user=Depends(auth.get_current_user),
):
    """Excel-шаблон плана продаж (шт) одного МП на выбранный год."""
    import openpyxl
    from openpyxl.styles import Font
    m = (marketplace or "").strip().lower()
    if m == "cross" or m not in MP_MAP:
        raise HTTPException(status_code=400, detail="План редактируется по одному маркетплейсу (ozon или wb)")
    mp_db = MP_MAP[m]

    # Текущий план: (art, month) -> plan_qty; ручные (is_manual) ячейки помечаем
    # для последующей зелёной подсветки шрифта в Excel.
    cur = {}
    manual = set()
    for r in db.query_all(
        "SELECT seller_article, month, plan_qty, is_manual FROM sales_plan WHERE year=%s AND marketplace=%s",
        (year, mp_db),
    ):
        cur[(r["seller_article"], int(r["month"]))] = r["plan_qty"]
        if r.get("is_manual"):
            manual.add((r["seller_article"], int(r["month"])))

    rows = _plan_universe(mp_db, year)

    # Иерархия групп товаров (из раздела «Справочник») — ОБЩАЯ, по артикулу.
    # art -> (l1, l2, l3). Добавляется в шаблон СПРАВОЧНО (при загрузке не читается).
    cat = {}
    for r in db.query_all(
        """SELECT seller_article, category_l1, category_l2, category_l3
           FROM catalog_items""",
    ):
        cat[r["seller_article"]] = (
            r.get("category_l1"), r.get("category_l2"), r.get("category_l3"),
        )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"План {mp_db} {year}"[:31]
    # Столбцы: Артикул | Уровень 1 | Уровень 2 | Уровень 3 |
    #          Текущий статус (справочно) | 12 месяцев.
    # Три столбца иерархии групп вставлены между «Артикул» и «Текущий статус».
    header = ["Артикул", "Уровень 1", "Уровень 2", "Уровень 3", "Текущий статус"] + \
             [f"{RU_MONTHS[m]} {year}" for m in range(1, 13)]
    ws.append(header)
    green = Font(color="16A360")
    for r in rows:
        art = r["seller_article"]
        l1, l2, l3 = cat.get(art, (None, None, None))
        line = [art, l1, l2, l3, r.get("status")]
        for mo in range(1, 13):
            v = cur.get((art, mo))
            line.append(float(v) if v is not None else None)
        ws.append(line)
        # Ручные ячейки (is_manual) — зелёный шрифт. Месяцы идут с 6-й колонки
        # (Артикул|Ур1|Ур2|Ур3|Статус = 5 столбцов), поэтому месяц mo → колонка 5+mo.
        row_idx = ws.max_row
        for mo in range(1, 13):
            if (art, mo) in manual and cur.get((art, mo)) is not None:
                ws.cell(row=row_idx, column=5 + mo).font = green

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"plan_{m}_{year}.xlsx"
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




@router.get("/report_export")
def report_export(
    marketplace: str = Query("cross"),
    year: int = Query(...),
    status: str = Query(None),
    manager: str = Query(None),
    user=Depends(auth.get_current_user),
):
    """Красивый Excel-отчёт «Продажи, шт» с иерархией номенклатуры (группировка
    Excel/outline), помесячным планом и фактом за год и прогнозом выполнения
    текущего месяца. Предназначен для вкладки «Сводная», но работает и для
    одиночного МП.
    """
    import datetime as _dt
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    data = _compute_plan_fact(marketplace, year, status, manager)
    tree = data["tree"]
    is_cross = data["is_cross"]
    cur_month = _dt.date.today().month if year == _dt.date.today().year else 0

    # --- Палитра (светлая, деловая) ---
    C_TITLE = "1F3B57"      # тёмно-синий — заголовок отчёта
    C_HEADER = "2E5A87"     # синий — шапка таблицы
    C_MON_A = "DCE6F1"      # чётные месяцы — светло-голубой
    C_MON_B = "EAF1F8"      # нечётные месяцы — ещё светлее
    C_TOTAL = "FCE4D6"      # итог за год — персиковый
    C_FC_HD = "E8DAEF"      # шапка прогноза — сиреневый
    L1_FILL = "BDD7EE"      # уровень 1 — насыщенно-голубой
    L2_FILL = "DDEBF7"      # уровень 2
    L3_FILL = "F2F7FC"      # уровень 3
    GREEN = "1E7D34"; AMBER = "9C6500"; REDC = "9C0006"
    GREEN_BG = "C6EFCE"; AMBER_BG = "FFEB9C"; RED_BG = "FFC7CE"

    thin = Side(style="thin", color="BFC9D4")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    f_title = Font(name="Calibri", size=15, bold=True, color="FFFFFF")
    f_sub = Font(name="Calibri", size=10, italic=True, color="555555")
    f_head = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    f_head_dark = Font(name="Calibri", size=10, bold=True, color="1F3B57")
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center")
    right = Alignment(horizontal="right", vertical="center")

    wb = openpyxl.Workbook()
    ws = wb.active
    mp_name = {"cross": "Сводная (OZON + Wildberries + Яндекс)",
               "ozon": "OZON", "wb": "Wildberries",
               "wildberries": "Wildberries",
               "yandex": "Яндекс", "ya": "Яндекс"}.get((marketplace or "").strip().lower(), marketplace)
    ws.title = f"Продажи {year}"[:31]

    # Колонки: A=Номенклатура | далее по месяцам (План,Факт) [+Прогноз% для тек.] | Итог(План,Факт)
    # Считаем раскладку колонок.
    NAME_COL = 1
    col = 2
    month_cols = {}  # m -> (plan_col, fact_col, fc_col|None)
    for m in range(1, 13):
        pc = col; fc_col = None
        col += 1
        fac = col; col += 1
        if m == cur_month:
            fc_col = col; col += 1
        month_cols[m] = (pc, fac, fc_col)
    total_plan_col = col; col += 1
    total_fact_col = col; last_col = col

    # --- Строка 1: заголовок отчёта ---
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    tc = ws.cell(row=1, column=1, value=f"Отчёт по продажам (шт): план / факт — {mp_name}, {year} г.")
    tc.font = f_title; tc.alignment = Alignment(horizontal="left", vertical="center")
    tc.fill = PatternFill("solid", fgColor=C_TITLE)
    ws.row_dimensions[1].height = 26

    # --- Строка 2: подзаголовок с датой формирования и пояснением прогноза ---
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=last_col)
    sub = f"Сформировано {_dt.date.today().strftime('%d.%m.%Y')}."
    if cur_month:
        sub += (f" Прогноз % — ожидаемое выполнение плана за {RU_MONTHS[cur_month]} "
                f"(факт экстраполирован на полный месяц).")
    sc = ws.cell(row=2, column=1, value=sub)
    sc.font = f_sub; sc.alignment = left

    # --- Строки 3-4: шапка таблицы (2 уровня) ---
    HR1, HR2 = 3, 4
    # Номенклатура (объединяем 2 строки)
    ws.merge_cells(start_row=HR1, start_column=NAME_COL, end_row=HR2, end_column=NAME_COL)
    hc = ws.cell(row=HR1, column=NAME_COL, value="Номенклатура")
    hc.font = f_head; hc.fill = PatternFill("solid", fgColor=C_HEADER)
    hc.alignment = center; hc.border = border
    ws.cell(row=HR2, column=NAME_COL).border = border
    ws.cell(row=HR2, column=NAME_COL).fill = PatternFill("solid", fgColor=C_HEADER)

    # Подпись «План» в шапке — светло-голубым (связь с синими цифрами плана).
    f_head_plan = Font(name="Calibri", size=10, bold=True, color="8FC3FF")

    def _paint_head(c, val, fill, font=f_head):
        cell = ws.cell(row=c[0], column=c[1], value=val)
        cell.font = font; cell.alignment = center; cell.border = border
        cell.fill = PatternFill("solid", fgColor=fill)
        return cell

    for m in range(1, 13):
        pc, fac, fcc = month_cols[m]
        span_end = fcc if fcc else fac
        ws.merge_cells(start_row=HR1, start_column=pc, end_row=HR1, end_column=span_end)
        mfill = C_MON_A if m % 2 == 0 else C_MON_B
        top = ws.cell(row=HR1, column=pc, value=f"{RU_MONTHS[m]}")
        top.font = f_head_dark; top.alignment = center; top.border = border
        top.fill = PatternFill("solid", fgColor=mfill)
        for cc in range(pc, span_end + 1):
            ws.cell(row=HR1, column=cc).border = border
            ws.cell(row=HR1, column=cc).fill = PatternFill("solid", fgColor=mfill)
        _paint_head((HR2, pc), "План", C_HEADER, font=f_head_plan)
        _paint_head((HR2, fac), "Факт", C_HEADER)
        if fcc:
            _paint_head((HR2, fcc), "Прогноз %", "7D3C98")

    # Итог за год
    ws.merge_cells(start_row=HR1, start_column=total_plan_col, end_row=HR1, end_column=total_fact_col)
    tt = ws.cell(row=HR1, column=total_plan_col, value="Итог за год")
    tt.font = f_head_dark; tt.alignment = center; tt.border = border
    tt.fill = PatternFill("solid", fgColor=C_TOTAL)
    ws.cell(row=HR1, column=total_fact_col).border = border
    ws.cell(row=HR1, column=total_fact_col).fill = PatternFill("solid", fgColor=C_TOTAL)
    _paint_head((HR2, total_plan_col), "План", C_HEADER, font=f_head_plan)
    _paint_head((HR2, total_fact_col), "Факт", C_HEADER)

    ws.row_dimensions[HR1].height = 18
    ws.row_dimensions[HR2].height = 26

    # --- Тело: обход дерева с outline-группировкой ---
    NUM_FMT = "#,##0"
    PCT_FMT = "0\\%"
    PLAN_BLUE = "0563C1"  # синий для цифр плана (визуально отделяет от факта)
    row = HR2 + 1
    level_fills = {1: L1_FILL, 2: L2_FILL, 3: L3_FILL}

    def _fc_style(pct):
        if pct >= 100:
            return Font(name="Calibri", size=10, bold=True, color=GREEN), GREEN_BG
        if pct >= 70:
            return Font(name="Calibri", size=10, bold=True, color=AMBER), AMBER_BG
        return Font(name="Calibri", size=10, bold=True, color=REDC), RED_BG

    def write_node(node, depth):
        nonlocal row
        r = row; row += 1
        lvl = node["level"]
        is_leaf = lvl == 4
        # Имя с отступом
        indent = "    " * (depth)
        art = ""
        if is_leaf and node.get("leaf_info"):
            art = node["leaf_info"].get("seller_article") or ""
        label = node["name"] if not is_leaf else (f"{node['name']}" if node['name'] else art)
        namec = ws.cell(row=r, column=NAME_COL, value=label)
        namec.alignment = Alignment(horizontal="left", vertical="center", indent=depth)
        namec.border = border
        if is_leaf:
            namec.font = Font(name="Calibri", size=10, color="333333")
        else:
            namec.font = Font(name="Calibri", size=10, bold=True, color="1F3B57")
            fill = level_fills.get(lvl, "FFFFFF")
            namec.fill = PatternFill("solid", fgColor=fill)
        # Месяцы
        yr_plan = 0.0; yr_fact = 0.0
        for m in range(1, 13):
            pc, fac, fcc = month_cols[m]
            mm = node["months"][m - 1]
            pl = mm["plan"]; ft = mm["fact"]
            yr_plan += pl; yr_fact += ft
            pcell = ws.cell(row=r, column=pc, value=(round(pl) if pl else None))
            fcell = ws.cell(row=r, column=fac, value=(round(ft) if ft else None))
            # План — синий, факт — тёмный/серый; группы жирные.
            for cc, is_plan in ((pcell, True), (fcell, False)):
                cc.number_format = NUM_FMT; cc.alignment = right; cc.border = border
                if not is_leaf:
                    cc.font = Font(name="Calibri", size=10, bold=True,
                                   color=(PLAN_BLUE if is_plan else "1F3B57"))
                    cc.fill = PatternFill("solid", fgColor=level_fills.get(lvl, "FFFFFF"))
                else:
                    # Уровень товара: план синий, но НЕ жирный (как факт рядом).
                    cc.font = Font(name="Calibri", size=10, bold=False,
                                   color=(PLAN_BLUE if is_plan else "333333"))
            if fcc:
                # прогноз % = fc/plan*100 (только текущий месяц)
                fcv = mm.get("fc", 0.0)
                pct = (fcv / pl * 100.0) if pl else 0.0
                fccell = ws.cell(row=r, column=fcc,
                                 value=(round(pct) if pl else None))
                fccell.number_format = PCT_FMT; fccell.alignment = center; fccell.border = border
                if pl:
                    fnt, bg = _fc_style(pct)
                    fccell.font = fnt
                    fccell.fill = PatternFill("solid", fgColor=bg)
                elif not is_leaf:
                    fccell.fill = PatternFill("solid", fgColor=level_fills.get(lvl, "FFFFFF"))
        # Итог за год
        tpc = ws.cell(row=r, column=total_plan_col, value=(round(yr_plan) if yr_plan else None))
        tfc = ws.cell(row=r, column=total_fact_col, value=(round(yr_fact) if yr_fact else None))
        for cc, is_plan in ((tpc, True), (tfc, False)):
            cc.number_format = NUM_FMT; cc.alignment = right; cc.border = border
            if not is_leaf:
                cc.font = Font(name="Calibri", size=10, bold=True,
                               color=(PLAN_BLUE if is_plan else "1F3B57"))
                cc.fill = PatternFill("solid", fgColor=C_TOTAL)
            else:
                # Итог за год на уровне товара: план синий, но НЕ жирный.
                cc.font = Font(name="Calibri", size=10, bold=False,
                               color=(PLAN_BLUE if is_plan else "555555"))
        # outline
        if depth > 0:
            ws.row_dimensions[r].outline_level = min(depth, 7)
        # дети
        for ch in node.get("children", []):
            write_node(ch, depth + 1)

    # Верхняя строка ИТОГО по корню дерева, затем дети L1 с группировкой.
    root_children = tree.get("children", [])
    _write_total_root(ws, tree, month_cols, total_plan_col, total_fact_col,
                      HR2 + 1, border, cur_month)
    row = HR2 + 2
    for ch in root_children:
        write_node(ch, 1)

    # --- Оформление колонок ---
    ws.column_dimensions[get_column_letter(NAME_COL)].width = 46
    for m in range(1, 13):
        pc, fac, fcc = month_cols[m]
        ws.column_dimensions[get_column_letter(pc)].width = 8
        ws.column_dimensions[get_column_letter(fac)].width = 8
        if fcc:
            ws.column_dimensions[get_column_letter(fcc)].width = 9
    ws.column_dimensions[get_column_letter(total_plan_col)].width = 10
    ws.column_dimensions[get_column_letter(total_fact_col)].width = 10

    # Заморозка: шапка + первый столбец
    ws.freeze_panes = ws.cell(row=HR2 + 1, column=2)
    # Группировка сверху (итоги над детьми)
    ws.sheet_properties.outlinePr.summaryBelow = False
    ws.sheet_view.showGridLines = False

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    tag = (marketplace or "cross").strip().lower()
    fname = f"otchet_prodazhi_{tag}_{year}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            # НЕ кешируем файл: иначе Cloudflare/браузер отдаёт старый отчёт (до правок).
            "Content-Disposition": f"attachment; filename={fname}",
            "Cache-Control": "no-cache, no-store, must-revalidate, private",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def _write_total_root(ws, tree, month_cols, total_plan_col, total_fact_col,
                      r, border, cur_month):
    """Верхняя строка ИТОГО по всей номенклатуре (сумма корня дерева)."""
    from openpyxl.styles import Font, PatternFill, Alignment
    ROOT_FILL = "1F3B57"
    NUM_FMT = "#,##0"; PCT_FMT = "0\\%"
    fwhite = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    # План в строке ИТОГО — светло-голубой (читаемо на тёмно-синем фоне).
    fblue = Font(name="Calibri", size=10, bold=True, color="8FC3FF")
    right = Alignment(horizontal="right", vertical="center")
    center = Alignment(horizontal="center", vertical="center")
    namec = ws.cell(row=r, column=1, value="ИТОГО по всей номенклатуре")
    namec.font = fwhite; namec.alignment = Alignment(horizontal="left", vertical="center")
    namec.fill = PatternFill("solid", fgColor=ROOT_FILL); namec.border = border
    yr_plan = 0.0; yr_fact = 0.0
    for m in range(1, 13):
        pc, fac, fcc = month_cols[m]
        mm = tree["months"][m - 1]
        pl = mm["plan"]; ft = mm["fact"]
        yr_plan += pl; yr_fact += ft
        for cc, val, is_plan in ((pc, pl, True), (fac, ft, False)):
            cell = ws.cell(row=r, column=cc, value=(round(val) if val else None))
            cell.number_format = NUM_FMT; cell.alignment = right; cell.border = border
            cell.font = fblue if is_plan else fwhite
            cell.fill = PatternFill("solid", fgColor=ROOT_FILL)
        if fcc:
            fcv = mm.get("fc", 0.0)
            pct = (fcv / pl * 100.0) if pl else 0.0
            cell = ws.cell(row=r, column=fcc, value=(round(pct) if pl else None))
            cell.number_format = PCT_FMT; cell.alignment = center; cell.border = border
            cell.font = fwhite; cell.fill = PatternFill("solid", fgColor=ROOT_FILL)
    for cc, val, is_plan in ((total_plan_col, yr_plan, True), (total_fact_col, yr_fact, False)):
        cell = ws.cell(row=r, column=cc, value=(round(val) if val else None))
        cell.number_format = NUM_FMT; cell.alignment = right; cell.border = border
        cell.font = fblue if is_plan else fwhite
        cell.fill = PatternFill("solid", fgColor=ROOT_FILL)

@router.post("/plan_import")
def plan_import(
    file: UploadFile = File(...),
    marketplace: str = Form(...),
    year: int = Form(...),
    user=Depends(auth.require_admin),
):
    """Импорт плана продаж (шт) из Excel. UPSERT по (art, mp, year, month).

    Пустая ячейка месяца ⇒ план за этот месяц удаляется (очистка). Год —
    параметром формы.

    Колонки определяются ПО ШАПКЕ (первая строка): артикул — первый
    столбец, 12 месяцев — по совпадению заголовка с названием месяца
    («Январь 2026» и т.п.). Благодаря этому файл грузится независимо от
    числа и порядка справочных столбцов (Уровень 1–3, Текущий статус) —
    совместимы и старые, и новые шаблоны. Справочные столбцы (иерархия,
    статус) НЕ читаются и ничего в системе не перезаписывают.
    """
    import openpyxl
    m = (marketplace or "").strip().lower()
    if m == "cross" or m not in MP_MAP:
        raise HTTPException(status_code=400, detail="План загружается по одному маркетплейсу (ozon или wb)")
    mp_db = MP_MAP[m]

    try:
        data = file.file.read()
        wbk = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
        ws = wbk.active
    except Exception:
        raise HTTPException(status_code=422, detail="Не удалось прочитать Excel-файл")

    def _num(v):
        if v is None or v == "":
            return None
        try:
            return float(str(v).replace(",", ".").strip())
        except (ValueError, TypeError):
            return None

    # Определяем индексы 12 месячных колонок по шапке (первая строка).
    # Заголовок месяца вида «Январь 2026» — сопоставляем по названию месяца.
    rows_iter = list(ws.iter_rows(values_only=True))
    header = rows_iter[0] if rows_iter else ()
    month_name_to_no = {RU_MONTHS[m].lower(): m for m in range(1, 13)}
    month_col = {}  # month_no -> column index
    for ci, h in enumerate(header or ()):
        if ci == 0 or h is None:
            continue
        first_word = str(h).strip().split()[0].lower() if str(h).strip() else ""
        mo = month_name_to_no.get(first_word)
        if mo and mo not in month_col:
            month_col[mo] = ci
    # Фолбэк: если шапка не распознана — берём последние 12 колонок строки.
    use_header = len(month_col) == 12

    upserts = []   # (art, mp, year, month, qty, qty) — второй qty → prev_qty
    seen_arts = set()
    for i, row in enumerate(rows_iter):
        if i == 0:
            continue
        if not row or not row[0]:
            continue
        art = str(row[0]).strip()
        if not art:
            continue
        seen_arts.add(art)
        if use_header:
            for mo in range(1, 13):
                ci = month_col[mo]
                q = _num(row[ci]) if ci < len(row) else None
                if q is not None:
                    upserts.append((art, mp_db, year, mo, q, q))
        else:
            # Без шапки: 12 месяцев — последние 12 непустых/всех колонок строки.
            tail = list(row)[-12:]
            tail = [None] * (12 - len(tail)) + tail
            for k, v in enumerate(tail):
                q = _num(v)
                if q is not None:
                    upserts.append((art, mp_db, year, k + 1, q, q))

    # Полная перезапись плана для артикулов из файла за (mp, year):
    # сначала удаляем все их строки (так очищаются пустые ячейки), затем
    # вставляем заполненные. Удаление — пакетно по списку артикулов.
    cleared = 0
    if seen_arts:
        cleared = db.execute(
            "DELETE FROM sales_plan WHERE marketplace=%s AND year=%s AND seller_article = ANY(%s)",
            (mp_db, year, list(seen_arts)),
        )
    if upserts:
        # Перезалив = все ячейки становятся нативными: is_manual=false,
        # prev_qty = значение из файла (фиксируем «было» для будущих ручных правок).
        db.execute_values(
            """INSERT INTO sales_plan (seller_article, marketplace, year, month, plan_qty, is_manual, prev_qty, updated_at)
               VALUES %s
               ON CONFLICT (seller_article, marketplace, year, month) DO UPDATE SET
                 plan_qty = EXCLUDED.plan_qty, is_manual = false,
                 prev_qty = EXCLUDED.prev_qty, updated_at = now()""",
            upserts,
            template="(%s,%s,%s,%s,%s, false, %s, now())",
        )

    return {"ok": True, "marketplace": mp_db, "year": year,
            "articles": len(seen_arts), "upserted": len(upserts), "cleared": cleared}
