"""Pemuatan dan normalisasi dataset CSV/Excel UNeed Malang.

Modul ini dipakai bersama oleh ``website_user`` dan ``website_dev`` agar kedua
aplikasi membaca dataset dengan cara yang sama. Semua fungsi parsing bersifat
defensif: nilai kosong, format aneh, atau file rusak tidak pernah menghentikan
aplikasi, melainkan dicatat sebagai ``issues`` pada hasil pemuatan.
"""
from __future__ import annotations

import csv
import math
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT_DIR / "website_user" / "uploads_excel"

# Kunci kategori = nama file dataset (kos.csv, warung.csv, ...)
CATEGORIES: Dict[str, str] = {
    "kos": "Kos & Kontrakan",
    "warung": "Tempat Makan & Warung",
    "cafe": "Café & Working Space",
    "klinik": "Layanan Kesehatan",
    "atk": "ATK & Percetakan",
    "mal": "Pusat Perbelanjaan & Mall",
}
CATEGORY_ICONS: Dict[str, str] = {
    "kos": "🏠", "warung": "🍲", "cafe": "☕", "klinik": "🏥", "atk": "🖨️", "mal": "🛍️",
}
# Nama pendek untuk tab/menu
CATEGORY_SHORT: Dict[str, str] = {
    "kos": "Kos & Kontrakan", "warung": "Tempat Makan", "cafe": "Café",
    "klinik": "Kesehatan", "atk": "ATK", "mal": "Pusat Perbelanjaan",
}
# Alamat lama (dipakai sidebar versi sebelumnya) -> kunci kategori resmi
CATEGORY_ALIASES: Dict[str, str] = {"makan": "warung", "kesehatan": "klinik"}

NAME_COLUMNS = ("Nama Tempat", "Nama Kos", "Nama Toko", "Nama Klinik")
PRICE_COLUMNS = ("Harga per Bulan (Rp)", "Range Harga (Rp)")
BADGE_COLUMNS = ("Tipe (Putra/Putri/Campur)", "Kategori Makanan", "Jenis Produk", "Jenis Layanan")
REVIEW_COLUMNS = ("Jumlah Ulasan", "Jumlah Review", "Jumlah Rating", "Ulasan", "Reviews")
COORD_COLUMN = "Koordinat (Lat, Long)"
RATING_COLUMN = "Rating Maps"
MAPS_COLUMN = "Link Google Maps"
ADDRESS_COLUMN = "Alamat"
ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")
SUPPORTED_EXTENSIONS = ("csv", "xlsx", "xls")


def data_dir() -> Path:
    """Folder dataset; dapat diganti lewat variabel lingkungan UNEED_DATA_DIR."""
    return Path(os.environ.get("UNEED_DATA_DIR") or DEFAULT_DATA_DIR)


# --------------------------------------------------------------------------
# Parsing nilai
# --------------------------------------------------------------------------
def _is_blank(val: Any) -> bool:
    if val is None:
        return True
    try:
        if isinstance(val, float) and math.isnan(val):
            return True
    except TypeError:
        pass
    return str(val).strip() in ("", "-", "–", "—", "nan", "NaN", "None")


def _to_number(token: str) -> Optional[float]:
    """Ubah '1.200.000', '1,5', '25' menjadi float; None bila tidak valid."""
    token = token.strip()
    if not token:
        return None
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", token):      # pemisah ribuan
        token = re.sub(r"[.,]", "", token)
    else:
        token = token.replace(",", ".")
    try:
        return float(token)
    except ValueError:
        return None


def parse_price(val: Any) -> Optional[Tuple[float, float]]:
    """'25-50K' -> (25000, 50000); 'Rp 800.000' -> (800000, 800000); '-' -> None.

    Harga <= 0 atau tanpa satuan yang jelas (mis. '25-50') dianggap tidak valid
    agar tidak menebak satuan.
    """
    if _is_blank(val):
        return None
    s = str(val).strip().lower()
    s = re.sub(r"rp\.?", "", s).strip()
    if re.match(r"^-\s*\d", s):      # angka negatif
        return None
    mult = 1.0
    if re.search(r"(jt|juta)", s):
        mult = 1_000_000.0
    elif re.search(r"(\d\s*k\b|\d\s*rb\b|ribu)", s):
        mult = 1_000.0
    tokens = re.findall(r"\d+(?:[.,]\d+)*", s)
    nums = [_to_number(t) for t in tokens]
    nums = [n * mult for n in nums if n is not None]
    nums = [n for n in nums if n > 0 and math.isfinite(n)]
    if not nums:
        return None
    lo, hi = min(nums), max(nums)
    if hi < 1000:        # tanpa satuan & terlalu kecil untuk rupiah
        return None
    return lo, hi


def parse_rating(val: Any) -> Optional[float]:
    """'4,3' / '4.3' -> 4.3. Di luar rentang 0 < r <= 5 dianggap tidak valid."""
    if _is_blank(val):
        return None
    try:
        r = float(str(val).strip().replace(",", "."))
    except ValueError:
        return None
    return r if (math.isfinite(r) and 0 < r <= 5) else None


