"""Tests for tools/insights.py: strength labels (spec 9.6), the recommendation (spec 10.2), and (change pack R1a) the plain
screen labels, the price facts and the pros and cons selection (spec 10.1). The what-if and negotiation parts come later."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tools import gates as gates_tool
from tools import insights
from tools.insights import (AVERAGE, INDUSTRY_WARNING_BELOW, STRONG, WEAK, Contender, CriterionResult, GateOutcome, PriceFacts,
                            Reason, Recommendation, choose_recommendation, format_lakh, gate_label, gate_problem,
                            industry_warning, plain_label, price_facts, select_pros_cons, strength_label)


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


# ---------------------------------------------------------------- plain labels (change pack R1, section 3.1)

@pytest.mark.parametrize("real, plain", [
    ("Technical Capability", "Laptop specs"), ("Implementation Plan", "Delivery plan"), ("Commercial Value", "Value for money"),
    ("Security & Compliance", "Security"), ("Support & Experience", "Support"),
])
def test_the_five_criteria_have_plain_names(real, plain):
    assert plain_label(real) == plain


def test_a_criterion_without_a_plain_name_shows_its_own_name():
    assert plain_label("Sustainability") == "Sustainability"
    assert plain_label("") == ""
    assert plain_label("technical capability") == "technical capability"   # only the exact seeded names are translated


@pytest.mark.parametrize("gate_id, label", [
    ("warranty", "3-yr warranty"), ("certification", "BIS certified"), ("price_table", "Price list"), ("delivery", "Delivery \u2264 8 wks"),
])
def test_the_four_must_haves_have_short_column_names(gate_id, label):
    assert gate_label(gate_id, "whatever the database says") == label


def test_a_must_have_without_a_short_name_shows_its_own_name():
    assert gate_label("battery", "Battery life") == "Battery life"


def test_the_gate_labels_cover_exactly_the_gates_that_are_seeded():
    from db import seed
    assert {gate[0] for gate in seed.GATES} == set(insights.GATE_LABELS) == set(insights._GATE_PROBLEMS)


# ---------------------------------------------------------------- what is wrong with a failed must-have

@pytest.mark.parametrize("gate_id, reason, expected", [
    ("warranty", "1 year (at least 3 required)", "warranty under 3 years"),
    ("warranty", "Not stated", "warranty not stated"),
    ("certification", "BIS registration not named", "BIS certificate not named"),
    ("certification", "Not stated", "BIS certificate not named"),
    ("price_table", "No price table", "no price list"),
    ("price_table", "Not stated", "no price list"),
    ("delivery", "10 weeks (at most 8 allowed)", "delivery over 8 weeks"),
    ("delivery", "Not stated", "delivery time not stated"),
    ("delivery", "  NOT   stated ", "delivery time not stated"),
])
def test_a_failed_must_have_is_put_in_a_few_plain_words(gate_id, reason, expected):
    assert gate_problem(gate_id, "Any name", reason) == expected


def test_an_unknown_must_have_gives_its_name_and_reason():
    assert gate_problem("battery", "Battery", "6 hours (at least 10 required)") == "Battery: 6 hours (at least 10 required)"
    assert gate_problem("battery", "Battery", "  ") == "Battery"
    assert gate_problem("battery", " Battery\n life ", "x\ny") == "Battery life: x y"


def test_the_not_stated_wording_follows_the_gates_module():
    assert insights._GATE_NOT_STATED == gates_tool.NOT_STATED.lower()


def test_every_reason_the_real_gates_give_is_understood():
    from types import SimpleNamespace
    from db import seed
    seeded = [SimpleNamespace(gate_id=g[0], name=g[1], rule_key=g[2]) for g in seed.GATES]
    missing = gates_tool.evaluate_gates(seeded, {})
    assert [gate_problem(r.gate_id, r.name, r.reason) for r in missing] == [
        "warranty not stated", "BIS certificate not named", "no price list", "delivery time not stated"]
    bad = gates_tool.evaluate_gates(seeded, {"warranty_years": 1, "bis_named": False, "price_table_present": False, "delivery_weeks": 12})
    assert [gate_problem(r.gate_id, r.name, r.reason) for r in bad] == [
        "warranty under 3 years", "BIS certificate not named", "no price list", "delivery over 8 weeks"]


# ---------------------------------------------------------------- lakh formatting and price facts (change pack R1, section 3.3)

@pytest.mark.parametrize("rupees, text", [
    (6_000_000, "Rs 60 lakh"), (5_600_000, "Rs 56 lakh"), (5_990_000, "Rs 59.9 lakh"), (1_398_600, "Rs 13.99 lakh"),
    (7_398_600, "Rs 73.99 lakh"), (7_398_600.0, "Rs 73.99 lakh"), (12_345_678, "Rs 123.46 lakh"), (100_000, "Rs 1 lakh"),
    (150_000, "Rs 1.5 lakh"), (4_500_000, "Rs 45 lakh"), (0, "Rs 0 lakh"),
])
def test_amounts_are_shown_in_lakh_with_at_most_two_decimals(rupees, text):
    assert format_lakh(rupees) == text


def test_lakh_rounds_half_up_on_the_exact_amount():
    assert format_lakh(1_500) == "Rs 0.02 lakh"       # 0.015 lakh
    assert format_lakh(1_499) == "Rs 0.01 lakh"
    assert format_lakh(5_999_500) == "Rs 60 lakh"      # 59.995 lakh
    assert format_lakh(5_999_499) == "Rs 59.99 lakh"


def test_a_small_positive_amount_never_shows_as_zero():
    assert format_lakh(1) == "Rs 0.01 lakh"
    assert format_lakh(499) == "Rs 0.01 lakh"


def test_a_negative_amount_is_refused():
    with pytest.raises(ValueError, match="negative"):
        format_lakh(-1)


FACTS = {"grand_total_rs": 7_398_600}


def test_a_total_above_the_budget_is_over_by_the_difference():
    assert price_facts(FACTS, 6_000_000) == PriceFacts(7_398_600.0, 6_000_000.0, False, 1_398_600.0)


def test_a_total_below_or_equal_to_the_budget_is_within_it():
    assert price_facts({"grand_total_rs": 5_600_000}, 6_000_000) == PriceFacts(5_600_000.0, 6_000_000.0, True, None)
    assert price_facts({"grand_total_rs": 6_000_000}, 6_000_000).within_budget is True     # exactly the budget is within it


def test_one_rupee_over_is_over():
    facts = price_facts({"grand_total_rs": 6_000_001}, 6_000_000)
    assert facts.within_budget is False and facts.over_by_rs == 1.0


def test_the_run_budget_is_used_not_a_fixed_one():
    assert price_facts({"grand_total_rs": 5_000_000}, 4_500_000).within_budget is False
    assert price_facts({"grand_total_rs": 5_000_000}, 7_000_000).within_budget is True


@pytest.mark.parametrize("total", [None, 0, -5, "7398600", True, float("nan"), float("inf"), [1], {}])
def test_a_total_that_is_missing_or_not_a_number_is_not_stated(total):
    facts = price_facts({"grand_total_rs": total}, 6_000_000)
    assert facts == PriceFacts(None, 6_000_000.0, None, None)


@pytest.mark.parametrize("facts", [{}, None, "text", [], 5])
def test_facts_that_are_not_a_dictionary_or_have_no_total_give_no_price(facts):
    assert price_facts(facts, 6_000_000).total_rs is None


@pytest.mark.parametrize("budget", [None, 0, -1, "6000000", True, float("nan")])
def test_a_run_without_a_usable_budget_says_nothing_about_the_budget(budget):
    facts = price_facts(FACTS, budget)
    assert (facts.total_rs, facts.budget_rs, facts.within_budget, facts.over_by_rs) == (7_398_600.0, None, None, None)


# ---------------------------------------------------------------- pros and cons (spec 10.1)

def crit(cid, name, score, bench, *, weight=20.0, maximum=10.0, filled=False, evidence=None, rel=None, gap=None):
    """A criterion with the gap and relative % worked out the way the ranking engine does (both rounded to 2 decimals)."""
    return CriterionResult(
        cid, name, score, maximum, weight, benchmark=bench, gap=round(score - bench, 2) if gap is None else gap,
        relative_pct=(100.0 if bench == 0 else round(score / bench * 100, 2)) if rel is None else rel,
        filled_by_validator=filled, evidence_found=evidence)


def gate(gate_id, passed=True, reason="ok", name=None):
    return GateOutcome(gate_id, name or gate_id.title(), passed, reason)


PASS_ALL = (gate("warranty"), gate("delivery"))


def texts(reasons):
    return [r.text for r in reasons]


def test_nothing_to_select_gives_nothing():
    assert select_pros_cons([], []) == insights.ProsCons((), ())


def test_the_benchmark_leader_is_a_pro_with_the_batch_as_the_yardstick():
    result = select_pros_cons([crit(4, "Security & Compliance", 9, 9)], [])
    assert result.pros == (Reason("Strong on Security (9/10, best of all bids)", "vs this batch", "criterion"),)
    assert result.cons == ()


def test_a_leader_who_is_not_strong_is_still_best_of_all_bids_and_the_marks_are_shown():
    assert texts(select_pros_cons([crit(3, "Commercial Value", 6, 6)], []).pros) == ["Best of all bids on Value for money (6/10)"]
    assert texts(select_pros_cons([crit(3, "Commercial Value", 3, 3)], []).pros) == ["Best of all bids on Value for money (3/10)"]


def test_a_supplier_at_90_percent_of_the_best_is_a_pro_and_89_99_is_not():
    near = select_pros_cons([crit(1, "Technical Capability", 9, 10, weight=30)], []).pros
    assert texts(near) == ["Close to the best bid on Laptop specs (9/10; best bid 10/10)"]
    assert select_pros_cons([crit(1, "Technical Capability", 8.9, 10, rel=89.99)], []).pros == ()
    assert len(select_pros_cons([crit(1, "Technical Capability", 8.9, 10, rel=90.0)], []).pros) == 1


def test_when_the_best_mark_is_zero_nobody_is_praised_for_it():
    assert select_pros_cons([crit(1, "Technical Capability", 0, 0)], []) == insights.ProsCons((), ())


def test_pros_are_ordered_by_weight_times_score_highest_first():
    result = select_pros_cons([
        crit(1, "Technical Capability", 9, 9, weight=30),    # 270
        crit(2, "Implementation Plan", 10, 10, weight=20),   # 200
        crit(3, "Commercial Value", 10, 10, weight=30),      # 300
    ], [])
    assert [r.text.split(" on ")[-1].split(" (")[0] for r in result.pros] == ["Value for money", "Laptop specs", "Delivery plan"]


def test_equal_weight_times_score_keeps_the_marking_scheme_order_and_is_worked_out_exactly():
    # 0.1 x 3 and 0.3 x 1 are both exactly 0.3 (with binary floats the first is 0.30000000000000004 and would jump ahead)
    first = select_pros_cons([crit(1, "B", 1, 1, weight=0.3), crit(2, "A", 3, 3, weight=0.1)], [])
    assert [r.text.split(" on ")[-1].split(" (")[0] for r in first.pros] == ["B", "A"]
    second = select_pros_cons([crit(1, "A", 3, 3, weight=0.1), crit(2, "B", 1, 1, weight=0.3)], [])
    assert [r.text.split(" on ")[-1].split(" (")[0] for r in second.pros] == ["A", "B"]


def test_at_most_three_pros_and_the_must_have_pro_comes_after_the_criteria_ones():
    four = [crit(i, f"C{i}", 10, 10, weight=10 + i) for i in range(1, 5)]
    capped = select_pros_cons(four, PASS_ALL)
    assert len(capped.pros) == 3 and all(r.kind == "criterion" for r in capped.pros)   # the must-have pro is the fourth, so it is dropped
    two = select_pros_cons(four[:2], PASS_ALL)
    assert [r.kind for r in two.pros] == ["criterion", "criterion", "all_gates"]
    assert two.pros[-1] == Reason("Meets every must-have", "vs requirement", "all_gates")


def test_the_must_have_pro_needs_gates_and_all_of_them_passed():
    assert select_pros_cons([], []).pros == ()                                   # no gates: nothing was required
    assert select_pros_cons([], [gate("warranty"), gate("delivery", False, "10 weeks")]).pros == ()
    assert texts(select_pros_cons([], PASS_ALL).pros) == ["Meets every must-have"]


def test_a_failed_must_have_is_the_first_con_with_its_reason():
    result = select_pros_cons([], [gate("warranty", False, "1 year (at least 3 required)", name="Warranty")])
    assert result.cons == (Reason("Not eligible: Warranty \u2014 1 year (at least 3 required)", "vs requirement", "gate"),)


def test_cons_come_in_the_order_gates_then_gaps_then_evidence_then_not_stated():
    criteria = [
        crit(1, "Technical Capability", 5, 10, weight=30),                                      # behind by 5
        crit(2, "Implementation Plan", 8, 8, evidence=False),                                   # evidence not found
        crit(3, "Commercial Value", 0, 6, filled=True, gap=-2.0, rel=0.0),                      # filled with 0, only 2 behind
    ]
    result = select_pros_cons(criteria, [gate("delivery", False, "Not stated", name="Delivery")])
    assert [r.kind for r in result.cons] == ["gate", "criterion", "evidence"]                   # capped at 3: the not-stated con is the fourth
    only = select_pros_cons(criteria[2:], [])
    assert only.cons == (Reason("Value for money not stated in the proposal", "not stated", "not_stated"),)
    four = select_pros_cons(criteria, [gate("delivery", False, "Not stated"), gate("warranty", False, "1 year")])
    assert [r.kind for r in four.cons] == ["gate", "gate", "criterion"]


def test_a_criterion_three_points_behind_is_a_con_and_two_point_nine_is_not():
    assert len(select_pros_cons([crit(3, "Commercial Value", 5, 8)], []).cons) == 1
    assert select_pros_cons([crit(3, "Commercial Value", 5.1, 8, gap=-2.9)], []).cons == ()
    assert select_pros_cons([crit(3, "Commercial Value", 6, 8)], []).cons == ()
    assert texts(select_pros_cons([crit(3, "Commercial Value", 5, 8)], []).cons) == ["Weaker on Value for money (5/10; best bid 8/10)"]


def test_gap_cons_are_ordered_by_weight_times_the_size_of_the_gap():
    first = select_pros_cons([crit(1, "A", 2, 5, weight=30), crit(2, "B", 0, 8, weight=10)], [])      # 30 x 3 = 90 beats 10 x 8 = 80
    assert [r.text.split(" on ")[-1].split(" (")[0] for r in first.cons] == ["A", "B"]
    second = select_pros_cons([crit(1, "A", 2, 5, weight=30), crit(2, "B", 0, 8, weight=12)], [])     # 90 against 96
    assert [r.text.split(" on ")[-1].split(" (")[0] for r in second.cons] == ["B", "A"]


def test_at_most_three_cons():
    criteria = [crit(i, f"C{i}", 0, 9, weight=10 + i) for i in range(1, 6)]
    assert len(select_pros_cons(criteria, [gate("warranty", False, "x")]).cons) == 3


def test_a_criterion_is_listed_once_among_the_cons_and_a_missing_result_is_said_plainly():
    item = crit(4, "Security & Compliance", 0, 8, filled=True, evidence=False)    # behind by 8, evidence not found, and filled with 0
    result = select_pros_cons([item], [])
    assert result.cons == (Reason("No result for Security (counted as 0/10; best bid 8/10)", "vs this batch", "criterion"),)


def test_only_a_criterion_the_validator_filled_with_zero_is_not_stated():
    assert select_pros_cons([crit(4, "Security & Compliance", 0, 0)], []).cons == ()                        # zero, but the model gave it
    assert select_pros_cons([crit(4, "Security & Compliance", 2, 2, filled=True)], []).cons == ()           # flagged, but not zero (hand-edited data)


def test_evidence_not_found_only_counts_when_the_checker_has_said_so():
    assert select_pros_cons([crit(1, "Technical Capability", 8, 8, evidence=None)], []).cons == ()
    assert select_pros_cons([crit(1, "Technical Capability", 8, 8, evidence=True)], []).cons == ()
    found = select_pros_cons([crit(1, "Technical Capability", 8, 8, evidence=False)], []).cons
    assert found == (Reason("Evidence for Laptop specs was not found in the proposal", "evidence", "evidence"),)


def test_a_criterion_can_be_a_pro_and_a_con_at_once_only_through_missing_evidence():
    result = select_pros_cons([crit(1, "Technical Capability", 9, 9, evidence=False)], [])
    assert len(result.pros) == 1 and len(result.cons) == 1


def test_unknown_criterion_names_are_used_as_they_are():
    assert texts(select_pros_cons([crit(9, "Sustainability", 7, 7)], []).pros) == ["Best of all bids on Sustainability (7/10)"]


def test_marks_on_another_scale_are_shown_on_that_scale():
    result = select_pros_cons([crit(5, "Support & Experience", 4, 4, maximum=5.0), crit(1, "Technical Capability", 1.5, 5, maximum=5.0)], [])
    assert texts(result.pros) == ["Strong on Support (4/5, best of all bids)"]
    assert texts(result.cons) == ["Weaker on Laptop specs (1.5/5; best bid 5/5)"]


def test_a_gate_reason_with_line_breaks_stays_on_one_line():
    result = select_pros_cons([], [gate("warranty", False, "1 year\n(at least 3)", name="Warranty\n")])
    assert result.cons[0].text == "Not eligible: Warranty \u2014 1 year (at least 3)"


# ---------------------------------------------------------------- the 5-dot meter (change pack R1b)

@pytest.mark.parametrize("score, maximum, expected", [
    (0, 10, 0), (4, 10, 2), (7, 10, 4), (10, 10, 5),          # the four cases of the change pack
    (5, 5, 5), (3, 5, 3), (0, 5, 0),                          # a maximum of 5
    (8, 10, 4), (9, 10, 5), (2, 10, 1), (1, 10, 1), (0.5, 10, 0), (4.9, 10, 2), (7.5, 10, 4), (6.9, 10, 3),
    (1, 3, 2), (2, 3, 3), (7, 20, 2), (50, 100, 3)])
def test_the_dots_are_the_share_of_the_maximum_times_five_to_the_nearest_dot(score, maximum, expected):
    assert insights.meter_dots(score, maximum) == expected


@pytest.mark.parametrize("score, maximum", [(5, 10), (1, 10), (3, 10), (7, 10), (9, 10), (2.5, 5), (0.5, 1), (1.5, 3)])
def test_exactly_half_a_dot_rounds_up_not_to_the_even_dot(score, maximum):
    """Python's round() would give 2 for 2.5 dots; the meter gives 3, so 50% (Average) never shows fewer dots than 3."""
    assert insights.meter_dots(score, maximum) == int(score / maximum * 5 + 0.5)
    assert insights.meter_dots(5, 10) == 3 and insights.meter_dots(1, 10) == 1


