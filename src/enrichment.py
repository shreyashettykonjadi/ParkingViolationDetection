"""
OSM / Overpass enrichment for hotspot centroids — Objective 2.

Quantifies parking-induced traffic impact using road criticality and urban
context (hospitals, offices, schools, transit) around each hotspot centroid.

Overpass is keyless. TomTom live congestion is reserved for a later phase.
All calls happen on-demand (button-triggered) and results are cached in SQLite
by the caller so the public Overpass instance is never hit twice per centroid.
"""

import math
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Overpass rejects the default python-requests UA (HTTP 406); identify the app.
HEADERS = {"User-Agent": "ParkSenseAI/1.0 (parking-enforcement-research)"}


def _overpass(query, timeout):
    """POST a query to Overpass with a proper User-Agent; return parsed JSON."""
    resp = requests.post(
        OVERPASS_URL, data={"data": query}, headers=HEADERS, timeout=timeout + 5
    )
    resp.raise_for_status()
    return resp.json()

# OSM highway tag → road importance 1..5
ROAD_IMPORTANCE = {
    "motorway": 5, "motorway_link": 5,
    "trunk": 5, "trunk_link": 5,
    "primary": 4, "primary_link": 4,
    "secondary": 3, "secondary_link": 3,
    "tertiary": 2, "tertiary_link": 2,
    "residential": 1, "unclassified": 1,
    "living_street": 1, "service": 1,
}

# Raw dominant_vehicle string → severity weight 1..6 (carriageway footprint)
VEHICLE_SEVERITY = {
    "SCOOTER": 1, "MOTOR CYCLE": 1,
    "CAR": 2, "PASSENGER AUTO": 2,
    "MAXI-CAB": 3,
    "PRIVATE BUS": 5, "TOURIST BUS": 5,
    "TANKER": 6, "LORRY": 6, "HGV": 6,
}

# Urban demand-generator → contribution weight (parking = legitimate supply, offsets)
URBAN_WEIGHTS = {
    "hospital": 3, "railway_station": 3,
    "office": 2, "mall": 2, "marketplace": 2, "school": 2,
    "college": 1, "university": 1, "bus_stop": 1, "place_of_worship": 1,
    "parking": -1,
}

# Count columns produced by fetch_urban_context (order fixed for the cache schema)
URBAN_COUNT_COLS = [
    "office_count", "hospital_count", "school_count", "college_count",
    "university_count", "bus_stop_count", "railway_station_count",
    "parking_lot_count", "mall_count", "market_count", "religious_place_count",
]

ROAD_COLS = [
    "road_type", "lane_count", "maxspeed", "is_oneway",
    "road_importance_score", "road_segment_length",
]


# ── Helpers ─────────────────────────────────────────────────────────────────────

def cell_key(lat, lon):
    """~11 m grid key — dedupes overlapping centroids across contexts."""
    return f"{round(float(lat), 4)}_{round(float(lon), 4)}"


def _haversine(a_lat, a_lon, b_lat, b_lon):
    R = 6371000.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dphi = math.radians(b_lat - a_lat)
    dlmb = math.radians(b_lon - a_lon)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _to_int(val, default=None):
    try:
        return int(str(val).split(";")[0].strip())
    except (ValueError, AttributeError, TypeError):
        return default


def _neutral_features():
    """Defaults used when an Overpass call fails — keeps the table rendering."""
    feat = {c: 0 for c in URBAN_COUNT_COLS}
    feat.update({
        "road_type": None, "lane_count": None, "maxspeed": None,
        "is_oneway": 0, "road_importance_score": 1.0, "road_segment_length": 0.0,
    })
    return feat


# ── Source 1: Road Intelligence (PRD §11) ───────────────────────────────────────

def fetch_road_features(lat, lon, timeout=25):
    q = f"""
    [out:json][timeout:{timeout}];
    way(around:50,{lat},{lon})["highway"];
    out body geom;
    """
    elements = _overpass(q, timeout).get("elements", [])

    best = None
    best_imp = -1
    for el in elements:
        tags = el.get("tags", {})
        hw = tags.get("highway")
        imp = ROAD_IMPORTANCE.get(hw, 1)
        if imp > best_imp:
            best_imp = imp
            best = el

    if best is None:
        f = _neutral_features()
        return {k: f[k] for k in ROAD_COLS}

    tags = best.get("tags", {})
    geom = best.get("geometry", []) or []
    seg_len = 0.0
    for p, nxt in zip(geom, geom[1:]):
        seg_len += _haversine(p["lat"], p["lon"], nxt["lat"], nxt["lon"])

    return {
        "road_type": tags.get("highway"),
        "lane_count": _to_int(tags.get("lanes")),
        "maxspeed": tags.get("maxspeed"),
        "is_oneway": 1 if tags.get("oneway") in ("yes", "true", "1", "-1") else 0,
        "road_importance_score": float(ROAD_IMPORTANCE.get(tags.get("highway"), 1)),
        "road_segment_length": round(seg_len, 1),
    }


# ── Source 2: Urban Context (PRD §12) ────────────────────────────────────────────

