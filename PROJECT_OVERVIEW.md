# ParkSense AI — Project Overview

> **AI-Driven Parking Intelligence & Enforcement Dashboard**
> Flipkart GridLock Hackathon · Round 2

---

## 1. Problem Statement

**Visibility on Parking-Induced Congestion**

On-street illegal parking and spillover parking near commercial areas, metro
stations, and events choke carriageways and intersections. Enforcement today is
**patrol-based and reactive** — there is no heatmap of parking violations vs.
their congestion impact, and it is hard to prioritise which zones to police.

> **Direction:** How can AI-driven parking intelligence detect illegal parking
> hotspots and **quantify their impact on traffic flow** to enable **targeted
> enforcement**?

ParkSense AI answers this in two objectives:

- **Objective 1 — Detect hotspots:** find *where* and *when* illegal parking
  clusters using unsupervised spatial clustering (HDBSCAN) over ~298K real
  Bengaluru violation records.
- **Objective 2 — Quantify traffic impact:** re-rank those hotspots by how much
  they actually hurt traffic flow, fusing OpenStreetMap road/urban context with
  optional **live TomTom congestion**, so enforcement effort goes where it
  relieves the most congestion.

---

## 2. What It Does (At a Glance)

A **Streamlit dashboard** with four tabs, backed by a precomputed **SQLite**
database of hotspots:

| Tab | Purpose | Visual |
|-----|---------|--------|
| **Violation Explorer** | Browse raw violations by station / hour / date; hourly trend chart | 🔵 blue points |
| **Persistent Hotspots** | All-time geographic hotspots over the full dataset (Nov 2023 – Apr 2024) | 🔴 red clusters |
| **Monthly Hotspots** | Hotspots recomputed per calendar month (6 months) | 🟣 purple clusters |
| **Live Enforcement** | Context-aware enforcement priority queue (day-type × time-bucket), auto-following current IST; plus **Traffic Impact Re-Ranking** | 🟠 orange (priority) → 🟢 green (impact) |

---

## 3. Architecture

```
                 data/clean_parking_data.csv  (~298K violations)
                              │
                              ▼
                     build_hotspots.py            ← offline batch pipeline
              (3 independent HDBSCAN layers)
                              │
                              ▼
                     data/hotspots.db   (SQLite, precomputed)
                              │
            ┌─────────────────┴──────────────────┐
            ▼                                     ▼
        src/db.py                          app.py (Streamlit)
   (read-only query layer)      ┌──── Tabs 1–4 read from db.py
                                │
                                └── Tab 4 "Compute Impact" button
                                          │
                                          ▼
                                  src/enrichment.py
                            (Overpass / OSM + TomTom live)
                                          │
                              cached → hotspot_enrichment table
```

**Two-phase design:** heavy clustering runs **offline** in `build_hotspots.py`
and is persisted to SQLite, so the dashboard stays fast and read-only. OSM/live
enrichment is **on-demand** (button-triggered) and cached to avoid re-hitting
public APIs.

---

## 4. Codebase Map

| Path | Role |
|------|------|
| `app.py` (386 LOC) | Streamlit UI — 4 tabs, Folium maps, metrics, impact re-ranking |
| `build_hotspots.py` (351 LOC) | Offline pipeline: builds `hotspots.db` (3 HDBSCAN layers + schema + indexes). Safe to re-run (drops & recreates). |
| `src/hotspot_detection.py` | `run_hdbscan()` — HDBSCAN with **haversine** metric on lat/long radians |
| `src/db.py` (205 LOC) | Read-only SQLite query layer + label constants (`MONTH_LABELS`, `SHIFT_DISPLAY`) |
| `src/enrichment.py` (352 LOC) | OSM (Overpass) road + urban context, TomTom live traffic, and the impact scoring formula |
| `src/data_prep.py` | (currently empty placeholder) |
| `data/clean_parking_data.csv` | Cleaned input — 298,450 records |
| `data/parking_data.csv` | Raw input |
| `data/hotspots.db` | Generated artifact consumed by the app |
| `notebooks/` | EDA & prototyping: data cleaning, exploration, map prototype |
| `docs/` | `dataset_audit.md`, `plan.md` |
| `presentation/` | PRD versions + architecture/scoring write-ups |
| `.streamlit/secrets.toml` | Holds `TOMTOM_API_KEY` (optional) |

---

## 5. The Data

- **Source:** ~298K real Bengaluru parking-violation records, **Nov 2023 – Apr 2024**.
- **Key columns:** `latitude`, `longitude`, `vehicle_type`, `primary_violation`,
  `police_station`, `junction_name`, `hour`, `weekday`, `month`, `date`.
- **Derived in pipeline:**
  - `time_bucket` — `night` / `morning_peak` / `midday` / `evening_peak` /
    `late_night` (hour bins `[0,6,9,14,20,24]`).
  - `day_type` — `weekday` / `weekend` (from `weekday`).
  - `year_month` — for the monthly layer.

