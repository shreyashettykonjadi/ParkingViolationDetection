# PRODUCT REQUIREMENTS DOCUMENT
## NagaraNetra — Traffic Impact Intelligence & Enforcement Planning Platform

| Field | Value |
|---|---|
| Document Version | 3.1 — Final Implemented Build |
| Status | Reflects V1 as shipped |
| Dataset | Bengaluru Traffic Police Enforcement Records, Nov 2023 – Apr 2024 |
| Records | 298,450 approved violations across 54 police stations |
| Primary Stakeholders | Traffic Command Centers, Enforcement Supervisors, Station Inspectors, Traffic Analysts |

---

## 1. The Problem

Bengaluru Traffic Police hold five months of parking violation data — 298,450 records across 54 stations. But the data in its raw form cannot answer the questions that matter for operations:

- Which locations are genuinely chronic problems, not just a single patrol's snapshot?
- Which zones should be prioritized right now, given it is Tuesday at 7 PM?
- How did violation geography shift between November and March?
- Where should we send the next patrol unit?

Enforcement today is reactive: officers go where they already expect violations, which reinforces patrol patterns rather than revealing where the real problem is.

---

## 2. What NagaraNetra Does

NagaraNetra runs **17 independent HDBSCAN models** on the violation dataset — offline, once — and stores all results in a SQLite database. The Streamlit dashboard reads those pre-computed results in real time. No clustering, no aggregation, and no external API calls happen during a dashboard session.

The output is a ranked, filterable enforcement intelligence dashboard that answers four operational questions:

1. **What does the raw data look like?** (Violation Explorer)
2. **What are the chronic problem zones, city-wide?** (Persistent Hotspots)
3. **How has the violation geography evolved month by month?** (Monthly Hotspots)
4. **Where should I send patrols right now, given the current time and day?** (Live Enforcement)

---

## 3. Who Uses It

### Traffic Command Center Supervisor
Opens the Live Enforcement tab at the start of each shift. The system has already detected the current IST time and day, loaded the correct temporal context, and ranked all enforcement zones by a score combining historical pattern strength and recent 30-day activity. The supervisor sees a ranked list and a map — no manual calculation required.

### Station-Level Inspector
Uses Persistent Hotspots filtered to their station. Sees which specific junctions in their jurisdiction have the highest all-time violation density and which vehicle types and violation categories dominate. Uses this for monthly planning and resource requests.

### Enforcement Planning Officer
Uses Monthly Hotspots to compare November through April side by side. A zone that appears in March but was absent in November is an emerging hotspot — a place to intervene before it becomes a chronic problem.

### Traffic Data Analyst
Uses Violation Explorer to drill into raw records by station, hour, and date. Validates data quality, checks specific incidents, and understands the underlying dataset before relying on aggregate views.

---

## 4. Dataset

| Attribute | Value |
|---|---|
| Source | Bengaluru Traffic Police enforcement records |
| Total records | 298,450 (approved violations only) |
| Police stations | 54 |
| Date range | 2023-11-09 to 2024-04-08 |
| Latitude range | 12.80 – 13.29 |
| Longitude range | 77.44 – 77.77 |
| Key columns used | latitude, longitude, created_datetime, hour, weekday, month, vehicle_type, primary_violation, police_station, junction_name, validation_status |

### Data Cleaning (build pipeline)
- Only `validation_status = approved` records used
- Invalid coordinates (null, (0,0), outside Bengaluru bounding box) dropped
- `created_datetime` parsed → hour (0–23), weekday (0–6), month, date, year_month extracted
- `violation_type` (JSON-style list field) parsed and primary violation extracted
- `year_month` column derived for monthly grouping
- `time_bucket` assigned via hour bins: night (0–6), morning_peak (6–9), midday (9–14), evening_peak (14–20), late_night (20–24)
- `day_type` assigned: weekday (Mon–Fri), weekend (Sat–Sun)

---

## 5. System Architecture

### Core Design Principle: Build Once, Query Always

