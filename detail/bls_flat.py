# -*- coding: utf-8 -*-
"""
BLS bulk ("flat") files: download and read
==========================================

https://download.bls.gov/pub/time.series/cu/ holds every CPI series as tab-separated text.
No API key and no daily limit, unlike api.bls.gov. Format details and traps: PITFALLS.md §8.
"""

from __future__ import annotations

import csv
import os
import time
import urllib.request
from typing import Dict

from common import norm

FLAT = "https://download.bls.gov/pub/time.series/cu/"
# cu.item (codes and names), all items, the eight U.S. major groups, and the special
# aggregates file (core CPI, SA0L1E, is only there)
FLAT_FILES = ["cu.item", "cu.data.1.AllItems", "cu.data.11.USFoodBeverage", "cu.data.12.USHousing",
              "cu.data.13.USApparel", "cu.data.14.USTransportation", "cu.data.15.USMedical",
              "cu.data.16.USRecreation", "cu.data.17.USEducationAndCommunication",
              "cu.data.18.USOtherGoodsAndServices", "cu.data.20.USCommoditiesServicesSpecial"]

Indexes = Dict[str, Dict[str, Dict[str, float]]]      # {'U'|'S': {item_code: {'YYYY-MM': value}}}


def fetch(cache: str, ua: str, max_age_h: float = 12.0) -> None:
    """Download the files into cache/, skipping any fetched in the last max_age_h hours."""
    os.makedirs(cache, exist_ok=True)
    for fn in FLAT_FILES:
        p = os.path.join(cache, fn)
        if os.path.exists(p) and time.time() - os.path.getmtime(p) < max_age_h * 3600:
            continue
        req = urllib.request.Request(FLAT + fn, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        if data[:200].lstrip().lower().startswith(b"<!doctype html"):
            raise RuntimeError(f"{fn}: got an HTML page instead of data (blocked?)")
        with open(p, "wb") as f:
            f.write(data)
        print(f"   {len(data):>11,d}  {fn}")
        time.sleep(0.5)


def load_items(cache: str) -> Dict[str, str]:
    """normalised item name -> item code (first match wins, cu.item lists current codes first)."""
    out: Dict[str, str] = {}
    with open(os.path.join(cache, "cu.item"), encoding="utf-8", errors="ignore") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            out.setdefault(norm(r["item_name"]), r["item_code"].strip())
    return out


def load_indexes(cache: str, first_year: int) -> Indexes:
    """U.S. city average monthly indexes: 'U' not seasonally adjusted (CUUR0000*),
    'S' seasonally adjusted (CUSR0000*). Semiannual series, other areas and the annual
    average (M13) are skipped."""
    out: Indexes = {"U": {}, "S": {}}
    for fn in FLAT_FILES[1:]:
        with open(os.path.join(cache, fn), encoding="utf-8", errors="ignore") as f:
            next(f)
            for line in f:
                p = line.rstrip("\r\n").split("\t")
                if len(p) < 4:
                    continue
                sid = p[0].strip()
                if not (sid.startswith("CUUR0000") or sid.startswith("CUSR0000")):
                    continue
                per = p[2].strip()
                if not per.startswith("M") or per == "M13":
                    continue
                y = int(p[1])
                if y < first_year:
                    continue
                try:
                    v = float(p[3])
                except ValueError:
                    continue
                out["S" if sid[2] == "S" else "U"].setdefault(sid[8:], {})[f"{y}-{per[1:]}"] = v
    return out