---

## 6. The Three Hotspot Layers (Objective 1)

All use **HDBSCAN** (density-based, no preset cluster count, labels noise as
`-1`) on geographic coordinates. Each centroid is summarised with its modal
violation type, dominant vehicle, modal junction/station, and peak hour.

| Layer | Scope | `min_cluster_size` | Ranked by |
|-------|-------|--------------------|-----------|
| **1. Persistent** | Full dataset | 30 | all-time violation count |
| **2. Contextual** | Per (day_type × time_bucket) → 10 runs | 15 | **enforcement score** |
| **3. Monthly** | Per calendar month → 6 runs | 20 | monthly violation count |

**Enforcement score (Layer 2):**
```
enforcement_score = 0.6 × percentile(historical volume)
                  + 0.4 × percentile(recent activity, last 30 days)
```
This balances chronic hotspots against currently-trending ones.

---

## 7. Traffic Impact Re-Ranking (Objective 2)

Triggered by the **"Compute Traffic Impact Scores"** button on the Live
Enforcement tab. For each top contextual zone it enriches the centroid and
recomputes a 0–100 **Impact Score**, then re-ranks (showing ▲/▼ vs. the
violation-only ranking).

**Data sources** (`src/enrichment.py`):
- **Road intelligence** (OSM, 50 m): highway type → importance 1–5, lanes,
  maxspeed, one-way, segment length.
- **Urban context** (OSM, 1 km): hospitals, offices, schools/colleges/universities,
  transit (bus stops, railway stations), malls/markets, religious places, and
  **parking lots** (which *offset* — legitimate supply).
- **Live congestion** (TomTom, optional): `congestion_index = 1 − current/free-flow speed`.

**Impact formula** (weights sum to 1.0; live component folds out when no key):

| Component | With live | Without live |
|-----------|:---------:|:------------:|
| Enforcement (violation volume) | 0.30 | 0.35 |
| Road criticality | 0.18 | 0.20 |
| Urban activity | 0.18 | 0.20 |
| Live congestion | 0.14 | — |
| Vehicle severity | 0.12 | 0.15 |
| Junction risk | 0.08 | 0.10 |

Each zone gets a plain-language **"Why"** reason (e.g. *"severe congestion (52%)
· Primary road · transit hub"*).

**Caching:** OSM features are deduped to an ~11 m grid key and stored in the
`hotspot_enrichment` table (Overpass never hit twice per centroid). Live traffic
is fetched fresh every run (never cached). All enrichment fails *gracefully* to
neutral defaults so the table always renders.

---

## 8. Database Schema (`data/hotspots.db`)

| Table | Contents |
|-------|----------|
| `violations` | Every record + its persistent cluster id, time_bucket, day_type |
| `persistent_hotspots` | Layer 1 centroids + `all_time_count`, `persistent_rank` |
| `contextual_hotspots` | Layer 2, keyed by `(context, cluster_id)` + `enforcement_score`, `recent_count` |
| `monthly_hotspots` | Layer 3, keyed by `(year_month, cluster_id)` |
| `hotspot_metadata` | Date range, contexts, last-30d cutoff, totals |
| `hotspot_enrichment` | OSM cache (created on demand by the app) |

Indexed for fast filtering by station/hour/date, context+rank, and year_month+rank.

---

## 9. Tech Stack

- **Python** · **Streamlit** (UI) · **streamlit-folium** + **Folium** (maps)
- **HDBSCAN** (clustering) · **pandas** / **numpy**
- **SQLite** (precomputed store) · **requests** (Overpass + TomTom)
- **APIs:** OpenStreetMap **Overpass** (keyless), **TomTom** Traffic (optional key)

---

## 10. How to Run

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Build the hotspot database (offline, re-runnable)
python build_hotspots.py

# 3. Launch the dashboard
streamlit run app.py
```

- **TomTom live congestion is optional** — set `TOMTOM_API_KEY` in
  `.streamlit/secrets.toml`. Without it, the impact score uses the 5-component
  formula and the app runs fully on free/keyless data.
- The app halts with a friendly error if `data/hotspots.db` is missing — run
  step 2 first.

---

## 11. Why This Approach Wins

- **Real data, real scale:** ~298K actual Bengaluru violations, not synthetic.
- **Unsupervised & label-free:** HDBSCAN needs no preset zones — it finds organic
  hotspots and discards noise.
- **Beyond a heatmap:** directly addresses the problem's hard part — *quantifying
  congestion impact* — by fusing violations + road criticality + urban demand +
  live traffic into a single, explainable enforcement priority.
- **Operationally usable:** the Live Enforcement queue auto-follows the current
  IST day/time, giving patrol teams a ranked, context-aware target list right now.
- **Fast & resilient:** heavy compute is offline & cached; every external call
  degrades gracefully so the dashboard never breaks.
```