def parse_review_count(val: Any) -> Optional[int]:
    if _is_blank(val):
        return None
    s = str(val).strip().lower().replace(" ", "")
    mult = 1000 if re.search(r"\d(k|rb)$", s) else 1
    s = re.sub(r"(k|rb)$", "", s)
    n = _to_number(s)
    if n is None or n < 0 or not math.isfinite(n):
        return None
    return int(round(n * mult))


def parse_coords(val: Any) -> Optional[Tuple[float, float]]:
    """'-7.95, 112.61' -> (-7.95, 112.61). Menolak koordinat di luar Indonesia."""
    if _is_blank(val):
        return None
    parts = str(val).split(",")
    if len(parts) != 2:
        return None
    try:
        lat, lng = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (math.isfinite(lat) and math.isfinite(lng)):
        return None
    if not (-11.5 <= lat <= 6.5 and 94.0 <= lng <= 141.5):
        return None
    return lat, lng


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def safe_url(val: Any) -> Optional[str]:
    """Hanya izinkan tautan http(s); mencegah ``javascript:`` dari isi CSV."""
    if _is_blank(val):
        return None
    s = str(val).strip()
    return s if re.match(r"^https?://", s, re.IGNORECASE) else None


def clean_phone(val: Any) -> Optional[str]:
    """Rapikan nomor WA; bila isinya ternyata koordinat atau bukan nomor -> None."""
    if _is_blank(val):
        return None
    s = str(val).strip()
    if re.fullmatch(r"\d+\.0", s):          # sisa konversi float
        s = s[:-2]
    digits = re.sub(r"[\s\-+()]", "", s)
    if not re.fullmatch(r"\d{8,15}", digits):
        return None
    if digits.startswith("62"):
        return digits
    return digits if digits.startswith("0") else "0" + digits


# --------------------------------------------------------------------------
# Pembacaan berkas
# --------------------------------------------------------------------------
def find_dataset_file(cat_key: str) -> Optional[Path]:
    for ext in SUPPORTED_EXTENSIONS:
        p = data_dir() / f"{cat_key}.{ext}"
        if p.is_file():
            return p
    return None


def _detect_delimiter(text_sample: str) -> str:
    try:
        return csv.Sniffer().sniff(text_sample, delimiters=",;\t").delimiter
    except csv.Error:
        first = text_sample.splitlines()[0] if text_sample else ""
        return ";" if first.count(";") > first.count(",") else ","


