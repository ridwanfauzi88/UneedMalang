"""Regression tests for the location and membership additions (no web framework needed)."""
from __future__ import annotations

import sqlite3
from urllib.parse import parse_qs, urlparse

from shared.community_schema import ensure_community_schema
from shared.location import (
    google_maps_directions_url,
    google_maps_search_url,
    haversine_km,
    parse_coordinates,
    valid_lat_lng,
)
from shared import membership
from shared import dss


def test_coordinate_validation_and_haversine_distance():
    assert parse_coordinates("-7.95, 112.61") == (-7.95, 112.61)
    assert parse_coordinates("not coordinates") is None
    assert parse_coordinates("91, 0") is None
    assert parse_coordinates("nan, 0") is None
    assert valid_lat_lng(-90, 180) == (-90.0, 180.0)
    assert valid_lat_lng(-90.01, 0) is None
    assert 111 < haversine_km(0, 0, 1, 0) < 112
    assert haversine_km("x", 0, 0, 0) is None


def test_google_maps_urls_use_valid_destination_and_encoded_text():
    search = google_maps_search_url("Kos A & B", "Jl. Contoh No. 1")
    directions = google_maps_directions_url("Kos A", "Jl. Contoh No. 1")
    assert search is not None and directions is not None
    assert parse_qs(urlparse(search).query)["query"] == ["Jl. Contoh No. 1"]
    assert parse_qs(urlparse(directions).query)["destination"] == ["Jl. Contoh No. 1"]
    coord_url = google_maps_search_url("", "", (-7.95, 112.61))
    assert parse_qs(urlparse(coord_url).query)["query"] == ["-7.9500000,112.6100000"]
    assert google_maps_search_url("", "") is None


def test_community_schema_migrates_membership_columns_and_creates_tables():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL)")
    ensure_community_schema(con)
    cols = {row[1] for row in con.execute("PRAGMA table_info(users)")}
    assert {"membership_tier", "membership_status", "membership_expires_at"} <= cols
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"community_recommendations", "recommendation_helpful", "recommendation_reports", "contribution_events", "saved_places", "saved_community_recommendations"} <= tables
    con.close()


def test_membership_config_has_no_invented_price_and_can_configure_badges(tmp_path, monkeypatch):
    monkeypatch.setattr(membership, "CONFIG_PATH", tmp_path / "membership.json")
    default = membership.load_membership_config()
    assert default["tiers"]["premium"]["price"] is None
    assert default["tiers"]["premium"]["benefits"] == []
    saved = membership.save_membership_config({
        "tiers": {
            "basic": {"label": "Basic", "price": "", "benefits": "Benefit satu\nBenefit dua"},
            "premium": {"label": "Premium", "price": "", "benefits": ""},
        },
        "contribution": {
            "approved_recommendation_points": "12",
            "helpful_vote_points": "2",
            "badges": "penggerak | Penggerak Komunitas | 25\nawal | Kontributor | 1",
        },
    })
    assert saved["tiers"]["premium"]["price"] is None
    assert saved["tiers"]["premium"]["benefits"] == []
    assert saved["contribution"]["approved_recommendation_points"] == 12
    assert [x["key"] for x in saved["contribution"]["badges"]] == ["awal", "penggerak"]


def test_dss_does_not_invent_missing_review_count():
    cfg = dict(dss.DEFAULT_CONFIG)
    cfg["weights"] = {"price": 40, "dist": 30, "rating": 30}
    row = {"name": "Place", "price": 100, "dist": 1, "rating": 5, "reviews": None}
    scored = dss.score_rows([row], config=cfg)[0]
    assert scored["reviews_used"] is None
    assert scored["reviews_assumed"] is False
    assert scored["bayes_rating"] == 5.0


def test_membership_config_supports_periods(tmp_path, monkeypatch):
    monkeypatch.setattr(membership, "CONFIG_PATH", tmp_path / "membership.json")
    saved = membership.save_membership_config({
        "tiers": {
            "basic": {"label": "Basic", "price": "", "benefits": []},
            "premium": {"label": "Premium", "price": "", "benefits": []},
        },
        "contribution": {
            "approved_recommendation_points": "10",
            "helpful_vote_points": "1",
            "period": "weekly",
            "badges": [],
        },
    })
    assert saved["contribution"]["period"] == "weekly"
    assert membership.load_membership_config()["contribution"]["period"] == "weekly"


def test_new_membership_and_reward_tables_are_safe_and_unique():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL)")
    ensure_community_schema(con)
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"membership_history", "reward_awards"} <= tables
    con.execute("INSERT INTO users (id, name, email) VALUES (1, 'A', 'a@example.test')")
    con.execute("INSERT INTO reward_awards (user_id, recipient_name, period_key, reward_type, reward_label, status, created_by, created_at) VALUES (1, 'A', '2026-10', 'community', 'Kontributor', 'pending', 'admin', 1)")
    try:
        con.execute("INSERT INTO reward_awards (user_id, recipient_name, period_key, reward_type, reward_label, status, created_by, created_at) VALUES (1, 'A', '2026-10', 'community', 'Kontributor', 'pending', 'admin', 2)")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("reward_awards must prevent duplicate user/period/type entries")
    con.close()
