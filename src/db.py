import sqlite3
import pandas as pd

DB_PATH = "data/hotspots.db"

MONTH_LABELS = {
    "2023-11": "Nov 2023", "2023-12": "Dec 2023",
    "2024-01": "Jan 2024", "2024-02": "Feb 2024",
    "2024-03": "Mar 2024", "2024-04": "Apr 2024",
}

SHIFT_DISPLAY = {
    "night":        "Night (12am–6am)",
    "morning_peak": "Morning Peak (6am–9am)",
    "midday":       "Midday (9am–2pm)",
    "evening_peak": "Evening Peak (2pm–8pm)",
    "late_night":   "Late Night (8pm–12am)",
}


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def query(sql, params=()):
    with _conn() as conn:
        return pd.read_sql(sql, conn, params=params)


# ── Persistent Hotspots ───────────────────────────────────────────────────────

def get_persistent_hotspots(police_station=None, limit=20):
    if police_station:
        return query("""
            SELECT cluster_id, police_station, nearest_junction,
                   centroid_lat, centroid_long, all_time_count,
                   top_violation, peak_hour, dominant_vehicle, persistent_rank
            FROM persistent_hotspots
            WHERE police_station = ?
            ORDER BY persistent_rank
            LIMIT ?
        """, (police_station, limit))
    return query("""
        SELECT cluster_id, police_station, nearest_junction,
               centroid_lat, centroid_long, all_time_count,
               top_violation, peak_hour, dominant_vehicle, persistent_rank
        FROM persistent_hotspots
        ORDER BY persistent_rank
        LIMIT ?
    """, (limit,))


# ── Monthly Hotspots ──────────────────────────────────────────────────────────

def get_monthly_hotspots(year_month, police_station=None, limit=20):
    if police_station:
        return query("""
            SELECT cluster_id, police_station, nearest_junction,
                   centroid_lat, centroid_long, violation_count,
                   top_violation, peak_hour, monthly_rank
            FROM monthly_hotspots
            WHERE year_month = ? AND police_station = ?
            ORDER BY monthly_rank
            LIMIT ?
        """, (year_month, police_station, limit))
    return query("""
        SELECT cluster_id, police_station, nearest_junction,
               centroid_lat, centroid_long, violation_count,
               top_violation, peak_hour, monthly_rank
        FROM monthly_hotspots
        WHERE year_month = ?
        ORDER BY monthly_rank
        LIMIT ?
    """, (year_month, limit))


def get_available_year_months():
    return query(
        "SELECT DISTINCT year_month FROM monthly_hotspots ORDER BY year_month"
    )["year_month"].tolist()


# ── Contextual Hotspots ───────────────────────────────────────────────────────

def get_contextual_hotspots(context, limit=20):
    return query("""
        SELECT cluster_id, police_station, nearest_junction,
               centroid_lat, centroid_long, violation_count,
               recent_count, enforcement_score, context_rank,
               top_violation, peak_hour, dominant_vehicle
        FROM contextual_hotspots
        WHERE context = ?
        ORDER BY context_rank
        LIMIT ?
    """, (context, limit))


# ── Violation Explorer ────────────────────────────────────────────────────────

def get_violations(police_station, hour, date, limit=1000):
    return query("""
        SELECT latitude, longitude, vehicle_type, primary_violation,
               police_station, hour, junction_name
        FROM violations
        WHERE police_station = ? AND hour = ? AND date = ?
        LIMIT ?
    """, (police_station, int(hour), str(date), limit))


def get_hourly_trend(police_station):
    return query("""
        SELECT hour, COUNT(*) AS count
        FROM violations
        WHERE police_station = ?
        GROUP BY hour
        ORDER BY hour
    """, (police_station,))


# ── Metadata ──────────────────────────────────────────────────────────────────

def get_police_stations():
    return query(
        "SELECT DISTINCT police_station FROM violations "
        "WHERE police_station IS NOT NULL "
        "AND TRIM(police_station) <> '' "
        "AND LOWER(police_station) <> 'nan' "
        "ORDER BY police_station"
    )["police_station"].tolist()


def get_meta(key):
    row = query("SELECT value FROM hotspot_metadata WHERE key = ?", (key,))
    return row.iloc[0, 0] if len(row) > 0 else None


def get_total_violations():
    return int(query("SELECT COUNT(*) AS n FROM violations").iloc[0, 0])


def get_total_clusters():
    return int(query("SELECT COUNT(*) AS n FROM persistent_hotspots").iloc[0, 0])


# ── Enrichment store (Objective 2) ────────────────────────────────────────────
# Kept in a SEPARATE SQLite file so re-running build_hotspots.py (which drops and
# recreates hotspots.db) never wipes the cached OSM context. Static OSM facts
# (hospital/office counts, road type…) persist across rebuilds; live TomTom
# snapshots are stored here too but are demo-grade (refreshed on every compute).

ENRICH_DB_PATH = "data/enrichment.db"


