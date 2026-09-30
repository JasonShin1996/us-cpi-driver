# -*- coding: utf-8 -*-
"""
Price changes and contributions per item, and the JSON for web/detail.html
=========================================================================

  MoM contribution:  RI_i(p) * (I_i(t)/I_i(p) - 1),  p = previous published month;
                     seasonally adjusted index when BLS publishes one, otherwise NSA
  YoY contribution:  RI_i(t-12) * (I_i(t)/I_i(t-12) - 1),  NSA
  remainder:         headline change - sum of the lowest-level items' contributions
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from common import minus12, r3
from hierarchy import GROUPS
from weights import Weights

ITEM_FIELDS = ("id", "name", "zh", "level", "parent", "leaf", "group", "core", "code", "sa", "unsampled", "proxy")


def previous_published(hist: List[str], all_u: Dict[str, float]) -> Dict[str, str]:
    """month -> the published month before it (skips e.g. the unpublished 2025-10)."""
    avail = [m for m in hist if m in all_u]
    return {m: avail[i - 1] for i, m in enumerate(avail) if i > 0}


def item_series(row: dict, code: Optional[str], U: Dict[str, dict], S: Dict[str, dict],
                weights: Weights, hist: List[str], sel: List[str], ri_months: List[str],
                prev: Dict[str, str]) -> dict:
    """{w, cm, cy} aligned to sel, {gm, gy} aligned to hist, for one row."""
    u = U.get(code, {}) if code else {}
    s = S.get(code, {}) if code else {}
    w = {m: weights.ri(row, m) for m in ri_months}

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
    pos = {m: k for k, m in enumerate(hist)}
    cm, cy, wt = [], [], []
    for m in sel:
        p = prev.get(m)
        wp = w.get(p) if p else None
        k = pos[m]
        cm.append(None if wp is None or chg_m[k] is None else wp * chg_m[k] / 100)
        wb = w.get(minus12(m))
        cy.append(None if wb is None or chg_y[k] is None else wb * chg_y[k] / 100)
        wt.append(w.get(m))
    return {"w": [r3(x, 3) for x in wt], "cm": [r3(x) for x in cm], "cy": [r3(x) for x in cy],
            "gm": [r3(x, 3) for x in chg_m], "gy": [r3(x, 3) for x in chg_y]}


def headline(U: Dict[str, dict], S: Dict[str, dict], sel: List[str], prev: Dict[str, str]) -> Dict[str, list]:
    """Headline and core CPI changes (%) aligned to sel: MoM SA, YoY NSA."""
    def mom(ser):
        return [((ser[m] / ser[prev[m]] - 1) * 100) if m in prev and m in ser and prev[m] in ser else None
                for m in sel]

    def yoy(ser):
        return [((ser[m] / ser[minus12(m)] - 1) * 100) if m in ser and minus12(m) in ser else None for m in sel]

    return {"mom": mom(S["SA0"]), "yoy": yoy(U["SA0"]), "core_mom": mom(S["SA0L1E"]), "core_yoy": yoy(U["SA0L1E"])}


def remainder(head: List[Optional[float]], series: Dict[int, dict], leaves: List[int], key: str) -> list:
    out = []
    for k in range(len(head)):
        if head[k] is None:
            out.append(None)
            continue
        out.append(r3(head[k] - sum(series[i][key][k] or 0.0 for i in leaves)))
    return out


def payload(rows: List[dict], series: Dict[int, dict], head: Dict[str, list], *, last: str,
            sel: List[str], hist: List[str], unpublished: List[str], max_level: int) -> dict:
    leaves = [r["id"] for r in rows if r["leaf"]]
    return {
        "meta": {
            "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "U.S. Bureau of Labor Statistics, CPI-U (download.bls.gov bulk files); "
                      "December relative importance tables",
            "latest": last,
            "months": sel,
            "history": hist,
            "max_level": max_level,
            "baseline_years": 10,
            "groups": [{"id": g, "en": en, "zh": zh, "color": col} for g, (en, zh, col) in enumerate(GROUPS)],
            "unpublished_months": unpublished,
            "notes": {"mom": "seasonally adjusted where BLS publishes a seasonally adjusted index, "
                             "otherwise not seasonally adjusted (items with sa=false)",
                      "yoy": "not seasonally adjusted"},
        },
        "headline": {"mom": [r3(x, 3) for x in head["mom"]], "yoy": [r3(x, 3) for x in head["yoy"]],
                     "core_mom": [r3(x, 3) for x in head["core_mom"]], "core_yoy": [r3(x, 3) for x in head["core_yoy"]],
                     "remainder_mom": remainder(head["mom"], series, leaves, "cm"),
                     "remainder_yoy": remainder(head["yoy"], series, leaves, "cy")},
        "items": [{k: r[k] for k in ITEM_FIELDS} for r in rows],
        "series": series,
    }
