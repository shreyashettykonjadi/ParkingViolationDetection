# PRODUCT REQUIREMENTS DOCUMENT
## NagaraNetra
### Traffic Impact Intelligence & Enforcement Planning Platform

**Operational Challenge**
Poor Visibility on Parking-Induced Congestion
How can AI-driven parking intelligence detect illegal parking hotspots and quantify their impact on traffic flow to enable targeted enforcement?

| Field | Value |
|---|---|
| Document Version | 3.0 (Post-Build Update) |
| Status | Updated to reflect actual hackathon build |
| Dataset | Bengaluru Traffic Police — Traffic Enforcement Records, Nov 2023–Apr 2024 (anonymized) |
| Primary Stakeholders | Bengaluru Traffic Police, Traffic Command Centers, Enforcement Supervisors, Urban Mobility Authorities |
| Build Format | Hackathon single-sprint build, ~24–36 hours |

> **v3.0 Change Summary:** This revision updates Sections 6, 9, 10, 16, and 24 to reflect the actual system built during the hackathon sprint. The Observation Confidence Engine (Section 10) was descoped from V1. The Spatial Intelligence Engine (Section 9) was expanded into a three-layer HDBSCAN architecture. The system now uses a SQLite pre-computation pipeline instead of in-memory computation at dashboard runtime.

---

## 1. Executive Summary

Bengaluru Traffic Police currently address illegal and spillover parking reactively, dispatching patrols after congestion has already formed at a location. The department holds a historical dataset of 298,450 traffic enforcement records (validated subset feeds production) spanning 54 police stations over a five-month period (Nov 2023–Apr 2024). This dataset records where violations occurred, but on its own it cannot answer the questions that matter operationally.

- Which violations are actually contributing to traffic congestion, as opposed to being isolated or low-impact incidents
- Which locations are recurring hotspots versus one-off occurrences
- Which of the many hotspots should be prioritized first, given finite enforcement capacity
- Which locations are likely to become problems in the next few hours or days, before violations accumulate

NagaraNetra converts this historical violation log into actionable enforcement intelligence. It does this by combining a three-layer spatial hotspot detection system, road-network intelligence, urban context enrichment, live traffic conditions, a deterministic Traffic Impact Score, and a Live Enforcement page that recommends deployment locations in real time.

**Core architectural insight:** Run all HDBSCAN clustering in a one-time offline build pipeline. Store results in SQLite. Streamlit reads pre-computed results — no clustering ever runs during a dashboard session.

---

## 2. Product Vision

Move Bengaluru Traffic Police from reactive enforcement to predictive enforcement intelligence. A traffic officer or command center supervisor opening the NagaraNetra dashboard should be able to answer five questions within seconds:

1. **What is happening?** — where are the active parking hotspots right now
2. **How severe is it?** — what is the quantified traffic impact of each hotspot
3. **What patterns exist?** — which zones are persistently problematic vs. recently emerging
4. **Which context matters now?** — which hotspots are worst for the current time of day and day type
5. **Which locations deserve attention first?** — a ranked, explainable enforcement queue

---

## 3. Problem Statement

### 3.1 Operational Challenge

On-street illegal parking and spillover parking near metro stations, commercial districts, hospitals, schools, markets, and junctions reduce effective road capacity and contribute materially to congestion.

### 3.2 Why This Is Hard Today

- Enforcement is patrol-based and reactive
- There is no heatmap or unified view that connects parking violation density to actual traffic congestion impact
- It is difficult to prioritize enforcement zones objectively

### 3.3 Problem Statement Direction

> How can AI-driven parking intelligence detect illegal parking hotspots and quantify their impact on traffic flow to enable targeted enforcement?

---

## 4. Product Objectives

### 4.1 Primary Objectives

- Detect parking violation hotspots using a three-layer HDBSCAN system (persistent, contextual, monthly)
- Pre-compute all hotspot analytics into SQLite so the dashboard operates with zero runtime computation
- Surface enforcement recommendations that adapt to the current time of day and day type
- Prioritize enforcement zones with a ranked, explainable enforcement queue

### 4.2 Secondary Objectives

- Provide explainable recommendations — every score is traceable to its contributing factors
- Expose a clean tool/function interface so a future autonomous enforcement-planning agent can consume the same engines

### 4.3 Out of Scope (V1)

