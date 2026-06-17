# Day 1 Detailed Plan — Data Engineering + Base Map

**Goal by end of Day 1:**

A police officer can:

1. Select a police station
2. Select an hour
3. See all parking violations on a Bengaluru map

Nothing more.

No DBSCAN.
No AI.
No Severity.
No Routing.

Just build a rock-solid foundation.

---

# 0. Project Setup (30 min)

Create structure:

```text
parksense-ai/

data/
│
├── parking_data.csv

src/
│
├── data_prep.py
│
app.py

requirements.txt
```

---

Install dependencies:

```bash
pip install pandas streamlit folium streamlit-folium numpy
```

Create requirements:

```bash
pip freeze > requirements.txt
```

---

# 1. Explore Dataset Properly (30-45 min)

Before writing a single line of cleaning code:

```python
df = pd.read_csv("data/parking_data.csv")
```

Print:

```python
print(df.shape)

print(df.columns)

print(df.head())

print(df.info())
```

Check:

```python
latitude
longitude
created_date
violation_type
vehicle_type
police_station
```

Actually exist.

---

Check missing values:

```python
print(df.isnull().sum())
```

---

Check unique stations:

```python
print(df["police_station"].nunique())

print(df["police_station"].unique())
```

---

Check violation types:

```python
print(df["violation_type"].head(20))
```

You want to confirm it looks like:

```python
["NO PARKING"]

["WRONG PARKING"]
```

or something weird.

---

### Deliverable

Understand dataset structure.

Do NOT skip this.

---

# 2. Create data_prep.py

File:

```text
src/data_prep.py
```

Purpose:

```text
Raw CSV
     ↓
Clean DataFrame
```

---

# 3. Load Dataset

Function:

```python
def load_data():
```

Inside:

```python
df = pd.read_csv(...)
```

---

Print shape:

```python
print("Original Shape:", df.shape)
```

---

# 4. Clean Coordinates

Most important step.

---

Remove null coordinates:

```python
df = df.dropna(
    subset=[
        "latitude",
        "longitude"
    ]
)
```

---

Remove impossible coordinates:

```python
df = df[
    (df["latitude"].between(-90,90))
]
```

```python
df = df[
    (df["longitude"].between(-180,180))
]
```

---

Optional:

Since this is Bengaluru:

```python
df = df[
    (df["latitude"].between(12.7,13.2))
]
```

```python
df = df[
    (df["longitude"].between(77.3,77.9))
]
```

Removes garbage locations.

---

Print shape again.

```python
print("After coord cleaning:", df.shape)
```

---

# 5. Parse Date Column

Convert:

```python
created_date
```

into datetime.

---

Example:

```python
df["created_date"] = pd.to_datetime(
    df["created_date"],
    errors="coerce"
)
```

---

Remove invalid dates:

```python
df = df.dropna(
    subset=["created_date"]
)
```

---

# 6. Create Time Features

Create:

```python
hour
```

```python
weekday
```

---

Hour:

```python
df["hour"] = (
    df["created_date"]
    .dt.hour
)
```

---

Weekday:

```python
df["weekday"] = (
    df["created_date"]
    .dt.dayofweek
)
```

---

Verify:

```python
print(
    df[
        ["created_date","hour","weekday"]
    ].head()
)
```

---

# 7. Parse Violation Type

This column is probably stored as:

```python
'["NO PARKING"]'
```

which is a string.

---

Import:

```python
import ast
```

---

Create helper:

```python
def parse_violation(x):
```

---

Logic:

```python
try:
    return ast.literal_eval(x)[0]
except:
    return x
```

---

Apply:

```python
df["violation_type"] = (
    df["violation_type"]
    .apply(parse_violation)
)
```

---

Check output:

```python
print(
    df["violation_type"]
    .unique()
)
```

---

# 8. Filter Parking Violations

Keep only:

```python
NO PARKING

WRONG PARKING
```

---

