#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Official BLS "Relative importance of components" (December tables) -> tidy CSV
==============================================================================

BLS publishes one Table 1 per December.  Two flavours exist:

  basis = "new"   YYYY.htm/.xlsx/.txt          December YYYY on the weights used in YYYY+1.
                                               This is the anchor that cpi_contrib.py solves
                                               for weight-year YYYY+1.
  basis = "old"   old-weights-YYYY.htm/.txt    December YYYY still on the weights used in
                                               YYYY (only published in weight-update years).

Sources (all under --dir, default ./ri_official):
  1987-1999  ri-archive-*/…/YYYY.txt       fixed width, old-style item codes, UPPER CASE
  2000-2019  ri-archive-*/…/YYYY.txt       dotted leaders, no codes
             ri-archive-*/…/old-weights-YYYY.txt
  2020-      YYYY.xlsx                     sheet "Table 1", has an indent column
  2021-      html_old-weights-YYYY.htm     old-weight tables are HTML only
  (PDF-only old-weight tables for 2009-2019 and the 1947-1986 workbook are not parsed.)

Item codes changed across the years (e.g. Food = SA11 in 1987, SAF1 today), so rows are
matched by *name*, which BLS has kept stable.

Outputs
  ri_official.csv        dec_year, basis, node, item, cpi_u, source   (nodes of cpi_contrib.py)
  ri_official_full.csv   every row of every table (cpi_u and cpi_w)

Usage
  python ri_official.py --download        # (re)fetch everything from bls.gov, then parse
  python ri_official.py                   # parse what is already on disk
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import re
import sys
import time
import urllib.request
import zipfile
from typing import Dict, List, Optional, Tuple

BLS = "https://www.bls.gov/cpi/tables/relative-importance/"
# bls.gov rejects browser-like user agents from scripts; it wants a contact string.
DEFAULT_UA = "cpi-contrib research@example.com"

# node id (same ids as cpi_contrib.NODES) -> accepted item names (lower case, normalised)
NODE_NAMES: Dict[str, List[str]] = {
    "headline":      ["all items"],
    "core":          ["all items less food and energy"],
    "food":          ["food"],
    "energy":        ["energy"],
    "core_goods":    ["commodities less food and energy commodities",
                      "commodities less food and energy"],
    "core_services": ["services less energy services", "services less energy"],
    "commodities":   ["commodities"],
    "services":      ["services"],
    "food_home":     ["food at home"],
    "food_away":     ["food away from home"],
    "energy_cmdty":  ["energy commodities"],
    "energy_svc":    ["energy services"],
    "rent_shelter":  ["rent of shelter"],
    "svc_less_ros":  ["services less rent of shelter"],
}

Row = Tuple[str, Optional[str], Optional[float], Optional[float]]   # item, indent, cpi_u, cpi_w


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------
def _get(url: str, path: str, ua: str) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
    except Exception as e:                      # noqa: BLE001
        print(f"   ! {url}: {e}")
        return False
    if data[:200].lstrip().lower().startswith(b"<!doctype html") and path.endswith((".xlsx", ".zip")):
        print(f"   ! {url}: got an HTML page (blocked?)")
        return False
    with open(path, "wb") as f:
        f.write(data)
    print(f"   {len(data):>9,d}  {os.path.basename(path)}")
    return True


def download(root: str, ua: str, last_year: int) -> None:
    os.makedirs(root, exist_ok=True)
    jobs = [(f"{BLS}ri-archive-{a}.zip", f"ri-archive-{a}.zip")
            for a in ("1987-1989", "1990-1999", "2000-2009", "2010-2019")]
    jobs += [(f"{BLS}historical-relative-importance-1947-1986.xlsx",
              "historical-relative-importance-1947-1986.xlsx"),
             ("https://www.bls.gov/web/cpi/cpi-u-historical-cost-weights.xlsx",
              "cpi-u-historical-cost-weights.xlsx")]
    for y in range(2020, last_year + 1):
        jobs.append((f"{BLS}{y}.xlsx", f"{y}.xlsx"))
        if y >= 2021:
            jobs.append((f"{BLS}old-weights-{y}.htm", f"html_old-weights-{y}.htm"))
    for url, fn in jobs:
        p = os.path.join(root, fn)
        if _get(url, p, ua) and fn.endswith(".zip"):
            with zipfile.ZipFile(p) as z:
                z.extractall(os.path.join(root, fn[:-4]))
        time.sleep(1.0)


