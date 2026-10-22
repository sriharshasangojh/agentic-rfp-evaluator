"""Tests for tools/insights.py: strength labels (spec 9.6) and the recommendation (spec 10.2). Pros/cons and the rest come in later features."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tools.insights import (AVERAGE, INDUSTRY_WARNING_BELOW, STRONG, WEAK, Contender, Recommendation,
                            choose_recommendation, industry_warning, strength_label)


def test_the_spec_examples():
    assert strength_label(8, 10) == "Strong"
    assert strength_label(7, 10) == "Average"
    assert strength_label(4, 10) == "Weak"


@pytest.mark.parametrize(
    "score, label",
    [
        (10, STRONG), (9, STRONG), (8, STRONG),
        (7.9, AVERAGE), (7, AVERAGE), (5, AVERAGE),
        (4.9, WEAK), (4, WEAK), (0, WEAK),
    ],
)
def test_boundaries_on_a_maximum_of_10(score, label):
    assert strength_label(score, 10) == label


@pytest.mark.parametrize(
    "score, max_score, label",
    [
        (16, 20, STRONG), (15.9, 20, AVERAGE), (10, 20, AVERAGE), (9.9, 20, WEAK),
        (4, 5, STRONG), (2.5, 5, AVERAGE), (2.4, 5, WEAK),
        (2.4, 3, STRONG),   # exactly 80%: a binary float would say 0.7999999999999999
        (12, 10, STRONG),   # above the maximum still counts as Strong
        (-1, 10, WEAK),
    ],
)
def test_other_maximums_and_odd_values(score, max_score, label):
    assert strength_label(score, max_score) == label


@pytest.mark.parametrize("bad_max", [0, -10])
def test_a_maximum_of_zero_or_less_is_refused(bad_max):
    with pytest.raises(ValueError, match="above 0"):
        strength_label(5, bad_max)


def test_the_label_follows_a_changed_score():
    assert strength_label(4, 10) == WEAK
    assert strength_label(8.5, 10) == STRONG  # e.g. after a human override


# ---------------------------------------------------------------- the recommendation (spec 10.2)

def contenders(*spec: tuple[str, int, bool]) -> list[Contender]:
    return [Contender(name, rank, eligible) for name, rank, eligible in spec]


def test_the_recommended_supplier_is_the_top_ranked_one_when_it_passes_every_check():
    result = choose_recommendation(contenders(("Apex", 1, True), ("Nexa", 2, True), ("Orbit", 3, True)))
    assert result == Recommendation(recommended="Apex", runner_up="Nexa", top_ranked="Apex")
    assert not result.none_eligible


def test_a_supplier_that_fails_a_check_is_skipped_even_when_it_ranks_first():
    result = choose_recommendation(contenders(("Vertexa", 1, False), ("Apex", 2, True), ("Nexa", 3, True)))
    assert (result.recommended, result.runner_up, result.top_ranked) == ("Apex", "Nexa", "Vertexa")


def test_the_runner_up_is_the_next_eligible_supplier_not_the_next_rank():
    result = choose_recommendation(contenders(("Apex", 1, True), ("BrightPath", 2, False), ("Nexa", 3, True)))
    assert (result.recommended, result.runner_up) == ("Apex", "Nexa")


def test_with_one_eligible_supplier_there_is_no_runner_up():
    result = choose_recommendation(contenders(("Apex", 1, False), ("Nexa", 2, True), ("Orbit", 3, False)))
    assert (result.recommended, result.runner_up) == ("Nexa", None)


def test_when_nobody_is_eligible_nobody_is_recommended_and_the_top_ranked_is_named():
    result = choose_recommendation(contenders(("Apex", 1, False), ("Nexa", 2, False)))
    assert (result.recommended, result.runner_up, result.top_ranked) == (None, None, "Apex")
    assert result.none_eligible


def test_one_supplier_can_be_recommended_and_has_no_runner_up():
    assert choose_recommendation(contenders(("Solo", 1, True))) == Recommendation("Solo", None, "Solo")
    assert choose_recommendation(contenders(("Solo", 1, False))).none_eligible


def test_the_order_of_the_list_does_not_matter_only_the_rank():
    shuffled = contenders(("Orbit", 3, True), ("Apex", 1, True), ("Nexa", 2, True))
    assert choose_recommendation(shuffled) == Recommendation("Apex", "Nexa", "Apex")


def test_no_suppliers_gives_no_recommendation_and_is_not_a_none_eligible_case():
    result = choose_recommendation([])
    assert result == Recommendation(None, None, None)
    assert not result.none_eligible


def test_the_industry_warning_starts_below_60():
    assert INDUSTRY_WARNING_BELOW == 60
    assert industry_warning(59.99, ["Warranty"]) == "Below industry standard on: Warranty"
    assert industry_warning(60, ["Warranty"]) is None
    assert industry_warning(60.01, ["Warranty"]) is None
    assert industry_warning(100, []) is None


def test_the_industry_warning_lists_the_weak_attributes_in_the_order_given():
    assert industry_warning(21.25, ["Unit price", "Repair time", "Data wiping"]) == (
        "Below industry standard on: Unit price, Repair time, Data wiping")


def test_the_industry_warning_still_works_when_no_attribute_is_named():
    assert industry_warning(10, []) == "Below industry standard on: several attributes"


def test_insights_module_stays_pure():
    """Spec 2.1 and 21: no LLM, I/O, randomness or clock in insights.py. Only these imports are allowed."""
    allowed = {"__future__", "collections", "dataclasses", "fractions", "math", "typing"}
    source = Path(__file__).resolve().parent.parent / "tools" / "insights.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported, called = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            func = node.func
            called.add(func.id if isinstance(func, ast.Name) else getattr(func, "attr", ""))
    assert imported <= allowed, f"unexpected imports in insights.py: {sorted(imported - allowed)}"
    assert not called & {"open", "print", "input", "now", "today", "utcnow", "random"}, sorted(called)
