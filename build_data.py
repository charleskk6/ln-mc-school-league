#!/usr/bin/env python3
"""Build per-year secondary.json / primary.json from DfE England CSVs.

Reads (from SRC_DIR):
  england_ks4final.csv  (secondary GCSE)
  england_ks2final.csv  (primary KS2 SATs)

Writes (to OUT_DIR):
  secondary.json
  primary.json

Usage:
  python3 build_data.py [SRC_DIR] [OUT_DIR]

  # 2024-2025 tables (CSVs in repo root) -> data/2024-2025/
  python3 build_data.py . data/2024-2025

  # 2023-2024 tables -> data/2023-2024/ (OUT_DIR defaults to SRC_DIR's name under data/)
  python3 build_data.py data/2023-2024

The page (index.html) fetches data/<year>/{secondary,primary}.json at runtime.
"""
import csv
import json
import os
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(REPO, "data")

# England national averages (from current index.html SQS formula card).
ENG_A8 = 46.1
ENG_EM = 45.4
ENG_APS = 4.09
ENG_ENT = 40.5

# Greater Manchester LEAs (10).
GM = {
    350: "Bolton", 351: "Bury", 352: "Manchester", 353: "Oldham",
    354: "Rochdale", 355: "Salford", 356: "Stockport", 357: "Tameside",
    358: "Trafford", 359: "Wigan",
}

# Greater London LEAs (33 = City + 32 boroughs).
LONDON = {
    201: "City of London", 202: "Camden", 203: "Greenwich", 204: "Hackney",
    205: "Hammersmith and Fulham", 206: "Islington", 207: "Kensington and Chelsea",
    208: "Lambeth", 209: "Lewisham", 210: "Southwark", 211: "Tower Hamlets",
    212: "Wandsworth", 213: "Westminster",
    301: "Barking and Dagenham", 302: "Barnet", 303: "Bexley", 304: "Brent",
    305: "Bromley", 306: "Croydon", 307: "Ealing", 308: "Enfield", 309: "Haringey",
    310: "Harrow", 311: "Havering", 312: "Hillingdon", 313: "Hounslow",
    314: "Kingston upon Thames", 315: "Merton", 316: "Newham", 317: "Redbridge",
    318: "Richmond upon Thames", 319: "Sutton", 320: "Waltham Forest",
}

# Cambridgeshire county LEA (873 — Cambridge city plus Ely, Huntingdon, March,
# St Neots, Wisbech). Peterborough (874) is a separate unitary authority and is
# not included. Areas within fall back to the town name (Cambridge, Ely, …).
CAMBS = {873: "Cambridgeshire"}


def lea_area(lea_code: int, town: str) -> str:
    """Borough name for curated regions, town fallback elsewhere."""
    if lea_code in GM:
        return GM[lea_code]
    if lea_code in LONDON:
        return LONDON[lea_code]
    if town:
        return town.title()
    if lea_code in CAMBS:
        return CAMBS[lea_code]
    return f"LEA {lea_code}"


def lea_region(lea_code: int) -> str:
    if lea_code in GM:
        return "manchester"
    if lea_code in LONDON:
        return "london"
    if lea_code in CAMBS:
        return "cambridge"
    return "other"


def parse_num(v):
    """Strip %, treat suppression markers as None."""
    if v is None:
        return None
    v = v.strip()
    if v in ("", "SUPP", "NE", "NP", "..", "LOWCOV", "N/A", "NA", "NR", "x"):
        return None
    v = v.rstrip("%")
    try:
        return float(v)
    except ValueError:
        return None


def parse_int(v):
    """Parse an integer identifier (e.g. URN); None if blank/non-numeric."""
    if v is None:
        return None
    v = v.strip()
    if not v.isdigit():
        return None
    return int(v)


def is_priv(nftype: str) -> bool:
    return nftype in {"IND", "INDSS", "NMSS"}


def religious_tag(reldenom: str) -> str | None:
    r = reldenom or ""
    if "Roman Catholic" in r or "Catholic" in r:
        return "RC"
    if "Church of England" in r or "Anglican" in r:
        return "CofE"
    if "Jewish" in r:
        return "Jewish"
    if "Islam" in r or "Muslim" in r:
        return "Muslim"
    if "Hindu" in r:
        return "Hindu"
    if "Sikh" in r:
        return "Sikh"
    if "Methodist" in r:
        return "Methodist"
    if "Quaker" in r:
        return "Quaker"
    return None