def fetch_urban_context(lat, lon, timeout=30):
    q = f"""
    [out:json][timeout:{timeout}];
    (
      nwr(around:1000,{lat},{lon})["office"];
      nwr(around:1000,{lat},{lon})["amenity"="hospital"];
      nwr(around:1000,{lat},{lon})["amenity"="school"];
      nwr(around:1000,{lat},{lon})["amenity"="college"];
      nwr(around:1000,{lat},{lon})["amenity"="university"];
      node(around:1000,{lat},{lon})["highway"="bus_stop"];
      nwr(around:1000,{lat},{lon})["railway"="station"];
      nwr(around:1000,{lat},{lon})["amenity"="parking"];
      nwr(around:1000,{lat},{lon})["shop"="mall"];
      nwr(around:1000,{lat},{lon})["amenity"="marketplace"];
      nwr(around:1000,{lat},{lon})["amenity"="place_of_worship"];
    );
    out tags;
    """
    elements = _overpass(q, timeout).get("elements", [])

    counts = {c: 0 for c in URBAN_COUNT_COLS}
    for el in elements:
        t = el.get("tags", {})
        amenity = t.get("amenity")
        if "office" in t:                       counts["office_count"] += 1
        if amenity == "hospital":               counts["hospital_count"] += 1
        if amenity == "school":                 counts["school_count"] += 1
        if amenity == "college":                counts["college_count"] += 1
        if amenity == "university":              counts["university_count"] += 1
        if t.get("highway") == "bus_stop":      counts["bus_stop_count"] += 1
        if t.get("railway") == "station":       counts["railway_station_count"] += 1
        if amenity == "parking":                counts["parking_lot_count"] += 1
        if t.get("shop") == "mall":             counts["mall_count"] += 1
        if amenity == "marketplace":            counts["market_count"] += 1
        if amenity == "place_of_worship":       counts["religious_place_count"] += 1
    return counts


def enrich_centroid(lat, lon):
    """Fetch road + urban context for one centroid. Never raises — returns
    neutral defaults on any failure so the dashboard table always renders."""
    feat = _neutral_features()
    try:
        feat.update(fetch_road_features(lat, lon))
        time.sleep(1)  # be polite to the public Overpass instance
        feat.update(fetch_urban_context(lat, lon))
    except (requests.RequestException, ValueError, KeyError):
        pass  # keep neutral defaults
    feat["centroid_lat"] = float(lat)
    feat["centroid_long"] = float(lon)
    feat["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return feat


# ── Impact scoring & re-ranking ──────────────────────────────────────────────────

def _minmax(s):
    s = s.astype(float)
    lo, hi = s.min(), s.max()
    if hi - lo < 1e-9:
        return pd.Series(0.5, index=s.index)  # all equal → neutral
    return (s - lo) / (hi - lo)


def _reason(row):
    bits = []
    if row["road_criticality"] >= 0.6:
        bits.append(f"{(row['road_type'] or 'major road').replace('_', ' ').title()}")
    if row["hospital_count"] > 0:
        bits.append(f"near {int(row['hospital_count'])} hospital(s)")
    if row["office_count"] >= 3:
        bits.append(f"{int(row['office_count'])} offices")
    if row["railway_station_count"] > 0:
        bits.append("transit hub")
    if row["enforcement_norm"] >= 0.7:
        bits.append("high violation volume")
    if row["junction_risk"] == 1:
        bits.append("at junction")
    return " · ".join(bits[:3]) if bits else "moderate impact factors"


def compute_impact(hotspots, enriched):
    """
    hotspots : top-N contextual hotspots DataFrame (from db.get_contextual_hotspots)
    enriched : matching OSM features DataFrame (one row per hotspot, same order)
    Returns a DataFrame sorted by importance desc with factor columns + reason.
    """
    df = hotspots.reset_index(drop=True).copy()
    en = enriched.reset_index(drop=True)
    for col in ROAD_COLS + URBAN_COUNT_COLS:
        df[col] = en[col].values

    # Component: road criticality
    imp = df["road_importance_score"].fillna(1.0) / 5.0
    lanes = df["lane_count"].fillna(2).clip(upper=6) / 6.0
    df["road_criticality"] = (0.6 * imp + 0.4 * lanes).round(3)

    # Component: urban activity (weighted sum of generators, min-max normalized)
    weighted = sum(
        df[col] * URBAN_WEIGHTS[key]
        for col, key in [
            ("office_count", "office"), ("hospital_count", "hospital"),
            ("school_count", "school"), ("college_count", "college"),
            ("university_count", "university"), ("bus_stop_count", "bus_stop"),
            ("railway_station_count", "railway_station"),
            ("parking_lot_count", "parking"), ("mall_count", "mall"),
            ("market_count", "marketplace"), ("religious_place_count", "place_of_worship"),
        ]
    )
    df["urban_activity"] = _minmax(weighted).round(3)

    # Component: vehicle severity
    df["vehicle_severity"] = (
        df["dominant_vehicle"].map(VEHICLE_SEVERITY).fillna(2) / 6.0
    ).round(3)

    # Component: junction risk
    df["junction_risk"] = df["nearest_junction"].apply(
        lambda j: 0 if (pd.isna(j) or str(j).strip().lower() in ("", "no junction")) else 1
    )

    # Existing parking-volume signal (already 0–1)
    df["enforcement_norm"] = df["enforcement_score"].astype(float).clip(0, 1)

    # Reserved live-congestion slot (neutral until TomTom key)
    df["live_congestion"] = "—"

    # Weighted-sum Traffic Impact Score (0–100); weights sum to 1.00
    df["importance"] = (100 * (
        0.35 * df["enforcement_norm"]
        + 0.20 * df["road_criticality"]
        + 0.20 * df["urban_activity"]
        + 0.15 * df["vehicle_severity"]
        + 0.10 * df["junction_risk"]
    )).round(1)

    df["zone"] = df["police_station"].astype(str) + " / " + (
        df["nearest_junction"].fillna("—").astype(str)
    )
    df["reason"] = df.apply(_reason, axis=1)

    df = df.sort_values("importance", ascending=False).reset_index(drop=True)
    df["new_rank"] = np.arange(1, len(df) + 1)
    df["rank_change"] = df["context_rank"].astype(int) - df["new_rank"]
    return df
