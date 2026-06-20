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
from src import enrichment

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

    auto_bucket = _hour_to_bucket(auto_hour)

    DAY_TYPES   = ["weekday", "weekend"]
    ALL_BUCKETS = list(SHIFT_DISPLAY.keys())

    # Live toggle — ON locks selectors to current IST context
    live_mode = st.toggle("🔴 Live", value=True,
                          help="ON = follow current IST time. OFF = pick any of the 10 contexts manually.")

    col_a, col_b = st.columns(2)

    if live_mode:
        st.info(
            f"🕐 Current IST: **{now.strftime('%H:%M')}** on **{now.strftime('%A')}** "
            f"→ **{auto_day_type} / {SHIFT_DISPLAY[auto_bucket]}**"
        )
        with col_a:
            st.selectbox("Day Type", DAY_TYPES,
                         index=DAY_TYPES.index(auto_day_type), disabled=True, key="live_day")
        with col_b:
            st.selectbox("Time Bucket", ALL_BUCKETS,
                         index=ALL_BUCKETS.index(auto_bucket),
                         format_func=lambda k: SHIFT_DISPLAY[k],
                         disabled=True, key="live_bucket")
        sel_day    = auto_day_type
        sel_bucket = auto_bucket
    else:
        with col_a:
            sel_day = st.selectbox("Day Type", DAY_TYPES,
                                   index=DAY_TYPES.index(auto_day_type), key="manual_day")
        with col_b:
            sel_bucket = st.selectbox("Time Bucket", ALL_BUCKETS,
                                      index=ALL_BUCKETS.index(auto_bucket),
                                      format_func=lambda k: SHIFT_DISPLAY[k],
                                      key="manual_bucket")

    context = f"{sel_day}_{sel_bucket}"

    st.subheader(f"Active Context: `{context}` — {SHIFT_DISPLAY[sel_bucket]} {'(Weekday)' if sel_day == 'weekday' else '(Weekend)'}")

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

    # ── Traffic Impact Re-Ranking (OSM-enriched) — Objective 2 ──────────────────
    st.divider()
    st.subheader("🚦 Traffic Impact Re-Ranking (OSM + Live Traffic)")
    tomtom_key = st.secrets.get("TOMTOM_API_KEY", "")
    st.caption(
        "Re-ranks the same zones by **traffic impact** — road criticality + urban "
        "context (hospitals, offices, schools, transit) + "
        + ("**live TomTom congestion**" if tomtom_key else "_(TomTom key not set)_")
        + " on top of violation volume."
    )

    if st.button("🔍 Compute Traffic Impact Scores", key="enrich_btn"):
        db.ensure_enrichment_table()
        prog = st.progress(0.0, text="Querying OpenStreetMap + TomTom…")
        enriched_rows = []
        live_rows = []
        n = len(rows)
        for i, (_, r) in enumerate(rows.iterrows()):
            key = enrichment.cell_key(r["centroid_lat"], r["centroid_long"])
            cached = db.get_enrichment([key])
            if len(cached) == 0:
                feat = enrichment.enrich_centroid(r["centroid_lat"], r["centroid_long"])
                feat["cell_key"] = key
                db.upsert_enrichment(feat)
                cached = db.get_enrichment([key])
            enriched_rows.append(cached.iloc[0])
            # Live congestion is fetched fresh every run (never cached permanently)
            live_rows.append(
                enrichment.fetch_live_traffic(r["centroid_lat"], r["centroid_long"], tomtom_key)
            )
            prog.progress((i + 1) / n, text=f"Enriched {i + 1}/{n} zones")
        prog.empty()
        live_df = pd.DataFrame(live_rows) if tomtom_key else None
        st.session_state[f"impact_{context}"] = enrichment.compute_impact(
            rows, pd.DataFrame(enriched_rows), live=live_df
        )

    if f"impact_{context}" in st.session_state:
        imp = st.session_state[f"impact_{context}"]

        has_live = "congestion_index" in imp.columns

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Avg Impact Score", f"{imp['importance'].mean():.1f}")
        k2.metric("Top Impact Zone", f"{imp.iloc[0]['importance']:.0f}/100")
        moved = int((imp["rank_change"] != 0).sum())
        k3.metric("Zones Re-Ranked", moved)
        if has_live:
            k4.metric("Avg Live Congestion", f"{imp['congestion_index'].mean()*100:.0f}%")
        else:
            k4.metric("Live Congestion", "—")

        st.caption("🗺️ Sized by Traffic Impact Score | greener = higher impact")

        def impact_popup(row):
            live_line = ""
            if has_live and pd.notna(row.get("current_speed")):
                live_line = (
                    f"Live: {row['congestion_index']*100:.0f}% congestion "
                    f"({int(row['current_speed'])}/{int(row['free_flow_speed'])} kmph)<br>"
                )
            return (
                f"<b>🚦 Impact #{int(row['new_rank'])} — {row['zone']}</b><br>"
                f"Impact score: {row['importance']:.1f}/100<br>"
                f"Was enforcement priority: #{int(row['context_rank'])}<br>"
                f"Road: {row['road_type'] or '—'} ({int(row['lane_count']) if pd.notna(row['lane_count']) else '?'} lanes)<br>"
                f"Hospitals: {int(row['hospital_count'])} | Offices: {int(row['office_count'])} | "
                f"Transit: {int(row['railway_station_count'])}<br>"
                f"{live_line}"
                f"Why: {row['reason']}"
            )

        m_imp = make_map(imp, "importance", "green", impact_popup)
        st_folium(m_imp, width=1200, height=550)

        def arrow(c):
            return f"▲ {c}" if c > 0 else (f"▼ {abs(c)}" if c < 0 else "—")

        out = imp.copy()
        out["Δ Rank"] = out["rank_change"].apply(arrow)
        out["live_congestion"] = out["live_congestion"].apply(
            lambda v: f"{float(v)*100:.0f}%" if isinstance(v, (int, float)) else v
        )
        show = out[[
            "new_rank", "context_rank", "Δ Rank", "zone",
            "road_type", "lane_count", "hospital_count", "office_count",
            "railway_station_count", "road_criticality", "urban_activity",
            "live_congestion", "importance", "reason",
        ]].copy()
        show.columns = [
            "Impact Rank", "Old Priority", "Δ Rank", "Zone",
            "Road Type", "Lanes", "Hospitals", "Offices",
            "Transit", "Road Crit.", "Urban Act.",
            "Live Cong.", "Impact Score", "Why",
        ]
        st.dataframe(show, use_container_width=True, hide_index=True)
    else:
        st.info("Click the button above to fetch OSM context + live TomTom congestion and compute traffic impact scores for these zones.")
