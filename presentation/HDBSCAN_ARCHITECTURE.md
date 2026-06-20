# HDBSCAN Architecture — How Runs & Storage Work

---

## How Many HDBSCAN Runs Happen?

**Total: 17 independent HDBSCAN runs** inside `build_hotspots.py`.

| Layer | Runs | Input Data | min_cluster_size |
|---|---|---|---|
| Persistent | 1 | All 298,450 records | 30 |
| Contextual | 10 | One subset per context | 15 |
| Monthly | 6 | One subset per month | 20 |

All 17 runs happen **only during `python build_hotspots.py`**. Streamlit never runs HDBSCAN.

---

## Layer 1 — Persistent (1 HDBSCAN run)

**Input:** All 298,450 records — no filtering.

**What HDBSCAN does:** Looks at ALL lat/long points together and finds dense geographic blobs, regardless of time of day or month. This gives stable, long-term hotspot zones.

**Output:** 1,992 clusters. Each violation gets a `persistent_cluster_id` (or -1 for noise).

**How it's stored:**

```
violations table              persistent_hotspots table
──────────────────────────    ──────────────────────────────────
id = "FKID000000"        →    cluster_id = 7
persistent_cluster_id = 7     centroid_lat = 12.9716
latitude = 12.97              centroid_long = 77.60
hour = 18                     police_station = "Koramangala"
date = "2024-03-15"           all_time_count = 2584
...                           persistent_rank = 2
```

Every violation is stored in the `violations` table with its `persistent_cluster_id`. The `persistent_hotspots` table has one row per cluster — aggregated centroid, dominant station, top violation, rank.

---

## Layer 2 — Contextual (10 separate HDBSCAN runs)

**10 contexts = 2 day_types × 5 time buckets:**

```
weekday_night           weekend_night
weekday_morning_peak    weekend_morning_peak
weekday_midday          weekend_midday
weekday_evening_peak    weekend_evening_peak
weekday_late_night      weekend_late_night
```

**Each run:**
1. Filter violations to only that context (e.g., weekday + hour 6–9)
2. Run HDBSCAN independently on that subset
3. Cluster IDs start from 0 for each context

**Critical point:** Cluster IDs are NOT shared across contexts. `cluster_id=3` in `weekday_morning_peak` is a completely different geographic zone from `cluster_id=3` in `weekend_night`.

**Storage — all 10 runs go into ONE table** `contextual_hotspots`, distinguished by the `context` column:

```
contextual_hotspots
────────────────────────────────────────────────────────────────
context                  | cluster_id | police_station  | violation_count
"weekday_morning_peak"   | 0          | Koramangala     | 580
"weekday_morning_peak"   | 1          | Upparpet        | 430
"weekday_morning_peak"   | 2          | Adugodi         | 310
"weekend_evening_peak"   | 0          | Shivajinagar    | 720
"weekend_evening_peak"   | 1          | K.R. Pura       | 490
```

Primary key is `(context, cluster_id)` — so `cluster_id=0` exists in every context as separate rows.

**Extra columns only in contextual:**
- `recent_count` — violations in the last 30 days of the dataset within that cluster
- `enforcement_score` = 0.6 × historical volume percentile + 0.4 × recent count percentile
- `context_rank` — rank within that context by enforcement score

---

## Layer 3 — Monthly (6 separate HDBSCAN runs)

**6 months:** 2023-11, 2023-12, 2024-01, 2024-02, 2024-03, 2024-04

**Each run:**
1. Filter violations to only that month
2. Run HDBSCAN independently on that subset
3. Cluster IDs start from 0 for each month

**Storage — all 6 runs go into ONE table** `monthly_hotspots`, distinguished by `year_month`:

```
monthly_hotspots
──────────────────────────────────────────────────────────
year_month | cluster_id | police_station  | violation_count | monthly_rank
"2023-11"  | 0          | Upparpet        | 450             | 1
"2023-11"  | 1          | Madiwala        | 380             | 2
"2024-01"  | 0          | K.R. Pura       | 620             | 1
"2024-01"  | 1          | Koramangala     | 510             | 2
"2024-04"  | 0          | Shivajinagar    | 290             | 1
```

Primary key is `(year_month, cluster_id)`.

---

## How Fetching Works — "Apr 2024" Selected

When a user picks Apr 2024 in the Monthly Hotspots tab:

```sql
SELECT cluster_id, police_station, nearest_junction,
       centroid_lat, centroid_long, violation_count,
       top_violation, peak_hour, monthly_rank
FROM monthly_hotspots
WHERE year_month = "2024-04"
ORDER BY monthly_rank
LIMIT 20
```

SQLite uses `idx_monthly_year_month` → instantly returns only April rows. Those clusters have their own centroids, their own police_station labels, their own counts — completely independent of every other month.

---

## How Fetching Works — Live Enforcement

App auto-detects current IST time → builds context string → queries:

```sql
SELECT cluster_id, police_station, nearest_junction,
       centroid_lat, centroid_long, violation_count,
       recent_count, enforcement_score, context_rank,
       top_violation, peak_hour
FROM contextual_hotspots
WHERE context = "weekday_evening_peak"
ORDER BY context_rank
LIMIT 20
```

Uses composite index `idx_contextual_rank ON contextual_hotspots(context, context_rank)` — single B-tree lookup, zero computation.

---

## Complete Database Structure

```
hotspots.db
├── violations              298,450 rows  — all violations + persistent_cluster_id
├── persistent_hotspots       1,992 rows  — 1 HDBSCAN run (full dataset)
├── contextual_hotspots       5,386 rows  — 10 HDBSCAN runs (context column separates them)
├── monthly_hotspots          4,177 rows  — 6 HDBSCAN runs (year_month column separates them)
└── hotspot_metadata              6 rows  — min_date, max_date, last_30d_cutoff
```

---

## Why Cluster IDs Are Not Global

Each HDBSCAN run produces IDs starting from 0 independently. A `cluster_id` only has meaning paired with its scope key:

| Layer | Unique identifier | Example |
|---|---|---|
| Persistent | `cluster_id` alone | cluster 7 = Koramangala zone |
| Contextual | `(context, cluster_id)` | (weekday_morning_peak, 7) = a morning rush zone |
| Monthly | `(year_month, cluster_id)` | (2024-01, 7) = a January-specific zone |

Global auto-increment IDs would add complexity without benefit since each layer is always queried in isolation.