```
clean_parking_data.csv (298,450 rows)
            │
            ▼
    build_hotspots.py          ← runs once offline
            │
    ┌───────┼───────┐
    ▼       ▼       ▼
 Layer 1  Layer 2  Layer 3
Persistent Contextual Monthly
(1 run)  (10 runs) (6 runs)
    │       │       │
    └───────┴───────┘
            │
      hotspots.db (SQLite)
      5 tables, 9 indexes
            │
            ▼
       app.py (Streamlit)
    Reads SQLite only.
    Zero runtime ML.
```

Streamlit never imports HDBSCAN. All intelligence is pre-computed. Dashboard latency is bounded by SQLite indexed reads, not by clustering time.

---

## 6. HDBSCAN Pipeline — 17 Runs

### Why HDBSCAN

HDBSCAN (Hierarchical Density-Based Spatial Clustering of Applications with Noise) detects clusters of arbitrary shape and handles uneven violation density across the city. A dense CBD cluster and a sparse suburban cluster are both found correctly with the same parameters. Standard DBSCAN requires manually tuning a single epsilon radius per area — impractical city-wide.

All runs use:
- `metric = "haversine"` (geographically correct great-circle distance)
- `cluster_selection_method = "eom"` (excess of mass — stable cluster boundaries)
- Input: `[latitude, longitude]` in radians

### Layer 1 — Persistent Hotspots (1 run)

| Parameter | Value |
|---|---|
| Input | All 298,450 records |
| min_cluster_size | 30 |
| Output | 1,992 clusters |
| Stored in | `persistent_hotspots` table |
| Ranked by | `all_time_count` (total violations in cluster) |

**Purpose:** Identify stable geographic zones that are problematic across the full five-month window. These are the chronic problem areas of Bengaluru — they exist regardless of time of day, day of week, or month.

Each cluster stores: centroid lat/long, dominant police station, nearest junction (mode), top violation type (mode), dominant vehicle type (mode), peak hour (mode), all-time violation count, persistent_rank.

### Layer 2 — Contextual Hotspots (10 runs)

**10 contexts = 2 day types × 5 time buckets:**

| Day Type | Time Bucket | Hour Range |
|---|---|---|
| weekday / weekend | night | 00:00 – 06:00 |
| weekday / weekend | morning_peak | 06:00 – 09:00 |
| weekday / weekend | midday | 09:00 – 14:00 |
| weekday / weekend | evening_peak | 14:00 – 20:00 |
| weekday / weekend | late_night | 20:00 – 24:00 |

| Parameter | Value |
|---|---|
| Input per run | Violations filtered to that (day_type, time_bucket) |
| min_cluster_size | 15 |
| Output total | 5,386 clusters across all 10 contexts |
| Stored in | `contextual_hotspots` table (context column separates runs) |
| Primary key | (context, cluster_id) |
| Ranked by | enforcement_score |

**Enforcement Score:**
```
enforcement_score = 0.6 × violation_count_percentile
                  + 0.4 × recent_count_percentile
```
- `violation_count_percentile` — percentile rank of all-time violations within this context
- `recent_count_percentile` — percentile rank of violations in the last 30 days of the dataset

Weight rationale: historical volume (0.6) reflects the underlying parking behaviour pattern; recent activity (0.4) detects whether the problem is worsening, without requiring external traffic data.

**Purpose:** Show which zones are specifically dangerous for the current operating context. A zone that is a hotspot during weekday evening peak may be irrelevant on weekend nights. This is the layer that powers the Live Enforcement tab.

### Layer 3 — Monthly Hotspots (6 runs)

| Parameter | Value |
|---|---|
| Input per run | Violations for one calendar month |
| Months | 2023-11, 2023-12, 2024-01, 2024-02, 2024-03, 2024-04 |
| min_cluster_size | 20 |
| Output total | 4,177 clusters across all months |
| Stored in | `monthly_hotspots` table (year_month column separates runs) |
| Primary key | (year_month, cluster_id) |
| Ranked by | monthly violation_count |

**Purpose:** Show how hotspot geography evolves over time. An officer can select any month and see a distinct cluster map. A zone that appears in 2024-03 but was absent in 2023-11 is an emerging problem worth early intervention.

### Database Summary

| Table | Rows | Description |
|---|---|---|
| violations | 298,450 | All records + persistent_cluster_id assigned |
| persistent_hotspots | 1,992 | 1 HDBSCAN run, full dataset |
| contextual_hotspots | 5,386 | 10 HDBSCAN runs, separated by context |
| monthly_hotspots | 4,177 | 6 HDBSCAN runs, separated by year_month |
| hotspot_metadata | 6 | Build config: min_date, max_date, last_30d_cutoff |

