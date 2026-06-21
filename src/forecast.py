"""
Predictive layer — "Tomorrow's Predicted Hotspots".

Turns the descriptive hotspot history into a forecast: for each persistent
enforcement zone, the expected number of violations on the *next day*, ranked, with
a confidence band. This is the proactive counterpart to the reactive enforcement
tabs — patrols can be pre-positioned instead of dispatched after the fact.

Honest framing: an individual next-event lat/long is irreducibly noisy. What is
predictable and operationally useful is the **expected violation count per zone per
day** (a spatio-temporal intensity). We forecast that and rank zones.

Two models, by design:
  • baseline  — seasonal-naive: historical mean count per (cluster, weekday).
                The benchmark the ML model must beat.
  • ML        — sklearn HistGradientBoostingRegressor(loss="poisson") for the point
                estimate, plus two quantile models (q10/q90) for the band.

scikit-learn is already installed (transitively via hdbscan); no new heavy package.

All functions are pure and operate on DataFrames so the offline build script
(build_forecast.py) can orchestrate train → evaluate → predict → persist.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_poisson_deviance

# India / Karnataka public holidays falling inside the dataset window (IST). Hardcoded
# to avoid a `holidays` dependency; extend when the window grows.
HOLIDAYS = {
    "2023-11-12",  # Diwali
    "2023-11-13",  # Govardhan Puja
    "2023-11-27",  # Guru Nanak Jayanti
    "2023-12-25",  # Christmas
    "2024-01-01",  # New Year's Day
    "2024-01-15",  # Makara Sankranti
    "2024-01-26",  # Republic Day
    "2024-03-08",  # Maha Shivaratri
    "2024-03-25",  # Holi
    "2024-03-29",  # Good Friday
}

# Feature groups — kept explicit so the same columns train and predict.
FEATURE_NUM = [
    "weekday", "is_weekend", "day_of_month", "month", "week_of_year",
    "doy_sin", "doy_cos", "is_holiday",
    "lag_1", "lag_7", "lag_14",
    "roll_mean_7", "roll_mean_14", "roll_mean_30", "roll_std_7",
    "expanding_mean", "days_since_first",
    "centroid_lat", "centroid_long", "all_time_count",
]
FEATURE_CAT = ["police_station", "dominant_vehicle", "top_violation"]

# Optional OSM static features, folded in only if the enrichment cache has them.
OSM_FEATURES = ["office_count", "hospital_count", "road_importance_score"]

RANDOM_STATE = 42


# ── Panel construction ───────────────────────────────────────────────────────────

def build_panel(violations: pd.DataFrame) -> pd.DataFrame:
    """Clustered violations → dense per-(cluster, day) count grid.

    Each cluster's daily series starts at its first observed date and runs through
    the global max date, with missing days filled as 0 (structural zeros — the
    Poisson model handles these natively). Noise (cluster_id < 0) is excluded.
    """
    v = violations[violations["persistent_cluster_id"] >= 0].copy()
    v["date"] = pd.to_datetime(v["date"])
    counts = (
        v.groupby(["persistent_cluster_id", "date"]).size()
        .rename("count").reset_index()
        .rename(columns={"persistent_cluster_id": "cluster_id"})
    )
    gmax = counts["date"].max()

    frames = []
    for cid, g in counts.groupby("cluster_id"):
        idx = pd.date_range(g["date"].min(), gmax, freq="D")
        series = g.set_index("date")["count"].reindex(idx, fill_value=0)
        frames.append(pd.DataFrame({"cluster_id": cid, "date": idx,
                                    "count": series.values}))
    return pd.concat(frames, ignore_index=True)


# ── Feature engineering ──────────────────────────────────────────────────────────

def _add_calendar(df: pd.DataFrame) -> pd.DataFrame:
    d = df["date"]
    df["weekday"]      = d.dt.weekday
    df["is_weekend"]   = (df["weekday"] >= 5).astype(int)
    df["day_of_month"] = d.dt.day
    df["month"]        = d.dt.month
    df["week_of_year"] = d.dt.isocalendar().week.astype(int)
    doy = d.dt.dayofyear
    df["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    df["is_holiday"] = d.dt.strftime("%Y-%m-%d").isin(HOLIDAYS).astype(int)
    return df


def _add_lags(df: pd.DataFrame) -> pd.DataFrame:
    """Lag / rolling features. All use shift(1) so no row sees its own day's count."""
    df = df.sort_values(["cluster_id", "date"]).reset_index(drop=True)
    grp = df.groupby("cluster_id")["count"]

    df["lag_1"]  = grp.shift(1)
    df["lag_7"]  = grp.shift(7)
    df["lag_14"] = grp.shift(14)

    df["roll_mean_7"]  = grp.transform(lambda s: s.shift(1).rolling(7,  min_periods=1).mean())
    df["roll_mean_14"] = grp.transform(lambda s: s.shift(1).rolling(14, min_periods=1).mean())
    df["roll_mean_30"] = grp.transform(lambda s: s.shift(1).rolling(30, min_periods=1).mean())
    df["roll_std_7"]   = grp.transform(lambda s: s.shift(1).rolling(7,  min_periods=2).std())
    df["expanding_mean"] = grp.transform(lambda s: s.shift(1).expanding(min_periods=1).mean())
    df["days_since_first"] = df.groupby("cluster_id").cumcount()
    return df


