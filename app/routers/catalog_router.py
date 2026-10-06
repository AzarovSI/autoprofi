# -*- coding: utf-8 -*-
"""Справочник товаров: чтение, редактирование, дерево категорий, импорт/экспорт.

Модель данных:
  • catalog_items        — ОБЩИЕ по артикулу поля: наименование + категории K1/K2/K3.
  • catalog_marketplace  — РАЗДЕЛЬНЫЕ по маркетплейсу поля: статус + менеджер
                           (ключ seller_article + marketplace).

Все эндпоинты принимают параметр marketplace ('Ozon' | 'Wildberries').
По умолчанию — 'Ozon' (исторический справочник).
"""
import io

from fastapi import APIRouter, Depends, Body, UploadFile, File, HTTPException, Query
from fastapi.responses import StreamingResponse

from .. import db, auth, cache
from ..util import canon_article

# Кэш дерева РНП-продаж зависит от справочника (категории/статус/менеджер в JOIN).
# При любой правке справочника бампаем его версию (должно совпадать с CACHE_NS роутера).
RNP_TREE_CACHE_NAMESPACES = (
    "rnp_sales_tree", "rnp_sales_tree_wb", "rnp_sales_tree_ya",
)


def _invalidate_rnp_trees():
    # Категории общие для маркетплейсов. Любая правка/импорт справочника
    # может затронуть несколько деревьев, поэтому сбрасываем все три.
    for namespace in RNP_TREE_CACHE_NAMESPACES:
        cache.bump(namespace)

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

# Допустимые маркетплейсы (как в fact_weekly / rnp_router).
_MP_ALLOWED = {"Ozon", "Wildberries"}


def _norm_mp(marketplace: str) -> str:
    """Нормализуем параметр маркетплейса к каноничному виду fact_weekly."""
    m = (marketplace or "").strip().lower()
    if m in ("wb", "wildberries"):
        return "Wildberries"
    if m in ("ya", "yandex"):
        return "Yandex"
    return "Ozon"


@router.get("/items")
def items(
    marketplace: str = Query("Ozon"),
    search: str = Query(None),
    only_uncategorized: bool = Query(False),
    limit: int = Query(2000),
    user=Depends(auth.get_current_user),
):
    """Строки справочника для выбранного маркетплейса.

    Категории — из catalog_items (общие), статус/менеджер — из catalog_marketplace
    (по маркетплейсу). Возвращаем строки тех артикулов, что заведены в
    catalog_marketplace для этого МП (т.е. участвуют в данном маркетплейсе).
    """
    mp = _norm_mp(marketplace)
    where = ["cm.marketplace = %s"]
    params = [mp]
    if search:
        where.append("cm.seller_article ILIKE %s")
        params.append(f"%{search.strip()}%")
    if only_uncategorized:
        # «нераспределённые» = нет полной цепочки категорий ЛИБО нет статуса по этому МП
        where.append(
            "(ci.category_l1 IS NULL OR ci.category_l2 IS NULL OR ci.category_l3 IS NULL OR cm.status IS NULL)"
        )
    params.append(int(limit))
    rows = db.query_all(
        f"""
        SELECT cm.seller_article, ci.sample_name,
               ci.category_l1, ci.category_l2, ci.category_l3,
               cm.status, cm.manager, cm.product_url, cm.updated_at
        FROM catalog_marketplace cm
        LEFT JOIN catalog_items ci ON ci.seller_article = cm.seller_article
        WHERE {' AND '.join(where)}
        ORDER BY cm.seller_article
        LIMIT %s
        """,
        tuple(params),
    )
    for r in rows:
        if r.get("updated_at"):
            r["updated_at"] = r["updated_at"].isoformat()
    return rows


