import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from pathlib import Path
from datetime import datetime, timezone
import pytz

DB_PATH = "data/hotspots.db"

st.set_page_config(page_title="ParkSense AI", layout="wide", page_icon="🚔")

# ── Light UI polish ────────────────────────────────────────────────────────────
st.markdown("""
<style>
    /* ── Sidebar shell ── */
    section[data-testid="stSidebar"] { background: #0e1117; }
    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3,
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] span { color: #dfe3ea !important; }
    section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p,
    section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] span {
        color: #8b93a3 !important;
    }
    section[data-testid="stSidebar"] hr { border-color: #232b3d; }

    /* ── Bubble navigation buttons ── */
    section[data-testid="stSidebar"] .stButton > button {
        border-radius: 999px;
        justify-content: flex-start;
        text-align: left;
        padding: 9px 18px;
        font-weight: 500;
        margin-bottom: 4px;
        transition: all .15s ease;
    }
    /* unselected = dark pill */
    section[data-testid="stSidebar"] .stButton > button[kind="secondary"],
    section[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"] {
        background: #1b2130; color: #cfd5e0; border: 1px solid #2a3142;
    }
    section[data-testid="stSidebar"] .stButton > button[kind="secondary"]:hover,
    section[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"]:hover {
        background: #232b3d; color: #fff; border-color: #3a4258;
    }
    /* selected = accent pill */
    section[data-testid="stSidebar"] .stButton > button[kind="primary"],
    section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] {
        background: linear-gradient(90deg, #ff4b4b, #ff6a3d);
        color: #fff; border: none; font-weight: 600;
    }

    /* ── Metric cards ── */
    div[data-testid="stMetric"] {
        background: #f7f8fa; border: 1px solid #e6e8eb;
        border-radius: 12px; padding: 14px 16px;
    }
    div[data-testid="stMetricValue"] { font-size: 1.6rem; }
    h1, h2, h3 { letter-spacing: -0.01em; }
</style>
""", unsafe_allow_html=True)

if not Path(DB_PATH).exists():
    st.error("Database not found. Run `python build_hotspots.py` first.")
    st.stop()

from src import db
from src import enrichment

MONTH_LABEL   = db.MONTH_LABELS
SHIFT_DISPLAY = db.SHIFT_DISPLAY

# Shared station list (NaN-cleaned in db.get_police_stations)
STATIONS = db.get_police_stations()


# ════════════════════════════════════════════════════════════════════════════════
# Map helpers
# ════════════════════════════════════════════════════════════════════════════════

def _fit(m, pts):
    """Fit the map view to the cluster bounds (single point → gentle zoom)."""
    if len(pts) == 0:
        return
    lat, lon = pts["centroid_lat"], pts["centroid_long"]
    if len(pts) == 1:
        m.location = [float(lat.iloc[0]), float(lon.iloc[0])]
        m.zoom_start = 15
        return
    m.fit_bounds(
        [[lat.min(), lon.min()], [lat.max(), lon.max()]],
        padding=(40, 40),
    )


def make_cluster_map(rows, size_col, color, popup_fn):
    pts = rows.dropna(subset=["centroid_lat", "centroid_long"])
    m = folium.Map(location=[12.97, 77.59], zoom_start=12, tiles="cartodbpositron")
    for _, row in pts.iterrows():
        folium.CircleMarker(
            location=[row["centroid_lat"], row["centroid_long"]],
            radius=max(6, min(28, row[size_col] / 40)),
            color=color, fill=True, fill_color=color, fill_opacity=0.75, weight=1,
            popup=folium.Popup(popup_fn(row), max_width=300),
        ).add_to(m)
    _fit(m, pts)
    return m


def render_map(m, base_key, height=550):
    """Render a folium map with a 🎯 Recenter button that re-fits to clusters.

    Recenter works by bumping a counter appended to the component key — that
    remounts st_folium, discarding the user's pan/zoom and reverting to the
    map's fitted bounds.
    """
    nkey = f"recenter_{base_key}"
    st.session_state.setdefault(nkey, 0)
    if st.button("🎯 Recenter", key=f"btn_{base_key}", width="content",
                 help="Re-center the map on the clusters"):
        st.session_state[nkey] += 1
    st_folium(
        m, width=1200, height=height,
        key=f"{base_key}_{st.session_state[nkey]}",
        returned_objects=[],
    )


def zone(row):
    return f"{row['police_station']} / {row.get('nearest_junction') or '—'}"


