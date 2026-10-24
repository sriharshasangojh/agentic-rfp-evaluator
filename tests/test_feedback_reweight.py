"""Tests for the satisfaction question and the priorities rule (feature F13, spec 11.2): `tools/reweight.py`, plain Python.

The numbers follow the spec: the chosen criterion gets +10 points (at most 60), every other weight is scaled by the same factor so the
total is exactly 100, rounded to 2 decimals with the remainder on the largest other weight. Applies to one run only."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from tools import reweight
from tools.review import ReviewError

NAMES = {1: "Technical Capability", 2: "Implementation Plan", 3: "Commercial Value", 4: "Security & Compliance", 5: "Support & Experience"}


def scheme(*weights):
    return [SimpleNamespace(criterion_id=i, name=NAMES.get(i, f"Criterion {i}"), weight=w) for i, w in enumerate(weights, start=1)]


SEEDED = scheme(30, 20, 20, 20, 10)


def total(plan):
    return sum(Decimal(str(line.now)) for line in plan.lines)


# ---------------------------------------------------------------- the constants and the scale

def test_the_constants_follow_the_spec_and_the_users_benchmark():
    assert (reweight.MAX_ROUNDS, reweight.STEP, reweight.CAP, reweight.TOTAL) == (3, 10, 60, 100)
    assert reweight.SCALE == (1, 2, 3, 4, 5) and reweight.SATISFIED_AT == 4
    assert reweight.ACTION_REWEIGHT == "reweight"


@pytest.mark.parametrize("score", [1, 2, 3, 4, 5])
def test_every_score_from_1_to_5_is_accepted(score):
    assert reweight.check_satisfaction(score) == score


@pytest.mark.parametrize("bad", [0, 6, -1, 3.5, "4", None, True, False, [4], float("nan")])
def test_anything_else_is_refused(bad):
    with pytest.raises(ReviewError, match="from 1 to 5"):
        reweight.check_satisfaction(bad)


@pytest.mark.parametrize("score, satisfied", [(1, False), (2, False), (3, False), (4, True), (5, True)])
def test_4_and_above_is_satisfied_and_below_4_is_not(score, satisfied):
    assert reweight.is_satisfied(score) is satisfied


def test_a_bad_score_is_refused_when_asking_whether_it_is_satisfied():
    with pytest.raises(ReviewError):
        reweight.is_satisfied(3.5)


# ---------------------------------------------------------------- rounds

def rows(*actions):
    return [SimpleNamespace(action=a) for a in actions]


@pytest.mark.parametrize("actions, used, left", [((), 0, 3), (("reweight",), 1, 2), (("reweight", "reweight"), 2, 1),
                                                 (("reweight", "reweight", "reweight"), 3, 0),
                                                 (("reweight", "reweight", "reweight", "reweight"), 4, 0)])
def test_rounds_are_the_changes_of_priorities_so_far_and_stop_at_three(actions, used, left):
    assert reweight.rounds_used(rows(*actions)) == used
    assert reweight.rounds_left(rows(*actions)) == left


def test_only_a_change_of_priorities_uses_a_round():
    history = rows("accept", "re_evaluate", "override", "rate", "set_aside", "manual_choice", "reweight")
    assert reweight.rounds_used(history) == 1


def test_rounds_can_be_counted_from_any_iterable():
    assert reweight.rounds_used(iter(rows("reweight"))) == 1


# ---------------------------------------------------------------- what the person reads

def test_each_seeded_criterion_has_its_own_plain_sentence_in_marking_scheme_order():
    options = reweight.options_for(SEEDED)
    assert [o.criterion_id for o in options] == [1, 2, 3, 4, 5]
    assert options[2].label == "The price is too high or goes over our budget"
    assert options[0].label == "The laptops should be better (battery life, speed, build quality)"
    assert len({o.label for o in options}) == 5


def test_no_option_uses_the_words_of_the_marking_scheme():
    for option in reweight.options_for(SEEDED):
        assert not any(word in option.label.lower() for word in ("criterion", "weight", "capability", "implementation", "compliance"))


def test_a_criterion_with_another_name_gets_a_generic_sentence_and_names_are_matched_in_any_case():
    options = reweight.options_for([SimpleNamespace(criterion_id=9, name="  Sustainability "), SimpleNamespace(criterion_id=3, name="COMMERCIAL VALUE")])
    assert options[0] == reweight.Option(9, "Sustainability matters more to us")
    assert options[1].label == "The price is too high or goes over our budget"


def test_the_none_of_the_above_choice_is_worded_for_a_buyer():
    assert reweight.NONE_OF_THE_ABOVE.startswith("None of the above")


@pytest.mark.parametrize("text, expected", [("  I want   more\n battery life ", "I want more battery life"), (None, ""), ("", ""),
                                             ("x" * 500, "x" * 300), ("a " * 400, ("a " * 150).strip())])
def test_a_typed_need_is_tidied_and_cut_to_300_characters(text, expected):
    assert reweight.clean_need(text) == expected


# ---------------------------------------------------------------- the reweight rule

def test_the_chosen_criterion_gets_ten_points_and_the_others_share_the_rest_proportionally():
    plan = reweight.plan_reweight(SEEDED, 3)           # price: 20 -> 30
    now = plan.new_weights()
    assert now[3] == 30.0
    assert [now[i] for i in (1, 2, 4, 5)] == [26.25, 17.5, 17.5, 8.75]       # 70 shared as 30:20:20:10 of 80
    assert total(plan) == 100


def test_the_plan_remembers_the_old_weight_next_to_the_new_one():
    plan = reweight.plan_reweight(SEEDED, 3)
    assert [(l.criterion_id, l.name, l.was, l.now) for l in plan.lines] == [
        (1, "Technical Capability", 30.0, 26.25), (2, "Implementation Plan", 20.0, 17.5), (3, "Commercial Value", 20.0, 30.0),
        (4, "Security & Compliance", 20.0, 17.5), (5, "Support & Experience", 10.0, 8.75)]
    assert plan.target.name == "Commercial Value" and plan.target_id == 3


def test_the_input_is_not_changed():
    reweight.plan_reweight(SEEDED, 3)
    assert [c.weight for c in SEEDED] == [30, 20, 20, 20, 10]


@pytest.mark.parametrize("target", [1, 2, 3, 4, 5])
def test_the_total_is_exactly_100_whichever_criterion_is_chosen(target):
    plan = reweight.plan_reweight(SEEDED, target)
    assert total(plan) == 100
    assert plan.target.now == plan.target.was + 10


def test_the_total_stays_exactly_100_when_the_scaling_does_not_divide_evenly():
    plan = reweight.plan_reweight(scheme(30, 20, 20, 20, 10), 5)       # others get 50 of 90: 16.666..., 11.111..., 5.555...
    assert total(plan) == 100
    assert plan.new_weights()[5] == 20.0


def test_a_rounding_remainder_that_is_too_much_is_taken_off_the_largest_other_weight():
    plan = reweight.plan_reweight(scheme(40, 20, 20, 20), 1)          # 50 left for three equal 20s: 16.67 x 3 = 50.01, so 0.01 comes off one
    now = plan.new_weights()
    assert now[1] == 50.0
    assert (now[2], now[3], now[4]) == (16.66, 16.67, 16.67)
    assert total(plan) == 100


def test_a_rounding_remainder_that_is_missing_is_added_to_the_largest_other_weight():
    plan = reweight.plan_reweight(scheme(55, 15, 15, 15), 1)          # capped at 60; 40 left: 13.33 x 3 = 39.99, so 0.01 is added to one
    now = plan.new_weights()
    assert now[1] == 60.0
    assert (now[2], now[3], now[4]) == (13.34, 13.33, 13.33)
    assert total(plan) == 100


def test_the_remainder_goes_on_the_one_criterion_with_the_largest_weight_not_the_first_one():
    plan = reweight.plan_reweight(scheme(40, 10, 40, 10), 1)          # others 10, 40, 10 share 50: 8.33, 33.33, 8.33 = 49.99
    now = plan.new_weights()
    assert (now[2], now[3], now[4]) == (8.33, 33.34, 8.33)
    assert total(plan) == 100


def test_a_tie_for_the_largest_other_weight_goes_to_the_lowest_criterion_id():
    plan = reweight.plan_reweight(scheme(10, 30, 30, 30), 1)          # 80 left for three equal 30s: 26.67 x 3 = 80.01
    now = plan.new_weights()
    assert now[1] == 20.0
    assert (now[2], now[3], now[4]) == (26.66, 26.67, 26.67)
    assert total(plan) == 100


def test_the_new_weight_never_goes_above_60():
    plan = reweight.plan_reweight(scheme(55, 15, 15, 15), 1)          # 55 + 10 would be 65
    assert plan.new_weights()[1] == 60.0
    plan = reweight.plan_reweight(scheme(52.5, 15, 15, 17.5), 1)
    assert plan.new_weights()[1] == 60.0 and total(plan) == 100


def test_the_cap_is_reached_exactly_at_50_plus_10():
    plan = reweight.plan_reweight(scheme(50, 20, 20, 10), 1)
    assert plan.new_weights()[1] == 60.0 and total(plan) == 100


@pytest.mark.parametrize("weights", [(60, 20, 10, 10), (65, 15, 10, 10), (100, 0, 0, 0)])
def test_a_criterion_already_at_the_cap_or_above_is_refused_and_not_lowered(weights):
    with pytest.raises(ReviewError, match="already counts for as much as it can"):
        reweight.plan_reweight(scheme(*weights), 1)


def test_the_refusal_names_the_criterion():
    with pytest.raises(ReviewError, match="Technical Capability already counts"):
        reweight.plan_reweight(scheme(60, 40), 1)


def test_a_criterion_that_is_not_in_the_run_is_refused():
    with pytest.raises(ReviewError, match="not part of this run"):
        reweight.plan_reweight(SEEDED, 99)


def test_with_no_other_weight_to_take_from_the_priorities_cannot_change():
    with pytest.raises(ReviewError, match="no other criterion"):
        reweight.plan_reweight(scheme(40), 1)
    with pytest.raises(ReviewError, match="no other criterion"):
        reweight.plan_reweight(scheme(40, 0, 0), 1)


def test_a_zero_weight_stays_zero():
    plan = reweight.plan_reweight(scheme(30, 0, 40, 30), 1)
    assert plan.new_weights()[2] == 0.0 and total(plan) == 100


def test_a_total_that_was_not_exactly_100_is_brought_to_exactly_100():
    plan = reweight.plan_reweight(scheme(25, 25, 25, 24.99), 1)
    assert total(plan) == 100 and plan.new_weights()[1] == 35.0


def test_weights_with_decimals_are_kept_exact():
    plan = reweight.plan_reweight(scheme(32.5, 22.5, 22.5, 22.5), 1)
    assert plan.new_weights()[1] == 42.5 and total(plan) == 100


def test_two_rounds_in_a_row_work_from_the_weights_of_the_round_before():
    first = reweight.plan_reweight(SEEDED, 3)
    second = reweight.plan_reweight([SimpleNamespace(criterion_id=l.criterion_id, name=l.name, weight=l.now) for l in first.lines], 1)
    assert second.new_weights()[1] == 36.25                       # 26.25 + 10
    assert second.new_weights()[3] == 25.93                       # 30 scaled by 63.75 / 73.75
    assert total(second) == 100


def test_the_same_input_always_gives_the_same_output():
    assert reweight.plan_reweight(SEEDED, 4) == reweight.plan_reweight(list(SEEDED), 4)


# ---------------------------------------------------------------- the words

@pytest.mark.parametrize("value, text", [(20.0, "20"), (17.5, "17.5"), (17.14, "17.14"), (26.25, "26.25"), (8.75, "8.75"), (60, "60"),
                                         (0.0, "0"), (33.333333, "33.33"), (100.0, "100"), (0.005, "0.01")])
def test_a_weight_is_written_the_way_people_write_it(value, text):
    assert reweight.percent(value) == text


def test_the_sentence_says_what_counts_for_more_and_by_how_much():
    assert reweight.describe_reweighting(reweight.plan_reweight(SEEDED, 3)) == \
        "Commercial Value now counts for 30% of the decision (it was 20%)."
    assert reweight.describe_reweighting(reweight.plan_reweight(scheme(32.5, 22.5, 22.5, 22.5), 2)) == \
        "Implementation Plan now counts for 32.5% of the decision (it was 22.5%)."


def test_what_the_ai_may_propose_but_this_version_cannot_do_is_explained_in_plain_words():
    assert set(reweight.UNSUPPORTED_ACTIONS) == {"re_evaluate", "set_aside"}
    assert all(len(text) > 20 and "criterion" not in text.lower() for text in reweight.UNSUPPORTED_ACTIONS.values())