Example:

```python
parking_types = [
    "NO PARKING",
    "WRONG PARKING"
]
```

---

Filter:

```python
df = df[
    df["violation_type"]
    .isin(parking_types)
]
```

---

Print final shape.

```python
print(df.shape)
```

---

# 9. Verify Police Station Column

Check:

```python
print(
    df["police_station"]
    .value_counts()
)
```

---

Remove null stations:

```python
df = df.dropna(
    subset=["police_station"]
)
```

---

Sort stations:

```python
stations = sorted(
    df["police_station"]
    .unique()
)
```

---

You'll use this tomorrow for filtering.

---

# 10. Return Clean DataFrame

End of function:

```python
return df
```

---

# 11. Test data_prep.py

Run:

```bash
python src/data_prep.py
```

Expected:

```text
Original Shape

After Cleaning

Stations Found

Violation Types

Sample Rows
```

No errors.

---

# 12. Create app.py

Now start UI.

---

Import:

```python
streamlit

folium

streamlit_folium

load_data
```

---

Page title:

```python
st.set_page_config(
    page_title="ParkSense AI"
)
```

---

Heading:

```python
st.title(
    "ParkSense AI"
)
```

---

Load data:

```python
df = load_data()
```

---

# 13. Create Sidebar Filters

Police Station dropdown:

```python
station = st.sidebar.selectbox(
    ...
)
```

---

Populate from:

```python
df["police_station"]
```

---

Hour slider:

```python
hour = st.sidebar.slider(
    ...
)
```

Range:

```python
0
23
```

---

# 14. Filter Dataset

Create:

```python
filtered_df
```

Conditions:

```python
selected station
```

and

```python
selected hour
```

---

Check:

```python
st.write(
    filtered_df.shape
)
```

Just for debugging.

---

# 15. Create Folium Map

Center:

```python
Bengaluru
```

Coordinates:

```python
12.9716

77.5946
```

---

Create:

```python
m = folium.Map(
    location=[12.9716,77.5946],
    zoom_start=11
)
```

---

# 16. Add Markers

Loop:

```python
for row in filtered_df
```

---

Add:

```python
folium.CircleMarker
```

Use:

```python
latitude

longitude
```

---

Radius:

```python
3
```

---

Color:

```python
blue
```

---

Popup:

```python
Violation Type

Vehicle Type

Police Station

Time
```

---

# 17. Render Map

Use:

```python
st_folium(m)
```

---

Map should appear.

---

# 18. Validate Everything

Test:

### Station Filter

Bellandur

↓

Madiwala

↓

Whitefield

Map changes.

---

### Hour Filter

```text
8 AM
```

↓

```text
6 PM
```

Map changes.

---

### Empty Results

If no records:

Show:

```python
st.warning(
    "No violations found"
)
```

---

# 19. Optional Nice Touches (Only If Time Left)

Show metrics:

```python
Total Records
```

```python
Unique Stations
```

```python
Visible Violations
```

using:

```python
st.metric()
```

---

# Day 1 Final Deliverable Checklist

### Data

* [ ] CSV loads
* [ ] Null coordinates removed
* [ ] Invalid coordinates removed
* [ ] Datetime parsed
* [ ] Hour extracted
* [ ] Weekday extracted
* [ ] Violation types parsed
* [ ] Parking violations filtered

### Dashboard

* [ ] Streamlit app runs
* [ ] Police station dropdown works
* [ ] Hour slider works
* [ ] Folium map renders

### Map

* [ ] Markers visible
* [ ] Popups visible
* [ ] Filters update map

### Demo

You should be able to say:

> "This dashboard visualizes historical parking violations across Bengaluru. Officers can filter by police station and time of day to understand where violations are concentrated before we apply our hotspot detection engine on Day 2."

If you finish all of this cleanly, Day 2 (DBSCAN hotspots) becomes very straightforward because all the filtering and mapping infrastructure is already done.
