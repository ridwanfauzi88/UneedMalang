"""UNeed Malang user application.

Security note: authentication uses the local SQLite store. E-mail delivery for OTP
and password resets requires SMTP values supplied through environment variables.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import sys
import time
from functools import wraps
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta

import pandas as pd
from dotenv import load_dotenv
from flask import (
    Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for,
)
from flask_mail import Mail, Message
from werkzeug.security import check_password_hash, generate_password_hash

_PROJECT_ROOT_IMPORT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT_IMPORT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_IMPORT))

from shared.community_schema import ensure_community_schema
from shared.location import (
    google_maps_directions_url, google_maps_search_url, haversine_km,
    parse_coordinates as parse_location_coordinates, valid_lat_lng,
)
from shared.membership import load_membership_config

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)
UPLOAD_FOLDER = BASE_DIR / "uploads_excel"
INSTANCE_DIR = BASE_DIR / "instance"
DB_PATH = Path(os.environ.get("UNEED_DATABASE_PATH", INSTANCE_DIR / "uneed_users.sqlite3"))
DSS_CONFIG_PATH = Path(os.environ.get("UNEED_DSS_CONFIG_PATH", PROJECT_ROOT / "instance" / "dss_config.json"))
SITE_CONFIG_PATH = Path(os.environ.get("UNEED_SITE_CONFIG_PATH", PROJECT_ROOT / "instance" / "site_config.json"))
ALLOWED_EXTENSIONS = {"xlsx", "xls", "csv"}
OTP_TTL_SECONDS = 10 * 60
OTP_MAX_ATTEMPTS = 5
RESET_TTL_SECONDS = 60 * 60

CATEGORIES = {
    "kos": "Kos & Kontrakan",
    "warung": "Tempat Makan & Warung",
    "cafe": "Café & Working Space",
    "klinik": "Layanan Kesehatan",
    "atk": "ATK & Percetakan",
    "mal": "Pusat Perbelanjaan & Mall",
}
CATEGORY_ICONS = {"kos": "🏠", "warung": "🍲", "cafe": "☕", "klinik": "🏥", "atk": "🖨️", "mal": "🛍️"}
LOCATION_ANCHOR_NAME = "lokasi perangkat setelah izin diberikan"
DEFAULT_DSS_CONFIG = {
    "bayesian_m": 5.0,
    "bayesian_c": 4.2,
    "w_price": 35.0,
    "w_dist": 25.0,
    "w_rating": 40.0,
}

app = Flask(__name__)
_secret_key = os.environ.get("UNEED_SECRET_KEY", "").strip()
if os.environ.get("UNEED_ENV", "development").lower() == "production" and not _secret_key:
    raise RuntimeError("UNEED_SECRET_KEY wajib diatur pada lingkungan production.")
app.secret_key = _secret_key or secrets.token_hex(32)
app.config.update(
    SESSION_PERMANENT=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("UNEED_COOKIE_SECURE", "true" if os.environ.get("UNEED_ENV", "development").lower() == "production" else "false").lower() in {"1", "true", "yes"},
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
    MAIL_SERVER=os.environ.get("MAIL_SERVER", "smtp.gmail.com"),
    MAIL_PORT=int(os.environ.get("MAIL_PORT", "587")),
    MAIL_USE_TLS=os.environ.get("MAIL_USE_TLS", "true").lower() in {"1", "true", "yes"},
    MAIL_USE_SSL=os.environ.get("MAIL_USE_SSL", "false").lower() in {"1", "true", "yes"},
    MAIL_USERNAME=os.environ.get("MAIL_USERNAME", "").strip(),
    MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD", ""),
    MAIL_DEFAULT_SENDER=os.environ.get("MAIL_DEFAULT_SENDER", os.environ.get("MAIL_USERNAME", "")).strip() or None,
)
mail = Mail(app)


def _ensure_instance_dirs() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DSS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)


def _connect_db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _init_db() -> None:
    _ensure_instance_dirs()
    with _connect_db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                preferences_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pending_registrations (
                email TEXT PRIMARY KEY COLLATE NOCASE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                otp_hash TEXT NOT NULL,
                expires_at REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS password_resets (
                email TEXT PRIMARY KEY COLLATE NOCASE,
                token_hash TEXT NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_attempts (
                email TEXT PRIMARY KEY COLLATE NOCASE,
                attempts INTEGER NOT NULL DEFAULT 0,
                window_started REAL NOT NULL,
                blocked_until REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS access_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                visitor_key TEXT NOT NULL,
                user_id INTEGER,
                user_email TEXT,
                user_name TEXT,
                page TEXT NOT NULL,
                accessed_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_access_logs_accessed_at ON access_logs(accessed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_access_logs_user_id ON access_logs(user_id, accessed_at DESC);
            CREATE TABLE IF NOT EXISTS user_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_key TEXT NOT NULL,
                listing_name TEXT NOT NULL,
                address TEXT NOT NULL,
                price TEXT NOT NULL DEFAULT '',
                maps_url TEXT NOT NULL DEFAULT '',
                contact TEXT NOT NULL DEFAULT '',
                opening_hours TEXT NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT '',
                submitter_id INTEGER,
                submitter_name TEXT NOT NULL,
                submitter_email TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
                submitted_at REAL NOT NULL,
                reviewed_at REAL,
                reviewed_by TEXT,
                reviewer_note TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_submissions_status ON user_submissions(status, submitted_at DESC);
            CREATE INDEX IF NOT EXISTS idx_submissions_category ON user_submissions(category_key, status);
            """
        )
        ensure_community_schema(con)


_init_db()



def read_site_config() -> dict[str, Any]:
    """Load maintenance settings shared with the developer console."""
    defaults: dict[str, Any] = {
        "maintenance_enabled": False,
        "maintenance_message": "UNeed sedang menjalani pemeliharaan. Silakan coba kembali beberapa saat lagi.",
        "updated_at": None,
    }
    try:
        if SITE_CONFIG_PATH.is_file():
            with SITE_CONFIG_PATH.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                defaults["maintenance_enabled"] = bool(payload.get("maintenance_enabled", False))
                message = str(payload.get("maintenance_message", defaults["maintenance_message"])).strip()
                defaults["maintenance_message"] = message[:500] or defaults["maintenance_message"]
                defaults["updated_at"] = payload.get("updated_at")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        app.logger.exception("Konfigurasi maintenance tidak dapat dibaca; maintenance dianggap nonaktif.")
    return defaults


def _approved_submission_rows(cat_key: str) -> pd.DataFrame:
    """Convert developer-approved public submissions into rows for the selected category."""
    with _connect_db() as con:
        rows = con.execute(
            "SELECT * FROM user_submissions WHERE category_key = ? AND status = 'approved' ORDER BY reviewed_at, id",
            (cat_key,),
        ).fetchall()
    if not rows:
        return pd.DataFrame()

    name_columns = {
        "kos": "Nama Kos",
        "warung": "Nama Tempat",
        "cafe": "Nama Tempat",
        "klinik": "Nama Klinik",
        "atk": "Nama Toko",
    }
    detail_columns = {
        "kos": "Fasilitas",
        "warung": "Menu Andalan",
        "cafe": "Menu Andalan",
        "klinik": "Jenis Layanan",
        "atk": "Jenis Produk",
    }
    data: list[dict[str, Any]] = []
    for item in rows:
        record: dict[str, Any] = {
            "No": f"APP-{item['id']}",
            name_columns[cat_key]: item["listing_name"],
            "Alamat": item["address"],
            "Link Google Maps": item["maps_url"],
            "Kontak/WA": item["contact"],
            "Jam Buka": item["opening_hours"],
            detail_columns[cat_key]: item["details"],
        }
        if cat_key == "kos":
            record["Harga per Bulan (Rp)"] = item["price"]
        elif cat_key in {"warung", "cafe"}:
            record["Range Harga (Rp)"] = item["price"]
        elif item["price"]:
            record["Perkiraan Biaya (Rp)"] = item["price"]
        record["Status Data"] = "Disetujui"
        record["Catatan"] = "Pengajuan komunitas telah diverifikasi dan disetujui."
        data.append(record)
    return pd.DataFrame(data)


def get_dss_config() -> dict[str, float]:
    config: dict[str, Any] = DEFAULT_DSS_CONFIG.copy()
    try:
        if DSS_CONFIG_PATH.exists():
            with DSS_CONFIG_PATH.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            for key in config:
                if key in data:
                    value = float(data[key])
                    if math.isfinite(value):
                        config[key] = value
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        app.logger.exception("Konfigurasi DSS tidak dapat dibaca. Menggunakan nilai default.")
    if config["bayesian_m"] <= 0:
        config["bayesian_m"] = DEFAULT_DSS_CONFIG["bayesian_m"]
    if not 0 <= config["bayesian_c"] <= 5:
        config["bayesian_c"] = DEFAULT_DSS_CONFIG["bayesian_c"]
    weights = [config["w_price"], config["w_dist"], config["w_rating"]]
    if any(not 0 <= w <= 100 for w in weights) or not math.isclose(sum(weights), 100, abs_tol=0.01):
        config.update({k: DEFAULT_DSS_CONFIG[k] for k in ("w_price", "w_dist", "w_rating")})
    return config