# ════════════════════════════════════════════════════════════════════════════════
# Sidebar navigation — the left pane changes per view
# ════════════════════════════════════════════════════════════════════════════════

st.sidebar.markdown("## 🚔 ParkSense AI")
st.sidebar.caption("AI-Driven Parking Intelligence")

NAV_ITEMS = [
    ("Violation Explorer",  "🔍"),
    ("Persistent Hotspots", "🔴"),
    ("Monthly Hotspots",    "🟣"),
    ("Live Enforcement",    "🟠"),
]
st.session_state.setdefault("view", NAV_ITEMS[0][0])

for name, icon in NAV_ITEMS:
    is_active = st.session_state["view"] == name
    if st.sidebar.button(
        f"{icon}  {name}",
        key=f"nav_{name}",
        width="stretch",
        type="primary" if is_active else "secondary",
    ):
        st.session_state["view"] = name
        st.rerun()

view = st.session_state["view"]
st.sidebar.divider()

st.title("🚔 ParkSense AI")
st.caption("AI-Driven Parking Intelligence & Enforcement Dashboard")


# ════════════════════════════════════════════════════════════════════════════════
# VIEW 1 — Violation Explorer  (filters: Station · Hour · Date)
# ════════════════════════════════════════════════════════════════════════════════

def render_violation_explorer():
    st.sidebar.subheader("Explorer Filters")
    sel_station = st.sidebar.selectbox("Police Station", STATIONS)
    sel_hour    = st.sidebar.slider("Hour of Day", 0, 23, 22)
    min_date    = pd.to_datetime(db.get_meta("min_date")).date()
    max_date    = pd.to_datetime(db.get_meta("max_date")).date()
    sel_date    = st.sidebar.date_input("Date", value=max_date,
                                        min_value=min_date, max_value=max_date)

    st.header("Violation Explorer")
    violations = db.get_violations(sel_station, sel_hour, sel_date)

    c1, c2, c3 = st.columns(3)
    c1.metric("Violations This Hour", f"{len(violations):,}")
    c2.metric("Total Clusters (Persistent)", f"{db.get_total_clusters():,}")
    c3.metric("Total Dataset Records", f"{db.get_total_violations():,}")

    st.subheader("🗺️ Violation Map")
    st.caption(f"🔵 Individual violations · {sel_station} · {sel_hour:02d}:00 · {sel_date}")
    center = (
        [violations["latitude"].mean(), violations["longitude"].mean()]
        if len(violations) > 0 else [12.9716, 77.5946]
    )
    m = folium.Map(location=center, zoom_start=13, tiles="cartodbpositron")
    lats, lons = [], []
    for _, row in violations.iterrows():
        lats.append(row["latitude"]); lons.append(row["longitude"])
        folium.CircleMarker(
            location=[row["latitude"], row["longitude"]],
            radius=4, color="#1f77b4", fill=True, fill_color="#1f77b4",
            fill_opacity=0.7, weight=1,
            popup=folium.Popup(
                f"<b>Vehicle:</b> {row['vehicle_type']}<br>"
                f"<b>Violation:</b> {row['primary_violation']}<br>"
                f"<b>Station:</b> {row['police_station']}<br>"
                f"<b>Hour:</b> {row['hour']}<br>"
                f"<b>Junction:</b> {row['junction_name']}",
                max_width=300,
            ),
        ).add_to(m)
    if lats:
        m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]], padding=(40, 40))
    render_map(m, "explorer", height=600)

    if len(violations) == 0:
        st.info("No violations match this Station / Hour / Date combination. "
                "Try a different hour or date.")

    st.subheader("Violations by Hour")
    hourly = db.get_hourly_trend(sel_station).set_index("hour")["count"]
    st.bar_chart(hourly)

    st.subheader("Sample Violation Records")
    st.dataframe(violations.head(20), width="stretch")


# ════════════════════════════════════════════════════════════════════════════════
# VIEW 2 — Persistent Hotspots  (no filters)
# ════════════════════════════════════════════════════════════════════════════════

