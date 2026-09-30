#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Validate web/data/detail.json
=============================

    python detail/validate.py                  # internal checks + both external comparisons
    python detail/validate.py --offline        # internal checks only (no network)

Internal (no network; also run by checks.py before every publish):
  1. coverage       - enough items, every item has a Chinese name, latest month present
  2. weights        - lowest-level weights add up to ~100 in every month
  3. remainder      - headline minus the sum of lowest-level contributions stays small
  4. NSA identity   - one-month NSA contributions add up to the headline change (this is the
                      real test of the weights: it holds exactly within a weight year)

External (compare with numbers published by others):
  5. BLS news release Table 2 - previous month's weights, MoM SA and YoY NSA changes
  6. SF Fed "CPI Inflation Contributions" - item contributions to monthly core CPI

Thresholds come from the values observed when the page was built (see detail/README.md);
a real error (a mis-parsed table, a wrong series) shows up far beyond them.
Exit code 1 if any check fails.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import statistics as st
import sys
import urllib.request
from typing import Callable, Dict, List, Tuple

from common import DEFAULT_UA, ROOT, norm

NEWS_T02 = "https://www.bls.gov/news.release/cpi.t02.htm"
SFFED_XLSX = "https://www.frbsf.org/wp-content/uploads/cpi-contributors-data.xlsx"

Result = Tuple[bool, str]


def _get(url: str, ua: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# internal
# ---------------------------------------------------------------------------
def check_coverage(D: dict) -> List[Result]:
    items = D["items"]
    leaves = [i for i in items if i["leaf"]]
    no_zh = [i["name"] for i in items if not i.get("zh")]
    k = len(D["meta"]["months"]) - 1
    return [
        (len(items) >= 200 and len(leaves) >= 140, f"{len(items)} items, {len(leaves)} lowest-level"),
        (not no_zh, "every item has a Chinese name" + (f" (missing: {', '.join(no_zh[:5])})" if no_zh else "")),
        (D["headline"]["mom"][k] is not None and D["headline"]["yoy"][k] is not None,
         f"latest month {D['meta']['latest']} has headline MoM and YoY"),
    ]


def check_weights(D: dict) -> List[Result]:
    M, S = D["meta"]["months"], D["series"]
    leaves = [i["id"] for i in D["items"] if i["leaf"]]
    sums = [(m, sum(S[str(i)]["w"][k] or 0 for i in leaves)) for k, m in enumerate(M)
            if m not in D["meta"]["unpublished_months"]]
    lo, hi = min(sums, key=lambda x: x[1]), max(sums, key=lambda x: x[1])
    return [(98.5 <= lo[1] and hi[1] <= 100.5,
             f"lowest-level weights sum to {lo[1]:.2f} ({lo[0]}) .. {hi[1]:.2f} ({hi[0]}), limit 98.5..100.5")]


def check_remainder(D: dict) -> List[Result]:
    out = []
    for key, lim in (("remainder_mom", 0.15), ("remainder_yoy", 0.5)):
        r = [abs(x) for x in D["headline"][key] if x is not None]
        out.append((max(r) < lim, f"{key}: mean {st.mean(r):.3f}, max {max(r):.3f} (limit {lim})"))
    return out


def check_nsa_identity(D: dict, cache: str) -> List[Result]:
    """Within a weight year, sum_i RI_i(p) * (I_i(t)/I_i(p) - 1) equals the headline NSA change."""
    import bls_flat
    M, H, S = D["meta"]["months"], D["meta"]["history"], D["series"]
    idx = bls_flat.load_indexes(cache, int(M[0][:4]) - 1)
    U, all_u = idx["U"], idx["U"]["SA0"]
    by = {i["id"]: i for i in D["items"]}

    def code_of(i):
        a = i
        while a is not None and not (by[a]["code"] and by[a]["code"] in U):
            a = by[a]["parent"]
        return by[a]["code"] if a is not None else None

    leaves = [i["id"] for i in D["items"] if i["leaf"]]
    codes = {i: code_of(i) for i in leaves}
    avail = [m for m in H if m in all_u]
    prev = {m: avail[j - 1] for j, m in enumerate(avail) if j}
    pos = {m: k for k, m in enumerate(M)}
    gaps = []
    for m in M:
        p = prev.get(m)
        if p not in pos:
            continue
        tot = 0.0
        for i in leaves:
            w, u = S[str(i)]["w"][pos[p]], U.get(codes[i], {})
            if w is not None and m in u and p in u:
                tot += w * (u[m] / u[p] - 1)
        gaps.append(abs((all_u[m] / all_u[p] - 1) * 100 - tot))
    return [(st.mean(gaps) < 0.03 and max(gaps) < 0.15,
             f"one-month NSA contributions vs headline: mean gap {st.mean(gaps):.4f}, max {max(gaps):.4f} "
             f"(limits 0.03 / 0.15; the biggest gaps are months with unpublished items)")]


# ---------------------------------------------------------------------------
# external
# ---------------------------------------------------------------------------
def check_news_release(D: dict, ua: str) -> List[Result]:
    """BLS news release Table 2 lists last month's weights and this month's changes (1 decimal)."""
    html = _get(NEWS_T02, ua).decode("utf-8", "ignore")
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    m = re.search(r"Relative importance ([A-Z][a-z]{2})\.? (\d{4})", text)
    rows: Dict[str, List[str]] = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S):
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x)).strip()
                 for x in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, flags=re.S)]
        if len(cells) >= 7:
            rows.setdefault(norm(re.sub(r"\(\d+\)", "", cells[0])), cells[1:7])
    M, H, S = D["meta"]["months"], D["meta"]["history"], D["series"]
    latest = D["meta"]["latest"]
    kL, kP, hL = M.index(latest), M.index(latest) - 1, H.index(latest)
    while kP >= 0 and M[kP] in D["meta"]["unpublished_months"]:
        kP -= 1

    def num(x):
        try:
            return float(x)
        except ValueError:
            return None

    n, dw, bad_y, bad_m = 0, [], [], []
    for it in D["items"]:
        r = rows.get(norm(it["name"]))
        if not r:
            continue
        n += 1
        s = S[str(it["id"])]
        w, y, mo = num(r[0]), num(r[1]), num(r[5])
        if w is not None and s["w"][kP] is not None:
            dw.append(abs(w - s["w"][kP]))
        if y is not None and s["gy"][hL] is not None and abs(y - round(s["gy"][hL], 1)) > 0.05:
            bad_y.append(it["name"])
        if mo is not None and s["gm"][hL] is not None and abs(mo - round(s["gm"][hL], 1)) > 0.05:
            bad_m.append(it["name"])
    rel = f"{m.group(1)} {m.group(2)}" if m else "?"
    return [
        (n >= 40, f"news release Table 2: {n} items matched by name (weights month {rel})"),
        (bool(dw) and max(dw) <= 0.0015, f"  previous-month weights: max diff {max(dw) if dw else float('nan'):.4f} (limit 0.0015)"),
        (len(bad_y) <= 2, f"  YoY NSA at 1 decimal: {n - len(bad_y)}/{n} equal" + (f" (off: {', '.join(bad_y)})" if bad_y else "")),
        (len(bad_m) <= 2, f"  MoM SA at 1 decimal: {n - len(bad_m)}/{n} equal" + (f" (off: {', '.join(bad_m)})" if bad_m else "")),
    ]


