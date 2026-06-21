# ParkSense AI — Predictive Forecasting Model

### "Tomorrow's Predicted Hotspots"

This document explains the predictive layer end-to-end: **what we predict, why that
framing is correct, how the data is preprocessed, the features we engineer, the model
we train, how we prevent leakage, how we evaluate it, and why the algorithm makes
sense** for parking-enforcement.

Code map:
- `src/forecast.py` — all modelling logic (pure functions)
- `build_forecast.py` — offline pipeline (train → evaluate → predict → persist)
- `data/forecast.db` — output store (predictions + metrics)
- `app.py` → **🔮 Forecast** tab — presentation

---

## 1. What are we predicting?

### The problem with "where will the *next* violation occur"
Taken literally — predicting the exact lat/long of the single next violation — this is
**not learnable**. An individual event is dominated by irreducible noise (which driver,
which minute, which exact metre of kerb). No honest model can output that.

### The correct, useful reframing
What *is* predictable — and what enforcement actually needs — is the **expected
intensity**: *how many violations each zone will see over the next day.* Rank those and
you get the operational answer:

> **"The next violations are most likely in these zones tomorrow — pre-position patrols here."**

So formally we predict, for every enforcement **zone** *z* and the next **day** *d+1*:

```
ŷ(z, d+1) = expected number of parking violations in zone z on day d+1
```

This is a **spatio-temporal count-intensity forecast**. It directly attacks the problem
statement's core pain point — enforcement today is *"patrol-based and reactive"* — by
making it **proactive**.

### Why a *zone* and why a *day*?
- **Zone** = an HDBSCAN persistent cluster (1,992 of them). These are organic
  violation hotspots already discovered by the detection layer — natural, data-driven
  enforcement units (see `HDBSCAN_ARCHITECTURE.md`).
- **Day** (not hour) is a **data-driven choice**, justified next.

---

## 2. Why day-level? (the sparsity argument)

We profiled the density of violations at different granularities:

| Granularity | Non-zero cells | Mean count when present | Verdict |
|---|---|---|---|
| zone × **hour** | very low | **< 1** | too sparse — mostly noise/zeros |
| zone × **day** | 18.2% | **4.2** | **chosen** — learnable signal, Poisson-friendly |

At the hourly level almost every (zone, hour) cell is 0 or 1 — there is no stable signal
to learn. At the **daily** level the series have real structure (weekly rhythm, trends,
~4 violations/day when active, 10–20 for the busiest zones). The 82% of cells that are
zero are **structural zeros** that a **Poisson** model handles natively. Hence:
**day-level count forecasting.**

---

## 3. Data & preprocessing

**Source:** `data/clean_parking_data.csv` → 298,450 real Bengaluru violations,
**2023-11-10 → 2024-04-08** (≈150 days), already clustered into 1,992 persistent zones
in `hotspots.db`.

### 3.1 Timezone normalisation (a real correctness fix)
The CSV's `hour`/`weekday`/`date` were derived from `created_datetime` in **UTC**, but
Bengaluru is **UTC+5:30**. Left as-is, "morning peak" bins and the app's
"current-IST → time-bucket" logic were misaligned by 5.5 hours. In `build_hotspots.py`
we now re-derive **every** temporal field in IST:

```python
ist = pd.to_datetime(df["created_datetime"], utc=True, format="ISO8601") \
        .dt.tz_convert("Asia/Kolkata")
df["hour"], df["weekday"], df["month"], df["date"] = (
    ist.dt.hour, ist.dt.weekday, ist.dt.month, ist.dt.strftime("%Y-%m-%d")
)
```
(A late-UTC event can correctly roll to the next IST date.) Every downstream layer —
including this forecast — now uses correct local time.

### 3.2 Building the panel (`forecast.build_panel`)
We turn raw events into a **dense daily panel**:

1. Keep only **clustered** violations (`persistent_cluster_id >= 0`); the 22% noise
   points don't belong to an enforcement zone.
2. Group by `(cluster_id, date)` → daily counts.
3. For each zone, build a **continuous daily index** from its first observed day to the
   global max date, filling missing days with **0** (the structural zeros).

Result: ~**280,508** rows of `(cluster_id, date, count)` — a clean panel time series per
zone. We start each zone's series at *its* first appearance so we don't fabricate
"pre-birth" zeros before a hotspot existed.