def render_persistent():
    st.sidebar.subheader("Filters")
    st.sidebar.caption("Persistent hotspots show the all-time top zones — no filters.")

    st.header("Persistent Hotspots")
    rows = db.get_persistent_hotspots(limit=20)

    c1, c2 = st.columns(2)
    c1.metric("Clusters Shown", len(rows))
    c2.metric("Violations in Top 20", int(rows["all_time_count"].sum()) if len(rows) else 0)

    st.subheader("🗺️ Persistent Hotspot Map")
    st.caption("🔴 HDBSCAN clusters on full dataset (Nov 2023 – Apr 2024) · Size = violation count")

    def popup(row):
        return (
            f"<b>🔴 #{int(row['persistent_rank'])} — {zone(row)}</b><br>"
            f"All-time violations: {int(row['all_time_count'])}<br>"
            f"Top violation: {row['top_violation']}<br>"
            f"Peak hour: {row['peak_hour']}<br>"
            f"Dominant vehicle: {row['dominant_vehicle']}"
        )

    render_map(make_cluster_map(rows, "all_time_count", "#d62728", popup), "persistent")

    st.subheader("🔴 Persistent Hotspot Rankings")
    display = rows[[
        "persistent_rank", "police_station", "nearest_junction",
        "all_time_count", "top_violation", "peak_hour",
    ]].copy()
    display.columns = ["Rank", "Police Station", "Junction",
                       "All-Time Violations", "Top Violation", "Peak Hour"]
    st.dataframe(display, width="stretch", hide_index=True)


# ════════════════════════════════════════════════════════════════════════════════
# VIEW 3 — Monthly Hotspots  (filter: Month)
# ════════════════════════════════════════════════════════════════════════════════

def render_monthly():
    available_yms = db.get_available_year_months()
    month_options = {MONTH_LABEL.get(ym, ym): ym for ym in available_yms}

    st.sidebar.subheader("Monthly Filter")
    sel_label = st.sidebar.selectbox("Month", list(month_options.keys()),
                                     index=len(month_options) - 1)
    sel_ym = month_options[sel_label]

    st.header("Monthly Hotspots")
    rows = db.get_monthly_hotspots(sel_ym, limit=20)

    c1, c2 = st.columns(2)
    c1.metric("Active Clusters", len(rows))
    c2.metric(f"Violations in {sel_label}",
              int(rows["violation_count"].sum()) if len(rows) else 0)

    st.subheader(f"🗺️ Monthly Hotspot Map — {sel_label}")
    st.caption(f"🟣 HDBSCAN clusters on {sel_label} only · Size = monthly violation count")

    def popup(row):
        return (
            f"<b>🟣 #{int(row['monthly_rank'])} — {zone(row)}</b><br>"
            f"Month: {sel_label}<br>"
            f"Violations: {int(row['violation_count'])}<br>"
            f"Top violation: {row['top_violation']}<br>"
            f"Peak hour: {row['peak_hour']}"
        )

    render_map(make_cluster_map(rows, "violation_count", "#9467bd", popup), "monthly")

    st.subheader(f"🟣 Top Hotspots — {sel_label}")
    display = rows[[
        "monthly_rank", "police_station", "nearest_junction",
        "violation_count", "top_violation", "peak_hour",
    ]].copy()
    display.columns = ["Rank", "Police Station", "Junction",
                       "Violations", "Top Violation", "Peak Hour"]
    st.dataframe(display, width="stretch", hide_index=True)


# ════════════════════════════════════════════════════════════════════════════════
# VIEW 4 — Live Enforcement  (filter: 10 contexts = day_type × time_bucket)
# ════════════════════════════════════════════════════════════════════════════════

def _hour_to_bucket(hour):
    if   hour < 6:  return "night"
    elif hour < 9:  return "morning_peak"
    elif hour < 14: return "midday"
    elif hour < 20: return "evening_peak"
    else:           return "late_night"