- Automated dispatch, ticketing, or any action that directly triggers enforcement without human review
- Real-time violation detection from live camera/video feeds
- Citizen-facing reporting or parking-availability features
- **Observation Confidence / Patrol Bias correction** — descoped from V1 (see Section 10)
- Hotspot Forecasting Engine (Sections 17–23) — descoped from V1 sprint; architecture documented for V2

### 4.4 Hackathon Build Plan & MVP Demo Scope

#### 4.4.1 Time Budget

| Phase | Focus |
|---|---|
| Phase 0 — Setup | Repo scaffold, dataset load, env setup |
| Phase 1 — Pipeline & Clustering | Data cleaning, feature extraction, three-layer HDBSCAN build pipeline |
| Phase 2 — SQLite Build | Pre-compute all hotspot tables, indexes, enforcement scores |
| Phase 3 — Dashboard | 4-tab Streamlit app reading from SQLite only |
| Phase 4 — Polish & Rehearsal | Bug fixes, demo script rehearsal |

#### 4.4.2 MVP — What Ships in the Demo

- **Data pipeline:** Cleaned violations table from full 298,450-row dataset
- **Three-layer hotspot detection:** Persistent, Contextual, and Monthly HDBSCAN — all pre-computed into SQLite via `build_hotspots.py`
- **Live Enforcement page:** Auto-detects current IST time → loads the correct contextual hotspot cluster → ranks enforcement locations
- **Dashboard:** 4-tab Streamlit app (Violation Explorer, Persistent Hotspots, Monthly Hotspots, Live Enforcement)

---

## 5. Dataset

*(Unchanged from v2.0 — see original PRD)*

---

## 6. System Architecture

### ⚠️ v3.0 Update — SQLite Pre-Computation Architecture

NagaraNetra V1 uses a **build-once, query-always** architecture. All HDBSCAN clustering and hotspot analytics are computed offline in a single pipeline script (`build_hotspots.py`) and stored in a SQLite database (`data/hotspots.db`). The Streamlit dashboard reads from SQLite only — no clustering, no aggregation, and no external API calls happen during a dashboard session.

```
Raw Violations CSV (298,450 rows)
            |
            v
  [build_hotspots.py] — runs ONCE offline
            |
    +-------+-------+-------+
    |               |       |
    v               v       v
Layer 1:        Layer 2:  Layer 3:
Persistent      Contextual Monthly
HDBSCAN         HDBSCAN   HDBSCAN
(1 run,         (10 runs, (6 runs,
full data)      per ctx)  per month)
    |               |       |
    v               v       v
persistent_   contextual_ monthly_
hotspots      hotspots    hotspots
    |               |       |
    +-------+-------+-------+
                    |
              SQLite DB
            (data/hotspots.db)
                    |
                    v
        [Streamlit app.py]
        Queries SQLite only.
        Zero runtime computation.
                    |
         +----------+----------+
         |          |          |
         v          v          v
    Violation  Persistent  Monthly
    Explorer   Hotspots    Hotspots
                               |
                               v
                          Live Enforcement
                    (auto-detects current context)
```

### Database Tables

| Table | Rows | Source |
|---|---|---|
| `violations` | 298,450 | All raw violations + persistent_cluster_id |
| `persistent_hotspots` | 1,992 | 1 HDBSCAN run on full dataset |
| `contextual_hotspots` | 5,386 | 10 HDBSCAN runs (one per context) |
| `monthly_hotspots` | 4,177 | 6 HDBSCAN runs (one per month) |
| `hotspot_metadata` | 6 | Build config, date range |

---

## 7–8. Data Processing Pipeline

*(Unchanged from v2.0 — cleaning, parsing, normalization steps remain as specified)*

---

## 9. Spatial Intelligence Engine

### ⚠️ v3.0 Update — Three-Layer HDBSCAN Architecture

V1 implements three independent HDBSCAN layers, each serving a distinct analytical purpose. All 17 HDBSCAN runs occur exclusively inside `build_hotspots.py`. The Streamlit dashboard never runs HDBSCAN.

---

### Layer 1 — Persistent Hotspots (1 HDBSCAN run)

| Property | Value |
|---|---|
| Input | All 298,450 approved violation records |
| Algorithm | HDBSCAN, `metric="haversine"`, `cluster_selection_method="eom"` |
| min_cluster_size | 30 |
| Output | 1,992 geographic clusters + noise label (-1) |
| Storage | `persistent_hotspots` table (one row per cluster) |

**Purpose:** Identify stable, long-term geographic violation zones that persist across the entire five-month dataset. These are the "chronic problem areas" of Bengaluru.

