"""Location and Google Maps helpers shared by the UNeed applications.

Coordinates are only processed in memory unless an explicit feature submits them.
The public geolocation feature intentionally keeps the user's device position in
browser memory and never writes it to the server-side access log or URL.
"""
from __future__ import annotations

import math
from urllib.parse import urlencode
from typing import Any

EARTH_RADIUS_KM = 6371.0088


def parse_coordinates(value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    try:
        pieces = [part.strip() for part in str(value).split(",")]
        if len(pieces) != 2:
            return None
        lat, lng = float(pieces[0]), float(pieces[1])
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lng):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    # (0, 0) can be a valid coordinate in general; it is not rejected here.
    return lat, lng


def valid_lat_lng(lat: Any, lng: Any) -> tuple[float, float] | None:
    try:
        latitude, longitude = float(lat), float(lng)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def haversine_km(lat1: Any, lng1: Any, lat2: Any, lng2: Any) -> float | None:
    origin = valid_lat_lng(lat1, lng1)
    destination = valid_lat_lng(lat2, lng2)
    if origin is None or destination is None:
        return None
    lat_a, lng_a = map(math.radians, origin)
    lat_b, lng_b = map(math.radians, destination)
    dlat, dlng = lat_b - lat_a, lng_b - lng_a
    h = math.sin(dlat / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin(dlng / 2) ** 2
    h = max(0.0, min(1.0, h))
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def google_maps_search_url(name: str = "", address: str = "", coords: tuple[float, float] | None = None) -> str | None:
    query = ""
    if coords is not None and valid_lat_lng(*coords) is not None:
        query = f"{coords[0]:.7f},{coords[1]:.7f}"
    elif str(address or "").strip() not in {"", "-"}:
        query = str(address).strip()
    elif str(name or "").strip() not in {"", "-"}:
        query = str(name).strip()
    return "https://www.google.com/maps/search/?" + urlencode({"api": "1", "query": query}) if query else None


def google_maps_directions_url(name: str = "", address: str = "", coords: tuple[float, float] | None = None) -> str | None:
    destination = ""
    if coords is not None and valid_lat_lng(*coords) is not None:
        destination = f"{coords[0]:.7f},{coords[1]:.7f}"
    elif str(address or "").strip() not in {"", "-"}:
        destination = str(address).strip()
    elif str(name or "").strip() not in {"", "-"}:
        destination = str(name).strip()
    return "https://www.google.com/maps/dir/?" + urlencode({"api": "1", "destination": destination}) if destination else None
