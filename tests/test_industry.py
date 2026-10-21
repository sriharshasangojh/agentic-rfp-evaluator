"""Tests for tools/industry.py (spec 9.5 and 16.1). No API key or network needed."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from db import repository
from db.seed import ensure_database
from tools.industry import ABOVE, BELOW, GRADERS, MEETS, NOT_STATED, PurchaseTerms, industry_score
from tools.ranking import CriterionWeight


@dataclass(frozen=True)
class FakeBenchmark:
    attribute_key: str
    label: str
    criterion_id: int


# The spec's own scenario (section 1.2): Rs 60 lakh for 100 laptops, so Rs 60,000 per laptop.
SCENARIO = PurchaseTerms(6_000_000, 100)


def grade(attribute_key, facts, terms=SCENARIO):
    return GRADERS[attribute_key](facts, terms)


def score(facts, benchmarks, criteria, terms=SCENARIO):
    return industry_score(facts, benchmarks, criteria, terms)


@pytest.fixture
def seeded():
    """The real seeded benchmarks and active criteria."""
    ensure_database()
    with repository.connection() as conn:
        return repository.list_benchmarks(conn), repository.list_criteria(conn, active_only=True)


# ---------------------------------------------------------------- unit_price

@pytest.mark.parametrize(
    "facts, expected",
    [
        ({"unit_price_rs": 44999}, ABOVE),
        ({"unit_price_rs": 44999.99}, ABOVE),
        ({"unit_price_rs": 45000}, MEETS),
        ({"unit_price_rs": 52000}, MEETS),
        ({"unit_price_rs": 60000}, MEETS),
        ({"unit_price_rs": 60001}, BELOW),
        ({}, NOT_STATED),
        ({"unit_price_rs": None, "grand_total_rs": None}, NOT_STATED),
        ({"unit_price_rs": 0}, NOT_STATED),
        ({"unit_price_rs": -5}, NOT_STATED),
        ({"unit_price_rs": "50000"}, NOT_STATED),
        # estimated from the grand total: total / 1.18 / 100 laptops
        ({"grand_total_rs": 5_309_999}, ABOVE),   # 44,999.99 per laptop
        ({"grand_total_rs": 5_310_000}, MEETS),   # exactly 45,000
        ({"grand_total_rs": 5_900_000}, MEETS),   # 50,000
        ({"grand_total_rs": 7_080_000}, MEETS),   # exactly 60,000
        ({"grand_total_rs": 7_080_001}, BELOW),
        ({"unit_price_rs": 0, "grand_total_rs": 5_900_000}, MEETS),  # a 0 price is not a price: fall back to the total
        ({"grand_total_rs": 0}, NOT_STATED),
    ],
)
def test_unit_price_grades(facts, expected):
    assert grade("unit_price", facts).grade == expected


def test_unit_price_estimated_from_the_total_says_so():
    graded = grade("unit_price", {"grand_total_rs": 5_900_000})
    assert graded.warnings == ("Unit price estimated from total",)
    assert graded.reason == "Rs 50,000 per laptop, estimated from the total (budget Rs 60,000 each)"


def test_a_stated_unit_price_wins_over_the_total_and_has_no_warning():
    graded = grade("unit_price", {"unit_price_rs": 49999, "grand_total_rs": 99_999_999})
    assert (graded.grade, graded.warnings, graded.reason) == (MEETS, (), "Rs 49,999 per laptop (budget Rs 60,000 each)")


@pytest.mark.parametrize("price, shown", [(120000, "Rs 1,20,000"), (999, "Rs 999"), (1000, "Rs 1,000"), (4999999, "Rs 49,99,999")])
def test_rupees_use_indian_grouping(price, shown):
    assert grade("unit_price", {"unit_price_rs": price}).reason.startswith(f"{shown} per laptop")


# ---------------------------------------------------------------- warranty

@pytest.mark.parametrize(
    "years, kind, repair, expected",
    [
        (3, "on-site", 1, ABOVE),
        (3, "on-site", 0.5, ABOVE),
        (5, "on-site", 1, ABOVE),
        (3, "on-site", 2, MEETS),
        (3, "on-site", None, MEETS),
        (5, "on-site", 3, MEETS),
        (2, "on-site", 1, BELOW),
        (2.5, "on-site", None, BELOW),
        (0, "on-site", None, BELOW),
        (3, "carry-in", 1, BELOW),
        (5, "carry-in", None, BELOW),
        (3, None, 1, BELOW),      # on-site cover not confirmed
        (1, None, None, BELOW),
        (None, "on-site", 1, NOT_STATED),
        (None, None, None, NOT_STATED),
    ],
)
def test_warranty_grades(years, kind, repair, expected):
    facts = {"warranty_years": years, "warranty_type": kind, "repair_time_days": repair}
    assert grade("warranty", facts).grade == expected


@pytest.mark.parametrize(
    "facts, reason",
    [
        ({"warranty_years": 3, "warranty_type": "on-site", "repair_time_days": 1}, "3 years, on-site, repair in 1 day"),
        ({"warranty_years": 1, "warranty_type": "carry-in"}, "1 year, carry-in"),
        ({"warranty_years": 3}, "3 years, cover type not stated (on-site cover not confirmed)"),
        ({}, "Warranty length not stated"),
    ],
)
def test_warranty_reasons(facts, reason):
    assert grade("warranty", facts).reason == reason


# ---------------------------------------------------------------- repair_time

@pytest.mark.parametrize(
    "facts, expected",
    [
        ({"replacement_within_24h": True}, ABOVE),
        ({"replacement_within_24h": True, "repair_time_days": 7}, ABOVE),
        ({"repair_time_days": 0.5}, ABOVE),
        ({"repair_time_days": 1}, ABOVE),
        ({"repair_time_days": 1.5}, MEETS),
        ({"repair_time_days": 2}, MEETS),
        ({"repair_time_days": 5}, MEETS),
        ({"repair_time_days": 5.1}, BELOW),
        ({"repair_time_days": 6}, BELOW),
        ({"replacement_within_24h": False, "repair_time_days": 1}, ABOVE),
        ({"replacement_within_24h": False, "repair_time_days": 7}, BELOW),
        ({}, NOT_STATED),
        ({"replacement_within_24h": False}, NOT_STATED),
        ({"replacement_within_24h": None, "repair_time_days": None}, NOT_STATED),
    ],
)
def test_repair_time_grades(facts, expected):
    assert grade("repair_time", facts).grade == expected


# ---------------------------------------------------------------- battery

@pytest.mark.parametrize(
    "hours, measured, expected",
    [
        (11.5, True, ABOVE),
        (10.1, True, ABOVE),
        (10, True, MEETS),
        (8, True, MEETS),
        (7.9, True, BELOW),
        (6, True, BELOW),
        (11, False, BELOW),
        (11, None, BELOW),
        (9, False, BELOW),
        (None, True, NOT_STATED),
        (None, None, NOT_STATED),
    ],
)
def test_battery_grades(hours, measured, expected):
    assert grade("battery", {"battery_hours": hours, "battery_measured": measured}).grade == expected


@pytest.mark.parametrize(
    "hours, measured, reason",
    [(11.5, True, "11.5 h, measured"), (9, False, "9 h, not measured"), (9, None, "9 h, measurement not stated")],
)
def test_battery_reasons(hours, measured, reason):
    assert grade("battery", {"battery_hours": hours, "battery_measured": measured}).reason == reason


# ---------------------------------------------------------------- memory_storage

@pytest.mark.parametrize(
    "ram, ssd, expected",
    [
        (16, 512, MEETS),
        (32, 512, ABOVE),
        (16, 1024, ABOVE),
        (32, 1024, ABOVE),
        (8, 512, BELOW),
        (16, 256, BELOW),
        (15.9, 512, BELOW),
        (32, 256, BELOW),     # one shortfall outweighs having more of the other
        (8, 1024, BELOW),
        (8, None, BELOW),     # a known shortfall is enough
        (None, 256, BELOW),
        (16, None, NOT_STATED),
        (None, 512, NOT_STATED),
        (32, None, NOT_STATED),
        (None, None, NOT_STATED),
    ],
)
def test_memory_storage_grades(ram, ssd, expected):
    assert grade("memory_storage", {"ram_gb": ram, "ssd_gb": ssd}).grade == expected


@pytest.mark.parametrize(
    "ram, ssd, reason",
    [
        (16, 512, "16 GB RAM, 512 GB SSD"),
        (16, None, "16 GB RAM, SSD not stated"),
        (None, None, "RAM and SSD size not stated"),
    ],
)
def test_memory_storage_reasons(ram, ssd, reason):
    assert grade("memory_storage", {"ram_gb": ram, "ssd_gb": ssd}).reason == reason


# ---------------------------------------------------------------- delivery

@pytest.mark.parametrize(
    "weeks, expected",
    [(4, ABOVE), (5.9, ABOVE), (6, MEETS), (7, MEETS), (8, MEETS), (8.1, BELOW), (12, BELOW), (None, NOT_STATED), ("6", NOT_STATED)],
)
def test_delivery_grades(weeks, expected):
    assert grade("delivery", {"delivery_weeks": weeks}).grade == expected


def test_delivery_with_no_fact_at_all_is_not_stated():
    assert grade("delivery", {}).grade == NOT_STATED


@pytest.mark.parametrize("weeks, reason", [(1, "1 week"), (6, "6 weeks")])
def test_delivery_reasons(weeks, reason):
    assert grade("delivery", {"delivery_weeks": weeks}).reason == reason


# ---------------------------------------------------------------- certifications

@pytest.mark.parametrize(
    "bis, energy, security, expected, reason",
    [
        (True, True, True, ABOVE, "BIS, energy rating, security certification named"),
        (True, True, None, MEETS, "BIS, energy rating named"),
        (True, True, False, MEETS, "BIS, energy rating named"),
        (True, None, None, BELOW, "BIS named"),
        (None, True, None, BELOW, "energy rating named"),
        (True, False, True, BELOW, "BIS, security certification named"),
        (False, False, False, BELOW, "None named"),
        (False, None, None, BELOW, "None named"),
        (None, None, None, NOT_STATED, "No certifications stated"),
    ],
)
def test_certification_grades(bis, energy, security, expected, reason):
    facts = {"bis_named": bis, "energy_rating_named": energy, "security_certification_named": security}
    graded = grade("certifications", facts)
    assert (graded.grade, graded.reason) == (expected, reason)


def test_certifications_with_no_facts_at_all_is_not_stated():
    assert grade("certifications", {}).grade == NOT_STATED


# ---------------------------------------------------------------- data_wiping

@pytest.mark.parametrize(
    "wipe, expected",
    [("certified_standard", ABOVE), ("described", MEETS), (None, NOT_STATED), ("", NOT_STATED), ("shredded", NOT_STATED)],
)
def test_data_wiping_grades(wipe, expected):
    assert grade("data_wiping", {"data_wipe": wipe}).grade == expected


def test_data_wiping_with_no_fact_at_all_is_not_stated():
    assert grade("data_wiping", {}).grade == NOT_STATED


# ---------------------------------------------------------------- the weighted industry score

STRONG_FACTS = {  # sample A
    "unit_price_rs": 52000, "warranty_years": 3, "warranty_type": "on-site", "repair_time_days": 1,
    "battery_hours": 11.5, "battery_measured": True, "ram_gb": 16, "ssd_gb": 512, "delivery_weeks": 7,
    "bis_named": True, "energy_rating_named": True, "security_certification_named": True,
    "data_wipe": "certified_standard",
}
WEAK_FACTS = {  # sample B: no unit price (only a total), 1-year carry-in, nothing about delivery or wiping
    "unit_price_rs": None, "grand_total_rs": 7_500_000, "warranty_years": 1, "warranty_type": "carry-in",
    "repair_time_days": 7, "battery_hours": 7, "battery_measured": False, "ram_gb": 8, "ssd_gb": 256,
    "delivery_weeks": None, "bis_named": True, "energy_rating_named": None,
    "security_certification_named": None, "data_wipe": None,
}


def test_every_seeded_attribute_has_a_grading_rule(seeded):
    benchmarks, _ = seeded
    assert len(benchmarks) == 8
    assert {b.attribute_key for b in benchmarks} <= set(GRADERS)


def test_each_attribute_gets_an_equal_share_of_its_criterions_weight(seeded):
    benchmarks, criteria = seeded
    result = score({}, benchmarks, criteria)
    weights = {a.attribute_key: a.weight for a in result.attributes}
    assert weights == {
        "battery": 15, "memory_storage": 15,       # Technical 30 / 2
        "delivery": 20,                            # Implementation 20 / 1
        "unit_price": 20,                          # Commercial 20 / 1
        "certifications": 10, "data_wiping": 10,   # Security 20 / 2
        "warranty": 5, "repair_time": 5,           # Support 10 / 2
    }
    assert sum(weights.values()) == 100


def test_sample_a_a_strong_supplier(seeded):
    benchmarks, criteria = seeded
    result = score(STRONG_FACTS, benchmarks, criteria)
    assert result.score == 86.25
    assert {a.attribute_key: a.grade for a in result.attributes} == {
        "unit_price": MEETS, "warranty": ABOVE, "repair_time": ABOVE, "battery": ABOVE,
        "memory_storage": MEETS, "delivery": MEETS, "certifications": ABOVE, "data_wiping": ABOVE,
    }
    assert result.warnings == ()


def test_sample_b_a_weak_supplier(seeded):
    benchmarks, criteria = seeded
    result = score(WEAK_FACTS, benchmarks, criteria)
    assert result.score == 17.5
    assert {a.attribute_key: a.grade for a in result.attributes} == {
        "unit_price": BELOW, "warranty": BELOW, "repair_time": BELOW, "battery": BELOW,
        "memory_storage": BELOW, "delivery": NOT_STATED, "certifications": BELOW, "data_wiping": NOT_STATED,
    }
    assert result.warnings == ("Unit price estimated from total",)


def test_every_attribute_above_scores_100_and_every_attribute_meets_scores_75(seeded):
    benchmarks, criteria = seeded
    all_above = {
        "unit_price_rs": 40000, "warranty_years": 3, "warranty_type": "on-site", "repair_time_days": 1,
        "battery_hours": 11, "battery_measured": True, "ram_gb": 32, "ssd_gb": 512, "delivery_weeks": 4,
        "bis_named": True, "energy_rating_named": True, "security_certification_named": True,
        "data_wipe": "certified_standard",
    }
    all_meets = {
        "unit_price_rs": 50000, "warranty_years": 3, "warranty_type": "on-site", "repair_time_days": 3,
        "battery_hours": 9, "battery_measured": True, "ram_gb": 16, "ssd_gb": 512, "delivery_weeks": 7,
        "bis_named": True, "energy_rating_named": True, "security_certification_named": None,
        "data_wipe": "described",
    }
    assert score(all_above, benchmarks, criteria).score == 100.0
    assert score(all_meets, benchmarks, criteria).score == 75.0


def test_no_facts_at_all_scores_0_with_every_attribute_not_stated(seeded):
    benchmarks, criteria = seeded
    for facts in (None, {}):
        result = score(facts, benchmarks, criteria)
        assert result.score == 0.0
        assert {a.grade for a in result.attributes} == {NOT_STATED}
        assert result.warnings == ()


def test_points_are_100_75_25_and_0(seeded):
    benchmarks, criteria = seeded
    points = {a.grade: a.points for r in (score(STRONG_FACTS, benchmarks, criteria), score(WEAK_FACTS, benchmarks, criteria)) for a in r.attributes}
    assert points == {ABOVE: 100, MEETS: 75, BELOW: 25, NOT_STATED: 0}


def test_the_score_is_rounded_half_up_to_two_decimals():
    # Eight attributes sharing one criterion: seven Meets (75) and one Above (100) average 78.125.
    keys = list(GRADERS)
    benchmarks = [FakeBenchmark(key, key, 1) for key in keys]
    criteria = [CriterionWeight(1, 100, 10)]
    facts = {
        "unit_price_rs": 50000, "warranty_years": 3, "warranty_type": "on-site", "repair_time_days": 3,
        "battery_hours": 9, "battery_measured": True, "ram_gb": 16, "ssd_gb": 512, "delivery_weeks": 7,
        "bis_named": True, "energy_rating_named": True, "data_wipe": "certified_standard",
    }
    assert score(facts, benchmarks, criteria).score == 78.13   # Python's round() would give 78.12


def test_a_criterion_that_is_not_active_leaves_its_attributes_out(seeded):
    benchmarks, criteria = seeded
    without_commercial = [c for c in criteria if c.criterion_id != 3]
    result = score(STRONG_FACTS, benchmarks, without_commercial)
    assert "unit_price" not in {a.attribute_key for a in result.attributes}
    assert result.score == 89.06    # (8625 - 1500) / 80 = 89.0625


def test_an_attribute_without_a_grading_rule_is_not_stated_with_a_warning():
    benchmarks = [FakeBenchmark("made_up", "Made up", 1)]
    result = score(STRONG_FACTS, benchmarks, [CriterionWeight(1, 100, 10)])
    assert result.score == 0.0
    assert result.attributes[0].grade == NOT_STATED
    assert result.warnings == ("No grading rule for industry attribute 'made_up'; it was graded Not stated.",)


def test_no_benchmarks_gives_0_with_a_warning():
    result = score(STRONG_FACTS, [], [CriterionWeight(1, 100, 10)])
    assert result.score == 0.0 and result.attributes == ()
    assert result.warnings == ("No industry attributes carry any weight, so the industry score is 0.",)


def test_facts_are_not_changed_by_grading(seeded):
    benchmarks, criteria = seeded
    facts = dict(WEAK_FACTS)
    score(facts, benchmarks, criteria)
    assert facts == WEAK_FACTS


def test_same_input_gives_the_same_result(seeded):
    benchmarks, criteria = seeded
    assert score(STRONG_FACTS, benchmarks, criteria) == score(STRONG_FACTS, benchmarks, criteria)


# ---------------------------------------------------------------- price bands follow the budget

@pytest.mark.parametrize(
    "budget, quantity, price, expected",
    [
        # Rs 80,000 per laptop: Meets from 60,000 to 80,000
        (8_000_000, 100, 59_999, ABOVE), (8_000_000, 100, 60_000, MEETS),
        (8_000_000, 100, 80_000, MEETS), (8_000_000, 100, 80_001, BELOW),
        # Rs 2 lakh per laptop: Meets from 1,50,000 to 2,00,000
        (20_000_000, 100, 149_999, ABOVE), (20_000_000, 100, 150_000, MEETS),
        (20_000_000, 100, 200_000, MEETS), (20_000_000, 100, 200_001, BELOW),
        # the same Rs 60,000 per laptop as the spec scenario, but an order of 50
        (3_000_000, 50, 44_999, ABOVE), (3_000_000, 50, 45_000, MEETS), (3_000_000, 50, 60_001, BELOW),
        # a much cheaper product: Rs 500 per unit
        (50_000, 100, 374, ABOVE), (50_000, 100, 375, MEETS), (50_000, 100, 500, MEETS), (50_000, 100, 501, BELOW),
        # an order that does not divide evenly: Rs 1 crore for 3 units is Rs 33,33,333.33 each
        (10_000_000, 3, 2_499_999, ABOVE), (10_000_000, 3, 2_500_000, MEETS),
        (10_000_000, 3, 3_333_333, MEETS), (10_000_000, 3, 3_333_334, BELOW),
    ],
)
def test_price_bands_follow_the_budget_per_unit(budget, quantity, price, expected):
    terms = PurchaseTerms(budget, quantity)
    assert grade("unit_price", {"unit_price_rs": price}, terms).grade == expected


@pytest.mark.parametrize(
    "total, budget, quantity, expected",
    [
        (17_699_999, 20_000_000, 100, ABOVE),   # 1,49,999.99 each: just under 75% of Rs 2 lakh
        (17_700_000, 20_000_000, 100, MEETS),   # exactly 1,50,000 each
        (23_600_000, 20_000_000, 100, MEETS),   # exactly 2,00,000 each
        (23_600_118, 20_000_000, 100, BELOW),   # 2,00,001 each
        (2_950_000, 3_000_000, 50, MEETS),      # 50 units: 50,000 each (with a fixed 100 it would look like 25,000)
    ],
)
def test_a_price_estimated_from_the_total_uses_the_runs_quantity(total, budget, quantity, expected):
    assert grade("unit_price", {"grand_total_rs": total}, PurchaseTerms(budget, quantity)).grade == expected


def test_the_reason_shows_the_budget_the_price_was_compared_with():
    graded = grade("unit_price", {"unit_price_rs": 180_000}, PurchaseTerms(20_000_000, 100))
    assert (graded.grade, graded.reason) == (MEETS, "Rs 1,80,000 per laptop (budget Rs 2,00,000 each)")


SPEC_TOTALS = {  # grand totals from the spec's table of the 7 test proposals (section 1.3)
    "Apex Systems": 7_398_600, "BrightPath Tech": 4_897_000, "NexaWorks": 5_935_400,
    "Horizon Datacom": 5_947_200, "Orbit Digital": 5_734_800, "Zenora Systems": 5_616_800,
    "Vertexa Solutions": None,
}
SPEC_GRADES = {
    "Apex Systems": BELOW, "BrightPath Tech": ABOVE, "NexaWorks": MEETS, "Horizon Datacom": MEETS,
    "Orbit Digital": MEETS, "Zenora Systems": MEETS, "Vertexa Solutions": NOT_STATED,
}


def test_the_spec_scenario_still_grades_the_seven_test_proposals_as_before():
    for name, total in SPEC_TOTALS.items():
        assert grade("unit_price", {"grand_total_rs": total}).grade == SPEC_GRADES[name], name


@pytest.mark.parametrize("factor", [0.5, 2, 2.5, 10])
def test_scaling_every_price_and_the_budget_together_changes_no_grade(factor):
    terms = PurchaseTerms(6_000_000 * factor, 100)
    for name, total in SPEC_TOTALS.items():
        scaled = None if total is None else total * factor
        assert grade("unit_price", {"grand_total_rs": scaled}, terms).grade == SPEC_GRADES[name], name


def test_a_pricey_batch_is_still_graded_and_scored_normally(seeded):
    benchmarks, criteria = seeded
    facts = {**STRONG_FACTS, "unit_price_rs": 180_000}          # Rs 1,80,000 per laptop
    result = score(facts, benchmarks, criteria, PurchaseTerms(20_000_000, 100))
    assert {a.attribute_key: a.grade for a in result.attributes}["unit_price"] == MEETS
    assert result.score == 86.25                                  # same as sample A at Rs 52,000 vs Rs 60,000


def test_without_a_budget_the_price_is_not_graded_but_everything_else_is(seeded):
    benchmarks, criteria = seeded
    result = score(STRONG_FACTS, benchmarks, criteria, None)
    grades = {a.attribute_key: a.grade for a in result.attributes}
    assert grades["unit_price"] == NOT_STATED and grades["battery"] == ABOVE
    assert result.score == 71.25                                  # (8625 - 1500) / 100
    assert result.warnings == ("No budget and quantity given, so unit price was not graded.",)


@pytest.mark.parametrize(
    "budget, quantity",
    [
        (0, 100), (-5, 100), (float("nan"), 100), (float("inf"), 100), ("6000000", 100), (None, 100), (True, 100),
        (6_000_000, 0), (6_000_000, -1), (6_000_000, 2.5), (6_000_000, True), (6_000_000, "100"), (6_000_000, None),
    ],
)
def test_an_unusable_budget_or_quantity_is_refused(budget, quantity):
    with pytest.raises(ValueError):
        PurchaseTerms(budget, quantity)


def test_the_budget_per_unit_is_exact():
    from fractions import Fraction
    assert PurchaseTerms(10_000_000, 3).budget_per_unit == Fraction(10_000_000, 3)
    assert PurchaseTerms(6_000_000, 100).budget_per_unit == 60_000


def test_no_price_limit_is_written_into_the_code():
    source = (Path(__file__).resolve().parent.parent / "tools" / "industry.py").read_text(encoding="utf-8")
    for fixed in ("45000", "60000", "45_000", "60_000", "LAPTOP_QUANTITY"):
        assert fixed not in source, f"industry.py still contains {fixed}"


# ---------------------------------------------------------------- guardrail

def test_industry_module_stays_pure():
    """Spec 2.1 and 21: no LLM, I/O, randomness or clock in industry.py. Only these imports are allowed."""
    allowed = {"__future__", "collections", "dataclasses", "decimal", "fractions", "math", "typing"}
    source = Path(__file__).resolve().parent.parent / "tools" / "industry.py"
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
    assert imported <= allowed, f"unexpected imports in industry.py: {sorted(imported - allowed)}"
    assert not called & {"open", "print", "input", "now", "today", "utcnow", "random"}, sorted(called)