def write_dss_config(config: dict[str, float]) -> None:
    """Atomically persist a validated shared configuration for both Flask apps."""
    _ensure_instance_dirs()
    temp_path = DSS_CONFIG_PATH.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temp_path, DSS_CONFIG_PATH)


def is_valid_email(email: str) -> bool:
    if not email or len(email) > 254:
        return False
    return re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+", email) is not None


def _normalise_email(value: Any) -> str:
    return str(value or "").strip().lower()


def parse_price(value: Any) -> tuple[float, float] | None:
    """Parse common Indonesian prices, e.g. '25-50K', 'Rp 800.000' or '15 rb'."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    raw = str(value).strip().lower().replace("rp", "").replace("\u00a0", " ")
    raw = raw.replace("–", "-").replace("—", "-").strip()
    if not raw or raw in {"-", "n/a", "na", "none", "nan"}:
        return None
    multiplier = 1000.0 if any(unit in raw for unit in ("k", "rb", "ribu")) else 1.0
    raw = re.sub(r"(?:ribu(?:an)?|rb|k)\b", "", raw)
    # A dot followed by three digits is treated as Indonesian thousands grouping.
    raw = re.sub(r"(?<=\d)\.(?=\d{3}(?:\D|$))", "", raw)
    numbers = re.findall(r"\d+(?:[,.]\d+)?", raw)
    if not numbers:
        return None
    parsed: list[float] = []
    for number in numbers[:2]:
        normalized = number.replace(",", ".")
        try:
            result = float(normalized) * multiplier
        except ValueError:
            continue
        if math.isfinite(result) and result > 0:
            parsed.append(result)
    if not parsed:
        return None
    return min(parsed), max(parsed)


def parse_rating(value: Any) -> float | None:
    try:
        rating = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return rating if math.isfinite(rating) and 0 <= rating <= 5 else None


def to_int(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = int(str(value).replace(".", "").replace(",", "").strip())
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def parse_coordinates(value: Any) -> tuple[float, float] | None:
    """Parse a recorded latitude/longitude pair and reject malformed coordinates."""
    return parse_location_coordinates(value)


def parse_distance_km(value: Any, origin: tuple[float, float] | None = None) -> float | None:
    """Return straight-line distance only when a real origin was supplied.

    No campus or city-centre fallback is used: callers without a location receive
    ``None``, so DSS can omit the distance criterion rather than imply GPS data.
    """
    coords = parse_coordinates(value)
    validated_origin = valid_lat_lng(*(origin or (None, None))) if origin else None
    if coords is None or validated_origin is None:
        return None
    return haversine_km(*validated_origin, *coords)


def parse_review_count(record: dict[str, Any]) -> int | None:
    candidates = ("Jumlah Ulasan", "Jumlah Review", "Review Count", "Reviews", "Ulasan")
    for column in candidates:
        if column in record:
            value = to_int(record.get(column))
            if value is not None:
                return value
    return None


def bayesian_rating(rating: float | None, reviews: int | None, m: float, c: float) -> float | None:
    """Apply the weighted-average Bayesian score when an actual review count exists.

    Datasets without a review-count column retain their recorded rating. We do not
    invent a review count just to force the Bayesian formula to run.
    """
    if rating is None:
        return None
    if reviews is None:
        return rating
    return ((reviews / (reviews + m)) * rating) + ((m / (reviews + m)) * c)


def read_csv_safe(filepath: Path) -> pd.DataFrame:
    """Read CSV with BOM/legacy encoding and comma/semicolon delimiter tolerance."""
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            frame = pd.read_csv(filepath, encoding=encoding, sep=None, engine="python", skip_blank_lines=True)
            if len(frame.columns) == 1 and ";" in str(frame.columns[0]):
                frame = pd.read_csv(filepath, encoding=encoding, sep=";", skip_blank_lines=True)
            return frame
        except (UnicodeDecodeError, pd.errors.ParserError, ValueError) as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise ValueError("Format CSV tidak dapat dibaca.")


def _category_file(cat_key: str) -> tuple[Path | None, str | None]:
    for extension in ("csv", "xlsx", "xls"):
        path = UPLOAD_FOLDER / f"{cat_key}.{extension}"
        if path.is_file():
            return path, extension
    return None, None


def read_category_dataframe(cat_key: str) -> pd.DataFrame:
    if cat_key not in CATEGORIES:
        raise ValueError("Kategori tidak valid.")
    path, extension = _category_file(cat_key)
    if path is None or extension is None:
        return pd.DataFrame()
    frame = read_csv_safe(path) if extension == "csv" else pd.read_excel(path)
    frame.columns = [str(column).replace("\ufeff", "").strip() for column in frame.columns]
    frame = frame.dropna(how="all")
    if "No" in frame.columns:
        numeric_ids = pd.to_numeric(frame["No"], errors="coerce")
        # Drop summary/footer rows only if there are identifiable numeric data rows.
        if numeric_ids.notna().any():
            frame = frame.loc[numeric_ids.notna()]
    frame = frame.reset_index(drop=True)
    return frame


def _minmax_cost(value: float | None, bounds: tuple[float, float] | None) -> float | None:
    if value is None or bounds is None:
        return None
    low, high = bounds
    if math.isclose(low, high):
        return 1.0
    return max(0.0, min(1.0, (high - value) / (high - low)))


def _has_dataset_value(value: Any) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return str(value).strip().casefold() not in {"", "-", "none", "nan", "n/a", "na"}


def clean_dataset_value(value: Any) -> str:
    return str(value).strip() if _has_dataset_value(value) else ""


def normalise_kos_type(value: Any) -> str:
    raw = clean_dataset_value(value).casefold()
    if not raw:
        return ""
    if "campur" in raw or ("putra" in raw and "putri" in raw):
        return "Campur"
    if "putra" in raw:
        return "Putra"
    if "putri" in raw:
        return "Putri"
    return ""


def normalise_availability(value: Any) -> str:
    raw = clean_dataset_value(value).casefold()
    if raw in {"y", "yes", "ya", "tersedia", "available", "1"}:
        return "Tersedia"
    if raw in {"n", "no", "tidak", "kosong", "penuh", "tidak tersedia", "unavailable", "0"}:
        return "Tidak tersedia"
    return ""


def safe_google_maps_url(value: Any) -> str | None:
    raw = safe_external_url(value)
    if not raw:
        return None
    host = (urlparse(raw).hostname or "").lower().rstrip(".")
    allowed = host == "maps.app.goo.gl" or host == "goo.gl" or host == "maps.google.com" or host == "google.com" or host.endswith(".google.com")
    return raw if allowed else None


def normalise_whatsapp_url(value: Any) -> str | None:
    """Return a wa.me URL only for plausible phone-number values, never coordinates/free text."""
    raw = clean_dataset_value(value)
    if not raw or len(raw) > 24:
        return None
    if re.search(r"[A-Za-z]", raw):
        return None
    digits = re.sub(r"\D", "", raw)
    if not 9 <= len(digits) <= 15:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = "62" + digits[1:]
    elif not digits.startswith("62"):
        digits = "62" + digits
    if not 10 <= len(digits) <= 15:
        return None
    # Reject values that look like malformed decimal coordinates rather than phone numbers.
    if "." in raw and "," in raw:
        return None
    return f"https://wa.me/{digits}"


def format_rupiah(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(number) or number < 0:
        return ""
    return "Rp" + f"{number:,.0f}".replace(",", ".")


app.jinja_env.filters["rupiah"] = format_rupiah


def build_category_records(cat_key: str, config: dict[str, float] | None = None, origin: tuple[float, float] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Load and score a category; distance participates only with a supplied device origin."""
    frame = read_category_dataframe(cat_key)
    approved_frame = _approved_submission_rows(cat_key)
    if not approved_frame.empty:
        frame = pd.concat([frame, approved_frame], ignore_index=True, sort=False)
    headers = frame.columns.tolist()
    config = config or get_dss_config()
    records: list[dict[str, Any]] = []
    for idx, source in enumerate(frame.to_dict(orient="records")):
        row = {str(k): ("-" if pd.isna(v) or str(v).strip() == "" else v) for k, v in source.items()}
        price_col = next((col for col in ("Harga per Bulan (Rp)", "Range Harga (Rp)", "Perkiraan Biaya (Rp)") if col in row), None)
        price_range = parse_price(row.get(price_col)) if price_col else None
        row["_record_id"] = idx
        row["_pmin"] = price_range[0] if price_range else None
        row["_pmax"] = price_range[1] if price_range else None
        row["_price_mid"] = sum(price_range) / 2 if price_range else None
        row["_rating"] = parse_rating(row.get("Rating Maps"))
        row["_reviews"] = parse_review_count(row)
        row["_bayesian_rating"] = bayesian_rating(row["_rating"], row["_reviews"], config["bayesian_m"], config["bayesian_c"])
        source_name = next((str(row[col]).strip() for col in ("Nama Kos", "Nama Tempat", "Nama Toko", "Nama Klinik") if _has_dataset_value(row.get(col))), None)
        row["_name"] = source_name if source_name is not None else "Tanpa Nama"
        row["_has_name"] = source_name is not None
        row["_address"] = str(row.get("Alamat", "-")).strip()
        row["_coordinates"] = parse_coordinates(row.get("Koordinat (Lat, Long)"))
        row["_dist"] = parse_distance_km(row.get("Koordinat (Lat, Long)"), origin=origin)
        row["_google_maps_url"] = safe_google_maps_url(row.get("Link Google Maps"))
        row["_maps_url"] = row["_google_maps_url"] or google_maps_search_url(row["_name"], row["_address"], row["_coordinates"])
        row["_directions_url"] = google_maps_directions_url(row["_name"], row["_address"], row["_coordinates"])
        row["_menu_url"] = safe_external_url(row.get("Link Menu"))
        if cat_key == "kos":
            row["_whatsapp_url"] = normalise_whatsapp_url(row.get("Kontak/WA"))
            row["_type"] = normalise_kos_type(row.get("Tipe (Putra/Putri/Campur)"))
            row["_area"] = clean_dataset_value(row.get("Kecamatan/Area")) or clean_dataset_value(row.get("Area"))
            facility_value = clean_dataset_value(row.get("Fasilitas"))
            row["_facilities"] = [part.strip() for part in re.split(r"[,;|]", facility_value) if part.strip()] if facility_value else []
            row["_availability"] = normalise_availability(row.get("Kamar Kosong (Y/N)"))
        records.append(row)

    price_values = [r["_price_mid"] for r in records if r["_price_mid"] is not None]
    distance_values = [r["_dist"] for r in records if r["_dist"] is not None]
    price_bounds = (min(price_values), max(price_values)) if price_values else None
    distance_bounds = (min(distance_values), max(distance_values)) if distance_values else None
    weights = {
        "price": config["w_price"] / 100,
        "dist": config["w_dist"] / 100,
        "rating": config["w_rating"] / 100,
    }
    for row in records:
        components: list[tuple[float, float]] = []
        price_score = _minmax_cost(row["_price_mid"], price_bounds)
        distance_score = _minmax_cost(row["_dist"], distance_bounds)
        bayesian = row["_bayesian_rating"]
        rating_score = max(0.0, min(1.0, bayesian / 5)) if bayesian is not None else None
        for criterion, score in (("price", price_score), ("dist", distance_score), ("rating", rating_score)):
            if score is not None and weights[criterion] > 0:
                components.append((weights[criterion], score))
        weight_sum = sum(weight for weight, _ in components)
        row["_dss_score"] = round(sum(weight * score for weight, score in components) / weight_sum, 4) if weight_sum else None
    return records, headers


