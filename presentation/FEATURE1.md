# Feature 1: Context-Aware Dynamic Hotspot Discovery

## Problem

Traditional hotspot detection systems identify illegal parking hotspots using the entire historical dataset and assume that hotspot locations remain fixed over time.

However, illegal parking behavior is highly dependent on temporal context:

* Weekday Morning Peak → Office districts and metro stations
* Weekday Evening Peak → Commercial areas and transit hubs
* Weekend Evening → Shopping districts and entertainment zones
* Late Night → Restaurants, event venues and nightlife zones

Using a single clustering model for all situations may hide context-specific parking patterns.

---

## Our Approach

Instead of generating one static hotspot map, we create context-specific hotspot models.

The dataset is divided into operational traffic contexts:

### Weekday

* Morning Peak
* Midday
* Evening Peak
* Night
* Late Night

### Weekend

* Morning Peak
* Midday
* Evening Peak
* Night
* Late Night

This results in 10 independent operating contexts.

---

## Context-Specific HDBSCAN

For each context:

1. Filter violations belonging to that context.
2. Run HDBSCAN independently using latitude and longitude.
3. Generate hotspot geometries unique to that context.
4. Store hotspot information in SQLite.

Example:

### Weekday Evening

HDBSCAN may identify:

* Metro Station Exit
* Commercial Street
* Office District

### Weekend Night

HDBSCAN may identify:

* Restaurant Cluster
* Shopping Mall
* Event Venue

The resulting hotspot geometries are allowed to differ because parking demand itself changes across time periods.

---

## Why This Is More Accurate

A single annual clustering model assumes:

Hotspot(Location, Time1) = Hotspot(Location, Time2)

Our system tests this assumption instead of accepting it.

By recomputing hotspots for each operational context, we allow:

Hotspot(Location, Context A)
≠
Hotspot(Location, Context B)

when supported by data.

This provides a more mathematically rigorous representation of parking behavior.

---

## Storage Architecture

All hotspot outputs are stored in SQLite.

Table: contextual_hotspots

Columns:

* hotspot_id
* context
* center_lat
* center_lon
* violation_count
* cluster_density
* risk_score
* polygon_geometry

Each context maintains its own hotspot universe.

This allows rapid retrieval without recomputing clustering during dashboard execution.

---

## Live Enforcement Intelligence

When the dashboard opens:

1. Current day is detected.
2. Current time bucket is detected.
3. Matching context is selected.

Example:

Friday 19:00

↓

weekday_evening_peak

↓

Load hotspots generated specifically for weekday_evening_peak

↓

Rank hotspots by risk score

↓

Recommend enforcement deployment locations

No clustering is executed at runtime.

All hotspot intelligence is precomputed and retrieved instantly.

---

## Innovation

Most parking enforcement dashboards use static hotspot maps.

Our approach introduces:

Context-Aware Dynamic Hotspot Discovery

where hotspot geography is allowed to evolve across operational conditions, producing a more realistic representation of urban parking behavior and enabling targeted, time-sensitive enforcement decisions.