@router.get("/tree")
def tree(marketplace: str = Query("Ozon"), user=Depends(auth.get_current_user)):
    """Дерево допустимых значений категорий (общее) + статусы/менеджеры по МП."""
    mp = _norm_mp(marketplace)

    # 5 независимых запросов — последовательно на тёплом пуле соединений.
    # (Параллелизм через ThreadPoolExecutor оказался медленнее из-за
    #  сериализации на блокировке пула psycopg2 и GIL — см. замеры.)
    l1 = [r["category_l1"] for r in db.query_all(
        "SELECT DISTINCT category_l1 FROM catalog_items WHERE category_l1 IS NOT NULL ORDER BY 1")]
    l2 = db.query_all(
        """SELECT DISTINCT category_l1 AS parent, category_l2 AS name FROM catalog_items
           WHERE category_l2 IS NOT NULL ORDER BY 1, 2""")
    l3 = db.query_all(
        """SELECT DISTINCT category_l2 AS parent, category_l3 AS name FROM catalog_items
           WHERE category_l3 IS NOT NULL ORDER BY 1, 2""")
    # Статусы и менеджеры — ТОЛЬКО по выбранному маркетплейсу.
    statuses = [r["status"] for r in db.query_all(
        "SELECT DISTINCT status FROM catalog_marketplace WHERE marketplace=%s AND status IS NOT NULL ORDER BY 1",
        (mp,))]
    managers = [r["manager"] for r in db.query_all(
        "SELECT DISTINCT manager FROM catalog_marketplace WHERE marketplace=%s AND manager IS NOT NULL ORDER BY 1",
        (mp,))]
    return {"l1": l1, "l2": l2, "l3": l3, "statuses": statuses, "managers": managers}


@router.put("/items")
def update_item(payload: dict = Body(...), user=Depends(auth.get_current_user)):
    """Обновить запись справочника.

    Категории/наименование (общие) → catalog_items.
    Статус/менеджер (по МП) → catalog_marketplace.
    """
    # Канонизируем артикул ДО любых INSERT/UPDATE — единый ключ во всём
    # приложении (catalog_items + catalog_marketplace + fact-таблицы).
    sa = canon_article(payload.get("seller_article"))
    if not sa:
        raise HTTPException(status_code=400, detail="Не указан артикул")
    mp = _norm_mp(payload.get("marketplace"))

    # 1) Общие поля (категории + наименование) → catalog_items
    common_fields, common_params = [], []
    for k in ("sample_name", "category_l1", "category_l2", "category_l3"):
        if k in payload:
            common_fields.append(f"{k} = %s")
            common_params.append(payload[k] if payload[k] != "" else None)
    db.execute(
        """INSERT INTO catalog_items (seller_article) VALUES (%s)
           ON CONFLICT (seller_article) DO NOTHING""",
        (sa,),
    )
    if common_fields:
        common_fields.append("updated_at = now()")
        common_params.append(sa)
        db.execute(
            f"UPDATE catalog_items SET {', '.join(common_fields)} WHERE seller_article = %s",
            tuple(common_params),
        )

    # 2) Поля по маркетплейсу (статус/менеджер) → catalog_marketplace.
    # Строку для этого МП создаём ВСЕГДА (даже без status/manager) —
    # именно по наличию строки в catalog_marketplace товар «заведён»
    # на маркетплейсе и появляется в отчётах РНП / «Продажи, шт»
    # сразу (ещё до загрузки факта). Статус/менеджер обновляем отдельно.
    db.execute(
        """INSERT INTO catalog_marketplace (seller_article, marketplace) VALUES (%s, %s)
           ON CONFLICT (seller_article, marketplace) DO NOTHING""",
        (sa, mp),
    )
    mp_fields, mp_params = [], []
    for k in ("status", "manager", "product_url"):
        if k in payload:
            mp_fields.append(f"{k} = %s")
            mp_params.append(payload[k] if payload[k] != "" else None)
    if mp_fields:
        mp_fields.append("updated_at = now()")
        mp_params.extend([sa, mp])
        db.execute(
            f"""UPDATE catalog_marketplace SET {', '.join(mp_fields)}
                WHERE seller_article = %s AND marketplace = %s""",
            tuple(mp_params),
        )
    _invalidate_rnp_trees()
    return {"ok": True, "seller_article": sa, "marketplace": mp}


