import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from pathlib import Path
from datetime import datetime
import pytz

DB_PATH = "data/hotspots.db"

st.set_page_config(page_title="ParkSense AI", layout="wide")
st.title("🚔 ParkSense AI")
st.caption("AI-Driven Parking Intelligence & Enforcement Dashboard")

if not Path(DB_PATH).exists():
    st.error("Database not found. Run `python build_hotspots.py` first.")
    st.stop()

from src import db

MONTH_LABEL   = db.MONTH_LABELS
SHIFT_DISPLAY = db.SHIFT_DISPLAY


# ── Map helper ────────────────────────────────────────────────────────────────

def make_map(rows, size_col, color, popup_fn):
    m = folium.Map(location=[12.97, 77.59], zoom_start=12)
    for _, row in rows.iterrows():
        if pd.isna(row.get("centroid_lat")):
            continue
        folium.CircleMarker(
            location=[row["centroid_lat"], row["centroid_long"]],
            radius=max(6, min(28, row[size_col] / 40)),
            color=color, fill=True, fill_opacity=0.75,
            popup=folium.Popup(popup_fn(row), max_width=280),
        ).add_to(m)
    return m


def zone(row):
    return f"{row['police_station']} / {row.get('nearest_junction') or '—'}"


# ── Sidebar (Violation Explorer filters only) ─────────────────────────────────

st.sidebar.header("Violation Explorer Filters")
stations      = db.get_police_stations()
sel_station   = st.sidebar.selectbox("Police Station", stations)
sel_hour      = st.sidebar.slider("Hour of Day", 0, 23, 22)
min_date      = pd.to_datetime(db.get_meta("min_date")).date()
max_date      = pd.to_datetime(db.get_meta("max_date")).date()
sel_date      = st.sidebar.date_input("Date", value=max_date,
                                       min_value=min_date, max_value=max_date)

# ── Tabs ──────────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4 = st.tabs([
    "Violation Explorer",
    "Persistent Hotspots",
    "Monthly Hotspots",
    "Live Enforcement",
])


