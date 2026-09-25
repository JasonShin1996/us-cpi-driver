#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
US CPI contribution decomposition  (Bloomberg WMA "Contributions to US CPI" clone)
==================================================================================

What it does
------------
1. Pulls CPI-U index levels (NSA = CUUR0000*, SA = CUSR0000*) from the BLS public API.
2. Builds the *relative importance* (RI, i.e. the expenditure weight of each component
   inside the all-items basket) for **every month**: BLS's published December tables
   (ri_official.csv, from ri_official.py) are the anchors; where no table exists the
   anchor is recovered from the indexes themselves (below).
3. Turns those weights into contributions to headline MoM% (SA) and YoY% (NSA)
   that add up to the headline print exactly.
4. Dumps everything to JSON / JS for the front-end, and to CSV.

How the weights are recovered  (the hard part)
----------------------------------------------
CPI is a modified-Laspeyres (Lowe) index, so for any month t and any anchor month a
inside the same weight-reference period:

        I_all(t)/I_all(a)  =  SUM_i  s_i(a) * I_i(t)/I_i(a)                    (1)

where s_i(a) is the relative importance of component i at month a and the components
i are mutually exclusive and exhaustive.

BLS re-references the expenditure weights every January, so `a = December of the
previous year` is exactly the anchor whose s_i(a) BLS publishes in its annual
"Relative importance of components" table.

Equation (1) is *linear* in s_i, we observe every index in it, and we have up to 12
monthly observations for only n-1 free parameters (they sum to 1). So we just solve
a small constrained least-squares problem per weight-year. Since 2007 the residual
is ~3e-6 (pure index-rounding noise) and the recovered December weights match the
published BLS table to within ~0.05pp.  Before 2007 BLS indexes carry one decimal,
the residual is ~5e-4 and the recovered weights can be off by several pp, so pass
--ri-file (official tables parsed by ri_official.py) for any history before 2007.

Once s_i(a) is known, the within-year price update is the standard BLS formula:

        RI_i(t) = RI_i(a) * [I_i(t)/I_i(a)] / [I_all(t)/I_all(a)]              (2)

Contributions
-------------
MoM:   c_i(t) = RI_i(t-1) * ( I_i(t)/I_i(t-1) - 1 )
       (exact for NSA; for SA there is a tiny residual because RI is an NSA concept,
        so the residual is re-allocated pro-rata by weight and also reported.)

YoY:   exact chained decomposition, from the telescoping identity
       PROD_k R(t-k) - 1 = SUM_k [ (R(t-k)-1) * PROD_{m<k} R(t-m) ],  R = I_all ratio
       so   c_i^yoy(t) = SUM_k  c_i(t-k) * PROD_{m<k} R(t-m)
       This sums to the headline YoY exactly and, unlike the naive
       RI(t-12)*(I(t)/I(t-12)-1), it survives the January weight reset.
       (`--yoy-method naive` gives the simple version for comparison.)

Missing months
--------------
October 2025 was never published (government shutdown). Everything below chains over
*available* months, so Nov-2025 is flagged as a 2-month change instead of silently
being wrong, and the 12-month chain still spans exactly 12 months of price change.

Usage
-----
    pip install numpy openpyxl
    python ri_official.py --download          # once a year, after BLS posts last December's table
    python cpi_contrib.py                     # monthly, after each CPI release
    python cpi_contrib.py --start 1970 --key $BLS_API_KEY      # v2 API, 20yr/req

