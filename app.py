import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium

st.set_page_config(
    page_title="ParkSense AI",
    layout="wide"
)

st.title("🚔 ParkSense AI")
st.caption(
    "AI-Driven Parking Intelligence & Enforcement Dashboard"
)

# Cache prevents reloading CSV every interaction
@st.cache_data
def load_data():
    return pd.read_csv("data/clean_parking_data.csv")


df = load_data()


# SIDEBAR FILTERS: User controls the dashboard from here
st.sidebar.header("Filters")


# Police Station Dropdown
stations = sorted(
    df["police_station"]
    .dropna()
    .unique()
)

selected_station = st.sidebar.selectbox(
    "Police Station",
    stations
)



# Hour Filter
selected_hour = st.sidebar.slider(
    "Hour of Day",
    0,
    23,
    22
)


# FILTER DATA:Create a smaller dataframe based on user selections
filtered_df = df[
    (df["police_station"] == selected_station)
    &
    (df["hour"] == selected_hour)
]


# SUMMARY SECTION:Quick information about current selection
st.write(
    f"Records Found: {len(filtered_df)}"
)


# KPI CARDS:Important metrics shown at top of dashboard
col1, col2, col3 = st.columns(3)

col1.metric(
    "Violations This Hour",
    len(filtered_df)
)

col2.metric(
    "Police Stations",
    df["police_station"].nunique()
)

col3.metric(
    "Total Dataset Records",
    len(df)
)



# MAP:Shows violation locations for selected station and hour
st.subheader("🗺️ Violation Map")
sample_df = filtered_df.head(1000)
center_lat = sample_df["latitude"].mean()
center_lon = sample_df["longitude"].mean()

m = folium.Map(
    location=[center_lat, center_lon],
    zoom_start=13
)

for _, row in sample_df.iterrows():

    popup_text = f"""
    <b>Vehicle:</b> {row['vehicle_type']}<br>
    <b>Violation:</b> {row['primary_violation']}<br>
    <b>Station:</b> {row['police_station']}<br>
    <b>Hour:</b> {row['hour']}<br>
    <b>Junction:</b> {row['junction_name']}
    """

    folium.CircleMarker(
        location=[
            row["latitude"],
            row["longitude"]
        ],
        radius=4,
        color="blue",
        fill=True,
        fill_color="blue",
        fill_opacity=0.7,
        popup=folium.Popup(
            popup_text,
            max_width=300
        )
    ).add_to(m)

st_folium(
    m,
    width=1200,
    height=600
)


# HOURLY TREND CHART:Shows violation distribution for selected station across all hours of the day
hourly = (
    df[df["police_station"] == selected_station]
    .groupby("hour")
    .size()
)

st.subheader("Violations by Hour")

st.line_chart(hourly)

# DATA TABLE:
st.subheader("Sample Violation Records")

st.dataframe(
    filtered_df.head(20)
)