# ════════════════════════════════════════════════════════════════════════════════
# TAB 1 — Violation Explorer
# ════════════════════════════════════════════════════════════════════════════════
with tab1:
    violations = db.get_violations(sel_station, sel_hour, sel_date)

    c1, c2, c3 = st.columns(3)
    c1.metric("Violations This Hour", len(violations))
    c2.metric("Total Clusters (Persistent)", db.get_total_clusters())
    c3.metric("Total Dataset Records", db.get_total_violations())

    st.subheader("🗺️ Violation Map")
    center = (
        [violations["latitude"].mean(), violations["longitude"].mean()]
        if len(violations) > 0 else [12.9716, 77.5946]
    )
    m = folium.Map(location=center, zoom_start=13)
    for _, row in violations.iterrows():
        folium.CircleMarker(
            location=[row["latitude"], row["longitude"]],
            radius=4, color="blue", fill=True, fill_color="blue", fill_opacity=0.7,
            popup=folium.Popup(
                f"<b>Vehicle:</b> {row['vehicle_type']}<br>"
                f"<b>Violation:</b> {row['primary_violation']}<br>"
                f"<b>Station:</b> {row['police_station']}<br>"
                f"<b>Hour:</b> {row['hour']}<br>"
                f"<b>Junction:</b> {row['junction_name']}",
                max_width=300,
            ),
        ).add_to(m)
    st_folium(m, width=1200, height=600)

    hourly = db.get_hourly_trend(sel_station).set_index("hour")["count"]
    st.subheader("Violations by Hour")
    st.line_chart(hourly)

    st.subheader("Sample Violation Records")
    st.dataframe(violations.head(20), use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════════
# TAB 2 — Persistent Hotspots
# ════════════════════════════════════════════════════════════════════════════════
with tab2:
    with st.expander("Filter by Police Station (optional)"):
        p_station = st.selectbox("Station", ["All"] + stations, key="p_st")
        p_filter  = None if p_station == "All" else p_station

    rows = db.get_persistent_hotspots(police_station=p_filter, limit=20)

    c1, c2 = st.columns(2)
    c1.metric("Clusters Shown", len(rows))
    c2.metric("Violations in Top 20", int(rows["all_time_count"].sum()) if len(rows) else 0)

    st.subheader("🗺️ Persistent Hotspot Map")
    st.caption("🔴 HDBSCAN clusters on full dataset (Nov 2023 – Apr 2024) | Size = violation count")

    def persistent_popup(row):
        return (
            f"<b>🔴 #{int(row['persistent_rank'])} — {zone(row)}</b><br>"
            f"All-time violations: {int(row['all_time_count'])}<br>"
            f"Top violation: {row['top_violation']}<br>"
            f"Peak hour: {row['peak_hour']}<br>"
            f"Dominant vehicle: {row['dominant_vehicle']}"
        )

    m = make_map(rows, "all_time_count", "red", persistent_popup)
    st_folium(m, width=1200, height=550)

    st.subheader("🔴 Persistent Hotspot Rankings")
    display = rows[[
        "persistent_rank", "police_station", "nearest_junction",
        "all_time_count", "top_violation", "peak_hour"
    ]].copy()
    display.columns = ["Rank", "Police Station", "Junction",
                        "All-Time Violations", "Top Violation", "Peak Hour"]
    st.dataframe(display, use_container_width=True, hide_index=True)


# ════════════════════════════════════════════════════════════════════════════════
# TAB 3 — Monthly Hotspots
# ════════════════════════════════════════════════════════════════════════════════
with tab3:
    available_yms  = db.get_available_year_months()
    month_options  = {MONTH_LABEL.get(ym, ym): ym for ym in available_yms}
    sel_label      = st.selectbox("Month", list(month_options.keys()),
                                   index=len(month_options) - 1, key="m_month")
    sel_ym         = month_options[sel_label]

    with st.expander("Filter by Police Station (optional)"):
        m_station = st.selectbox("Station", ["All"] + stations, key="m_st")
        m_filter  = None if m_station == "All" else m_station

    rows = db.get_monthly_hotspots(sel_ym, police_station=m_filter, limit=20)

    c1, c2 = st.columns(2)
    c1.metric("Active Clusters", len(rows))
    c2.metric(f"Violations in {sel_label}", int(rows["violation_count"].sum()) if len(rows) else 0)

    st.subheader(f"🗺️ Monthly Hotspot Map — {sel_label}")
    st.caption(f"🟣 HDBSCAN clusters on {sel_label} violations only | Size = monthly violation count")

    def monthly_popup(row):
        return (
            f"<b>🟣 #{int(row['monthly_rank'])} — {zone(row)}</b><br>"
            f"Month: {sel_label}<br>"
            f"Violations: {int(row['violation_count'])}<br>"
            f"Top violation: {row['top_violation']}<br>"
            f"Peak hour: {row['peak_hour']}"
        )

    m = make_map(rows, "violation_count", "purple", monthly_popup)
    st_folium(m, width=1200, height=550)

    st.subheader(f"🟣 Top Hotspots — {sel_label}")
    display = rows[[
        "monthly_rank", "police_station", "nearest_junction",
        "violation_count", "top_violation", "peak_hour"
    ]].copy()
    display.columns = ["Rank", "Police Station", "Junction",
                        "Violations", "Top Violation", "Peak Hour"]
    st.dataframe(display, use_container_width=True, hide_index=True)


# ════════════════════════════════════════════════════════════════════════════════
# TAB 4 — Live Enforcement
# ════════════════════════════════════════════════════════════════════════════════
with tab4:
    # Auto-detect current Bengaluru time
    IST = pytz.timezone("Asia/Kolkata")
    now = datetime.now(IST)
    auto_day_type = "weekend" if now.weekday() >= 5 else "weekday"
    auto_hour     = now.hour

    def _hour_to_bucket(hour):
        if   hour < 6:  return "night"
        elif hour < 9:  return "morning_peak"
        elif hour < 14: return "midday"
        elif hour < 20: return "evening_peak"
        else:           return "late_night"

    auto_bucket  = _hour_to_bucket(auto_hour)
    auto_context = f"{auto_day_type}_{auto_bucket}"

    st.info(
        f"🕐 Current IST: **{now.strftime('%H:%M')}** on **{now.strftime('%A')}** "
        f"→ Context: `{auto_context}`"
    )

    use_override = st.checkbox("Override time (for demo)")
    if use_override:
        col_a, col_b = st.columns(2)
        with col_a:
            ov_day = st.selectbox("Day Type", ["weekday", "weekend"])
        with col_b:
            ov_bucket = st.selectbox("Time Bucket", list(SHIFT_DISPLAY.keys()),
                                      format_func=lambda k: SHIFT_DISPLAY[k])
        context = f"{ov_day}_{ov_bucket}"
    else:
        context = auto_context

    st.subheader(f"Active Context: `{context}`")

    rows = db.get_contextual_hotspots(context, limit=20)

    if len(rows) == 0:
        st.warning("No contextual hotspot data for this context. Re-run `build_hotspots.py`.")
        st.stop()

    c1, c2, c3 = st.columns(3)
    c1.metric("Enforcement Zones", len(rows))
    c2.metric("Historical Violations (this context)", int(rows["violation_count"].sum()))
    c3.metric("Recent Violations (last 30d)", int(rows["recent_count"].sum()))

    st.subheader("🗺️ Recommended Enforcement Locations")
    st.caption("🟠 Ranked by enforcement score (60% historical + 40% recent activity) | Size = violation count")

    def enforcement_popup(row):
        return (
            f"<b>🟠 Priority #{int(row['context_rank'])} — {zone(row)}</b><br>"
            f"Context: {context}<br>"
            f"Historical violations: {int(row['violation_count'])}<br>"
            f"Last 30d violations: {int(row['recent_count'])}<br>"
            f"Enforcement score: {row['enforcement_score']:.3f}<br>"
            f"Top violation: {row['top_violation']}<br>"
            f"Peak hour: {row['peak_hour']}"
        )

    m = make_map(rows, "violation_count", "orange", enforcement_popup)
    st_folium(m, width=1200, height=550)

    st.subheader("📋 Enforcement Priority Queue")
    display = rows[[
        "context_rank", "police_station", "nearest_junction",
        "violation_count", "recent_count", "enforcement_score",
        "top_violation", "peak_hour"
    ]].copy()
    display.columns = [
        "Priority", "Police Station", "Junction",
        "Historical", "Last 30d", "Score",
        "Top Violation", "Peak Hour"
    ]
    st.dataframe(display, use_container_width=True, hide_index=True)
