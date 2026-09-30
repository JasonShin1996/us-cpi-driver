# -*- coding: utf-8 -*-
"""
Relative importance of every item, every month
==============================================

  * December anchors: BLS's official December tables (ri_official_full.csv), new basis,
    matched by item name, with earlier names for renamed items (ALIASES, PITFALLS.md §3).
  * Within the year: RI_i(t) = RI_i(a) * [I_i(t)/I_i(a)] / [I_all(t)/I_all(a)],
    a = previous December; each December uses its own new-basis table.
  * Items BLS does not publish (unsampled items, the odd item without a U.S. index) are
    proxied by the nearest ancestor that has an index (PITFALLS.md §4).
  * Months an item is not published are filled, for the weights only, by moving it with its
    parent (PITFALLS.md §5).
"""

from __future__ import annotations

import csv
from typing import Dict, List, Optional

from common import norm

# Earlier names of items BLS has renamed, so older December tables still match
# (normalised current name -> normalised earlier names, newest first)
ALIASES = {
    "airline fares": ["airline fare"],
    "cable satellite and live streaming television service":
        ["cable and satellite television service", "cable and satellite television and radio service"],
    "purchase subscription and rental of video":
        ["video discs and other media including rental of video",
         "video discs and other media including rental of video and audio"],
    "recorded music and music subscriptions": ["audio discs tapes and other media"],
    "photographers and photo processing": ["photographers and film processing"],
    "club membership for shopping clubs fraternal or other organizations or participant sports fees":
        ["club dues and fees for participant sports and group exercises"],
    "day care and preschool": ["child care and nursery school"],
    "residential telephone services": ["land line telephone services"],
    "computers peripherals and smart home assistants":
        ["computers peripherals and smart home assistant devices", "personal computers and peripheral equipment"],
    "men s underwear nightwear swimwear and accessories": ["men s furnishings"],
    "women s underwear nightwear swimwear and accessories": ["women s underwear nightwear sportswear and accessories"],
}

Official = Dict[int, Dict[str, float]]      # {dec_year: {normalised name: RI %}}


def load_official(path: str, first_dec: int) -> Official:
    """New-basis December tables from ri_official_full.csv, from first_dec on."""
    out: Official = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            y = int(r["dec_year"])
            if r["basis"] != "new" or y < first_dec or r["cpi_u"] == "":
                continue
            out.setdefault(y, {}).setdefault(norm(r["item"]), float(r["cpi_u"]))
    return out


def anchor(official: Official, name: str, dec_year: int) -> Optional[float]:
    """The item's December RI: current name first, then earlier names."""
    t = official.get(dec_year)
    if t is None:
        return None
    n = norm(name)
    for key in [n] + ALIASES.get(n, []):
        if key in t:
            return t[key]
    return None


def index_sources(rows: List[dict], U: Dict[str, dict], S: Dict[str, dict]) -> Dict[int, int]:
    """Row id -> id of the row whose index stands for it (itself, or the nearest ancestor with
    a published index). Also sets row['proxy'] and row['sa'] in place."""
    src: Dict[int, int] = {}
    for row in rows:                                   # parents come before their children
        if row["code"] and row["code"] in U:
            src[row["id"]] = row["id"]
        else:
            a = row["parent"]
            while a is not None and a not in src:
                a = rows[a]["parent"]
            if a is not None:
                src[row["id"]] = src[a]
        row["proxy"] = row["id"] in src and src[row["id"]] != row["id"]
        row["sa"] = row["id"] in src and rows[src[row["id"]]]["code"] in S
    return src


def filled_indexes(rows: List[dict], src: Dict[int, int], U: Dict[str, dict],
                   all_u: Dict[str, float], months: List[str]) -> Dict[int, Dict[str, float]]:
    """NSA index per row with unpublished months filled by the parent's movement.
    For computing weights only; price changes and contributions use the raw index."""
    fill: Dict[int, Dict[str, float]] = {}
    for row in rows:
        i = row["id"]
        if i not in src:
            continue
        if src[i] != i:
            fill[i] = fill[src[i]]
            continue
        raw, f = U[row["code"]], {}
        par = row["parent"]
        while par is not None and par not in fill:
            par = rows[par]["parent"]
        pser = fill[par] if par is not None else all_u
        last_m = None
        for m in months:
            if m in raw:
                f[m], last_m = raw[m], m
            elif last_m is not None and m in pser and last_m in pser:
                f[m] = f[last_m] * pser[m] / pser[last_m]
                last_m = m
        fill[i] = f
    return fill


class Weights:
    """ri(row, 'YYYY-MM') -> relative importance, % of all items (None if unknown)."""

    def __init__(self, official: Official, fill: Dict[int, Dict[str, float]], all_u: Dict[str, float]):
        self.official, self.fill, self.all_u = official, fill, all_u

    def ri(self, row: dict, m: str) -> Optional[float]:
        y = int(m[:4])
        if m.endswith("-12") and (y in self.official):
            return anchor(self.official, row["name"], y)          # new basis
        a_m = f"{y - 1}-12"
        base = anchor(self.official, row["name"], y - 1)
        if base is None or row["id"] not in self.fill:
            return None
        u, all_u = self.fill[row["id"]], self.all_u
        if a_m not in u or m not in u or a_m not in all_u or m not in all_u:
            return None
        return base * (u[m] / u[a_m]) / (all_u[m] / all_u[a_m])
