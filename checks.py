#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sanity checks run before anything is published (exit 1 = do not deploy).

  python checks.py                          check web/data/cpi_data.json
  python checks.py --previous old.json      also make sure no history was lost

Checks
  1. weights of every breakdown add up to ~100 in every month
  2. exact mode: contributions add up to the headline print
  3. residual of the standard decomposition stays small (a parse or weight error shows up
     here first as whole percentage points)
  4. the latest year of contributions has no gaps
  5. benchmark against Bloomberg WMA, March 2026 YoY (NSA data is never revised)
  6. recovered vs official December weights agree after 2007 (catches a mis-parsed table)
  7. the new file has at least the months of the previous one
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Bloomberg WMA "Contributions to US CPI YoY% NSA", March 2026 (see README / methodology)
BENCH_MONTH = "2026-03"
BENCH_YOY = {"food": 0.366, "energy": 0.791, "core_goods": 0.229, "core_services": 1.848}
BENCH_W = {"food": 13.681, "energy": 6.312, "core_goods": 19.367, "core_services": 60.640}
BENCH_HEADLINE = 3.256


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(HERE, "web", "data", "cpi_data.json"))
    ap.add_argument("--compare", default=os.path.join(HERE, "web", "data", "ri_official_vs_estimated.csv"))
    ap.add_argument("--previous", default=None, help="the data file before this run")
    args = ap.parse_args(argv)

    with open(args.data, encoding="utf-8") as f:
        D = json.load(f)
    fails, notes = [], []

    def check(ok: bool, msg: str):
        (notes if ok else fails).append(("ok  " if ok else "FAIL") + "  " + msg)

    for bk, B in D["breakdowns"].items():
        ids = [c["id"] for c in B["components"]]
        for tf in ("mom", "yoy"):
            s, x = B[tf], B[tf + "_exact"]
            # 1. weights (before 2008 the 1-decimal indexes make the in-year price update drift a bit)
            for since, lim in (("2008-01", 0.05), ("0000", 0.10)):
                worst = 0.0
                for k, d in enumerate(s["dates"]):
                    w = [s["weights"][i][k] for i in ids]
                    if d >= since and None not in w:
                        worst = max(worst, abs(sum(w) - 100))
                check(worst < lim, f"{bk}/{tf}: weights sum to 100 since {since[:4] if since != '0000' else 'start'}"
                                   f" (worst gap {worst:.4f} < {lim})")
            # 2. exact mode adds up
            gap = max(abs(sum(x["contrib"][i][k] for i in ids) - x["headline"][k])
                      for k in range(len(x["dates"])))
            check(gap < 1e-4, f"{bk}/{tf}_exact: contributions add to headline (max gap {gap:.2e})")
            # 3. residual of the standard decomposition, since 2007 (3-decimal indexes)
            rs = [abs(r) for d, r in zip(s["dates"], s["residual"]) if d >= "2007-01"]
            # a real error (mis-parsed table, wrong series) shows up as whole percentage points;
            # the largest legitimate values are ~0.16 (YoY, Apr-2020) and ~0.12 (MoM SA)
            lim = 0.25 if tf == "yoy" else 0.15
            check(max(rs) < lim, f"{bk}/{tf}: |residual| since 2007 < {lim} (max {max(rs):.3f})")
            # 4. no holes in the last 12 prints
            tail = [s["contrib"][i][k] for i in ids for k in range(max(0, len(s["dates"]) - 12), len(s["dates"]))]
            check(None not in tail, f"{bk}/{tf}: last 12 months complete")

    # 5. benchmark
    y = D["breakdowns"]["basic4"]["yoy"]
    if BENCH_MONTH in y["dates"]:
        k = y["dates"].index(BENCH_MONTH)
        check(abs(y["headline"][k] - BENCH_HEADLINE) < 0.0015,
              f"benchmark {BENCH_MONTH}: headline {y['headline'][k]:.3f} vs {BENCH_HEADLINE}")
        for c, v in BENCH_YOY.items():
            check(abs(y["contrib"][c][k] - v) < 0.0025, f"benchmark {BENCH_MONTH}: {c} {y['contrib'][c][k]:.3f} vs {v}")
        for c, v in BENCH_W.items():
            check(abs(y["weights"][c][k] - v) < 0.0025, f"benchmark {BENCH_MONTH}: weight {c} {y['weights'][c][k]:.3f} vs {v}")
    else:
        fails.append(f"FAIL  benchmark month {BENCH_MONTH} missing")

    # 6. recovered vs official weights (only present when the official tables were used)
    if os.path.exists(args.compare):
        worst = 0.0
        with open(args.compare, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (r["basis"] == "new" and int(r["dec_year"]) >= 2007 and r["est_minus_official"]
                        and r["node"] in BENCH_W):
                    worst = max(worst, abs(float(r["est_minus_official"])))
        check(worst < 0.5, f"recovered vs official December weights since 2007: max |Δ| {worst:.3f}pp")

    # 7. no lost history
    if args.previous and os.path.exists(args.previous):
        with open(args.previous, encoding="utf-8") as f:
            P = json.load(f)
        old = set(P["breakdowns"]["basic4"]["yoy"]["dates"])
        new = set(y["dates"])
        check(old <= new, f"history kept ({len(old)} → {len(new)} YoY months)")

    for n in notes + fails:
        print(n)
    print(f"\n{len(notes)} passed, {len(fails)} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
