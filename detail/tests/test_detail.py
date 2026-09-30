"""
Tests for the item-level pipeline.  Run from the project root:

    python -m pytest detail/tests -q

Tests marked "needs data" use ri_official/ and cache/bls_flat/ and are skipped when those
are missing (run `python ri_official.py --download` and `python detail/cpi_detail.py` first).
"""

import csv
import os

import pytest

import contributions
import hierarchy
import weights
from common import ROOT, minus12, month_list, norm

RI_DIR = os.path.join(ROOT, "ri_official")
CACHE = os.path.join(ROOT, "cache", "bls_flat")
needs_ri = pytest.mark.skipif(not os.path.exists(os.path.join(RI_DIR, "2025.xlsx")), reason="needs data: ri_official/")
needs_cache = pytest.mark.skipif(not os.path.exists(os.path.join(CACHE, "cu.item")), reason="needs data: cache/bls_flat/")


# ---------------------------------------------------------------- common
def test_norm_matches_bls_spellings():
    assert norm("Men's underwear, nightwear, swimwear, and accessories") == "men s underwear nightwear swimwear and accessories"
    assert norm("Fruits & vegetables") == norm("Fruits and vegetables")
    assert norm("Motor fuel(1)") == "motor fuel"          # footnote markers are dropped
    assert norm("Gasoline (all types)") == "gasoline all types"


def test_months_cross_year_boundary():
    assert month_list("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert minus12("2026-01") == "2025-01"


# ---------------------------------------------------------------- hierarchy repair (PITFALLS §1)
def _rows(spec):
    return [{"name": n, "level": lv, "ri": ri} for n, lv, ri in spec]


def test_repair_moves_a_too_deep_child_up():
    # 'Alcoholic beverages' published one level too deep, inside Food
    rows = _rows([("Food and beverages", 1, 14.5), ("Food", 2, 13.7), ("Food at home", 3, 8.3),
                  ("Food away from home", 3, 5.4), ("Alcoholic beverages", 3, 0.8)])
    fixes = hierarchy.repair_levels(rows)
    assert [r["level"] for r in rows] == [1, 2, 3, 3, 2]
    assert fixes and "Alcoholic beverages" in fixes[0]
    assert hierarchy.hierarchy_problems(rows) == []


def test_repair_moves_a_too_shallow_subtree_down():
    # 'Information technology...' and its children published one level too shallow
    rows = _rows([("Communication", 2, 3.245), ("Postage and delivery", 3, 0.064),
                  ("Information and information processing", 3, 3.181), ("Telephone services", 4, 1.466),
                  ("Information technology", 3, 1.714), ("Computers", 4, 0.3), ("Internet", 4, 1.414)])
    hierarchy.repair_levels(rows)
    assert [r["level"] for r in rows] == [2, 3, 3, 4, 4, 5, 5]
    assert hierarchy.hierarchy_problems(rows) == []


@needs_ri
@pytest.mark.parametrize("year", ["2024", "2025"])
def test_real_tables_repair_to_a_consistent_tree(year):
    rows = hierarchy.read_table1(os.path.join(RI_DIR, f"{year}.xlsx"))
    fixes = hierarchy.repair_levels(rows)
    assert len(fixes) == 2                               # the two known BLS indentation errors
    assert hierarchy.hierarchy_problems(rows) == []


def test_link_sets_parents_and_leaves():
    rows = hierarchy.link(_rows([("A", 1, 1), ("A1", 2, 1), ("B", 1, 1)]))
    assert [r["parent"] for r in rows] == [None, 0, None]
    assert [r["leaf"] for r in rows] == [False, True, True]


# ---------------------------------------------------------------- codes and names (PITFALLS §2)
@needs_cache
def test_name_overrides_point_at_real_bls_codes():
    with open(os.path.join(CACHE, "cu.item"), encoding="utf-8", errors="ignore") as f:
        codes = {r["item_code"].strip() for r in csv.DictReader(f, delimiter="\t")}
    for name, code in hierarchy.NAME_OVERRIDES.items():
        assert code in codes, f"{name} -> {code} is not in cu.item"


def test_every_translation_row_is_well_formed():
    with open(os.path.join(ROOT, "detail", "item_names_zh.csv"), newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["name", "zh"]
    assert all(len(r) == 2 and r[1].strip() for r in rows[1:]), "a row has a stray comma or an empty name"


# ---------------------------------------------------------------- weights (PITFALLS §3-5)
def test_price_update_and_december_new_basis():
    official = {2024: {"x": 10.0}, 2025: {"x": 12.0}}
    fill = {0: {"2024-12": 100.0, "2025-06": 110.0, "2025-12": 120.0}}
    all_u = {"2024-12": 200.0, "2025-06": 205.0, "2025-12": 210.0}
    w = weights.Weights(official, fill, all_u)
    row = {"id": 0, "name": "X"}
    # within the year: RI(a) * item relative / all-items relative
    assert w.ri(row, "2025-06") == pytest.approx(10.0 * (110 / 100) / (205 / 200))
    # December takes the published new-basis table, not the price-updated old basis
    assert w.ri(row, "2025-12") == 12.0


def test_aliases_find_renamed_items_in_older_tables():
    official = {2016: {"airline fare": 0.8}}
    assert weights.anchor(official, "Airline fares", 2016) == 0.8


def test_unpublished_items_use_the_nearest_ancestor_index():
    rows = [{"id": 0, "code": "P", "parent": None}, {"id": 1, "code": None, "parent": 0},
            {"id": 2, "code": "C", "parent": 0}]
    src = weights.index_sources(rows, U={"P": {}, "C": {}}, S={"P": {}})
    assert src == {0: 0, 1: 0, 2: 2}
    assert [r["proxy"] for r in rows] == [False, True, False]
    assert [r["sa"] for r in rows] == [True, True, False]


def test_missing_months_move_with_the_parent_for_weights_only():
    rows = [{"id": 0, "code": "P", "parent": None}, {"id": 1, "code": "C", "parent": 0}]
    U = {"P": {"2025-01": 100.0, "2025-02": 110.0}, "C": {"2025-01": 50.0}}
    src = weights.index_sources(rows, U, S={})
    fill = weights.filled_indexes(rows, src, U, all_u={"2025-01": 1.0, "2025-02": 1.0}, months=["2025-01", "2025-02"])
    assert fill[1]["2025-02"] == pytest.approx(55.0)     # 50 moved with the parent's +10%
    assert "2025-02" not in U["C"]                       # the raw index is left alone


# ---------------------------------------------------------------- contributions
def test_previous_published_skips_an_unpublished_month():
    prev = contributions.previous_published(["2025-09", "2025-10", "2025-11"], {"2025-09": 1, "2025-11": 1})
    assert prev == {"2025-11": "2025-09"}


def test_mom_uses_sa_when_published_and_nsa_otherwise():
    hist = sel = ri_months = ["2026-01", "2026-02"]
    prev = {"2026-02": "2026-01"}

    class W:
        def ri(self, row, m):
            return 10.0

    U = {"X": {"2026-01": 100.0, "2026-02": 101.0}}
    with_sa = contributions.item_series({"id": 0}, "X", U, {"X": {"2026-01": 100.0, "2026-02": 102.0}},
                                        W(), hist, sel, ri_months, prev)
    no_sa = contributions.item_series({"id": 0}, "X", U, {}, W(), hist, sel, ri_months, prev)
    assert with_sa["gm"][1] == pytest.approx(2.0) and with_sa["cm"][1] == pytest.approx(0.2)
    assert no_sa["gm"][1] == pytest.approx(1.0) and no_sa["cm"][1] == pytest.approx(0.1)
