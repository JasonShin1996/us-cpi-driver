#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Item-level contributions to US CPI  (data for web/detail.html)
==============================================================

Goes down the BLS expenditure hierarchy to --max-level (default 5; ~240 items, ~166 of them
the lowest level) and, for every month, computes each item's relative importance, its own
price change and its contribution to headline MoM (SA) and YoY (NSA).

    python detail/cpi_detail.py            # -> web/data/detail.json

Pipeline (one module per step):
  bls_flat.py        download and read the BLS bulk files (indexes, item codes)
  hierarchy.py       items and how they nest (newest December Table 1), codes, names, groups
  weights.py         relative importance per item and month (December anchors, price update)
  contributions.py   price changes, contributions, remainder, the output JSON

Method: web/methodology.html.  Data traps and how they are handled: detail/PITFALLS.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import bls_flat
import contributions
import hierarchy
import weights
from common import DEFAULT_UA, ROOT, minus12, month_list


def build(args) -> dict:
    print("→ BLS bulk files")
    bls_flat.fetch(args.cache, args.user_agent)
    codes = bls_flat.load_items(args.cache)
    idx = bls_flat.load_indexes(args.cache, int(args.history_start[:4]) - 1)
    U, S = idx["U"], idx["S"]
    all_u = U["SA0"]

    # months: price-change history, selectable months (last --years years), months needing weights
    last = max(all_u)
    hist = month_list(args.history_start, last)
    sel_first = f"{int(last[:4]) - args.years}-{last[5:]}"
    sel = [m for m in hist if m > sel_first]
    ri_months = month_list(minus12(sel[0]), last)

    print("→ hierarchy and codes")
    rows = hierarchy.load_hierarchy(os.path.join(ROOT, "ri_official"), args.max_level)
    zh = hierarchy.load_zh_names(os.path.join(ROOT, "detail", "item_names_zh.csv"))
    missing_code, missing_zh = hierarchy.annotate(rows, codes, zh)
    if missing_code:
        print(f"   ! no BLS item code for: {', '.join(missing_code)}")
    if missing_zh:
        print(f"   ! no Chinese name in detail/item_names_zh.csv for: {', '.join(missing_zh)}")

    print("→ relative importance")
    official = weights.load_official(os.path.join(ROOT, "ri_official_full.csv"), int(ri_months[0][:4]) - 1)
    src = weights.index_sources(rows, U, S)
    fill = weights.filled_indexes(rows, src, U, all_u, hist)
    w = weights.Weights(official, fill, all_u)

    print("→ price changes and contributions")
    prev = contributions.previous_published(hist, all_u)
    series = {}
    for row in rows:
        code = rows[src[row["id"]]]["code"] if row["id"] in src else None
        series[row["id"]] = contributions.item_series(row, code, U, S, w, hist, sel, ri_months, prev)
    head = contributions.headline(U, S, sel, prev)
    return contributions.payload(rows, series, head, last=last, sel=sel, hist=hist,
                                 unpublished=[m for m in hist if m not in all_u], max_level=args.max_level)


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