---

## 4. Feature engineering (`forecast.make_features`)

Each panel row `(zone, day)` becomes a feature vector. Four families:

### 4.1 Calendar / seasonality
`weekday`, `is_weekend`, `day_of_month`, `month`, `week_of_year`, and a **cyclical**
encoding of day-of-year (`doy_sin`, `doy_cos`) so the model sees the year as a circle,
not a discontinuity. Plus `is_holiday` — a hardcoded India/Karnataka holiday set for the
window (Diwali, Christmas, Republic Day, Sankranti, Holi, …), avoiding an extra
dependency.

### 4.2 Lag & rolling history (the strongest predictors)
A zone's recent past is the best predictor of its near future:

| Feature | Meaning |
|---|---|
| `lag_1`, `lag_7`, `lag_14` | count 1 / 7 / 14 days ago (7 captures same-weekday) |
| `roll_mean_7/14/30` | rolling average over the last 7 / 14 / 30 days |
| `roll_std_7` | recent volatility |
| `expanding_mean` | the zone's all-history average to date |
| `days_since_first` | zone maturity / trend anchor |

> **Leakage prevention:** every lag/rolling feature is computed on a series
> **`shift(1)`-ed within the zone** — so a row only ever sees days *strictly before*
> itself. No row can peek at its own (or future) counts.

### 4.3 Zone-static context
`centroid_lat`, `centroid_long` (geography), `all_time_count` (how busy the zone is
overall), and categoricals `police_station`, `dominant_vehicle`, `top_violation`.

### 4.4 Optional OSM context
If the enrichment cache exists, we fold in `office_count`, `hospital_count`,
`road_importance_score` (joined by rounded-centroid `cell_key`). Optional — the model
runs fine without it.

Categorical columns are passed as pandas `category` dtype and consumed via
scikit-learn's **native categorical support** (`categorical_features="from_dtype"`) — no
one-hot blow-up.

---

## 5. The models

We train **two** things on purpose: a baseline to beat, and the ML model.

### 5.1 Baseline — seasonal-naive (the benchmark)
`forecast.train_baseline`: the historical **mean count per (zone, weekday)**, with a
zone-mean fallback. This is the standard, strong naive benchmark for daily seasonal
data ("a Tuesday in this zone usually sees N"). **If our ML model can't beat this, it
isn't earning its place.**

### 5.2 ML — Poisson Gradient-Boosted Trees
`forecast.train_model` uses scikit-learn's
**`HistGradientBoostingRegressor(loss="poisson")`**:

```python
HistGradientBoostingRegressor(
    loss="poisson",            # count target: variance grows with the mean
    max_iter=300, learning_rate=0.05,
    max_leaf_nodes=31, min_samples_leaf=40, l2_regularization=1.0,
    categorical_features="from_dtype", random_state=42,
)
```

**Why this model?**
- **Poisson loss** is the statistically correct objective for **non-negative counts**
  with many zeros and mean-dependent variance — exactly our panel. It optimises Poisson
  deviance, not squared error, so it won't be dragged around by a few large days.
