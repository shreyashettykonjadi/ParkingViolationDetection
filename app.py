import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium

st.set_page_config(page_title="ParkSense AI", layout="wide")

st.title("🚔 ParkSense AI")
st.caption("AI-Driven Parking Intelligence & Enforcement Dashboard")


@st.cache_data
def load_data():
    return pd.read_csv("data/clean_parking_data.csv")


@st.cache_data
def get_hotspots():
    from src.hotspot_detection import run_full_pipeline
    df = load_data()
    summary, _ = run_full_pipeline(df)
    return summary


df = load_data()

tab1, tab2 = st.tabs(["Violation Explorer", "Hotspot Analysis"])

# ── TAB 1: Violation Explorer (existing dashboard) ──────────────────────────
with tab1:
    st.sidebar.header("Filters")

    stations = sorted(df["police_station"].dropna().unique())
    selected_station = st.sidebar.selectbox("Police Station", stations)
    selected_hour = st.sidebar.slider("Hour of Day", 0, 23, 22)

    filtered_df = df[
        (df["police_station"] == selected_station) & (df["hour"] == selected_hour)
    ]

    st.write(f"Records Found: {len(filtered_df)}")

    col1, col2, col3 = st.columns(3)
    col1.metric("Violations This Hour", len(filtered_df))
    col2.metric("Police Stations", df["police_station"].nunique())
    col3.metric("Total Dataset Records", len(df))

    st.subheader("🗺️ Violation Map")
    sample_df = filtered_df.head(1000)

    if len(sample_df) > 0:
        center_lat = sample_df["latitude"].mean()
        center_lon = sample_df["longitude"].mean()
    else:
        center_lat, center_lon = 12.9716, 77.5946

    m = folium.Map(location=[center_lat, center_lon], zoom_start=13)

    for _, row in sample_df.iterrows():
        popup_text = (
            f"<b>Vehicle:</b> {row['vehicle_type']}<br>"
            f"<b>Violation:</b> {row['primary_violation']}<br>"
            f"<b>Station:</b> {row['police_station']}<br>"
            f"<b>Hour:</b> {row['hour']}<br>"
            f"<b>Junction:</b> {row['junction_name']}"
        )
        folium.CircleMarker(
            location=[row["latitude"], row["longitude"]],
            radius=4,
            color="blue",
            fill=True,
            fill_color="blue",
            fill_opacity=0.7,
            popup=folium.Popup(popup_text, max_width=300),
        ).add_to(m)

    st_folium(m, width=1200, height=600)

    hourly = df[df["police_station"] == selected_station].groupby("hour").size()
    st.subheader("Violations by Hour")
    st.line_chart(hourly)

    st.subheader("Sample Violation Records")
    st.dataframe(filtered_df.head(20))


# ── TAB 2: Hotspot Analysis ──────────────────────────────────────────────────
with tab2:
    col_a, col_b = st.columns(2)
    with col_a:
        day_type_sel = st.selectbox("Day Type", ["all", "weekday", "weekend"])
    with col_b:
        shift_sel = st.selectbox(
            "Shift",
            ["all", "morning_peak", "evening_peak", "midday", "night", "late_night"],
        )

    with st.spinner("Running HDBSCAN hotspot detection… (cached after first run)"):
        summary = get_hotspots()

    filtered = summary.copy()
    if day_type_sel != "all":
        filtered = filtered[filtered["day_type"] == day_type_sel]
    if shift_sel != "all":
        filtered = filtered[filtered["shift"] == shift_sel]

    k1, k2, k3 = st.columns(3)
    k1.metric("Hotspot Clusters", len(filtered))
    k2.metric("High-Confidence Zones", int((filtered["observation_confidence"] > 0.75).sum()))
    k3.metric("Violations in Clusters", int(filtered["violation_count"].sum()))

    st.subheader("🗺️ Hotspot Cluster Map")
    st.caption("Red = high confidence | Orange = medium | Yellow = low confidence | Circle size = violation volume")

    hm = folium.Map(location=[12.97, 77.59], zoom_start=12)
    for _, row in filtered.head(300).iterrows():
        if pd.isna(row["centroid_lat"]) or pd.isna(row["centroid_long"]):
            continue
        if row["observation_confidence"] > 0.75:
            color = "red"
        elif row["observation_confidence"] > 0.4:
            color = "orange"
        else:
            color = "yellow"

        popup_html = (
            f"<b>{row['police_station']}</b><br>"
            f"Shift: {row['shift']} | {row['day_type']}<br>"
            f"Violations: {row['violation_count']}<br>"
            f"Top violation: {row['top_violation']}<br>"
            f"Peak hour: {row['peak_hour']}<br>"
            f"Junction: {row['nearest_junction']}<br>"
            f"Confidence: {row['observation_confidence']:.2f}<br>"
            f"Risk score: {row['risk_score']:.2f}"
        )
        folium.CircleMarker(
            location=[row["centroid_lat"], row["centroid_long"]],
            radius=max(5, min(25, row["violation_count"] / 20)),
            color=color,
            fill=True,
            fill_opacity=0.65,
            popup=folium.Popup(popup_html, max_width=260),
        ).add_to(hm)

    st_folium(hm, width=1200, height=600)

    st.subheader("Enforcement Priority Queue")
    st.dataframe(
        filtered[
            [
                "police_station",
                "shift",
                "day_type",
                "violation_count",
                "top_violation",
                "peak_hour",
                "dominant_vehicle",
                "nearest_junction",
                "observation_confidence",
                "risk_score",
            ]
        ].head(30),
        use_container_width=True,
    )
