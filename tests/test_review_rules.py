"""Tests for the review rules (feature F13): `tools/review.py`, plain Python with no database, screen or AI.

The satisfaction question and the priorities rule are tested in `test_feedback_reweight.py`."""
from __future__ import annotations

import math
from datetime import date

import pytest

from tools import insights, ranking, review

CRITERIA = [ranking.CriterionWeight(1, 30, 10), ranking.CriterionWeight(2, 20, 10), ranking.CriterionWeight(3, 20, 10),
            ranking.CriterionWeight(4, 20, 10), ranking.CriterionWeight(5, 10, 10)]
# the spec's ranking fixture (16.1): name, date, experience, marks
FIXTURE = {"Apex": (date(2026, 10, 5), 4, [9, 7, 5, 9, 8]), "BrightPath": (date(2026, 10, 7), 2, [5, 4, 8, 3, 3]),
           "Nexa": (date(2026, 10, 6), 4, [7, 9, 7, 7, 9]), "Horizon": (date(2026, 10, 6), 3, [7, 9, 7, 7, 9]),
           "Orbit": (date(2026, 10, 8), 5, [7, 5, 7, 6, 9])}
DATES = {n: d for n, (d, _r, _s) in FIXTURE.items()}
EXPERIENCE = {n: r for n, (_d, r, _s) in FIXTURE.items()}


def result_of(scores, **extra):
    return {"supplier_name": "x", "keep_me": ["a", {"b": 1}], "warnings": ["a validation warning"], "tie_break_explanation": None,
            "criteria": [{"criterion_id": i, "name": f"C{i}", "score": s, "max_score": 10.0, "benchmark": 0.0, "gap": 0.0,
                          "relative_pct": 0.0, "weight": 0.0, "strength": "", "justification": f"why {i}", "evidence": f"q{i}",
                          "evidence_found": None} for i, s in enumerate(scores, start=1)], **extra}


RESULTS = {n: result_of(s) for n, (_d, _r, s) in FIXTURE.items()}


# ---------------------------------------------------------------- the two re-evaluations and the comment text (the AI re-check)

def test_the_constants_follow_the_spec():
    assert review.MAX_RE_EVALUATIONS == 2 and review.COMMENT_MAX_CHARS == 1000


@pytest.mark.parametrize("attempt, used, left", [(1, 0, 2), (2, 1, 1), (3, 2, 0), (4, 3, 0), (0, 0, 2)])
def test_re_evaluations_are_counted_from_the_attempt_number_and_stop_at_two(attempt, used, left):
    assert review.re_evaluations_used(attempt) == used
    assert review.re_evaluations_left(attempt) == left


def test_text_is_trimmed_and_cut_to_the_longest_allowed():
    assert review.clean_text("  hello  ") == "hello"
    assert review.clean_text(None) == "" and review.clean_text("   ") == ""
    long = "x" * 5000
    assert len(review.clean_text(long)) == review.COMMENT_MAX_CHARS == 1000
    assert review.clean_text("a" * 999 + "  " + "b" * 50) == "a" * 999


def test_the_text_for_the_prompt_names_the_criterion_and_the_comment():
    text = review.feedback_for_prompt(3, "Commercial Value", "  The price table is on page 4.  ")
    assert text == ("The reviewer disagrees with the mark for criterion 3 (Commercial Value).\n"
                    "Reviewer's comment: The price table is on page 4.")


def test_the_text_for_the_prompt_copes_with_no_criterion_name_or_no_criterion():
    assert review.feedback_for_prompt(2, None, "hmm") == "The reviewer disagrees with the mark for criterion 2.\nReviewer's comment: hmm"
    assert review.feedback_for_prompt(None, None, "hmm") == "The reviewer disagrees with this supplier's marks.\nReviewer's comment: hmm"
    assert review.feedback_for_prompt(None, "Price", "hmm") == "The reviewer disagrees with this supplier's marks.\nReviewer's comment: hmm"


def test_the_comment_in_the_prompt_text_is_cut_to_the_longest_allowed():
    text = review.feedback_for_prompt(1, "Price", "y" * 5000)
    assert text.endswith("y" * 1000) and "y" * 1001 not in text


# ---------------------------------------------------------------- the to-do list

def crit(cid, name, evidence_found=None):
    return review.TodoCriterion(cid, name, evidence_found)


def supplier(name, rank, criteria=(), why_not=(), failed=False):
    return review.TodoSupplier(name, rank, tuple(why_not), failed, tuple(criteria))


def test_nothing_to_do_gives_an_empty_list():
    assert review.build_todo([supplier("Apex", 1, [crit(1, "Technical")])]) == ()
    assert review.build_todo([]) == ()


