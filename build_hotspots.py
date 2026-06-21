#!/usr/bin/env python3
"""
Hotspot build pipeline — generates data/hotspots.db from clean_parking_data.csv.

Three independent HDBSCAN layers:
  1. Persistent  — full dataset, all-time geographic hotspot zones
  2. Contextual  — one HDBSCAN per (day_type × time_bucket) context (10 runs)
  3. Monthly     — one HDBSCAN per calendar month (6 runs)

Usage:
    python build_hotspots.py

Safe to re-run: drops and recreates the database each time.
"""

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from src.hotspot_detection import run_hdbscan

CSV_PATH = "data/clean_parking_data.csv"
DB_PATH  = "data/hotspots.db"

SHIFT_BINS   = [0, 6, 9, 14, 20, 24]
SHIFT_LABELS = ["night", "morning_peak", "midday", "evening_peak", "late_night"]

YEAR_MONTH_ORDER = ["2023-11", "2023-12", "2024-01", "2024-02", "2024-03", "2024-04"]

CONTEXTS = [
    "weekday_night", "weekday_morning_peak", "weekday_midday",
    "weekday_evening_peak", "weekday_late_night",
    "weekend_night", "weekend_morning_peak", "weekend_midday",
    "weekend_evening_peak", "weekend_late_night",
]

PERSISTENT_MIN_CLUSTER = 30
CONTEXTUAL_MIN_CLUSTER = 15
MONTHLY_MIN_CLUSTER    = 20


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE violations (
    id                    TEXT PRIMARY KEY,
    persistent_cluster_id INTEGER,
    latitude              REAL,
    longitude             REAL,
    hour                  INTEGER,
    weekday               INTEGER,
    month                 INTEGER,
    year_month            TEXT,
    date                  TEXT,
    primary_violation     TEXT,
    vehicle_type          TEXT,
    police_station        TEXT,
    junction_name         TEXT,
    time_bucket           TEXT,
    day_type              TEXT
);

CREATE TABLE persistent_hotspots (
    cluster_id       INTEGER PRIMARY KEY,
    centroid_lat     REAL,
    centroid_long    REAL,
    police_station   TEXT,
    nearest_junction TEXT,
    top_violation    TEXT,
    dominant_vehicle TEXT,
    peak_hour        INTEGER,
    all_time_count   INTEGER,
    persistent_rank  INTEGER
);

CREATE TABLE contextual_hotspots (
    context          TEXT,
    cluster_id       INTEGER,
    centroid_lat     REAL,
    centroid_long    REAL,
    police_station   TEXT,
    nearest_junction TEXT,
    top_violation    TEXT,
    dominant_vehicle TEXT,
    peak_hour        INTEGER,
    violation_count  INTEGER,
    recent_count     INTEGER,
    enforcement_score REAL,
    context_rank     INTEGER,
    PRIMARY KEY (context, cluster_id)
);

CREATE TABLE monthly_hotspots (
    year_month       TEXT,
    cluster_id       INTEGER,
    centroid_lat     REAL,
    centroid_long    REAL,
    police_station   TEXT,
    nearest_junction TEXT,
    top_violation    TEXT,
    dominant_vehicle TEXT,
    peak_hour        INTEGER,
    violation_count  INTEGER,
    monthly_rank     INTEGER,
    PRIMARY KEY (year_month, cluster_id)
);

CREATE TABLE hotspot_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

