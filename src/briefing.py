"""
Feature 1 — Smart Briefing Generator.

Pure template-based natural-language generation from a single action-hotspot dict
(see src/action.build_action_hotspots). No LLM call: deterministic, instant, and
fully offline. Produces a shift-ready operational paragraph per hotspot.
"""

from datetime import datetime

import pytz

IST = pytz.timezone("Asia/Kolkata")


def current_time_period(now: datetime | None = None) -> str:
    """Human label for the current shift window, e.g. 'weekday morning peak'.

    Derived dynamically from the day-of-week and hour (IST). Time buckets mirror
    the pipeline's own bins so the briefing language matches the enforcement
    context the hotspots were ranked under.
    """
    if now is None:
        now = datetime.now(IST)
    day = "weekend" if now.weekday() >= 5 else "weekday"
    h = now.hour
    if h < 6:
        bucket = "night"
    elif h < 9:
        bucket = "morning peak"
    elif h < 14:
        bucket = "midday"
    elif h < 20:
        bucket = "evening peak"
    else:
        bucket = "late-night"
    return f"{day} {bucket}"


def _congestion_clause(congestion) -> str:
    if congestion is None:
        return "Live congestion data is not currently available for this corridor."
    return f"Current congestion is estimated at {congestion}%."


def _corridor_clause(hotspot: dict) -> str:
    road = hotspot.get("road_type")
    road_label = (str(road).replace("_", " ") if road else "local") + " road"
    hospitals = hotspot.get("hospital_count", 0) or 0
    offices = hotspot.get("office_count", 0) or 0
    return (
        f"The hotspot lies on a {road_label} corridor serving "
        f"{hospitals} hospital{'s' if hospitals != 1 else ''} and "
        f"{offices} office{'s' if offices != 1 else ''}."
    )


def generate_briefing(hotspot: dict, now: datetime | None = None) -> str:
    """Return a plain-English operational briefing paragraph for one hotspot."""
    period = current_time_period(now)
    location = hotspot.get("location", "This location")
    historical = hotspot.get("historical_violations", 0)
    recent = hotspot.get("recent_violations", 0)
    action = hotspot.get("recommended_action", "Routine monitoring sufficient")

    spike_note = ""
    if hotspot.get("spike_ratio", 0) and hotspot["spike_ratio"] > 1.2:
        spike_note = (
            " Recent activity is running above its historical baseline, "
            "indicating an escalating trend."
        )

    return (
        f"{location} has been identified as the highest-priority enforcement "
        f"location for the current {period} period. "
        f"The area recorded {historical} historical parking violations and "
        f"{recent} violations during the last 30 days. "
        f"{_corridor_clause(hotspot)} "
        f"{_congestion_clause(hotspot.get('congestion'))}"
        f"{spike_note} "
        f"{action}."
    )


def briefing_filename(hotspot: dict, now: datetime | None = None) -> str:
    """Safe .txt filename: location + date, e.g. 'Madiwala_Junction_2026-06-20.txt'."""
    if now is None:
        now = datetime.now(IST)
    raw = str(hotspot.get("location", "hotspot"))
    safe = "".join(c if c.isalnum() else "_" for c in raw).strip("_")
    safe = "_".join(filter(None, safe.split("_")))  # collapse repeats
    return f"{safe}_{now.strftime('%Y-%m-%d')}.txt"
