# -*- coding: utf-8 -*-
"""Small helpers shared by the detail/ modules."""

from __future__ import annotations

import os
import re
from typing import List, Optional

# detail/ lives one level below the project root; data and outputs are relative to the root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# bls.gov and download.bls.gov want a contact string with an email (see PITFALLS.md)
DEFAULT_UA = "cpi-contrib research@example.com"


def norm(s: str) -> str:
    """Normalise a BLS item name for matching: lower case, '&' -> 'and', no punctuation or
    footnote markers. Used everywhere names are compared (Table 1, cu.item, older tables)."""
    s = s.lower().replace("&", "and")
    s = re.sub(r"\(\d+\)|\s*\d+$", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def month_list(first: str, last: str) -> List[str]:
    """Every month 'YYYY-MM' from first to last inclusive."""
    y, m = int(first[:4]), int(first[5:])
    out = []
    while f"{y}-{m:02d}" <= last:
        out.append(f"{y}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def minus12(m: str) -> str:
    """Same month one year earlier."""
    return f"{int(m[:4]) - 1}{m[4:]}"


def r3(x: Optional[float], d: int = 4) -> Optional[float]:
    return None if x is None else round(x, d)
