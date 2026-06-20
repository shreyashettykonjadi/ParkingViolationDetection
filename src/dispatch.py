"""
Feature 3 — TomTom Patrol Routing (Dispatch Mode).

Converts hotspot priorities into a concrete dispatch plan. Three concerns:

  1. compute_travel_matrix() — TomTom Matrix Routing API v2 (one batched POST) to
     get the unit×hotspot travel-time matrix used for assignment.
  2. greedy_assign()        — assign the nearest free unit to the highest-priority
     hotspot first, then the next, until units or hotspots run out.
  3. calculate_route()      — TomTom Calculate Route API (GET) for the FINAL
     assigned pairs only: traffic-aware ETA + polyline geometry for the map.

Every network call degrades gracefully to a haversine estimate (~25 km/h urban
speed) so the dispatch table always renders, with the caller surfacing a warning
that the figure is an estimate, not live data.
"""

import math

import requests

MATRIX_URL = "https://api.tomtom.com/routing/matrix/2"
CALC_ROUTE_URL = "https://api.tomtom.com/routing/1/calculateRoute/{locations}/json"

URBAN_SPEED_KMPH = 25.0    # fallback average speed
DETOUR_FACTOR = 1.3        # straight-line → road-distance fudge factor


# ── Coordinate validation ────────────────────────────────────────────────────────

def valid_coord(lat, lng) -> bool:
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return False
    if math.isnan(lat) or math.isnan(lng):
        return False
    return -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0


def _loc_string(o_lat, o_lng, d_lat, d_lng) -> str:
    """Colon-separated 'lat,lng:lat,lng' path segment for Calculate Route.

    Raises ValueError on malformed coordinates so the caller can fall back before
    TomTom returns a 400.
    """
    if not (valid_coord(o_lat, o_lng) and valid_coord(d_lat, d_lng)):
        raise ValueError(f"Malformed coordinates: {o_lat},{o_lng}:{d_lat},{d_lng}")
    return f"{float(o_lat)},{float(o_lng)}:{float(d_lat)},{float(d_lng)}"


# ── Haversine fallback ───────────────────────────────────────────────────────────

def haversine_km(a_lat, a_lng, b_lat, b_lng) -> float:
    R = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dphi = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def haversine_estimate(o_lat, o_lng, d_lat, d_lng) -> dict:
    """Straight-line ETA/distance estimate (with a road-detour factor)."""
    straight = haversine_km(o_lat, o_lng, d_lat, d_lng)
    road_km = straight * DETOUR_FACTOR
    eta_sec = (road_km / URBAN_SPEED_KMPH) * 3600.0
    return {
        "eta_sec": eta_sec,
        "distance_m": road_km * 1000.0,
        "points": [(float(o_lat), float(o_lng)), (float(d_lat), float(d_lng))],
        "source": "estimate",
    }


# ── 1. Matrix Routing API v2 ─────────────────────────────────────────────────────

