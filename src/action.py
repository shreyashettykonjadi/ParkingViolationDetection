"""
Action layer — turns raw contextual hotspots into a ranked, decision-ready list.

This module is the single source of truth shared by the three "action" views:
  • Command Center  (Feature 2) — priority cards
  • Briefings       (Feature 1) — plain-English summaries
  • Patrol Dispatch (Feature 3) — TomTom routing targets

`build_action_hotspots()` returns a list of plain dicts (not a DataFrame) so the
structure is trivial to serialise to session_state and swap for a real data
source later. Each dict carries everything the UI needs — no view recomputes
scores.

Enrichment (road type, hospital/office counts, live congestion) is *optional*:
if the Live Enforcement tab has already computed a traffic-impact DataFrame for
this context, those fields are folded in; otherwise the layer still works off the
violation/enforcement signal alone and simply marks the context fields unknown.
"""

import pandas as pd

# OSM highway classes we treat as a "primary" arterial corridor.
PRIMARY_ROADS = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link",
}

# Severity cut-offs on the 0–100 priority (traffic-impact) score.
SEVERITY_HIGH = 66
SEVERITY_MEDIUM = 40

RECOMMENDED_ACTION = {
    "HIGH":   "Immediate patrol deployment is recommended",
    "MEDIUM": "Increased monitoring advised",
    "LOW":    "Routine monitoring sufficient",
}

SEVERITY_COLORS = {  # used by the Command Center cards
    "HIGH":   "#e53935",   # red
    "MEDIUM": "#fb8c00",   # amber
    "LOW":    "#43a047",   # green
}


def _severity(score: float) -> str:
    if score >= SEVERITY_HIGH:
        return "HIGH"
    if score >= SEVERITY_MEDIUM:
        return "MEDIUM"
    return "LOW"


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def build_action_hotspots(rows: pd.DataFrame, context: str,
                          window_days: int = 181,
                          impact_df: pd.DataFrame | None = None) -> list[dict]:
    """
    rows        : contextual hotspots (db.get_contextual_hotspots), already ranked.
    context     : e.g. "weekday_evening_peak" — carried through for traceability.
    window_days : span of the historical dataset, used to judge a recent spike.
    impact_df   : optional output of enrichment.compute_impact for this context
                  (adds road_type / hospital_count / office_count / congestion).

    Returns a list of dicts sorted by priority score (descending), each with a
    1-based `priority_rank`.
    """
    if rows is None or len(rows) == 0:
        return []

    # Index enrichment by cluster_id for an O(1) lookup per hotspot.
    enrich_by_cid: dict = {}
    if impact_df is not None and len(impact_df) and "cluster_id" in impact_df.columns:
        for _, e in impact_df.iterrows():
            enrich_by_cid[e["cluster_id"]] = e

    expected_recent_frac = min(1.0, 30.0 / max(window_days, 30))

    out: list[dict] = []
    for _, r in rows.iterrows():
        enforcement_norm = _clip01(float(r.get("enforcement_score") or 0.0))
        historical = int(r.get("violation_count") or 0)
        recent = int(r.get("recent_count") or 0)

        # Recent-activity momentum: how does the last-30d count compare to what a
        # flat distribution over the full window would predict?
        expected_recent = max(1.0, historical * expected_recent_frac)
        spike = recent / expected_recent
        momentum = _clip01(spike / 2.0)          # 2× on-trend → full momentum

        # Optional enrichment fields.
        e = enrich_by_cid.get(r.get("cluster_id"))
        road_type = office_count = hospital_count = None
        congestion = None  # int percent, or None when no live data
        traffic_impact = round(100 * enforcement_norm, 0)
        if e is not None:
            road_type = e.get("road_type")
            office_count = int(e.get("office_count") or 0)
            hospital_count = int(e.get("hospital_count") or 0)
            if "importance" in e and pd.notna(e.get("importance")):
                traffic_impact = round(float(e["importance"]), 0)
            ci = e.get("congestion_index")
            if ci is not None and pd.notna(ci) and pd.notna(e.get("current_speed")):
                congestion = int(round(float(ci) * 100))

        traffic_impact = int(traffic_impact)
        forecast_risk = int(round(100 * _clip01(0.5 * enforcement_norm + 0.5 * momentum)))
        severity = _severity(traffic_impact)

        # "Why this location?" — only factors that actually apply.
        factors = []
        if enforcement_norm >= 0.6:
            factors.append("High violations")
        if congestion is not None and congestion > 60:
            factors.append("High congestion")
        if road_type in PRIMARY_ROADS:
            factors.append("Primary road")
        if hospital_count and hospital_count > 0:
            factors.append("Hospital corridor")
        if spike > 1.2:
            factors.append("Recent activity spike")

        junction = r.get("nearest_junction")
        junction = None if (pd.isna(junction) or str(junction).strip().lower()
                            in ("", "nan", "no junction")) else str(junction)
        location = f"{r.get('police_station')}" + (f" / {junction}" if junction else "")

        out.append({
            "context": context,
            "cluster_id": r.get("cluster_id"),
            "location": location,
            "police_station": str(r.get("police_station")),
            "nearest_junction": junction,
            "centroid_lat": float(r["centroid_lat"]),
            "centroid_long": float(r["centroid_long"]),
            "historical_violations": historical,
            "recent_violations": recent,
            "road_type": road_type,
            "hospital_count": hospital_count if hospital_count is not None else 0,
            "office_count": office_count if office_count is not None else 0,
            "congestion": congestion,            # int % or None (no live data)
            "traffic_impact": traffic_impact,    # 0–100 priority score
            "forecast_risk": forecast_risk,      # 0–100 %
            "severity": severity,
            "recommended_action": RECOMMENDED_ACTION[severity],
            "why_factors": factors,
            "spike_ratio": round(spike, 2),
            "enforcement_score": enforcement_norm,
            "peak_hour": r.get("peak_hour"),
            "dominant_vehicle": r.get("dominant_vehicle"),
            "top_violation": r.get("top_violation"),
            "enriched": e is not None,
        })

    out.sort(key=lambda h: h["traffic_impact"], reverse=True)
    for i, h in enumerate(out, start=1):
        h["priority_rank"] = i
    return out