INDEXES = """
CREATE INDEX idx_violations_persistent_cluster ON violations(persistent_cluster_id);
CREATE INDEX idx_violations_explorer           ON violations(police_station, hour, date);
CREATE INDEX idx_violations_year_month         ON violations(year_month);
CREATE INDEX idx_contextual_context            ON contextual_hotspots(context);
CREATE INDEX idx_contextual_rank               ON contextual_hotspots(context, context_rank);
CREATE INDEX idx_monthly_year_month            ON monthly_hotspots(year_month);
CREATE INDEX idx_monthly_rank                  ON monthly_hotspots(year_month, monthly_rank);
CREATE INDEX idx_persistent_rank               ON persistent_hotspots(persistent_rank);
CREATE INDEX idx_persistent_station            ON persistent_hotspots(police_station);
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mode(series):
    counts = series.dropna().value_counts()
    return counts.index[0] if len(counts) > 0 else None


def _assign_shift(hour_series):
    return pd.cut(
        hour_series, bins=SHIFT_BINS, labels=SHIFT_LABELS, right=False
    ).astype(str)


def _assign_day_type(weekday_series):
    return weekday_series.apply(lambda x: "weekend" if x >= 5 else "weekday")


def _cluster_summary(valid_df, id_col="cluster_id"):
    return (
        valid_df.groupby(id_col)
        .agg(
            centroid_lat     = ("latitude",          "mean"),
            centroid_long    = ("longitude",         "mean"),
            top_violation    = ("primary_violation", _mode),
            dominant_vehicle = ("vehicle_type",      _mode),
            nearest_junction = ("junction_name",     _mode),
            police_station   = ("police_station",    _mode),
            peak_hour        = ("hour",              _mode),
            violation_count  = (id_col,              "count"),
        )
        .reset_index()
        .rename(columns={id_col: "cluster_id"})
    )


# ── Layer 1: Persistent ───────────────────────────────────────────────────────

def build_persistent_layer(df, conn):
    print("\n[Layer 1] Persistent Hotspots — full dataset")
    labels = run_hdbscan(df, min_cluster_size=PERSISTENT_MIN_CLUSTER)
    df["persistent_cluster_id"] = labels
    noise_pct  = (labels == -1).mean() * 100
    print(f"  Clusters: {pd.Series(labels[labels>=0]).nunique():,}  |  Noise: {noise_pct:.1f}%")

    # violations table
    print("  Inserting violations table...")
    v = df[[
        "id", "persistent_cluster_id", "latitude", "longitude",
        "hour", "weekday", "month", "year_month", "date",
        "primary_violation", "vehicle_type", "police_station",
        "junction_name", "time_bucket", "day_type",
    ]].copy()
    v.to_sql("violations", conn, if_exists="append", index=False, chunksize=10_000)

    # persistent_hotspots table
    valid = df[df["persistent_cluster_id"] >= 0].copy()
    summary = _cluster_summary(valid, id_col="persistent_cluster_id")
    summary = summary.rename(columns={"violation_count": "all_time_count"})
    summary = summary.sort_values("all_time_count", ascending=False)
    summary["persistent_rank"] = range(1, len(summary) + 1)
    summary.to_sql("persistent_hotspots", conn, if_exists="append", index=False)
    print(f"  Stored {len(summary):,} persistent hotspots")

    return df


# ── Layer 2: Contextual ───────────────────────────────────────────────────────

def build_contextual_layer(df, conn, last_30d_cutoff):
    print("\n[Layer 2] Contextual Hotspots — 10 contexts")
    all_rows = []

    for context in CONTEXTS:
        day_type, time_bucket = context.split("_", 1)
        subset = df[
            (df["day_type"] == day_type) & (df["time_bucket"] == time_bucket)
        ].copy()

        if len(subset) < CONTEXTUAL_MIN_CLUSTER * 2:
            print(f"  {context}: skipped (only {len(subset)} records)")
            continue

        labels = run_hdbscan(subset, min_cluster_size=CONTEXTUAL_MIN_CLUSTER)
        subset["cluster_id"] = labels
        valid = subset[subset["cluster_id"] >= 0]

        if valid["cluster_id"].nunique() == 0:
            print(f"  {context}: no clusters found")
            continue

        summary = _cluster_summary(valid)

        # Recent count: violations in last 30 days of dataset within each cluster
        recent_subset = valid[valid["date"] > last_30d_cutoff]
        recent_counts = (
            recent_subset.groupby("cluster_id").size().rename("recent_count")
        )
        summary = summary.join(recent_counts, on="cluster_id", how="left")
        summary["recent_count"] = summary["recent_count"].fillna(0).astype(int)

        # Enforcement score: 60% historical volume + 40% recent activity
        summary["violation_count_pct"] = summary["violation_count"].rank(pct=True)
        summary["recent_count_pct"]    = summary["recent_count"].rank(pct=True)
        summary["enforcement_score"]   = (
            0.6 * summary["violation_count_pct"] + 0.4 * summary["recent_count_pct"]
        ).round(3)

        summary["context_rank"] = (
            summary["enforcement_score"].rank(ascending=False, method="min").astype(int)
        )
        summary["context"] = context
        summary = summary.drop(columns=["violation_count_pct", "recent_count_pct"])
        all_rows.append(summary)
        print(f"  {context}: {valid['cluster_id'].nunique():>4} clusters")

    if all_rows:
        pd.concat(all_rows, ignore_index=True).to_sql(
            "contextual_hotspots", conn, if_exists="append", index=False
        )


# ── Layer 3: Monthly ──────────────────────────────────────────────────────────

def build_monthly_layer(df, conn):
    print("\n[Layer 3] Monthly Hotspots — 6 months")
    all_rows = []

    for ym in YEAR_MONTH_ORDER:
        subset = df[df["year_month"] == ym].copy()
        if len(subset) < MONTHLY_MIN_CLUSTER * 2:
            print(f"  {ym}: skipped (only {len(subset)} records)")
            continue

        labels = run_hdbscan(subset, min_cluster_size=MONTHLY_MIN_CLUSTER)
        subset["cluster_id"] = labels
        valid = subset[subset["cluster_id"] >= 0]

        if valid["cluster_id"].nunique() == 0:
            print(f"  {ym}: no clusters found")
            continue

        summary = _cluster_summary(valid)
        summary["monthly_rank"] = (
            summary["violation_count"].rank(ascending=False, method="min").astype(int)
        )
        summary["year_month"] = ym
        all_rows.append(summary)
        print(f"  {ym}: {valid['cluster_id'].nunique():>4} clusters")

    if all_rows:
        pd.concat(all_rows, ignore_index=True).to_sql(
            "monthly_hotspots", conn, if_exists="append", index=False
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("NagaraNetra — Hotspot Build Pipeline")
    print("=" * 55)

    # Load & prepare
    print("\n[0/4] Loading and preparing data...")
    df = pd.read_csv(CSV_PATH)

    # The CSV's hour/weekday/date columns are derived from created_datetime in UTC.
    # Bengaluru is UTC+5:30, so we re-derive every temporal field in IST — otherwise
    # the time buckets (and the app's "current IST -> bucket" logic) are misaligned by
    # 5.5h. Note a late-UTC event can roll over to the next IST date.
    ist = (pd.to_datetime(df["created_datetime"], utc=True, format="ISO8601")
             .dt.tz_convert("Asia/Kolkata"))
    df["hour"]    = ist.dt.hour
    df["weekday"] = ist.dt.weekday
    df["month"]   = ist.dt.month
    df["date"]    = ist.dt.strftime("%Y-%m-%d")

    df["year_month"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m")
    df["time_bucket"] = _assign_shift(df["hour"])
    df["day_type"]    = _assign_day_type(df["weekday"])
    max_date          = df["date"].max()
    last_30d_cutoff   = (pd.to_datetime(max_date) - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    print(f"  Records: {len(df):,}  |  Date range: {df['date'].min()} to {max_date}")
    print(f"  Last-30d cutoff: {last_30d_cutoff}")

    # Init DB
    print("\n[1/4] Initialising database...")
    Path(DB_PATH).parent.mkdir(exist_ok=True)
    if Path(DB_PATH).exists():
        Path(DB_PATH).unlink()
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()

    # Build layers
    df = build_persistent_layer(df, conn)
    build_contextual_layer(df, conn, last_30d_cutoff)
    build_monthly_layer(df, conn)

    # Metadata
    meta = [
        ("min_date",          df["date"].min()),
        ("max_date",          max_date),
        ("last_30d_cutoff",   last_30d_cutoff),
        ("total_records",     str(len(df))),
        ("year_month_order",  json.dumps(YEAR_MONTH_ORDER)),
        ("contexts",          json.dumps(CONTEXTS)),
    ]
    pd.DataFrame(meta, columns=["key", "value"]).to_sql(
        "hotspot_metadata", conn, if_exists="append", index=False
    )

    # Indexes
    print("\n[4/4] Creating indexes...")
    conn.executescript(INDEXES)
    conn.commit()

    # Summary
    print("\n=== Build Complete ===")
    tables = [
        "violations", "persistent_hotspots",
        "contextual_hotspots", "monthly_hotspots", "hotspot_metadata",
    ]
    for t in tables:
        n = pd.read_sql(f"SELECT COUNT(*) AS n FROM {t}", conn).iloc[0, 0]
        print(f"  {t:<30} {n:>8,} rows")
    conn.close()
    print(f"\nDatabase: {DB_PATH}")


if __name__ == "__main__":
    main()
