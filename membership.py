"""Configurable UNeed membership and contribution settings.

No subscription price or Premium benefit is assumed by default. Administrators
may configure both explicitly; payment integration remains outside the MVP.
"""
from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(os.environ.get("UNEED_MEMBERSHIP_CONFIG") or PROJECT_ROOT / "instance" / "membership_config.json")
DEFAULT_MEMBERSHIP_CONFIG: dict[str, Any] = {
    "tiers": {
        "basic": {
            "label": "Basic Member",
            "price": None,
            "benefits": [
                "Menjelajahi katalog lima kategori tempat.",
                "Melihat rekomendasi publik dan skor DSS yang tersedia.",
                "Membuat rekomendasi komunitas dan berpartisipasi dalam moderasi komunitas.",
                "Mengakses tautan peta dan petunjuk arah untuk data lokasi yang valid.",
            ],
        },
        "premium": {
            "label": "Premium Member",
            "price": None,
            "benefits": [],
        },
    },
    "contribution": {
        "approved_recommendation_points": 10,
        "helpful_vote_points": 1,
        "period": "monthly",
        "badges": [
            {"key": "contributor", "label": "Kontributor", "threshold": 1},
            {"key": "trusted", "label": "Kontributor Aktif", "threshold": 5},
            {"key": "community_guide", "label": "Pemandu Komunitas", "threshold": 15},
        ],
    },
}


def load_membership_config() -> dict[str, Any]:
    config = deepcopy(DEFAULT_MEMBERSHIP_CONFIG)
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return config
        tiers = payload.get("tiers", {})
        if isinstance(tiers, dict):
            for key in ("basic", "premium"):
                tier = tiers.get(key)
                if not isinstance(tier, dict):
                    continue
                label = str(tier.get("label", config["tiers"][key]["label"])).strip()
                if label and len(label) <= 80:
                    config["tiers"][key]["label"] = label
                price = tier.get("price")
                if price is None or str(price).strip() == "":
                    config["tiers"][key]["price"] = None
                else:
                    price_text = str(price).strip()[:80]
                    config["tiers"][key]["price"] = price_text
                benefits = tier.get("benefits", [])
                if isinstance(benefits, list):
                    config["tiers"][key]["benefits"] = [str(x).strip()[:240] for x in benefits if str(x).strip()][:20]
        contribution = payload.get("contribution", {})
        if isinstance(contribution, dict):
            period = str(contribution.get("period", config["contribution"]["period"])).strip().lower()
            if period in {"weekly", "monthly", "all_time"}:
                config["contribution"]["period"] = period
            for key in ("approved_recommendation_points", "helpful_vote_points"):
                try:
                    points = int(contribution.get(key, config["contribution"][key]))
                except (TypeError, ValueError, OverflowError):
                    continue
                if 0 <= points <= 1000:
                    config["contribution"][key] = points
            badges = contribution.get("badges")
            if isinstance(badges, list):
                valid = []
                seen = set()
                for badge in badges[:30]:
                    if not isinstance(badge, dict):
                        continue
                    key = str(badge.get("key", "")).strip().lower()
                    label = str(badge.get("label", "")).strip()
                    try:
                        threshold = int(badge.get("threshold", 0))
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if key and key not in seen and len(key) <= 40 and label and len(label) <= 80 and 1 <= threshold <= 100000:
                        valid.append({"key": key, "label": label, "threshold": threshold})
                        seen.add(key)
                config["contribution"]["badges"] = sorted(valid, key=lambda row: row["threshold"])
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return config
    return config


def save_membership_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a submitted membership configuration before atomic persistence."""
    config = deepcopy(DEFAULT_MEMBERSHIP_CONFIG)
    if not isinstance(payload, dict):
        raise ValueError("Konfigurasi membership tidak valid.")
    tiers = payload.get("tiers", {})
    if not isinstance(tiers, dict):
        raise ValueError("Struktur tier membership tidak valid.")
    for key in ("basic", "premium"):
        current = tiers.get(key, {})
        if not isinstance(current, dict):
            raise ValueError(f"Tier {key} tidak valid.")
        label = str(current.get("label", "")).strip()
        if not label or len(label) > 80:
            raise ValueError(f"Nama tier {key} wajib diisi (maksimal 80 karakter).")
        price = str(current.get("price", "")).strip()
        if len(price) > 80:
            raise ValueError("Deskripsi harga terlalu panjang.")
        benefits_raw = current.get("benefits", [])
        if isinstance(benefits_raw, str):
            benefits = [line.strip() for line in benefits_raw.splitlines() if line.strip()]
        elif isinstance(benefits_raw, list):
            benefits = [str(line).strip() for line in benefits_raw if str(line).strip()]
        else:
            raise ValueError(f"Format manfaat tier {key} tidak valid.")
        if len(benefits) > 20 or any(len(line) > 240 for line in benefits):
            raise ValueError("Maksimal 20 manfaat per tier; setiap manfaat maksimal 240 karakter.")
        config["tiers"][key] = {"label": label, "price": price or None, "benefits": benefits}
    contribution = payload.get("contribution", {})
    if not isinstance(contribution, dict):
        raise ValueError("Konfigurasi poin tidak valid.")
    for key in ("approved_recommendation_points", "helpful_vote_points"):
        try:
            value = int(contribution.get(key))
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"Nilai {key} harus berupa bilangan bulat.")
        if not 0 <= value <= 1000:
            raise ValueError(f"Nilai {key} harus berada pada rentang 0–1000.")
        config["contribution"][key] = value
    period = str(contribution.get("period", "monthly")).strip().lower()
    if period not in {"weekly", "monthly", "all_time"}:
        raise ValueError("Periode reward harus mingguan, bulanan, atau sepanjang waktu.")
    config["contribution"]["period"] = period
    current_badges = load_membership_config()["contribution"]["badges"]
    badges_raw = contribution.get("badges")
    if badges_raw is None:
        config["contribution"]["badges"] = current_badges
    else:
        if isinstance(badges_raw, str):
            parsed_badges = []
            for line in badges_raw.splitlines():
                if not line.strip():
                    continue
                pieces = [piece.strip() for piece in line.split("|", 2)]
                if len(pieces) != 3:
                    raise ValueError("Format badge harus: key | nama badge | ambang poin.")
                parsed_badges.append({"key": pieces[0], "label": pieces[1], "threshold": pieces[2]})
            badges_raw = parsed_badges
        if not isinstance(badges_raw, list) or len(badges_raw) > 30:
            raise ValueError("Daftar badge harus berupa daftar dengan maksimal 30 entri.")
        valid_badges, seen = [], set()
        for item in badges_raw:
            if not isinstance(item, dict):
                raise ValueError("Format badge tidak valid.")
            key = str(item.get("key", "")).strip().lower()
            label = str(item.get("label", "")).strip()
            try:
                threshold = int(item.get("threshold", 0))
            except (TypeError, ValueError, OverflowError):
                raise ValueError("Ambang poin badge harus bilangan bulat.")
            if not key or len(key) > 40 or key in seen or not label or len(label) > 80 or not 1 <= threshold <= 100000:
                raise ValueError("Pastikan key unik, nama badge terisi, dan ambang berada pada rentang 1–100000.")
            seen.add(key)
            valid_badges.append({"key": key, "label": label, "threshold": threshold})
        config["contribution"]["badges"] = sorted(valid_badges, key=lambda row: row["threshold"])
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(CONFIG_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, CONFIG_PATH)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
    return config
