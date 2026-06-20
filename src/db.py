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
        "SELECT DISTINCT police_station FROM violations ORDER BY police_station"
    )["police_station"].tolist()


def get_meta(key):
    row = query("SELECT value FROM hotspot_metadata WHERE key = ?", (key,))
    return row.iloc[0, 0] if len(row) > 0 else None


def get_total_violations():
    return int(query("SELECT COUNT(*) AS n FROM violations").iloc[0, 0])


def get_total_clusters():
    return int(query("SELECT COUNT(*) AS n FROM persistent_hotspots").iloc[0, 0])
