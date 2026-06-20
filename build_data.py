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
                "region": lea_region(lea),
                "rwm": round(rwm, 1),
                "hs": round(hs, 1),
                "read": round(read, 1),
                "maths": round(maths, 1),
                "gps": round(gps, 1),
            })

    # National percentiles across the full mainstream set.
    pe = percentile_ranks([r["rwm"] for r in rows])
    ph = percentile_ranks([r["hs"] for r in rows])
    pr = percentile_ranks([r["read"] for r in rows])
    pm = percentile_ranks([r["maths"] for r in rows])
    pg = percentile_ranks([r["gps"] for r in rows])
    for i, r in enumerate(rows):
        r["pe"] = round(pe[i], 1)
        r["ph"] = round(ph[i], 1)
        r["pr"] = round(pr[i], 1)
        r["pm"] = round(pm[i], 1)
        r["pg"] = round(pg[i], 1)

    # 11+ Readiness: percentile rank within each LA on hs, read, maths, gps.
    # (Robust to a single outlier school, unlike min-max; solo-school LA -> 50.)
    by_lea = defaultdict(list)
    for i, r in enumerate(rows):
        by_lea[r["lea"]].append(i)

    for lea, idxs in by_lea.items():
        if len(idxs) < 2:
            for i in idxs:
                rows[i]["lh"] = rows[i]["lr"] = rows[i]["lm"] = rows[i]["lg"] = 50.0
            continue
        for field, key in (("hs", "lh"), ("read", "lr"), ("maths", "lm"), ("gps", "lg")):
            pcts = percentile_ranks([rows[i][field] for i in idxs])
            for k, i in enumerate(idxs):
                rows[i][key] = round(pcts[k], 1)
    return rows


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
