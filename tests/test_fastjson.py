# -*- coding: utf-8 -*-
"""Быстрая отдача больших JSON: округление, типы как у jsonable_encoder, gzip-ответ, кэш статики."""
import datetime
import decimal
import gzip
import json
import unittest

from fastapi.testclient import TestClient

from app import fastjson
from app.main import app


class FastJsonTest(unittest.TestCase):
    def test_compact_keeps_meaning(self):
        data = {"a": 0.05723094491458683, "b": 3.0, "c": None, "d": [1, 2.5000004, -0.0000123456789, 1234567.891],
                "e": decimal.Decimal("12.50"), "f": decimal.Decimal("7"), "g": datetime.date(2026, 10, 1),
                "h": True, "s": "текст"}
        out = json.loads(fastjson.dumps(data))
        self.assertEqual(out, {"a": 0.057230945, "b": 3, "c": None, "d": [1, 2.5000004, -1.2345679e-05, 1234567.891],
                               "e": 12.5, "f": 7, "g": "2026-10-01", "h": True, "s": "текст"})

    def test_gzipped_response(self):
        gz = fastjson.gzip_bytes(fastjson.dumps({"x": 1}))

        class Req:
            headers = {"accept-encoding": "gzip, br"}
        r = fastjson.gzipped_response(gz, Req())
        self.assertEqual(r.headers["content-encoding"], "gzip")
        self.assertEqual(json.loads(gzip.decompress(r.body)), {"x": 1})
        plain = fastjson.gzipped_response(gz, None)
        self.assertNotIn("content-encoding", plain.headers)
        self.assertEqual(json.loads(plain.body), {"x": 1})


class StaticCacheTest(unittest.TestCase):
    def test_only_stamped_files_are_immutable(self):
        client = TestClient(app)
        stamped = client.get("/static/js/util.js?v=abc")
        self.assertEqual(stamped.status_code, 200)
        self.assertIn("immutable", stamped.headers.get("cache-control", ""))
        plain = client.get("/static/js/util.js")
        self.assertNotIn("immutable", plain.headers.get("cache-control", ""))


if __name__ == "__main__":
    unittest.main()
