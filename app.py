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
from src import action, briefing, dispatch, stations

MONTH_LABEL   = db.MONTH_LABELS
SHIFT_DISPLAY = db.SHIFT_DISPLAY

# Shared station list (NaN-cleaned in db.get_police_stations)
STATIONS = db.get_police_stations()

# Span of the historical dataset — used by the action layer to judge recent spikes.
def _window_days():
    try:
        lo = pd.to_datetime(db.get_meta("min_date"))
        hi = pd.to_datetime(db.get_meta("max_date"))
        return max(30, int((hi - lo).days))
    except Exception:
        return 181

WINDOW_DAYS = _window_days()


def get_tomtom_key():
    """TomTom key from a sidebar override (session) or .streamlit/secrets.toml."""
    return st.session_state.get("tomtom_key_override") or st.secrets.get("TOMTOM_API_KEY", "")


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
    ("Command Center",      "🎯"),
    ("Forecast",            "🔮"),
    ("Briefings",           "📝"),
    ("Patrol Dispatch",     "🚓"),
    ("Live Enforcement",    "🟠"),
    ("Violation Explorer",  "🔍"),
    ("Persistent Hotspots", "🔴"),
    ("Monthly Hotspots",    "🟣"),
]
st.session_state.setdefault("view", "Command Center")

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

# TomTom key: prefilled from secrets.toml if present, overridable here for demos.
with st.sidebar.expander("🔑 TomTom API key"):
    _has_secret = bool(st.secrets.get("TOMTOM_API_KEY", ""))
    st.text_input(
        "Key (overrides secrets.toml)",
        key="tomtom_key_override", type="password",
        placeholder="set in secrets.toml" if _has_secret else "paste key for live routing",
    )
    st.caption("✅ Key loaded from secrets.toml" if _has_secret
               else "⚠️ No key in secrets — routing falls back to estimates without one.")

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

def compute_impact_for_context(rows, context):
    """Enrich the given hotspot rows (OSM context + live TomTom) and store the
    impact DataFrame in session_state under `impact_{context}`. Shared by the Live
    Enforcement tab and the action-layer 'Enrich' button so there is one code path
    and no duplicate API calls."""
    tomtom_key = get_tomtom_key()
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


def _render_traffic_impact(rows, context):
    st.divider()
    st.subheader("🚦 Traffic Impact Re-Ranking (OSM + Live Traffic)")
    tomtom_key = get_tomtom_key()
    st.caption(
        "Re-ranks the same zones by **traffic impact** — road criticality + urban "
        "context (hospitals, offices, schools, transit) + "
        + ("**live TomTom congestion**" if tomtom_key else "_(TomTom key not set)_")
        + " on top of violation volume."
    )

    if st.button("🔍 Compute Traffic Impact Scores", key="enrich_btn"):
        compute_impact_for_context(rows, context)

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
# ACTION LAYER — shared context + hotspot loader for the three action views
# ════════════════════════════════════════════════════════════════════════════════

ACTION_POOL = 20   # how many ranked hotspots the action layer considers


def _action_context_controls():
    """Sidebar context picker shared by Command Center / Briefings / Dispatch.
    Widget state is keyed (act_*) so all three views stay in sync."""
    IST = pytz.timezone("Asia/Kolkata")
    now = datetime.now(IST)
    auto_day    = "weekend" if now.weekday() >= 5 else "weekday"
    auto_bucket = _hour_to_bucket(now.hour)
    DAY_TYPES   = ["weekday", "weekend"]
    ALL_BUCKETS = list(SHIFT_DISPLAY.keys())

    st.sidebar.subheader("Operational Context")
    follow = st.sidebar.toggle("🔴 Follow live IST", value=True, key="act_follow_live",
                               help="ON = current IST day/time. OFF = choose any context.")
    if follow:
        st.sidebar.info(f"🕐 {now.strftime('%H:%M')} · {now.strftime('%A')}\n\n"
                        f"→ **{auto_day} / {SHIFT_DISPLAY[auto_bucket]}**")
        sel_day, sel_bucket = auto_day, auto_bucket
    else:
        sel_day = st.sidebar.selectbox("Day Type", DAY_TYPES,
                                       index=DAY_TYPES.index(auto_day), key="act_day")
        sel_bucket = st.sidebar.selectbox("Time Bucket", ALL_BUCKETS,
                                          index=ALL_BUCKETS.index(auto_bucket),
                                          format_func=lambda k: SHIFT_DISPLAY[k],
                                          key="act_bucket")
    return f"{sel_day}_{sel_bucket}", now