def make_features(panel: pd.DataFrame, static: pd.DataFrame,
                  osm: pd.DataFrame | None = None) -> pd.DataFrame:
    """Attach calendar + lag + zone-static (+ optional OSM) features to the panel.

    `static` must have cluster_id, centroid_lat, centroid_long, all_time_count,
    police_station, dominant_vehicle, top_violation.
    `osm`, if given, has cluster_id + OSM_FEATURES.
    Returns the panel with feature columns; categoricals set to category dtype.
    """
    df = _add_calendar(panel.copy())
    df = _add_lags(df)
    df = df.merge(static, on="cluster_id", how="left")

    if osm is not None and len(osm):
        df = df.merge(osm, on="cluster_id", how="left")
    for col in OSM_FEATURES:
        if col not in df.columns:
            df[col] = np.nan

    for col in FEATURE_CAT:
        df[col] = df[col].astype("category")
    return df


def feature_columns(include_osm: bool) -> list[str]:
    cols = FEATURE_NUM + FEATURE_CAT
    if include_osm:
        cols = cols + OSM_FEATURES
    return cols


# ── Baseline (seasonal-naive benchmark) ──────────────────────────────────────────

def train_baseline(train: pd.DataFrame) -> dict:
    """Historical mean count per (cluster, weekday), with a cluster-mean fallback."""
    return {
        "by_cw": train.groupby(["cluster_id", "weekday"])["count"].mean().to_dict(),
        "by_c":  train.groupby("cluster_id")["count"].mean().to_dict(),
        "global": float(train["count"].mean()),
    }


def baseline_predict(base: dict, cluster_ids, weekdays) -> np.ndarray:
    by_cw, by_c, g = base["by_cw"], base["by_c"], base["global"]
    return np.array([
        by_cw.get((cid, wd), by_c.get(cid, g))
        for cid, wd in zip(cluster_ids, weekdays)
    ], dtype=float)


# ── ML model (Poisson point + quantile band) ─────────────────────────────────────

def _estimator(**kw) -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
        min_samples_leaf=40, l2_regularization=1.0,
        categorical_features="from_dtype", random_state=RANDOM_STATE, **kw,
    )


def train_model(X: pd.DataFrame, y: pd.Series) -> dict:
    """Poisson point estimate + 10th/90th quantile models for the confidence band."""
    point = _estimator(loss="poisson").fit(X, y)
    q10   = _estimator(loss="quantile", quantile=0.1).fit(X, y)
    q90   = _estimator(loss="quantile", quantile=0.9).fit(X, y)
    return {"point": point, "q10": q10, "q90": q90}


def model_predict(models: dict, X: pd.DataFrame) -> pd.DataFrame:
    point = np.clip(models["point"].predict(X), 0, None)
    q10   = np.clip(models["q10"].predict(X),   0, None)
    q90   = np.clip(models["q90"].predict(X),   0, None)
    # The point (Poisson mean) and the independent quantile models aren't guaranteed
    # coherent, so build a band that is the WIDER of (a) the data-driven quantile
    # band — which captures over-dispersion — and (b) a Poisson-noise floor around
    # the point (±1.2816·sqrt(λ) ≈ the 10–90% interval of a Poisson). This keeps the
    # band coherent (q10 ≤ point ≤ q90) without it collapsing onto the point.
    sd = np.sqrt(point)
    q10 = np.clip(np.minimum(q10, point - 1.2816 * sd), 0, None)
    q90 = np.maximum(q90, point + 1.2816 * sd)
    return pd.DataFrame({"pred_count": point, "q10": q10, "q90": q90})


# ── Evaluation (temporal holdout, ML vs baseline) ────────────────────────────────