**What is stored per cluster:**
- `centroid_lat`, `centroid_long` — geographic center
- `police_station`, `nearest_junction` — dominant values within cluster
- `top_violation`, `dominant_vehicle`, `peak_hour` — mode values
- `all_time_count` — total violations in cluster
- `persistent_rank` — rank by all_time_count

**Cluster ID scope:** Global and stable. `cluster_id=7` in `persistent_hotspots` always refers to the same geographic zone.

---

### Layer 2 — Contextual Hotspots (10 HDBSCAN runs)

| Property | Value |
|---|---|
| Input | Violations filtered to one (day_type × time_bucket) context |
| Algorithm | HDBSCAN, `metric="haversine"`, `cluster_selection_method="eom"` |
| min_cluster_size | 15 |
| Output | 5,386 clusters total across all contexts |
| Storage | `contextual_hotspots` table — all 10 runs, separated by `context` column |

**10 Contexts:**

| Weekday | Weekend |
|---|---|
| weekday_night | weekend_night |
| weekday_morning_peak | weekend_morning_peak |
| weekday_midday | weekend_midday |
| weekday_evening_peak | weekend_evening_peak |
| weekday_late_night | weekend_late_night |

**Time bucket definitions:**

| Bucket | Hours |
|---|---|
| night | 12am – 6am |
| morning_peak | 6am – 9am |
| midday | 9am – 2pm |
| evening_peak | 2pm – 8pm |
| late_night | 8pm – 12am |

**Purpose:** Detect hotspots that are specific to a particular operating context. A zone that is dangerous during weekday evening peak may be irrelevant on weekend nights. This layer answers: "Given it is Tuesday at 7pm, which zones should I send patrols to?"

**Cluster ID scope:** Local to each context. `cluster_id=3` in `weekday_morning_peak` is a different zone from `cluster_id=3` in `weekend_evening_peak`. Primary key is `(context, cluster_id)`.

**Extra columns (contextual layer only):**
- `recent_count` — violations in the last 30 days of the dataset within this cluster
- `enforcement_score` = 0.6 × historical_volume_percentile + 0.4 × recent_count_percentile
- `context_rank` — rank within context by enforcement_score

**Enforcement Score Formula:**

```
enforcement_score = 0.6 × violation_count_percentile
                  + 0.4 × recent_count_percentile
```

This weights historical volume (how often this zone has problems in this context) against recent activity (whether the problem is getting worse lately), without requiring an external traffic API.

---

### Layer 3 — Monthly Hotspots (6 HDBSCAN runs)

| Property | Value |
|---|---|
| Input | Violations filtered to one calendar month |
| Algorithm | HDBSCAN, `metric="haversine"`, `cluster_selection_method="eom"` |
| min_cluster_size | 20 |
| Output | 4,177 clusters total across all months |
| Storage | `monthly_hotspots` table — all 6 runs, separated by `year_month` column |

**6 Months:** 2023-11, 2023-12, 2024-01, 2024-02, 2024-03, 2024-04

**Purpose:** Show how hotspot geography evolves month to month. A zone that appears in Jan 2024 but not Nov 2023 signals a newly emerging problem area. Cluster IDs are local to each month; primary key is `(year_month, cluster_id)`.

**Cluster ID scope:** Local to each month. `cluster_id=0` in `2024-01` is independent from `cluster_id=0` in `2024-04`.

---

### Why HDBSCAN over DBSCAN (unchanged)

HDBSCAN handles varying violation densities across the city — a dense CBD cluster and a sparse suburban cluster can both be detected correctly without manually tuning a single epsilon radius per area.

---

## 10. Observation Confidence Engine

### ⚠️ v3.0 Update — Descoped from V1

**Decision:** The Observation Confidence Engine has been removed from V1 scope.

**Reason:** The engine assumed that `device_id` and `created_by_id` diversity within a cluster indicates independent verification rather than patrol artifacts. After analysis, we could not confirm the provenance of these fields — if the entire dataset originates from a single centralized enforcement system, diversity in device/officer IDs reflects operational logistics rather than independent corroboration. Implementing the engine under this uncertainty would produce a confidence signal of unknown validity.

**V2 path:** If ground-truth patrol route data or independent violation reporting channels become available, the Observation Confidence Engine can be re-introduced using violation_type diversity as a proxy (a genuine hotspot shows multiple violation types; a patrol artifact tends to show one repeated type).

**Impact on Section 16:** The Enforcement Priority Score no longer multiplies by Observation Confidence. See updated Section 16 below.

