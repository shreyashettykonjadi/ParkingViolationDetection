import numpy as np
import hdbscan


def run_hdbscan(df, min_cluster_size=30, min_samples=5):
    """Assign each violation a cluster_id. Noise points receive -1."""
    coords = np.radians(df[["latitude", "longitude"]].values)
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        metric="haversine",
        cluster_selection_method="eom",
    )
    return clusterer.fit_predict(coords)