def _enrich_conn():
    conn = sqlite3.connect(ENRICH_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _enrich_query(sql, params=()):
    with _enrich_conn() as conn:
        return pd.read_sql(sql, conn, params=params)


ENRICHMENT_COLUMNS = [
    "cell_key", "centroid_lat", "centroid_long",
    "road_type", "lane_count", "maxspeed", "is_oneway",
    "road_importance_score", "road_segment_length",
    "office_count", "hospital_count", "school_count", "college_count",
    "university_count", "bus_stop_count", "railway_station_count",
    "parking_lot_count", "mall_count", "market_count", "religious_place_count",
    "fetched_at",
]

LIVE_TRAFFIC_COLUMNS = [
    "cell_key", "centroid_lat", "centroid_long",
    "current_speed", "free_flow_speed", "congestion_index",
    "delay_ratio", "road_closure", "fetched_at",
]


def ensure_enrichment_table():
    """Create both the persistent OSM cache and the live-traffic snapshot table."""
    with _enrich_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hotspot_enrichment (
                cell_key              TEXT PRIMARY KEY,
                centroid_lat          REAL,
                centroid_long         REAL,
                road_type             TEXT,
                lane_count            INTEGER,
                maxspeed              TEXT,
                is_oneway             INTEGER,
                road_importance_score REAL,
                road_segment_length   REAL,
                office_count          INTEGER,
                hospital_count        INTEGER,
                school_count          INTEGER,
                college_count         INTEGER,
                university_count      INTEGER,
                bus_stop_count        INTEGER,
                railway_station_count INTEGER,
                parking_lot_count     INTEGER,
                mall_count            INTEGER,
                market_count          INTEGER,
                religious_place_count INTEGER,
                fetched_at            TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS live_traffic (
                cell_key         TEXT PRIMARY KEY,
                centroid_lat     REAL,
                centroid_long    REAL,
                current_speed    REAL,
                free_flow_speed  REAL,
                congestion_index REAL,
                delay_ratio      REAL,
                road_closure     INTEGER,
                fetched_at       TEXT
            )
        """)
        conn.commit()


# ── OSM cache (persistent) ──

def get_enrichment(cell_keys):
    if not cell_keys:
        return pd.DataFrame(columns=ENRICHMENT_COLUMNS)
    placeholders = ",".join("?" for _ in cell_keys)
    return _enrich_query(
        f"SELECT * FROM hotspot_enrichment WHERE cell_key IN ({placeholders})",
        tuple(cell_keys),
    )


def upsert_enrichment(row):
    vals = [row.get(c) for c in ENRICHMENT_COLUMNS]
    placeholders = ",".join("?" for _ in ENRICHMENT_COLUMNS)
    cols = ",".join(ENRICHMENT_COLUMNS)
    with _enrich_conn() as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO hotspot_enrichment ({cols}) VALUES ({placeholders})",
            vals,
        )
        conn.commit()


# ── Live traffic snapshots (demo) ──

def upsert_live_traffic(row):
    vals = [row.get(c) for c in LIVE_TRAFFIC_COLUMNS]
    placeholders = ",".join("?" for _ in LIVE_TRAFFIC_COLUMNS)
    cols = ",".join(LIVE_TRAFFIC_COLUMNS)
    with _enrich_conn() as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO live_traffic ({cols}) VALUES ({placeholders})",
            vals,
        )
        conn.commit()


def get_live_traffic(cell_keys):
    if not cell_keys:
        return pd.DataFrame(columns=LIVE_TRAFFIC_COLUMNS)
    placeholders = ",".join("?" for _ in cell_keys)
    return _enrich_query(
        f"SELECT * FROM live_traffic WHERE cell_key IN ({placeholders})",
        tuple(cell_keys),
    )


# ── Forecast store (Predictive layer) ─────────────────────────────────────────
# Separate SQLite file written by build_forecast.py — survives build_hotspots.py
# rebuilds, same as enrichment.db. Predictions join back to persistent_hotspots
# (in hotspots.db) for centroid/station/junction in pandas, since the tables live
# in different database files.

from pathlib import Path as _Path

FORECAST_DB_PATH = "data/forecast.db"


def _forecast_conn():
    conn = sqlite3.connect(FORECAST_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _forecast_query(sql, params=()):
    with _forecast_conn() as conn:
        return pd.read_sql(sql, conn, params=params)


def forecast_available():
    return _Path(FORECAST_DB_PATH).exists()


def get_predictions(limit=20):
    """Next-day predictions joined to persistent_hotspots for display."""
    if not forecast_available():
        return pd.DataFrame()
    preds = _forecast_query(
        "SELECT * FROM predictions ORDER BY pred_rank LIMIT ?", (int(limit),)
    )
    if len(preds) == 0:
        return preds
    ph = query(
        "SELECT cluster_id, centroid_lat, centroid_long, police_station, "
        "nearest_junction, top_violation, peak_hour, dominant_vehicle "
        "FROM persistent_hotspots"
    )
    return preds.merge(ph, on="cluster_id", how="left")


def get_forecast_metrics():
    if not forecast_available():
        return pd.DataFrame(columns=["metric", "model", "baseline"])
    return _forecast_query("SELECT metric, model, baseline FROM forecast_metrics")


def get_forecast_meta(key):
    if not forecast_available():
        return None
    row = _forecast_query("SELECT value FROM forecast_meta WHERE key = ?", (key,))
    return row.iloc[0, 0] if len(row) > 0 else None
