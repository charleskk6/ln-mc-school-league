# UK School League Tables

A single-page ranking of every mainstream primary and secondary school in
England, computed from the Department for Education's annual performance
tables. The page is fully self-contained: open `index.html` in any browser
(including straight from the file system) and it works — no server, no build
step, no dependencies.

Coverage: **4,067 secondary** (KS4) and **15,056 primary** (KS2) schools.
Quick filters for Greater Manchester (10 boroughs), Greater London (33
boroughs) and Cambridgeshire (county), plus an "All England" view; instant
search by school name across the full national dataset.

---

## Repository contents

| Path                              | Purpose                                                                      |
| --------------------------------- | ---------------------------------------------------------------------------- |
| `index.html`                      | The app. Fetches the active year's data at runtime via a year selector.      |
| `build_data.py`                   | Pre-processor — turns a year's DfE CSVs into `secondary.json` / `primary.json`. |
| `england_ks4final.csv`            | DfE raw secondary (KS4) table for the latest year (2024-2025), at repo root. |
| `england_ks2final.csv`            | DfE raw primary (KS2) table for the latest year (2024-2025), at repo root.   |
| `data/<year>/secondary.json`      | Generated secondary dataset the page fetches for that academic year.         |
| `data/<year>/primary.json`        | Generated primary dataset the page fetches for that academic year.           |
| `data/<year>/england_ks*.csv`     | DfE raw CSVs for earlier years (e.g. `data/2023-2024/`).                      |

Academic years currently built: **2024-2025** and **2023-2024**, switchable
from the **Academic year** dropdown above the league table.

---

## Running the app

The page **fetches** `data/<year>/{secondary,primary}.json`, so it must be
served over http(s) — opening the file directly from disk will fail on the
fetch.

```sh
python3 -m http.server 8000   # then visit http://localhost:8000/
```

Deployed on GitHub Pages it works as-is. No build step or dependencies at
runtime — just static files.

---

## Updating / adding a year with `build_data.py`

Run this when the source DfE CSVs change (typically once a year, when new
performance tables are published) or to add an earlier year.

```sh
# Latest year — CSVs at repo root -> data/2024-2025/
python3 build_data.py . data/2024-2025

# An earlier year — CSVs in a folder; OUT_DIR defaults to that folder
python3 build_data.py data/2023-2024
```

Each `data/<year>/` folder holds that year's `england_ks4final.csv` +
`england_ks2final.csv` and the generated `secondary.json` / `primary.json`.
To expose a new year in the UI, add it to `YEAR_RESULTS` / `YEAR_LABELS` and
the `#yearSelect` options in `index.html`.

What it does:

1. **Reads** `england_ks4final.csv` and `england_ks2final.csv` from `SRC_DIR`.
2. **Filters** to mainstream schools (`RECTYPE == '1'`) with valid scores in
   every input metric. Suppression markers (`SUPP`, `NE`, `NP`, `..`) are
   treated as missing and drop the row.
3. **Classifies** each school: Grammar (selective admissions), Independent
   (private), or Non-selective; appends gender (Boys/Girls) and religious
   affiliation (RC/CofE/Jewish/Muslim/Hindu/Sikh/Methodist/Quaker) where
   present.
4. **Maps** the DfE LEA code to a borough/area name and tags Greater
   Manchester / Greater London / Cambridgeshire / other.
5. **Computes** the ranking scores (see *Ranking algorithms* below), including
   national percentiles and within-LA percentiles for the primary indices.
6. **Writes** `secondary.json` + `primary.json` into `OUT_DIR`.

Requirements: Python 3 stdlib only. No `pip install` needed.

---

## Ranking algorithms

### Secondary — School Quality Score (SQS)

A single 0–∞ composite score where **100 = exactly the England average**.
Numbers above 100 mean above-average performance, scaled linearly.

```
SQS = (A8_norm     × 0.35)
    + (EM_norm     × 0.30)
    + (EBaccAPS_norm × 0.20)
    + (EBaccEnt_norm × 0.15)

where each _norm = (School value / England average) × 100
```

England averages used (DfE-published, 2024/25 data year):

| Metric       | England avg |
| ------------ | ----------- |
| Attainment 8 | 46.1        |
| Grade 5+ Eng & Maths | 45.4% |
| EBacc APS    | 4.09        |
| EBacc Entry  | 40.5%       |

Interpretation bands:

| SQS range  | Meaning            |
| ---------- | ------------------ |
| 180+       | Exceptional        |
| 160–179    | Outstanding        |
| 140–159    | Well above average |
| 120–139    | Above average      |
| < 120      | Near / below avg   |