def classify_secondary(row) -> str:
    admpol = row.get("ADMPOL", "")
    nftype = row.get("NFTYPE", "")
    egender = row.get("EGENDER", "")

    if admpol == "SEL":
        base = "Grammar"
    elif is_priv(nftype):
        base = "Independent"
    elif nftype == "CTC":
        base = "City Technology College"
    elif nftype == "UTC":
        base = "UTC"
    else:
        base = "Non-selective"

    parts = [base]
    if egender == "BOYS":
        parts.append("Boys")
    elif egender == "GIRLS":
        parts.append("Girls")

    label = " · ".join(parts)
    tag = religious_tag(row.get("RELDENOM", ""))
    if tag:
        label += f" ({tag})"
    return label


def classify_primary(row) -> str:
    nftype = row.get("NFTYPE", "")
    base_map = {
        "AC": "Academy", "ACC": "Academy", "ACCS": "Academy", "ACS": "Academy",
        "F": "Free School", "FS": "Free School", "FD": "Foundation", "FDS": "Foundation",
        "VA": "Voluntary Aided", "VC": "Voluntary Controlled",
        "CY": "Community", "CYS": "Community",
    }
    base = base_map.get(nftype, "State")
    tag = religious_tag(row.get("RELDENOM", ""))
    if tag:
        base += f" ({tag})"
    return base


def percentile_ranks(values):
    """Return parallel list of percentiles (0–100) using midrank for ties.

    Ties get the average of the ranks they would occupy (standard percentile-
    rank convention used by DfE-style ranking). p = 100 * midrank / n.
    """
    n = len(values)
    indexed = sorted(range(n), key=lambda i: values[i])
    pct = [0.0] * n
    i = 0
    while i < n:
        j = i
        # Walk forward while values are tied.
        v = values[indexed[i]]
        while j + 1 < n and values[indexed[j + 1]] == v:
            j += 1
        # Ranks i..j (1-based: i+1 .. j+1). Midrank = average.
        mid = ((i + 1) + (j + 1)) / 2.0
        p = 100.0 * mid / n
        for k in range(i, j + 1):
            pct[indexed[k]] = p
        i = j + 1
    return pct


