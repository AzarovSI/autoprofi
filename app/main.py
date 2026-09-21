# -*- coding: utf-8 -*-
"""ТД АВТОПРОФИ — монолит FastAPI: API + раздача дашборда (статика).

Эндпоинты под /api/*, статика под /static/*, дашборд на «/».
"""
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from .routers import (
    auth_router, metrics_router, trend_router,
    rnp_router, catalog_router, upload_router, users_router,
    abc_router, sales_router, rnp_sales_router, rnp_comments_router,
    rnp_anomalies_router, warehouses_router, ozon_warehouses_router,
    prices_router,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

app = FastAPI(title="ТД АВТОПРОФИ — аналитика маркетплейсов")

@app.on_event("startup")
def migrate_daily_pi():
    from .daily_pi import migrate
    migrate()

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
app.include_router(rnp_anomalies_router.router)
app.include_router(warehouses_router.router)
app.include_router(ozon_warehouses_router.router)
app.include_router(prices_router.router)

# Статика
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
def health():
    return {"status": "ok", "build": "20260921-daily-pi"}


@app.get("/")
def index():
    idx = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.isfile(idx):
        # index.html НЕ кешируем: внутри него штамп «?v=...» для js/css.
        # Иначе браузер держит старый index со старым штампом и грузит устаревший код.
        return FileResponse(idx, headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        })
    return JSONResponse({"detail": "index.html не найден"}, status_code=404)