# ---------------------------------------------------------------------------
# parsers
# ---------------------------------------------------------------------------
_NUM = r"-?\d*\.\d+|-?\d+"
_TXT_ROW = re.compile(rf"^(?P<name>.*?)[\s.]*\s(?P<u>{_NUM})\s+(?P<w>{_NUM})\s*$")
_TXT_CODE = re.compile(r"^(?P<code>S[A-Z0-9]+)\s+(?P<rest>.*)$")


def _f(x) -> Optional[float]:
    try:
        return float(str(x).strip())
    except (TypeError, ValueError):
        return None


def parse_txt(path: str) -> List[Row]:
    rows: List[Row] = []
    pending = ""                                   # name wrapped onto the next line
    with open(path, encoding="latin-1") as f:
        lines = f.read().splitlines()
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            pending = ""
            continue
        m_code = _TXT_CODE.match(line)             # 1987-1999: "SAC      COMMODITIES   45.531  49.440"
        body = m_code.group("rest") if m_code else line
        m = _TXT_ROW.match(body)
        if m:
            name = m.group("name").rstrip(" .")
            indent = len(name) - len(name.lstrip())
            name = (pending + " " + name.strip()).strip() if pending else name.strip()
            pending = ""
            if name:
                rows.append((name, str(indent), _f(m.group("u")), _f(m.group("w"))))
        else:
            # a long item name wrapped without numbers; keep it for the next line
            txt = body.strip().rstrip(" .")
            pending = txt if (txt and not txt.startswith(("(", "Table", "Item", "U.S."))) else ""
    return rows


def parse_xlsx(path: str) -> List[Row]:
    import openpyxl                                # only needed for 2020+ files
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Table 1"] if "Table 1" in wb.sheetnames else wb[wb.sheetnames[0]]
    rows: List[Row] = []
    for r in ws.iter_rows(values_only=True):
        if len(r) < 3 or r[1] is None:
            continue
        u = _f(r[2])
        if u is None:
            continue
        rows.append((str(r[1]).strip(), None if r[0] is None else str(r[0]), u,
                     _f(r[3]) if len(r) > 3 else None))
    return rows


def parse_html(path: str) -> List[Row]:
    with open(path, encoding="utf-8", errors="ignore") as f:
        html = f.read()
    rows: List[Row] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I):
        cells = re.findall(r"<t[hd]([^>]*)>(.*?)</t[hd]>", tr, flags=re.S | re.I)
        if len(cells) < 2:
            continue
        txt = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c[1])).strip() for c in cells]
        u = _f(txt[1].replace(",", ""))
        if u is None:
            continue
        m = re.search(r"sub(\d+)", cells[0][0])
        rows.append((txt[0], m.group(1) if m else None, u, _f(txt[2]) if len(txt) > 2 else None))
    return rows


def _title(path: str) -> str:
    """Pull '(2024 Weights)' or similar from the file header, if present."""
    try:
        if path.endswith(".xlsx"):
            import openpyxl
            ws = openpyxl.load_workbook(path, read_only=True)["Table 1"]
            head = " ".join(str(c) for r in ws.iter_rows(max_row=3, values_only=True) for c in r if c)
        else:
            with open(path, encoding="latin-1") as f:
                head = f.read(4000)
    except Exception:                              # noqa: BLE001
        return ""
    m = re.search(r"\(([^()]*[Ww]eights)\)", head)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------
def discover(root: str) -> List[Tuple[int, str, str]]:
    """[(dec_year, basis, path)] with one source per (year, basis); xlsx > htm > txt."""
    found: Dict[Tuple[int, str], str] = {}
    rank = {".xlsx": 0, ".htm": 1, ".txt": 2}
    pats = [os.path.join(root, "*.xlsx"), os.path.join(root, "html_*.htm"),
            os.path.join(root, "ri-archive-*", "**", "*.txt")]
    for pat in pats:
        for p in glob.glob(pat, recursive=True):
            fn = os.path.basename(p)
            m = re.fullmatch(r"(?:html_)?(old-weights-)?(\d{4})\.(xlsx|htm|txt)", fn)
            if not m:
                continue
            key = (int(m.group(2)), "old" if m.group(1) else "new")
            ext = "." + m.group(3)
            if key not in found or rank[ext] < rank[os.path.splitext(found[key])[1]]:
                found[key] = p
    return sorted((y, b, p) for (y, b), p in found.items())


