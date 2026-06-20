"""
Police station registry — patrol origins for Dispatch Mode (Feature 3).

Loaded from data/police_stations.csv so the real coordinate source can be swapped
in later without touching any UI logic. The CSV ships with one row per Bengaluru
traffic police station and a representative lat/long (currently the centroid of
that station's recorded violations — replace with surveyed station coordinates
when available).
"""

from functools import lru_cache
from pathlib import Path

import pandas as pd

STATIONS_CSV = "data/police_stations.csv"

# A sensible default subset of "active" units pre-selected in the dispatch panel.
# These span the city (south, central, east, north, west) so the demo always
# shows a spread of origins. Edit freely — any station name in the CSV is valid.
DEFAULT_ACTIVE_UNITS = [
    "Madiwala", "City Market", "K.R. Pura", "Shivajinagar",
    "Whitefield", "Malleshwaram", "HSR Layout",
]


@lru_cache(maxsize=1)
def load_stations() -> pd.DataFrame:
    """Return the station registry as a DataFrame: station, latitude, longitude."""
    path = Path(STATIONS_CSV)
    if not path.exists():
        return pd.DataFrame(columns=["station", "latitude", "longitude"])
    df = pd.read_csv(path)
    df["station"] = df["station"].astype(str).str.strip()
    return df.dropna(subset=["latitude", "longitude"]).reset_index(drop=True)


def station_names() -> list[str]:
    return load_stations()["station"].tolist()


def station_coords(name: str) -> tuple[float, float] | None:
    """(latitude, longitude) for a station name, or None if unknown."""
    df = load_stations()
    hit = df[df["station"] == name]
    if len(hit) == 0:
        return None
    r = hit.iloc[0]
    return float(r["latitude"]), float(r["longitude"])


def default_active_units() -> list[str]:
    """DEFAULT_ACTIVE_UNITS intersected with what's actually in the registry."""
    available = set(station_names())
    return [s for s in DEFAULT_ACTIVE_UNITS if s in available]
