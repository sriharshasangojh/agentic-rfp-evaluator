"""Tests for tools/ranking.py (spec 9.3, 9.4 and the 16.1 fixture). No API key, database or network needed."""
from __future__ import annotations

import ast
import itertools
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path

import pytest

from tools.ranking import CriterionWeight, SupplierInput, rank_suppliers

# Weights 30/20/20/20/10, max score 10 (spec 16.1).
CRITERIA = (
    CriterionWeight(1, 30, 10),
    CriterionWeight(2, 20, 10),
    CriterionWeight(3, 20, 10),
    CriterionWeight(4, 20, 10),
    CriterionWeight(5, 10, 10),
)


def supplier(name, scores, submitted, experience, criteria=CRITERIA):
    return SupplierInput(
        name=name,
        submission_date=date.fromisoformat(submitted),
        experience_rating=experience,
        scores={c.criterion_id: s for c, s in zip(criteria, scores)},
    )


FIXTURE = [
    supplier("Apex Systems", (9, 7, 5, 9, 8), "2026-10-05", 4),
    supplier("BrightPath Tech", (5, 4, 8, 3, 3), "2026-10-07", 2),
    supplier("NexaWorks", (7, 9, 7, 7, 9), "2026-10-06", 4),
    supplier("Horizon Datacom", (7, 9, 7, 7, 9), "2026-10-06", 3),
    supplier("Orbit Digital", (7, 5, 7, 6, 9), "2026-10-08", 5),
]

EXPECTED = [  # name, absolute, PPI (spec 16.1), in rank order
    ("Apex Systems", 77.00, 86.94),
    ("NexaWorks", 76.00, 86.39),
    ("Horizon Datacom", 76.00, 86.39),
    ("Orbit Digital", 66.00, 75.28),
    ("BrightPath Tech", 48.00, 55.56),
]


def one_criterion(max_score=10):
    return (CriterionWeight(1, 100, max_score),)


# ---------------------------------------------------------------- the spec fixture

def test_fixture_scores_and_ranks_match_the_spec():
    result = rank_suppliers(FIXTURE, CRITERIA)
    got = [(r.rank, r.name, r.absolute_score, r.ppi) for r in result.suppliers]
    want = [(i, name, absolute, ppi) for i, (name, absolute, ppi) in enumerate(EXPECTED, start=1)]
    assert got == want


def test_fixture_tie_break_is_explained():
    result = rank_suppliers(FIXTURE, CRITERIA)
    assert result.tie_break_explanations == (
        "NexaWorks above Horizon Datacom: higher experience rating (4 vs 3)",
    )


def test_fixture_has_no_warnings():
    assert rank_suppliers(FIXTURE, CRITERIA).warnings == ()


def test_fixture_benchmarks_gaps_and_relative_percentages():
    result = rank_suppliers(FIXTURE, CRITERIA)
    apex = next(r for r in result.suppliers if r.name == "Apex Systems")
    assert [c.benchmark for c in apex.criteria] == [9, 9, 8, 9, 9]
    assert [c.gap for c in apex.criteria] == [0, -2, -3, 0, -1]
    assert [c.relative_pct for c in apex.criteria] == [100.00, 77.78, 62.50, 100.00, 88.89]


def test_leader_on_each_criterion_has_zero_gap_and_100_percent():
    result = rank_suppliers(FIXTURE, CRITERIA)
    for position in range(len(CRITERIA)):
        leaders = [r.criteria[position] for r in result.suppliers if r.criteria[position].gap == 0]
        assert leaders, "someone must hold the benchmark"
        assert all(c.relative_pct == 100.00 for c in leaders)
        assert all(r.criteria[position].gap <= 0 for r in result.suppliers)


def test_input_order_does_not_change_the_result():
    expected = rank_suppliers(FIXTURE, CRITERIA)
    for permutation in itertools.permutations(FIXTURE):
        assert rank_suppliers(list(permutation), CRITERIA) == expected


def test_same_input_gives_identical_output_every_time():
    assert rank_suppliers(FIXTURE, CRITERIA) == rank_suppliers(FIXTURE, CRITERIA)