def _norm(s: str) -> str:
    s = s.lower().replace("&", "and")
    s = re.sub(r"\(\d+\)|\s*\d+$", "", s)          # footnote markers like "(1)" or trailing "1"
    return re.sub(r"[^a-z]+", " ", s).strip()


def pick_nodes(rows: List[Row]) -> Tuple[Dict[str, Tuple[str, float]], List[str]]:
    """First matching row per node; warn when a later duplicate disagrees."""
    out: Dict[str, Tuple[str, float]] = {}
    warn: List[str] = []
    lookup = {n: node for node, names in NODE_NAMES.items() for n in names}
    for item, _, u, _ in rows:
        node = lookup.get(_norm(item))
        if node is None or u is None:
            continue
        if node not in out:
            out[node] = (item, u)
        elif abs(out[node][1] - u) > 1e-9:
            warn.append(f"{node}: '{out[node][0]}'={out[node][1]} vs '{item}'={u}")
    # Tables before 2010 have no "Energy services" row; it is energy less energy commodities.
    if "energy_svc" not in out and "energy" in out and "energy_cmdty" in out:
        out["energy_svc"] = ("(derived) Energy - Energy commodities",
                             round(out["energy"][1] - out["energy_cmdty"][1], 3))
    return out, warn


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Parse official BLS CPI relative-importance tables")
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--dir", default=os.path.join(here, "ri_official"), help="where the BLS files live")
    ap.add_argument("--out", default=os.path.join(here, "ri_official.csv"))
    ap.add_argument("--full-out", default=os.path.join(here, "ri_official_full.csv"))
    ap.add_argument("--download", action="store_true", help="fetch the files from bls.gov first")
    ap.add_argument("--if-missing", action="store_true",
                    help="with --download: only download when the table for --last-year is not "
                         "in --out yet (cheap to call from a scheduled job)")
    ap.add_argument("--user-agent", default=os.environ.get("BLS_USER_AGENT") or DEFAULT_UA,
                    help="contact string sent to bls.gov (they block browser-like UAs)")
    ap.add_argument("--last-year", type=int, default=time.localtime().tm_year - 1)
    args = ap.parse_args(argv)

    if args.download and args.if_missing and os.path.exists(args.out):
        with open(args.out, newline="", encoding="utf-8") as f:
            have = {(int(r["dec_year"]), r["basis"]) for r in csv.DictReader(f)}
        if (args.last_year, "new") in have:
            print(f"December {args.last_year} table already in {args.out}; nothing to download")
            return 0
    if args.download:
        print(f"→ downloading into {args.dir}")
        download(args.dir, args.user_agent, args.last_year)

    sources = discover(args.dir)
    if not sources:
        print(f"no tables found under {args.dir} (try --download)")
        return 1

    parsers = {".xlsx": parse_xlsx, ".htm": parse_html, ".txt": parse_txt}
    key_rows, full_rows = [], []
    print(f"→ parsing {len(sources)} tables")
    print(f"   {'year':<5}{'basis':<6}{'rows':>5}  {'nodes':>5}  source")
    for y, basis, p in sources:
        rows = parsers[os.path.splitext(p)[1]](p)
        nodes, warn = pick_nodes(rows)
        label = _title(p)
        src = os.path.relpath(p, args.dir)
        for item, indent, u, w in rows:
            full_rows.append([y, basis, label, indent if indent is not None else "", item,
                              "" if u is None else u, "" if w is None else w, src])
        for node in NODE_NAMES:
            if node in nodes:
                key_rows.append([y, basis, label, node, nodes[node][0], nodes[node][1], src])
        missing = [n for n in NODE_NAMES if n not in nodes]
        print(f"   {y:<5}{basis:<6}{len(rows):>5}  {len(nodes):>2}/{len(NODE_NAMES)}  {src}"
              + (f"   missing: {','.join(missing)}" if missing else ""))
        for w in warn:
            print(f"        ! {w}")

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["dec_year", "basis", "weights", "node", "item", "cpi_u", "source"])
        wr.writerows(key_rows)
    with open(args.full_out, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["dec_year", "basis", "weights", "indent", "item", "cpi_u", "cpi_w", "source"])
        wr.writerows(full_rows)
    print(f"\n✓ wrote {args.out} ({len(key_rows)} rows) and {args.full_out} ({len(full_rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
