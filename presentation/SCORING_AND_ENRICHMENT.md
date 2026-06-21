# Scoring & Enrichment — Complete Reference

How NagaraNetra assigns attributes to a hotspot, scores it for enforcement, and
re-ranks it by traffic impact. Every formula below is taken directly from the
implementation (`build_hotspots.py`, `src/enrichment.py`, `app.py`).

---

## 1. What is a "hotspot"?

A hotspot is **one HDBSCAN cluster** — a dense blob of individual violation
points. A single cluster can contain hundreds of violations. Every per-hotspot
attribute (centroid, police station, top violation, etc.) is an **aggregate over
all the violation rows inside that cluster**.

The aggregation happens once, offline, in `build_hotspots.py` →
`_cluster_summary()`:

```python
valid_df.groupby(cluster_id).agg(
    centroid_lat     = ("latitude",          "mean"),   # geometric center
    centroid_long    = ("longitude",         "mean"),
    top_violation    = ("primary_violation", mode),      # most common
    dominant_vehicle = ("vehicle_type",      mode),
    nearest_junction = ("junction_name",     mode),
    police_station   = ("police_station",     mode),      # most common station
    peak_hour        = ("hour",              mode),
    violation_count  = (cluster_id,          "count"),
)
```

---

## 2. How is a police station assigned to a hotspot?

**By majority vote (statistical mode).**

```python
def _mode(series):
    counts = series.dropna().value_counts()
    return counts.index[0]      # the most frequent value
```

For each cluster, we look at the `police_station` field of **every violation in
that cluster** and pick the **one that appears most often**.

### Your question: "there might be multiple police stations in a hotspot"

Correct — a single geographic cluster can straddle two or three station
jurisdictions. The current design **does not split** the cluster. It assigns the
**dominant station** (the one that logged the most violations in that cluster).

Example:

| Violations in cluster | Station |
|---|---|
| 412 | Koramangala |
| 88 | Madiwala |
| 30 | Adugodi |

→ Hotspot is labelled **Koramangala** (412 is the plurality).

**Why this is acceptable:** the police station label is only a **human-readable
tag** for the officer reading the queue ("send the Koramangala unit"). It is
**not** used for any geographic calculation. All spatial work — the map pin and
the OSM enrichment — uses the **centroid**, not the station. So even if the
station label is imperfect for a border-straddling cluster, the impact score is
unaffected.

> **Known limitation (documented):** for a hotspot that genuinely sits on a
> jurisdiction boundary, only the dominant station is shown. A future version
> could list the top-2 stations or split by jurisdiction polygon. Out of scope
> for V1.

---

## 3. What is the Contextual Score (`enforcement_score`)?

This is the score that ranks the **top 20 in the Live Enforcement tab** (the
*first* table). It measures **parking-violation pressure only** — how busy a zone
is and whether it is getting worse. It uses **no road or urban data**.

Computed per contextual cluster in `build_contextual_layer()`:

```python
violation_count_pct = violation_count.rank(pct=True)   # all-time volume, percentile 0–1
recent_count_pct    = recent_count.rank(pct=True)       # last-30-day volume, percentile 0–1

enforcement_score   = 0.6 × violation_count_pct + 0.4 × recent_count_pct
```

| Term | Meaning |
|---|---|
| `violation_count` | Total violations in this cluster, **within this context** (e.g. weekday evening peak) |
| `recent_count` | Violations in this cluster in the **last 30 days** of the dataset |
| `.rank(pct=True)` | Converts a raw count into a **percentile (0–1)** relative to the other clusters in the same context |
| weight 0.6 | Historical/chronic pressure |
| weight 0.4 | Recent/worsening pressure |

`context_rank` = rank of clusters by `enforcement_score` (1 = worst) within that
context. The first Live Enforcement table is sorted by this.

**Plain English:** "Rank zones by how many violations happen here in this time
slot (60%), boosted if the problem has been active recently (40%)."

---

## 4. The Impact Score (Objective 2) — OSM re-ranking

This is the **second table** in the Live Enforcement tab. It takes the same top
20 zones and re-ranks them by **how much traffic disruption** each one causes,
not just how many tickets were written. It adds **road**, **urban context**, and
**vehicle** signals on top of the volume signal.

### 4.1 Which coordinates fetch the OSM data?

**The hotspot CENTROID** — the mean latitude/longitude of all violations in the
cluster. **Not** the police station location.

From `app.py`:

```python
key  = enrichment.cell_key(r["centroid_lat"], r["centroid_long"])
feat = enrichment.enrich_centroid(r["centroid_lat"], r["centroid_long"])
```

So when we count "15 hospitals nearby," that is **15 hospitals within 1 km of the
cluster's geographic center** — the actual place the illegal parking is
happening — which is exactly what we want.

| Query | Radius around centroid | Returns | Cached? |
|---|---|---|---|
| Road features (OSM §11) | 50 m | road type, lane count, oneway, segment length | Yes (permanent) |
| Urban context (OSM §12) | 1000 m | counts of hospitals, offices, schools, transit, malls, etc. | Yes (permanent) |
| Live traffic (TomTom §13) | road segment at centroid | current speed, free-flow speed, congestion index | No — fetched fresh each run |

OSM data is static, so it is cached permanently in `hotspot_enrichment`. **TomTom
congestion is live** — it changes minute to minute — so it is fetched fresh on
every button press and never written to the permanent cache.

Results are cached in the `hotspot_enrichment` SQLite table, keyed by the
centroid rounded to 4 decimals (~11 m grid), so the same place is never queried
twice.