### Primary — three percentile indices

KS2 doesn't have a single headline metric, so the page offers **three
different rankings**, selected via the "Rank by" dropdown. Each is scored
on a 0–100 scale where the England average sits around 49.

Inputs are **national percentiles** (rank against all ~15,000 England
primaries, midrank handling for ties): a percentile of 75 means the school
is ahead of 75% of primaries on that measure.

Inputs are RWM-expected, RWM-higher, **English** and maths. Because reading
and **GPS (grammar, punctuation & spelling)** are both English-domain, their
percentiles are averaged into one `English%ile = (Reading%ile + GPS%ile) / 2`,
kept balanced 1:1 with maths.

```
API (Academic Performance Index — default)
  = (Expected%ile × 0.40) + (Higher%ile × 0.40)
  + (English%ile × 0.10) + (Maths%ile × 0.10)

GRI (Grammar School Readiness)
  = (Higher%ile × 0.50) + (English%ile × 0.25) + (Maths%ile × 0.25)

11+ Readiness (within the school's own Local Authority)
  = (Higher_LA × 0.50) + (English_LA × 0.25) + (Maths_LA × 0.25)
```

For 11+, each input is a **percentile rank 0–100 within the LA** instead of
nationally — so it measures standing among local peers and, unlike min-max,
isn't distorted by a single outlier school. A solo-school LA falls back to a
neutral 50.

**Cohort-size adjustment (empirical-Bayes shrinkage).** A score from a 15-pupil
class is far noisier than one from 90 pupils, so small schools otherwise dominate
both ends of the table by luck. Before computing the percentiles, each raw metric
is pulled toward a reference mean by a weight that shrinks with cohort size:

```
adjusted = (n × raw  +  K × reference_mean) / (n + K)
```

where `n` is the KS2 cohort (`TELIG`) and `K = SHRINK_K` (default **30**, ≈ the
national median cohort, set in `build_data.py`). A school keeps `n/(n+K)` of its
own signal — 33% at n=15, 75% at n=90. National indices (API, GRI) shrink toward
the national mean; 11+ shrinks toward each school's **LA** mean. The **raw
percentages are still stored and displayed unchanged** — only the percentiles,
and therefore the index scores and ranking, are adjusted. Each row shows its
cohort `n`, flagged ⚠ when `n < 16`. Raise `K` for stronger damping.

Interpretation bands (apply to API / GRI / 11+ identically):

| Score range | Meaning            |
| ----------- | ------------------ |
| 80+         | Top tier           |
| 65–79       | Well above average |
| 50–64       | Above average      |
| 35–49       | Around / below avg |
| < 35        | Lower tier         |

---

## Reading the data columns

Every row in either table carries: school name, area (borough or town),
school type (e.g. "Grammar · Boys (CofE)"), the ranking score badge, and
four metric columns specific to the phase.

### Secondary table

| Column                | Field   | DfE source        | What it measures                                                                                       |
| --------------------- | ------- | ----------------- | ------------------------------------------------------------------------------------------------------ |
| **SQS ★**             | (computed) | — | Composite ranking score (see formula above). 100 = England average. Color-banded.                       |
| **Attainment 8**      | `a8`    | `ATT8SCR`         | Average GCSE point score across 8 subjects per pupil. Broadest headline measure.                       |
| **Gr.5+ Eng & Maths** | `em`    | `PTL2BASICS_95`   | % of pupils achieving Grade 5+ in BOTH English and Maths GCSE — the strong-pass benchmark.             |
| **EBacc APS**         | `aps`   | `EBACCAPS`        | Average point score across 5 EBacc pillars (English, Maths, sciences, humanities, language). Depth.    |
| **EBacc Entry**       | `ent`   | `PTEBACC_E_PTQ_EE`| % of pupils entered for the full EBacc combination. Signal of academic ambition.                       |

### Primary table

| Column                | Field   | DfE source        | What it measures                                                                                       |
| --------------------- | ------- | ----------------- | ------------------------------------------------------------------------------------------------------ |
| **API / GRI / 11+ ★** | (computed) | — | Selected index score (see formulas above). 0–100 scale. Color-banded.                                  |
| **RWM Expected**      | `rwm`   | `PTRWM_EXP`       | % of pupils reaching the expected standard in reading, writing AND maths combined. Breadth incl. writing. |
| **RWM Higher**        | `hs`    | `PTRWM_HIGH`      | % of pupils working at "greater depth" across all three. Stretch measure for the most able.            |
| **Reading SS**        | `read`  | `READ_AVERAGE`    | Average KS2 reading scaled score (typically 80–120, 100 = expected). Depth in literacy.                |
| **Maths SS**          | `maths` | `MAT_AVERAGE`     | Average KS2 maths scaled score (typically 80–120, 100 = expected). Depth in numeracy.                  |