def safe_external_url(value: Any) -> str | None:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return raw
    return None


def sort_none_last(rows: list[dict[str, Any]], key: str, reverse: bool = False) -> list[dict[str, Any]]:
    known = [row for row in rows if row.get(key) is not None and math.isfinite(float(row[key]))]
    missing = [row for row in rows if row.get(key) is None or not math.isfinite(float(row[key]))]
    known.sort(key=lambda row: float(row[key]), reverse=reverse)
    return known + missing


def get_csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@app.context_processor
def inject_common_template_values() -> dict[str, Any]:
    return {
        "csrf_token": get_csrf_token,
        "current_user_email": session.get("user"),
        "current_user_name": session.get("user_name", ""),
        "user_theme": session.get("theme", "light"),
        "font_size": session.get("font_size", "medium"),
        "maps_interactive_enabled": os.environ.get("UNEED_ENABLE_INTERACTIVE_MAP", "false").lower() in {"1", "true", "yes"},
        "membership_config": load_membership_config(),
    }


@app.before_request
def protect_post_requests() -> None:
    if request.method == "POST":
        sent_token = request.form.get("csrf_token", "") or request.headers.get("X-CSRFToken", "")
        saved_token = session.get("_csrf_token", "")
        if not sent_token or not saved_token or not hmac.compare_digest(sent_token, saved_token):
            abort(400, description="Token keamanan form tidak valid. Muat ulang halaman dan coba kembali.")


@app.after_request
def apply_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(self)")
    return response


@app.before_request
def enforce_site_maintenance():
    if request.endpoint in {"static", "maintenance_page"}:
        return None
    state = read_site_config()
    if state["maintenance_enabled"]:
        return redirect(url_for("maintenance_page"))
    return None