def load_action_hotspots(context):
    """Build (and cache in session_state) the ranked action-hotspot list — the
    single source of truth shared by all three action views."""
    rows = db.get_contextual_hotspots(context, limit=ACTION_POOL)
    impact_df = st.session_state.get(f"impact_{context}")
    hotspots = action.build_action_hotspots(rows, context, WINDOW_DAYS, impact_df)
    st.session_state["action_hotspots"] = hotspots
    st.session_state["action_context"]  = context
    return rows, hotspots


def _severity_badge(severity):
    color = action.SEVERITY_COLORS.get(severity, "#777")
    return (f"<span style='background:{color};color:#fff;padding:2px 10px;"
            f"border-radius:999px;font-weight:700;font-size:0.78rem;"
            f"letter-spacing:.04em;'>{severity}</span>")


# ════════════════════════════════════════════════════════════════════════════════
# VIEW — COMMAND CENTER  (Feature 2, default landing)
# ════════════════════════════════════════════════════════════════════════════════

def render_command_center():
    context, _ = _action_context_controls()
    rows, hotspots = load_action_hotspots(context)

    st.sidebar.divider()
    st.sidebar.caption("Enrich adds road type, hospital/office counts and live "
                       "congestion (OSM + TomTom) to the cards.")
    if st.sidebar.button("⚡ Enrich (OSM + TomTom)", width="stretch", key="cc_enrich"):
        if len(rows):
            compute_impact_for_context(rows, context)
            st.rerun()

    st.header("🎯 Command Center")
    st.caption(f"What should we do right now? · Context `{context}` · "
               f"{SHIFT_DISPLAY[context.split('_', 1)[1]]}")

    if not hotspots:
        st.warning("No hotspot data for this context. Re-run `build_hotspots.py` "
                   "or pick another context.")
        return

    enriched = any(h["enriched"] for h in hotspots)
    dispatch_res = st.session_state.get(f"dispatch_{context}")
    eta_by_cluster = (dispatch_res or {}).get("eta_by_cluster", {})

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Priority Zones", len(hotspots))
    k2.metric("HIGH Severity", sum(h["severity"] == "HIGH" for h in hotspots))
    k3.metric("Top Impact", f"{hotspots[0]['traffic_impact']} / 100")
    k4.metric("Units Dispatched", len(dispatch_res["assignments"]) if dispatch_res else 0)

    if not enriched:
        st.info("ℹ️ Cards show violation-based priority. Click **⚡ Enrich (OSM + "
                "TomTom)** in the sidebar to add road type, hospital/office counts "
                "and live congestion.")
    if not dispatch_res:
        st.info("ℹ️ Nearest-unit ETAs appear here once you run **Patrol Dispatch**.")

    st.divider()
    for h in hotspots:
        _render_priority_card(h, eta_by_cluster.get(h["cluster_id"]))


def _render_priority_card(h, eta_info):
    sev_color = action.SEVERITY_COLORS.get(h["severity"], "#777")
    with st.container(border=True):
        left, mid, right = st.columns([3.2, 1.6, 1.6])

        with left:
            st.markdown(
                f"<div style='display:flex;align-items:center;gap:10px;'>"
                f"<span style='background:#1b2130;color:#fff;padding:3px 12px;"
                f"border-radius:8px;font-weight:700;'>Priority #{h['priority_rank']}</span>"
                f"{_severity_badge(h['severity'])}</div>"
                f"<h3 style='margin:.35rem 0 .1rem 0;'>{h['location']}</h3>"
                f"<div style='color:#444;font-weight:600;'>➡️ {h['recommended_action']}</div>",
                unsafe_allow_html=True,
            )
            if h["why_factors"]:
                items = "".join(f"<li>✅ {f}</li>" for f in h["why_factors"])
                st.markdown(
                    f"<div style='margin-top:.5rem;font-size:0.9rem;'>"
                    f"<b>Why this location?</b><ul style='margin:.2rem 0 0 0;"
                    f"padding-left:1.1rem;'>{items}</ul></div>",
                    unsafe_allow_html=True,
                )

        with mid:
            st.metric("Traffic Impact", f"{h['traffic_impact']} / 100")
            st.metric("Forecast Risk", f"{h['forecast_risk']}%")

        with right:
            if eta_info:
                src = "🟢 live" if eta_info["source"] == "tomtom" else "🟡 est"
                st.markdown(
                    f"<div style='font-size:0.8rem;color:#666;'>NEAREST UNIT ETA</div>"
                    f"<div style='font-size:2.0rem;font-weight:800;color:{sev_color};"
                    f"line-height:1.1;'>{dispatch.fmt_eta(eta_info['eta_sec'])}</div>"
                    f"<div style='font-size:0.8rem;color:#666;'>{eta_info['unit']} · "
                    f"{eta_info['station']} · {src}</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    "<div style='font-size:0.8rem;color:#666;'>NEAREST UNIT ETA</div>"
                    "<div style='font-size:1.4rem;font-weight:700;color:#999;'>—</div>"
                    "<div style='font-size:0.75rem;color:#999;'>run Patrol Dispatch</div>",
                    unsafe_allow_html=True,
                )

        with st.expander("📝 Full operational briefing"):
            st.code(briefing.generate_briefing(h), language=None)


