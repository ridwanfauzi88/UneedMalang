"""Integration smoke tests for the community and membership routes."""
from __future__ import annotations

import re
import os
import sqlite3
import time
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

from helpers import load_app
from shared import membership


def _csrf(html: str) -> str:
    found = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert found, "form must include a CSRF token"
    return found.group(1)


@pytest.fixture()
def uneed_apps(tmp_path, monkeypatch, request):
    # helpers.load_app updates os.environ directly, so restore these test-only
    # values after this fixture to avoid contaminating tests that run later.
    env_keys = (
        "UNEED_DATABASE_PATH", "UNEED_DSS_CONFIG_PATH", "UNEED_SITE_CONFIG_PATH",
        "UNEED_MEMBERSHIP_CONFIG", "UNEED_SECRET_KEY", "UNEED_DEV_SECRET_KEY",
        "UNEED_DEV_KEY", "UNEED_ENV",
    )
    old_env = {key: os.environ.get(key) for key in env_keys}

    def restore_env():
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    request.addfinalizer(restore_env)
    db_path = tmp_path / "users.sqlite3"
    env = {
        "UNEED_DATABASE_PATH": str(db_path),
        "UNEED_DSS_CONFIG_PATH": str(tmp_path / "dss.json"),
        "UNEED_SITE_CONFIG_PATH": str(tmp_path / "site.json"),
        "UNEED_MEMBERSHIP_CONFIG": str(tmp_path / "membership.json"),
        "UNEED_SECRET_KEY": "community-user-test-secret-key",
        "UNEED_DEV_SECRET_KEY": "community-dev-test-secret-key",
        "UNEED_DEV_KEY": "community-dev-test-key",
        "UNEED_ENV": "testing",
    }
    user_app = load_app("app_user_community_tests", "website_user/app_user.py", env)
    dev_app = load_app("app_dev_community_tests", "website_dev/app_dev.py", env)
    monkeypatch.setattr(membership, "CONFIG_PATH", tmp_path / "membership.json")
    user_app._init_db()
    dev_app.USER_DB_PATH = db_path
    dev_app._ensure_admin_tables()
    with sqlite3.connect(db_path) as con:
        ids = []
        for email, name in (("community-a@example.test", "Nama Pribadi A"), ("community-b@example.test", "Nama Pribadi B")):
            con.execute(
                "INSERT INTO users (email, name, password_hash, preferences_json, created_at) VALUES (?, ?, ?, '{}', ?)",
                (email, name, generate_password_hash("irrelevant-test-password"), time.time()),
            )
            ids.append(con.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()[0])
    user_app.app.config.update(TESTING=True)
    dev_app.app.config.update(TESTING=True)
    return user_app, dev_app, ids, db_path


def _signed_in_client(app, email, user_id, name):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = email
        sess["user_id"] = user_id
        sess["user_name"] = name
    return client


