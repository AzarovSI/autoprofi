#!/usr/bin/env python3
"""Сборка dist/public из templates/index.html + static/.

Преобразует пути шаблона FastAPI (/static/...) в относительные для статической
раздачи (css/..., js/..., vendor/...) и проставляет ?v=<штамп> для сброса кэша.
Штамп — это текущее время в формате YYYYMMDDHHMM (минутная точность достаточна
для деплоя). Копирует только статику; index.html генерируется заново.
"""
import re, shutil, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC_STATIC = ROOT / "static"
TEMPLATE = ROOT / "templates" / "index.html"
DIST = ROOT / "dist" / "public"

STAMP = datetime.datetime.now().strftime("%Y%m%d%H%M")


def build():
    DIST.mkdir(parents=True, exist_ok=True)
    # 1) копируем статику (css/js/vendor) в dist/public
    for sub in ("css", "js", "vendor"):
        src = SRC_STATIC / sub
        if src.is_dir():
            dst = DIST / sub
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)

    # 2) генерируем index.html: /static/<path> -> <path>?v=STAMP
    html = TEMPLATE.read_text(encoding="utf-8")

    def repl(m):
        path = m.group(2)              # напр. css/app.css
        return f'{m.group(1)}"{path}?v={STAMP}"'

    # href="/static/css/app.css"  и  src="/static/js/views.js"  и vendor
    html = re.sub(r'(href=|src=)"/static/((?:css|js|vendor)/[^"]+)"', repl, html)

    (DIST / "index.html").write_text(html, encoding="utf-8")

    refs = re.findall(r'(?:js|css)/[a-zA-Z0-9_./]+\?v=\d+', html)
    print("stamp", STAMP)
    views = [r for r in refs if "views.js" in r]
    print("views ref:", views)


if __name__ == "__main__":
    build()
