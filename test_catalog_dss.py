import math
import unittest

from helpers import ROOT, tmpdir  # noqa: F401
from shared import catalog, dss


class ParseTests(unittest.TestCase):
    def test_price_formats(self):
        self.assertEqual(catalog.parse_price("25-50K"), (25000.0, 50000.0))
        self.assertEqual(catalog.parse_price("1-25K"), (1000.0, 25000.0))
        self.assertEqual(catalog.parse_price("Rp 1.200.000"), (1200000.0, 1200000.0))
        self.assertEqual(catalog.parse_price("1,5jt"), (1500000.0, 1500000.0))
        self.assertEqual(catalog.parse_price("800.000"), (800000.0, 800000.0))

    def test_price_invalid(self):
        for v in (None, "", "-", "nan", "abc", "0", "-5000", "25-50", float("nan")):
            self.assertIsNone(catalog.parse_price(v), v)

    def test_rating(self):
        self.assertEqual(catalog.parse_rating("4,3"), 4.3)
        self.assertEqual(catalog.parse_rating("4.9"), 4.9)
        for v in ("0", "6", "-1", "abc", "", None, "nan", "inf"):
            self.assertIsNone(catalog.parse_rating(v), v)

    def test_coords(self):
        self.assertEqual(catalog.parse_coords("-7.95, 112.61"), (-7.95, 112.61))
        for v in ("", None, "abc", "1,2,3", "-7.95", "95, 112", "0, 0", "nan, nan"):
            self.assertIsNone(catalog.parse_coords(v), v)

    def test_phone_and_url(self):
        self.assertEqual(catalog.clean_phone("81944245050"), "081944245050")
        self.assertEqual(catalog.clean_phone("81944245050.0"), "081944245050")
        self.assertIsNone(catalog.clean_phone("-7.959448339557285, 112.61549091349228"))
        self.assertIsNone(catalog.safe_url("javascript:alert(1)//http://x"))
        self.assertEqual(catalog.safe_url("https://maps.app.goo.gl/x"), "https://maps.app.goo.gl/x")


class LoaderTests(unittest.TestCase):
    def test_real_datasets_load(self):
        for key in catalog.CATEGORIES:
            res = catalog.load_category(key)
            self.assertIsNone(res.error, key)
            self.assertGreater(res.total, 0, key)

    def test_cp1252_klinik(self):
        self.assertEqual(catalog.load_category("klinik").meta["encoding"], "cp1252")

    def test_bad_files_do_not_raise(self):
        import os
        d = tmpdir()
        os.environ["UNEED_DATA_DIR"] = str(d)
        try:
            self.assertIn("belum tersedia", catalog.load_category("kos").error)
            (d / "kos.csv").write_bytes(b"")
            self.assertEqual(catalog.load_category("kos").total, 0)
            (d / "kos.csv").write_text("A;B\n1;2\n", encoding="utf-8")
            self.assertIn("Kolom nama", catalog.load_category("kos").error)
            (d / "kos.csv").write_text("No;Nama Kos;Rating Maps;Harga per Bulan (Rp)\n1;A;9;abc\n2;B;4,5;800.000\n", encoding="utf-8")
            res = catalog.load_category("kos")
            self.assertIsNone(res.error)
            self.assertEqual([r["rating"] for r in res.rows], [None, 4.5])
            self.assertEqual(res.rows[1]["pmin"], 800000.0)
            (d / "kos.csv").write_text("No,Nama Kos\n", encoding="utf-8")        # header saja
            self.assertEqual(catalog.load_category("kos").total, 0)
        finally:
            del os.environ["UNEED_DATA_DIR"]


def row(price=None, dist=None, rating=None, reviews=None, id_=1):
    return {"id": id_, "price": price, "dist": dist, "rating": rating, "reviews": reviews, "name": f"r{id_}"}


