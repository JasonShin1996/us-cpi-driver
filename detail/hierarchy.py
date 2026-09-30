# -*- coding: utf-8 -*-
"""
Item hierarchy: which items exist, how they nest, and what we know about each
============================================================================

The hierarchy comes from the newest December Table 1 (ri_official/YYYY.xlsx, expenditure
category section). BLS's Excel has a few mis-indented rows, repaired here by checking that
every parent's weight equals the sum of its children's (PITFALLS.md §1).

Each row is then annotated with its BLS item code (by name, PITFALLS.md §2), Chinese name,
major group and whether it belongs to core CPI.
"""

from __future__ import annotations

import csv
import glob
import os
from typing import Dict, List, Optional, Tuple

from common import norm

# The eight BLS major groups (level 1 of the expenditure hierarchy) -> Chinese name and colour
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
# Items outside core CPI: everything under these nodes (food and energy)
NON_CORE_ROOTS = {"food", "household energy", "motor fuel"}
# Table-1 names that differ from cu.item names (checked against cu.item).
# "Care of invalids and elderly at home" has no published U.S. index; it stays uncoded and
# is estimated from the level above, like the unsampled items.
NAME_OVERRIDES = {
    "housing at school excluding board": "SEHB01",                  # Lodging while at school
    "technical and business school tuition and fees": "SEEB04",     # Technical and vocational school tuition and fixed fees
}


# ---------------------------------------------------------------------------
# indentation repair
# ---------------------------------------------------------------------------
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
    Repair them in place so every parent's RI equals the sum of its children's, and report each fix.

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
    """Parents whose RI still differs from the sum of their children's."""
    out = []
    for i in range(len(rows)):
        kids = _children(rows, i)
        if kids:
            tot = sum(rows[k]["ri"] for k in kids)
            if abs(tot - rows[i]["ri"]) > _tol(len(kids)):
                out.append(f"'{rows[i]['name']}' {rows[i]['ri']:.3f} vs children {tot:.3f}")
    return out


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def read_table1(path: str) -> List[dict]:
    """Expenditure-category rows of a December Table 1 xlsx: [{name, level, ri}] in table order."""
    import openpyxl
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True)["Table 1"]
    rows, section = [], None
    for r in ws.iter_rows(values_only=True):
        if r[1] and isinstance(r[1], str) and r[2] is None and str(r[0]) == "0":
            section = r[1].strip().lower()
        if r[1] and isinstance(r[2], (int, float)) and section and section.startswith("expenditure"):
            rows.append({"name": r[1].strip(), "level": int(r[0]), "ri": float(r[2])})
    return rows


def link(rows: List[dict]) -> List[dict]:
    """Add parent (index) and leaf (no deeper row follows) from the level numbers."""
    stack: List[int] = []
    for i, row in enumerate(rows):
        while stack and rows[stack[-1]]["level"] >= row["level"]:
            stack.pop()
        row["parent"] = stack[-1] if stack else None
        stack.append(i)
    for i, row in enumerate(rows):
        row["leaf"] = not (i + 1 < len(rows) and rows[i + 1]["level"] > row["level"])
    return rows


def load_hierarchy(ri_dir: str, max_level: int) -> List[dict]:
    """Rows of the newest December Table 1 down to max_level, indentation repaired and linked."""
    newest = max(glob.glob(os.path.join(ri_dir, "[0-9][0-9][0-9][0-9].xlsx")))
    full = read_table1(newest)
    for f in repair_levels(full):
        print(f"   fixed indentation: {f}")
    for p in hierarchy_problems(full):
        print(f"   ! hierarchy does not add up: {p}")
    rows = link([dict(r) for r in full if r["level"] <= max_level])
    print(f"   hierarchy from {os.path.basename(newest)}: {len(rows)} items to level {max_level}, "
          f"{sum(r['leaf'] for r in rows)} lowest-level")
    return rows


def load_zh_names(path: str) -> Dict[str, str]:
    """detail/item_names_zh.csv: BLS item name -> Traditional Chinese name."""
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {norm(r["name"]): r["zh"].strip() for r in csv.DictReader(f) if r.get("zh")}


def annotate(rows: List[dict], codes: Dict[str, str], zh: Dict[str, str]) -> Tuple[List[str], List[str]]:
    """Add id, zh, unsampled, code, group and core to every row (in place).
    Returns the names with no BLS code and the names with no Chinese name."""
    missing_code, missing_zh = [], []
    for i, row in enumerate(rows):
        n = norm(row["name"])
        row["id"] = i
        row["zh"] = zh.get(n)
        if row["zh"] is None:
            missing_zh.append(row["name"])
        row["unsampled"] = n.startswith("unsampled")
        row["code"] = None if row["unsampled"] else (NAME_OVERRIDES.get(n) or codes.get(n))
        if row["code"] is None and not row["unsampled"]:
            missing_code.append(row["name"])
        # group = level-1 ancestor; core = nothing on the way up is food or energy
        a, chain = i, []
        while a is not None:
            chain.append(norm(rows[a]["name"]))
            a = rows[a]["parent"]
        a = i
        while rows[a]["level"] > 1:
            a = rows[a]["parent"]
        top = norm(rows[a]["name"])
        row["group"] = next((g for g, (en, _, _) in enumerate(GROUPS) if norm(en) == top), None)
        row["core"] = not any(c in NON_CORE_ROOTS for c in chain)
    return missing_code, missing_zh