# ════════════════════════════════════════════════════════════════════════════════
# VIEW — BRIEFINGS  (Feature 1)
# ════════════════════════════════════════════════════════════════════════════════

def render_briefings():
    context, _ = _action_context_controls()
    _, hotspots = load_action_hotspots(context)

    st.header("📝 Smart Briefings")
    st.caption(f"Shift-ready, plain-English operational summaries · Context `{context}`")

    if not hotspots:
        st.warning("No hotspot data for this context.")
        return

    c1, c2 = st.columns([1, 3])
    gen_all = c1.button("🗒️ Generate All Briefings", type="primary")

    if gen_all:
        combined = "\n\n".join(
            f"PRIORITY #{h['priority_rank']} — {h['location']} [{h['severity']}]\n"
            + briefing.generate_briefing(h)
            for h in hotspots
        )
        header = (f"BENGALURU TRAFFIC POLICE — SHIFT BRIEFING\n"
                  f"Context: {context}\nGenerated: "
                  f"{datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M IST')}\n"
                  f"{'='*60}\n\n")
        st.download_button(
            "⬇️ Download All Briefings (.txt)", header + combined,
            file_name=f"shift_briefing_{context}_"
                      f"{datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d')}.txt",
            mime="text/plain", key="dl_all",
        )

    st.divider()
    for h in hotspots:
        text = briefing.generate_briefing(h)
        with st.expander(
            f"Priority #{h['priority_rank']} · {h['location']} · {h['severity']}",
            expanded=gen_all,
        ):
            st.code(text, language=None)   # st.code → built-in copy icon
            st.download_button(
                "⬇️ Download Briefing", text,
                file_name=briefing.briefing_filename(h),
                mime="text/plain", key=f"dl_{h['cluster_id']}_{h['priority_rank']}",
            )


# ════════════════════════════════════════════════════════════════════════════════
# VIEW — PATROL DISPATCH  (Feature 3, TomTom routing)
# ════════════════════════════════════════════════════════════════════════════════

SEVERITY_MARKER = {"HIGH": "red", "MEDIUM": "orange", "LOW": "green"}


def _parse_manual_units(text):
    """Parse 'Name,lat,lng' (or 'lat,lng') lines into manual unit origins."""
    units = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        try:
            if len(parts) == 3:
                name, lat, lng = parts[0], float(parts[1]), float(parts[2])
            elif len(parts) == 2:
                name, lat, lng = "On-road", float(parts[0]), float(parts[1])
            else:
                continue
            if dispatch.valid_coord(lat, lng):
                units.append({"station": name or "On-road", "lat": lat, "lng": lng})
        except ValueError:
            continue
    return units


