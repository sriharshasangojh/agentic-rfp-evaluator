"""F21, the pure part: the AI-quality tiles, the run-history filters and the change trail (tools/audit_view.py). No database, no Streamlit."""
from __future__ import annotations

import ast
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import audit_view as V


# ---------------------------------------------------------------- the tiles

@pytest.mark.parametrize("part, whole, expected", [(1, 3, 33.3), (2, 3, 66.7), (1, 8, 12.5), (0, 5, 0.0), (5, 5, 100.0), (1, 6, 16.7), (1, 0, None),
                                                   (0, 0, None), (-1, 5, None)])
def test_percent_has_one_decimal_rounded_half_up_and_no_answer_without_a_whole(part, whole, expected):
    assert V.percent(part, whole) == expected


def test_the_average_cost_leaves_out_runs_that_reported_none_instead_of_counting_them_as_free():
    assert V.average_cost([0.1, None, 0.3]) == (pytest.approx(0.2), 2)
    assert V.average_cost([None, "x", True, float("nan"), -1]) == (None, 0)
    assert V.average_cost([]) == (None, 0)
    assert V.average_cost([0, 0.5]) == (0.25, 2)


def test_the_tiles_carry_the_counts_behind_each_rate():
    t = V.quality_tiles(4, 1, 3, 12, [0.01, 0.03, None, None])
    assert (t.runs, t.changed_runs, t.changed_rate) == (4, 1, 25.0)
    assert (t.evidence_missing, t.evidence_checked, t.evidence_rate) == (3, 12, 25.0)
    assert t.average_cost_usd == pytest.approx(0.02) and t.runs_with_cost == 2


def test_the_tiles_of_an_empty_store_have_no_rates_instead_of_zero():
    t = V.quality_tiles(0, 0, 0, 0, [])
    assert t.changed_rate is None and t.evidence_rate is None and t.average_cost_usd is None


# ---------------------------------------------------------------- the history

def line(run_id, day, model="gpt-4o-mini", winner="Apex", status="Evaluated", hour=9):
    return V.HistoryLine(run_id, datetime(2026, 10, day, hour, tzinfo=timezone.utc) if day else None, model, winner, status, 3, 0.01)


LINES = (line("R1", 20, winner="Apex"), line("R2", 22, model="gpt-4o", winner="Nexa"), line("R3", 24, winner=None), line("R4", None, model="", winner=None))


def ids(lines):
    return [l.run_id for l in lines]


def test_no_filter_keeps_every_run_in_order():
    assert ids(V.filter_history(LINES)) == ["R1", "R2", "R3", "R4"]


def test_the_date_range_includes_both_end_days_and_drops_runs_without_a_date():
    assert ids(V.filter_history(LINES, start=date(2026, 10, 22))) == ["R2", "R3"]
    assert ids(V.filter_history(LINES, end=date(2026, 10, 22))) == ["R1", "R2"]
    assert ids(V.filter_history(LINES, start=date(2026, 10, 22), end=date(2026, 10, 22))) == ["R2"]
    assert ids(V.filter_history(LINES, start=date(2026, 10, 23), end=date(2026, 10, 21))) == []


def test_late_evening_utc_counts_for_its_own_utc_day():
    late = line("L", 22, hour=23)
    assert ids(V.filter_history([late], start=date(2026, 10, 22), end=date(2026, 10, 22))) == ["L"]
    assert ids(V.filter_history([late], start=date(2026, 10, 23))) == []


def test_model_and_winner_filters_match_exactly_and_all_lets_everything_through():
    assert ids(V.filter_history(LINES, model="gpt-4o")) == ["R2"]
    assert ids(V.filter_history(LINES, winner="Apex")) == ["R1"]
    assert ids(V.filter_history(LINES, model="gpt-4o-mini", winner="Apex")) == ["R1"]
    assert ids(V.filter_history(LINES, model="gpt-4o", winner="Apex")) == []
    assert ids(V.filter_history(LINES, model=V.ALL, winner=V.ALL)) == ["R1", "R2", "R3", "R4"]


def test_the_drop_down_entries_are_all_then_each_value_once_sorted_without_empty_ones():
    assert V.choices(LINES, "model") == (V.ALL, "gpt-4o", "gpt-4o-mini")
    assert V.choices(LINES, "winner") == (V.ALL, "Apex", "Nexa")
    assert V.choices([], "winner") == (V.ALL,)


def test_history_lines_read_the_run_rows():
    run = SimpleNamespace(rfp_run_id="R", created_at="2026-10-24T14:20:53+00:00", model=" gpt-4o ", status="Final", total_cost_usd=0.5)
    got = V.history_lines([SimpleNamespace(run=run, winner="Apex", suppliers=2)])[0]
    assert got == V.HistoryLine("R", datetime(2026, 10, 24, 14, 20, 53, tzinfo=timezone.utc), "gpt-4o", "Apex", "Final", 2, 0.5)
    run.created_at, run.model, run.total_cost_usd = "junk", None, None
    got = V.history_lines([SimpleNamespace(run=run, winner=None, suppliers=0)])[0]
    assert got.started is None and got.model == "" and got.cost_usd is None