def test_reweighted_fixture_reproduces_the_spec_what_if_numbers():
    # Spec 16.1 what-if: price (C3) at 30%, the other four scaled proportionally from 70 (hand-written here;
    # the what-if feature itself comes later). Expected PPIs: NexaWorks 86.53, Apex 83.89, NexaWorks leads.
    reweighted = (
        CriterionWeight(1, 26.25, 10),
        CriterionWeight(2, 17.5, 10),
        CriterionWeight(3, 30, 10),
        CriterionWeight(4, 17.5, 10),
        CriterionWeight(5, 8.75, 10),
    )
    result = rank_suppliers(FIXTURE, reweighted)
    ppi = {r.name: r.ppi for r in result.suppliers}
    assert ppi["NexaWorks"] == 86.53
    assert ppi["Apex Systems"] == 83.89
    assert result.suppliers[0].name == "NexaWorks"


# ---------------------------------------------------------------- tie-breaks

def test_equal_ppi_is_broken_by_earlier_submission_date_first():
    suppliers = [
        supplier("Beta", (5,), "2026-10-09", 5, one_criterion()),
        supplier("Alpha", (5,), "2026-10-02", 1, one_criterion()),
    ]
    result = rank_suppliers(suppliers, one_criterion())
    assert [r.name for r in result.suppliers] == ["Alpha", "Beta"]
    assert result.tie_break_explanations == (
        "Alpha above Beta: earlier submission date (2026-10-02 vs 2026-10-09)",
    )


def test_name_breaks_a_tie_when_ppi_date_and_experience_are_equal_ignoring_case():
    suppliers = [
        supplier("zeta Corp", (6,), "2026-10-05", 3, one_criterion()),
        supplier("Alpha Ltd", (6,), "2026-10-05", 3, one_criterion()),
        supplier("beta Inc", (6,), "2026-10-05", 3, one_criterion()),
    ]
    result = rank_suppliers(suppliers, one_criterion())
    assert [r.name for r in result.suppliers] == ["Alpha Ltd", "beta Inc", "zeta Corp"]
    assert result.tie_break_explanations == (
        "Alpha Ltd above beta Inc: supplier name comes first alphabetically (Alpha Ltd vs beta Inc)",
        "beta Inc above zeta Corp: supplier name comes first alphabetically (beta Inc vs zeta Corp)",
    )


def test_no_explanation_when_nobody_ties():
    suppliers = [
        supplier("Alpha", (9,), "2026-10-05", 3, one_criterion()),
        supplier("Beta", (4,), "2026-10-05", 3, one_criterion()),
    ]
    assert rank_suppliers(suppliers, one_criterion()).tie_break_explanations == ()


def test_scores_are_rounded_to_two_decimals_before_sorting():
    # Beta's exact PPI is 99.9999, which rounds to 100.00 and so ties with Alpha;
    # Beta submitted earlier, so Beta must rank first. Without rounding first, Alpha would win.
    criteria = one_criterion(max_score=10000)
    suppliers = [
        supplier("Alpha", (10000,), "2026-10-09", 3, criteria),
        supplier("Beta", (9999.99,), "2026-10-01", 3, criteria),
    ]
    result = rank_suppliers(suppliers, criteria)
    assert [r.ppi for r in result.suppliers] == [100.00, 100.00]
    assert [r.name for r in result.suppliers] == ["Beta", "Alpha"]
    assert result.tie_break_explanations == (
        "Beta above Alpha: earlier submission date (2026-10-01 vs 2026-10-09)",
    )


def test_rounding_is_half_up_not_bankers_or_binary_float():
    # 6.2505 / 10 * 100 = 62.505 exactly; half up gives 62.51 (plain float rounding would give 62.5).
    suppliers = [supplier("Alpha", (6.2505,), "2026-10-05", 3, one_criterion())]
    assert rank_suppliers(suppliers, one_criterion()).suppliers[0].absolute_score == 62.51


def test_decimal_scores_are_not_polluted_by_binary_float_noise():
    criteria = (CriterionWeight(1, 50, 10), CriterionWeight(2, 50, 10))
    suppliers = [supplier("Alpha", (0.1, 0.2), "2026-10-05", 3, criteria)]
    # (0.1/10 * 50) + (0.2/10 * 50) = 0.5 + 1.0 = 1.5 exactly
    assert rank_suppliers(suppliers, criteria).suppliers[0].absolute_score == 1.5