---

## 11–13. Road Intelligence, Urban Context, Live Traffic

*(Unchanged from v2.0 — OSM/Overpass/TomTom enrichment architecture remains as specified for V2 implementation)*

> **V1 Note:** Sections 11–13 enrichment (OSM Road Intelligence, Urban Context, TomTom live layer) are documented for the V2 roadmap. V1 uses violation density, vehicle type, time-of-day, and spatial clustering as the primary signals. External API enrichment is the next planned enhancement.

---

## 14. Traffic Impact Intelligence Engine

*(Components unchanged from v2.0)*

### 14.2 Traffic Impact Formula (unchanged)

```
Traffic Impact Score
  = 0.25 × Parking Pressure
  + 0.15 × Vehicle Severity
  + 0.15 × Road Criticality
  + 0.15 × Live Congestion Factor
  + 0.10 × Junction Risk
  + 0.10 × Urban Activity Factor
  + 0.10 × Temporal Criticality

(all components independently normalized to 0–1 before weighting;
 weights sum to 1.00; output scaled to 0–100)
```

---

## 16. Enforcement Zone Ranking

### ⚠️ v3.0 Update — Observation Confidence Removed

**V1 Enforcement Score (Contextual Hotspots):**

```
enforcement_score = 0.6 × violation_count_percentile
                  + 0.4 × recent_count_percentile
```

This is computed per contextual cluster during `build_hotspots.py` and stored in `contextual_hotspots.enforcement_score`. The dashboard reads this pre-computed value directly.

**V2 Planned Formula (when Observation Confidence is re-introduced):**

```
Enforcement Priority Score = Traffic Impact Score × Observation Confidence
```

The multiplicative combination remains correct in principle — a high-impact hotspot with unverified observation should be pulled down sharply, not just slightly discounted.

**Output:** A ranked enforcement queue ordered by `context_rank` (contextual layer) or `persistent_rank` (persistent layer), surfaced in the Live Enforcement tab of the dashboard.

---

## 17–23. Layer 2 — Hotspot Forecasting Engine

*(Unchanged from v2.0 — documented for V2 roadmap)*

> **V1 Note:** The Forecasting Engine (Random Forest / XGBoost, time-aware train/val/test split, recurrence intelligence) is documented for the V2 roadmap. V1 focuses on the pre-computation pipeline and contextual hotspot ranking.

---

## 24. Dashboard

### ⚠️ v3.0 Update — 4-Tab Structure

V1 delivers a 4-tab Streamlit dashboard. All tabs read from SQLite only — no computation at runtime.

---

### Tab 1 — Violation Explorer

**Purpose:** Raw violation browsing with filters.

**Controls (sidebar):**
- Police Station dropdown
- Hour of Day slider (0–23)
- Date picker

**Panels:**
- KPI row: Violations This Hour | Total Clusters | Total Dataset Records
- Interactive Folium map — individual violation points for selected station/hour/date
- Line chart — hourly violation distribution for selected station
- Data table — sample violation records

---

### Tab 2 — Persistent Hotspots

**Purpose:** Long-term geographic hotspot zones from the full-dataset HDBSCAN run.

**Controls:**
- Optional Police Station filter

**Panels:**
- KPI row: Clusters Shown | Violations in Top 20
- Folium map — top 20 persistent clusters, circle size = all_time_count, color = red
- Ranked table: Rank | Police Station | Junction | All-Time Violations | Top Violation | Peak Hour

**Data source:** `persistent_hotspots` table, ordered by `persistent_rank`.

---

### Tab 3 — Monthly Hotspots

**Purpose:** Month-specific hotspot clusters showing how violation geography shifts over time.

**Controls:**
- Month selector (Nov 2023 – Apr 2024)
- Optional Police Station filter

**Panels:**
- KPI row: Active Clusters | Violations in Selected Month
- Folium map — top 20 monthly clusters, color = purple
- Ranked table: Rank | Police Station | Junction | Violations | Top Violation | Peak Hour

**Data source:** `monthly_hotspots` table filtered by `year_month`.

---

### Tab 4 — Live Enforcement

**Purpose:** Real-time enforcement recommendations based on current time and day.

**Auto-detection:**
1. System detects current IST time
2. Determines day_type (weekday / weekend)
3. Determines time_bucket (night / morning_peak / midday / evening_peak / late_night)
4. Builds context string: e.g. `weekday_evening_peak`
5. Loads pre-computed contextual hotspots for that context

