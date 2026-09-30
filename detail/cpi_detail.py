#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Item-level contributions to US CPI  (data for web/detail.html)
==============================================================

Goes down the BLS expenditure hierarchy to --max-level (default 5; ~240 items, ~166 of them
the lowest level) and, for every month, computes each item's relative importance, its own
price change and its contribution to headline MoM (SA) and YoY (NSA).

Same method as cpi_contrib.py, one item at a time:
  * December anchors: BLS's official December relative importance (ri_official_full.csv,
    from ri_official.py), matched by item name.
  * within the year:  RI_i(t) = RI_i(a) * [I_i(t)/I_i(a)] / [I_all(t)/I_all(a)]
    December uses the new-basis table (it is the base of January and of next December's YoY).
  * MoM:  RI_i(p) * (I_i(t)/I_i(p) - 1), p = previous published month, seasonally adjusted
          index when BLS publishes one, otherwise not seasonally adjusted (what BLS itself does
          for series without identifiable seasonality)
  * YoY:  RI_i(t-12) * (I_i(t)/I_i(t-12) - 1), not seasonally adjusted

Data: BLS bulk files https://download.bls.gov/pub/time.series/cu/ (no API key, no daily limit):
  cu.item (codes and names), cu.data.1.AllItems, the eight US major-group files and
  cu.data.20 (special aggregates, for core CPI).

Output: web/data/detail.json

Known traps in the BLS data and how they are handled: detail/PITFALLS.md
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional

# this file lives in detail/; data and outputs are relative to the project root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLAT = "https://download.bls.gov/pub/time.series/cu/"
FLAT_FILES = ["cu.item", "cu.data.1.AllItems", "cu.data.11.USFoodBeverage", "cu.data.12.USHousing",
              "cu.data.13.USApparel", "cu.data.14.USTransportation", "cu.data.15.USMedical",
              "cu.data.16.USRecreation", "cu.data.17.USEducationAndCommunication",
              "cu.data.18.USOtherGoodsAndServices", "cu.data.20.USCommoditiesServicesSpecial"]
DEFAULT_UA = "cpi-contrib research@example.com"     # bls.gov wants a contact string with an email

# The eight BLS major groups (level 1 of the expenditure hierarchy) -> colour and Chinese name
GROUPS = [
    ("Food and beverages",          "食物與飲料", "#4d92e0"),
    ("Housing",                     "住宅",       "#e8c02a"),
    ("Apparel",                     "服裝",       "#c779d0"),
    ("Transportation",              "交通",       "#e8794a"),
    ("Medical care",                "醫療",       "#4fc3a1"),
    ("Recreation",                  "娛樂",       "#9ccc65"),
    ("Education and communication", "教育與通訊", "#7e8ce0"),
    ("Other goods and services",    "其他商品與服務", "#a1887f"),
]
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
# Items outside core CPI: everything under these nodes (food and energy)
NON_CORE_ROOTS = {"food", "household energy", "motor fuel"}
# Table-1 names that differ from cu.item names (checked against cu.item).
# "Care of invalids and elderly at home" has no published U.S. index; it stays uncoded and
# its contribution ends up in the remainder row, like the unsampled items.
NAME_OVERRIDES = {
    "housing at school excluding board": "SEHB01",                  # Lodging while at school
    "technical and business school tuition and fees": "SEEB04",     # Technical and vocational school tuition and fixed fees
}


def norm(s: str) -> str:
    s = s.lower().replace("&", "and")
    s = re.sub(r"\(\d+\)|\s*\d+$", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# ---------------------------------------------------------------------------
# download / load
# ---------------------------------------------------------------------------
def fetch(cache: str, ua: str, max_age_h: float = 12.0) -> None:
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


def load_indexes(cache: str, first_year: int) -> Dict[str, Dict[str, Dict[str, float]]]:
    """{'U'|'S': {item_code: {'YYYY-MM': value}}} for U.S. city average monthly series."""
    out = {"U": {}, "S": {}}
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


def _subtree_end(rows: List[dict], i: int) -> int:
    j = i + 1
    while j < len(rows) and rows[j]["level"] > rows[i]["level"]:
        j += 1
    return j


def _children(rows: List[dict], i: int) -> List[int]:
    return [j for j in range(i + 1, _subtree_end(rows, i)) if rows[j]["level"] == rows[i]["level"] + 1]


def _tol(n: int) -> float:
    return 0.002 + 0.0006 * n          # each published RI is rounded to 3 decimals


def repair_levels(rows: List[dict]) -> List[str]:
    """BLS's Excel Table 1 has a few mis-indented rows (e.g. 'Alcoholic beverages' one level too
    deep under Food, 'Information technology, hardware and services' one level too shallow).
    Repair them so every parent's RI equals the sum of its children's, and report each fix.

    A parent whose children fall short takes in the siblings that follow it; this is tried
    first, because putting a missing child back usually fixes the level above as well. Only
    then are surplus trailing children moved up a level."""

    def demote_once() -> Optional[str]:
        for i in range(len(rows)):
            kids = _children(rows, i)
            if not kids:
                continue
            tot, want, tol = sum(rows[k]["ri"] for k in kids), rows[i]["ri"], _tol(len(kids))
            if tot >= want - tol:
                continue
            acc, j, moved = tot, _subtree_end(rows, i), []
            while j < len(rows) and rows[j]["level"] == rows[i]["level"] and acc < want - tol:
                acc += rows[j]["ri"]
                moved.append(j)
                j = _subtree_end(rows, j)
            if moved and abs(acc - want) <= _tol(len(kids) + len(moved)):
                for kk in reversed(moved):
                    for jj in range(kk, _subtree_end(rows, kk)):
                        rows[jj]["level"] += 1
                return ", ".join(f"'{rows[k]['name']}'" for k in moved) + f" moved down into '{rows[i]['name']}'"
        return None

    def promote_once() -> Optional[str]:
        for i in range(len(rows)):
            kids = _children(rows, i)
            if not kids:
                continue
            tot, want, tol = sum(rows[k]["ri"] for k in kids), rows[i]["ri"], _tol(len(kids))
            if tot <= want + tol:
                continue
            acc = tot
            for n, k in enumerate(reversed(kids), 1):
                acc -= rows[k]["ri"]
                if abs(acc - want) <= tol:
                    moved = kids[-n:]
                    for kk in moved:
                        for j in range(kk, _subtree_end(rows, kk)):
                            rows[j]["level"] -= 1
                    return ", ".join(f"'{rows[k]['name']}'" for k in moved) + f" moved up out of '{rows[i]['name']}'"
        return None

    fixes = []
    for _ in range(50):
        f = demote_once() or promote_once()
        if not f:
            break
        fixes.append(f)
    return fixes


def hierarchy_problems(rows: List[dict]) -> List[str]:
    out = []
    for i in range(len(rows)):
        kids = _children(rows, i)
        if kids:
            tot = sum(rows[k]["ri"] for k in kids)
            if abs(tot - rows[i]["ri"]) > _tol(len(kids)):
                out.append(f"'{rows[i]['name']}' {rows[i]['ri']:.3f} vs children {tot:.3f}")
    return out


def load_hierarchy(ri_dir: str, max_level: int) -> List[dict]:
    """Expenditure-category rows of the newest December Table 1, down to max_level."""
    import openpyxl
    newest = max(glob.glob(os.path.join(ri_dir, "[0-9][0-9][0-9][0-9].xlsx")))
    ws = openpyxl.load_workbook(newest, read_only=True, data_only=True)["Table 1"]
    full, section = [], None
    for r in ws.iter_rows(values_only=True):
        if r[1] and isinstance(r[1], str) and r[2] is None and str(r[0]) == "0":
            section = r[1].strip().lower()
        if r[1] and isinstance(r[2], (int, float)) and section and section.startswith("expenditure"):
            full.append({"name": r[1].strip(), "level": int(r[0]), "ri": float(r[2])})
    for f in repair_levels(full):
        print(f"   fixed indentation: {f}")
    for p in hierarchy_problems(full):
        print(f"   ! hierarchy does not add up: {p}")
    rows = [dict(r) for r in full if r["level"] <= max_level]
    stack: List[int] = []
    for i, row in enumerate(rows):
        while stack and rows[stack[-1]]["level"] >= row["level"]:
            stack.pop()
        row["parent"] = stack[-1] if stack else None
        stack.append(i)
    for i, row in enumerate(rows):
        row["leaf"] = not (i + 1 < len(rows) and rows[i + 1]["level"] > row["level"])
    print(f"   hierarchy from {os.path.basename(newest)}: {len(rows)} items to level {max_level}, "
          f"{sum(r['leaf'] for r in rows)} lowest-level")
    return rows


def load_zh_names(path: str) -> Dict[str, str]:
    """detail/item_names_zh.csv: BLS item name -> Traditional Chinese name."""
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {norm(r["name"]): r["zh"].strip() for r in csv.DictReader(f) if r.get("zh")}


def load_official(path: str, first_dec: int) -> Dict[int, Dict[str, float]]:
    """{dec_year: {normalised name: RI %}} from the new-basis December tables."""
    out: Dict[int, Dict[str, float]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            y = int(r["dec_year"])
            if r["basis"] != "new" or y < first_dec or r["cpi_u"] == "":
                continue
            out.setdefault(y, {}).setdefault(norm(r["item"]), float(r["cpi_u"]))
    return out


# ---------------------------------------------------------------------------
# computation
# ---------------------------------------------------------------------------
def month_list(first: str, last: str) -> List[str]:
    y, m = int(first[:4]), int(first[5:])
    out = []
    while f"{y}-{m:02d}" <= last:
        out.append(f"{y}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def minus12(m: str) -> str:
    return f"{int(m[:4]) - 1}{m[4:]}"


def r3(x: Optional[float], d: int = 4) -> Optional[float]:
    return None if x is None else round(x, d)


def build(args) -> dict:
    cache = args.cache
    print("→ BLS bulk files")
    fetch(cache, args.user_agent)
    names = load_items(cache)
    idx = load_indexes(cache, int(args.history_start[:4]) - 1)
    U, S = idx["U"], idx["S"]
    all_u = U["SA0"]
    last = max(all_u)
    hist = month_list(args.history_start, last)
    sel_first = f"{int(last[:4]) - args.years}-{last[5:]}"
    sel = [m for m in hist if m > sel_first]
    ri_months = month_list(minus12(sel[0]), last)

    print("→ hierarchy and codes")
    rows = load_hierarchy(os.path.join(ROOT, "ri_official"), args.max_level)
    official = load_official(os.path.join(ROOT, "ri_official_full.csv"), int(ri_months[0][:4]) - 1)
    zh = load_zh_names(os.path.join(ROOT, "detail", "item_names_zh.csv"))
    missing_code, missing_zh = [], []
    for i, row in enumerate(rows):
        row["zh"] = zh.get(norm(row["name"]))
        if row["zh"] is None:
            missing_zh.append(row["name"])
        n = norm(row["name"])
        row["id"] = i
        row["unsampled"] = n.startswith("unsampled")
        row["code"] = None if row["unsampled"] else (NAME_OVERRIDES.get(n) or names.get(n))
        if row["code"] is None and not row["unsampled"]:
            missing_code.append(row["name"])
        # group = level-1 ancestor; core = not under food / energy
        a, chain = i, []
        while a is not None:
            chain.append(norm(rows[a]["name"]))
            a = rows[a]["parent"]
        top = rows[i] if rows[i]["level"] == 1 else None
        a = i
        while rows[a]["level"] > 1:
            a = rows[a]["parent"]
        top = rows[a]["name"]
        row["group"] = next((g for g, (en, _, _) in enumerate(GROUPS) if norm(en) == norm(top)), None)
        row["core"] = not any(c in NON_CORE_ROOTS for c in chain)
    if missing_code:
        print(f"   ! no BLS item code for: {', '.join(missing_code)}")
    if missing_zh:
        print(f"   ! no Chinese name in detail/item_names_zh.csv for: {', '.join(missing_zh)}")

    # December anchors per item (current name first, then earlier names)
    def anchor(row, dec_year: int) -> Optional[float]:
        t = official.get(dec_year)
        if t is None:
            return None
        n = norm(row["name"])
        for key in [n] + ALIASES.get(n, []):
            if key in t:
                return t[key]
        return None

    # Index behind each row. Items BLS does not publish (unsampled items, and the odd item with
    # no U.S. index) are proxied by the nearest ancestor that has one; BLS imputes such items
    # from the movement of the level above, so this mirrors how they enter the headline.
    # Rows flagged proxy=True are estimates.
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

    # Some small items are not published every month. For the weights only, a missing month
    # is filled by moving the item with its parent (BLS imputes non-response the same way);
    # the item's own price change and contribution stay empty for that month.
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
        for m in hist:
            if m in raw:
                f[m], last_m = raw[m], m
            elif last_m is not None and m in pser and last_m in pser:
                f[m] = f[last_m] * pser[m] / pser[last_m]
                last_m = m
        fill[i] = f

    def ri(row, m: str) -> Optional[float]:
        """Relative importance (% of all items) of row in month m."""
        y = int(m[:4])
        if m.endswith("-12") and (y in official):
            return anchor(row, y)                       # new basis
        a_m = f"{y - 1}-12"
        base = anchor(row, y - 1)
        if base is None or row["id"] not in fill:
            return None
        u = fill[row["id"]]
        if a_m not in u or m not in u or a_m not in all_u or m not in all_u:
            return None
        return base * (u[m] / u[a_m]) / (all_u[m] / all_u[a_m])

    print("→ relative importance, price changes, contributions")
    avail = [m for m in hist if m in all_u]
    prev = {m: avail[i - 1] for i, m in enumerate(avail) if i > 0}
    out_items, series = [], {}
    for row in rows:
        code = rows[src[row["id"]]]["code"] if row["id"] in src else None
        u = U.get(code, {}) if code else {}
        s = S.get(code, {}) if code else {}
        w = {m: ri(row, m) for m in ri_months}

        def mom_chg(m):
            p = prev.get(m)
            if p is None:
                return None
            src = s if (p in s and m in s) else u
            return (src[m] / src[p] - 1) * 100 if (p in src and m in src) else None

        def yoy_chg(m):
            b = minus12(m)
            return (u[m] / u[b] - 1) * 100 if (b in u and m in u) else None

        chg_m = [mom_chg(m) for m in hist]
        chg_y = [yoy_chg(m) for m in hist]
        cm, cy, wt = [], [], []
        for m in sel:
            p = prev.get(m)
            wp = w.get(p) if p else None
            k = hist.index(m)
            cm.append(None if wp is None or chg_m[k] is None else wp * chg_m[k] / 100)
            wb = w.get(minus12(m))
            cy.append(None if wb is None or chg_y[k] is None else wb * chg_y[k] / 100)
            wt.append(w.get(m))
        series[row["id"]] = {"w": [r3(x, 3) for x in wt], "cm": [r3(x) for x in cm], "cy": [r3(x) for x in cy],
                             "gm": [r3(x, 3) for x in chg_m], "gy": [r3(x, 3) for x in chg_y]}
        out_items.append({k: row[k] for k in ("id", "name", "zh", "level", "parent", "leaf", "group", "core",
                                               "code", "sa", "unsampled", "proxy")})

    # headline and the unexplained remainder at the lowest level
    head_m = [((S["SA0"][m] / S["SA0"][prev[m]] - 1) * 100) if m in prev and m in S["SA0"] and prev[m] in S["SA0"]
              else None for m in sel]
    head_y = [((all_u[m] / all_u[minus12(m)] - 1) * 100) if m in all_u and minus12(m) in all_u else None
              for m in sel]
    core_u, core_s = U["SA0L1E"], S["SA0L1E"]
    core_m = [((core_s[m] / core_s[prev[m]] - 1) * 100) if m in prev and m in core_s and prev[m] in core_s
              else None for m in sel]
    core_y = [((core_u[m] / core_u[minus12(m)] - 1) * 100) if m in core_u and minus12(m) in core_u else None
              for m in sel]
    leaves = [r["id"] for r in rows if r["leaf"]]

    def remainder(head, key):
        out = []
        for k in range(len(sel)):
            if head[k] is None:
                out.append(None)
                continue
            out.append(r3(head[k] - sum(series[i][key][k] or 0.0 for i in leaves)))
        return out

    payload = {
        "meta": {
            "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "U.S. Bureau of Labor Statistics, CPI-U (download.bls.gov bulk files); "
                      "December relative importance tables",
            "latest": last,
            "months": sel,
            "history": hist,
            "max_level": args.max_level,
            "baseline_years": 10,
            "groups": [{"id": g, "en": en, "zh": zh, "color": col} for g, (en, zh, col) in enumerate(GROUPS)],
            "unpublished_months": [m for m in hist if m not in all_u],
            "notes": {"mom": "seasonally adjusted where BLS publishes a seasonally adjusted index, "
                             "otherwise not seasonally adjusted (items with sa=false)",
                      "yoy": "not seasonally adjusted"},
        },
        "headline": {"mom": [r3(x, 3) for x in head_m], "yoy": [r3(x, 3) for x in head_y],
                     "core_mom": [r3(x, 3) for x in core_m], "core_yoy": [r3(x, 3) for x in core_y],
                     "remainder_mom": remainder(head_m, "cm"), "remainder_yoy": remainder(head_y, "cy")},
        "items": out_items,
        "series": series,
    }
    return payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Item-level US CPI contributions (web/detail.html data)")
    ap.add_argument("--max-level", type=int, default=5)
    ap.add_argument("--years", type=int, default=10, help="selectable months: the last N years")
    ap.add_argument("--history-start", default="2006-01",
                    help="first month of price-change history (the anomaly baseline needs "
                         "10 years before the first selectable month)")
    ap.add_argument("--cache", default=os.path.join(ROOT, "cache", "bls_flat"))
    ap.add_argument("--out", default=os.path.join(ROOT, "web", "data", "detail.json"))
    ap.add_argument("--user-agent", default=os.environ.get("BLS_USER_AGENT") or DEFAULT_UA)
    args = ap.parse_args(argv)

    payload = build(args)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    m = payload["meta"]
    print(f"\n✓ wrote {args.out} ({os.path.getsize(args.out) / 1e6:.2f} MB): {len(payload['items'])} items, "
          f"months {m['months'][0]}..{m['latest']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