@app.after_request
def record_page_access(response):
    """Record page-level GET activity without storing IPs, query strings, or form data."""
    if request.method != "GET" or request.endpoint in {None, "static"} or request.url_rule is None:
        return response
    try:
        visitor_key = session.get("visitor_key")
        if not visitor_key:
            visitor_key = secrets.token_urlsafe(12)
            session["visitor_key"] = visitor_key
        page = request.url_rule.rule
        if request.endpoint == "category_detail":
            category_arg = str((request.view_args or {}).get("cat_name", "")).lower()
            if category_arg in CATEGORIES:
                page = f"/category [{category_arg}]"
        accessed_at = time.time()
        with _connect_db() as con:
            con.execute(
                """INSERT INTO access_logs
                   (visitor_key, user_id, user_email, user_name, page, accessed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    visitor_key,
                    session.get("user_id"),
                    session.get("user"),
                    session.get("user_name"),
                    page,
                    accessed_at,
                ),
            )
            # Periodically prune page-view history older than 180 days.
            if int(accessed_at) % 101 == 0:
                con.execute("DELETE FROM access_logs WHERE accessed_at < ?", (accessed_at - 180 * 86400,))
    except Exception:
        app.logger.exception("Gagal merekam aktivitas page view.")
    return response


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            flash("Silakan masuk terlebih dahulu.", "info")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def _email_delivery_configured() -> bool:
    return bool(app.config.get("MAIL_USERNAME") and app.config.get("MAIL_PASSWORD") and app.config.get("MAIL_DEFAULT_SENDER"))


def _send_email(subject: str, recipient: str, body: str) -> None:
    if not _email_delivery_configured():
        raise RuntimeError("SMTP belum dikonfigurasi. Atur MAIL_USERNAME, MAIL_PASSWORD, dan MAIL_DEFAULT_SENDER.")
    message = Message(subject=subject, recipients=[recipient], body=body, sender=app.config["MAIL_DEFAULT_SENDER"])
    mail.send(message)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _login_is_blocked(email: str) -> bool:
    with _connect_db() as con:
        row = con.execute("SELECT blocked_until FROM login_attempts WHERE email = ?", (email,)).fetchone()
    return bool(row and row["blocked_until"] > time.time())


def _record_failed_login(email: str) -> None:
    now = time.time()
    window_seconds = 15 * 60
    block_seconds = 10 * 60
    with _connect_db() as con:
        entry = con.execute("SELECT attempts, window_started FROM login_attempts WHERE email = ?", (email,)).fetchone()
        if not entry or now - entry["window_started"] > window_seconds:
            con.execute(
                "INSERT OR REPLACE INTO login_attempts (email, attempts, window_started, blocked_until) VALUES (?, 1, ?, 0)",
                (email, now),
            )
            return
        attempts = entry["attempts"] + 1
        blocked_until = now + block_seconds if attempts >= 5 else 0
        con.execute(
            "UPDATE login_attempts SET attempts = ?, blocked_until = ? WHERE email = ?",
            (attempts, blocked_until, email),
        )


def _finish_registration(email: str, otp_value: str, submitted_password: str | None = None) -> bool:
    now = time.time()
    normal_email = _normalise_email(email)
    with _connect_db() as con:
        pending = con.execute("SELECT * FROM pending_registrations WHERE email = ?", (normal_email,)).fetchone()
        if not pending:
            flash("Silakan minta kode OTP terlebih dahulu.", "danger")
            return False
        if pending["expires_at"] < now:
            con.execute("DELETE FROM pending_registrations WHERE email = ?", (normal_email,))
            flash("Kode OTP kedaluwarsa. Silakan kirim ulang OTP.", "danger")
            return False
        if pending["attempts"] >= OTP_MAX_ATTEMPTS:
            con.execute("DELETE FROM pending_registrations WHERE email = ?", (normal_email,))
            flash("Batas percobaan OTP tercapai. Silakan kirim ulang kode.", "danger")
            return False
        if submitted_password is not None and not check_password_hash(pending["password_hash"], submitted_password):
            flash("Kata sandi berbeda dari yang digunakan saat meminta OTP. Minta OTP baru atau gunakan kata sandi semula.", "danger")
            return False
        if not re.fullmatch(r"\d{6}", str(otp_value or "")) or not check_password_hash(pending["otp_hash"], str(otp_value)):
            con.execute("UPDATE pending_registrations SET attempts = attempts + 1 WHERE email = ?", (normal_email,))
            flash("Kode OTP tidak valid.", "danger")
            return False
        try:
            con.execute(
                "INSERT INTO users (email, name, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (normal_email, pending["name"], pending["password_hash"], now),
            )
        except sqlite3.IntegrityError:
            con.execute("DELETE FROM pending_registrations WHERE email = ?", (normal_email,))
            flash("Email ini sudah terdaftar. Silakan masuk.", "danger")
            return False
        con.execute("DELETE FROM pending_registrations WHERE email = ?", (normal_email,))
    session.pop("pending_email", None)
    flash("Registrasi berhasil. Silakan masuk menggunakan akun Anda.", "success")
    return True


@app.route("/")
def index():
    return redirect(url_for("dashboard" if session.get("user") else "login"))


@app.route("/maintenance")
def maintenance_page():
    state = read_site_config()
    if not state["maintenance_enabled"]:
        return redirect(url_for("index"))
    response = app.make_response(render_template("maintenance.html", maintenance_message=state["maintenance_message"]))
    response.status_code = 503
    response.headers["Retry-After"] = "300"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


@app.route("/submit-listing", methods=["GET", "POST"])
@login_required
def submit_listing():
    if request.method == "POST":
        category_key = request.form.get("category_key", "").strip().lower()
        listing_name = request.form.get("listing_name", "").strip()
        address = request.form.get("address", "").strip()
        price = request.form.get("price", "").strip()
        maps_url = request.form.get("maps_url", "").strip()
        contact = request.form.get("contact", "").strip()
        opening_hours = request.form.get("opening_hours", "").strip()
        details = request.form.get("details", "").strip()

        errors = []
        if category_key not in CATEGORIES:
            errors.append("Pilih salah satu dari lima kategori yang tersedia.")
        if not listing_name or len(listing_name) > 160:
            errors.append("Nama tempat/layanan wajib diisi dan maksimal 160 karakter.")
        if not address or len(address) > 300:
            errors.append("Alamat wajib diisi dan maksimal 300 karakter.")
        if len(price) > 100 or len(contact) > 100 or len(opening_hours) > 100:
            errors.append("Harga, kontak, dan jam buka maksimal 100 karakter per kolom.")
        if len(details) > 800:
            errors.append("Deskripsi maksimal 800 karakter.")
        if len(maps_url) > 500:
            errors.append("Link Google Maps maksimal 500 karakter.")
        elif maps_url and safe_external_url(maps_url) is None:
            errors.append("Link Google Maps harus berupa URL yang dimulai dengan http:// atau https://.")
        if errors:
            for message in errors:
                flash(message, "danger")
        else:
            with _connect_db() as con:
                con.execute(
                    """INSERT INTO user_submissions
                       (category_key, listing_name, address, price, maps_url, contact,
                        opening_hours, details, submitter_id, submitter_name, submitter_email,
                        status, submitted_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                    (
                        category_key, listing_name, address, price, maps_url, contact,
                        opening_hours, details, session.get("user_id"),
                        session.get("user_name") or session.get("user"), session.get("user"), time.time(),
                    ),
                )
            flash("Pengajuan berhasil dikirim. Data baru akan muncul setelah diperiksa dan disetujui developer.", "success")
            return redirect(url_for("my_submissions"))
    return render_template("submit_listing.html", categories=CATEGORIES, category_icons=CATEGORY_ICONS)


@app.route("/my-submissions")
@login_required
def my_submissions():
    with _connect_db() as con:
        rows = con.execute(
            "SELECT * FROM user_submissions WHERE submitter_id = ? ORDER BY submitted_at DESC LIMIT 100",
            (session.get("user_id"),),
        ).fetchall()
    submissions = []
    for row in rows:
        item = dict(row)
        try:
            item["submitted_at_display"] = time.strftime("%d-%m-%Y %H:%M", time.localtime(float(item["submitted_at"])))
        except (TypeError, ValueError, OSError, OverflowError):
            item["submitted_at_display"] = "Waktu tidak tersedia"
        submissions.append(item)
    return render_template("my_submissions.html", submissions=submissions, categories=CATEGORIES)


def _community_member_label(user_id: int | None) -> str:
    """Stable pseudonym for public community views; account names stay private."""
    if not user_id:
        return "Kontributor UNeed"
    code = hashlib.sha256(f"UNeed-public-contributor:{int(user_id)}".encode("utf-8")).hexdigest()[:6].upper()
    return f"Kontributor {code}"


def _period_start(period: str) -> float:
    """Return a UTC timestamp for the selected contributor-ranking period."""
    now = datetime.now(timezone.utc)
    selected = str(period or "monthly").strip().lower()
    if selected == "all_time":
        return 0.0
    if selected == "weekly":
        monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        return monday.timestamp()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def _period_label(period: str) -> str:
    return {"weekly": "Minggu ini", "monthly": "Bulan ini", "all_time": "Sepanjang waktu"}.get(str(period or "monthly").strip().lower(), "Bulan ini")


def _format_timestamp_local(value: object) -> str:
    try:
        return time.strftime("%d-%m-%Y %H:%M", time.localtime(float(value)))
    except (TypeError, ValueError, OSError, OverflowError):
        return "Waktu tidak tersedia"


def _current_month_start() -> float:
    # Kept as a compatibility helper for existing tests and other project code.
    return _period_start("monthly")


def _contributor_stats(user_id: int) -> dict[str, Any]:
    config = load_membership_config()
    period = config["contribution"].get("period", "monthly")
    period_start = _period_start(period)
    month_start = _current_month_start()
    with _connect_db() as con:
        total = int(con.execute("SELECT COALESCE(SUM(points), 0) FROM contribution_events WHERE recipient_user_id = ?", (user_id,)).fetchone()[0])
        monthly = int(con.execute("SELECT COALESCE(SUM(points), 0) FROM contribution_events WHERE recipient_user_id = ? AND created_at >= ?", (user_id, month_start)).fetchone()[0])
        period_points = int(con.execute("SELECT COALESCE(SUM(points), 0) FROM contribution_events WHERE recipient_user_id = ? AND created_at >= ?", (user_id, period_start)).fetchone()[0])
        events = con.execute("SELECT event_type, points, note, created_at FROM contribution_events WHERE recipient_user_id = ? ORDER BY created_at DESC, id DESC LIMIT 50", (user_id,)).fetchall()
        counts = con.execute("SELECT COUNT(*) FROM community_recommendations WHERE user_id = ? AND status = 'approved'", (user_id,)).fetchone()[0]
    badges = [badge for badge in config["contribution"]["badges"] if total >= int(badge["threshold"])]
    return {
        "total_points": total,
        "monthly_points": monthly,
        "period_points": period_points,
        "period_label": _period_label(period),
        "approved_recommendations": int(counts),
        "badges": badges,
        "events": [dict(event, created_at_display=time.strftime("%d-%m-%Y %H:%M", time.localtime(float(event["created_at"])))) for event in events],
    }


@app.route("/community")
@login_required
def community():
    selected_category = str(request.args.get("category", "all")).strip().lower()
    if selected_category not in {"all", *CATEGORIES.keys()}:
        selected_category = "all"
    viewer_id = int(session.get("user_id") or 0)
    sql = """SELECT r.*,
                     (SELECT COUNT(*) FROM recommendation_helpful h WHERE h.recommendation_id = r.id) AS helpful_count,
                     EXISTS(SELECT 1 FROM recommendation_helpful h WHERE h.recommendation_id = r.id AND h.user_id = ?) AS viewer_helpful,
                     EXISTS(SELECT 1 FROM saved_community_recommendations s WHERE s.recommendation_id = r.id AND s.user_id = ?) AS viewer_saved,
                     (SELECT COUNT(*) FROM recommendation_reports p WHERE p.recommendation_id = r.id AND p.status = 'open') AS open_report_count
              FROM community_recommendations r LEFT JOIN users u ON u.id = r.user_id
              WHERE r.status = 'approved'"""
    params: list[Any] = [viewer_id, viewer_id]
    if selected_category != "all":
        sql += " AND r.category_key = ?"
        params.append(selected_category)
    sql += " ORDER BY r.submitted_at DESC LIMIT 100"
    config = load_membership_config()
    period = config["contribution"].get("period", "monthly")
    leaderboard_start = _period_start(period)
    with _connect_db() as con:
        recommendations = [dict(row) for row in con.execute(sql, params).fetchall()]
        leaderboard_rows = con.execute(
            """SELECT u.id,
                      (SELECT COALESCE(SUM(e.points), 0) FROM contribution_events e
                       WHERE e.recipient_user_id = u.id AND e.created_at >= ?) AS points,
                      (SELECT COUNT(*) FROM community_recommendations r
                       WHERE r.user_id = u.id AND r.status = 'approved') AS approved_count
               FROM users u ORDER BY points DESC, approved_count DESC, u.id ASC LIMIT 10""",
            (leaderboard_start,),
        ).fetchall()
    for rec in recommendations:
        rec["contributor_name"] = _community_member_label(rec.get("user_id"))
        rec["maps_link"] = rec.get("maps_url") or google_maps_search_url(
            rec.get("place_name", ""), rec.get("address", ""),
            (rec["latitude"], rec["longitude"]) if rec.get("latitude") is not None and rec.get("longitude") is not None else None,
        )
        rec["directions_link"] = google_maps_directions_url(
            rec.get("place_name", ""), rec.get("address", ""),
            (rec["latitude"], rec["longitude"]) if rec.get("latitude") is not None and rec.get("longitude") is not None else None,
        )
        rec["submitted_at_display"] = time.strftime("%d-%m-%Y", time.localtime(float(rec["submitted_at"])))
    leaderboard = [dict(row, name=_community_member_label(row["id"])) for row in leaderboard_rows]
    with _connect_db() as con:
        pending_mine = int(con.execute("SELECT COUNT(*) FROM community_recommendations WHERE user_id = ? AND status = 'pending'", (viewer_id,)).fetchone()[0])
    stats = _contributor_stats(viewer_id)
    return render_template(
        "community.html", recommendations=recommendations, categories=CATEGORIES,
        category_icons=CATEGORY_ICONS, selected_category=selected_category,
        leaderboard=leaderboard, stats=stats, pending_mine=pending_mine,
        period_label=_period_label(period),
    )


@app.route("/community/new", methods=["GET", "POST"])
@login_required
def community_new():
    if request.method == "POST":
        category_key = str(request.form.get("category_key", "")).strip().lower()
        place_name = str(request.form.get("place_name", "")).strip()
        address = str(request.form.get("address", "")).strip()
        reason = str(request.form.get("reason", "")).strip()
        experience = str(request.form.get("experience", "")).strip()
        maps_input = str(request.form.get("maps_url", "")).strip()
        lat_text = str(request.form.get("latitude", "")).strip()
        lng_text = str(request.form.get("longitude", "")).strip()
        errors: list[str] = []
        if category_key not in CATEGORIES:
            errors.append("Pilih salah satu dari lima kategori UNeed.")
        if not place_name or len(place_name) > 160:
            errors.append("Nama tempat wajib diisi dan maksimal 160 karakter.")
        if len(address) > 300:
            errors.append("Alamat maksimal 300 karakter.")
        if len(reason) < 10 or len(reason) > 800:
            errors.append("Alasan rekomendasi harus berisi 10–800 karakter.")
        if len(experience) > 1200:
            errors.append("Pengalaman atau informasi tambahan maksimal 1.200 karakter.")
        if len(maps_input) > 500 or (maps_input and not safe_google_maps_url(maps_input)):
            errors.append("Tautan harus berupa URL Google Maps yang valid.")
        coords = None
        if bool(lat_text) != bool(lng_text):
            errors.append("Lintang dan bujur harus diisi berpasangan.")
        elif lat_text and lng_text:
            coords = valid_lat_lng(lat_text, lng_text)
            if coords is None:
                errors.append("Koordinat tidak valid. Lintang harus −90..90 dan bujur −180..180.")
        if errors:
            for message in errors:
                flash(message, "danger")
        else:
            now = time.time()
            with _connect_db() as con:
                duplicate = con.execute(
                    """SELECT id FROM community_recommendations
                       WHERE category_key = ? AND lower(place_name) = lower(?) AND lower(address) = lower(?)
                         AND status IN ('pending','approved') LIMIT 1""",
                    (category_key, place_name, address),
                ).fetchone()
                if duplicate:
                    flash("Rekomendasi dengan nama tempat dan alamat yang sama sudah dikirim atau telah diterbitkan.", "danger")
                else:
                    con.execute(
                        """INSERT INTO community_recommendations
                           (user_id, category_key, place_name, address, reason, experience,
                            latitude, longitude, maps_url, status, submitted_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                        (int(session.get("user_id") or 0), category_key, place_name, address, reason,
                         experience, coords[0] if coords else None, coords[1] if coords else None,
                         safe_google_maps_url(maps_input) or "", now),
                    )
                    flash("Rekomendasi berhasil dikirim dan menunggu moderasi. Poin baru diberikan setelah rekomendasi disetujui.", "success")
                    return redirect(url_for("community"))
    return render_template("community_new.html", categories=CATEGORIES, category_icons=CATEGORY_ICONS)


@app.route("/community/<int:recommendation_id>/helpful", methods=["POST"])
@login_required
def community_helpful(recommendation_id: int):
    user_id = int(session.get("user_id") or 0)
    now = time.time()
    config = load_membership_config()["contribution"]
    with _connect_db() as con:
        rec = con.execute("SELECT id, user_id, status FROM community_recommendations WHERE id = ?", (recommendation_id,)).fetchone()
        if not rec or rec["status"] != "approved":
            flash("Rekomendasi tidak ditemukan.", "danger")
        elif rec["user_id"] == user_id:
            flash("Rekomendasi milik sendiri tidak dapat ditandai membantu.", "info")
        else:
            cur = con.execute(
                "INSERT OR IGNORE INTO recommendation_helpful (recommendation_id, user_id, created_at) VALUES (?, ?, ?)",
                (recommendation_id, user_id, now),
            )
            if cur.rowcount:
                con.execute(
                    "INSERT INTO contribution_events (recipient_user_id, actor_user_id, recommendation_id, event_type, points, note, created_at) VALUES (?, ?, ?, 'helpful_vote_received', ?, 'Rekomendasi ditandai membantu oleh anggota lain', ?)",
                    (rec["user_id"], user_id, recommendation_id, int(config["helpful_vote_points"]), now),
                )
                flash("Terima kasih. Tandai membantu hanya dapat diberikan sekali untuk setiap rekomendasi.", "success")
            else:
                flash("Anda sudah menandai rekomendasi ini sebagai membantu.", "info")
    return redirect(url_for("community"))


@app.route("/community/<int:recommendation_id>/report", methods=["POST"])
@login_required
def community_report(recommendation_id: int):
    user_id = int(session.get("user_id") or 0)
    reason_input = str(request.form.get("reason", "")).strip()[:80]
    reason_map = {
        "incorrect": "Informasi keliru",
        "duplicate": "Spam atau duplikat",
        "spam": "Spam atau duplikat",
        "misleading": "Informasi menyesatkan",
        "other": "Lainnya",
        "Informasi keliru": "Informasi keliru",
        "Spam atau duplikat": "Spam atau duplikat",
        "Konten tidak pantas": "Konten tidak pantas",
        "Konflik kepentingan": "Konflik kepentingan",
        "Lainnya": "Lainnya",
    }
    reason = reason_map.get(reason_input)
    if reason is None:
        flash("Pilih alasan laporan yang tersedia.", "danger")
        return redirect(url_for("community"))
    with _connect_db() as con:
        rec = con.execute("SELECT user_id, status FROM community_recommendations WHERE id = ?", (recommendation_id,)).fetchone()
        if not rec or rec["status"] != "approved":
            flash("Rekomendasi tidak ditemukan.", "danger")
        elif rec["user_id"] == user_id:
            flash("Anda tidak dapat melaporkan rekomendasi sendiri.", "info")
        else:
            cur = con.execute(
                "INSERT OR IGNORE INTO recommendation_reports (recommendation_id, reporter_id, reason, created_at) VALUES (?, ?, ?, ?)",
                (recommendation_id, user_id, reason, time.time()),
            )
            flash("Laporan diteruskan kepada pengelola untuk ditinjau." if cur.rowcount else "Anda sudah pernah melaporkan rekomendasi ini.", "success" if cur.rowcount else "info")
    return redirect(url_for("community"))


@app.route("/community/<int:recommendation_id>/save", methods=["POST"])
@login_required
def save_community_recommendation(recommendation_id: int):
    user_id = int(session.get("user_id") or 0)
    with _connect_db() as con:
        rec = con.execute("SELECT id, status FROM community_recommendations WHERE id = ?", (recommendation_id,)).fetchone()
        if not rec or rec["status"] != "approved":
            flash("Rekomendasi tidak ditemukan atau belum diterbitkan.", "danger")
        else:
            cur = con.execute(
                "INSERT OR IGNORE INTO saved_community_recommendations (user_id, recommendation_id, saved_at) VALUES (?, ?, ?)",
                (user_id, recommendation_id, time.time()),
            )
            flash("Rekomendasi komunitas disimpan." if cur.rowcount else "Rekomendasi ini sudah tersimpan.", "success" if cur.rowcount else "info")
    return redirect(request.referrer or url_for("community"))


@app.route("/community-saved/<int:saved_id>/remove", methods=["POST"])
@login_required
def remove_saved_community_recommendation(saved_id: int):
    with _connect_db() as con:
        cur = con.execute("DELETE FROM saved_community_recommendations WHERE id = ? AND user_id = ?", (saved_id, int(session.get("user_id") or 0)))
    flash("Rekomendasi dihapus dari koleksi." if cur.rowcount else "Rekomendasi tersimpan tidak ditemukan.", "success" if cur.rowcount else "info")
    return redirect(url_for("saved_places"))


@app.route("/membership")
@login_required
def membership():
    user_id = int(session.get("user_id") or 0)
    with _connect_db() as con:
        user = con.execute("SELECT membership_tier, membership_status, membership_expires_at FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        flash("Informasi akun tidak ditemukan. Silakan masuk kembali.", "danger")
        return redirect(url_for("login"))
    config = load_membership_config()
    status = str(user["membership_status"] or "active").lower()
    expiry = user["membership_expires_at"]
    expired = bool(expiry and float(expiry) <= time.time())
    if expired:
        status = "expired"
    tier = user["membership_tier"] if user["membership_tier"] in config["tiers"] else "basic"
    if status != "active":
        tier = "basic"
    stats = _contributor_stats(user_id)
    period = config["contribution"].get("period", "monthly")
    period_start = _period_start(period)
    with _connect_db() as con:
        leaderboard_row = con.execute(
            """SELECT COUNT(*) + 1 AS rank FROM (
                 SELECT u.id, COALESCE(SUM(e.points), 0) AS points
                 FROM users u LEFT JOIN contribution_events e ON e.recipient_user_id = u.id AND e.created_at >= ?
                 GROUP BY u.id HAVING points > (
                   SELECT COALESCE(SUM(e2.points), 0) FROM contribution_events e2
                   WHERE e2.recipient_user_id = ? AND e2.created_at >= ?
                 )
               )""", (period_start, user_id, period_start),
        ).fetchone()
        membership_history = [dict(row) for row in con.execute(
            "SELECT previous_tier, current_tier, previous_status, current_status, previous_expires_at, current_expires_at, note, changed_at FROM membership_history WHERE user_id = ? ORDER BY changed_at DESC, id DESC LIMIT 50",
            (user_id,),
        ).fetchall()]
        awards = [dict(row) for row in con.execute(
            "SELECT period_key, reward_type, reward_label, status, note, created_at, awarded_at FROM reward_awards WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT 50",
            (user_id,),
        ).fetchall()]
    for row in membership_history:
        row["changed_at_display"] = _format_timestamp_local(row.get("changed_at"))
        row["previous_expiry_display"] = _format_timestamp_local(row.get("previous_expires_at")) if row.get("previous_expires_at") else "Tidak ditetapkan"
        row["current_expiry_display"] = _format_timestamp_local(row.get("current_expires_at")) if row.get("current_expires_at") else "Tidak ditetapkan"
    for award in awards:
        award["created_at_display"] = _format_timestamp_local(award.get("created_at"))
        award["awarded_at_display"] = _format_timestamp_local(award.get("awarded_at")) if award.get("awarded_at") else "Belum diberikan"
    return render_template(
        "membership.html", config=config, current_tier=tier,
        membership_status=status, membership_expires_at=user["membership_expires_at"],
        stats=stats, monthly_rank=int(leaderboard_row["rank"]) if leaderboard_row else 1,
        period_label=_period_label(period), membership_history=membership_history, awards=awards,
    )


@app.route("/saved-places")
@login_required
def saved_places():
    with _connect_db() as con:
        places = [dict(row) for row in con.execute(
            "SELECT * FROM saved_places WHERE user_id = ? ORDER BY saved_at DESC LIMIT 200",
            (int(session.get("user_id") or 0),),
        ).fetchall()]
    for place in places:
        place["category_label"] = CATEGORIES.get(place["category_key"], place["category_key"])
        try:
            records, _ = build_category_records(place["category_key"], get_dss_config())
            current = next((
                r for r in records
                if r["_record_id"] == place["record_id"] and r.get("_has_name")
                and str(r.get("_name", "")).strip().casefold() == str(place.get("place_name", "")).strip().casefold()
                and str(r.get("_address", "")).strip().casefold() == str(place.get("address", "")).strip().casefold()
            ), None)
        except Exception:
            current = None
        place["detail_url"] = url_for("detail", category=place["category_key"], item_id=place["record_id"]) if current else None
        place["data_current"] = bool(current)
    with _connect_db() as con:
        community_places = [dict(row) for row in con.execute(
            """SELECT s.id AS saved_id, s.saved_at, r.id AS recommendation_id, r.category_key, r.place_name,
                      r.address, r.status, r.reason
               FROM saved_community_recommendations s
               JOIN community_recommendations r ON r.id = s.recommendation_id
               WHERE s.user_id = ? ORDER BY s.saved_at DESC LIMIT 200""",
            (int(session.get("user_id") or 0),),
        ).fetchall()]
    for place in community_places:
        place["category_label"] = CATEGORIES.get(place["category_key"], place["category_key"])
        place["community_url"] = url_for("community", category=place["category_key"]) + f"#community-rec-{place['recommendation_id']}" if place["status"] == "approved" else None
    return render_template("saved_places.html", places=places, community_places=community_places)


@app.route("/save-place/<cat_name>/<int:item_id>", methods=["POST"])
@login_required
def save_place(cat_name: str, item_id: int):
    category_key = str(cat_name or "").lower().strip()
    if category_key not in CATEGORIES:
        abort(404)
    try:
        records, _ = build_category_records(category_key, get_dss_config())
    except Exception:
        app.logger.exception("Dataset tidak bisa dimuat saat menyimpan tempat.")
        flash("Data tempat belum bisa dibaca.", "danger")
        return redirect(request.referrer or url_for("category_detail", cat_name=category_key))
    record = next((r for r in records if r["_record_id"] == item_id and r.get("_has_name")), None)
    if not record:
        abort(404)
    with _connect_db() as con:
        cur = con.execute(
            "INSERT OR IGNORE INTO saved_places (user_id, category_key, record_id, place_name, address, saved_at) VALUES (?, ?, ?, ?, ?, ?)",
            (int(session.get("user_id") or 0), category_key, item_id, record["_name"], record["_address"], time.time()),
        )
    flash("Tempat disimpan ke koleksi Anda." if cur.rowcount else "Tempat ini sudah ada di koleksi Anda.", "success" if cur.rowcount else "info")
    return redirect(request.referrer or url_for("saved_places"))


@app.route("/saved-places/<int:saved_id>/remove", methods=["POST"])
@login_required
def remove_saved_place(saved_id: int):
    with _connect_db() as con:
        cur = con.execute("DELETE FROM saved_places WHERE id = ? AND user_id = ?", (saved_id, int(session.get("user_id") or 0)))
    flash("Tempat dihapus dari koleksi." if cur.rowcount else "Koleksi tidak ditemukan.", "success" if cur.rowcount else "info")
    return redirect(url_for("saved_places"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = _normalise_email(request.form.get("email"))
        password = request.form.get("password", "")
        with _connect_db() as con:
            user = con.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        blocked = _login_is_blocked(email) if email else False
        valid_password = bool(user and password and check_password_hash(user["password_hash"], password)) if not blocked else False
        if user and valid_password:
            with _connect_db() as con:
                con.execute("DELETE FROM login_attempts WHERE email = ?", (email,))
            session.clear()
            session["user"] = user["email"]
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            try:
                preferences = json.loads(user["preferences_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                preferences = {}
            session["theme"] = preferences.get("theme", "light")
            session["font_size"] = preferences.get("font_size", "medium")
            return redirect(url_for("booting"))
        if email and not blocked:
            _record_failed_login(email)
        flash("Email atau kata sandi tidak sesuai, atau terlalu banyak percobaan login.", "danger")
    return render_template("login.html")


@app.route("/booting")
@login_required
def booting():
    return render_template("booting.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        action = request.form.get("action", "submit_register")
        email = _normalise_email(request.form.get("email"))
        name = str(request.form.get("nama", "")).strip()
        password = request.form.get("password", "")
        if action == "send_otp":
            prior_pending_email = _normalise_email(session.get("pending_email"))
            if prior_pending_email and prior_pending_email != email:
                session.pop("pending_email", None)
            if not is_valid_email(email):
                flash("Format email tidak valid.", "danger")
            elif not name or len(name) > 100:
                flash("Nama wajib diisi dan maksimal 100 karakter.", "danger")
            elif len(password) < 8:
                flash("Kata sandi minimal 8 karakter.", "danger")
            else:
                with _connect_db() as con:
                    existing = con.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
                if existing:
                    flash("Email ini sudah terdaftar. Silakan masuk.", "danger")
                else:
                    with _connect_db() as con:
                        previous = con.execute(
                            "SELECT created_at FROM pending_registrations WHERE email = ?", (email,)
                        ).fetchone()
                    if previous and time.time() - previous["created_at"] < 60:
                        flash("Tunggu setidaknya 60 detik sebelum meminta OTP baru untuk email ini.", "info")
                    else:
                        now = time.time()
                        code = f"{secrets.randbelow(1_000_000):06d}"
                        with _connect_db() as con:
                            con.execute(
                                "INSERT OR REPLACE INTO pending_registrations "
                                "(email, name, password_hash, otp_hash, expires_at, attempts, created_at) VALUES (?, ?, ?, ?, ?, 0, ?)",
                                (email, name, generate_password_hash(password), generate_password_hash(code), now + OTP_TTL_SECONDS, now),
                            )
                        try:
                            _send_email(
                                "Kode Verifikasi OTP - UNeed Malang", email,
                                f"Halo {name},\n\nKode OTP pendaftaran UNeed Malang Anda adalah {code}. Kode berlaku selama 10 menit. Jangan bagikan kode ini kepada siapa pun.\n",
                            )
                            session["pending_email"] = email
                            flash("Kode OTP telah dikirim ke alamat email Anda. Kode berlaku 10 menit.", "info")
                        except Exception:
                            app.logger.warning("Pengiriman OTP gagal untuk alamat yang diminta.", exc_info=True)
                            with _connect_db() as con:
                                con.execute("DELETE FROM pending_registrations WHERE email = ?", (email,))
                            flash("OTP belum dapat dikirim. Periksa konfigurasi SMTP, lalu coba kembali.", "danger")
        elif action == "submit_register":
            if not is_valid_email(email):
                flash("Format email tidak valid.", "danger")
            elif email != _normalise_email(session.get("pending_email")):
                flash("Minta OTP untuk email ini terlebih dahulu.", "danger")
            elif _finish_registration(email, request.form.get("otp", ""), password):
                return redirect(url_for("login"))
        return render_template("register.html", form_values={"nama": name, "email": email})
    return render_template("register.html", form_values={"nama": "", "email": session.get("pending_email", "")})


@app.route("/verify-otp", methods=["GET", "POST"])
def otp_verification():
    email = _normalise_email(session.get("pending_email"))
    if not email:
        flash("Mulai pendaftaran dan minta OTP terlebih dahulu.", "info")
        return redirect(url_for("register"))
    if request.method == "POST":
        if _finish_registration(email, request.form.get("otp", "")):
            return redirect(url_for("login"))
    return render_template("verify_otp.html", email=email)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = _normalise_email(request.form.get("email"))
        if is_valid_email(email):
            with _connect_db() as con:
                user = con.execute("SELECT email FROM users WHERE email = ?", (email,)).fetchone()
            # Keep the response identical whether an account exists or not.
            if user:
                token = secrets.token_urlsafe(32)
                with _connect_db() as con:
                    con.execute(
                        "INSERT OR REPLACE INTO password_resets (email, token_hash, expires_at) VALUES (?, ?, ?)",
                        (email, _hash_token(token), time.time() + RESET_TTL_SECONDS),
                    )
                try:
                    reset_url = url_for("reset_password", token=token, _external=True)
                    _send_email(
                        "Reset Kata Sandi - UNeed Malang", email,
                        f"Permintaan reset kata sandi diterima. Buka tautan berikut dalam waktu satu jam:\n\n{reset_url}\n\nJika Anda tidak meminta reset, abaikan email ini.",
                    )
                except Exception:
                    app.logger.warning("Pengiriman instruksi reset kata sandi gagal.", exc_info=True)
                    with _connect_db() as con:
                        con.execute("DELETE FROM password_resets WHERE email = ?", (email,))
            flash("Jika email tersebut terdaftar, instruksi reset kata sandi akan dikirimkan.", "info")
            return redirect(url_for("login"))
        flash("Masukkan alamat email dengan format yang valid.", "danger")
    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    token_hash = _hash_token(token)
    with _connect_db() as con:
        reset = con.execute("SELECT * FROM password_resets WHERE token_hash = ?", (token_hash,)).fetchone()
    if not reset or reset["expires_at"] < time.time():
        if reset:
            with _connect_db() as con:
                con.execute("DELETE FROM password_resets WHERE email = ?", (reset["email"],))
        flash("Tautan reset tidak valid atau sudah kedaluwarsa.", "danger")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(password) < 8:
            flash("Kata sandi minimal 8 karakter.", "danger")
        elif password != confirm:
            flash("Konfirmasi kata sandi belum sama.", "danger")
        else:
            with _connect_db() as con:
                con.execute("UPDATE users SET password_hash = ? WHERE email = ?", (generate_password_hash(password), reset["email"]))
                con.execute("DELETE FROM password_resets WHERE email = ?", (reset["email"],))
            flash("Kata sandi berhasil diubah. Silakan masuk.", "success")
            return redirect(url_for("login"))
    return render_template("reset_password.html")


@app.route("/api/location-dss", methods=["POST"])
@login_required
def location_dss():
    """Recalculate distance and SAW scores for a one-off device location.

    The coordinates are read from the JSON body only for this calculation. They
    are not copied to session, database, URL, or page-access logs. Responses are
    explicitly marked no-store to avoid caching position-derived results.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Koordinat lokasi tidak valid."}), 400
    origin = valid_lat_lng(payload.get("lat"), payload.get("lng"))
    if origin is None:
        return jsonify({"error": "Koordinat lokasi tidak valid."}), 400

    config = get_dss_config()
    items: list[dict[str, Any]] = []
    for category_key in CATEGORIES:
        try:
            records, _ = build_category_records(category_key, config, origin=origin)
        except Exception:
            app.logger.exception("Gagal menghitung DSS berbasis lokasi untuk kategori %s.", category_key)
            continue
        for row in records:
            distance = row.get("_dist")
            score = row.get("_dss_score")
            items.append({
                "category": category_key,
                "record_id": row.get("_record_id"),
                "distance_km": round(float(distance), 6) if distance is not None and math.isfinite(float(distance)) else None,
                "dss_score": round(float(score), 6) if score is not None and math.isfinite(float(score)) else None,
            })
    response = jsonify({"items": items, "distance_type": "haversine_straight_line"})
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/dashboard")
@login_required
def dashboard():
    """Show one combined recommendation feed for the general public."""
    query = str(request.args.get("q", "")).strip()[:120]
    config = get_dss_config()
    combined: list[dict[str, Any]] = []
    for cat_key, cat_title in CATEGORIES.items():
        try:
            records, _ = build_category_records(cat_key, config)
        except Exception:
            app.logger.exception("Dataset kategori %s gagal dibaca.", cat_key)
            records = []
        if query:
            needle = query.casefold()
            records = [r for r in records if needle in " ".join(str(v) for k, v in r.items() if not k.startswith("_")).casefold()]
        for record in records:
            record["_category_key"] = cat_key
            record["_category_title"] = cat_title
            record["_category_icon"] = CATEGORY_ICONS[cat_key]
            combined.append(record)
    combined.sort(key=lambda row: (
        row.get("_dss_score") is not None,
        row.get("_dss_score") if row.get("_dss_score") is not None else -1,
        row.get("_rating") if row.get("_rating") is not None else -1,
        row.get("_name", "").casefold(),
    ), reverse=True)
    search_result_count = len(combined)
    # Dashboard remains concise; users can open any complete category from the hamburger menu.
    limit = 12 if query else 6
    return render_template(
        "dashboard.html", recommendations=combined[:limit], search_query=query,
        search_result_count=search_result_count,
    )


@app.route("/category")
@login_required
def category():
    return redirect(url_for("category_detail", cat_name="kos"))


def _kos_options(records: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Build filter options only from values that actually occur in named listings."""
    types = sorted({row["_type"] for row in records if row.get("_type")}, key=lambda v: ["Putra", "Putri", "Campur"].index(v) if v in {"Putra", "Putri", "Campur"} else 99)
    areas = sorted({row["_area"] for row in records if row.get("_area")}, key=str.casefold)
    facilities = sorted({facility for row in records for facility in row.get("_facilities", [])}, key=str.casefold)
    availability = sorted({row["_availability"] for row in records if row.get("_availability")}, key=str.casefold)
    return {"types": types, "areas": areas, "facilities": facilities, "availability": availability}


def _request_choice(name: str, options: list[str]) -> str:
    value = str(request.args.get(name, "")).strip()[:120]
    match = next((option for option in options if option.casefold() == value.casefold()), None)
    return match or ""


@app.route("/category/<cat_name>")
@login_required
def category_detail(cat_name: str):
    cat_key = str(cat_name or "").lower().strip()
    if cat_key not in CATEGORIES:
        flash("Kategori tidak ditemukan.", "danger")
        return redirect(url_for("dashboard"))

    query = str(request.args.get("q", "")).strip()[:120]
    requested_sort = str(request.args.get("sort", "")).strip()
    allowed_sorts = {"", "rating", "harga_asc", "harga_desc", "jarak", "dss"}
    sort = requested_sort if requested_sort in allowed_sorts else ""
    if cat_key == "kos" and not sort:
        # Kos pages use the real DSS score as their default ranking, not a made-up score.
        sort = "dss"
    min_harga = to_int(request.args.get("min_harga"))
    max_harga = to_int(request.args.get("max_harga"))
    view = "table" if request.args.get("view") == "table" else "cards"
    data: list[dict[str, Any]] = []
    headers: list[str] = []
    total_all = 0
    raw_record_count = 0
    hidden_incomplete = 0
    load_error = False
    options = {"types": [], "areas": [], "facilities": [], "availability": []}
    source_missing = _category_file(cat_key)[0] is None
    type_counts: dict[str, int] = {}
    selected_type = selected_area = selected_facility = selected_availability = ""

    try:
        records, headers = build_category_records(cat_key, get_dss_config())
        if source_missing and not records:
            raise FileNotFoundError(f"Dataset kategori '{cat_key}' tidak ditemukan dan tidak ada pengajuan yang disetujui.")
        raw_record_count = len(records)
        if cat_key == "kos":
            hidden_incomplete = sum(1 for row in records if not row.get("_has_name"))
            # Rows without a name stay in the source CSV but are not presented as distinct properties.
            records = [row for row in records if row.get("_has_name")]
            options = _kos_options(records)
            type_counts = {kind: sum(1 for row in records if row.get("_type") == kind) for kind in options["types"]}
            selected_type = _request_choice("tipe", options["types"])
            selected_area = _request_choice("area", options["areas"])
            selected_facility = _request_choice("fasilitas", options["facilities"])
            selected_availability = _request_choice("ketersediaan", options["availability"])

        total_all = len(records)
        if query:
            needle = query.casefold()
            data = [row for row in records if needle in " ".join(str(v) for k, v in row.items() if not k.startswith("_")).casefold()]
        else:
            data = list(records)

        if min_harga is not None and max_harga is not None and min_harga > max_harga:
            flash("Harga minimum tidak boleh melebihi harga maksimum. Filter harga direset.", "danger")
            min_harga = max_harga = None
        if min_harga is not None or max_harga is not None:
            def price_overlaps(row: dict[str, Any]) -> bool:
                if row["_pmin"] is None or row["_pmax"] is None:
                    return False
                if min_harga is not None and row["_pmax"] < min_harga:
                    return False
                if max_harga is not None and row["_pmin"] > max_harga:
                    return False
                return True
            data = [row for row in data if price_overlaps(row)]

        if cat_key == "kos":
            if selected_type:
                data = [row for row in data if row.get("_type") == selected_type]
            if selected_area:
                data = [row for row in data if row.get("_area", "").casefold() == selected_area.casefold()]
            if selected_facility:
                data = [row for row in data if any(selected_facility.casefold() in facility.casefold() for facility in row.get("_facilities", []))]
            if selected_availability:
                data = [row for row in data if row.get("_availability") == selected_availability]

        if sort == "rating":
            data = sort_none_last(data, "_rating", reverse=True)
        elif sort == "harga_asc":
            data = sort_none_last(data, "_pmin")
        elif sort == "harga_desc":
            data = sort_none_last(data, "_pmax", reverse=True)
        elif sort == "jarak":
            data = sort_none_last(data, "_dist")
        elif sort == "dss":
            data = sort_none_last(data, "_dss_score", reverse=True)
    except Exception:
        load_error = True
        data = []
        total_all = 0
        app.logger.exception("Gagal memproses dataset kategori %s.", cat_key)
        flash("Data kategori belum dapat dibaca. Periksa format file CSV/Excel.", "danger")

    filter_values_active = bool(
        query or min_harga is not None or max_harga is not None or selected_type
        or selected_area or selected_facility or selected_availability
        or (cat_key != "kos" and sort)
    )
    active_filter_count = sum(bool(value) for value in (query, selected_type, selected_area, selected_facility, selected_availability))
    active_filter_count += int(min_harga is not None or max_harga is not None)
    if load_error:
        dataset_state = "error"
    elif raw_record_count == 0:
        dataset_state = "empty"
    elif cat_key == "kos" and total_all == 0 and hidden_incomplete:
        dataset_state = "unidentified"
    elif not data:
        dataset_state = "no_results"
    else:
        dataset_state = "results"

    sort_labels = {
        "dss": "Rekomendasi UNeed (DSS)", "rating": "Rating tertinggi",
        "harga_asc": "Harga terendah", "harga_desc": "Harga tertinggi",
        "jarak": "Jarak garis lurus terdekat", "": "Urutan data",
    }
    if cat_key == "kos":
        return render_template(
            "kos.html", cat_key=cat_key, cat_title=CATEGORIES[cat_key], categories=CATEGORIES,
            category_icons=CATEGORY_ICONS, headers=headers, data=data, total_records=len(data),
            total_all=total_all, raw_record_count=raw_record_count, hidden_incomplete=hidden_incomplete,
            sort=sort, sort_label=sort_labels.get(sort, "Rekomendasi UNeed (DSS)"),
            min_harga=min_harga, max_harga=max_harga, view=view,
            active_filter=filter_values_active, active_filter_count=active_filter_count,
            search_query=query, location_anchor_name=LOCATION_ANCHOR_NAME,
            dataset_state=dataset_state, type_options=options["types"], area_options=options["areas"],
            facility_options=options["facilities"], availability_options=options["availability"],
            type_counts=type_counts, selected_type=selected_type, selected_area=selected_area,
            selected_facility=selected_facility, selected_availability=selected_availability,
        )
    return render_template(
        "category.html", cat_key=cat_key, cat_title=CATEGORIES[cat_key], categories=CATEGORIES,
        category_icons=CATEGORY_ICONS, headers=headers, data=data, total_records=len(data),
        total_all=total_all, sort=sort, min_harga=min_harga, max_harga=max_harga,
        view=view, active_filter=filter_values_active, search_query=query,
        location_anchor_name=LOCATION_ANCHOR_NAME,
    )


@app.route("/upload_excel/<cat_name>", methods=["POST"])
def upload_excel(cat_name: str):
    # Intentionally disabled: dataset replacement must be performed by an operator on disk.
    abort(404)


@app.route("/detail")
@login_required
def detail():
    cat_key = str(request.args.get("category", "")).strip().lower()
    item_id = to_int(request.args.get("item_id"))
    if cat_key not in CATEGORIES or item_id is None:
        return redirect(url_for("category"))
    try:
        records, _ = build_category_records(cat_key, get_dss_config())
        row = next((item for item in records if item["_record_id"] == item_id), None)
    except Exception:
        app.logger.exception("Gagal memuat detail kategori %s.", cat_key)
        row = None
    if row is None:
        flash("Data tempat tidak ditemukan.", "danger")
        return redirect(url_for("category_detail", cat_name=cat_key))
    if cat_key == "kos":
        if not row.get("_has_name"):
            flash("Data kos ini belum memiliki nama yang cukup untuk ditampilkan sebagai listing.", "info")
            return redirect(url_for("category_detail", cat_name=cat_key))
        return render_template("kos_detail.html", item=row, cat_key=cat_key, cat_title=CATEGORIES[cat_key])
    return render_template("detail.html", item=row, cat_key=cat_key, cat_title=CATEGORIES[cat_key])


@app.route("/detail/<cat_name>/<int:item_id>")
@login_required
def detail_path(cat_name: str, item_id: int):
    """Render detail records for links that use a category and record ID in the path."""
    cat_key = str(cat_name or "").strip().lower()
    if cat_key not in CATEGORIES:
        abort(404)
    try:
        records, _ = build_category_records(cat_key, get_dss_config())
        row = next((item for item in records if item["_record_id"] == item_id), None)
    except Exception:
        app.logger.exception("Gagal memuat detail kategori %s.", cat_key)
        abort(404)
    if row is None:
        abort(404)
    if cat_key == "kos":
        if not row.get("_has_name"):
            abort(404)
        return render_template("kos_detail.html", item=row, cat_key=cat_key, cat_title=CATEGORIES[cat_key])
    return render_template("detail.html", item=row, cat_key=cat_key, cat_title=CATEGORIES[cat_key])


@app.route("/profile")
@login_required
def profile():
    return render_template("profile.html")


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        theme = request.form.get("theme", "light")
        font_size = request.form.get("font_size", "medium")
        if theme not in {"light", "dark", "indigo", "emerald", "rose"}:
            theme = "light"
        if font_size not in {"small", "medium", "large"}:
            font_size = "medium"
        user_id = session.get("user_id")
        with _connect_db() as con:
            user = con.execute("SELECT preferences_json FROM users WHERE id = ?", (user_id,)).fetchone()
            try:
                preferences = json.loads(user["preferences_json"] or "{}") if user else {}
            except (json.JSONDecodeError, TypeError):
                preferences = {}
            preferences.update({"theme": theme, "font_size": font_size})
            con.execute("UPDATE users SET preferences_json = ? WHERE id = ?", (json.dumps(preferences), user_id))
        session["theme"] = theme
        session["font_size"] = font_size
        flash("Pengaturan berhasil disimpan.", "success")
        return redirect(url_for("settings"))
    return render_template("settings.html")


@app.route("/delete-account", methods=["POST"])
@login_required
def delete_account():
    user_id = session.get("user_id")
    password_confirm = request.form.get("password_confirm", "")
    with _connect_db() as con:
        user = con.execute("SELECT email, password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user or not password_confirm or not check_password_hash(user["password_hash"], password_confirm):
        flash("Kata sandi saat ini diperlukan untuk menghapus akun.", "danger")
        return redirect(url_for("profile"))
    with _connect_db() as con:
        con.execute("DELETE FROM password_resets WHERE email = ?", (user["email"],))
        con.execute("DELETE FROM pending_registrations WHERE email = ?", (user["email"],))
        con.execute("DELETE FROM login_attempts WHERE email = ?", (user["email"],))
        con.execute("DELETE FROM access_logs WHERE user_id = ?", (user_id,))
        # Preserve approved public listings, but remove the deleted account's identity.
        con.execute("DELETE FROM user_submissions WHERE submitter_id = ? AND status = 'pending'", (user_id,))
        con.execute(
            """UPDATE user_submissions
               SET submitter_id = NULL, submitter_name = 'Akun dihapus', submitter_email = 'deleted-account@local.invalid'
               WHERE submitter_id = ?""",
            (user_id,),
        )
        con.execute("DELETE FROM users WHERE id = ?", (user_id,))
    session.clear()
    flash("Akun dan riwayat akses telah dihapus. Listing yang sudah disetujui tetap tayang tanpa identitas pengaju.", "info")
    return redirect(url_for("login"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    debug = os.environ.get("UNEED_DEBUG", "false").lower() in {"1", "true", "yes"}
    app.run(host=os.environ.get("UNEED_HOST", "127.0.0.1"), port=int(os.environ.get("UNEED_PORT", "5000")), debug=debug)