def render_live_enforcement():
    IST = pytz.timezone("Asia/Kolkata")
    now = datetime.now(IST)
    auto_day    = "weekend" if now.weekday() >= 5 else "weekday"
    auto_bucket = _hour_to_bucket(now.hour)

    DAY_TYPES   = ["weekday", "weekend"]
    ALL_BUCKETS = list(SHIFT_DISPLAY.keys())

    st.sidebar.subheader("Enforcement Context")
    live_mode = st.sidebar.toggle(
        "🔴 Live (follow IST)", value=True,
        help="ON = follow current IST time. OFF = pick any of the 10 contexts.",
    )
    if live_mode:
        st.sidebar.info(
            f"🕐 {now.strftime('%H:%M')} · {now.strftime('%A')}\n\n"
            f"→ **{auto_day} / {SHIFT_DISPLAY[auto_bucket]}**"
        )
        st.sidebar.selectbox("Day Type", DAY_TYPES,
                             index=DAY_TYPES.index(auto_day), disabled=True)
        st.sidebar.selectbox("Time Bucket", ALL_BUCKETS,
                             index=ALL_BUCKETS.index(auto_bucket),
                             format_func=lambda k: SHIFT_DISPLAY[k], disabled=True)
        sel_day, sel_bucket = auto_day, auto_bucket
    else:
        sel_day = st.sidebar.selectbox("Day Type", DAY_TYPES,
                                       index=DAY_TYPES.index(auto_day))
        sel_bucket = st.sidebar.selectbox("Time Bucket", ALL_BUCKETS,
                                          index=ALL_BUCKETS.index(auto_bucket),
                                          format_func=lambda k: SHIFT_DISPLAY[k])

    context = f"{sel_day}_{sel_bucket}"

    st.header("Live Enforcement")
    st.subheader(f"Active Context: `{context}` — {SHIFT_DISPLAY[sel_bucket]} "
                 f"{'(Weekday)' if sel_day == 'weekday' else '(Weekend)'}")

    rows = db.get_contextual_hotspots(context, limit=20)
    if len(rows) == 0:
        st.warning("No contextual hotspot data for this context. "
                   "Re-run `build_hotspots.py`.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Enforcement Zones", len(rows))
    c2.metric("Historical Violations (this context)", int(rows["violation_count"].sum()))
    c3.metric("Recent Violations (last 30d)", int(rows["recent_count"].sum()))

    st.subheader("🗺️ Recommended Enforcement Locations")
    st.caption("🟠 Ranked by enforcement score (60% historical + 40% recent) · Size = violation count")

    def popup(row):
        return (
            f"<b>🟠 Priority #{int(row['context_rank'])} — {zone(row)}</b><br>"
            f"Context: {context}<br>"
            f"Historical violations: {int(row['violation_count'])}<br>"
            f"Last 30d violations: {int(row['recent_count'])}<br>"
            f"Enforcement score: {row['enforcement_score']:.3f}<br>"
            f"Top violation: {row['top_violation']}<br>"
            f"Peak hour: {row['peak_hour']}"
        )

    render_map(make_cluster_map(rows, "violation_count", "#ff7f0e", popup), "enforce")

    st.subheader("📋 Enforcement Priority Queue")
    display = rows[[
        "context_rank", "police_station", "nearest_junction",
        "violation_count", "recent_count", "enforcement_score",
        "top_violation", "peak_hour",
    ]].copy()
    display.columns = ["Priority", "Police Station", "Junction", "Historical",
                       "Last 30d", "Score", "Top Violation", "Peak Hour"]
    st.dataframe(display, width="stretch", hide_index=True)

    _render_traffic_impact(rows, context)


# ── Objective 2: Traffic Impact Re-Ranking (OSM + live TomTom) ──────────────────

