# -*- coding: utf-8 -*-
"""Быстрая отдача больших JSON-ответов (деревья «РНП заказы» и юнит-экономики).

Замер 11.10.2026: дерево РНП Ozon — 11,4 МБ JSON; сервер (1 ядро) каждый раз заново
сжимал его GZipMiddleware (~0,7 с ожидания), юнит-экономика (~7 МБ) проходила через
jsonable_encoder FastAPI. Здесь:
  • compact(): дроби округляются до 8 значащих цифр (не до N знаков после запятой: браузер
    сам считает из долей изменения к прошлой неделе, и у маленьких долей вроде CTR 0,04
    грубое округление сдвигало последний знак процента), но не меньше 4 знаков после
    запятой (рубли и копейки не трогаются); целые дроби (3.0) пишутся как 3,
    без пробелов; пустые значения (null) остаются;
  • dumps(): прямой json.dumps — Decimal → число, даты → ISO, как jsonable_encoder;
  • gzipped_response(): отдать заранее сжатые байты (GZipMiddleware не сжимает ответ
    с уже выставленным Content-Encoding).
"""
import datetime
import decimal
import gzip
import json
import math

from fastapi.responses import Response

SIGNIFICANT = 8


def _default(o):
    if isinstance(o, decimal.Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    if isinstance(o, (datetime.date, datetime.datetime)):
        return o.isoformat()
    return str(o)


def compact(o, sig=SIGNIFICANT):
    if isinstance(o, float):
        if o == 0 or not math.isfinite(o):
            return 0 if o == 0 else o
        # не меньше 4 знаков после запятой: суммы в рублях не округляются до десятков
        r = round(o, max(4, sig - 1 - math.floor(math.log10(abs(o)))))
        return int(r) if r.is_integer() and abs(r) < 2 ** 53 else r
    if isinstance(o, dict):
        return {k: compact(v, sig) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [compact(v, sig) for v in o]
    return o


def dumps(obj) -> bytes:
    return json.dumps(compact(obj), ensure_ascii=False, separators=(",", ":"),
                      default=_default).encode("utf-8")


def json_response(obj) -> Response:
    return Response(content=dumps(obj), media_type="application/json")


def gzip_bytes(data: bytes) -> bytes:
    return gzip.compress(data, 6)


def gzipped_response(gz: bytes, request=None) -> Response:
    """Ответ из заранее сжатых байтов; клиенту без gzip — распакованный."""
    accept = request.headers.get("accept-encoding", "") if request is not None else ""
    if "gzip" in accept.lower():
        return Response(content=gz, media_type="application/json",
                        headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(content=gzip.decompress(gz), media_type="application/json")