- **Gradient-boosted trees** capture **non-linear interactions** (e.g. "weekend × this
  station × rising 7-day trend") automatically, handle **mixed numeric + categorical**
  features, and are **robust to missing values** natively — early rows have NaN lags and
  the histogram splitter routes NaNs without imputation.
- **No new heavy dependency** — scikit-learn already ships with the project (via
  `hdbscan`). It trains on ~280k rows in seconds, so the whole pipeline is re-runnable
  on a laptop.

**Why not ARIMA / Prophet / LSTM?** Classical per-series models (ARIMA/Prophet) would
mean fitting **1,992 separate models** and they ignore cross-zone structure and static
context; deep nets (LSTM) are overkill for a 150-day window and would overfit. One
**global** GBM over the panel shares strength across zones, uses static + calendar
context, and stays explainable — the right tool here.

### 5.3 Confidence band (`q10`–`q90`)
We also fit two **quantile** GBMs (`loss="quantile"`, `quantile=0.1` and `0.9`). Because
independent quantile models aren't guaranteed coherent with the Poisson mean, the final
band is the **wider of** (a) the data-driven quantile band — which captures real
over-dispersion (volatile zones genuinely drop to ~0 some days) — and (b) a **Poisson
noise floor** `point ± 1.2816·√λ`. This guarantees `q10 ≤ prediction ≤ q90` and a
band that never collapses onto the point, communicating uncertainty honestly.

---

## 6. Training & evaluation protocol

### 6.1 Temporal holdout (no leakage across time)
We split by **time**, never randomly:

```
train = days ≤ (max_date − 30)      # learn from the past
test  = the final 30 unseen days    # predict the future
```

This simulates real deployment: the model only ever learns from the past and is scored
on days it has never seen. Both the baseline and the ML model are fit on `train` and
scored on `test`.

### 6.2 Metrics — two questions
**(a) Are the counts accurate?** MAE, RMSE, Poisson deviance.
**(b) Is the *ranking* right?** This is what enforcement cares about — did we put the
busiest zones at the top? Per test day we rank zones by predicted vs actual and average:
**Precision@10 / @20**, **NDCG@10**, **Spearman** rank correlation.

### 6.3 Results (ML vs baseline, 30-day holdout)

| Metric | Baseline | **Model** | Better? |
|---|---|---|---|
| MAE | 1.113 | **1.020** | ✅ −8.4% |
| RMSE | 2.856 | **2.714** | ✅ |
| Poisson deviance | 3.931 | **2.374** | ✅ −40% |
| Precision@10 | 0.183 | **0.233** | ✅ |
| Precision@20 | 0.225 | **0.258** | ✅ |
| NDCG@10 | 0.308 | **0.409** | ✅ |
| Spearman | 0.206 | **0.298** | ✅ |

**The model beats the seasonal baseline on every metric** — including the ranking
metrics that matter most for patrol prioritisation.

---

## 7. Producing tomorrow's forecast (`forecast.predict_next_day`)

For deployment we **retrain on the full history** (no held-out days wasted), then:

1. Append a **target-day row** (`date = max_date + 1`) for every zone.
2. Recompute features — the lag/rolling features for that row are built **only from real
   past days** (the `shift(1)` guarantees the placeholder count is never used).
3. Predict `pred_count`, `q10`, `q90`; compute the baseline for comparison; attach the
   recent 7-day average.
4. **Rank** zones by `pred_count` → the priority queue.

Persisted to `data/forecast.db` (a **separate** DB so rebuilding `hotspots.db` never
wipes it, mirroring the `enrichment.db` pattern).

### Worked example — why it's smarter than the baseline
Zone **K.R. Pura** averages ~20 violations/day overall, so the seasonal **baseline
predicts ~17** for the next day. But the **last two days surged to 93 then 106**. The ML
model reads this through `lag_1` and `roll_mean_7` and predicts **~64 [band 0–74]** —
catching an escalating situation the baseline is blind to. *That* momentum-awareness is
the proactive value.

---

## 8. Why the algorithm makes sense (summary)

| Design choice | Why it's right |
|---|---|
| Predict **zone-day intensity**, not a point event | The only honestly learnable, operationally useful target |
| **Day** granularity | Hourly is too sparse (<1/cell); daily has real signal (4.2/cell) |
| **HDBSCAN zones** as units | Organic, data-driven enforcement areas already in the system |
| **Poisson** objective | Correct for non-negative, zero-inflated, mean-variance-coupled counts |
| **Global GBM** over a panel | Shares strength across zones; uses lags + calendar + static context; handles NaNs & categoricals |
| **Lag/rolling** features (shifted) | Recent history is the dominant predictor; `shift(1)` blocks leakage |
| **Seasonal-naive baseline** | Forces an honest "does ML actually help?" comparison — it does |
| **Temporal holdout** + ranking metrics | Evaluates the way the tool is actually used: future days, ranked zones |
| **Quantile + Poisson band** | Communicates uncertainty without over-claiming precision |

**In one line:** we forecast each hotspot's next-day violation load with a leakage-safe,
Poisson gradient-boosted model that demonstrably out-ranks a strong seasonal baseline —
turning ParkSense from *reactive* reporting into *proactive*, pre-positioned enforcement.

---

## 9. How to reproduce

```bash
python build_hotspots.py     # rebuild zones with IST-correct time
python build_forecast.py     # train, evaluate (prints the table above), write forecast.db
streamlit run app.py         # → 🔮 Forecast tab
```