def _render_traffic_impact(rows, context):
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
        enriched_rows, live_rows = [], []
        n = len(rows)
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

        for i, (_, r) in enumerate(rows.iterrows()):
            key = enrichment.cell_key(r["centroid_lat"], r["centroid_long"])

            # OSM context — persisted in enrichment.db, fetched once per ~11 m cell
            cached = db.get_enrichment([key])
            if len(cached) == 0:
                feat = enrichment.enrich_centroid(r["centroid_lat"], r["centroid_long"])
                feat["cell_key"] = key
                db.upsert_enrichment(feat)
                cached = db.get_enrichment([key])
            enriched_rows.append(cached.iloc[0])

            # Live congestion — fetched fresh, stored as a demo snapshot
            live = enrichment.fetch_live_traffic(
                r["centroid_lat"], r["centroid_long"], tomtom_key
            )
            live.update({
                "cell_key": key,
                "centroid_lat": float(r["centroid_lat"]),
                "centroid_long": float(r["centroid_long"]),
                "fetched_at": now_iso,
            })
            if tomtom_key:
                db.upsert_live_traffic(live)
            live_rows.append(live)

            prog.progress((i + 1) / n, text=f"Enriched {i + 1}/{n} zones")
        prog.empty()

        enriched_df = pd.DataFrame(enriched_rows).reset_index(drop=True)
        live_df = pd.DataFrame(live_rows) if tomtom_key else None
        st.session_state[f"osm_{context}"]  = enriched_df
        st.session_state[f"live_{context}"] = live_df
        st.session_state[f"impact_{context}"] = enrichment.compute_impact(
            rows, enriched_df,
            live=live_df[["congestion_index", "current_speed",
                          "free_flow_speed", "road_closure"]] if live_df is not None else None,
        )

    if f"impact_{context}" not in st.session_state:
        st.info("Click the button above to fetch OSM context + live TomTom congestion "
                "and compute traffic impact scores for these zones.")
        return

    imp = st.session_state[f"impact_{context}"]
    has_live = "congestion_index" in imp.columns

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Avg Impact Score", f"{imp['importance'].mean():.1f}")
    k2.metric("Top Impact Zone", f"{imp.iloc[0]['importance']:.0f}/100")
    k3.metric("Zones Re-Ranked", int((imp["rank_change"] != 0).sum()))
    if has_live:
        k4.metric("Avg Live Congestion", f"{imp['congestion_index'].mean()*100:.0f}%")
    else:
        k4.metric("Live Congestion", "—")

    st.caption("🗺️ Sized by Traffic Impact Score · greener = higher impact")

    def popup(row):
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
            f"Road: {row['road_type'] or '—'} "
            f"({int(row['lane_count']) if pd.notna(row['lane_count']) else '?'} lanes)<br>"
            f"Hospitals: {int(row['hospital_count'])} | Offices: {int(row['office_count'])} | "
            f"Transit: {int(row['railway_station_count'])}<br>"
            f"{live_line}"
            f"Why: {row['reason']}"
        )

    render_map(make_cluster_map(imp, "importance", "#2ca02c", popup), "impact")

    def arrow(c):
        return f"▲ {c}" if c > 0 else (f"▼ {abs(c)}" if c < 0 else "—")

    out = imp.copy()
    out["Δ Rank"] = out["rank_change"].apply(arrow)
    out["live_congestion"] = out["live_congestion"].apply(
        lambda v: f"{float(v)*100:.0f}%" if isinstance(v, (int, float)) else v
    )
    show = out[[
        "new_rank", "context_rank", "Δ Rank", "zone", "road_type", "lane_count",
        "hospital_count", "office_count", "railway_station_count",
        "road_criticality", "urban_activity", "live_congestion", "importance", "reason",
    ]].copy()
    show.columns = [
        "Impact Rank", "Old Priority", "Δ Rank", "Zone", "Road Type", "Lanes",
        "Hospitals", "Offices", "Transit", "Road Crit.", "Urban Act.",
        "Live Cong.", "Impact Score", "Why",
    ]
    st.dataframe(show, width="stretch", hide_index=True)

    # ── Separate source tables ──────────────────────────────────────────────────
    osm_df  = st.session_state.get(f"osm_{context}")
    live_df = st.session_state.get(f"live_{context}")

    with st.expander("🅿️ OSM Context Table — persisted (static facts cached in enrichment.db)"):
        st.caption("Hospital / office / school counts and road attributes don't change, "
                   "so these are cached once per ~11 m cell and survive `build_hotspots.py` rebuilds.")
        if osm_df is not None and len(osm_df):
            cols = ["cell_key", "road_type", "lane_count", "maxspeed",
                    "road_importance_score", "office_count", "hospital_count",
                    "school_count", "railway_station_count", "parking_lot_count",
                    "fetched_at"]
            st.dataframe(osm_df[[c for c in cols if c in osm_df.columns]],
                         width="stretch", hide_index=True)

    with st.expander("🚦 Live Traffic Table — TomTom snapshot (demo only)"):
        st.warning(
            "⚠️ **Demo only.** TomTom returns **current** road speeds, but this "
            "dataset is historical (Nov 2023 – Apr 2024), so live congestion can't "
            "be matched to past violations one-to-one. In a **real-time deployment** "
            "this table would stream live and the impact score would react instantly "
            "to actual on-road congestion. Snapshots are stored in `live_traffic` "
            "(enrichment.db) for transparency."
        )
        if live_df is not None and len(live_df):
            cols = ["cell_key", "current_speed", "free_flow_speed",
                    "congestion_index", "delay_ratio", "road_closure", "fetched_at"]
            st.dataframe(live_df[[c for c in cols if c in live_df.columns]],
                         width="stretch", hide_index=True)
        else:
            st.info("No live data — set `TOMTOM_API_KEY` in `.streamlit/secrets.toml` "
                    "to enable the live snapshot.")


# ════════════════════════════════════════════════════════════════════════════════
# Router
# ════════════════════════════════════════════════════════════════════════════════

if   view == "Violation Explorer":  render_violation_explorer()
elif view == "Persistent Hotspots": render_persistent()
elif view == "Monthly Hotspots":    render_monthly()
elif view == "Live Enforcement":    render_live_enforcement()