### Internal fields (in the JSON, not visible on the page)

| Field        | Meaning                                                                              |
| ------------ | ------------------------------------------------------------------------------------ |
| `name`, `area`, `type`, `lea` | School name, borough/town, classification, DfE LEA code (3-digit).  |
| `region`     | One of `manchester`, `london`, `cambridge`, `other` — drives the region-tab filter. |
| `urn`        | DfE Unique Reference Number — the join key used to merge destination data.           |
| `n`          | KS2 cohort size (`TELIG`, primary only) — drives the empirical-Bayes shrinkage.       |
| `priv`       | Present and `true` for independent / non-maintained schools (KS4 only).              |
| `rwm, hs, read, maths, gps` | Raw KS2: RWM-expected %, RWM-higher %, reading / maths / GPS scaled scores. |
| `pe, ph, pr, pm, pg` | National percentiles for RWM-expected / RWM-higher / reading / maths / GPS. |
| `lh, lr, lm, lg` | Within-LA percentile ranks (the 11+ Readiness inputs). 50 for solo-school LAs.   |
| `d_sust, d_edu, d_appr, d_emp` | Secondary post-16 destinations (sustained / education / apprenticeship / employment %), merged from the KS4 destinations file. Absent until that file is added. |
| `d_gram`     | Optional primary→grammar %, merged from a user-supplied file (see below). Absent by default. |
| `_lc`        | Pre-lowercased name; populated at runtime to make the search filter allocation-free. |

---

## Student destinations (separate, opt-in data)

The performance tables carry **attainment only** — destinations are separate DfE
datasets, so the **Destinations** view in the app is empty until you add them.
`build_data.py` merges any of these files (by **URN**) if present in the source
directory; if a file is missing, the merge is a silent no-op.

| File (in the source dir)            | Feeds                | Phase     |
| ----------------------------------- | -------------------- | --------- |
| `england_ks4-pupdest.csv`           | `d_sust/d_edu/d_appr/d_emp` (post-16) | Secondary |
| `england_ks5-studest.csv`           | `d18_he/d18_sust` (post-18)  | Secondary (sixth forms) |
| `england_ks5-studest-he.csv`        | `d18_prog/d18_top3` (HE progression) | Secondary (sixth forms) |
| `primary-destinations.csv` (yours)  | `d_gram`             | Primary   |

These are the DfE performance-tables destination files (uppercase column codes,
`RECTYPE=1` rows = schools, percentages with a `%` suffix). The exact percentage
codes are matched first — e.g. `OVERALL_DESTPER`, `EDUCATIONPER`, `APPRENPER`,
`EMPLOYMENTPER` (KS4); `TOT_OVERALLPER`, `TOT_HEPER` (KS5); `ALL_PROGRESSED`,
`ALL_TOP3RD` (KS5 HE) — so the count columns are never mistaken for percentages.

Download the institution-level CSVs from DfE Explore Education Statistics
([KS4 destinations](https://explore-education-statistics.service.gov.uk/find-statistics/key-stage-4-destination-measures),
[16-18 destinations](https://explore-education-statistics.service.gov.uk/find-statistics/16-18-destination-measures)).
Column names are matched case/punctuation-insensitively against common variants
(`overall`, `education`, `apprenticeships`, `employment`, `higher_education`, …);
all-pupils total rows are used when a breakdown column is present; cohorts <6 are
suppressed by DfE.

**Primary → grammar.** There is **no public dataset** of which primary pupils
enter grammar schools (true flows need restricted pupil-level NPD access). So the
primary Destinations view shows the existing **11+ Readiness** index as a
labelled *proxy*. To plug in real figures, drop a `primary-destinations.csv` with:
- a join key: `urn` (preferred), else `name` + `area`
- a measure: `pct_grammar`, **or** `n_grammar` + `n_total`

---

## Data source and currency

DfE Performance Tables — England:
[https://www.gov.uk/government/collections/statistics-school-and-college-performance-tables](https://www.gov.uk/government/collections/statistics-school-and-college-performance-tables)

The CSV files in this repo are the published 2024/25 datasets (released in
the 2025 performance tables cycle). All percentile ranks and league
standings are computed against that snapshot.