**9 indexes** on police_station, hour, date, context, context_rank, year_month, monthly_rank, persistent_rank — all dashboard queries use indexed paths.

---

## 7. Dashboard — 4 Tabs

All tabs read from SQLite. No computation at runtime.

---

### Tab 1 — Violation Explorer

**Who uses it:** Traffic Analysts, data validation, ad-hoc investigation.

**Controls (sidebar):**
- Police Station — dropdown, 54 stations
- Hour of Day — slider, 0–23
- Date — date picker, bounded to dataset range (Nov 9 2023 – Apr 8 2024)

**Dashboard panels:**

| Panel | Content |
|---|---|
| KPI row | Violations This Hour / Total Persistent Clusters / Total Dataset Records |
| Map | Individual violation points (blue circles) for selected station/hour/date. Popups show vehicle type, violation type, junction |
| Line chart | Hourly violation distribution for selected station (all dates) |
| Data table | First 20 violation records matching filter |

**Data source:** `violations` table, `idx_violations_explorer` index on (police_station, hour, date).

---

### Tab 2 — Persistent Hotspots

**Who uses it:** Station inspectors for chronic zone identification, planning officers for jurisdiction-level review.

**Controls:**
- Police Station filter (optional, defaults to all)

**Dashboard panels:**

| Panel | Content |
|---|---|
| KPI row | Clusters Shown / Violations in Top 20 |
| Map | Top 20 persistent clusters (red circles). Size = all_time_count. Popup: rank, station, junction, violations, top violation type, peak hour |
| Ranked table | Rank / Police Station / Junction / All-Time Violations / Top Violation / Peak Hour |

**Data source:** `persistent_hotspots` ORDER BY persistent_rank, filtered by police_station if set.

---

### Tab 3 — Monthly Hotspots

**Who uses it:** Planning officers tracking trend and emergence of new problem zones.

**Controls:**
- Month selector — Nov 2023 through Apr 2024
- Police Station filter (optional)

**Dashboard panels:**

| Panel | Content |
|---|---|
| KPI row | Active Clusters (this month) / Violations in Selected Month |
| Map | Top 20 monthly clusters (purple circles). Size = monthly violation_count. Popup: rank, month, station, junction, violations, top violation type, peak hour |
| Ranked table | Rank / Police Station / Junction / Violations / Top Violation / Peak Hour |

**Data source:** `monthly_hotspots` WHERE year_month = ? ORDER BY monthly_rank.

---

### Tab 4 — Live Enforcement

**Who uses it:** Command center supervisors at the start of every shift for immediate deployment decisions.

**Auto-detection logic:**
```
Current IST time
      │
      ├── hour → time_bucket (night / morning_peak / midday / evening_peak / late_night)
      └── weekday() → day_type (weekday / weekend)
                │
                └── context = "{day_type}_{time_bucket}"
                    e.g. "weekday_evening_peak"
                         │
                         └── Load contextual_hotspots WHERE context = ?
                             ORDER BY context_rank LIMIT 20
```

**Demo override:** Checkbox to manually select any context — allows presentation at any time of day without losing demo clarity.

**Dashboard panels:**

| Panel | Content |
|---|---|
| Context indicator | "Current IST: 18:43 on Tuesday → weekday_evening_peak" |
| KPI row | Enforcement Zones / Historical Violations (this context) / Recent Violations (last 30d) |
| Map | Top 20 contextual clusters (orange circles). Size = violation_count. Popup: priority rank, station, junction, historical violations, last-30d violations, enforcement score, top violation type, peak hour |
| Enforcement Priority Queue table | Priority / Police Station / Junction / Historical / Last 30d / Score / Top Violation / Peak Hour |

**Data source:** `contextual_hotspots` WHERE context = ? ORDER BY context_rank. Uses composite index `idx_contextual_rank(context, context_rank)` — single B-tree lookup.

---

## 8. Build Pipeline

**Script:** `build_hotspots.py`
**Run once:** `python build_hotspots.py`
**Safe to re-run:** drops and recreates `data/hotspots.db` each time.