def _precision_at_k(day: pd.DataFrame, pred_col: str, k: int) -> float | None:
    if len(day) < k:
        return None
    top_pred = set(day.nlargest(k, pred_col)["cluster_id"])
    top_true = set(day.nlargest(k, "count")["cluster_id"])
    return len(top_pred & top_true) / k


def _ndcg_at_k(day: pd.DataFrame, pred_col: str, k: int) -> float | None:
    if len(day) < k:
        return None
    order = day.sort_values(pred_col, ascending=False).head(k)
    gains = order["count"].to_numpy(dtype=float)
    discounts = 1.0 / np.log2(np.arange(2, k + 2))
    dcg = float((gains * discounts).sum())
    ideal = np.sort(day["count"].to_numpy(dtype=float))[::-1][:k]
    idcg = float((ideal * discounts).sum())
    return dcg / idcg if idcg > 0 else 0.0


def _ranking_metrics(test: pd.DataFrame, pred_col: str) -> dict:
    from scipy.stats import spearmanr
    p10, p20, ndcg, spear = [], [], [], []
    for _, day in test.groupby("date"):
        v = _precision_at_k(day, pred_col, 10)
        if v is not None: p10.append(v)
        v = _precision_at_k(day, pred_col, 20)
        if v is not None: p20.append(v)
        v = _ndcg_at_k(day, pred_col, 10)
        if v is not None: ndcg.append(v)
        if day["count"].nunique() > 1 and day[pred_col].nunique() > 1:
            rho = spearmanr(day[pred_col], day["count"]).correlation
            if rho == rho:  # not NaN
                spear.append(rho)
    mean = lambda a: float(np.mean(a)) if a else float("nan")
    return {"precision@10": mean(p10), "precision@20": mean(p20),
            "ndcg@10": mean(ndcg), "spearman": mean(spear)}


def evaluate(test: pd.DataFrame) -> dict:
    """`test` must carry columns: count, pred_count (ML), base_count (baseline)."""
    y = test["count"].to_numpy(dtype=float)
    out = {}
    for tag, col in [("model", "pred_count"), ("baseline", "base_count")]:
        pred = np.clip(test[col].to_numpy(dtype=float), 0, None)
        out[tag] = {
            "MAE": float(mean_absolute_error(y, pred)),
            "RMSE": float(np.sqrt(np.mean((y - pred) ** 2))),
            "poisson_deviance": float(mean_poisson_deviance(y, np.clip(pred, 1e-6, None))),
            **_ranking_metrics(test, col),
        }
    base_mae, model_mae = out["baseline"]["MAE"], out["model"]["MAE"]
    out["mae_improvement_pct"] = (
        100.0 * (base_mae - model_mae) / base_mae if base_mae > 0 else 0.0
    )
    return out


# ── Next-day prediction ──────────────────────────────────────────────────────────

def predict_next_day(panel: pd.DataFrame, static: pd.DataFrame, models: dict,
                     base: dict, target_date: pd.Timestamp,
                     osm: pd.DataFrame | None = None,
                     include_osm: bool = False) -> pd.DataFrame:
    """Forecast `target_date` for every cluster.

    Appends a target-date row per cluster (placeholder count 0 — never used as its
    own feature because all lag/rolling features shift by 1), rebuilds features, and
    predicts on those rows only.
    """
    target_date = pd.Timestamp(target_date)
    cids = panel["cluster_id"].unique()
    target_rows = pd.DataFrame({"cluster_id": cids, "date": target_date, "count": 0})
    extended = pd.concat([panel, target_rows], ignore_index=True)

    feat = make_features(extended, static, osm)
    tgt = feat[feat["date"] == target_date].copy()

    X = tgt[feature_columns(include_osm)]
    preds = model_predict(models, X)
    tgt = tgt.reset_index(drop=True)
    tgt[["pred_count", "q10", "q90"]] = preds[["pred_count", "q10", "q90"]].values

    tgt["base_count"] = baseline_predict(base, tgt["cluster_id"], tgt["weekday"])
    # Recent context for the UI: mean actual over the last 7 known days per cluster.
    recent = (
        panel.sort_values("date").groupby("cluster_id")["count"]
        .apply(lambda s: s.tail(7).mean()).rename("recent_7d_mean")
    )
    tgt = tgt.merge(recent, on="cluster_id", how="left")

    tgt = tgt.sort_values("pred_count", ascending=False).reset_index(drop=True)
    tgt["pred_rank"] = np.arange(1, len(tgt) + 1)
    return tgt
