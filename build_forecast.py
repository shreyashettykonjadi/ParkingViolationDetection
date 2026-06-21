#!/usr/bin/env python3
"""
Forecast build pipeline — trains the next-day violation model and writes
data/forecast.db.

Pipeline:
  1. Load clustered violations from hotspots.db → dense (cluster × day) panel.
  2. Engineer calendar + lag/rolling + zone-static (+ optional OSM) features.
  3. Temporal holdout (train ≤ max_date−30d, test = last 30d): train baseline + ML,
     evaluate ML vs baseline (count + ranking metrics).
  4. Retrain on the full history and forecast the next day for every zone.
  5. Persist predictions + metrics to a SEPARATE forecast.db (so re-running
     build_hotspots.py never wipes it — same pattern as enrichment.db).

Usage:
    python build_forecast.py

Safe to re-run: drops and recreates forecast.db each time. Requires hotspots.db
(run build_hotspots.py first).
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src import forecast as fc

HOTSPOTS_DB = "data/hotspots.db"
ENRICH_DB   = "data/enrichment.db"
FORECAST_DB = "data/forecast.db"
HOLDOUT_DAYS = 30

SCHEMA = """
CREATE TABLE predictions (
    cluster_id      INTEGER PRIMARY KEY,
    target_date     TEXT,
    weekday         INTEGER,
    pred_count      REAL,
    q10             REAL,
    q90             REAL,
    base_count      REAL,
    recent_7d_mean  REAL,
    pred_rank       INTEGER
);
CREATE TABLE forecast_metrics (
    metric    TEXT PRIMARY KEY,
    model     REAL,
    baseline  REAL
);
CREATE TABLE forecast_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def load_static(conn):
    return pd.read_sql(
        "SELECT cluster_id, centroid_lat, centroid_long, all_time_count, "
        "police_station, dominant_vehicle, top_violation FROM persistent_hotspots",
        conn,
    )


def load_osm(static):
    """Optional OSM static features, joined to clusters by rounded-centroid cell_key.
    Returns None if the enrichment cache is missing or has no overlap."""
    if not Path(ENRICH_DB).exists():
        return None
    try:
        ec = sqlite3.connect(ENRICH_DB)
        en = pd.read_sql(
            "SELECT cell_key, office_count, hospital_count, road_importance_score "
            "FROM hotspot_enrichment", ec,
        )
        ec.close()
    except Exception:
        return None
    if len(en) == 0:
        return None
    s = static.copy()
    s["cell_key"] = (s["centroid_lat"].round(4).astype(str) + "_"
                     + s["centroid_long"].round(4).astype(str))
    merged = s.merge(en, on="cell_key", how="inner")[["cluster_id"] + fc.OSM_FEATURES]
    return merged if len(merged) else None