# ---------------------------------------------------------------- edge cases (spec 9.4)

def test_zero_benchmark_gives_everyone_100_percent():
    criteria = (CriterionWeight(1, 50, 10), CriterionWeight(2, 50, 10))
    suppliers = [
        supplier("Alpha", (0, 8), "2026-10-05", 3, criteria),
        supplier("Beta", (0, 4), "2026-10-05", 3, criteria),
    ]
    result = rank_suppliers(suppliers, criteria)
    for ranked in result.suppliers:
        first = ranked.criteria[0]
        assert (first.benchmark, first.gap, first.relative_pct) == (0, 0, 100.00)
    assert [(r.name, r.ppi) for r in result.suppliers] == [("Alpha", 100.00), ("Beta", 75.00)]
    assert any("every supplier scored 0" in w for w in result.warnings)


def test_one_supplier_is_allowed_scores_100_and_warns():
    result = rank_suppliers([FIXTURE[0]], CRITERIA)
    only = result.suppliers[0]
    assert (only.rank, only.ppi, only.absolute_score) == (1, 100.00, 77.00)
    assert any("Only one supplier" in w for w in result.warnings)
    assert result.tie_break_explanations == ()


def test_no_suppliers_gives_an_empty_result():
    result = rank_suppliers([], CRITERIA)
    assert result.suppliers == () and result.tie_break_explanations == () and result.warnings == ()


def test_duplicate_supplier_names_are_refused_ignoring_case():
    suppliers = [FIXTURE[0], supplier("APEX SYSTEMS", (1, 1, 1, 1, 1), "2026-10-06", 1)]
    with pytest.raises(ValueError, match="unique"):
        rank_suppliers(suppliers, CRITERIA)


@pytest.mark.parametrize(
    "criteria, message",
    [
        ((), "At least one criterion"),
        ((CriterionWeight(1, 50, 10), CriterionWeight(1, 50, 10)), "unique"),
        ((CriterionWeight(1, 100, 0),), "max score"),
        ((CriterionWeight(1, -5, 10), CriterionWeight(2, 105, 10)), "negative weight"),
        ((CriterionWeight(1, 0, 10),), "more than 0"),
    ],
)
def test_bad_criteria_are_refused(criteria, message):
    suppliers = [SupplierInput("Alpha", date(2026, 10, 5), 3, {1: 5, 2: 5})]
    with pytest.raises(ValueError, match=message):
        rank_suppliers(suppliers, criteria)


def test_a_missing_score_is_refused():
    broken = SupplierInput("Alpha", date(2026, 10, 5), 3, {1: 5, 2: 5, 3: 5, 4: 5})  # no criterion 5
    with pytest.raises(ValueError, match="no score for criterion 5"):
        rank_suppliers([broken], CRITERIA)


@pytest.mark.parametrize("bad_score", [-1, 10.5, 12])
def test_an_out_of_range_score_is_refused(bad_score):
    broken = supplier("Alpha", (bad_score, 5, 5, 5, 5), "2026-10-05", 3)
    with pytest.raises(ValueError, match="outside 0 to"):
        rank_suppliers([broken], CRITERIA)


# ---------------------------------------------------------------- guardrails

def test_results_cannot_be_changed_after_the_fact():
    first = rank_suppliers(FIXTURE, CRITERIA).suppliers[0]
    with pytest.raises(FrozenInstanceError):
        first.ppi = 0  # type: ignore[misc]


def test_ranking_module_stays_pure():
    """Spec 2.1: ranking.py has no LLM, I/O, randomness or clock. Only these imports are allowed."""
    allowed = {"__future__", "collections", "dataclasses", "datetime", "decimal", "fractions", "typing"}
    source = Path(__file__).resolve().parent.parent / "tools" / "ranking.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= allowed, f"unexpected imports in ranking.py: {sorted(imported - allowed)}"
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            called.add(func.id if isinstance(func, ast.Name) else getattr(func, "attr", ""))
    assert not called & {"open", "print", "input", "now", "today", "utcnow", "random"}, sorted(called)
