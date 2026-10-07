"""Штампы ?v= для js/css в index.html проставляются сервером по содержимому файлов."""
import os
import re
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("DB_DSN", "postgresql://wb_test:local-test-only@127.0.0.1:5432/wb_price_test")
os.environ.setdefault("SECRET_KEY", "local-test-secret-key-0123456789abcdef")

from app import main  # noqa: E402

REF = re.compile(r'(?:href|src)="(/static/[^"]+)"')


class IndexStampTests(unittest.TestCase):
    def test_every_static_ref_in_real_template_is_stamped(self):
        refs = REF.findall(main.render_index())
        self.assertGreaterEqual(len(refs), 10)
        for ref in refs:
            self.assertRegex(ref, r"\?v=[0-9a-f]{12}$", ref)

    def test_stamp_follows_content_and_replaces_manual_stamp(self):
        with tempfile.TemporaryDirectory() as d:
            tpl, st = Path(d, "t"), Path(d, "s")
            (st / "js").mkdir(parents=True)
            tpl.mkdir()
            (tpl / "index.html").write_text(
                '<script src="/static/js/a.js?v=202601010000"></script>'
                '<script src="/static/js/b.js"></script>'
                '<script src="/static/js/missing.js"></script>', encoding="utf-8")
            (st / "js" / "a.js").write_text("one")
            (st / "js" / "b.js").write_text("two")
            first = main.render_index(tpl, st)
            self.assertNotIn("202601010000", first)
            self.assertIn('/static/js/missing.js"', first)  # нет файла — ссылку не трогаем
            (st / "js" / "a.js").write_text("changed")
            second = main.render_index(tpl, st)
            a1, b1 = REF.findall(first)[:2]
            a2, b2 = REF.findall(second)[:2]
            self.assertNotEqual(a1, a2)  # изменился файл — изменился штамп
            self.assertEqual(b1, b2)     # остальные не трогаются


if __name__ == "__main__":
    unittest.main()