class DSSTests(unittest.TestCase):
    def setUp(self):
        self.cfg = dss.validate_config(dss.DEFAULT_CONFIG)[0]

    def test_bayesian_math(self):
        self.assertAlmostEqual(dss.bayesian_rating(5.0, 5, 5, 4.0), 4.5)
        self.assertEqual(dss.bayesian_rating(5.0, 0, 5, 4.0), 4.0)      # tanpa ulasan -> C
        self.assertEqual(dss.bayesian_rating(5.0, 0, 0, 4.0), 4.0)      # v+m == 0 tidak membagi nol
        self.assertAlmostEqual(dss.bayesian_rating(5.0, -3, 5, 4.0), 4.0)

    def test_saw_ranking_and_normalization(self):
        rows = [row(10000, 1.0, 4.0, id_=1), row(20000, 1.0, 4.0, id_=2), row(10000, 0.5, 5.0, id_=3)]
        out = dss.score_rows(rows, {"price": 40, "dist": 30, "rating": 30}, self.cfg)
        self.assertEqual([r["id"] for r in out][0], 3)                     # unggul di semua kriteria
        for r in out:
            self.assertTrue(0 <= r["score"] <= 1.0 + 1e-9)
        self.assertAlmostEqual(out[0]["score"], 1.0)                        # unggul di semua kriteria -> skor maksimum
        self.assertGreater(out[0]["score"], out[1]["score"])

    def test_weights_change_ranking(self):
        rows = [row(10000, 5.0, 3.0, id_=1), row(50000, 0.5, 5.0, id_=2)]
        cheap = dss.score_rows(rows, {"price": 100, "dist": 0, "rating": 0}, self.cfg)
        self.assertEqual(cheap[0]["id"], 1)
        good = dss.score_rows(rows, {"price": 0, "dist": 50, "rating": 50}, self.cfg)
        self.assertEqual(good[0]["id"], 2)

    def test_missing_and_invalid_values(self):
        rows = [row(None, None, None, id_=1), row(0, 0.0, 0, id_=2), row(-5, -1, 7, id_=3),
                row(10000, None, None, id_=4), row(10000, 0.0, 4.0, id_=5)]
        out = {r["id"]: r for r in dss.score_rows(rows, None, self.cfg)}
        self.assertIsNone(out[1]["score"])
        self.assertIsNone(out[4]["score"])                                  # hanya 1 kriteria
        self.assertIsNotNone(out[5]["score"])                               # jarak 0 aman (dibatasi minimum)
        for r in out.values():
            if r["score"] is not None:
                self.assertTrue(math.isfinite(r["score"]))

    def test_invalid_weights_fallback(self):
        rows = [row(10000, 1.0, 4.0)]
        for w in ({"price": 0, "dist": 0, "rating": 0}, {"price": "x"}, {"price": -1, "dist": 1, "rating": 1}, None):
            out = dss.score_rows(rows, w, self.cfg)
            self.assertTrue(math.isfinite(out[0]["score"]))

    def test_empty_input(self):
        self.assertEqual(dss.score_rows([], None, self.cfg), [])

    def test_single_candidate(self):
        out = dss.score_rows([row(10000, 1.0, 4.0)], None, self.cfg)
        self.assertAlmostEqual(out[0]["score"], 1.0)

    def test_config_validation(self):
        good = dict(dss.DEFAULT_CONFIG)
        self.assertEqual(dss.validate_config(good)[1], [])
        bad = dict(good, bayesian_m="abc", bayesian_c=9, weights={"price": 50, "dist": 30, "rating": 30})
        errors = dss.validate_config(bad)[1]
        self.assertGreaterEqual(len(errors), 3)
        nan = dict(good, bayesian_m="nan")
        self.assertTrue(dss.validate_config(nan)[1])

    def test_config_save_roundtrip_and_corrupt_file(self):
        p = tmpdir() / "cfg.json"
        old = dss.CONFIG_PATH
        dss.CONFIG_PATH = p
        try:
            self.assertEqual(dss.load_config()["bayesian_m"], 5.0)         # file belum ada -> default
            cfg = dict(dss.DEFAULT_CONFIG, bayesian_m=7)
            dss.save_config(cfg)
            self.assertEqual(dss.load_config()["bayesian_m"], 7.0)
            p.write_text("{rusak", encoding="utf-8")
            dss._cfg_cache.update(mtime=None, cfg=None)
            self.assertEqual(dss.load_config()["bayesian_m"], 5.0)         # rusak -> default
            with self.assertRaises(ValueError):
                dss.save_config(dict(cfg, bayesian_c=0))
        finally:
            dss.CONFIG_PATH = old
            dss._cfg_cache.update(mtime=None, cfg=None)


if __name__ == "__main__":
    unittest.main()