def test_each_kind_of_item_says_what_is_wrong():
    items = review.build_todo([supplier("Apex", 1, [crit(3, "Price", evidence_found=False)], why_not=["Warranty: 1 year (at least 3 required)"])])
    assert [i.kind for i in items] == [review.KIND_GATE, review.KIND_EVIDENCE]
    assert items[0].text == "Warranty: 1 year (at least 3 required)" and items[0].criterion_id is None
    assert items[1].text == "Price: the quoted evidence was not found in the proposal." and items[1].criterion_id == 3
    assert {i.supplier for i in items} == {"Apex"}


def test_items_are_listed_by_kind_then_by_rank_then_by_criterion():
    items = review.build_todo([
        supplier("Second", 2, [crit(2, "B", evidence_found=False), crit(1, "A", evidence_found=False)], why_not=["gate two"]),
        supplier("First", 1, [crit(3, "C", evidence_found=False)], why_not=["gate one"]),
        supplier("Broken", 3, failed=True)])
    assert [(i.kind, i.supplier, i.criterion_id) for i in items] == [
        (review.KIND_FAILED, "Broken", None), (review.KIND_GATE, "First", None), (review.KIND_GATE, "Second", None),
        (review.KIND_EVIDENCE, "First", 3), (review.KIND_EVIDENCE, "Second", 1), (review.KIND_EVIDENCE, "Second", 2)]


def test_every_failed_must_have_is_its_own_item():
    items = review.build_todo([supplier("Apex", 1, why_not=["one", "two", "three"])])
    assert [i.text for i in items] == ["one", "two", "three"] and {i.kind for i in items} == {review.KIND_GATE}


def test_a_failed_evaluation_says_to_run_it_again_and_has_no_gate_items():
    items = review.build_todo([supplier("Apex", 1, why_not=["The evaluation failed (see the warnings on the Scorecards tab)."], failed=True)])
    assert [(i.kind, i.criterion_id) for i in items] == [(review.KIND_FAILED, None)]
    assert items[0].text == "The AI's reply could not be read, so every mark is 0. Run this evaluation again on the New evaluation tab."


def test_evidence_that_was_found_or_not_checked_is_not_on_the_list():
    assert review.build_todo([supplier("Apex", 1, [crit(1, "A", evidence_found=True), crit(2, "B", evidence_found=None)])]) == ()


def test_a_supplier_can_have_several_evidence_items():
    items = review.build_todo([supplier("Apex", 1, [crit(1, "A", evidence_found=False), crit(2, "B", evidence_found=False)])])
    assert [i.criterion_id for i in items] == [1, 2]


def test_the_counts_are_per_supplier():
    items = review.build_todo([supplier("A", 1, why_not=["x", "y"]), supplier("B", 2, why_not=["z"]), supplier("C", 3)])
    assert review.todo_counts(items) == {"A": 2, "B": 1}
    assert review.todo_counts(()) == {}


# ---------------------------------------------------------------- re-ranking

def test_the_marks_are_read_from_the_saved_result():
    assert review.scores_of(RESULTS["Apex"], [1, 2, 3, 4, 5]) == {1: 9.0, 2: 7.0, 3: 5.0, 4: 9.0, 5: 8.0}


@pytest.mark.parametrize("broken", [{"criteria": [{"criterion_id": 1, "score": "9"}]}, {"criteria": [{"criterion_id": 1, "score": True}]},
                                    {"criteria": [{"criterion_id": 1, "score": math.nan}]}, {"criteria": "no"}, {},
                                    {"criteria": [{"criterion_id": "x", "score": 4}]}, {"criteria": [None, 3]}])
def test_a_result_with_a_missing_or_unusable_mark_is_refused(broken):
    with pytest.raises(review.ReviewError, match="no usable mark for criterion"):
        review.scores_of(broken, [1])


def test_ranking_from_saved_results_matches_the_spec_fixture():
    ranked = review.rank_results(RESULTS, CRITERIA, DATES, EXPERIENCE)
    assert [(r.rank, r.name, r.ppi) for r in ranked.suppliers] == [
        (1, "Apex", 86.94), (2, "Nexa", 86.39), (3, "Horizon", 86.39), (4, "Orbit", 75.28), (5, "BrightPath", 55.56)]
    assert ranked.tie_break_explanations == ("Nexa above Horizon: higher experience rating (4 vs 3)",)