@router.get("/export")
def export_items(marketplace: str = Query("Ozon"), user=Depends(auth.get_current_user)):
    """Выгрузка справочника выбранного маркетплейса в Excel."""
    import openpyxl
    mp = _norm_mp(marketplace)
    rows = db.query_all(
        """SELECT cm.seller_article, ci.sample_name,
                  ci.category_l1, ci.category_l2, ci.category_l3,
                  cm.status, cm.manager, cm.product_url
           FROM catalog_marketplace cm
           LEFT JOIN catalog_items ci ON ci.seller_article = cm.seller_article
           WHERE cm.marketplace = %s
           ORDER BY cm.seller_article""",
        (mp,),
    )
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Справочник {mp}"
    ws.append(["Артикул", "Наименование", "Уровень 1", "Уровень 2", "Уровень 3", "Статус", "Менеджер", "Ссылка на товар"])
    for r in rows:
        ws.append([r["seller_article"], r["sample_name"], r["category_l1"],
                   r["category_l2"], r["category_l3"], r["status"], r["manager"],
                   r.get("product_url")])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    # Имя файла по маркетплейсу (ранее для всех кроме Ozon давалось _wb —
    # из-за чего справочник Yandex выгружался как spravochnik_wb).
    fname = {
        "Ozon": "spravochnik_ozon.xlsx",
        "Wildberries": "spravochnik_wb.xlsx",
        "Yandex": "spravochnik_yandex.xlsx",
    }.get(mp, "spravochnik.xlsx")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@router.post("/import")
def import_items(
    file: UploadFile = File(...),
    marketplace: str = Query("Ozon"),
    user=Depends(auth.get_current_user),
):
    """Импорт справочника из Excel для выбранного маркетплейса.

    Колонки: Артикул, Наименование, У1, У2, У3, Статус, [Менеджер].
    Наименование/категории → catalog_items (общие).
    Статус/менеджер → catalog_marketplace (по маркетплейсу).
    """
    import openpyxl
    mp = _norm_mp(marketplace)
    data = file.file.read()
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
    ws = wb.active

    def _clean(v):
        """Пустая строка/пробелы → NULL; остальное → строка без краёвых пробелов."""
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    # Собираем все строки в памяти и делаем ДВА пакетных запроса
    # (вместо 2 запросов на каждую строку) — иначе на сотнях строк
    # открывалось бы сотни TLS-соединений и запрос рвался по таймауту.
    items_rows, mp_rows = [], []
    seen = set()
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue
        if not row or not row[0]:
            continue
        # Канонизируем артикул ПЕРЕД дедупликацией (seen) и записью,
        # чтобы «AGR-140 Smerch» и «AGR-140 SMERCH» считались одним товаром.
        sa = canon_article(row[0])
        if not sa or sa in seen:
            continue
        seen.add(sa)
        vals = list(row) + [None] * (8 - len(row))
        items_rows.append((sa, _clean(vals[1]), _clean(vals[2]), _clean(vals[3]), _clean(vals[4])))
        mp_rows.append((sa, mp, _clean(vals[5]), _clean(vals[6]), _clean(vals[7])))

    # 1) Общие поля (наименование + категории) → catalog_items — одним пакетом
    db.execute_values(
        """INSERT INTO catalog_items
             (seller_article, sample_name, category_l1, category_l2, category_l3, updated_at)
           VALUES %s
           ON CONFLICT (seller_article) DO UPDATE SET
             sample_name=EXCLUDED.sample_name,
             category_l1=EXCLUDED.category_l1,
             category_l2=EXCLUDED.category_l2,
             category_l3=EXCLUDED.category_l3,
             updated_at=now()""",
        items_rows,
        template="(%s,%s,%s,%s,%s, now())",
    )

    # 2) Поля по маркетплейсу (статус/менеджер) → catalog_marketplace — одним пакетом
    db.execute_values(
        """INSERT INTO catalog_marketplace
             (seller_article, marketplace, status, manager, product_url, updated_at)
           VALUES %s
           ON CONFLICT (seller_article, marketplace) DO UPDATE SET
             status=EXCLUDED.status,
             manager=EXCLUDED.manager,
             product_url=EXCLUDED.product_url,
             updated_at=now()""",
        mp_rows,
        template="(%s,%s,%s,%s,%s, now())",
    )

    _invalidate_rnp_trees()
    return {"ok": True, "imported": len(items_rows), "marketplace": mp}