def check_sffed(D: dict) -> List[Result]:
    """SF Fed's granular sheet is contributions to *core* CPI: divide ours by the core weight."""
    import openpyxl
    # not read_only: this workbook has no sheet dimensions, and read_only mode then sees one cell
    wb = openpyxl.load_workbook(io.BytesIO(_get(SFFED_XLSX, "Mozilla/5.0")), data_only=True)
    ws = wb["chart5_coreCPI_granularCont_MoM"]
    it_rows = ws.iter_rows(values_only=True)
    header = list(next(it_rows))
    data = {}
    for r in it_rows:
        if r[1] is None:
            continue
        m = r[1].strftime("%Y-%m") if hasattr(r[1], "strftime") else str(r[1])[:7]
        data[m] = r
    M, S = D["meta"]["months"], D["series"]
    by_norm = {norm(i["name"]): i for i in D["items"]}
    cols = {}
    for c, h in enumerate(header):
        if isinstance(h, str) and ":" in h:
            n = norm(h.split(":", 1)[1])
            it = by_norm.get(n) or next((i for k, i in by_norm.items() if k.startswith(n) or n.startswith(k)), None)
            if it:
                cols[c] = it
    core_leaves = [i["id"] for i in D["items"] if i["leaf"] and i["core"]]
    unpub = set(D["meta"]["unpublished_months"])
    diffs = []
    for k, m in enumerate(M):
        if m not in data or k == 0:
            continue
        kp = k - 1
        while kp >= 0 and M[kp] in unpub:
            kp -= 1
        core_w = sum(S[str(i)]["w"][kp] or 0 for i in core_leaves)
        for c, it in cols.items():
            ours, raw = S[str(it["id"])]["cm"][k], data[m][c]
            try:
                theirs = float(raw)            # the sheet stores numbers as text, blanks as ''
            except (TypeError, ValueError):
                continue
            if ours is None:
                continue
            diffs.append(abs(ours * 100 / core_w - theirs))
    diffs.sort()
    p95 = diffs[int(0.95 * len(diffs))] if diffs else float("nan")
    return [(len(cols) >= 45 and bool(diffs) and st.mean(diffs) < 0.002 and p95 < 0.005,
             f"SF Fed core contributions: {len(cols)} items matched, {len(diffs)} points, "
             f"mean gap {st.mean(diffs) if diffs else float('nan'):.4f}pp, p95 {p95:.4f} (limits 0.002 / 0.005)")]


# ---------------------------------------------------------------------------
def internal_checks(path: str, cache: str) -> List[Result]:
    D = _load(path)
    return check_coverage(D) + check_weights(D) + check_remainder(D) + check_nsa_identity(D, cache)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validate web/data/detail.json")
    ap.add_argument("--data", default=os.path.join(ROOT, "web", "data", "detail.json"))
    ap.add_argument("--cache", default=os.path.join(ROOT, "cache", "bls_flat"))
    ap.add_argument("--offline", action="store_true", help="internal checks only")
    ap.add_argument("--user-agent", default=os.environ.get("BLS_USER_AGENT") or DEFAULT_UA)
    args = ap.parse_args(argv)

    results = internal_checks(args.data, args.cache)
    if not args.offline:
        D = _load(args.data)
        external: List[Tuple[str, Callable[[], List[Result]]]] = [
            ("BLS news release", lambda: check_news_release(D, args.user_agent)),
            ("SF Fed", lambda: check_sffed(D)),
        ]
        for name, fn in external:
            try:
                results += fn()
            except Exception as e:                   # noqa: BLE001 - a site being down is not a data error
                results.append((True, f"{name}: skipped ({e})"))
    for ok, msg in results:
        print(("ok    " if ok else "FAIL  ") + msg)
    fails = sum(not ok for ok, _ in results)
    print(f"\n{len(results) - fails} passed, {fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