def test_community_recommendation_moderation_points_saving_and_reports(uneed_apps):
    user_app, dev_app, ids, db_path = uneed_apps
    first_id, second_id = ids
    member = _signed_in_client(user_app.app, "community-a@example.test", first_id, "Nama Pribadi A")
    form_html = member.get("/community/new").get_data(as_text=True)
    response = member.post("/community/new", data={
        "csrf_token": _csrf(form_html),
        "category_key": "cafe",
        "place_name": "Cafe Uji Integrasi",
        "address": "Jl. Uji 1, Malang",
        "reason": "Tempat ini layak dipertimbangkan karena lokasi dan informasi aksesnya jelas.",
        "experience": "Informasi dibagikan sebagai contoh pengujian, bukan data dataset katalog.",
        "latitude": "-7.95",
        "longitude": "112.61",
        "maps_url": "https://maps.google.com/?q=-7.95,112.61",
    }, follow_redirects=True)
    assert response.status_code == 200
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        rec = con.execute("SELECT * FROM community_recommendations WHERE place_name = ?", ("Cafe Uji Integrasi",)).fetchone()
    assert rec and rec["status"] == "pending"
    assert "Cafe Uji Integrasi" not in member.get("/community").get_data(as_text=True)

    admin = dev_app.app.test_client()
    login_html = admin.get("/dev/login").get_data(as_text=True)
    assert admin.post("/dev/login", data={"csrf_token": _csrf(login_html), "dev_key": "community-dev-test-key"}).status_code == 302
    moderation_html = admin.get("/dev/community").get_data(as_text=True)
    approve = admin.post(f"/dev/community/{rec['id']}/review", data={
        "csrf_token": _csrf(moderation_html), "action": "approve", "reviewer_note": "Diperiksa pada pengujian."
    }, follow_redirects=True)
    assert approve.status_code == 200
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        published = con.execute("SELECT status, reviewed_by FROM community_recommendations WHERE id = ?", (rec["id"],)).fetchone()
        events = con.execute("SELECT points, event_type FROM contribution_events WHERE recommendation_id = ?", (rec["id"],)).fetchall()
    assert published["status"] == "approved"
    assert published["reviewed_by"]
    assert [(row["points"], row["event_type"]) for row in events] == [(10, "approved_recommendation")]

    feed = member.get("/community")
    feed_html = feed.get_data(as_text=True)
    assert feed.status_code == 200 and "Cafe Uji Integrasi" in feed_html
    save_token = _csrf(feed_html)
    assert member.post(f"/community/{rec['id']}/save", data={"csrf_token": save_token}, follow_redirects=True).status_code == 200
    assert "Cafe Uji Integrasi" in member.get("/saved-places").get_data(as_text=True)

    second = _signed_in_client(user_app.app, "community-b@example.test", second_id, "Nama Pribadi B")
    second_feed = second.get("/community").get_data(as_text=True)
    token = _csrf(second_feed)
    assert second.post(f"/community/{rec['id']}/helpful", data={"csrf_token": token}, follow_redirects=True).status_code == 200
    assert second.post(f"/community/{rec['id']}/report", data={"csrf_token": token, "reason": "incorrect"}, follow_redirects=True).status_code == 200
    with sqlite3.connect(db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM recommendation_helpful WHERE recommendation_id = ?", (rec["id"],)).fetchone()[0] == 1
        report = con.execute("SELECT reason, status FROM recommendation_reports WHERE recommendation_id = ?", (rec["id"],)).fetchone()
        assert report == ("Informasi keliru", "open")


def test_membership_and_admin_pages_render_without_payment_gateway(uneed_apps):
    user_app, dev_app, ids, _ = uneed_apps
    member = _signed_in_client(user_app.app, "community-a@example.test", ids[0], "Nama Pribadi A")
    response = member.get("/membership")
    assert response.status_code == 200
    assert "Harga belum ditetapkan" in response.get_data(as_text=True)
    admin = dev_app.app.test_client()
    login_html = admin.get("/dev/login").get_data(as_text=True)
    admin.post("/dev/login", data={"csrf_token": _csrf(login_html), "dev_key": "community-dev-test-key"})
    assert admin.get("/dev/membership").status_code == 200
    assert admin.get("/dev/membership/users").status_code == 200


def test_rewards_admin_flow_and_membership_history(uneed_apps):
    user_app, dev_app, ids, db_path = uneed_apps
    user_id = ids[0]
    admin = dev_app.app.test_client()
    login_html = admin.get("/dev/login").get_data(as_text=True)
    login = admin.post("/dev/login", data={"csrf_token": _csrf(login_html), "dev_key": "community-dev-test-key"})
    assert login.status_code == 302

    rewards_html = admin.get("/dev/rewards").get_data(as_text=True)
    assert "Catat penghargaan" in rewards_html
    created = admin.post("/dev/rewards", data={
        "csrf_token": _csrf(rewards_html),
        "user_id": str(user_id),
        "period_key": "2026-10",
        "reward_type": "community_contributor",
        "reward_label": "Kontributor Komunitas",
        "note": "Catatan hasil seleksi internal.",
    }, follow_redirects=True)
    assert created.status_code == 200
    assert "Kontributor Komunitas" in created.get_data(as_text=True)

    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        award = con.execute("SELECT id, status FROM reward_awards WHERE user_id = ?", (user_id,)).fetchone()
    assert award and award["status"] == "pending"
    award_id = int(award["id"])

    status_html = admin.get("/dev/rewards").get_data(as_text=True)
    updated = admin.post(f"/dev/rewards/{award_id}/status", data={
        "csrf_token": _csrf(status_html), "status": "awarded"
    }, follow_redirects=True)
    assert updated.status_code == 200
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        award = con.execute("SELECT status, awarded_by, awarded_at FROM reward_awards WHERE id = ?", (award_id,)).fetchone()
        assert award["status"] == "awarded" and award["awarded_by"] and award["awarded_at"]
        audit = con.execute("SELECT previous_status, current_status FROM reward_award_history WHERE reward_award_id = ?", (award_id,)).fetchone()
        assert tuple(audit) == ("pending", "awarded")

    # Point corrections are additive ledger events rather than overwrites.
    reward_page = admin.get("/dev/rewards").get_data(as_text=True)
    adjustment = admin.post("/dev/rewards/points-adjustment", data={
        "csrf_token": _csrf(reward_page), "user_id": str(user_id), "points": "-2",
        "note": "Koreksi berdasarkan audit poin.",
    }, follow_redirects=True)
    assert adjustment.status_code == 200
    with sqlite3.connect(db_path) as con:
        adjustment_row = con.execute("SELECT event_type, points, note FROM contribution_events WHERE recipient_user_id = ? AND event_type = 'admin_adjustment'", (user_id,)).fetchone()
    assert adjustment_row[0] == "admin_adjustment" and adjustment_row[1] == -2
    assert "Koreksi berdasarkan audit poin." in adjustment_row[2]

    # Membership changes are recorded instead of silently overwriting the state.
    users_html = admin.get("/dev/membership/users").get_data(as_text=True)
    changed = admin.post(f"/dev/membership/users/{user_id}", data={
        "csrf_token": _csrf(users_html), "membership_tier": "premium",
        "membership_status": "active", "membership_expires_at": "",
    }, follow_redirects=True)
    assert changed.status_code == 200
    with sqlite3.connect(db_path) as con:
        history = con.execute("SELECT previous_tier, current_tier FROM membership_history WHERE user_id = ?", (user_id,)).fetchone()
    assert history == ("basic", "premium")

    member = _signed_in_client(user_app.app, "community-a@example.test", user_id, "Nama Pribadi A")
    profile = member.get("/membership")
    assert profile.status_code == 200
    profile_html = profile.get_data(as_text=True)
    assert "Kontributor Komunitas" in profile_html
    assert "Riwayat membership" in profile_html
    assert "basic" in profile_html and "premium" in profile_html


def test_location_dss_api_requires_valid_coordinates_and_never_caches(uneed_apps):
    user_app, _, ids, db_path = uneed_apps
    member = _signed_in_client(user_app.app, "community-a@example.test", ids[0], "Nama Pribadi A")
    response = member.post("/api/location-dss", json={"lat": -7.95, "lng": 112.61}, headers={
        "X-CSRFToken": member.get("/dashboard").get_data(as_text=True).split('name="csrf_token" value="')[1].split('"')[0]
    })
    assert response.status_code == 200
    assert "no-store" in response.headers.get("Cache-Control", "")
    body = response.get_json()
    assert body["distance_type"] == "haversine_straight_line"
    assert isinstance(body["items"], list)
    known = next(item for item in body["items"] if item["category"] == "cafe" and item["record_id"] == 0)
    assert known["distance_km"] is not None and known["distance_km"] > 0
    assert known["dss_score"] is not None
    second_response = member.post("/api/location-dss", json={"lat": -7.90, "lng": 112.70}, headers={
        "X-CSRFToken": _csrf(member.get("/dashboard").get_data(as_text=True))
    })
    assert second_response.status_code == 200
    second_known = next(item for item in second_response.get_json()["items"] if item["category"] == "cafe" and item["record_id"] == 0)
    assert second_known["distance_km"] != known["distance_km"]
    bad = member.post("/api/location-dss", json={"lat": 95, "lng": 112.61}, headers={
        "X-CSRFToken": member.get("/dashboard").get_data(as_text=True).split('name="csrf_token" value="')[1].split('"')[0]
    })
    assert bad.status_code == 400
    with sqlite3.connect(db_path) as con:
        cols = {r[1] for r in con.execute("PRAGMA table_info(access_logs)")}
        assert "latitude" not in cols and "longitude" not in cols