def build_secondary(src_dir):
    src = os.path.join(src_dir, "england_ks4final.csv")
    rows = []
    with open(src, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("RECTYPE") != "1":
                continue
            a8 = parse_num(row.get("ATT8SCR"))
            em = parse_num(row.get("PTL2BASICS_95"))
            aps = parse_num(row.get("EBACCAPS"))
            ent = parse_num(row.get("PTEBACC_E_PTQ_EE"))
            if None in (a8, em, aps, ent):
                continue
            lea = int(row["LEA"])
            rec = {
                "name": row["SCHNAME"].strip(),
                "area": lea_area(lea, row.get("TOWN", "")),
                "type": classify_secondary(row),
                "lea": lea,
                "urn": parse_int(row.get("URN")),
                "region": lea_region(lea),
                "a8": round(a8, 1),
                "em": round(em, 1),
                "aps": round(aps, 2),
                "ent": round(ent, 1),
            }
            if is_priv(row.get("NFTYPE", "")):
                rec["priv"] = True
            rows.append(rec)
    return rows


# Shrinkage strength for the primary indices, in "pseudo-pupils". A school keeps
# n/(n+K) of its own signal; the rest is pulled toward the reference mean. K≈30
# (~the national median KS2 cohort) shrinks a 30-pupil school halfway to the mean
# while a 90-pupil school keeps ~75% of its own value. Raise K for more damping.
SHRINK_K = 30


def build_primary(src_dir):
    src = os.path.join(src_dir, "england_ks2final.csv")
    rows = []
    with open(src, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("RECTYPE") != "1":
                continue
            rwm = parse_num(row.get("PTRWM_EXP"))
            hs = parse_num(row.get("PTRWM_HIGH"))
            read = parse_num(row.get("READ_AVERAGE"))
            maths = parse_num(row.get("MAT_AVERAGE"))
            gps = parse_num(row.get("GPS_AVERAGE"))
            if None in (rwm, hs, read, maths, gps):
                continue
            lea = int(row["LEA"])
            rows.append({
                "name": row["SCHNAME"].strip(),
                "area": lea_area(lea, row.get("TOWN", "")),
                "type": classify_primary(row),
                "lea": lea,
                "urn": parse_int(row.get("URN")),
                "region": lea_region(lea),
                "n": parse_int(row.get("TELIG")) or 0,   # KS2 cohort size
                "rwm": round(rwm, 1),
                "hs": round(hs, 1),
                "read": round(read, 1),
                "maths": round(maths, 1),
                "gps": round(gps, 1),
            })

    # ── Reliability-adjusted ranking (empirical-Bayes / Bayesian-average
    # shrinkage by cohort size) ────────────────────────────────────────────────
    # A score from a small cohort is far noisier than one from a large cohort, so
    # small schools otherwise dominate both extremes of the table by luck. We pull
    # each metric toward a reference mean by a weight that shrinks with cohort
    # size — adj = (n*obs + K*ref) / (n + K) — and rank on the adjusted value.
    # Raw metrics are still stored/shown unchanged; only the percentiles (and thus
    # the indices) use the adjusted values. National indices shrink toward the
    # national mean; the within-LA index shrinks toward each school's LA mean.
    METRICS = ("rwm", "hs", "read", "maths", "gps")

    def _adj(r, field, ref):
        n = r["n"]
        return (n * r[field] + SHRINK_K * ref) / (n + SHRINK_K) if n > 0 else ref

    # National percentiles on nationally-shrunk metrics.
    nat_mean = {m: sum(r[m] for r in rows) / len(rows) for m in METRICS}
    for field, key in (("rwm", "pe"), ("hs", "ph"), ("read", "pr"),
                       ("maths", "pm"), ("gps", "pg")):
        pcts = percentile_ranks([_adj(r, field, nat_mean[field]) for r in rows])
        for i, r in enumerate(rows):
            r[key] = round(pcts[i], 1)

    # 11+ Readiness: percentile rank within each LA on LA-shrunk metrics.
    # (Solo-school LA -> 50. Shrinking toward the LA mean keeps the comparison
    # local while still damping small-cohort noise.)
    by_lea = defaultdict(list)
    for i, r in enumerate(rows):
        by_lea[r["lea"]].append(i)

    for lea, idxs in by_lea.items():
        if len(idxs) < 2:
            for i in idxs:
                rows[i]["lh"] = rows[i]["lr"] = rows[i]["lm"] = rows[i]["lg"] = 50.0
            continue
        for field, key in (("hs", "lh"), ("read", "lr"), ("maths", "lm"), ("gps", "lg")):
            la_mean = sum(rows[i][field] for i in idxs) / len(idxs)
            adj = [_adj(rows[i], field, la_mean) for i in idxs]
            pcts = percentile_ranks(adj)
            for k, i in enumerate(idxs):
                rows[i][key] = round(pcts[k], 1)
    return rows


# ── Student-destination merge ────────────────────────────────────────────────
# Destination measures are NOT in the academic performance tables; they are
# separate DfE datasets (Key stage 4 destination measures, 16-18 destination
# measures) plus an optional user-supplied primary->grammar file. These helpers
# read whichever destination CSVs are present in SRC_DIR and merge their fields
# onto the already-built school records, keyed by URN. If a file is absent the
# merge is a silent no-op, so the academic build is unchanged. Column names vary
# between DfE releases, so columns are matched case/punctuation-insensitively
# against a list of candidates (see README for the expected files/columns).

def _norm(s):
    """Lowercase, strip everything but a-z0-9 — for fuzzy header matching."""
    return "".join(c for c in (s or "").lower() if c.isalnum())


def _pick(row, norm_keys, candidates):
    """Return parse_num() of the first candidate column present in the row.

    norm_keys maps normalised-header -> original-header for this row's reader.
    """
    for cand in candidates:
        key = norm_keys.get(_norm(cand))
        if key is not None:
            return parse_num(row.get(key))
    return None


def _pick_raw(row, norm_keys, candidates):
    """Like _pick but returns the raw string (for id/text columns)."""
    for cand in candidates:
        key = norm_keys.get(_norm(cand))
        if key is not None:
            return (row.get(key) or "").strip()
    return None


# EES "underlying data" is often long-format with a breakdown column; we keep
# only the all-pupils total rows.
_TOTAL_MARKERS = {"total", "totalpupils", "allpupils", "all", "totalstudents",
                  "allstudents", "totalcohort"}
_BREAKDOWN_COLS = {"characteristic", "characteristictype", "breakdown",
                   "breakdowntopic", "pupilcharacteristic", "characteristicgroup"}

# Candidate column names per destination metric. The exact DfE
# performance-tables percentage codes are listed FIRST so they win over the
# raw-count columns (e.g. OVERALL_DESTPER before any generic "overall").
_C_URN = ["urn", "schoolurn", "school_urn", "estaburn", "institutionurn"]
_KS4 = {  # england_ks4-pupdest.csv (pupils at end of KS4 -> post-16 destination)
    "d_sust": ["overall_destper", "overalldestper", "overallpercent", "overall"],
    "d_edu":  ["educationper", "educationpercent", "education"],
    "d_appr": ["apprenper", "apprenticeshipper", "apprenticeshipspercent", "appren"],
    "d_emp":  ["employmentper", "employmentpercent", "employment"],
}
_KS5 = {  # england_ks5-studest.csv (16-18 study leavers -> post-18 destination)
    "d18_he":   ["tot_heper", "totheper", "heper", "hepercent"],
    "d18_sust": ["tot_overallper", "totoverallper", "overallper", "overallpercent"],
}
_KS5HE = {  # england_ks5-studest-he.csv (progression to higher education/training)
    "d18_cohort": ["all_cohort", "allcohort"],
    "d18_prog": ["all_progressed", "allprogressed", "progressed"],
    "d18_top3": ["all_top3rd", "alltop3rd", "top3rd", "topthird"],
}


def _csv_reader(path):
    """Yield (row, norm_keys) for each CSV row; norm_keys maps normalised
    header -> original header. Skips non-total breakdown rows when present."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return
        norm_keys = {_norm(h): h for h in reader.fieldnames}
        breakdown_col = next((norm_keys[c] for c in _BREAKDOWN_COLS
                              if c in norm_keys), None)
        for row in reader:
            if breakdown_col is not None:
                val = _norm(row.get(breakdown_col))
                if val and val not in _TOTAL_MARKERS:
                    continue
            yield row, norm_keys


def _merge_by_urn(path, records, metrics, label):
    """Merge `metrics` (dest_key -> candidate column names) from `path` onto
    `records` (list of school dicts) matched by URN. Returns #records updated."""
    if not os.path.isfile(path):
        return 0
    by_urn = {}
    for row, nk in _csv_reader(path):
        # DfE files carry school (RECTYPE=1) plus LA/National aggregate rows;
        # keep only individual schools.
        rectype = _pick_raw(row, nk, ["rectype"])
        if rectype not in (None, "", "1"):
            continue
        urn = parse_int(_pick_raw(row, nk, _C_URN))
        if urn is None:
            continue
        vals = {k: _pick(row, nk, cands) for k, cands in metrics.items()}
        vals = {k: (int(v) if v == int(v) else round(v, 1)) for k, v in vals.items() if v is not None}
        if vals:
            by_urn[urn] = vals
    hits = 0
    for r in records:
        v = by_urn.get(r.get("urn"))
        if v:
            r.update(v)
            hits += 1
    print(f"  {label}: matched {hits}/{len(records)} schools "
          f"from {os.path.basename(path)}", file=sys.stderr)
    return hits


def merge_ks4_destinations(src_dir, sec):
    """Merge post-16 destinations (sustained/education/apprenticeship/employment)
    onto secondary records from the DfE KS4 pupil-destinations file."""
    for fn in ("england_ks4-pupdest.csv", "england_ks4-destinations.csv",
               "ks4-destinations.csv"):
        path = os.path.join(src_dir, fn)
        if os.path.isfile(path):
            return _merge_by_urn(path, sec, _KS4, "KS4 destinations")
    return 0


def merge_ks5_destinations(src_dir, sec):
    """Merge 16-18 destinations (sustained / HE) onto secondary records with
    matching URNs (i.e. schools with sixth forms), then overlay the progression
    measures (progressed to level 4+, top-third HE) from the HE file. Standalone
    colleges without a KS4 record are out of scope for now."""
    hits = 0
    for fn in ("england_ks5-studest.csv", "england_16-18-destinations.csv",
               "england_ks5-destinations.csv", "16-18-destinations.csv"):
        path = os.path.join(src_dir, fn)
        if os.path.isfile(path):
            hits = _merge_by_urn(path, sec, _KS5, "16-18 destinations")
            break
    for fn in ("england_ks5-studest-he.csv", "england_ks5-he.csv"):
        path = os.path.join(src_dir, fn)
        if os.path.isfile(path):
            _merge_by_urn(path, sec, _KS5HE, "16-18 HE progression")
            break
    return hits


def merge_primary_destinations(src_dir, pri):
    """Merge a user-supplied primary->grammar file onto primary records.

    Join key: urn (preferred), else name+area (case-insensitive).
    Measure: pct_grammar, or n_grammar + n_total -> percentage. Emits d_gram."""
    path = next((os.path.join(src_dir, fn) for fn in
                 ("primary-destinations.csv", "england_primary-destinations.csv")
                 if os.path.isfile(os.path.join(src_dir, fn))), None)
    if path is None:
        return 0

    # Index primary records by each possible join key.
    by_urn = {r["urn"]: r for r in pri if r.get("urn")}
    by_name = {}
    for r in pri:
        by_name.setdefault((_norm(r["name"]), _norm(r["area"])), r)

    hits = 0
    for row, nk in _csv_reader(path):
        urn = parse_int(_pick_raw(row, nk, _C_URN))
        rec = by_urn.get(urn)
        if rec is None:
            nm = _pick_raw(row, nk, ["name", "schname", "schoolname"])
            ar = _pick_raw(row, nk, ["area", "town", "la", "laname"])
            if nm:
                rec = by_name.get((_norm(nm), _norm(ar)))
        if rec is None:
            continue
        pct = _pick(row, nk, ["pctgrammar", "grammarpercent", "pctselective",
                              "selectivepercent", "grammar", "selective"])
        if pct is None:
            n_g = _pick(row, nk, ["ngrammar", "grammarcount", "countgrammar"])
            n_t = _pick(row, nk, ["ntotal", "cohort", "totalcount", "total"])
            if n_g is not None and n_t:
                pct = 100.0 * n_g / n_t
        if pct is not None:
            rec["d_gram"] = round(pct, 1)
            hits += 1
    print(f"  primary->grammar: matched {hits}/{len(pri)} schools "
          f"from {os.path.basename(path)}", file=sys.stderr)
    return hits


def inject_inline():
    """Splice every data/<year>/{secondary,primary}.json into index.html
    between matching marker comments, so the page can be opened directly
    from disk (file://) without needing a server."""
    html_path = os.path.join(REPO, "index.html")
    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    injected = 0
    for entry in sorted(os.listdir(OUT_DIR)):
        year_dir = os.path.join(OUT_DIR, entry)
        if not os.path.isdir(year_dir):
            continue
        # Expect a YYYY-YYYY style directory.
        for phase in ("secondary", "primary"):
            payload_path = os.path.join(year_dir, f"{phase}.json")
            if not os.path.isfile(payload_path):
                continue
            with open(payload_path, encoding="utf-8") as pf:
                payload = pf.read().strip()
            label = f"{entry}-{phase}".upper()
            begin = f"<!-- BEGIN-DATA-{label} -->"
            end = f"<!-- END-DATA-{label} -->"
            bi = html.find(begin)
            ei = html.find(end)
            if bi == -1 or ei == -1:
                print(f"  skip {label}: markers not found in index.html", file=sys.stderr)
                continue
            block = (
                f'{begin}\n'
                f'<script type="application/json" id="data-{entry}-{phase}">{payload}</script>\n'
                f'{end}'
            )
            html = html[:bi] + block + html[ei + len(end):]
            injected += 1
            print(f"  injected {label} ({len(payload):,} bytes)", file=sys.stderr)

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Updated index.html with {injected} inline data block(s)", file=sys.stderr)


def build(src_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)

    print(f"Source CSVs: {src_dir}", file=sys.stderr)
    print("Building secondary…", file=sys.stderr)
    sec = build_secondary(src_dir)
    print(f"  {len(sec)} secondary schools", file=sys.stderr)

    print("Building primary…", file=sys.stderr)
    pri = build_primary(src_dir)
    print(f"  {len(pri)} primary schools", file=sys.stderr)

    # Merge student-destination datasets where present (no-op if files absent).
    print("Merging destinations…", file=sys.stderr)
    merge_ks4_destinations(src_dir, sec)
    merge_ks5_destinations(src_dir, sec)
    merge_primary_destinations(src_dir, pri)

    sec_json = json.dumps(sec, separators=(",", ":"), ensure_ascii=False)
    pri_json = json.dumps(pri, separators=(",", ":"), ensure_ascii=False)

    with open(os.path.join(out_dir, "secondary.json"), "w", encoding="utf-8") as f:
        f.write(sec_json)
    with open(os.path.join(out_dir, "primary.json"), "w", encoding="utf-8") as f:
        f.write(pri_json)

    print(f"Wrote {out_dir}/secondary.json and {out_dir}/primary.json", file=sys.stderr)


def main():
    # Modes:
    #   build_data.py inject              re-inject existing JSON into index.html
    #   build_data.py [SRC_DIR] [OUT_DIR] build a year, then auto-inject all years
    if len(sys.argv) > 1 and sys.argv[1] == "inject":
        inject_inline()
        return

    src_dir = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else REPO
    if len(sys.argv) > 2:
        out_dir = os.path.abspath(sys.argv[2])
    elif src_dir == REPO:
        out_dir = OUT_DIR
    else:
        out_dir = os.path.join(OUT_DIR, os.path.basename(src_dir.rstrip("/")))

    build(src_dir, out_dir)
    inject_inline()


if __name__ == "__main__":
    main()