def render_patrol_dispatch():
    context, _ = _action_context_controls()
    _, hotspots = load_action_hotspots(context)

    st.sidebar.divider()
    st.sidebar.subheader("Dispatch Controls")
    sel_stations = st.sidebar.multiselect(
        "Active patrol units (stations)", stations.station_names(),
        default=stations.default_active_units(),
    )
    manual_text = st.sidebar.text_area(
        "Manual on-road units", placeholder="UnitName,lat,lng\n12.93,77.62",
        help="One per line: 'Name,lat,lng' (or just 'lat,lng').", height=80,
    )
    max_n = max(1, len(hotspots))
    n_targets = st.sidebar.number_input(
        "Top-priority hotspots to dispatch to", min_value=1,
        max_value=max_n, value=min(3, max_n), step=1,
    )
    refresh = st.sidebar.button("🔄 Compute / Refresh Routing", type="primary",
                                width="stretch")

    st.header("🚓 Patrol Dispatch")
    st.caption(f"Who should go where? · Context `{context}` · "
               "TomTom Matrix Routing + Calculate Route")

    if not hotspots:
        st.warning("No hotspot data for this context.")
        return

    # Assemble unit origins (stations first, then manual on-road units).
    origins = []
    for name in sel_stations:
        c = stations.station_coords(name)
        if c:
            origins.append({"station": name, "lat": c[0], "lng": c[1]})
    origins.extend(_parse_manual_units(manual_text))

    if refresh:
        if not origins:
            st.error("Select at least one patrol unit (or add a manual on-road unit).")
        else:
            _run_dispatch(context, hotspots, origins, int(n_targets))

    result = st.session_state.get(f"dispatch_{context}")
    if not result:
        st.info("Set your units and target count in the sidebar, then click "
                "**Compute / Refresh Routing**.")
        return

    if result["source"] != "tomtom":
        st.warning("⚠️ " + (result.get("matrix_warning") or
                   "ETAs/routes are haversine estimates (~25 km/h), not live TomTom data."))
    else:
        st.success("✅ ETAs and routes are live, traffic-aware TomTom data.")

    # ── Assignment table ──
    st.subheader("📋 Patrol Assignment Table")
    table = pd.DataFrame([{
        "Patrol/Unit": a["unit"],
        "Station Origin": a["station"],
        "Assigned Hotspot": a["hotspot_location"],
        "Severity": a["severity"],
        "ETA": dispatch.fmt_eta(a["eta_sec"]),
        "Distance": dispatch.fmt_distance(a["distance_m"]),
        "Source": "TomTom" if a["route_source"] == "tomtom" else "Estimate",
    } for a in result["assignments"]])
    st.dataframe(table, width="stretch", hide_index=True)

    # ── Route map ──
    st.subheader("🗺️ Dispatch Map")
    route_labels = ["Show All Routes"] + [
        f"{a['unit']} → {a['hotspot_location']}" for a in result["assignments"]
    ]
    sel_route = st.selectbox("Highlight route", route_labels)
    _render_dispatch_map(result, sel_route)


def _run_dispatch(context, hotspots, origins, n_targets):
    api_key = get_tomtom_key()
    targets = hotspots[:n_targets]                      # already priority-ordered
    o_coords = [(o["lat"], o["lng"]) for o in origins]
    d_coords = [(h["centroid_lat"], h["centroid_long"]) for h in targets]

    with st.spinner("Computing travel-time matrix (TomTom Matrix Routing v2)…"):
        mat = dispatch.compute_travel_matrix(o_coords, d_coords, api_key)
    assigns = dispatch.greedy_assign(mat["matrix"], len(origins), len(targets))

    assignments, eta_by_cluster = [], {}
    route_source_any_estimate = mat["source"] != "tomtom"
    with st.spinner("Fetching final route geometry (TomTom Calculate Route)…"):
        for k, a in enumerate(assigns):
            o = origins[a["origin_index"]]
            h = targets[a["dest_index"]]
            route = dispatch.calculate_route(
                o["lat"], o["lng"], h["centroid_lat"], h["centroid_long"], api_key
            )
            if route["source"] != "tomtom":
                route_source_any_estimate = True
            unit = f"Patrol {chr(65 + k)}"               # Patrol A, B, C…
            assignments.append({
                "unit": unit,
                "station": o["station"],
                "origin": (o["lat"], o["lng"]),
                "hotspot_location": h["location"],
                "hotspot_cluster_id": h["cluster_id"],
                "dest": (h["centroid_lat"], h["centroid_long"]),
                "severity": h["severity"],
                "eta_sec": route["eta_sec"],
                "distance_m": route["distance_m"],
                "points": route["points"],
                "route_source": route["source"],
            })
            eta_by_cluster[h["cluster_id"]] = {
                "eta_sec": route["eta_sec"], "unit": unit,
                "station": o["station"], "source": route["source"],
            }

    st.session_state[f"dispatch_{context}"] = {
        "context": context,
        "source": "tomtom" if not route_source_any_estimate else "estimate",
        "matrix_warning": mat.get("warning"),
        "assignments": assignments,
        "eta_by_cluster": eta_by_cluster,
    }


