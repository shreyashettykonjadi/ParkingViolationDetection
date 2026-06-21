# Dataset Audit & Understanding

## Project

**NagaraNetra – AI-Driven Parking Intelligence & Enforcement System**

---

## Dataset Overview

The dataset contains historical traffic enforcement records collected across Bengaluru police jurisdictions.

Each record represents a traffic violation event reported by enforcement personnel.

---

## Dataset Size

| Metric              | Value                  |
| ------------------- | ---------------------- |
| Total Records       | 298,450                |
| Total Columns       | 24                     |
| Time Period         | Nov 2023 – Apr 2024    |
| Police Stations     | 54                     |
| Geographic Coverage | Bengaluru Urban Region |

---

## Time Coverage

### Earliest Record

```text
2023-11-09 19:11:46+00:00
```

### Latest Record

```text
2024-04-08 17:30:46+00:00
```

### Duration

Approximately:

```text
5 Months
```

This duration is sufficient for:

* Hotspot discovery
* Time-of-day analysis
* Day-of-week analysis
* Simulated real-time replay
* Basic forecasting experiments

---

## Geographic Coverage

### Latitude

```text
Min: 12.802667
Max: 13.293684
Mean: 12.980802
```

### Longitude

```text
Min: 77.442553
Max: 77.771735
Mean: 77.600512
```

The coordinates fall within Bengaluru metropolitan limits and appear valid.

No obvious coordinate corruption was observed during initial inspection.

---

# Column Inventory

## Identity

### id

Unique violation identifier.

Example:

```text
FKID000001
```

Potential Uses:

* Record tracing
* Deduplication

---

## Location Information

### latitude

Geographic latitude.

Used for:

* Mapping
* Clustering
* Hotspot detection

---

### longitude

Geographic longitude.

Used for:

* Mapping
* Clustering
* Hotspot detection

---

### location

Human-readable address.

Example:

```text
Sarjapura Main Road,
Janatha Colony,
Bellandur
```

Potential Uses:

* Map popups
* Reporting
* Smart briefing generation

---

### junction_name

Nearby traffic junction.

Example:

```text
BTP044 - Sagar Theatre Junction
```

Potential Uses:

* Junction risk analysis
* High-risk corridor detection

---

# Vehicle Information

### vehicle_number

Vehicle registration number.

Example:

```text
KA01AB1234
```

Potential Future Uses:

* Repeat offender detection
* ANPR integration
* Automated challan systems
* Vehicle behaviour analytics

---

### vehicle_type

Reported vehicle category.

Examples:

```text
CAR
SCOOTER
MAXI-CAB
BUS
TANKER
AUTO
```

Used for:

* Severity scoring
* Congestion impact estimation

---

### updated_vehicle_type

Validated vehicle category.

Potentially more accurate than original entry.

Potential Uses:

* Data quality improvement
* Better severity calculations

---

### updated_vehicle_number

Validated vehicle number.

Potential Uses:

* ANPR verification
* Duplicate detection

---

# Violation Information

### violation_type

Stored as JSON-like lists.

Examples:

```text
["NO PARKING"]

["WRONG PARKING",
 "PARKING IN A MAIN ROAD"]
```

Observed Categories:

* NO PARKING
* WRONG PARKING
* DOUBLE PARKING
* PARKING ON FOOTPATH
* PARKING IN A MAIN ROAD
* PARKING NEAR ROAD CROSSING
* PARKING NEAR SCHOOL/HOSPITAL/BUS STOP
* PARKING NEAR TRAFFIC SIGNAL
* And several non-parking traffic offences

This is the most important analytical column in the project.

---

### offence_code

Traffic offence identifiers.

Example:

```text
[113]
```

Potential Uses:

* Violation classification
* Legal mapping
* Enforcement reporting

---

# Temporal Information

### created_datetime

Violation creation timestamp.

Primary time column.

Used for:

* Hour analysis
* Weekday analysis
* Trend analysis
* Forecasting

---

### modified_datetime

Record modification timestamp.

Potential Uses:

* Data quality audits

---

### validation_timestamp

Approval timestamp.

Potential Uses:

* Enforcement workflow analytics

---

# Enforcement Metadata

### police_station

Reporting police station.

Observed:

```text
54 Stations
```

Examples:

```text
Bellandur
Madiwala
Whitefield
Electronic City
HSR Layout
```

Used for:

* Dashboard filtering
* Resource allocation
* Routing origin selection

---

### center_code

Internal enforcement region code.

Potential Uses:

* Zone-level analysis

---

### created_by_id

Officer identifier.

Potential Uses:

* Productivity analytics
* Enforcement performance metrics

---

### device_id

Reporting device identifier.

Potential Uses:

* Operational monitoring

---

# Validation & Workflow Columns

### validation_status

Examples:

```text
approved
```

Potential Uses:

* Data quality filtering
* Approved-only analytics

---

### data_sent_to_scita

Boolean flag.

Indicates downstream system transfer status.

---

### data_sent_to_scita_timestamp

Transfer timestamp.

Potential Uses:

* System audit tracking

---

# Missing Data Audit

## Fully Empty Columns

### description

```text
298,450 null values
```

No analytical value currently.

---

### closed_datetime

```text
298,450 null values
```

No analytical value currently.

---

### action_taken_timestamp

```text
298,450 null values
```

No analytical value currently.

---

# Vehicle Distribution

Top categories:

| Vehicle Type   |  Count |
| -------------- | -----: |
| SCOOTER        | 94,856 |
| CAR            | 88,870 |
| MOTOR CYCLE    | 40,811 |
| PASSENGER AUTO | 37,813 |
| MAXI-CAB       | 11,372 |

Heavy vehicles are present:

* TANKER
* PRIVATE BUS
* TOURIST BUS
* HGV
* LORRY

This enables weighted congestion scoring.

---

# Police Station Coverage

Total Stations:

```text
54
```

Examples:

* Bellandur
* Whitefield
* Electronic City
* Madiwala
* HSR Layout
* Yelahanka

This enables city-wide spatial intelligence.

---

# Project-Relevant Features

The following columns are considered core for NagaraNetra:

```text
latitude
longitude
created_datetime
vehicle_type
updated_vehicle_type
vehicle_number
violation_type
offence_code
police_station
junction_name
location
validation_status
```

---

# Planned Analytics

## Day 1

* Data Cleaning
* Interactive Map
* Station Filtering
* Time Filtering

## Day 2

* DBSCAN Hotspot Detection
* Severity Scoring

## Day 3

* Congestion Impact Engine
* Tow-Truck Routing

## Day 4

* Dashboard Polish
* Smart Briefing
* Presentation

---

# Long-Term Expansion Possibilities

Using existing columns, the platform can later support:

### Repeat Offender Detection

Using:

```text
vehicle_number
```

---

### ANPR Integration

Using:

```text
vehicle_number
updated_vehicle_number
```

---

### Officer Analytics

Using:

```text
created_by_id
```

---

### Junction Risk Intelligence

Using:

```text
junction_name
```

---

### Parking Hotspot Forecasting

Using:

```text
created_datetime
latitude
longitude
```

---

### Automated Challan Systems

Using:

```text
vehicle_number
offence_code
validation_status
```

This dataset is significantly richer than a simple parking-violation dataset and can support both operational enforcement workflows and future predictive traffic intelligence features.