### 4.2 The four impact components

Each component is normalized to **0–1** before weighting.

**(a) Road Criticality** — a violation on an arterial blocks more traffic than
one on a side street.
```
road_importance = ROAD_IMPORTANCE[road_type] / 5      # motorway/trunk=5 … residential=1
lanes_norm      = min(lane_count, 6) / 6
road_criticality = 0.6 × road_importance + 0.4 × lanes_norm
```

**(b) Urban Activity** — how much legitimate parking demand the area generates.
A weighted sum of the OSM counts, then min–max normalized across the 20 zones:
```
weighted = 3×hospitals + 3×railway_stations
         + 2×offices + 2×malls + 2×markets + 2×schools
         + 1×colleges + 1×universities + 1×bus_stops + 1×worship
         − 1×parking_lots        # legitimate supply slightly offsets demand
urban_activity = minmax(weighted)         # 0–1 across the current top-20
```

**(c) Vehicle Severity** — bigger vehicles block more carriageway.
```
vehicle_severity = VEHICLE_SEVERITY[dominant_vehicle] / 6
# scooter/motorcycle=1, car/auto=2, maxi-cab=3, bus=5, tanker/lorry/HGV=6
```

**(d) Junction Risk** — parking at a junction blocks turns and sightlines.
```
junction_risk = 1  if nearest_junction is a real junction
              = 0  if "No Junction" / null
```

**(e) Live Congestion** — actual measured traffic slowdown at the centroid.
```
congestion_index = 1 − (current_speed / free_flow_speed)   # 0–1, from TomTom
# e.g. 36 kmph vs 52 free-flow → 1 − 36/52 = 0.31 (31% congestion)
```

### 4.3 The final Impact Score formula

The formula has **two forms** depending on whether a TomTom key is configured.

**With live traffic (6 components — the active formula when a TomTom key is set):**
```
Impact Score (0–100) = 100 × (
      0.30 × enforcement_norm     # parking volume + recency (the §3 score)
    + 0.18 × road_criticality     # OSM road importance + lanes
    + 0.18 × urban_activity       # OSM hospitals/offices/transit/etc.
    + 0.14 × congestion_index     # TomTom live congestion
    + 0.12 × vehicle_severity     # dominant vehicle footprint
    + 0.08 × junction_risk        # at a junction or not
)
# weights sum to 1.00
```

**Without live traffic (5 components — fallback if no key / TomTom fails):**
```
Impact Score (0–100) = 100 × (
      0.35 × enforcement_norm + 0.20 × road_criticality
    + 0.20 × urban_activity + 0.15 × vehicle_severity + 0.10 × junction_risk
)
# congestion weight folded back into the other five; still sums to 1.00
```

| Weight (live) | Component | Source |
|---|---|---|
| 0.30 | `enforcement_norm` | existing contextual score (§3) — already 0–1 |
| 0.18 | `road_criticality` | OSM road query (centroid, 50 m) |
| 0.18 | `urban_activity` | OSM context query (centroid, 1 km) |
| 0.14 | `congestion_index` | TomTom Flow Segment API (centroid, live) |
| 0.12 | `vehicle_severity` | cluster's dominant vehicle type |
| 0.08 | `junction_risk` | cluster's nearest_junction field |

### 4.4 Re-ranking output

Zones are sorted by Impact Score (descending). The table shows:
- `new_rank` vs `old priority` (the §3 `context_rank`) with a **Δ-rank arrow** (▲/▼)
- every contributing factor column (road type, lanes, hospitals, offices, transit, sub-scores)
- a **`reason`** string built from the top contributing factors
  (e.g. *"Trunk · near 15 hospitals · 4 offices"*) — this is the explainable
  part of Objective 3.

---

## 5. Worked example (real output)

K.R. Pura zone, weekday evening peak:

| Field | Value | Where from |
|---|---|---|
| police_station | K.R. Pura | mode of cluster's violations (§2) |
| centroid | 13.0082, 77.6949 | mean lat/long of cluster |
| road_type | trunk | OSM 50 m around centroid |
| lane_count | 2 | OSM |
| hospital_count | 15 | OSM 1 km around centroid |
| office_count | 4 | OSM |
| road_criticality | 0.733 | 0.6×(5/5) + 0.4×(2/6) |
| urban_activity | 1.000 | highest weighted urban sum in the top-20 |
| vehicle_severity | 0.167 | dominant vehicle = car → 1/6 |
| **Impact Score** | **72.2** | weighted sum above → top rank |
| reason | "Trunk · near 15 hospital(s) · 4 offices" | top factors |

---

## 6. Summary of your questions

| Question | Answer |
|---|---|
| What is the contextual score based on? | 60% all-time violation volume + 40% last-30-day volume, as percentiles within the context. No road/urban data. |
| How is a police station assigned? | Majority vote — the station that logged the most violations in the cluster (statistical mode). |
| Multiple stations in one hotspot? | Yes possible; only the **dominant** one is shown. It's a display label only — no calculation uses it. Documented limitation. |
| What coordinates fetch hospital/office data? | The **hotspot centroid** (mean of violation lat/long), never the police station location. Road = 50 m, urban = 1 km, TomTom = segment at centroid. |
| Impact score formula? | Live: `100 × (0.30·enforcement + 0.18·road + 0.18·urban + 0.14·congestion + 0.12·vehicle + 0.08·junction)`. Fallback without TomTom drops congestion and re-balances. See §4.3. |
