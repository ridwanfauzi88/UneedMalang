import hashlib
import re
import sqlite3
from pathlib import Path

import pytest

import app_user
import app_dev


def _csrf(client, path):
    response = client.get(path)
    assert response.status_code == 200
    match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
    assert match, f"CSRF token missing on {path}"
    return match.group(1).decode()


@pytest.fixture()
def user_client():
    app_user.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    # Do not keep a request context open while exercising the separate developer app.
    # Flask context locals are process-global within the thread; overlapping two
    # context-preserving clients from different Flask applications causes teardown errors.
    return app_user.app.test_client()


@pytest.fixture()
def dev_client():
    app_dev.app.config.update(TESTING=True)
    return app_dev.app.test_client()


def test_price_parser_indonesian_formats():
    assert app_user.parse_price("25-50K") == (25000.0, 50000.0)
    assert app_user.parse_price("Rp 800.000") == (800000.0, 800000.0)
    assert app_user.parse_price("1-25K") == (1000.0, 25000.0)
    assert app_user.parse_price("-") is None
    assert app_user.parse_price("tidak tersedia") is None


def test_rating_and_coordinates_are_validated():
    assert app_user.parse_rating("4,8") == pytest.approx(4.8)
    assert app_user.parse_rating("5,5") is None
    assert app_user.parse_coordinates("-7.95, 112.61") == pytest.approx((-7.95, 112.61))
    assert app_user.parse_coordinates("-120, 112.61") is None
    assert app_user.parse_distance_km("invalid") is None
    # A distance must not silently fall back to a fixed city/campus reference.
    assert app_user.parse_distance_km("-7.95, 112.61") is None
    assert app_user.parse_distance_km("-7.95, 112.61", origin=(-7.95, 112.61)) == pytest.approx(0)


def test_dss_dataset_scores_are_finite_and_preserve_rows():
    for category in app_user.CATEGORIES:
        rows, headers = app_user.build_category_records(category)
        assert headers
        assert rows
        for row in rows:
            score = row["_dss_score"]
            assert score is None or 0 <= score <= 1
            assert row["_rating"] is None or 0 <= row["_rating"] <= 5
            assert row["_dist"] is None or row["_dist"] >= 0


def test_user_protected_page_redirects_to_login(user_client):
    response = user_client.get("/dashboard")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_user_post_without_csrf_is_rejected(user_client):
    response = user_client.post("/login", data={"email": "x@example.com", "password": "password123"})
    assert response.status_code == 400


def test_registration_uses_otp_and_password_hash(user_client, monkeypatch):
    sent = {}

    def fake_send(subject, recipient, body):
        sent.update(subject=subject, recipient=recipient, body=body)

    monkeypatch.setattr(app_user, "_send_email", fake_send)
    token = _csrf(user_client, "/register")
    response = user_client.post("/register", data={
        "csrf_token": token,
        "action": "send_otp",
        "nama": "Penguji",
        "email": "tester@example.com",
        "password": "Test-password-123",
    }, follow_redirects=False)
    assert response.status_code == 200
    assert sent["recipient"] == "tester@example.com"
    otp = re.search(r"\b(\d{6})\b", sent["body"]).group(1)

    response = user_client.post("/register", data={
        "csrf_token": token,
        "action": "submit_register",
        "nama": "Penguji",
        "email": "tester@example.com",
        "password": "Test-password-123",
        "otp": otp,
    })
    assert response.status_code == 302
    with sqlite3.connect(app_user.DB_PATH) as con:
        user = con.execute("SELECT email, password_hash FROM users WHERE email = ?", ("tester@example.com",)).fetchone()
    assert user is not None
    assert user[1] != "Test-password-123"
    assert user[1].startswith("scrypt:") or user[1].startswith("pbkdf2:")


def test_login_requires_a_registered_password(user_client, monkeypatch):
    with sqlite3.connect(app_user.DB_PATH) as con:
        con.execute("DELETE FROM users WHERE email = ?", ("login-check@example.com",))
    token = _csrf(user_client, "/login")
    response = user_client.post("/login", data={
        "csrf_token": token,
        "email": "login-check@example.com",
        "password": "anything123",
    })
    assert response.status_code == 200
    assert b"Email atau kata sandi tidak sesuai" in response.data


def test_developer_dashboard_requires_auth(dev_client):
    response = dev_client.get("/dev/dashboard")
    assert response.status_code == 302
    assert "/dev/login" in response.headers["Location"]


def test_developer_config_requires_auth_and_validates_weights(dev_client):
    response = dev_client.get("/dev/dss-config")
    assert response.status_code == 302

    token = _csrf(dev_client, "/dev/login")
    response = dev_client.post("/dev/login", data={"csrf_token": token, "dev_key": "test-only-developer-key"})
    assert response.status_code == 302
    csrf_dashboard = _csrf(dev_client, "/dev/dss-config")
    before = app_dev.load_settings()
    response = dev_client.post("/dev/dss-config", data={
        "csrf_token": csrf_dashboard,
        "m": "5", "c": "4.2", "w_price": "35", "w_dist": "25", "w_rating": "30",
    })
    assert response.status_code == 200
    assert "Total bobot" in response.get_data(as_text=True)
    assert app_dev.load_settings() == before


def _login_test_session(client, email="ui-check@example.com"):
    with client.session_transaction() as session:
        session["user"] = email
        session["user_name"] = "UI Check"
        session["theme"] = "light"
        session["font_size"] = "medium"