def test_a_time_without_a_zone_is_taken_as_utc():
    assert V.moment_of("2026-10-24T14:20:53") == datetime(2026, 10, 24, 14, 20, 53, tzinfo=timezone.utc)
    assert V.moment_of("2026-10-24T19:50:53+05:30") == datetime(2026, 10, 24, 14, 20, 53, tzinfo=timezone.utc)


# ---------------------------------------------------------------- the trail

def event(action, details=None, actor="system", at="2026-10-24T14:20:53+00:00"):
    return SimpleNamespace(timestamp=at, actor=actor, action=action, details=details or {})


@pytest.mark.parametrize("action, details, expected", [
    ("run_created", {"suppliers": ["A", "B"], "model": "gpt-4o"}, "Run started with 2 suppliers, model gpt-4o."),
    ("run_created", {"suppliers": ["A"]}, "Run started with 1 supplier, model not recorded."),
    ("documents_read", {"documents": [{}, {}]}, "Read 2 proposals."),
    ("supplier_evaluated", {"supplier": "Apex", "attempt_no": 1}, "The AI evaluated Apex (attempt 1)."),
    ("supplier_evaluated", {"supplier": "Apex", "evaluation_failed": True}, "The AI evaluated Apex (attempt 1) and the evaluation failed."),
    ("validation_warnings", {"supplier": "Apex", "warnings": ["x"]}, "Apex: 1 warning about the AI's reply."),
    ("evidence_not_found", {"supplier": "Apex", "criterion_ids": [1, 2]}, "Apex: 2 quotes not found in the proposal."),
    ("gate_results", {"supplier": "Apex", "failed": []}, "Apex: must-haves checked, all met."),
    ("gate_results", {"supplier": "Apex", "failed": ["a", "b"]}, "Apex: must-haves checked, 2 not met."),
    ("price_marks_set", {"lowest_total_rs": 5616800}, "Price marks worked out from the totals; the lowest total is Rs 56.17 lakh."),
    ("price_marks_set", {}, "Price marks worked out from the totals; the lowest total is an unknown amount."),
    ("priorities_set", {"ticked": ["Support", "Price"]}, "Priorities chosen before the run: 2 areas count for more."),
    ("priorities_set", {"ticked": ["Support"]}, "Priorities chosen before the run: 1 area counts for more."),
    ("priorities_set", {}, "Priorities chosen before the run."),
    ("ranking_computed", {"top3": [{"supplier": "Apex"}]}, "Ranking worked out; first: Apex."),
    ("ranking_computed", {}, "Ranking worked out."),
    ("priorities_changed", {"round": 2, "criterion": "Support", "recommended_before": "A", "recommended_after": "B"},
     "Round 2: Support now counts for more. Recommended changed from A to B."),
    ("priorities_changed", {"round": 1, "criterion": "Support", "recommended_before": "A", "recommended_after": "A"},
     "Round 1: Support now counts for more. Recommended: A."),
    ("recommendation_accepted", {"supplier": "Apex"}, "Apex accepted; the run is final."),
    ("negotiation_suggested", {"supplier": "Apex"}, "Final recommendation and negotiation points made for Apex."),
    ("json_exported", {}, "The JSON file was downloaded."),
    ("run_failed", {}, "The run stopped before it finished."),
    ("something_new", {}, "Event: something new."),
])
def test_every_event_reads_as_one_plain_sentence(action, details, expected):
    assert V.describe(action, details) == expected


def test_only_events_that_change_scores_weights_or_ranks_count_as_score_changes():
    rows = V.trail_rows([event(a) for a in ("run_created", "price_marks_set", "supplier_evaluated", "priorities_set", "ranking_computed",
                                            "priorities_changed", "json_exported", "negotiation_suggested")])
    assert [r.score_change for r in rows] == [False, True, False, True, True, True, False, False]
    assert [r.text for r in V.only_score_changes(rows)] == [r.text for r in rows if r.score_change] and len(V.only_score_changes(rows)) == 4


def test_the_trail_keeps_order_actor_and_the_time_text_given():
    rows = V.trail_rows([event("run_created", actor="system", at="a"), event("json_exported", actor="Asha", at="b")], lambda s: f"at {s}")
    assert [(r.when, r.actor) for r in rows] == [("at a", "system"), ("at b", "Asha")]


def test_a_trail_without_score_changes_filters_to_nothing():
    assert V.only_score_changes(V.trail_rows([event("run_created"), event("json_exported")])) == ()


def test_the_module_is_pure():
    tree = ast.parse(Path(V.__file__).read_text(encoding="utf-8"))
    imported = {n.module.split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
                for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in (n.names if isinstance(n, ast.Import) else [n])}
    assert imported <= {"__future__", "collections", "dataclasses", "datetime", "fractions", "typing", "tools"}, imported