def main():
    print("=" * 55)
    print("ParkSense AI — Forecast Build Pipeline")
    print("=" * 55)

    if not Path(HOTSPOTS_DB).exists():
        raise SystemExit("hotspots.db not found — run `python build_hotspots.py` first.")

    print("\n[0/5] Loading violations from hotspots.db...")
    conn = sqlite3.connect(HOTSPOTS_DB)
    viol = pd.read_sql(
        "SELECT persistent_cluster_id, date FROM violations "
        "WHERE persistent_cluster_id >= 0", conn,
    )
    static = load_static(conn)
    conn.close()

    print("\n[1/5] Building (cluster × day) panel...")
    panel = fc.build_panel(viol)
    max_date = panel["date"].max()
    cutoff   = max_date - pd.Timedelta(days=HOLDOUT_DAYS)
    n_zones  = panel["cluster_id"].nunique()
    print(f"  Zones: {n_zones:,}  |  Panel rows: {len(panel):,}")
    print(f"  Date range: {panel['date'].min().date()} → {max_date.date()}")
    print(f"  Train ≤ {cutoff.date()}  |  Test = last {HOLDOUT_DAYS} days")

    osm = load_osm(static)
    include_osm = osm is not None and len(osm) > 0
    print(f"  OSM features: {'on (' + str(len(osm)) + ' zones)' if include_osm else 'off'}")

    print("\n[2/5] Engineering features...")
    feat = fc.make_features(panel, static, osm)
    cols = fc.feature_columns(include_osm)

    train = feat[feat["date"] <= cutoff]
    test  = feat[feat["date"] >  cutoff].copy()

    print("\n[3/5] Training baseline + ML on the train split, evaluating on holdout...")
    base_eval = fc.train_baseline(train)
    models_eval = fc.train_model(train[cols], train["count"])

    preds = fc.model_predict(models_eval, test[cols])
    test["pred_count"] = preds["pred_count"].values
    test["base_count"] = fc.baseline_predict(base_eval, test["cluster_id"], test["weekday"])
    metrics = fc.evaluate(test)

    print(f"  {'metric':<18}{'baseline':>12}{'model':>12}")
    for m in ["MAE", "RMSE", "poisson_deviance",
              "precision@10", "precision@20", "ndcg@10", "spearman"]:
        print(f"  {m:<18}{metrics['baseline'][m]:>12.3f}{metrics['model'][m]:>12.3f}")
    print(f"  MAE improvement: {metrics['mae_improvement_pct']:+.1f}%  "
          f"(model {'beats' if metrics['mae_improvement_pct'] >= 0 else 'WORSE THAN'} baseline)")

    print("\n[4/5] Retraining on full history and forecasting the next day...")
    base_full   = fc.train_baseline(feat)
    models_full = fc.train_model(feat[cols], feat["count"])
    target_date = max_date + pd.Timedelta(days=1)
    nextday = fc.predict_next_day(panel, static, models_full, base_full,
                                  target_date, osm, include_osm)
    print(f"  Forecast target: {target_date.date()} "
          f"({target_date.day_name()})  |  {len(nextday):,} zones")
    for _, r in nextday.head(5).iterrows():
        print(f"    #{int(r['pred_rank'])} {str(r['police_station']):<18} "
              f"~{r['pred_count']:.1f}  [{r['q10']:.1f}–{r['q90']:.1f}]")

    print("\n[5/5] Writing forecast.db...")
    Path(FORECAST_DB).parent.mkdir(exist_ok=True)
    if Path(FORECAST_DB).exists():
        Path(FORECAST_DB).unlink()
    fconn = sqlite3.connect(FORECAST_DB)
    fconn.executescript(SCHEMA)

    out = nextday[["cluster_id", "weekday", "pred_count", "q10", "q90",
                   "base_count", "recent_7d_mean", "pred_rank"]].copy()
    out["target_date"] = target_date.strftime("%Y-%m-%d")
    out.to_sql("predictions", fconn, if_exists="append", index=False)

    mrows = [{"metric": m, "model": metrics["model"][m], "baseline": metrics["baseline"][m]}
             for m in ["MAE", "RMSE", "poisson_deviance",
                       "precision@10", "precision@20", "ndcg@10", "spearman"]]
    pd.DataFrame(mrows).to_sql("forecast_metrics", fconn, if_exists="append", index=False)

    meta = [
        ("target_date",         target_date.strftime("%Y-%m-%d")),
        ("target_weekday",      target_date.day_name()),
        ("trained_at",          datetime.now(timezone.utc).isoformat(timespec="seconds")),
        ("n_zones",             str(n_zones)),
        ("n_train_days",        str((cutoff - panel['date'].min()).days)),
        ("holdout_days",        str(HOLDOUT_DAYS)),
        ("include_osm",         str(include_osm)),
        ("mae_improvement_pct", f"{metrics['mae_improvement_pct']:.1f}"),
        ("data_max_date",       max_date.strftime("%Y-%m-%d")),
    ]
    pd.DataFrame(meta, columns=["key", "value"]).to_sql(
        "forecast_meta", fconn, if_exists="append", index=False)
    fconn.commit()
    fconn.close()
    print(f"\nDatabase: {FORECAST_DB}")
    print("=== Forecast Build Complete ===")


if __name__ == "__main__":
    main()