**Steps:**
1. Load `data/clean_parking_data.csv`
2. Parse dates, extract hour/weekday/month/year_month, assign time_bucket and day_type
3. Compute last_30d_cutoff (30 days before dataset max_date)
4. Create SQLite schema (5 tables)
5. Layer 1: Run HDBSCAN on full dataset → write violations + persistent_hotspots
6. Layer 2: For each of 10 contexts → run HDBSCAN → compute enforcement_score → write to contextual_hotspots
7. Layer 3: For each of 6 months → run HDBSCAN → rank by count → write to monthly_hotspots
8. Write hotspot_metadata (6 config rows)
9. Create 9 indexes

**Output:** `data/hotspots.db` — all analytics stored, dashboard-ready.

---

## 9. Files Delivered

| File | Purpose |
|---|---|
| `build_hotspots.py` | Offline build pipeline — runs all 17 HDBSCAN models |
| `src/hotspot_detection.py` | HDBSCAN wrapper (`run_hdbscan` function) |
| `src/db.py` | All SQLite query helpers — zero computation |
| `app.py` | 4-tab Streamlit dashboard |
| `data/clean_parking_data.csv` | Cleaned violation dataset |
| `data/hotspots.db` | Pre-computed SQLite database (generated) |
| `presentation/FEATURE1.md` | Feature narrative — Context-Aware Dynamic Hotspot Discovery |
| `presentation/HDBSCAN_ARCHITECTURE.md` | Technical deep-dive — all 17 runs, storage, query mechanics |
| `requirements.txt` | Python dependencies including hdbscan==0.8.44 |

---

## 10. What Is NOT in V1

These features are documented in earlier PRD drafts but were explicitly descoped:

| Feature | Reason Not Built |
|---|---|
| Observation Confidence Engine | device_id / officer diversity is meaningless from a single centralized enforcement system — would produce a metric of unknown validity |
| OSM Road Intelligence (lane_count, maxspeed, road_type) | V2 roadmap — requires Overpass API enrichment per hotspot centroid |
| Urban Context (office_count, hospital_count, etc.) | V2 roadmap — requires Overpass API enrichment per hotspot centroid |
| TomTom Live Traffic (congestion_index, delay_ratio) | V2 roadmap — requires live API subscription |
| Full Traffic Impact Score (7-component formula) | Blocked by OSM/TomTom enrichment not being implemented |
| Hotspot Forecasting Engine (Random Forest / XGBoost) | V2 roadmap — needs more historical data to train reliably |
| Explainability layer (plain-language per-hotspot summaries) | V2 roadmap |
| OSRM patrol dispatch routing | V2 roadmap |
| Repeat-Offender Radar | V2 roadmap |

---

## 11. Success Criteria (V1)

| Criterion | Met |
|---|---|
| Live Enforcement auto-detects IST context without manual input | Yes |
| All 17 HDBSCAN runs complete and persist to SQLite | Yes |
| Dashboard never runs HDBSCAN at session time | Yes |
| Monthly tab shows distinct cluster maps per month | Yes |
| build_hotspots.py idempotent (safe to re-run) | Yes |
| All tabs load sub-second (indexed SQLite reads) | Yes |

---

## 12. V2 Roadmap

In priority order:

1. **OSM Road Intelligence** — enrich each hotspot centroid with lane count, road type, max speed, no-parking zone flag via Overpass API. Cache permanently. Powers Road Criticality component of Traffic Impact Score.
2. **Urban Context Enrichment** — enrich each centroid with office, hospital, school, transit, mall counts via Overpass. Powers Urban Activity Factor.
3. **Full Traffic Impact Score** — implement the 7-component weighted formula once OSM enrichment is available.
4. **TomTom Live Layer** — pull congestion_index and delay_ratio for dashboard-visible hotspots on a 2–5 minute refresh cadence.
5. **Forecasting Engine** — Random Forest on hotspot × hour aggregates, time-aware train/val/test split (Nov–Feb / March / April), predict next-hour violation volume.
6. **Observation Confidence** — re-introduce using violation_type diversity as proxy (a genuine hotspot shows multiple violation types; a single patrol's snapshot repeats one type).
7. **Explainability Layer** — plain-language per-hotspot summary from weighted component contributions.
