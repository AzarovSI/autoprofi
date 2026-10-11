# -*- coding: utf-8 -*-
"""Фоновый прогрев РНП: период по умолчанию совпадает с фронтом, bump будит прогрев."""
import datetime
import unittest

from app import cache, rnp_warmup


class DefaultPeriodTest(unittest.TestCase):
    def test_matches_frontend_rule(self):
        # views.js rnpSales: с 1-го числа позапрошлого месяца по конец текущего
        cases = {
            datetime.date(2026, 10, 11): ("2026-08-01", "2026-10-31"),
            datetime.date(2026, 1, 15): ("2025-11-01", "2026-01-31"),
            datetime.date(2026, 2, 1): ("2025-12-01", "2026-02-28"),
            datetime.date(2026, 12, 31): ("2026-10-01", "2026-12-31"),
            datetime.date(2028, 3, 5): ("2028-01-01", "2028-03-31"),
        }
        for today, expected in cases.items():
            with self.subTest(today=today):
                self.assertEqual(rnp_warmup.default_period(today), expected)


class BumpWakesWarmupTest(unittest.TestCase):
    def test_only_rnp_namespaces_wake(self):
        rnp_warmup._wake.clear()
        rnp_warmup._on_bump("other_ns")
        self.assertFalse(rnp_warmup._wake.is_set())
        rnp_warmup._on_bump(rnp_warmup.rs.CACHE_NS_WB)
        self.assertTrue(rnp_warmup._wake.is_set())
        rnp_warmup._wake.clear()

    def test_listener_errors_do_not_break_bump(self):
        def broken(_ns):
            raise RuntimeError("подписчик упал")
        cache.on_bump(broken)
        try:
            before = cache.version("test_ns_warmup")
            self.assertEqual(cache.bump("test_ns_warmup"), before + 1)
        finally:
            cache._bump_listeners.remove(broken)


if __name__ == "__main__":
    unittest.main()
