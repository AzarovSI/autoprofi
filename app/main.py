# -*- coding: utf-8 -*-
"""ТД АВТОПРОФИ — монолит FastAPI: API + раздача дашборда (статика).

Эндпоинты под /api/*, статика под /static/*, дашборд на «/».
"""
import hashlib
import os
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from .routers import (
    auth_router, metrics_router, trend_router,
    rnp_router, catalog_router, upload_router, users_router,
    abc_router, sales_router, rnp_sales_router, rnp_comments_router, rnp_promos_router,
    warehouses_router, ozon_warehouses_router,
    prices_router, roles_router, ads_router,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

app = FastAPI(title="ТД АВТОПРОФИ — аналитика маркетплейсов")

@app.on_event("startup")
def migrate_daily_pi():
    from .daily_pi import migrate
    migrate()


@app.on_event("startup")
def migrate_access_roles():
    from .permissions import migrate
    migrate()


@app.on_event("startup")
def migrate_ads_rights():
    # После migrate_access_roles: права раздела «Реклама» выдаются по таблицам ролей.
    from .permissions import migrate_ads
    migrate_ads()


@app.on_event("startup")
def migrate_wb_ads_stats():
    # После migrate_ads_rights: таблицы статистики рекламы WB и права загрузки «Маркетингу».
    from .routers.ads_router import migrate
    migrate()


@app.on_event("startup")
def migrate_price_model():
    from .price_model import migrate
    migrate()


@app.on_event("startup")
def migrate_abc_rules():
    from .abc_rules import migrate
    migrate()


@app.on_event("startup")
def migrate_fact_schema():
    from .fact_schema import migrate
    migrate()


@app.on_event("startup")
def migrate_rnp_comments():
    from .routers.rnp_comments_router import migrate
    migrate()


@app.on_event("startup")
def migrate_rnp_promos():
    # После migrate_access_roles: миграция выдаёт права акций по таблицам ролей.
    from .routers.rnp_promos_router import migrate
    migrate()


@app.on_event("startup")
def start_rnp_warmup():
    # Фоновый прогрев дерева «РНП заказы» (только при RNP_WARMUP=1, см. app/rnp_warmup.py)
    from .rnp_warmup import start
    start()

# CORS: статика может раздаваться с другого origin (S3 на *.pplx.app).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gzip-сжатие ответов. Тяжёлые JSON (дерево «РНП заказы Ozon» ~13 МБ,
# текст с множеством повторов) сжимаются в 10–20 раз → быстрее передача
# по сети. minimum_size: не жмём мелкие ответы (накладные расходы > выгоды).
app.add_middleware(GZipMiddleware, minimum_size=1024)

# API-роутеры
app.include_router(auth_router.router)
app.include_router(metrics_router.router)
app.include_router(trend_router.router)
app.include_router(rnp_router.router)
app.include_router(catalog_router.router)
app.include_router(upload_router.router)
app.include_router(users_router.router)
app.include_router(abc_router.router)
app.include_router(sales_router.router)
app.include_router(rnp_sales_router.router)
app.include_router(rnp_comments_router.router)
app.include_router(rnp_promos_router.router)
app.include_router(warehouses_router.router)
app.include_router(ozon_warehouses_router.router)
app.include_router(prices_router.router)
app.include_router(roles_router.router)
app.include_router(ads_router.router)

# Статика
class StampedStaticFiles(StaticFiles):
    """Файл со штампом ?v=<отпечаток> (render_index) не меняется — браузер держит его
    в кэше год и не перепроверяет на каждом входе (было: ~10 запросов-проверок)."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200 and b"v=" in scope.get("query_string", b""):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


if os.path.isdir(STATIC_DIR):
    app.mount("/static", StampedStaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
def health():
    return {"status": "ok", "build": "20260921-daily-pi"}


_STATIC_REF = re.compile(r'(href=|src=)"/static/([^"?]+)(?:\?v=[^"]*)?"')


def render_index(templates_dir=TEMPLATES_DIR, static_dir=STATIC_DIR):
    """index.html, где у каждого /static/-файла штамп ?v=<отпечаток содержимого>.

    Штамп считается по содержимому файла, поэтому браузер перекачивает только
    изменившиеся js/css — вручную ничего проставлять не нужно.
    """
    html = Path(templates_dir, "index.html").read_text(encoding="utf-8")

    def stamp(m):
        try:
            digest = hashlib.sha256(Path(static_dir, m.group(2)).read_bytes()).hexdigest()[:12]
        except OSError:
            return m.group(0)
        return f'{m.group(1)}"/static/{m.group(2)}?v={digest}"'

    return _STATIC_REF.sub(stamp, html)


# Статика меняется только при выкладке (= перезапуск процесса) — считаем один раз.
_index_html = None


@app.get("/")
def index():
    global _index_html
    if not os.path.isfile(os.path.join(TEMPLATES_DIR, "index.html")):
        return JSONResponse({"detail": "index.html не найден"}, status_code=404)
    if _index_html is None:
        _index_html = render_index()
    # index.html НЕ кешируем: внутри него штампы «?v=...» для js/css.
    # Иначе браузер держит старый index со старыми штампами и грузит устаревший код.
    return HTMLResponse(_index_html, headers={
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    })