def test_dashboard_uses_one_mixed_feed_for_general_users(user_client):
    _login_test_session(user_client)
    response = user_client.get("/dashboard")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Rekomendasi layanan" in text
    assert "Jarak garis lurus dari Universitas Brawijaya" not in text
    # Category navigation is now present only in the shared hamburger, not as dashboard chips.
    assert "Jelajahi kategori lengkap" not in text


def test_category_query_is_applied_on_backend(user_client):
    _login_test_session(user_client)
    rows, _ = app_user.build_category_records("warung")
    searchable_name = next(row["_name"] for row in rows if row.get("_name") and row["_name"] != "Tanpa Nama")
    response = user_client.get("/category/warung", query_string={"q": searchable_name})
    assert response.status_code == 200
    assert searchable_name in response.get_data(as_text=True)
    assert 'name="q"' in response.get_data(as_text=True)


def test_settings_persist_theme_and_font_size(user_client):
    email = "settings-check@example.com"
    with app_user._connect_db() as con:
        con.execute("INSERT OR REPLACE INTO users (email,name,password_hash,preferences_json,created_at) VALUES (?,?,?,?,?)",
                    (email, "Settings Check", "not-used-in-this-test", "{}", 1.0))
        row = con.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    with user_client.session_transaction() as session:
        session["user"] = email
        session["user_id"] = row["id"]
    token = _csrf(user_client, "/settings")
    response = user_client.post("/settings", data={
        "csrf_token": token, "theme": "dark", "font_size": "large"
    })
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings")
    with app_user._connect_db() as con:
        stored = con.execute("SELECT preferences_json FROM users WHERE id = ?", (row["id"],)).fetchone()
    preferences = __import__("json").loads(stored["preferences_json"])
    assert preferences["theme"] == "dark"
    assert preferences["font_size"] == "large"


def test_maintenance_toggle_blocks_user_site_and_can_be_disabled(user_client, dev_client):
    token = _csrf(dev_client, "/dev/login")
    response = dev_client.post("/dev/login", data={"csrf_token": token, "dev_key": "test-only-developer-key"})
    assert response.status_code == 302
    dashboard_token = _csrf(dev_client, "/dev/dashboard")
    response = dev_client.post("/dev/maintenance", data={
        "csrf_token": dashboard_token,
        "action": "enable",
        "maintenance_message": "Pemeliharaan untuk pengujian.",
    })
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dev/dashboard")

    blocked = user_client.get("/login")
    assert blocked.status_code == 302
    assert blocked.headers["Location"].endswith("/maintenance")
    maintenance = user_client.get("/maintenance")
    assert maintenance.status_code == 503
    assert "Pemeliharaan untuk pengujian." in maintenance.get_data(as_text=True)

    dashboard_token = _csrf(dev_client, "/dev/dashboard")
    response = dev_client.post("/dev/maintenance", data={
        "csrf_token": dashboard_token,
        "action": "disable",
        "maintenance_message": "Pemeliharaan untuk pengujian.",
    })
    assert response.status_code == 302
    assert user_client.get("/login").status_code == 200


def test_public_submission_requires_login_and_approval_before_catalog(user_client, dev_client):
    email = "submitter@example.test"
    with app_user._connect_db() as con:
        con.execute("DELETE FROM user_submissions WHERE submitter_email = ?", (email,))
        con.execute("INSERT OR REPLACE INTO users (email,name,password_hash,preferences_json,created_at) VALUES (?,?,?,?,?)",
                    (email, "Pengaju Uji", "not-used-in-this-test", "{}", 10.0))
        row = con.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()

    with user_client.session_transaction() as session:
        session["user"] = email
        session["user_id"] = row["id"]
        session["user_name"] = "Pengaju Uji"

    token = _csrf(user_client, "/submit-listing")
    response = user_client.post("/submit-listing", data={
        "csrf_token": token,
        "category_key": "warung",
        "listing_name": "Warung Uji Komunitas",
        "address": "Jl. Contoh No. 10, Malang",
        "price": "10-20K",
        "maps_url": "https://maps.google.com/?q=malang",
        "contact": "0812345678",
        "opening_hours": "08.00-21.00",
        "details": "Menu makanan rumahan",
    })
    assert response.status_code == 302

    with app_user._connect_db() as con:
        submission = con.execute("SELECT id, status FROM user_submissions WHERE submitter_email = ? AND listing_name = ?",
                                  (email, "Warung Uji Komunitas")).fetchone()
    assert submission is not None
    assert submission["status"] == "pending"
    rows, _ = app_user.build_category_records("warung")
    assert all(item["_name"] != "Warung Uji Komunitas" for item in rows)

    token = _csrf(dev_client, "/dev/login")
    response = dev_client.post("/dev/login", data={"csrf_token": token, "dev_key": "test-only-developer-key"})
    assert response.status_code == 302
    review_token = _csrf(dev_client, "/dev/submissions")
    response = dev_client.post(f"/dev/submissions/{submission['id']}/review", data={
        "csrf_token": review_token,
        "action": "approve",
        "reviewer_note": "Data diverifikasi saat pengujian.",
    })
    assert response.status_code == 302
    rows, _ = app_user.build_category_records("warung")
    approved = next(item for item in rows if item["_name"] == "Warung Uji Komunitas")
    assert approved["_address"] == "Jl. Contoh No. 10, Malang"


def test_page_access_logging_does_not_store_query_or_ip(user_client):
    response = user_client.get("/login?campaign=private-test")
    assert response.status_code == 200
    with app_user._connect_db() as con:
        row = con.execute("SELECT page, visitor_key FROM access_logs ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert row["page"] == "/login"
    assert "campaign" not in row["page"]
    assert row["visitor_key"]
