import numpy as np
import pandas as pd
import hdbscan

SHIFT_BINS = [0, 6, 9, 14, 20, 24]
SHIFT_LABELS = ["night", "morning_peak", "midday", "evening_peak", "late_night"]


def assign_shift(hour_series):
    return pd.cut(hour_series, bins=SHIFT_BINS, labels=SHIFT_LABELS, right=False)


def assign_day_type(weekday_series):
    return weekday_series.apply(lambda x: "weekend" if x >= 5 else "weekday")


def run_hdbscan_for_group(group_df, min_cluster_size=15, min_samples=5):
    coords = np.radians(group_df[["latitude", "longitude"]].values)
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        metric="haversine",
        cluster_selection_method="eom"
    )
    group_df = group_df.copy()
    group_df["cluster_id"] = clusterer.fit_predict(coords)
    group_df["cluster_prob"] = clusterer.probabilities_
    return group_df


def compute_observation_confidence(df):
    valid = df[df["cluster_id"] != -1]
    conf = (
        valid.groupby(["temporal_group", "cluster_id"])
        .agg(
            unique_devices=("device_id", "nunique"),
            unique_officers=("created_by_id", "nunique"),
            total=("cluster_id", "count")
        )
        .reset_index()
    )
    # Diversity of reporting sources relative to cluster size — high = real hotspot, low = patrol artifact
    conf["raw_conf"] = (conf["unique_devices"] + conf["unique_officers"]) / (2 * np.log1p(conf["total"]))
    conf["observation_confidence"] = conf["raw_conf"].rank(pct=True).round(3)
    return conf[["temporal_group", "cluster_id", "observation_confidence"]]


def _mode(series):
    counts = series.dropna().value_counts()
    return counts.index[0] if len(counts) > 0 else None


def build_cluster_summary(df):
    valid = df[df["cluster_id"] != -1]
    summary = (
        valid.groupby(["temporal_group", "day_type", "shift", "cluster_id"], observed=True)
        .agg(
            violation_count=("cluster_id", "count"),
            centroid_lat=("latitude", "mean"),
            centroid_long=("longitude", "mean"),
            top_violation=("primary_violation", _mode),
            peak_hour=("hour", _mode),
            dominant_vehicle=("vehicle_type", _mode),
            nearest_junction=("junction_name", _mode),
            police_station=("police_station", _mode),
        )
        .reset_index()
    )
    return summary


def run_full_pipeline(df, min_cluster_size=15):
    df = df.copy()
    df["shift"] = assign_shift(df["hour"])
    df["day_type"] = assign_day_type(df["weekday"])
    df["temporal_group"] = df["day_type"] + "_" + df["shift"].astype(str)

    results = []
    for _group_key, group in df.groupby(["day_type", "shift"], observed=True):
        if len(group) < min_cluster_size:
            continue
        group = run_hdbscan_for_group(group, min_cluster_size=min_cluster_size)
        results.append(group)

    df_clustered = pd.concat(results, ignore_index=True)

    summary = build_cluster_summary(df_clustered)
    confidence = compute_observation_confidence(df_clustered)
    summary = summary.merge(confidence, on=["temporal_group", "cluster_id"], how="left")
    summary["observation_confidence"] = summary["observation_confidence"].fillna(0.5)

    summary["risk_score"] = (
        summary["violation_count"].rank(pct=True) * 0.6
        + summary["observation_confidence"] * 0.4
    ).round(3)
    summary = summary.sort_values("risk_score", ascending=False).reset_index(drop=True)

    return summary, df_clustered
