"""Review flow tests (feature F13): satisfaction and priorities, run against a real batch with a fake model.

Every test starts from a batch that went through the real pipeline (`review_support.evaluated_run`), then asks the orchestrator to
accept the recommendation, change the priorities or read a typed need, and reads back what was saved. The fake
model returns prepared replies, so no API key or network is used. After the actions the saved ranking is compared with a fresh ranking
of the saved marks and the run's weights, so the two cannot drift.

The Review tab asks "how satisfied are you, 1 to 5?" and offers accept or change priorities. (Asking the AI to read one supplier again
was removed when "Your priorities" replaced the planned F19: marks are the AI's reading and are not changed afterwards.)
"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from agents import orchestrator
from db import repository, seed
from review_support import (BAD_FACTS, KEY, NAMES, SENTINEL, SETTINGS, Clock, FakeBackend, evaluated_run, marks,
                            pdf_for, reply, response, rows, saved, whole_database)
from tools import industry, insights, ranking, reweight
from ui import tab_audit, tab_overview

TECH, PLAN, PRICE, SECURITY, SUPPORT = 1, 2, 3, 4, 5
APEX, BRIGHT, NEXA, HORIZON, ORBIT = NAMES
COMMENT = "The price table is on page 4."


@pytest.fixture(autouse=True)
def seeded_database():
    seed.ensure_database()


@pytest.fixture
def run_id() -> str:
    return evaluated_run().run_id


@pytest.fixture
def clock() -> Clock:
    return Clock()






def assert_consistent(run_id: str) -> None:
    """The saved ranking, benchmarks, gaps, relative % and strength labels are exactly what a fresh ranking of the saved marks gives."""
    with repository.connection() as conn:
        run = repository.get_run(conn, run_id)
        stored = repository.list_supplier_results(conn, run_id)
    assert run is not None
    weights = [ranking.CriterionWeight(c["criterion_id"], c["weight"], c["max_score"]) for c in run.criteria_snapshot]
    inputs = [ranking.SupplierInput(s.supplier_name, s.submission_date, s.experience_rating,
                                    {c["criterion_id"]: c["score"] for c in s.result["criteria"]}) for s in stored]
    fresh = ranking.rank_suppliers(inputs, weights)
    by_name = {s.supplier_name: s for s in stored}
    assert sorted(s.final_rank for s in stored) == list(range(1, len(stored) + 1))
    for r in fresh.suppliers:
        s = by_name[r.name]
        assert (s.final_rank, s.ppi, s.absolute_score) == (r.rank, r.ppi, r.absolute_score), r.name
        assert (s.result["rank"], s.result["ppi"], s.result["absolute_score"]) == (r.rank, r.ppi, r.absolute_score)
        for cr, item in zip(r.criteria, s.result["criteria"]):
            assert (item["score"], item["benchmark"], item["gap"], item["relative_pct"], item["weight"]) == (
                cr.score, cr.benchmark, cr.gap, cr.relative_pct, cr.weight), (r.name, cr.criterion_id)
            assert item["strength"] == insights.strength_label(cr.score, cr.max_score)
        assert s.result.get("tie_break_explanation") == next(
            (e for e in fresh.tie_break_explanations if e.startswith(f"{r.name} above ")), None)


def overview_args(run_id):
    with repository.connection() as conn:
        return repository.get_run(conn, run_id), repository.list_supplier_results(conn, run_id)


NEED = "I want a laptop that lasts a full working day"
READING = "I understood: battery life matters more to you."
ROUND_ONE_WEIGHTS = [24.37, 16.25, 35.0, 16.25, 8.13]           # price raised from 20 to 35
ROUND_TWO_WEIGHTS = [39.37, 13.03, 28.05, 13.03, 6.52]         # then technical raised from 24.37 to 39.37


def priorities(run_id, clock, satisfaction=2, criterion=PRICE, round_no=1, typed="", actor="Sri", **extra):
    return orchestrator.change_priorities(run_id, satisfaction, criterion, round_no, typed, actor, now=clock, **extra)


def accept(run_id, clock, satisfaction=5, round_no=1, actor="Sri"):
    return orchestrator.accept_recommendation(run_id, satisfaction, round_no, actor, now=clock)


def ai_reply(action="reweight", criterion_id=TECH, reading=READING, supplier=None) -> str:
    return json.dumps({"action": action, "supplier": supplier, "criterion_id": criterion_id, "reading": reading})


def understand(run_id, *replies, text=NEED, settings=SETTINGS, actor="Sri"):
    backend = FakeBackend(*(replies or [ai_reply()]))
    return orchestrator.understand_need(run_id, text, settings, actor, backend=backend, sleep=lambda _s: None), backend


def order(run_id):
    return [(s.final_rank, n) for n, s in sorted(saved(run_id).items(), key=lambda kv: kv[1].final_rank)]


def weights_of(run_id):
    with repository.connection() as conn:
        return [c["weight"] for c in repository.get_run(conn, run_id).criteria_snapshot]


# ---------------------------------------------------------------- the history tables

def test_a_reviewer_action_is_stored_with_its_details_and_read_back_in_order():
    result = evaluated_run()
    with repository.connection() as conn:
        first = repository.add_feedback(conn, result.run_id, action="override", created_at="2026-10-23T10:00:00+00:00", supplier_name=APEX,
                                        criterion_id=3, attempt_no=1, comment="why", override_score=7.5, details={"by": "Sri", "x": [1]})
        second = repository.add_feedback(conn, result.run_id, action="accept", created_at="2026-10-23T10:01:00+00:00")
        found = repository.list_feedback(conn, result.run_id)
    assert second > first and [f.id for f in found] == [first, second]
    assert found[0] == repository.FeedbackRow(first, APEX, 3, 1, None, "why", "override", 7.5, {"by": "Sri", "x": [1]}, "2026-10-23T10:00:00+00:00")
    assert (found[1].supplier_name, found[1].criterion_id, found[1].rating, found[1].details) == (None, None, None, {})


def test_feedback_with_unreadable_details_still_comes_back():
    result = evaluated_run()
    with repository.connection() as conn:
        conn.execute("INSERT INTO reviewer_feedback (rfp_run_id, comment, action, details_json, created_at) VALUES (?, '', 'accept', '{oops', 'x')",
                     (result.run_id,))
        assert repository.list_feedback(conn, result.run_id)[0].details == {}


def test_feedback_only_allows_the_known_actions_and_a_rating_from_1_to_5_when_there_is_one():
    result = evaluated_run()
    with repository.connection() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            repository.add_feedback(conn, result.run_id, action="delete", created_at="x")
        with pytest.raises(sqlite3.IntegrityError):
            repository.add_feedback(conn, result.run_id, action="accept", created_at="x", rating=6)
        with pytest.raises(sqlite3.IntegrityError):
            repository.add_feedback(conn, "RUN-NOPE", action="accept", created_at="x")   # needs an existing run


@pytest.mark.parametrize("table, column", [("evaluation_attempts", "score"), ("reviewer_feedback", "comment")])
def test_attempts_and_feedback_can_be_added_but_never_changed_or_removed(table, column):
    result = evaluated_run()
    with repository.connection() as conn:
        if table == "reviewer_feedback":
            repository.add_feedback(conn, result.run_id, action="accept", created_at="x", comment="keep me")
    with repository.connection() as conn:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute(f"UPDATE {table} SET {column} = {column}")
    with repository.connection() as conn:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute(f"DELETE FROM {table}")
    assert rows(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] > 0


def test_a_database_made_before_f13_gets_the_append_only_rules_on_the_next_start():
    evaluated_run()
    with repository.connection() as conn:
        for name in ("attempts_no_update", "attempts_no_delete", "feedback_no_update", "feedback_no_delete"):
            conn.execute(f"DROP TRIGGER {name}")
        conn.execute("DELETE FROM reviewer_feedback")          # allowed while the rules are missing
    seed.ensure_database()
    names = {r["name"] for r in rows("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert {"attempts_no_update", "attempts_no_delete", "feedback_no_update", "feedback_no_delete", "audit_log_no_update"} <= names


def test_attempts_are_listed_by_supplier_attempt_and_criterion_with_their_time():
    result = evaluated_run()
    with repository.connection() as conn:
        attempts = repository.list_attempts(conn, result.run_id)
        assert repository.current_attempt_no(conn, result.run_id, APEX) == 1
        assert repository.current_attempt_no(conn, result.run_id, "Nobody") == 0
    assert len(attempts) == 25 and {a.attempt_no for a in attempts} == {1} and {a.evidence_found for a in attempts} == {True}
    assert [(a.supplier_name, a.criterion_id) for a in attempts] == sorted((a.supplier_name, a.criterion_id) for a in attempts)
    assert attempts[0].supplier_name == APEX and attempts[0].created_at.startswith("2026-10-23T09:30")


def test_a_supplier_row_is_updated_in_place_and_an_unknown_one_is_refused():
    result = evaluated_run()
    with repository.connection() as conn:
        repository.update_supplier_result(conn, run_id=result.run_id, supplier_name=APEX, absolute_score=1.5, ppi=2.5, industry_score=3.5,
                                          final_rank=4, gates_passed=False, result_json='{"a": 1}')
        with pytest.raises(sqlite3.DatabaseError, match="No saved result for Nobody"):
            repository.update_supplier_result(conn, run_id=result.run_id, supplier_name="Nobody", absolute_score=0, ppi=0, industry_score=0,
                                              final_rank=1, gates_passed=True, result_json="{}")
    apex = saved(result.run_id)[APEX]
    assert (apex.absolute_score, apex.ppi, apex.industry_score, apex.final_rank, apex.gates_passed, apex.result) == (1.5, 2.5, 3.5, 4, False, {"a": 1})


@pytest.mark.parametrize("before, add, expected", [((1000, 0.5), (500, 0.25), (1500, 0.75)), ((1000, 0.5), (None, None), (1000, 0.5)),
                                                   ((None, None), (500, 0.25), (500, 0.25)), ((None, None), (None, None), (None, None)),
                                                   ((1000, None), (None, 0.25), (1000, 0.25))])
def test_the_cost_of_a_later_call_is_added_to_the_run_totals_and_unknown_stays_unknown(before, add, expected):
    result = evaluated_run()
    with repository.connection() as conn:
        repository.finish_run(conn, result.run_id, status="Evaluated", total_tokens=before[0], total_cost_usd=before[1], duration_seconds=3)
        repository.add_to_run_totals(conn, result.run_id, *add)
        run = repository.get_run(conn, result.run_id)
    assert (run.total_tokens, run.total_cost_usd) == expected












def test_the_first_evaluation_still_sends_none_as_the_feedback():
    backend = FakeBackend(reply([5, 5, 5, 5, 5]))
    from agents import evaluator
    with repository.connection() as conn:
        criteria = repository.list_criteria(conn, active_only=True)
    evaluator.evaluate_supplier("Apex Systems", "text", criteria, evaluated_terms(), SETTINGS, backend=backend, sleep=lambda _s: None)
    assert "<reviewer_feedback>\nNone\n</reviewer_feedback>" in backend.sent_text()


def evaluated_terms():
    from tools import industry
    return industry.PurchaseTerms(6000000, 100)




































def snapshot_of(run_id: str):
    return {"results": rows("SELECT * FROM supplier_results ORDER BY supplier_name"), "attempts": rows("SELECT * FROM evaluation_attempts"),
            "feedback": rows("SELECT * FROM reviewer_feedback"), "run": rows("SELECT * FROM rfp_runs")}
















# ---------------------------------------------------------------- the weights of a run

def test_a_runs_weights_can_be_saved_and_an_unknown_run_is_refused(run_id):
    with repository.connection() as conn:
        snapshot = repository.get_run(conn, run_id).criteria_snapshot
        repository.update_run_weights(conn, run_id, json.dumps([{**snapshot[0], "weight": 12.5}]))
        assert repository.get_run(conn, run_id).criteria_snapshot == [{**snapshot[0], "weight": 12.5}]
        with pytest.raises(sqlite3.DatabaseError, match="No run RUN-NOPE"):
            repository.update_run_weights(conn, "RUN-NOPE", "[]")


def test_the_action_names_match_between_the_rules_and_the_database():
    assert reweight.ACTION_REWEIGHT == repository.ACTION_REWEIGHT == "reweight"
    assert repository.ACTION_ACCEPT == "accept" and repository.RUN_FINAL == "Final"


# ---------------------------------------------------------------- not satisfied: a criterion counts for more

def test_not_being_satisfied_raises_the_chosen_criterion_and_ranks_everyone_again(run_id, clock):
    assert order(run_id)[:3] == [(1, APEX), (2, NEXA), (3, HORIZON)]
    out = priorities(run_id, clock)
    assert out.ok
    assert out.message == ("Commercial Value now counts for 35% of the decision (it was 20%). "
                           "We now recommend NexaWorks instead of Apex Systems.")
    assert weights_of(run_id) == ROUND_ONE_WEIGHTS and sum(weights_of(run_id)) == 100
    assert order(run_id) == [(1, NEXA), (2, HORIZON), (3, APEX), (4, ORBIT), (5, BRIGHT)]
    assert {n: s.ppi for n, s in saved(run_id).items() if n in (NEXA, APEX)} == {APEX: 82.36, NEXA: 86.6}
    assert_consistent(run_id)


def test_the_marks_reasons_evidence_attempts_and_must_haves_are_not_touched(run_id, clock):
    before = snapshot_of(run_id)
    old = {n: s.result for n, s in saved(run_id).items()}
    assert priorities(run_id, clock).ok
    after = snapshot_of(run_id)
    assert after["attempts"] == before["attempts"]
    for name, stored in saved(run_id).items():
        for new_line, old_line in zip(stored.result["criteria"], old[name]["criteria"]):
            for field in ("criterion_id", "name", "score", "max_score", "justification", "evidence", "evidence_found"):
                assert new_line[field] == old_line[field], (name, field)
        for field in ("eligible", "gates", "gate_details", "extracted_facts", "risks", "overall_summary", "file_name", "submission_date",
                      "experience_rating", "document_sha256", "evaluation_failed"):
            assert stored.result[field] == old[name][field], (name, field)
    assert [r["gates_passed"] for r in after["results"]] == [r["gates_passed"] for r in before["results"]]
    assert KEY not in whole_database()


def test_the_industry_score_is_worked_out_again_with_the_new_weights(run_id, clock):
    assert {n: s.industry_score for n, s in saved(run_id).items()} == {APEX: 51.25, BRIGHT: 16.25, NEXA: 51.25, HORIZON: 51.25, ORBIT: 51.25}
    assert priorities(run_id, clock).ok
    run, stored = overview_args(run_id)
    with repository.connection() as conn:
        benchmarks = repository.list_benchmarks(conn, run.template_id)
    criteria = [SimpleNamespace(criterion_id=c["criterion_id"], weight=c["weight"]) for c in run.criteria_snapshot]
    for s in stored:
        fresh = industry.industry_score(s.result["extracted_facts"], benchmarks, criteria, industry.PurchaseTerms(run.budget_rs, run.quantity))
        assert s.industry_score == s.result["industry_score"] == s.result["industry_breakdown"]["score"] == fresh.score
        assert [a["weight"] for a in s.result["industry_breakdown"]["attributes"]] == [a.weight for a in fresh.attributes]
    assert {n: s.industry_score for n, s in saved(run_id).items()} == {APEX: 55.7, BRIGHT: 13.2, NEXA: 55.7, HORIZON: 55.7, ORBIT: 55.7}


def test_the_decision_is_recorded_with_the_old_and_new_weights_and_the_old_and_new_winner(run_id, clock):
    assert priorities(run_id, clock, satisfaction=2, actor="Asha").ok
    [row] = rows("SELECT * FROM reviewer_feedback")
    assert (row["action"], row["supplier_name"], row["criterion_id"], row["attempt_no"], row["rating"], row["override_score"]) == (
        "reweight", None, PRICE, None, 2, None)
    assert row["comment"] == "The price is too high or goes over our budget"
    details = json.loads(row["details_json"])
    assert details["weights"] == [{"criterion_id": i, "name": n, "was": w, "now": now} for (i, n), w, now in zip(
        [(1, "Technical Capability"), (2, "Implementation Plan"), (3, "Commercial Value"), (4, "Security & Compliance"),
         (5, "Support & Experience")], [30.0, 20.0, 20.0, 20.0, 10.0], ROUND_ONE_WEIGHTS)]
    assert (details["round"], details["satisfaction"], details["how"], details["typed"], details["reading"], details["by"]) == (
        1, 2, "option", "", "", "Asha")
    assert details["option"] == "The price is too high or goes over our budget" and details["proposed_criterion_id"] is None
    assert (details["recommended_before"], details["recommended_after"]) == (APEX, NEXA)
    assert [(r["rank"], r["supplier"]) for r in details["before"]] == [(1, APEX), (2, NEXA), (3, HORIZON), (4, ORBIT), (5, BRIGHT)]
    assert [(r["rank"], r["supplier"]) for r in details["after"]] == [(1, NEXA), (2, HORIZON), (3, APEX), (4, ORBIT), (5, BRIGHT)]
    assert details["before"][0]["ppi"] == 86.94 and details["after"][0]["ppi"] == 86.6
    assert [r["eligible"] for r in details["after"]] == [True, True, True, True, False]


def test_the_audit_trail_gets_the_decision_and_the_new_ranking(run_id, clock):
    assert priorities(run_id, clock, actor="Asha").ok
    [event] = rows("SELECT * FROM audit_log WHERE action = 'priorities_changed'")
    assert event["actor"] == "Asha"
    details = json.loads(event["details_json"])
    assert (details["round"], details["satisfaction"], details["criterion_id"], details["criterion"], details["how"]) == (
        1, 2, PRICE, "Commercial Value", "option")
    assert (details["recommended_before"], details["recommended_after"]) == (APEX, NEXA)
    assert [w["now"] for w in details["weights"]] == ROUND_ONE_WEIGHTS
    last = json.loads(rows("SELECT details_json FROM audit_log WHERE action = 'ranking_computed' ORDER BY id DESC LIMIT 1")[0]["details_json"])
    assert last["because"] == "Commercial Value counts for more (round 1)" and last["top3"][0]["supplier"] == NEXA


def test_a_typed_need_is_kept_in_the_persons_words_with_what_the_ai_proposed(run_id, clock):
    assert priorities(run_id, clock, 3, TECH, 1, typed="  I want a laptop\n that lasts   a full working day ", reading=READING,
                      proposed_id=TECH).ok
    [row] = rows("SELECT * FROM reviewer_feedback")
    assert row["comment"] == NEED and row["criterion_id"] == TECH and row["rating"] == 3
    details = json.loads(row["details_json"])
    assert (details["how"], details["typed"], details["reading"], details["proposed_criterion_id"]) == ("typed", NEED, READING, TECH)
    assert details["option"] == "The laptops should be better (battery life, speed, build quality)"
    assert weights_of(run_id)[0] == 45.0
    assert json.loads(rows("SELECT details_json FROM audit_log WHERE action = 'priorities_changed'")[0]["details_json"])["reason"] == NEED


def test_the_person_may_change_what_the_ai_proposed(run_id, clock):
    assert priorities(run_id, clock, 2, PRICE, 1, typed="It costs too much", reading=READING, proposed_id=TECH).ok
    details = json.loads(rows("SELECT details_json FROM reviewer_feedback")[0]["details_json"])
    assert (details["proposed_criterion_id"], details["criterion"], details["how"]) == (TECH, "Commercial Value", "typed")
    assert weights_of(run_id)[2] == 35.0


def test_a_long_typed_need_is_cut_to_300_characters(run_id, clock):
    assert priorities(run_id, clock, typed="x" * 900).ok
    assert len(rows("SELECT comment FROM reviewer_feedback")[0]["comment"]) == 300


def test_each_criterion_can_be_raised(run_id, clock):
    for criterion in (TECH, PLAN, PRICE, SECURITY, SUPPORT):
        evaluated = evaluated_run().run_id
        out = priorities(evaluated, clock, criterion=criterion)
        assert out.ok and weights_of(evaluated)[criterion - 1] == [45.0, 35.0, 35.0, 35.0, 25.0][criterion - 1]
        assert sum(weights_of(evaluated)) == 100
        assert_consistent(evaluated)


def test_the_second_round_starts_from_the_weights_of_the_first(run_id, clock):
    assert priorities(run_id, clock, 2, PRICE, 1).ok
    out = priorities(run_id, clock, 3, TECH, 2)
    assert out.ok and out.message == ("Technical Capability now counts for 39.37% of the decision (it was 24.37%). "
                                      "We now recommend Apex Systems instead of NexaWorks.")
    assert weights_of(run_id) == ROUND_TWO_WEIGHTS and sum(weights_of(run_id)) == 100
    assert [json.loads(r["details_json"])["round"] for r in rows("SELECT details_json FROM reviewer_feedback ORDER BY id")] == [1, 2]
    assert_consistent(run_id)


def test_there_are_three_rounds_and_not_a_fourth(run_id, clock):
    assert priorities(run_id, clock, round_no=1).ok and priorities(run_id, clock, criterion=TECH, round_no=2).ok
    assert priorities(run_id, clock, criterion=SECURITY, round_no=3).ok
    before = snapshot_of(run_id)
    out = priorities(run_id, clock, criterion=SUPPORT, round_no=4)
    assert not out.ok and out.message == orchestrator.ROUNDS_USED_TEXT
    assert "All 3 rounds" in out.message and "you can still accept" in out.message
    assert snapshot_of(run_id) == before


def test_a_round_that_ends_with_the_same_winner_says_so(run_id, clock):
    out = priorities(run_id, clock, criterion=TECH)
    assert out.ok and out.message.endswith("The recommended supplier is unchanged: Apex Systems.")


@pytest.mark.parametrize("score", [4, 5])
def test_a_person_who_is_satisfied_does_not_change_the_priorities(run_id, clock, score):
    before = snapshot_of(run_id)
    out = priorities(run_id, clock, satisfaction=score)
    assert not out.ok and out.message == ("A satisfaction of 4 or higher means you are satisfied, so the priorities stay as they are. "
                                          "Accept the recommendation instead.")
    assert snapshot_of(run_id) == before


@pytest.mark.parametrize("bad", [0, 6, 3.5, "2", None, True])
def test_a_satisfaction_that_is_not_1_to_5_is_refused(run_id, clock, bad):
    before = snapshot_of(run_id)
    out = priorities(run_id, clock, satisfaction=bad)
    assert not out.ok and out.message == "Choose how satisfied you are, from 1 to 5."
    assert snapshot_of(run_id) == before


@pytest.mark.parametrize("round_no", [0, 2, 3, "1", None])
def test_a_question_that_is_not_the_one_the_run_is_on_is_refused(run_id, clock, round_no):
    before = snapshot_of(run_id)
    out = priorities(run_id, clock, round_no=round_no)
    assert not out.ok and out.message == orchestrator.ANSWERED_TEXT
    assert snapshot_of(run_id) == before


def test_a_second_click_on_the_same_question_does_nothing(run_id, clock):
    assert priorities(run_id, clock, round_no=1).ok
    after_first = snapshot_of(run_id)
    out = priorities(run_id, clock, round_no=1)
    assert not out.ok and out.message == orchestrator.ANSWERED_TEXT
    assert snapshot_of(run_id) == after_first and weights_of(run_id) == ROUND_ONE_WEIGHTS


def test_an_unknown_criterion_is_refused(run_id, clock):
    before = snapshot_of(run_id)
    out = priorities(run_id, clock, criterion=99)
    assert not out.ok and out.message == "That criterion is not part of this run."
    assert snapshot_of(run_id) == before


def test_a_criterion_that_already_counts_for_as_much_as_it_may_is_refused(run_id, clock):
    with repository.connection() as conn:
        snapshot = repository.get_run(conn, run_id).criteria_snapshot
        repository.update_run_weights(conn, run_id, json.dumps([{**c, "weight": w} for c, w in zip(snapshot, [60, 10, 10, 10, 10])]))
    before = snapshot_of(run_id)
    out = priorities(run_id, clock, criterion=TECH)
    assert not out.ok and out.message == "Technical Capability already counts for as much as it can (60% of the decision). Pick something else."
    assert snapshot_of(run_id) == before
    assert priorities(run_id, clock, criterion=PRICE).ok          # the others can still be raised, and the cap holds
    assert weights_of(run_id)[0] < 60 and sum(weights_of(run_id)) == 100


def test_an_unknown_run_is_refused(clock):
    evaluated_run()
    for out in (orchestrator.change_priorities("RUN-NOPE", 2, PRICE, 1, now=clock), orchestrator.accept_recommendation("RUN-NOPE", 5, 1, now=clock)):
        assert not out.ok and out.message == "This run was not found."


@pytest.mark.parametrize("status", ["Failed", "Interrupted", "Running", "Final"])
def test_a_run_that_is_not_evaluated_cannot_be_changed(run_id, clock, status):
    with repository.connection() as conn:
        repository.set_run_status(conn, run_id, status)
    for out in (priorities(run_id, clock), accept(run_id, clock)):
        assert not out.ok and out.message == f"This run is {status.lower()}, so it can no longer be changed."
    assert rows("SELECT * FROM reviewer_feedback") == []


def test_a_run_without_its_budget_keeps_its_priorities(run_id, clock):
    with repository.connection() as conn:
        conn.execute("UPDATE rfp_runs SET budget_rs = NULL")
    before = snapshot_of(run_id)
    out = priorities(run_id, clock)
    assert not out.ok and out.message == ("This run did not save its budget and quantity, so the industry score cannot be worked out "
                                          "again with the new priorities.")
    assert snapshot_of(run_id) == before


def test_a_database_that_is_locked_gives_a_friendly_message_and_changes_nothing(run_id, clock, monkeypatch):
    before = snapshot_of(run_id)

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repository, "add_feedback", broken)
    expected = ("The change could not be saved because the database was busy or unavailable. Nothing was changed. "
                "Wait a few seconds and try again.")
    for out in (priorities(run_id, clock), accept(run_id, clock)):
        assert not out.ok and out.message == expected
    assert snapshot_of(run_id) == before and weights_of(run_id) == [30.0, 20.0, 20.0, 20.0, 10.0]
    assert rows("SELECT * FROM audit_log WHERE action IN ('priorities_changed', 'recommendation_accepted')") == []


def test_an_empty_actor_is_recorded_as_the_reviewer(run_id, clock):
    assert priorities(run_id, clock, actor="").ok and accept(run_id, clock, round_no=2, actor="").ok
    assert {r["actor"] for r in rows("SELECT actor FROM audit_log WHERE action IN ('priorities_changed', 'recommendation_accepted')")} == {"reviewer"}
    assert [json.loads(r["details_json"])["by"] for r in rows("SELECT details_json FROM reviewer_feedback ORDER BY id")] == ["reviewer", "reviewer"]


def test_a_supplier_whose_facts_were_not_saved_is_still_ranked_again(run_id, clock):
    with repository.connection() as conn:
        result = json.loads(conn.execute("SELECT result_json FROM supplier_results WHERE supplier_name = ?", (ORBIT,)).fetchone()[0])
        result.pop("extracted_facts")
        conn.execute("UPDATE supplier_results SET result_json = ? WHERE supplier_name = ?", (json.dumps(result), ORBIT))
    assert priorities(run_id, clock).ok
    assert saved(run_id)[ORBIT].industry_score < 51.25 and weights_of(run_id) == ROUND_ONE_WEIGHTS
    assert_consistent(run_id)


def test_the_overview_and_the_json_file_read_the_new_priorities_without_changes(run_id, clock):
    assert priorities(run_id, clock).ok
    run, stored = overview_args(run_id)
    overview = tab_overview.build_overview(run, stored)
    assert [(r.rank, r.name) for r in overview.rows][:2] == [(1, NEXA), (2, HORIZON)] and overview.recommended.name == NEXA
    assert [w for _c, _n, w in overview.criteria] == ROUND_ONE_WEIGHTS
    export = tab_audit.build_export(run, stored, overview)
    nexa = next(s for s in export["suppliers"] if s["supplier_name"] == NEXA)
    assert nexa["rank"] == 1 and nexa["ppi"] == 86.6 and [c["weight"] for c in nexa["criteria"]] == ROUND_ONE_WEIGHTS
    assert export["recommendation"]["recommended"] == NEXA
    assert [c["weight"] for c in export["criteria_snapshot"]] == ROUND_ONE_WEIGHTS




def test_nothing_is_sent_to_the_ai_when_the_priorities_change(run_id, clock, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("the AI must not be called")

    monkeypatch.setattr("llm.client.call_llm", forbidden)
    assert priorities(run_id, clock).ok


def test_the_cost_and_status_of_the_run_do_not_change_when_the_priorities_change(run_id, clock):
    with repository.connection() as conn:
        before = repository.get_run(conn, run_id)
    assert priorities(run_id, clock).ok
    with repository.connection() as conn:
        after = repository.get_run(conn, run_id)
    assert (after.status, after.total_tokens, after.total_cost_usd, after.duration_seconds, after.model, after.created_at) == (
        before.status, before.total_tokens, before.total_cost_usd, before.duration_seconds, before.model, before.created_at)


# ---------------------------------------------------------------- satisfied: accept the recommendation

def test_a_satisfied_person_accepts_and_the_run_becomes_final(run_id, clock):
    before = snapshot_of(run_id)
    out = accept(run_id, clock, satisfaction=5, actor="Asha")
    assert out.ok and out.message == "You accepted Apex Systems. This evaluation is now final."
    with repository.connection() as conn:
        assert repository.get_run(conn, run_id).status == "Final"
    after = snapshot_of(run_id)
    assert after["results"] == before["results"] and after["attempts"] == before["attempts"]
    [row] = rows("SELECT * FROM reviewer_feedback")
    assert (row["action"], row["supplier_name"], row["criterion_id"], row["rating"], row["comment"]) == ("accept", APEX, None, 5, "")
    details = json.loads(row["details_json"])
    assert (details["satisfaction"], details["recommended"], details["rounds_used"], details["accepted_anyway"], details["by"]) == (
        5, APEX, 0, False, "Asha")
    assert [w["weight"] for w in details["weights"]] == [30.0, 20.0, 20.0, 20.0, 10.0]
    assert details["board"][0]["supplier"] == APEX
    [event] = rows("SELECT * FROM audit_log WHERE action = 'recommendation_accepted'")
    assert event["actor"] == "Asha"
    assert json.loads(event["details_json"]) == {"supplier": APEX, "satisfaction": 5, "accepted_anyway": False, "rounds_used": 0}


def test_a_4_is_enough_and_a_3_is_not(run_id, clock):
    before = snapshot_of(run_id)
    out = accept(run_id, clock, satisfaction=3)
    assert not out.ok and out.message == "You are not satisfied yet, so say what you are not satisfied with first."
    assert snapshot_of(run_id) == before
    assert accept(run_id, clock, satisfaction=4).ok


@pytest.mark.parametrize("bad", [0, 6, None, 4.5, "5", True])
def test_accepting_needs_a_satisfaction_from_1_to_5(run_id, clock, bad):
    out = accept(run_id, clock, satisfaction=bad)
    assert not out.ok and out.message == "Choose how satisfied you are, from 1 to 5."


def test_after_a_round_the_new_recommendation_is_what_is_accepted(run_id, clock):
    assert priorities(run_id, clock).ok
    out = accept(run_id, clock, satisfaction=4, round_no=2)
    assert out.ok and out.message == "You accepted NexaWorks. This evaluation is now final."
    assert json.loads(rows("SELECT details_json FROM reviewer_feedback WHERE action = 'accept'")[0]["details_json"])["rounds_used"] == 1
    assert rows("SELECT supplier_name FROM reviewer_feedback WHERE action = 'accept'") == [{"supplier_name": NEXA}]


def test_after_all_three_rounds_a_person_who_is_still_not_satisfied_may_accept_anyway(run_id, clock):
    assert priorities(run_id, clock, round_no=1).ok and priorities(run_id, clock, criterion=TECH, round_no=2).ok
    assert priorities(run_id, clock, criterion=SECURITY, round_no=3).ok
    out = accept(run_id, clock, satisfaction=2, round_no=4)
    assert out.ok and out.message.endswith("You accepted Apex Systems. This evaluation is now final.")
    assert out.message.startswith("You were not satisfied after all 3 rounds, so the final recommendation is the best fit with the original weights")
    details = json.loads(rows("SELECT details_json FROM reviewer_feedback WHERE action = 'accept'")[0]["details_json"])
    assert (details["accepted_anyway"], details["rounds_used"], details["satisfaction"]) == (True, 3, 2)
    assert json.loads(rows("SELECT details_json FROM audit_log WHERE action = 'recommendation_accepted'")[0]["details_json"])["accepted_anyway"] is True


@pytest.mark.parametrize("round_no", [0, 2, 3, "1", None])
def test_an_old_question_cannot_be_accepted(run_id, clock, round_no):
    out = accept(run_id, clock, round_no=round_no)
    assert not out.ok and out.message == orchestrator.ANSWERED_TEXT
    assert rows("SELECT * FROM reviewer_feedback") == []


def test_a_run_that_was_accepted_cannot_be_accepted_or_changed_again(run_id, clock):
    assert accept(run_id, clock).ok
    assert [o.message for o in (accept(run_id, clock), priorities(run_id, clock))] == ["This run is final, so it can no longer be changed."] * 2
    assert len(rows("SELECT * FROM reviewer_feedback")) == 1


def test_with_no_eligible_supplier_there_is_nothing_to_accept(clock):
    run = evaluated_run(facts={n: BAD_FACTS for n in NAMES}).run_id
    before = snapshot_of(run)
    out = accept(run, clock)
    assert not out.ok and out.message == "No supplier meets every must-have, so there is no recommendation to accept."
    assert snapshot_of(run) == before


def test_the_other_tabs_show_a_final_run_like_any_other(run_id, clock):
    assert accept(run_id, clock).ok
    run, stored = overview_args(run_id)
    overview = tab_overview.build_overview(run, stored)
    assert overview.recommended.name == APEX and run.status == "Final"
    assert tab_audit.build_export(run, stored, overview)["status"] == "Final"


# ---------------------------------------------------------------- "None of the above": the AI reads a typed need

def test_the_ai_proposes_one_criterion_and_nothing_changes_until_the_person_confirms(run_id):
    before = snapshot_of(run_id)
    out, backend = understand(run_id)
    assert (out.ok, out.message, out.criterion_id, out.reading) == (True, "", TECH, READING)
    assert len(backend.calls) == 1
    after = snapshot_of(run_id)
    assert after["results"] == before["results"] and after["feedback"] == [] and weights_of(run_id) == [30.0, 20.0, 20.0, 20.0, 10.0]


def test_the_ai_is_sent_the_criteria_of_this_run_and_the_typed_text_and_nothing_else_about_the_run(run_id):
    _, backend = understand(run_id)
    sent = backend.sent_text()
    assert NEED in sent and "1 | Technical Capability | Exact processor" in sent and "3 | Commercial Value | Itemised price table" in sent
    assert KEY not in sent and SENTINEL not in sent and "Long proposal text" not in sent
    assert not any(name in sent for name in NAMES) and "Rs 60" not in sent and "6000000" not in sent
    assert backend.calls[0]["temperature"] == 0 and backend.calls[0]["api_key"] == KEY


def test_the_call_is_logged_and_its_cost_is_added_to_the_run(run_id):
    with repository.connection() as conn:
        before = repository.get_run(conn, run_id)
    understand(run_id, actor="Asha")
    with repository.connection() as conn:
        after = repository.get_run(conn, run_id)
    assert after.total_tokens == before.total_tokens + 1000 and after.total_cost_usd == pytest.approx(before.total_cost_usd + 0.01)
    assert after.status == "Evaluated"
    [event] = rows("SELECT * FROM audit_log WHERE action = 'need_interpreted'")
    assert event["actor"] == "AI"
    assert json.loads(event["details_json"]) == {
        "text": NEED, "action": "reweight", "criterion_id": TECH, "reading": READING, "readable": True, "llm_calls": 1,
        "model": "test-model", "provider": "OpenAI", "tokens": 1000, "cost_usd": 0.01, "requested_by": "Asha", "leaning": None}
    assert KEY not in whole_database()


@pytest.mark.parametrize("action", ["re_evaluate", "set_aside"])
def test_a_need_the_ai_reads_as_something_else_is_explained_and_the_person_picks_an_option(run_id, action):
    before = snapshot_of(run_id)
    out, _ = understand(run_id, ai_reply(action=action, criterion_id=PRICE, supplier="Vertexa"), text="I do not like Vertexa")
    assert not out.ok and out.criterion_id is None
    assert out.message == f"{reweight.UNSUPPORTED_ACTIONS[action]} {orchestrator.PICK_AN_OPTION} Nothing was changed."
    assert snapshot_of(run_id)["feedback"] == before["feedback"] and weights_of(run_id) == [30.0, 20.0, 20.0, 20.0, 10.0]
    assert json.loads(rows("SELECT details_json FROM audit_log WHERE action = 'need_interpreted'")[0]["details_json"])["action"] == action


def test_a_need_the_ai_cannot_place_asks_the_person_to_pick_an_option(run_id):
    out, _ = understand(run_id, ai_reply(action="unclear", criterion_id=None, reading="I could not tell."))
    assert not out.ok and out.message == ("I could not match that to anything this evaluation looks at. "
                                          "Pick one of the options, or write it a different way. Nothing was changed.")


def test_a_reweight_that_points_outside_the_run_is_treated_as_unclear(run_id):
    out, _ = understand(run_id, ai_reply(criterion_id=99))
    assert not out.ok and out.criterion_id is None and "could not match" in out.message


def test_a_reply_that_cannot_be_read_twice_asks_the_person_to_pick_an_option_and_the_cost_is_counted(run_id):
    with repository.connection() as conn:
        before = repository.get_run(conn, run_id)
    out, backend = understand(run_id, "nope", "still nope")
    assert not out.ok and out.message == f"The AI's reply could not be read. {orchestrator.PICK_AN_OPTION} Nothing was changed."
    assert len(backend.calls) == 2
    with repository.connection() as conn:
        assert repository.get_run(conn, run_id).total_tokens == before.total_tokens + 2000
    assert json.loads(rows("SELECT details_json FROM audit_log WHERE action = 'need_interpreted'")[0]["details_json"])["readable"] is False


def test_a_provider_problem_changes_nothing_and_is_logged(run_id):
    before = snapshot_of(run_id)
    error = type("AuthenticationError", (Exception,), {})("bad key " + KEY)
    error.status_code = 401
    out, _ = understand(run_id, error)
    assert not out.ok and out.message.startswith("The AI could not be reached:") and out.message.endswith("Nothing was changed.")
    assert KEY not in out.message
    assert snapshot_of(run_id) == before
    [event] = rows("SELECT * FROM audit_log WHERE action = 'need_interpretation_failed'")
    assert json.loads(event["details_json"])["model"] == "test-model" and KEY not in whole_database()


@pytest.mark.parametrize("text", ["", "   ", None])
def test_nothing_typed_sends_nothing(run_id, text):
    out, backend = understand(run_id, text=text)
    assert not out.ok and out.message == "Write what you are not satisfied with." and backend.calls == []


def test_a_text_that_contains_the_api_key_is_not_sent_or_saved(run_id):
    out, backend = understand(run_id, text=f"my key is {KEY}, please use it")
    assert not out.ok and out.message == "What you wrote contains your API key. Remove it and try again. Nothing was sent."
    assert backend.calls == [] and KEY not in whole_database()


def test_a_very_short_key_is_not_matched_against_the_text(run_id):
    short = client_settings("abc")
    out, backend = understand(run_id, text="abc and more words about battery", settings=short)
    assert out.ok and len(backend.calls) == 1


def client_settings(key):
    from llm import client
    return client.LLMSettings("OpenAI", "test-model", key)


def test_no_question_is_asked_of_the_ai_once_all_rounds_are_used(run_id, clock):
    assert priorities(run_id, clock, round_no=1).ok and priorities(run_id, clock, criterion=TECH, round_no=2).ok
    assert priorities(run_id, clock, criterion=SECURITY, round_no=3).ok
    out, backend = understand(run_id)
    assert not out.ok and out.message == orchestrator.ROUNDS_USED_TEXT and backend.calls == []


@pytest.mark.parametrize("status", ["Failed", "Final"])
def test_a_run_that_cannot_be_changed_is_not_asked_about(run_id, status):
    with repository.connection() as conn:
        repository.set_run_status(conn, run_id, status)
    out, backend = understand(run_id)
    assert not out.ok and out.message == f"This run is {status.lower()}, so it can no longer be changed." and backend.calls == []


def test_an_unknown_run_is_not_asked_about():
    evaluated_run()
    out, backend = understand("RUN-NOPE")
    assert not out.ok and out.message == "This run was not found." and backend.calls == []


# ---------------------------------------------------------------- the whole story

def test_the_whole_story_of_a_run_leaves_every_step_in_the_history_and_the_ranking_consistent(run_id, clock):
    out, _ = understand(run_id)                                   # "None of the above": the AI reads the need, nothing changes yet
    assert out.ok and out.criterion_id == TECH
    assert priorities(run_id, clock, 3, out.criterion_id, 1, typed=NEED, reading=out.reading, proposed_id=out.criterion_id).ok
    assert priorities(run_id, clock, 2, PRICE, 2).ok              # round two: an option
    assert priorities(run_id, clock, 1, SECURITY, 3).ok           # round three: an option
    assert accept(run_id, clock, 2, 4).ok                         # all three rounds used: accept anyway
    assert [r["action"] for r in rows("SELECT action FROM reviewer_feedback ORDER BY id")] == ["reweight", "reweight", "reweight", "accept"]
    assert [r["rating"] for r in rows("SELECT rating FROM reviewer_feedback ORDER BY id")] == [3, 2, 1, 2]
    log = [r["action"] for r in rows("SELECT action FROM audit_log ORDER BY id")]
    for needed in ("need_interpreted", "priorities_changed", "recommendation_accepted", "ranking_computed"):
        assert needed in log
    assert log.index("need_interpreted") < log.index("priorities_changed") < log.index("recommendation_accepted")
    assert sum(weights_of(run_id)) == 100 and rows("SELECT status FROM rfp_runs") == [{"status": "Final"}]
    assert rows("SELECT COUNT(*) AS n FROM evaluation_attempts")[0]["n"] == 25      # the marks were never read again
    assert_consistent(run_id)
    assert KEY not in whole_database() and SENTINEL not in whole_database()