def compute_travel_matrix(origins: list[tuple[float, float]],
                          destinations: list[tuple[float, float]],
                          api_key: str, timeout: int = 30) -> dict:
    """
    origins / destinations : lists of (lat, lng).
    Returns {"matrix": [[sec|None]], "source": "tomtom"|"estimate", "warning": str|None}
    matrix[i][j] = travel time (seconds) from origin i to destination j.

    A single TomTom Matrix Routing v2 POST is attempted; on any failure the whole
    matrix falls back to haversine estimates.
    """
    n_o, n_d = len(origins), len(destinations)

    def _full_estimate(reason: str) -> dict:
        matrix = [
            [haversine_estimate(*origins[i], *destinations[j])["eta_sec"]
             for j in range(n_d)]
            for i in range(n_o)
        ]
        return {"matrix": matrix, "source": "estimate", "warning": reason}

    if not api_key:
        return _full_estimate("No TomTom API key set — ETAs are haversine estimates.")
    if any(not valid_coord(*o) for o in origins) or \
       any(not valid_coord(*d) for d in destinations):
        return _full_estimate("Some coordinates were invalid — ETAs are estimates.")

    body = {
        "origins": [{"point": {"latitude": la, "longitude": lo}} for la, lo in origins],
        "destinations": [{"point": {"latitude": la, "longitude": lo}}
                         for la, lo in destinations],
        "options": {"routeType": "fastest", "traffic": "live", "travelMode": "car"},
    }
    try:
        resp = requests.post(
            MATRIX_URL, params={"key": api_key}, json=body, timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            return _full_estimate("TomTom matrix returned no data — using estimates.")

        matrix = [[None] * n_d for _ in range(n_o)]
        for cell in data:
            i = cell.get("originIndex")
            j = cell.get("destinationIndex")
            summ = cell.get("routeSummary") or {}
            tt = summ.get("travelTimeInSeconds")
            if i is not None and j is not None and tt is not None:
                matrix[i][j] = float(tt)
        # Backfill any holes (per-pair routing failures) with estimates.
        for i in range(n_o):
            for j in range(n_d):
                if matrix[i][j] is None:
                    matrix[i][j] = haversine_estimate(
                        *origins[i], *destinations[j])["eta_sec"]
        return {"matrix": matrix, "source": "tomtom", "warning": None}
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        return _full_estimate(f"TomTom matrix call failed ({type(exc).__name__}) "
                              "— ETAs are haversine estimates.")


# ── 2. Greedy nearest-assignment ─────────────────────────────────────────────────

def greedy_assign(matrix: list[list[float]], n_origins: int, n_dest: int) -> list[dict]:
    """
    Assign the closest free unit to the highest-priority hotspot first, remove
    both from the pool, repeat. Destinations are assumed already priority-ordered
    (index 0 = top priority).

    Returns a list of {"dest_index", "origin_index", "matrix_eta_sec"}, one per
    assigned hotspot, in priority order. Stops when units or hotspots run out.
    """
    available = set(range(n_origins))
    assignments = []
    for j in range(n_dest):
        if not available:
            break
        best_i = min(available, key=lambda i: matrix[i][j])
        assignments.append({
            "dest_index": j,
            "origin_index": best_i,
            "matrix_eta_sec": matrix[best_i][j],
        })
        available.discard(best_i)
    return assignments


# ── 3. Calculate Route API (GET) — final assigned pairs only ─────────────────────

def calculate_route(o_lat, o_lng, d_lat, d_lng, api_key, timeout: int = 20) -> dict:
    """
    Traffic-aware route for ONE origin→destination pair.
    Returns {"eta_sec", "distance_m", "points": [(lat,lng), ...], "source"}.

    Falls back to a haversine estimate (straight polyline) on any failure. The
    function is intentionally structured around a single GET so it can later be
    switched to the POST variant (avoidance zones / alternatives) without changing
    its return contract.
    """
    if not api_key:
        return haversine_estimate(o_lat, o_lng, d_lat, d_lng)
    try:
        locations = _loc_string(o_lat, o_lng, d_lat, d_lng)  # validates coords
    except ValueError:
        return haversine_estimate(o_lat, o_lng, d_lat, d_lng)

    url = CALC_ROUTE_URL.format(locations=locations)
    try:
        resp = requests.get(
            url,
            params={"key": api_key, "routeType": "fastest", "traffic": "true"},
            timeout=timeout,
        )
        resp.raise_for_status()
        route = resp.json()["routes"][0]
        summary = route["summary"]
        points = [
            (float(p["latitude"]), float(p["longitude"]))
            for p in route["legs"][0]["points"]
        ]
        return {
            "eta_sec": float(summary["travelTimeInSeconds"]),
            "distance_m": float(summary["lengthInMeters"]),
            "points": points or [(o_lat, o_lng), (d_lat, d_lng)],
            "source": "tomtom",
        }
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        return haversine_estimate(o_lat, o_lng, d_lat, d_lng)


# ── Formatting helpers ───────────────────────────────────────────────────────────

def fmt_eta(seconds) -> str:
    if seconds is None:
        return "—"
    minutes = round(seconds / 60.0)
    return f"{minutes} min" if minutes >= 1 else "<1 min"


def fmt_distance(meters) -> str:
    if meters is None:
        return "—"
    return f"{meters / 1000.0:.1f} km"