def _render_dispatch_map(result, sel_route):
    m = folium.Map(location=[12.97, 77.59], zoom_start=12, tiles="cartodbpositron")
    all_pts = []

    # Origin markers (patrol units) — blue.
    seen_origins = set()
    for a in result["assignments"]:
        if a["origin"] in seen_origins:
            continue
        seen_origins.add(a["origin"])
        folium.Marker(
            location=list(a["origin"]),
            icon=folium.Icon(color="blue", icon="car", prefix="fa"),
            popup=folium.Popup(f"<b>{a['unit']}</b><br>{a['station']} PS", max_width=250),
        ).add_to(m)
        all_pts.append(a["origin"])

    # Hotspot markers — colored by severity.
    for a in result["assignments"]:
        folium.Marker(
            location=list(a["dest"]),
            icon=folium.Icon(color=SEVERITY_MARKER.get(a["severity"], "gray"),
                             icon="triangle-exclamation", prefix="fa"),
            popup=folium.Popup(
                f"<b>{a['hotspot_location']}</b><br>{a['severity']}<br>"
                f"ETA {dispatch.fmt_eta(a['eta_sec'])} · {dispatch.fmt_distance(a['distance_m'])}",
                max_width=260),
        ).add_to(m)
        all_pts.append(a["dest"])

    # Route polylines.
    show_all = sel_route == "Show All Routes"
    for a in result["assignments"]:
        label = f"{a['unit']} → {a['hotspot_location']}"
        highlighted = show_all or label == sel_route
        color = action.SEVERITY_COLORS.get(a["severity"], "#3388ff")
        folium.PolyLine(
            locations=[list(p) for p in a["points"]],
            color=color, weight=6 if highlighted else 2,
            opacity=0.9 if highlighted else 0.25,
            dash_array=None if a["route_source"] == "tomtom" else "8,8",
            tooltip=f"{label} · {dispatch.fmt_eta(a['eta_sec'])}",
        ).add_to(m)
        if highlighted:
            all_pts.extend(a["points"])

    if all_pts:
        lats = [p[0] for p in all_pts]; lons = [p[1] for p in all_pts]
        m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]], padding=(40, 40))
    st_folium(m, width=1200, height=560, key=f"dispatch_map_{result['context']}",
              returned_objects=[])


# ════════════════════════════════════════════════════════════════════════════════
# VIEW — Forecast (Predictive layer: tomorrow's predicted hotspots)
# ════════════════════════════════════════════════════════════════════════════════