Writes ./web/data/* (JSON, JS, CSV) and ./cpi_dashboard_standalone.html (web/index.html
with the data inlined).  ./ri_official.csv is picked up automatically when present.

Registration key (free, 500 req/day): https://data.bls.gov/registrationEngine/
Without a key the v1 API is used: 25 series & 10 years per request, 25 req/day/IP.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

API_V1 = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
API_V2 = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

NSA_PREFIX = "CUUR0000"
SA_PREFIX = "CUSR0000"

# ---------------------------------------------------------------------------
# 1. Series universe
# ---------------------------------------------------------------------------
# node id -> (BLS item code, human label EN, label ZH)
NODES: Dict[str, Tuple[str, str, str]] = {
    "headline":      ("SA0",      "All items",                      "整體 CPI"),
    "core":          ("SA0L1E",   "Core CPI",                       "核心 CPI"),
    # level 1 (spans headline)
    "food":          ("SAF1",     "Food",                           "食物"),
    "energy":        ("SA0E",     "Energy",                         "能源"),
    "core_goods":    ("SACL1E",   "Core goods",                     "核心商品"),
    "core_services": ("SASLE",    "Core services",                  "核心服務"),
    # alternative level-1 cut (spans headline) - needed to price the shelter split
    "commodities":   ("SAC",      "Commodities",                    "商品"),
    "services":      ("SAS",      "Services",                       "服務"),
    # level 2
    "food_home":     ("SAF11",    "Food at home",                   "在家食物"),
    "food_away":     ("SEFV",     "Food away from home",            "外食"),
    "energy_cmdty":  ("SACE",     "Energy commodities",             "能源商品"),
    "energy_svc":    ("SEHF",     "Energy services",                "能源服務"),
    "rent_shelter":  ("SAS2RS",   "Rent of shelter",                "住宅租金"),
    "svc_less_ros":  ("SASL2RS",  "Services less rent of shelter",  "服務(不含住宅租金)"),
}

# Spanning systems used to identify the weights.
# (parent, [children])  -- children are mutually exclusive & exhaust the parent.
SPANNING_SYSTEMS: List[Tuple[str, List[str]]] = [
    ("headline", ["food", "energy", "core_goods", "core_services"]),
    ("headline", ["commodities", "services"]),
    ("food",     ["food_home", "food_away"]),
    ("energy",   ["energy_cmdty", "energy_svc"]),
    ("services", ["rent_shelter", "svc_less_ros"]),
]

# ---------------------------------------------------------------------------
# 2. Display breakdowns.  A component = signed sum of nodes (weights AND
#    contributions are linear, so differences are legal).
# ---------------------------------------------------------------------------
BREAKDOWNS: Dict[str, Dict] = {
    "basic4": {
        "label_en": "4 components",
        "label_zh": "四大類",
        "components": [
            {"id": "food",          "en": "Food",          "zh": "食物",
             "color": "#2f6fd0", "terms": {"food": 1}},
            {"id": "energy",        "en": "Energy",        "zh": "能源",
             "color": "#d4541e", "terms": {"energy": 1}},
            {"id": "core_goods",    "en": "Core goods",    "zh": "核心商品",
             "color": "#9a63c4", "terms": {"core_goods": 1}},
            {"id": "core_services", "en": "Core services", "zh": "核心服務",
             "color": "#e8c02a", "terms": {"core_services": 1}},
        ],
    },
    "detail7": {
        "label_en": "7 components",
        "label_zh": "七細項",
        "components": [
            {"id": "food_home",   "en": "Food at home",     "zh": "在家食物",
             "color": "#1f4e9c", "terms": {"food_home": 1}},
            {"id": "food_away",   "en": "Food away",        "zh": "外食",
             "color": "#4d92e0", "terms": {"food_away": 1}},
            {"id": "energy_cmdty","en": "Energy commodities","zh": "能源商品",
             "color": "#c0391b", "terms": {"energy_cmdty": 1}},
            {"id": "energy_svc",  "en": "Energy services",  "zh": "能源服務",
             "color": "#e8794a", "terms": {"energy_svc": 1}},
            {"id": "core_goods",  "en": "Core goods",       "zh": "核心商品",
             "color": "#9a63c4", "terms": {"core_goods": 1}},
            {"id": "shelter",     "en": "Rent of shelter",  "zh": "住宅租金",
             "color": "#e8c02a", "terms": {"rent_shelter": 1}},
            {"id": "supercore",   "en": "Core services ex shelter", "zh": "核心服務(不含住宅)",
             "color": "#8f9c2b", "terms": {"svc_less_ros": 1, "energy_svc": -1}},
        ],
    },
}

OVERLAYS = [
    {"id": "headline", "en": "All items", "zh": "整體 CPI", "color": "#ffffff", "width": 2.0},
    {"id": "core",     "en": "Core CPI",  "zh": "核心 CPI", "color": "#22d3ee", "width": 1.6},
]


# ---------------------------------------------------------------------------
# 3. BLS fetch (with on-disk cache)
# ---------------------------------------------------------------------------
def _post(url: str, payload: dict, timeout: int = 90) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": "cpi-contrib/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_series(series_ids: Sequence[str], start_year: int, end_year: int,
                 api_key: Optional[str] = None, cache_dir: Optional[str] = None,
                 max_cache_age_h: float = 12.0) -> Dict[str, Dict[str, float]]:
    """Return {series_id: {'YYYY-MM': index_level}}.  Missing months are simply absent."""
    out: Dict[str, Dict[str, float]] = {sid: {} for sid in series_ids}
    url = API_V2 if api_key else API_V1
    span = 20 if api_key else 10          # years per request
    chunk = 50 if api_key else 25         # series per request

    windows = []
    y0 = start_year
    while y0 <= end_year:
        y1 = min(y0 + span - 1, end_year)
        windows.append((y0, y1))
        y0 = y1 + 1

    # Merge anything already on disk first, so a rate-limited or offline run still works.
    if cache_dir and os.path.isdir(cache_dir):
        for fn in sorted(os.listdir(cache_dir)):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(cache_dir, fn), encoding="utf-8") as f:
                    blob = json.load(f)
            except (OSError, ValueError):
                continue
            for s in blob.get("Results", {}).get("series", []):
                if s["seriesID"] not in out:
                    continue
                for row in s.get("data", []):
                    p = row.get("period", "")
                    if not p.startswith("M") or p == "M13":
                        continue
                    try:
                        out[s["seriesID"]][f"{row['year']}-{p[1:]}"] = float(row["value"])
                    except (ValueError, KeyError):
                        continue

    def covered(sids, a, b):
        for sid in sids:
            if not any(a <= int(k[:4]) <= b for k in out[sid]):
                return False
        return True

    for i in range(0, len(series_ids), chunk):
        batch = list(series_ids[i:i + chunk])
        for (a, b) in windows:
            if covered(batch, a, b):
                continue
            payload = {"seriesid": batch, "startyear": str(a), "endyear": str(b)}
            if api_key:
                payload["registrationkey"] = api_key

            data = None
            ck = None
            if cache_dir:
                os.makedirs(cache_dir, exist_ok=True)
                tag = hashlib.md5(("|".join(batch) + f"#{a}#{b}").encode()).hexdigest()[:16]
                ck = os.path.join(cache_dir, f"{a}_{b}_{tag}.json")
                if os.path.exists(ck) and (time.time() - os.path.getmtime(ck)) < max_cache_age_h * 3600:
                    with open(ck, encoding="utf-8") as f:
                        data = json.load(f)

            if data is None:
                data = _post(url, payload)
                if data.get("status") != "REQUEST_SUCCEEDED":
                    raise RuntimeError(f"BLS API error: {data.get('status')} {data.get('message')}")
                if ck:
                    with open(ck, "w", encoding="utf-8") as f:
                        json.dump(data, f)
                time.sleep(0.4)

            for s in data.get("Results", {}).get("series", []):
                sid = s["seriesID"]
                for row in s.get("data", []):
                    p = row.get("period", "")
                    if not p.startswith("M") or p == "M13":
                        continue
                    try:
                        out[sid][f"{row['year']}-{p[1:]}"] = float(row["value"])
                    except (ValueError, KeyError):
                        continue
    return out


# ---------------------------------------------------------------------------
# 4. Weight (relative importance) reconstruction
# ---------------------------------------------------------------------------
def _dec(year: int) -> str:
    return f"{year - 1}-12"


def _solve_shares(A: np.ndarray, b: np.ndarray) -> np.ndarray:
    """min ||A w - b||  s.t.  sum(w) = 1.  Solved by eliminating the last variable."""
    n = A.shape[1]
    if n == 1:
        return np.array([1.0])
    Ared = A[:, :-1] - A[:, [-1]]
    bred = b - A[:, -1]
    w_head, *_ = np.linalg.lstsq(Ared, bred, rcond=None)
    return np.append(w_head, 1.0 - w_head.sum())


def estimate_shares(idx: Dict[str, Dict[str, float]], parent: str, children: List[str],
                    year: int, min_obs: Optional[int] = None) -> Optional[Tuple[np.ndarray, float, int]]:
    """Shares of `children` inside `parent`, anchored at December of year-1.

    Returns (shares, max_abs_residual, n_obs) or None if not identifiable.
    """
    a = _dec(year)
    if a not in idx.get(parent, {}) or any(a not in idx.get(c, {}) for c in children):
        return None
    need = min_obs if min_obs is not None else max(len(children), 3)
    rows, rhs = [], []
    for m in range(1, 13):
        t = f"{year}-{m:02d}"
        if t not in idx[parent] or any(t not in idx[c] for c in children):
            continue
        rows.append([idx[c][t] / idx[c][a] for c in children])
        rhs.append(idx[parent][t] / idx[parent][a])
    if len(rows) < need:
        return None
    A, b = np.array(rows), np.array(rhs)
    w = _solve_shares(A, b)
    return w, float(np.max(np.abs(A @ w - b))), len(rows)


Shares = Dict[Tuple[str, Tuple[str, ...]], Dict[int, Tuple[np.ndarray, float, int, bool]]]


def load_official_ri(path: str) -> Dict[Tuple[int, str], Dict[str, float]]:
    """Read ri_official.csv (from ri_official.py) -> {(dec_year, basis): {node: RI 0-1}}."""
    out: Dict[Tuple[int, str], Dict[str, float]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["node"] in NODES and r["cpi_u"] not in ("", None):
                out.setdefault((int(r["dec_year"]), r["basis"]), {})[r["node"]] = float(r["cpi_u"]) / 100
    return out


def _official_shares(official, parent: str, kids: List[str], year: int) -> Optional[Tuple[np.ndarray, float]]:
    """Children's shares inside `parent` for weight-year `year` from BLS's December table.

    BLS rounds each RI to 3 decimals, so the children rarely add up to the parent to the
    last digit; they are renormalised and the gap (in pp of all items) is returned.
    """
    tab = official.get((year - 1, "new"))
    if not tab or parent not in tab and parent != "headline" or any(k not in tab for k in kids):
        return None
    vals = np.array([tab[k] for k in kids])
    parent_ri = 1.0 if parent == "headline" else tab[parent]
    return vals / vals.sum(), float(vals.sum() - parent_ri) * 100


def ri_at(idx: Dict[str, Dict[str, float]], shares: Shares, m: str,
          basis_of: "callable") -> Dict[str, float]:
    """RI (0-1) of every node in month m.  basis_of(key) -> (weight_year, anchor) or None."""
    out = {"headline": 1.0}
    for parent, kids in SPANNING_SYSTEMS:
        key = (parent, tuple(kids))
        b = basis_of(key)
        if b is None or parent not in out:
            continue
        wy, anchor = b
        if anchor not in idx[parent] or m not in idx[parent]:
            continue
        pr = idx[parent][m] / idx[parent][anchor]
        for k, s in zip(kids, shares[key][wy][0]):
            if anchor in idx[k] and m in idx[k]:
                out[k] = out[parent] * s * (idx[k][m] / idx[k][anchor]) / pr
    return out


def build_weights(idx: Dict[str, Dict[str, float]], months: List[str], verbose: bool = False,
                  official: Optional[Dict[Tuple[int, str], Dict[str, float]]] = None,
                  ) -> Tuple[Dict[str, Dict[str, float]], Dict[str, dict], Shares]:
    """Monthly relative importance for every node, expressed as a share of all-items (0-1).

    With `official` (see load_official_ri), BLS's published December weights are used as
    the anchor wherever they exist; least squares only fills the years BLS doesn't cover.
    """
    years = sorted({int(m[:4]) for m in months})
    diag: Dict[str, dict] = {}

    # shares[(parent, tuple(children))][year] = (anchor_shares, residual, nobs, carried_flag)
    shares: Shares = {}
    for parent, kids in SPANNING_SYSTEMS:
        key = (parent, tuple(kids))
        shares[key] = {}
        last_good: Optional[np.ndarray] = None
        for y in years + [years[-1] + 1]:       # +1: next year's anchor = this December
            res = estimate_shares(idx, parent, kids, y)
            off = _official_shares(official, parent, kids, y) if official else None
            if off is not None:
                w = off[0]
                if res is not None:              # how well do BLS's weights fit eq. (1)?
                    A = np.array([[idx[c][f"{y}-{mm:02d}"] / idx[c][_dec(y)] for c in kids]
                                  for mm in range(1, 13)
                                  if all(f"{y}-{mm:02d}" in idx[c] for c in kids + [parent])])
                    b = np.array([idx[parent][f"{y}-{mm:02d}"] / idx[parent][_dec(y)]
                                  for mm in range(1, 13)
                                  if all(f"{y}-{mm:02d}" in idx[c] for c in kids + [parent])])
                    r, n = float(np.max(np.abs(A @ w - b))), len(b)
                else:
                    r, n = float("nan"), 0
                shares[key][y] = (w, r, n, False)
                last_good = w
            elif res is None:
                if last_good is None or y > years[-1]:
                    continue
                # Not enough months yet this year (e.g. January of the current year):
                # carry last year's anchor forward. Small error until ~4 prints land.
                shares[key][y] = (last_good, float("nan"), 0, True)
            else:
                w, r, n = res
                shares[key][y] = (w, r, n, False)
                last_good = w
            if y > years[-1]:
                continue
            w, r, n, carried = shares[key][y]
            src = "official" if off is not None else ("carried" if carried else "estimated")
            if verbose:
                print(f"   {parent:<12} {y}  n={n:2d}  resid={r:.1e}  {src:<9} "
                      + " ".join(f"{k}={v * 100:6.3f}" for k, v in zip(kids, w)))
            d = {
                "children": kids,
                "shares_dec": [round(float(x), 8) for x in w],
                "max_resid": None if np.isnan(r) else r,
                "n_obs": n,
                "carried_forward": carried,
                "source": src,
            }
            if off is not None:
                d["official_rounding_gap_pp"] = round(off[1], 4)
                if res is not None:
                    d["shares_estimated"] = [round(float(x), 8) for x in res[0]]
            diag.setdefault(f"{parent}:{'+'.join(kids)}", {})[str(y)] = d

    # Roll shares forward month by month, then chain parent -> child.
    ri: Dict[str, Dict[str, float]] = {n: {} for n in NODES}
    for m in months:
        y = int(m[:4])
        a = _dec(y)
        # BLS re-references the expenditure weights in December: the December RI that
        # BLS publishes is already on the NEXT year's basis, and it is the base that
        # January's MoM and the following December's YoY are computed against.
        new_basis_dec = m.endswith("-12")

        def basis_of(key, y=y, a=a, m=m, new_basis_dec=new_basis_dec):
            if new_basis_dec and (y + 1) in shares[key]:
                return y + 1, m                  # December is already on next year's basis
            if y in shares[key]:
                return y, a
            return None

        for k, v in ri_at(idx, shares, m, basis_of).items():
            ri[k][m] = v
    return ri, diag, shares


def compare_official(idx: Dict[str, Dict[str, float]], shares_est: Shares,
                     official: Dict[Tuple[int, str], Dict[str, float]]) -> List[list]:
    """December-by-December check of the recovered weights against BLS's tables.

    basis 'new': estimated anchor for weight-year Y+1 vs BLS December Y table.
    basis 'old': estimated weight-year-Y anchor price-updated to December Y (eq. 2)
                 vs BLS's old-weights December Y table.  For these rows we also roll
                 BLS's own December Y-1 table forward, which isolates eq. (2) itself.
    """
    rows = []
    for (y, basis), tab in sorted(official.items()):
        m = f"{y}-12"
        if basis == "new":
            est = ri_at(idx, shares_est, m,
                        lambda key: (y + 1, m) if (y + 1) in shares_est[key] else None)
            rolled = {}
        else:
            a = f"{y - 1}-12"
            est = ri_at(idx, shares_est, m, lambda key: (y, a) if y in shares_est[key] else None)
            prev = official.get((y - 1, "new"))
            rolled = {}
            if prev:
                tmp: Shares = {}
                for parent, kids in SPANNING_SYSTEMS:
                    o = _official_shares(official, parent, kids, y)
                    if o is not None:
                        tmp[(parent, tuple(kids))] = {y: (o[0], 0.0, 0, False)}
                rolled = ri_at(idx, tmp, m, lambda key: (y, a) if key in tmp else None)
        for node, v in tab.items():
            e, r = est.get(node), rolled.get(node)
            rows.append([y, basis, node, round(v * 100, 3),
                         None if e is None else round(e * 100, 4),
                         None if e is None else round((e - v) * 100, 4),
                         None if r is None else round(r * 100, 4),
                         None if r is None else round((r - v) * 100, 4)])
    return rows


# ---------------------------------------------------------------------------
# 5. Contributions
# ---------------------------------------------------------------------------
def _prev_available(months: List[str], i: int, idx_all: Dict[str, float]) -> Optional[int]:
    j = i - 1
    while j >= 0:
        if months[j] in idx_all:
            return j
        j -= 1
    return None


def compute_contributions(idx_sa, idx_nsa, ri, months, breakdown: str,
                          yoy_method: str = "chained", reconcile: str = "proportional"):
    """Return dict with per-month MoM (SA) and YoY (NSA) contributions per component."""
    comps = BREAKDOWNS[breakdown]["components"]
    ids = [c["id"] for c in comps]

    def node_ri(node: str, m: str) -> Optional[float]:
        return ri.get(node, {}).get(m)

    def comp_weight(c, m) -> Optional[float]:
        tot = 0.0
        for node, sign in c["terms"].items():
            v = node_ri(node, m)
            if v is None:
                return None
            tot += sign * v
        return tot

    # ---- step 1: single-period (month-over-previous-available-month) contributions
    def step_contribs(idx_src) -> Dict[str, Dict[str, float]]:
        """{month: {comp_id: contribution as a FRACTION of the all-items level}}"""
        res: Dict[str, Dict[str, float]] = {}
        avail = [m for m in months if m in idx_src["headline"]]
        for i, m in enumerate(avail):
            if i == 0:
                continue
            p = avail[i - 1]
            row = {}
            ok = True
            for c in comps:
                w = comp_weight(c, p)
                if w is None:
                    ok = False
                    break
                g = 0.0
                for node, sign in c["terms"].items():
                    if p not in idx_src.get(node, {}) or m not in idx_src.get(node, {}):
                        ok = False
                        break
                    # weight the growth by that node's own share inside the component
                    wn = node_ri(node, p)
                    if wn is None:
                        ok = False
                        break
                    g += sign * wn * (idx_src[node][m] / idx_src[node][p] - 1.0)
                if not ok:
                    break
                row[c["id"]] = g
            if not ok:
                continue
            # reconcile with the actual headline move
            total = idx_src["headline"][m] / idx_src["headline"][p] - 1.0
            s = sum(row.values())
            resid = total - s
            if reconcile == "proportional":
                wts = {c["id"]: abs(comp_weight(c, p) or 0.0) for c in comps}
                wsum = sum(wts.values()) or 1.0
                for k in row:
                    row[k] += resid * wts[k] / wsum
            row["__total__"] = total
            row["__resid__"] = resid
            row["__prev__"] = p
            res[m] = row
        return res

    mom_steps = step_contribs(idx_sa)
    nsa_steps = step_contribs(idx_nsa)

    # ---- step 2: YoY
    avail_nsa = [m for m in months if m in idx_nsa["headline"]]
    pos = {m: i for i, m in enumerate(avail_nsa)}
    yoy: Dict[str, Dict[str, float]] = {}
    for m in avail_nsa:
        y, mm = int(m[:4]), int(m[5:])
        base = f"{y - 1}-{mm:02d}"
        if base not in pos or m not in pos:
            continue
        i0, i1 = pos[base], pos[m]
        chain = avail_nsa[i0 + 1:i1 + 1]          # every step between base and m
        if not chain or any(t not in nsa_steps for t in chain):
            continue
        if yoy_method == "naive":
            row = {}
            ok = True
            for c in comps:
                w = comp_weight(c, base)
                if w is None:
                    ok = False
                    break
                g = 0.0
                for node, sign in c["terms"].items():
                    wn = node_ri(node, base)
                    if wn is None or base not in idx_nsa[node] or m not in idx_nsa[node]:
                        ok = False
                        break
                    g += sign * wn * (idx_nsa[node][m] / idx_nsa[node][base] - 1.0)
                if not ok:
                    break
                row[c["id"]] = g
            if not ok:
                continue
        else:
            # exact telescoping:  prod(R)-1 = sum_k (R_k-1)*prod_{m<k} R_m
            row = {cid: 0.0 for cid in ids}
            carry = 1.0
            for t in chain:
                st = nsa_steps[t]
                for cid in ids:
                    row[cid] += st[cid] * carry
                carry *= (1.0 + st["__total__"])
        total = idx_nsa["headline"][m] / idx_nsa["headline"][base] - 1.0
        s = sum(row[cid] for cid in ids)
        resid = total - s
        if reconcile == "proportional":
            wts = {cid: abs(comp_weight(c, base) or 0.0) for cid, c in zip(ids, comps)}
            wsum = sum(wts.values()) or 1.0
            for cid in ids:
                row[cid] += resid * wts[cid] / wsum
        row["__total__"] = total
        row["__resid__"] = resid
        row["__base__"] = base
        row["__span__"] = len(chain)
        yoy[m] = row

    return mom_steps, yoy, comps


# ---------------------------------------------------------------------------
# 6. Assemble the payload for the front end
# ---------------------------------------------------------------------------
def build_payload(idx_sa, idx_nsa, ri, months, yoy_method="naive", reconcile="none") -> dict:
    payload = {
        "meta": {
            "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "U.S. Bureau of Labor Statistics, CPI-U (api.bls.gov)",
            "method": ("Anchored on BLS's published December relative importance; within-year "
                       "price-updated; see methodology.html."),
            "variants": {"default": "naive YoY, residual shown (matches Bloomberg WMA)",
                         "_exact": "chained YoY, residual re-allocated (bars sum to the print)"},
            "mom_basis": "seasonally adjusted (CUSR0000*)",
            "yoy_basis": "not seasonally adjusted (CUUR0000*)",
        },
        "breakdowns": {},
        "overlays": OVERLAYS,
    }

    for bk in BREAKDOWNS:
        entry = {
            "label_en": BREAKDOWNS[bk]["label_en"],
            "label_zh": BREAKDOWNS[bk]["label_zh"],
        }
        # two flavours: "bloomberg" (naive YoY, residual left visible) and
        # "exact" (chained YoY, residual re-allocated so the bars add to the print)
        for flavour, ym, rec in (("", "naive", "none"), ("_exact", "chained", "proportional")):
            mom, yoy, comps = compute_contributions(idx_sa, idx_nsa, ri, months, bk, ym, rec)
            mom_months = [m for m in months if m in mom]
            yoy_months = [m for m in months if m in yoy]

            def pack(store, mlist, idx_src, base_key):
                ids = [c["id"] for c in comps]
                out = {
                    "dates": mlist,
                    "contrib": {cid: [round(store[m][cid] * 100, 6) for m in mlist] for cid in ids},
                    "headline": [round(store[m]["__total__"] * 100, 6) for m in mlist],
                    "residual": [round(store[m]["__resid__"] * 100, 8) for m in mlist],
                    "weights": {},
                }
                for cid, c in zip(ids, comps):
                    col = []
                    for m in mlist:
                        b = store[m][base_key]
                        tot, bad = 0.0, False
                        for node, sign in c["terms"].items():
                            v = ri.get(node, {}).get(b)
                            if v is None:
                                bad = True
                                break
                            tot += sign * v
                        col.append(None if bad else round(tot * 100, 4))
                    out["weights"][cid] = col
                out["span"] = [store[m].get("__span__", _month_gap(store[m].get("__prev__", m), m))
                               for m in mlist]
                core = []
                for m in mlist:
                    b = store[m][base_key]
                    if b in idx_src["core"] and m in idx_src["core"]:
                        core.append(round((idx_src["core"][m] / idx_src["core"][b] - 1) * 100, 6))
                    else:
                        core.append(None)
                out["core"] = core
                return out

            entry["mom" + flavour] = pack(mom, mom_months, idx_sa, "__prev__")
            entry["yoy" + flavour] = pack(yoy, yoy_months, idx_nsa, "__base__")
        entry["components"] = [{k: c[k] for k in ("id", "en", "zh", "color")} for c in comps]
        payload["breakdowns"][bk] = entry
    return payload


def _month_gap(a: str, b: str) -> int:
    ya, ma = int(a[:4]), int(a[5:])
    yb, mb = int(b[:4]), int(b[5:])
    return (yb - ya) * 12 + (mb - ma)


# ---------------------------------------------------------------------------
# 7. CLI
# ---------------------------------------------------------------------------
def month_range(start_year: int, end: str) -> List[str]:
    ey, em = int(end[:4]), int(end[5:])
    out = []
    for y in range(start_year, ey + 1):
        for m in range(1, 13):
            if y == ey and m > em:
                break
            out.append(f"{y}-{m:02d}")
    return out


def _print_check(rows: List[list]) -> None:
    """Per December table: largest |estimated - official| over the basic4 nodes, in pp."""
    nodes = ("food", "energy", "core_goods", "core_services")
    by: Dict[Tuple[int, str], Dict[str, list]] = {}
    for r in rows:
        by.setdefault((r[0], r[1]), {})[r[2]] = r
    print("   Dec  basis   food    energy  c.goods c.svcs   (estimated - official, pp)   max|Δ| all nodes")
    for (y, basis), d in sorted(by.items()):
        cells = [d[n][5] if n in d and d[n][5] is not None else None for n in nodes]
        if all(c is None for c in cells):
            continue
        allmax = max((abs(r[5]) for r in d.values() if r[5] is not None), default=float("nan"))
        print(f"   {y} {basis:<5} " + " ".join("   n/a  " if c is None else f"{c:+7.3f} " for c in cells)
              + f"                     {allmax:.3f}")


HERE = os.path.dirname(os.path.abspath(__file__))
_STANDALONE_TAG = re.compile(r'<script src="[^"]*cpi_data\.js"[^>]*></script>')


def write_standalone(template: str, js_payload: str, dest: str) -> bool:
    """index.html with the data inlined, so the page opens from anywhere (email, Drive…)."""
    with open(template, encoding="utf-8") as f:
        html = f.read()
    if not _STANDALONE_TAG.search(html):
        print(f"   ! {template}: no <script src=...cpi_data.js> tag, standalone page not written")
        return False
    html = _STANDALONE_TAG.sub(
        lambda _: "<script>window.CPI_STANDALONE=true;\n" + js_payload + "</script>", html, count=1)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(html)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="US CPI contribution decomposition")
    ap.add_argument("--start", type=int, default=1990, help="first year to pull (default 1990)")
    ap.add_argument("--key", default=os.environ.get("BLS_API_KEY"), help="BLS v2 registration key")
    ap.add_argument("--outdir", default=os.path.join(HERE, "web", "data"),
                    help="data files for the site (default ./web/data)")
    ap.add_argument("--cache", default=os.path.join(HERE, "cache"))
    ap.add_argument("--yoy-method", choices=["chained", "naive"], default="chained")
    ap.add_argument("--reconcile", choices=["proportional", "none"], default="proportional",
                    help="how to absorb the (tiny) rounding residual; 'none' reproduces Bloomberg literally")
    default_ri = os.path.join(HERE, "ri_official.csv")
    ap.add_argument("--ri-file", default=default_ri if os.path.exists(default_ri) else None,
                    help="ri_official.csv from ri_official.py: use BLS's published December "
                         "weights as anchors and write a recovered-vs-official comparison "
                         "(default: ./ri_official.csv when it exists)")
    ap.add_argument("--no-ri-file", action="store_true",
                    help="ignore official tables, recover every weight by least squares")
    ap.add_argument("--html", default=os.path.join(HERE, "cpi_dashboard_standalone.html"),
                    help="where to write the self-contained dashboard ('' to skip)")
    ap.add_argument("--template", default=os.path.join(HERE, "web", "index.html"))
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.no_ri_file:
        args.ri_file = None

    os.makedirs(args.outdir, exist_ok=True)
    cache = None if args.no_cache else args.cache
    end_year = date.today().year

    nsa_ids = [NSA_PREFIX + code for code, _, _ in NODES.values()]
    sa_ids = [SA_PREFIX + code for code, _, _ in NODES.values()]

    print(f"→ fetching {len(nsa_ids)} NSA + {len(sa_ids)} SA series, {args.start}-{end_year} "
          f"({'v2 w/ key' if args.key else 'v1 no key'})")
    raw_nsa = fetch_series(nsa_ids, args.start, end_year, args.key, cache)
    raw_sa = fetch_series(sa_ids, args.start, end_year, args.key, cache)

    idx_nsa = {nid: raw_nsa[NSA_PREFIX + NODES[nid][0]] for nid in NODES}
    idx_sa = {nid: raw_sa[SA_PREFIX + NODES[nid][0]] for nid in NODES}

    last = max(idx_nsa["headline"])
    months = month_range(args.start, last)
    have = [m for m in months if m in idx_nsa["headline"]]
    missing = [m for m in months if m not in idx_nsa["headline"]]
    print(f"→ {len(have)} monthly observations, latest = {last}")
    if missing:
        print(f"   ! not published: {', '.join(missing)}")

    print("→ recovering relative importance")
    ri, diag, shares_est = build_weights(idx_nsa, months, verbose=args.verbose and not args.ri_file)
    weight_source = "least squares on the Laspeyres identity"
    if args.ri_file:
        official = load_official_ri(args.ri_file)
        print(f"→ official December RI tables from {args.ri_file}: "
              f"{min(y for y, _ in official)}-{max(y for y, _ in official)}")
        need = int(last[:4]) - 1
        if (need, "new") not in official:
            print(f"   ! BLS's December {need} table is not in {args.ri_file}: weights for {need + 1} "
                  f"are estimated.  once BLS posts it (early in the year) run: python ri_official.py --download")
        check = compare_official(idx_nsa, shares_est, official)
        cpath = os.path.join(args.outdir, "ri_official_vs_estimated.csv")
        with open(cpath, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["dec_year", "basis", "node", "official", "estimated", "est_minus_official",
                        "official_rolled", "rolled_minus_official"])
            w.writerows(check)
        _print_check(check)
        print(f"   full table: {cpath}")
        ri, diag, _ = build_weights(idx_nsa, months, verbose=args.verbose, official=official)
        weight_source = ("BLS published December relative importance (" + args.ri_file
                         + "); least squares where no table exists")

    w_last = {nid: ri[nid].get(last) for nid in
              ("food", "energy", "core_goods", "core_services")}
    print("   latest RI:  " + "  ".join(
        f"{k}={v * 100:.3f}" for k, v in w_last.items() if v is not None)
        + f"   (sum={sum(v for v in w_last.values() if v) * 100:.3f})")

    print("→ computing contributions")
    payload = build_payload(idx_sa, idx_nsa, ri, months, args.yoy_method, args.reconcile)
    payload["meta"]["weight_diagnostics"] = diag
    payload["meta"]["weight_source"] = weight_source
    b4 = diag.get("headline:food+energy+core_goods+core_services", {})
    payload["meta"]["weight_years"] = {src: sorted(int(y) for y, d in b4.items() if d["source"] == src)
                                       for src in ("official", "estimated", "carried")}
    payload["meta"]["unpublished_months"] = missing
    # next BLS release, from release_schedule.json (schedule.py); shown on the dashboard
    try:
        with open(os.path.join(HERE, "release_schedule.json"), encoding="utf-8") as f:
            rel = json.load(f)["releases"]
        today = date.today().isoformat()
        up = sorted((r for r in rel if r["date"] > today and r["ref"] > last), key=lambda r: r["date"])
        if up:
            payload["meta"]["next_release"] = {"ref": up[0]["ref"], "date": up[0]["date"],
                                               "time_et": up[0]["time_et"]}
    except (OSError, ValueError, KeyError):
        pass

    jpath = os.path.join(args.outdir, "cpi_data.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    jspath = os.path.join(args.outdir, "cpi_data.js")
    with open(jspath, "w", encoding="utf-8") as f:
        f.write("window.CPI_DATA = ")
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    # tidy CSVs
    for bk in payload["breakdowns"]:
        for tf in ("mom", "yoy"):
            d = payload["breakdowns"][bk][tf]
            ids = list(d["contrib"])
            p = os.path.join(args.outdir, f"cpi_contrib_{bk}_{tf}.csv")
            with open(p, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["date", "headline", "core"] + [f"contrib_{i}" for i in ids]
                           + [f"weight_{i}" for i in ids] + ["residual", "span_months"])
                for k, m in enumerate(d["dates"]):
                    w.writerow([m, d["headline"][k], d["core"][k]]
                               + [d["contrib"][i][k] for i in ids]
                               + [d["weights"][i][k] for i in ids]
                               + [d["residual"][k], d["span"][k]])

    b = payload["breakdowns"]["basic4"]
    print(f"\n  latest YoY {b['yoy']['dates'][-1]}: headline {b['yoy']['headline'][-1]:.3f}%")
    for c in b["components"]:
        print(f"      {c['en']:<16} {b['yoy']['contrib'][c['id']][-1]:+7.3f} pp"
              f"   (w={b['yoy']['weights'][c['id']][-1]:.3f})")
    print(f"  latest MoM {b['mom']['dates'][-1]}: headline {b['mom']['headline'][-1]:.3f}%")
    for c in b["components"]:
        print(f"      {c['en']:<16} {b['mom']['contrib'][c['id']][-1]:+7.3f} pp")
    print(f"\n✓ wrote {jpath}, {jspath} and CSVs to {args.outdir}")
    if args.html:
        with open(jspath, encoding="utf-8") as f:
            js = f.read().strip()
        if write_standalone(args.template, js, args.html):
            print(f"✓ wrote {args.html}  (self-contained dashboard, data through {last})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
