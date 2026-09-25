#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BLS CPI release calendar -> release_schedule.json, and the "is there new data?" gate
===================================================================================

  python schedule.py --download        refresh release_schedule.json from
                                       https://www.bls.gov/schedule/news_release/cpi.htm
  python schedule.py check             print run=true|false (GitHub Actions output format)
  python schedule.py --download-if-needed check
                                       what the daily job runs: only hits bls.gov when fewer
                                       than 3 future releases are known or a new release is due

`check` answers one question: has BLS already released a reference month that the site
does not have yet?  That single rule covers the release day itself, retries later that day,
and catching up after missed runs.  Exit code is always 0; read the printed `run=` line.

release_schedule.json keeps every release ever seen (past rows are never dropped), so the
calendar survives BLS rolling old months off the page.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "https://www.bls.gov/schedule/news_release/cpi.htm"
DEFAULT_UA = "cpi-contrib research@example.com"     # bls.gov wants a contact string with an email
ET = ZoneInfo("America/New_York")
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _month(name: str) -> int:
    return MONTHS[name.strip(". ").lower()[:3]]


def parse(html: str) -> list:
    """Rows of the 'release-list' table -> [{ref, date, time_et}]."""
    m = re.search(r'<table[^>]*class="release-list"[^>]*>(.*?)</table>', html, flags=re.S)
    if not m:
        raise ValueError("release table not found (page layout changed?)")
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), flags=re.S):
        cells = [re.sub(r"<[^>]+>|\s+", " ", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S)]
        if len(cells) < 3:
            continue
        ref = re.match(r"([A-Za-z]+)\s+(\d{4})", cells[0])
        rel = re.match(r"([A-Za-z]+)\.?\s+(\d{1,2}),\s*(\d{4})", cells[1])
        tm = re.match(r"(\d{1,2}):(\d{2})\s*([AP]M)", cells[2], flags=re.I)
        if not (ref and rel and tm):
            continue
        h = int(tm.group(1)) % 12 + (12 if tm.group(3).upper() == "PM" else 0)
        out.append({
            "ref": f"{ref.group(2)}-{_month(ref.group(1)):02d}",
            "date": f"{rel.group(3)}-{_month(rel.group(1)):02d}-{int(rel.group(2)):02d}",
            "time_et": f"{h:02d}:{tm.group(2)}",
        })
    if not out:
        raise ValueError("release table is empty")
    return out


def load(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)["releases"]
    except (OSError, ValueError, KeyError):
        return []


def save(path: str, rows: list) -> None:
    rows = sorted({r["ref"]: r for r in rows}.values(), key=lambda r: r["ref"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"source": URL, "timezone": "America/New_York", "releases": rows}, f, indent=1)
        f.write("\n")


def release_dt(r: dict) -> datetime:
    return datetime.fromisoformat(f"{r['date']}T{r['time_et']}").replace(tzinfo=ET)


def latest_released(rows: list, now: datetime) -> str | None:
    done = [r["ref"] for r in rows if release_dt(r) <= now]
    return max(done) if done else None


def next_release(rows: list, now: datetime) -> dict | None:
    up = [r for r in rows if release_dt(r) > now]
    return min(up, key=release_dt) if up else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="BLS CPI release calendar")
    ap.add_argument("cmd", nargs="?", choices=["check"], help="print run=true|false")
    ap.add_argument("--download", action="store_true", help="refresh the calendar from bls.gov")
    ap.add_argument("--download-if-needed", action="store_true",
                    help="refresh only when fewer than 3 future releases are known, or a release "
                         "the site does not have is due (catches a rescheduled release)")
    ap.add_argument("--file", default=os.path.join(HERE, "release_schedule.json"))
    ap.add_argument("--data", default=os.path.join(HERE, "web", "data", "cpi_data.json"))
    ap.add_argument("--user-agent", default=os.environ.get("BLS_USER_AGENT") or DEFAULT_UA)
    args = ap.parse_args(argv)

    rows = load(args.file)
    now = datetime.now(ET)
    try:
        with open(args.data, encoding="utf-8") as f:
            have = max(json.load(f)["breakdowns"]["basic4"]["mom"]["dates"])
    except (OSError, ValueError, KeyError):
        have = None

    download = args.download
    if args.download_if_needed and not download:
        future = [r for r in rows if release_dt(r) > now]
        want = latest_released(rows, now)
        download = len(future) < 3 or want is None or have is None or want > have
        if not download:
            print(f"schedule: {len(future)} future releases known, no refresh needed", file=sys.stderr)
    if download:
        req = urllib.request.Request(URL, headers={"User-Agent": args.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                fresh = parse(r.read().decode("utf-8", "ignore"))
            rows = rows + fresh                      # save() de-duplicates, newest wins
            save(args.file, rows)
            rows = load(args.file)
            print(f"schedule: {len(fresh)} releases on the page, {len(rows)} kept", file=sys.stderr)
        except Exception as e:                       # noqa: BLE001 - keep the old calendar
            print(f"schedule: download failed ({e}); using {args.file}", file=sys.stderr)

    if args.cmd == "check":
        want = latest_released(rows, now)
        nxt = next_release(rows, now)
        if not rows or nxt is None:
            print("schedule: calendar is empty or has no future release; run schedule.py --download",
                  file=sys.stderr)
        # no calendar -> run anyway rather than silently stop updating
        run = want is None or have is None or want > have
        print(f"schedule: site has {have}, BLS has released {want}, next "
              f"{nxt['ref'] + ' on ' + nxt['date'] if nxt else 'unknown'}", file=sys.stderr)
        print(f"run={'true' if run else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