def render_forecast():
    st.sidebar.subheader("Forecast")
    st.sidebar.caption("Next-day predicted violations per zone, ranked by a trained "
                       "Poisson model. No filters — shows tomorrow's top zones.")

    st.header("🔮 Forecast — Tomorrow's Predicted Hotspots")

    if not db.forecast_available():
        st.warning("No forecast yet. Run `python build_forecast.py` to train the model "
                   "and generate next-day predictions.")
        return

    target_date  = db.get_forecast_meta("target_date")
    target_wd    = db.get_forecast_meta("target_weekday")
    improvement  = db.get_forecast_meta("mae_improvement_pct")
    data_max     = db.get_forecast_meta("data_max_date")

    st.caption(
        f"Predicting **{target_wd}, {target_date}** — the day after the dataset ends "
        f"({data_max}). We forecast the **expected violation count per zone** (a "
        "spatio-temporal intensity) and rank zones, so patrols can be pre-positioned. "
        "An individual violation's exact location is not predictable; zone-level "
        "demand is."
    )

    preds = db.get_predictions(limit=20)
    if len(preds) == 0:
        st.info("Forecast database is empty. Re-run `python build_forecast.py`.")
        return

    metrics = db.get_forecast_metrics()
    mm = metrics.set_index("metric") if len(metrics) else None

    # ── Headline metrics: ML vs baseline ──
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Forecast Day", f"{target_wd}", help=str(target_date))
    if mm is not None and "MAE" in mm.index:
        c2.metric("Model MAE", f"{mm.loc['MAE','model']:.2f}",
                  delta=f"{float(improvement):+.0f}% vs baseline", delta_color="inverse")
        c3.metric("Baseline MAE", f"{mm.loc['MAE','baseline']:.2f}")
        c4.metric("Ranking Precision@10", f"{mm.loc['precision@10','model']:.0%}",
                  delta=f"{(mm.loc['precision@10','model']-mm.loc['precision@10','baseline']):+.0%}")
    st.caption("Metrics measured on a 30-day temporal holdout (train on history before "
               "it, test on the unseen last 30 days). Lower MAE / higher Precision@10 is better.")

    # ── Map ──
    st.subheader("🗺️ Predicted Hotspot Map")
    st.caption("🟢 Sized by predicted violations for the forecast day · top 20 zones")
    plot = preds.dropna(subset=["centroid_lat", "centroid_long"]).copy()
    mx = max(float(plot["pred_count"].max()), 1.0)
    plot["size_scaled"] = plot["pred_count"] / mx * 1100   # /40 in make_cluster_map → ≤~28

    def popup(row):
        return (
            f"<b>🔮 #{int(row['pred_rank'])} — {zone(row)}</b><br>"
            f"Predicted: <b>{row['pred_count']:.1f}</b> violations "
            f"({target_wd})<br>"
            f"Confidence band: {row['q10']:.1f} – {row['q90']:.1f}<br>"
            f"Recent 7-day avg: {row['recent_7d_mean']:.1f}/day<br>"
            f"Seasonal baseline: {row['base_count']:.1f}<br>"
            f"Top violation: {row['top_violation']}"
        )

    render_map(make_cluster_map(plot, "size_scaled", "#0d9488", popup), "forecast")

    # ── Ranked table ──
    st.subheader("📋 Predicted Priority Queue")
    show = preds.copy()
    show["Band"] = show.apply(lambda r: f"{r['q10']:.0f}–{r['q90']:.0f}", axis=1)
    show["Δ vs baseline"] = (show["pred_count"] - show["base_count"]).round(1)
    disp = show[[
        "pred_rank", "police_station", "nearest_junction", "pred_count", "Band",
        "recent_7d_mean", "base_count", "Δ vs baseline", "top_violation",
    ]].copy()
    disp.columns = ["Rank", "Police Station", "Junction", "Predicted", "Band (q10–q90)",
                    "Recent 7d/day", "Baseline", "Δ vs baseline", "Top Violation"]
    disp["Predicted"]     = disp["Predicted"].round(1)
    disp["Recent 7d/day"] = disp["Recent 7d/day"].round(1)
    disp["Baseline"]      = disp["Baseline"].round(1)
    st.dataframe(disp, width="stretch", hide_index=True)

    # ── Per-zone history + prediction ──
    st.subheader("📈 Zone History & Prediction")
    labels = {f"#{int(r['pred_rank'])} — {zone(r)}": int(r["cluster_id"])
              for _, r in preds.iterrows()}
    sel = st.selectbox("Zone", list(labels.keys()))
    cid = labels[sel]
    hist = db.query(
        "SELECT date, COUNT(*) AS count FROM violations "
        "WHERE persistent_cluster_id = ? GROUP BY date ORDER BY date", (cid,)
    )
    if len(hist):
        hist = hist.set_index("date")["count"]
        prow = preds[preds["cluster_id"] == cid].iloc[0]
        hist.loc[str(target_date)] = prow["pred_count"]   # append the forecast point
        st.line_chart(hist)
        st.caption(f"Daily actual violations for this zone; the final point "
                   f"({target_date}) is the model's prediction "
                   f"(~{prow['pred_count']:.1f}, band {prow['q10']:.1f}–{prow['q90']:.1f}).")


# ════════════════════════════════════════════════════════════════════════════════
# Router
# ════════════════════════════════════════════════════════════════════════════════

if   view == "Command Center":      render_command_center()
elif view == "Forecast":            render_forecast()
elif view == "Briefings":           render_briefings()
elif view == "Patrol Dispatch":     render_patrol_dispatch()
elif view == "Violation Explorer":  render_violation_explorer()
elif view == "Persistent Hotspots": render_persistent()
elif view == "Monthly Hotspots":    render_monthly()
elif view == "Live Enforcement":    render_live_enforcement()