def test_a_changed_mark_changes_everyones_benchmark_and_the_order():
    changed = {**RESULTS, "Horizon": result_of([7, 9, 7, 9, 9])}      # Horizon now beats Nexa on security
    ranked = review.rank_results(changed, CRITERIA, DATES, EXPERIENCE)
    assert [(r.rank, r.name, r.ppi) for r in ranked.suppliers][:3] == [(1, "Horizon", 90.83), (2, "Apex", 86.94), (3, "Nexa", 86.39)]
    nexa = next(r for r in ranked.suppliers if r.name == "Nexa")
    assert {c.criterion_id: (c.benchmark, c.relative_pct) for c in nexa.criteria}[4] == (9.0, 77.78)    # Nexa's 7 against the new best 9


def test_new_weights_change_the_order_but_not_the_marks():
    heavier_price = [ranking.CriterionWeight(1, 26.25, 10), ranking.CriterionWeight(2, 17.5, 10), ranking.CriterionWeight(3, 30, 10),
                     ranking.CriterionWeight(4, 17.5, 10), ranking.CriterionWeight(5, 8.75, 10)]
    ranked = review.rank_results(RESULTS, heavier_price, DATES, EXPERIENCE)
    assert [r.name for r in ranked.suppliers][:3] == ["Nexa", "Horizon", "Apex"]          # Apex's weak price now costs it first place
    assert [r.name for r in review.rank_results(RESULTS, CRITERIA, DATES, EXPERIENCE).suppliers][0] == "Apex"
    assert review.scores_of(RESULTS["Apex"], [1, 2, 3, 4, 5]) == {1: 9.0, 2: 7.0, 3: 5.0, 4: 9.0, 5: 8.0}


def test_applying_new_weights_puts_the_new_weight_on_every_criterion_line():
    heavier_price = [ranking.CriterionWeight(1, 26.25, 10), ranking.CriterionWeight(2, 17.5, 10), ranking.CriterionWeight(3, 30, 10),
                     ranking.CriterionWeight(4, 17.5, 10), ranking.CriterionWeight(5, 8.75, 10)]
    updated = review.apply_ranking(RESULTS, review.rank_results(RESULTS, heavier_price, DATES, EXPERIENCE))
    assert [c["weight"] for c in updated["Apex"]["criteria"]] == [26.25, 17.5, 30.0, 17.5, 8.75]
    assert [c["weight"] for c in RESULTS["Apex"]["criteria"]] == [0.0] * 5      # the input is left alone


def test_a_mark_outside_its_range_cannot_be_ranked():
    with pytest.raises(review.ReviewError, match="cannot be ranked"):
        review.rank_results({**RESULTS, "Apex": result_of([99, 7, 5, 9, 8])}, CRITERIA, DATES, EXPERIENCE)


def test_applying_a_ranking_keeps_every_other_field_and_updates_the_numbers():
    ranked = review.rank_results(RESULTS, CRITERIA, DATES, EXPERIENCE)
    updated = review.apply_ranking(RESULTS, ranked)
    apex = updated["Apex"]
    assert apex["keep_me"] == ["a", {"b": 1}] and apex["supplier_name"] == "x"
    assert (apex["rank"], apex["ppi"], apex["absolute_score"]) == (1, 86.94, 77.0)
    first = apex["criteria"][0]
    assert (first["score"], first["max_score"], first["benchmark"], first["gap"], first["relative_pct"], first["weight"]) == (
        9.0, 10.0, 9.0, 0.0, 100.0, 30.0)
    assert first["justification"] == "why 1" and first["evidence"] == "q1" and first["strength"] == "Strong"
    brightpath = updated["BrightPath"]["criteria"][0]
    assert (brightpath["benchmark"], brightpath["gap"], brightpath["relative_pct"], brightpath["strength"]) == (9.0, -4.0, 55.56, "Average")


def test_applying_a_ranking_does_not_change_the_old_results():
    before = repr(RESULTS)
    review.apply_ranking(RESULTS, review.rank_results(RESULTS, CRITERIA, DATES, EXPERIENCE))
    assert repr(RESULTS) == before


def test_the_tie_break_note_goes_on_the_winner_and_is_cleared_elsewhere():
    changed = {**RESULTS, "Horizon": result_of([7, 9, 7, 9, 9])}
    ranked = review.rank_results(RESULTS, CRITERIA, DATES, EXPERIENCE)
    first = review.apply_ranking(RESULTS, ranked)
    assert first["Nexa"]["tie_break_explanation"] == "Nexa above Horizon: higher experience rating (4 vs 3)"
    second = review.apply_ranking(changed, review.rank_results(changed, CRITERIA, DATES, EXPERIENCE))
    assert second["Nexa"]["tie_break_explanation"] is None and second["Horizon"]["tie_break_explanation"] is None