def read_table(path: Path) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Baca CSV/Excel sebagai teks. Mencoba beberapa encoding & delimiter."""
    meta: Dict[str, Any] = {"file": path.name}
    if path.suffix.lower() == ".csv":
        last_err: Optional[Exception] = None
        for enc in ENCODINGS:
            try:
                with open(path, "r", encoding=enc, newline="") as fh:
                    sample = fh.read(4096)
                delim = _detect_delimiter(sample)
                df = pd.read_csv(path, encoding=enc, sep=delim, dtype=str,
                                 keep_default_na=False, skip_blank_lines=True)
                meta.update(encoding=enc, delimiter=delim)
                return df, meta
            except UnicodeDecodeError as exc:
                last_err = exc
            except pd.errors.EmptyDataError:
                return pd.DataFrame(), {**meta, "encoding": enc, "delimiter": ","}
        raise ValueError(f"Encoding file tidak dikenali ({last_err})")
    df = pd.read_excel(path, dtype=str, keep_default_na=False)
    meta.update(encoding="excel", delimiter="-")
    return df, meta


# --------------------------------------------------------------------------
# Hasil pemuatan
# --------------------------------------------------------------------------
@dataclass
class LoadResult:
    key: str
    title: str
    rows: List[Dict[str, Any]] = field(default_factory=list)
    headers: List[str] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    error: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    skipped_blank: int = 0

    @property
    def total(self) -> int:
        return len(self.rows)

    def completeness(self) -> Dict[str, int]:
        """Jumlah baris yang punya nilai valid untuk tiap kriteria DSS."""
        return {
            "harga": sum(1 for r in self.rows if r["price"] is not None),
            "rating": sum(1 for r in self.rows if r["rating"] is not None),
            "koordinat": sum(1 for r in self.rows if r["lat"] is not None),
            "ulasan": sum(1 for r in self.rows if r["reviews"] is not None),
        }


_cache: Dict[Tuple[str, float, str], LoadResult] = {}
_cache_lock = threading.Lock()


def _build_rows(df: pd.DataFrame, res: LoadResult) -> None:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    res.headers = df.columns.tolist()
    name_col = next((c for c in NAME_COLUMNS if c in df.columns), None)
    if name_col is None:
        name_col = next((c for c in df.columns if c.lower().startswith("nama")), None)
    if name_col is None:
        raise ValueError("Kolom nama (mis. 'Nama Tempat') tidak ditemukan pada header")
    price_col = next((c for c in PRICE_COLUMNS if c in df.columns), None)
    review_col = next((c for c in REVIEW_COLUMNS if c in df.columns), None)
    badge_cols = [c for c in BADGE_COLUMNS if c in df.columns]

    bad = {"harga": 0, "rating": 0, "koordinat": 0}
    for _, rec in df.iterrows():
        raw = {h: (None if _is_blank(rec[h]) else str(rec[h]).strip()) for h in res.headers}
        no_raw = raw.get("No")
        try:
            item_id = int(float(no_raw)) if no_raw is not None else None
        except ValueError:
            item_id = None
        if item_id is None:            # baris 'Total terdata', catatan, dsb.
            continue
        content = [v for k, v in raw.items() if k != "No" and v is not None]
        if not content:                # baris kerangka kosong
            res.skipped_blank += 1
            continue

        price = parse_price(raw.get(price_col)) if price_col else None
        if price_col and raw.get(price_col) is not None and price is None:
            bad["harga"] += 1
        rating = parse_rating(raw.get(RATING_COLUMN))
        if raw.get(RATING_COLUMN) is not None and rating is None:
            bad["rating"] += 1
        coords = parse_coords(raw.get(COORD_COLUMN))
        if raw.get(COORD_COLUMN) is not None and coords is None:
            bad["koordinat"] += 1
        badge = next((raw[c] for c in badge_cols if raw.get(c)), None)

        res.rows.append({
            "id": item_id,
            "cat": res.key,
            "name": raw.get(name_col) or f"Tanpa nama (baris {item_id})",
            "has_name": raw.get(name_col) is not None,
            "raw": raw,
            "address": raw.get(ADDRESS_COLUMN),
            "badge": badge,
            "price_text": raw.get(price_col) if price_col else None,
            "pmin": price[0] if price else None,
            "pmax": price[1] if price else None,
            "price": (price[0] + price[1]) / 2 if price else None,   # titik tengah
            "rating": rating,
            "reviews": parse_review_count(raw.get(review_col)) if review_col else None,
            "lat": coords[0] if coords else None,
            "lng": coords[1] if coords else None,
            "maps_url": safe_url(raw.get(MAPS_COLUMN)),
            "menu_url": safe_url(raw.get("Link Menu")),
            "phone": clean_phone(raw.get("Kontak/WA")),
            "dist": None,            # diisi oleh with_distance()
        })
    for label, n in bad.items():
        if n:
            res.issues.append(f"{n} baris memiliki nilai {label} yang tidak valid dan diabaikan pada perhitungan")
    if res.skipped_blank:
        res.issues.append(f"{res.skipped_blank} baris kerangka kosong dilewati")
    no_name = sum(1 for r in res.rows if not r["has_name"])
    if no_name:
        res.issues.append(f"{no_name} baris tidak memiliki nama")
    ids = [r["id"] for r in res.rows]
    if len(ids) != len(set(ids)):
        res.issues.append("Terdapat nomor (kolom 'No') ganda; halaman detail memakai nomor pertama")


def load_category(cat_key: str) -> LoadResult:
    """Muat satu kategori. Tidak pernah melempar exception; lihat ``result.error``."""
    title = CATEGORIES.get(cat_key, cat_key)
    path = find_dataset_file(cat_key)
    if path is None:
        return LoadResult(cat_key, title, error=f"File data {cat_key}.csv belum tersedia di folder dataset")
    sig = (str(path), path.stat().st_mtime, str(path.stat().st_size))
    with _cache_lock:
        hit = _cache.get(sig)
    if hit is not None:
        return hit
    res = LoadResult(cat_key, title)
    try:
        df, meta = read_table(path)
        res.meta = meta
        if df.empty:
            res.issues.append("Dataset tidak memiliki baris data")
        else:
            _build_rows(df, res)
            if not res.rows:
                res.issues.append("Dataset tidak memiliki baris data yang terisi")
    except Exception as exc:  # noqa: BLE001 - dataset rusak tidak boleh menjatuhkan aplikasi
        res.rows, res.error = [], f"Gagal membaca {path.name}: {exc}"
    with _cache_lock:
        _cache.clear() if len(_cache) > 50 else None
        _cache[sig] = res
    return res


def with_distance(rows: List[Dict[str, Any]], lat: float, lng: float) -> List[Dict[str, Any]]:
    """Salin baris + jarak (km) ke titik acuan tanpa mengubah hasil cache."""
    out = []
    for r in rows:
        c = dict(r)
        c["dist"] = haversine_km(lat, lng, r["lat"], r["lng"]) if r["lat"] is not None else None
        out.append(c)
    return out


def get_item(cat_key: str, item_id: int) -> Optional[Dict[str, Any]]:
    for r in load_category(cat_key).rows:
        if r["id"] == item_id:
            return r
    return None
