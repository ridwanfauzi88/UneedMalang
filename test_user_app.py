import re
import unittest

from helpers import csrf_of, load_app, tmpdir

M = load_app("app_user", "website_user/app_user.py",
             {"SECRET_KEY": "k" * 40, "UNEED_DEV_SHOW_OTP": "1", "UNEED_ENV": "development"})


class UserAppTests(unittest.TestCase):
    def setUp(self):
        d = tmpdir()
        M.USERS_FILE, M.REVIEWS_FILE = d / "users.json", d / "reviews.json"
        M.PENDING.clear()
        M._attempts.clear()
        self.c = M.app.test_client()

    def token(self, path="/login"):
        return csrf_of(self.c.get(path).get_data(as_text=True))

    def register(self, email="a@b.co", pw="rahasia123"):
        t = self.token("/register")
        html = self.c.post("/register", data={"csrf_token": t, "action": "send_otp", "nama": "Ani", "email": email, "password": pw}).get_data(as_text=True)
        otp = re.search(r"Kode OTP: (\d{6})", html)
        self.assertIsNotNone(otp, html[:300])
        r = self.c.post("/register", data={"csrf_token": t, "action": "submit_register", "nama": "Ani", "email": email, "password": pw, "otp": otp.group(1)})
        self.assertEqual(r.status_code, 302)

    def login(self, email="a@b.co", pw="rahasia123"):
        return self.c.post("/login", data={"csrf_token": self.token(), "email": email, "password": pw})

    def test_protected_routes_redirect(self):
        for p in ("/dashboard", "/category/kos", "/profile", "/settings", "/search?q=ab", "/detail/kos/1", "/booting"):
            r = self.c.get(p)
            self.assertEqual((r.status_code, r.headers["Location"]), (302, "/login"), p)

    def test_login_requires_real_account(self):
        r = self.login("x@y.co", "apasaja123")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.c.get("/dashboard").status_code, 302)

    def test_csrf_enforced(self):
        self.assertEqual(self.c.post("/login", data={"email": "a@b.co", "password": "x"}).status_code, 400)

    def test_register_login_flow_and_pages(self):
        self.register()
        r = self.login()
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/booting"))
        paths = ["/booting", "/dashboard", "/category", "/category/kos", "/category/warung?view=table&sort=rekomendasi",
                 "/category/klinik?sort=jarak", "/category/cafe?sort=harga_asc&min_harga=10000&max_harga=50000",
                 "/category/atk?sort=rating", "/category/makan", "/category/kesehatan", "/search?q=fotocopy",
                 "/search?q=a", "/search", "/detail/warung/1", "/detail/kos/1", "/detail", "/profile", "/settings"]
        for p in paths:
            r = self.c.get(p, follow_redirects=True)
            self.assertEqual(r.status_code, 200, p)
        self.assertEqual(self.c.get("/detail/warung/9999").status_code, 404)
        self.assertEqual(self.c.get("/detail/xx/1").status_code, 404)
        self.assertEqual(self.c.get("/upload_excel/kos").status_code, 405)

    def test_dashboard_uses_dataset_not_static_names(self):
        self.register(); self.login()
        html = self.c.get("/dashboard").get_data(as_text=True)
        self.assertNotIn("Griya Suhat Exclusive", html)
        self.assertIn("Top 3", html)

    def test_filter_and_sort_params(self):
        self.register(); self.login()
        html = self.c.get("/category/warung?min_harga=99999999").get_data(as_text=True)
        self.assertIn("Tidak ada data yang cocok", html)
        html = self.c.get("/category/warung?min_harga=abc&max_harga=-5&sort=ngawur&w_price=zzz").get_data(as_text=True)
        self.assertIn("Daftar Tempat Makan", html)                       # input tidak valid tidak membuat error

    def test_xss_escaped_in_search_query(self):
        self.register(); self.login()
        html = self.c.get('/search?q=<script>alert(1)</script>').get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", html)

    def test_review_flow(self):
        self.register(); self.login()
        t = csrf_of(self.c.get("/detail/warung/1").get_data(as_text=True))
        bad = self.c.post("/detail/warung/1/review", data={"csrf_token": t, "rating": "9", "comment": "bagus"}, follow_redirects=True)
        self.assertIn("Pilih rating", bad.get_data(as_text=True))
        ok = self.c.post("/detail/warung/1/review", data={"csrf_token": t, "rating": "5", "comment": "<b>enak</b>"}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("Ulasan Anda tersimpan", ok)
        self.assertIn("&lt;b&gt;enak&lt;/b&gt;", ok)

    def test_change_password_and_delete_account(self):
        self.register(); self.login()
        t = csrf_of(self.c.get("/profile").get_data(as_text=True))
        r = self.c.post("/profile/password", data={"csrf_token": t, "current": "salah", "password": "baru12345", "password2": "baru12345"}, follow_redirects=True)
        self.assertIn("Password saat ini salah", r.get_data(as_text=True))
        self.c.post("/profile/password", data={"csrf_token": t, "current": "rahasia123", "password": "baru12345", "password2": "baru12345"})
        self.c.post("/logout", data={"csrf_token": t})
        self.assertEqual(self.login(pw="rahasia123").status_code, 401)
        self.assertEqual(self.login(pw="baru12345").status_code, 302)
        t = csrf_of(self.c.get("/profile").get_data(as_text=True))
        r = self.c.post("/delete-account", data={"csrf_token": t, "password": "salah"}, follow_redirects=True)
        self.assertIn("Akun tidak dihapus", r.get_data(as_text=True))
        self.c.post("/delete-account", data={"csrf_token": t, "password": "baru12345"})
        self.assertIsNone(M.get_user("a@b.co"))

    def test_otp_wrong_code_and_lockout(self):
        t = self.token("/register")
        self.c.post("/register", data={"csrf_token": t, "action": "send_otp", "nama": "Ani", "email": "z@b.co", "password": "rahasia123"})
        for _ in range(M.OTP_MAX_ATTEMPTS + 1):
            r = self.c.post("/register", data={"csrf_token": t, "action": "submit_register", "nama": "Ani", "email": "z@b.co", "password": "rahasia123", "otp": "000000"})
        self.assertIsNone(M.get_user("z@b.co"))
        self.assertIn("Terlalu banyak kode salah", r.get_data(as_text=True))

    def test_register_validation(self):
        t = self.token("/register")
        for email, pw, msg in [("bukan-email", "rahasia123", "Format email"), ("a@b.co", "pendek", "minimal 8"), ("a@b.co", "hurufsajaaa", "huruf dan angka")]:
            html = self.c.post("/register", data={"csrf_token": t, "action": "send_otp", "nama": "Ani", "email": email, "password": pw}).get_data(as_text=True)
            self.assertIn(msg, html)

    def test_forgot_and_reset_password(self):
        self.register()
        t = self.token("/forgot-password")
        r = self.c.post("/forgot-password", data={"csrf_token": t, "email": "a@b.co"}, follow_redirects=True)
        link = re.search(r"/reset-password/([\w\-\.]+)", r.get_data(as_text=True))
        self.assertIsNotNone(link)
        tok = link.group(1)
        self.assertEqual(self.c.get(f"/reset-password/{tok}").status_code, 200)
        t = csrf_of(self.c.get(f"/reset-password/{tok}").get_data(as_text=True))
        self.c.post(f"/reset-password/{tok}", data={"csrf_token": t, "password": "baru12345", "password2": "baru12345"})
        self.assertEqual(self.login(pw="baru12345").status_code, 302)
        again = self.c.get(f"/reset-password/{tok}", follow_redirects=True)            # token sekali pakai
        self.assertIn("tidak berlaku", again.get_data(as_text=True))
        self.assertEqual(self.c.get("/reset-password/ngawur", follow_redirects=True).status_code, 200)

    def test_forgot_does_not_reveal_account(self):
        t = self.token("/forgot-password")
        r = self.c.post("/forgot-password", data={"csrf_token": t, "email": "tidakada@b.co"}, follow_redirects=True)
        self.assertNotIn("tidak terdaftar", r.get_data(as_text=True))

    def test_login_rate_limit(self):
        self.register()
        for _ in range(5):
            self.login(pw="salahsalah1")
        self.assertEqual(self.login().status_code, 429)

    def test_otp_hidden_when_not_dev(self):
        M.app.config["DEV_SHOW_OTP"] = False
        try:
            t = self.token("/register")
            html = self.c.post("/register", data={"csrf_token": t, "action": "send_otp", "nama": "Ani", "email": "q@b.co", "password": "rahasia123"}).get_data(as_text=True)
            self.assertNotRegex(html, r"Kode OTP: \d{6}")
            self.assertIn("belum dikonfigurasi", html)
        finally:
            M.app.config["DEV_SHOW_OTP"] = True

    def test_pages_have_no_hardcoded_credentials(self):
        with open(M.__file__, encoding="utf-8") as fh:
            src = fh.read()
        for bad in ("uneed_malang_secret_key_12345", "password_aplikasi_gmail", "email_pengirim@gmail.com"):
            self.assertNotIn(bad, src)


if __name__ == "__main__":
    unittest.main()
