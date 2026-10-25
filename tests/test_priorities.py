"""Tests for "Your priorities" (before a run): the weights rule, the AI's reading of a typed sentence, and a real run that uses them.

The rule (`reweight.plan_priorities`) is tested on its own. The orchestrator is tested with the real pipeline and a fake model, so
nothing needs an API key or network: the saved weights, marks, ranking, audit rows and token totals are read back from the database.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agents import orchestrator
from db import repository, seed
from review_support import FIVE, KEY, NAMES, SETTINGS, FakeBackend, evaluated_run, marks, rows, saved, whole_database
from tools import ranking, reweight

TECH, PLAN, PRICE, SECURITY, SUPPORT = 1, 2, 3, 4, 5
SEEDED = (("Technical Capability", 30.0), ("Implementation Plan", 20.0), ("Commercial Value", 20.0), ("Security & Compliance", 20.0),
          ("Support & Experience", 10.0))
CRITERIA = [SimpleNamespace(criterion_id=i, name=n, weight=w) for i, (n, w) in enumerate(SEEDED, start=1)]
NEED = "I want laptops that last a full working day"
READING = "I understood: battery life matters more to you."


@pytest.fixture(autouse=True)
def seeded_database():
    seed.ensure_database()


def weights(plan):
    return [plan.new_weights()[i] for i in range(1, 6)]


# ---------------------------------------------------------------- the rule

def test_nothing_ticked_changes_nothing():
    plan = reweight.plan_priorities(CRITERIA, [])
    assert weights(plan) == [30.0, 20.0, 20.0, 20.0, 10.0] and not plan.changed and plan.ticked == () and plan.skipped == ()


def test_one_tick_is_the_spec_rule_once():
    plan = reweight.plan_priorities(CRITERIA, [PRICE])
    assert weights(plan) == [24.37, 16.25, 35.0, 16.25, 8.13] and plan.changed and plan.ticked == (PRICE,)


def test_one_tick_is_the_same_as_the_satisfaction_rule_of_the_review_tab():
    assert reweight.plan_priorities(CRITERIA, [TECH]).new_weights() == reweight.plan_reweight(CRITERIA, TECH).new_weights()


def test_two_ticks_are_applied_in_marking_scheme_order_whatever_order_they_were_ticked():
    a = reweight.plan_priorities(CRITERIA, [PRICE, TECH])
    b = reweight.plan_priorities(CRITERIA, [TECH, PRICE])
    assert a == b and a.ticked == (TECH, PRICE)
    first = reweight.plan_reweight(CRITERIA, TECH)
    then = reweight.plan_reweight([SimpleNamespace(criterion_id=l.criterion_id, name=l.name, weight=l.now) for l in first.lines], PRICE)
    assert a.new_weights() == then.new_weights()


@pytest.mark.parametrize("ticked", [[1], [3], [1, 3], [2, 4, 5], [1, 2, 3, 4], [5, 5, 5]])
def test_the_weights_always_total_100_and_never_exceed_the_cap(ticked):
    plan = reweight.plan_priorities(CRITERIA, ticked)
    assert round(sum(plan.new_weights().values()), 2) == 100 and max(plan.new_weights().values()) <= 60


def test_a_tick_counts_once_however_often_it_is_given():
    assert reweight.plan_priorities(CRITERIA, [PRICE, PRICE]) == reweight.plan_priorities(CRITERIA, [PRICE])


def test_ticking_everything_changes_nothing_and_says_so():
    plan = reweight.plan_priorities(CRITERIA, [1, 2, 3, 4, 5])
    assert not plan.changed and plan.everything_ticked and weights(plan) == [30.0, 20.0, 20.0, 20.0, 10.0]
    assert reweight.describe_priorities(plan) == "Everything is ticked, so nothing counts for more than anything else. The weights stay as they are."


def test_ticks_for_criteria_that_are_not_in_the_run_are_ignored():
    plan = reweight.plan_priorities(CRITERIA, [99, PRICE])
    assert plan.ticked == (PRICE,) and weights(plan) == [24.37, 16.25, 35.0, 16.25, 8.13]
    assert not reweight.plan_priorities(CRITERIA, [99]).changed


def test_a_criterion_that_already_counts_for_the_most_it_can_is_skipped_and_named():
    heavy = [SimpleNamespace(criterion_id=1, name="Quality", weight=60.0), SimpleNamespace(criterion_id=2, name="Price", weight=40.0)]
    plan = reweight.plan_priorities(heavy, [1])
    assert plan.skipped == ("Quality",) and not plan.changed and plan.ticked == ()
    assert reweight.describe_priorities(plan) == "Quality already counts for as much as it can, so the weights stay as they are."


def test_one_skipped_tick_does_not_stop_the_others():
    mixed = [SimpleNamespace(criterion_id=1, name="Quality", weight=60.0), SimpleNamespace(criterion_id=2, name="Price", weight=30.0),
             SimpleNamespace(criterion_id=3, name="Support", weight=10.0)]
    plan = reweight.plan_priorities(mixed, [1, 2])
    assert plan.skipped == ("Quality",) and plan.ticked == (2,) and plan.new_weights()[2] == 45.0
    assert "Quality already counts for as much as it can." in reweight.describe_priorities(plan)


def test_a_run_with_one_criterion_has_nothing_to_take_points_from():
    plan = reweight.plan_priorities([SimpleNamespace(criterion_id=1, name="Only", weight=100.0)], [1])
    assert not plan.changed and plan.everything_ticked


def test_the_rule_does_not_change_what_it_is_given():
    before = [(c.criterion_id, c.weight) for c in CRITERIA]
    reweight.plan_priorities(CRITERIA, [1, 3])
    assert [(c.criterion_id, c.weight) for c in CRITERIA] == before


def test_the_sentence_names_every_weight_that_changed_and_says_it_is_for_this_run():
    text = reweight.describe_priorities(reweight.plan_priorities(CRITERIA, [PRICE]))
    assert text == ("With your priorities, for this run only: Technical Capability 30% to 24.37%; Implementation Plan 20% to 16.25%; "
                    "Commercial Value 20% to 35%; Security & Compliance 20% to 16.25%; Support & Experience 10% to 8.13%.")
    assert reweight.describe_priorities(reweight.plan_priorities(CRITERIA, [])) == "Nothing ticked: every criterion keeps the weight set on the Criteria tab."


def test_every_seeded_criterion_has_a_plain_tick_label_and_others_show_their_name():
    labels = [reweight.priority_label(n) for n, _w in SEEDED]
    assert len(set(labels)) == 5 and all(l and l != n for l, (n, _w) in zip(labels, SEEDED))
    assert reweight.priority_label("  Delivery speed ") == "Delivery speed"
    assert reweight.priority_label("TECHNICAL CAPABILITY") == labels[0]


# ---------------------------------------------------------------- reading a typed sentence

def ai_reply(action="reweight", criterion_id=TECH, reading=READING) -> str:
    return json.dumps({"action": action, "supplier": None, "criterion_id": criterion_id, "reading": reading})


def understand(*replies, text=NEED, settings=SETTINGS):
    backend = FakeBackend(*(replies or [ai_reply()]))
    return orchestrator.understand_priority(text, settings, backend=backend, sleep=lambda _s: None), backend


def test_the_ai_proposes_one_criterion_and_nothing_is_saved():
    before = whole_database()
    out, backend = understand()
    assert out.ok and out.message == "" and out.typed.criterion_id == TECH and out.typed.reading == READING and out.typed.text == NEED
    assert out.typed.tokens == 1000 and out.typed.cost_usd == 0.01 and out.typed.attempts == 1 and len(backend.calls) == 1
    assert whole_database() == before


def test_only_the_criteria_and_the_sentence_are_sent_to_the_ai():
    _, backend = understand()
    sent = backend.sent_text()
    assert NEED in sent and "1 | Technical Capability | Exact processor" in sent and KEY not in sent
    assert backend.calls[0]["temperature"] == 0 and backend.calls[0]["api_key"] == KEY


@pytest.mark.parametrize("action", ["re_evaluate", "set_aside", "unclear"])
def test_something_that_is_not_a_criterion_is_explained_and_nothing_is_proposed(action):
    out, _ = understand(ai_reply(action, None, ""))
    assert not out.ok and out.typed is None and out.message == orchestrator.PRIORITY_UNCLEAR


@pytest.mark.parametrize("action", ["re_evaluate", "set_aside"])
def test_a_criterion_named_for_another_action_does_not_become_a_priority(action):
    out, _ = understand(ai_reply(action, PRICE, "I understood: something else."))
    assert not out.ok and out.typed is None and out.message == orchestrator.PRIORITY_UNCLEAR


def test_a_criterion_that_is_not_in_the_run_is_treated_as_unclear():
    out, _ = understand(ai_reply("reweight", 42))
    assert not out.ok and out.message == orchestrator.PRIORITY_UNCLEAR


def test_a_reply_that_cannot_be_read_twice_is_explained():
    out, backend = understand("nope", "still nope")
    assert not out.ok and out.message == f"The AI's reply could not be read. {orchestrator.PRIORITY_UNCLEAR}" and len(backend.calls) == 2


def test_a_provider_problem_is_explained_without_the_key():
    error = type("AuthenticationError", (Exception,), {})("bad key " + KEY)
    error.status_code = 401
    out, _ = understand(error)
    assert not out.ok and out.message.startswith("The AI could not be reached:") and out.message.endswith("Nothing was changed.") and KEY not in out.message


@pytest.mark.parametrize("text", ["", "   ", None])
def test_nothing_written_sends_nothing(text):
    out, backend = understand(text=text)
    assert not out.ok and out.message == "Write what matters to you first." and backend.calls == []


def test_a_sentence_with_the_api_key_in_it_is_not_sent():
    out, backend = understand(text=f"my key is {KEY}")
    assert not out.ok and "contains your API key" in out.message and backend.calls == [] and KEY not in whole_database()


def test_a_long_sentence_is_cut_to_300_characters():
    out, _ = understand(text="battery " * 100)
    assert out.ok and len(out.typed.text) <= reweight.NEED_MAX_CHARS


# ---------------------------------------------------------------- a run that uses the priorities

def run_with(ticked=(), typed=(), rows_=FIVE):
    return evaluated_run(rows_, priorities=orchestrator.PriorityRequest(tuple(ticked), tuple(typed)))


def run_weights(run_id):
    with repository.connection() as conn:
        return [c["weight"] for c in repository.get_run(conn, run_id).criteria_snapshot]


def setup_weights():
    with repository.connection() as conn:
        return [c.weight for c in repository.list_criteria(conn, active_only=True)]


def events(action):
    return [json.loads(r["details_json"]) for r in rows("SELECT details_json FROM audit_log WHERE action = ? ORDER BY id", action)]


def typed(criterion=TECH, tokens=123, cost=0.5):
    return orchestrator.TypedPriority(NEED, criterion, READING, tokens, cost, 1)


def test_a_run_without_priorities_uses_the_setup_weights_and_logs_nothing():
    run = evaluated_run()
    assert run_weights(run.run_id) == [30.0, 20.0, 20.0, 20.0, 10.0] and events("priorities_set") == []


def test_a_tick_changes_this_runs_weights_and_not_the_marking_scheme():
    run = run_with([PRICE])
    assert run_weights(run.run_id) == [24.37, 16.25, 35.0, 16.25, 8.13]
    assert setup_weights() == [30.0, 20.0, 20.0, 20.0, 10.0]


def test_the_next_run_starts_from_the_setup_weights_again():
    run_with([PRICE])
    assert run_weights(evaluated_run().run_id) == [30.0, 20.0, 20.0, 20.0, 10.0]


def test_the_marks_are_the_same_with_or_without_priorities():
    plain, ticked = saved(evaluated_run().run_id), saved(run_with([PRICE, SUPPORT]).run_id)
    assert all(marks(plain, n) == marks(ticked, n) for n in NAMES)


def test_the_saved_ranking_is_the_ranking_of_the_marks_with_the_new_weights():
    run = run_with([PRICE])
    stored = saved(run.run_id)
    inputs = [ranking.SupplierInput(n, s.submission_date, s.experience_rating, {i: m for i, m in enumerate(marks(stored, n), start=1)})
              for n, s in stored.items()]
    fresh = ranking.rank_suppliers(inputs, [ranking.CriterionWeight(i, w, 10.0) for i, w in enumerate(run_weights(run.run_id), start=1)])
    assert [(r.rank, r.name, r.ppi, r.absolute_score) for r in fresh.suppliers] == [
        (stored[n].final_rank, n, stored[n].ppi, stored[n].absolute_score) for n in [r.name for r in fresh.suppliers]]


def test_the_ranking_can_differ_from_the_one_without_priorities():
    plain = {n: s.ppi for n, s in saved(evaluated_run().run_id).items()}
    ticked = {n: s.ppi for n, s in saved(run_with([PRICE]).run_id).items()}
    assert plain != ticked


def test_each_suppliers_saved_criteria_carry_the_weights_of_the_run():
    run = run_with([PRICE])
    for s in saved(run.run_id).values():
        assert [c["weight"] for c in s.result["criteria"]] == [24.37, 16.25, 35.0, 16.25, 8.13]


def test_the_choice_is_written_to_the_audit_log_with_the_old_and_new_weights():
    run = run_with([PRICE, TECH])
    [event] = events("priorities_set")
    assert event["ticked"] == [TECH, PRICE] and event["typed"] == [] and event["skipped"] == [] and event["everything_ticked"] is False
    assert [(w["criterion_id"], w["was"]) for w in event["weights"]] == [(1, 30.0), (2, 20.0), (3, 20.0), (4, 20.0), (5, 10.0)]
    assert [w["now"] for w in event["weights"]] == run_weights(run.run_id)
    [row] = rows("SELECT actor FROM audit_log WHERE action = 'priorities_set'")
    assert row["actor"] == "Sri"


def test_the_step_log_says_what_the_ticks_did():
    assert any(line.startswith("With your priorities, for this run only:") for line in run_with([PRICE]).steps)
    assert not any("priorities" in line for line in evaluated_run().steps)


def test_a_confirmed_sentence_is_saved_with_the_run_and_its_cost_is_added():
    base = evaluated_run()
    with repository.connection() as conn:
        base_tokens, base_cost = repository.get_run(conn, base.run_id).total_tokens, repository.get_run(conn, base.run_id).total_cost_usd
    run = run_with([TECH], [typed()])
    with repository.connection() as conn:
        record = repository.get_run(conn, run.run_id)
    assert record.total_tokens == base_tokens + 123 and round(record.total_cost_usd - base_cost, 6) == 0.5
    [need] = events("need_interpreted")
    assert need["text"] == NEED and need["criterion_id"] == TECH and need["reading"] == READING and need["before_run"] is True
    assert need["tokens"] == 123 and need["model"] == "test-model" and need["provider"] == "OpenAI"
    [event] = events("priorities_set")
    assert event["typed"] == [{"text": NEED, "criterion_id": TECH, "reading": READING}]


def test_a_sentence_whose_tick_was_removed_is_dropped_and_not_charged():
    base = evaluated_run()
    with repository.connection() as conn:
        base_tokens = repository.get_run(conn, base.run_id).total_tokens
    run = run_with([PRICE], [typed(TECH)])
    with repository.connection() as conn:
        assert repository.get_run(conn, run.run_id).total_tokens == base_tokens
    assert events("need_interpreted") == [] and events("priorities_set")[0]["typed"] == []


def test_a_sentence_without_any_tick_leaves_no_trace():
    run = run_with([], [typed()])
    assert events("need_interpreted") == [] and events("priorities_set") != [] and run_weights(run.run_id) == [30.0, 20.0, 20.0, 20.0, 10.0]


def test_ticking_everything_keeps_the_weights_and_says_so_in_the_log():
    run = run_with([1, 2, 3, 4, 5])
    assert run_weights(run.run_id) == [30.0, 20.0, 20.0, 20.0, 10.0]
    assert events("priorities_set")[0]["everything_ticked"] is True


def test_unknown_criteria_in_the_request_are_ignored():
    assert run_weights(run_with([99]).run_id) == [30.0, 20.0, 20.0, 20.0, 10.0]


def test_the_industry_score_uses_the_new_weights():
    plain, ticked = saved(evaluated_run().run_id), saved(run_with([PRICE]).run_id)
    assert {n: s.industry_score for n, s in plain.items()} != {n: s.industry_score for n, s in ticked.items()}


def test_the_key_is_never_saved_by_a_run_with_priorities():
    run_with([TECH], [typed()])
    assert KEY not in whole_database()


def test_a_later_not_satisfied_round_starts_from_the_buyers_weights():
    run = run_with([PRICE])
    out = orchestrator.change_priorities(run.run_id, 2, TECH, 1)
    assert out.ok, out.message
    assert "Technical Capability now counts for 39.37% of the decision (it was 24.37%)." in out.message


def test_the_overview_and_the_review_tab_read_a_run_with_priorities_like_any_other():
    from ui import tab_overview
    run = run_with([PRICE])
    with repository.connection() as conn:
        overview = tab_overview.build_overview(repository.get_run(conn, run.run_id), repository.list_supplier_results(conn, run.run_id))
    assert [r.name for r in overview.rows] == [n for n, _s in sorted(((n, s.final_rank) for n, s in saved(run.run_id).items()), key=lambda t: t[1])]
