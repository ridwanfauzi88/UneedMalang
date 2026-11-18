"""SQLite schema additions shared by the public site and management console."""
from __future__ import annotations
import sqlite3


def ensure_community_schema(con: sqlite3.Connection) -> None:
    user_columns = {row[1] for row in con.execute("PRAGMA table_info(users)").fetchall()}
    if "membership_tier" not in user_columns:
        con.execute("ALTER TABLE users ADD COLUMN membership_tier TEXT NOT NULL DEFAULT 'basic'")
    if "membership_status" not in user_columns:
        con.execute("ALTER TABLE users ADD COLUMN membership_status TEXT NOT NULL DEFAULT 'active'")
    if "membership_expires_at" not in user_columns:
        con.execute("ALTER TABLE users ADD COLUMN membership_expires_at REAL")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS community_recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            category_key TEXT NOT NULL CHECK(category_key IN ('kos','warung','cafe','klinik','atk')),
            place_name TEXT NOT NULL,
            address TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL,
            experience TEXT NOT NULL DEFAULT '',
            latitude REAL,
            longitude REAL,
            maps_url TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
            submitted_at REAL NOT NULL,
            reviewed_at REAL,
            reviewed_by TEXT,
            reviewer_note TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_community_status_date ON community_recommendations(status, submitted_at DESC);
        CREATE INDEX IF NOT EXISTS idx_community_category ON community_recommendations(category_key, status);

        CREATE TABLE IF NOT EXISTS recommendation_helpful (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(recommendation_id, user_id),
            FOREIGN KEY(recommendation_id) REFERENCES community_recommendations(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS recommendation_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id INTEGER NOT NULL,
            reporter_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','reviewed','dismissed')),
            reviewed_at REAL,
            reviewed_by TEXT,
            UNIQUE(recommendation_id, reporter_id),
            FOREIGN KEY(recommendation_id) REFERENCES community_recommendations(id) ON DELETE CASCADE,
            FOREIGN KEY(reporter_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_recommendation_reports_status ON recommendation_reports(status, created_at DESC);

        CREATE TABLE IF NOT EXISTS contribution_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient_user_id INTEGER NOT NULL,
            actor_user_id INTEGER,
            recommendation_id INTEGER,
            event_type TEXT NOT NULL,
            points INTEGER NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            FOREIGN KEY(recipient_user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY(recommendation_id) REFERENCES community_recommendations(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_contribution_recipient_date ON contribution_events(recipient_user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_contribution_period ON contribution_events(created_at DESC, points);

        CREATE TABLE IF NOT EXISTS saved_places (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category_key TEXT NOT NULL CHECK(category_key IN ('kos','warung','cafe','klinik','atk')),
            record_id INTEGER NOT NULL,
            place_name TEXT NOT NULL,
            address TEXT NOT NULL DEFAULT '',
            saved_at REAL NOT NULL,
            UNIQUE(user_id, category_key, record_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_saved_places_user ON saved_places(user_id, saved_at DESC);

        CREATE TABLE IF NOT EXISTS saved_community_recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            recommendation_id INTEGER NOT NULL,
            saved_at REAL NOT NULL,
            UNIQUE(user_id, recommendation_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(recommendation_id) REFERENCES community_recommendations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_saved_community_user ON saved_community_recommendations(user_id, saved_at DESC);

        CREATE TABLE IF NOT EXISTS membership_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            actor TEXT NOT NULL,
            previous_tier TEXT NOT NULL,
            current_tier TEXT NOT NULL,
            previous_status TEXT NOT NULL,
            current_status TEXT NOT NULL,
            previous_expires_at REAL,
            current_expires_at REAL,
            note TEXT NOT NULL DEFAULT '',
            changed_at REAL NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_membership_history_user ON membership_history(user_id, changed_at DESC);

        CREATE TABLE IF NOT EXISTS reward_awards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            recipient_name TEXT NOT NULL,
            period_key TEXT NOT NULL,
            reward_type TEXT NOT NULL,
            reward_label TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','awarded','cancelled')),
            note TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL,
            created_at REAL NOT NULL,
            awarded_by TEXT,
            awarded_at REAL,
            UNIQUE(user_id, period_key, reward_type),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_reward_awards_period ON reward_awards(period_key, status, created_at DESC);

        CREATE TABLE IF NOT EXISTS reward_award_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reward_award_id INTEGER NOT NULL,
            previous_status TEXT,
            current_status TEXT NOT NULL,
            actor TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            changed_at REAL NOT NULL,
            FOREIGN KEY(reward_award_id) REFERENCES reward_awards(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_reward_award_history_id ON reward_award_history(reward_award_id, changed_at DESC);
        """
    )