def test_the_dots_agree_with_the_strength_word_at_every_tenth_of_a_point():
    """Weak has 0 to 2 dots, Average 3 or 4, Strong 4 or 5, so a meter never contradicts the word beside it."""
    ranges = {insights.WEAK: (0, 2), insights.AVERAGE: (3, 4), insights.STRONG: (4, 5)}
    for tenths in range(0, 101):
        score = tenths / 10
        low, high = ranges[insights.strength_label(score, 10)]
        dots = insights.meter_dots(score, 10)
        assert dots is not None and low <= dots <= high, (score, dots)
    assert insights.meter_dots(9.9, 10) == 5 and insights.meter_dots(8.9, 10) == 4


def test_dots_never_decrease_when_the_mark_goes_up():
    previous = -1
    for hundredths in range(0, 1001):
        dots = insights.meter_dots(hundredths / 100, 10)
        assert dots is not None and 0 <= dots <= 5 and dots >= previous
        previous = dots


def test_a_mark_outside_the_range_is_clamped_to_empty_or_full():
    assert insights.meter_dots(12, 10) == 5 and insights.meter_dots(-3, 10) == 0


def test_the_number_of_dots_can_be_changed():
    assert insights.METER_DOTS == 5
    assert insights.meter_dots(5, 10, dots=10) == 5 and insights.meter_dots(10, 10, dots=3) == 3 and insights.meter_dots(7, 10, dots=3) == 2


@pytest.mark.parametrize("score, maximum", [
    (None, 10), (5, None), (5, 0), (5, -10), (0, 0), ("5", 10), (5, "10"), (True, 10), (5, True), (float("nan"), 10), (5, float("nan")),
    (float("inf"), 10), (5, float("inf")), ([5], 10)])
def test_no_meter_when_the_mark_or_the_maximum_is_missing_or_not_a_usable_number(score, maximum):
    assert insights.meter_dots(score, maximum) is None


def test_insights_module_stays_pure():
    """Spec 2.1 and 21: no LLM, I/O, randomness or clock in insights.py. Only these imports are allowed."""
    allowed = {"__future__", "collections", "dataclasses", "fractions", "math", "typing", "tools"}      # "tools": the pure ranking engine, for the what-if
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
    tools_imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and n.module.split(".")[0] == "tools"}
    tools_imports |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "tools" for a in n.names}
    assert tools_imports <= {"tools", "ranking"}, "insights.py may import only the pure ranking engine from tools"
    assert not called & {"open", "print", "input", "now", "today", "utcnow", "random"}, sorted(called)