def test_old_ranking_notes_are_replaced_by_the_new_ones_and_other_warnings_stay():
    only = {"Apex": result_of([9, 7, 5, 9, 8], warnings=["old note", "validation note"])}
    only["Apex"]["warnings"] = ["validation note", "old note"]
    ranked = ranking.RankingResult(
        ranking.rank_suppliers([ranking.SupplierInput("Apex", date(2026, 10, 5), 4, {1: 9, 2: 7, 3: 5, 4: 9, 5: 8})], CRITERIA).suppliers,
        (), ("new note",))
    updated = review.apply_ranking(only, ranked, dropped_warnings=["old note"])
    assert updated["Apex"]["warnings"] == ["validation note", "new note"]


def test_a_ranking_note_that_is_already_there_is_not_repeated():
    result = {"Apex": result_of([9, 7, 5, 9, 8])}
    result["Apex"]["warnings"] = ["same note"]
    ranked = ranking.RankingResult(ranking.rank_suppliers(
        [ranking.SupplierInput("Apex", date(2026, 10, 5), 4, {1: 9, 2: 7, 3: 5, 4: 9, 5: 8})], CRITERIA).suppliers, (), ("same note",))
    assert review.apply_ranking(result, ranked)["Apex"]["warnings"] == ["same note"]


# ---------------------------------------------------------------- before and after

def test_standings_are_the_leaderboard_as_plain_data():
    ranked = review.rank_results(RESULTS, CRITERIA, DATES, EXPERIENCE)
    board = review.standings(ranked, {"Apex": True, "Nexa": True, "Horizon": False, "Orbit": True})
    assert board[0] == {"rank": 1, "supplier": "Apex", "ppi": 86.94, "absolute_score": 77.0, "eligible": True}
    assert [(r["rank"], r["supplier"], r["eligible"]) for r in board] == [
        (1, "Apex", True), (2, "Nexa", True), (3, "Horizon", False), (4, "Orbit", True), (5, "BrightPath", False)]


def test_the_recommended_supplier_is_the_best_ranked_eligible_one():
    board = [{"rank": 1, "supplier": "A", "eligible": False}, {"rank": 2, "supplier": "B", "eligible": True},
             {"rank": 3, "supplier": "C", "eligible": True}]
    assert review.recommended_of(board) == "B"
    assert review.recommended_of([{"rank": 1, "supplier": "A", "eligible": False}]) is None
    assert review.recommended_of([]) is None


def test_movement_gives_the_rank_before_and_after():
    before = [{"rank": 1, "supplier": "A"}, {"rank": 2, "supplier": "B"}]
    after = [{"rank": 1, "supplier": "B"}, {"rank": 2, "supplier": "A"}]
    assert review.movement(before, after, "B") == (2, 1)
    assert review.movement(before, after, "Z") == (None, None)


def test_the_sentences_describe_the_marks_that_changed_and_the_move():
    changes = [{"name": "Price", "old": 5.0, "new": 8.0}, {"name": "Plan", "old": 7.0, "new": 7.0}, {"name": "Tech", "old": 9, "new": 7.5}]
    assert review.describe_changes(changes) == "Marks changed: Price 5 to 8; Tech 9 to 7.5."
    assert review.describe_changes([{"name": "Price", "old": 5.0, "new": 5.0}]) == "The AI kept every mark the same."
    assert review.describe_changes([]) == "The AI kept every mark the same."
    assert review.describe_move("Apex", 3, 2) == "Apex moved up from rank 3 to rank 2."
    assert review.describe_move("Apex", 2, 4) == "Apex moved down from rank 2 to rank 4."
    assert review.describe_move("Apex", 2, 2) == "Apex stays at rank 2."
    assert review.describe_move("Apex", None, 2) == "" and review.describe_move("Apex", 2, None) == ""


@pytest.mark.parametrize("before, after, sentence", [
    ("Apex", "Apex", "The recommended supplier is unchanged: Apex."),
    ("Apex", "Nexa", "We now recommend Nexa instead of Apex."),
    (None, "Nexa", "We now recommend Nexa."),
    ("Apex", None, "No supplier meets every must-have now."),
    (None, None, "No supplier meets every must-have.")])
def test_the_recommendation_sentence_says_who_is_recommended_and_whether_that_changed(before, after, sentence):
    assert review.describe_recommendation(before, after) == sentence


def test_the_strength_label_used_after_a_change_is_the_one_the_other_tabs_use():
    assert insights.strength_label(8, 10) == "Strong" and insights.strength_label(7.9, 10) == "Average"