**Override toggle:** Allows demo/simulation of any context without waiting for the actual time.

**Panels:**
- Context indicator: "Current IST: 18:43 on Tuesday → weekday_evening_peak"
- KPI row: Enforcement Zones | Historical Violations (this context) | Recent Violations (last 30d)
- Folium map — top 20 contextual clusters, color = orange, size = violation_count
- Enforcement Priority Queue table: Priority | Police Station | Junction | Historical | Last 30d | Score | Top Violation | Peak Hour

**Data source:** `contextual_hotspots` table filtered by `context`, ordered by `context_rank`.

**No computation at runtime.** All enforcement scores were calculated during `build_hotspots.py`.

---

## 25. Explainability Requirements

*(Unchanged from v2.0 — requirement stands for V2 full implementation)*

Every score surfaced must answer "why was this hotspot ranked here?" Each Traffic Impact Score component should expose its normalized contribution so explanation text can be generated from the highest-weighted contributing factors.

---

## 26. Future Agent Integration

*(Unchanged from v2.0)*

---

## 27. Final Feature Inventory

*(Unchanged from v2.0 — historical/live boundary remains as specified)*

---

## 28. Deliverables

| Deliverable | Status |
|---|---|
| `data/clean_parking_data.csv` | Complete |
| `src/hotspot_detection.py` | Complete — HDBSCAN wrapper |
| `build_hotspots.py` | Complete — 17-run pipeline, generates `hotspots.db` |
| `src/db.py` | Complete — all SQLite query functions |
| `app.py` | Complete — 4-tab Streamlit dashboard |
| `data/hotspots.db` | Complete — 5 tables, 9 indexes |
| `presentation/FEATURE1.md` | Complete |
| `presentation/HDBSCAN_ARCHITECTURE.md` | Complete |

---

## 29. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| HDBSCAN produces too few/many clusters | Tune `min_cluster_size` per layer independently; current values (30/15/20) validated on 298k records |
| Dashboard slow on first load | All computation pre-done in build pipeline; Streamlit queries indexed SQLite, sub-second response |
| Observation Confidence removed | Documented in Section 10; V2 path defined |
| Contextual cluster IDs not globally unique | By design — primary key is always `(context, cluster_id)` or `(year_month, cluster_id)` |

---

## 30. Success Criteria

- Live Enforcement tab loads the correct context automatically based on IST time with zero manual input
- Persistent hotspot map renders all 1,992 clusters with correct centroids
- Monthly hotspot view switches between all 6 months with distinct cluster sets
- `build_hotspots.py` runs end-to-end in under 10 minutes and produces a valid SQLite database
- No HDBSCAN or pandas aggregation runs during any Streamlit session

---

## 31. Bonus & Differentiator Features

*(Unchanged from v2.0 for roadmap items)*

### 31.1 Demo-Ready Bonus Features (V2 Candidates)

- Tow-Truck / Patrol Dispatch Routing (OSRM)
- Smart Briefing Generator (Auto-Written Hotspot Summary)
- Repeat-Offender Radar
- Weather-Aware Context Multiplier
- Observation Confidence Engine (re-introduction with violation_type diversity proxy)
- Full Traffic Impact Score with OSM + TomTom enrichment

---

## 32. Hackathon Demo Script

### 32.1 Recommended Flow

1. Open dashboard → show **Violation Explorer** — "Here is the raw data. 298,450 records. Let's make it intelligent."
2. Switch to **Persistent Hotspots** — "These are the chronic problem areas of Bengaluru, identified by HDBSCAN on the full dataset. 1,992 geographic clusters."
3. Switch to **Monthly Hotspots** — "Watch how the hotspot map changes month by month. New zones appear in March that weren't there in November — those are emerging problems."
4. Switch to **Live Enforcement** — "It is currently [time] on [day]. The system automatically loaded `[context]`. These are the exact locations where enforcement should be deployed right now, ranked by historical pattern and recent activity. No clustering ran to produce this — it was all pre-computed."

### 32.2 What to Lead With If Judges Ask Technical Questions

- "We run 17 independent HDBSCAN models — one on the full dataset, ten on time-context subsets, six on monthly subsets — all pre-computed, stored in SQLite, and served instantly."
- "The Live Enforcement page detects the current IST time and loads the correct context automatically. No computation at query time."
- "We deliberately removed the patrol bias correction because we couldn't verify the provenance of the device_id field. We documented the decision and the V2 path rather than shipping a metric of unknown validity."
