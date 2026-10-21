"""Tests for tools/gates.py (spec 5.2 and 16.1). No API key or network needed."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from db import repository
from db.seed import ensure_database
from tools.gates import RULES, GateResult, evaluate_gates, failed_gates, gate_status, is_eligible


@dataclass(frozen=True)
class FakeGate:
    gate_id: str
    name: str
    rule_key: str


GATES = (
    FakeGate("warranty", "Warranty", "warranty_years_min_3"),
    FakeGate("certification", "Certification", "bis_named"),
    FakeGate("price_table", "Price table", "price_table_present"),
    FakeGate("delivery", "Delivery", "delivery_weeks_max_8"),
)

GOOD_FACTS = {"warranty_years": 3, "bis_named": True, "price_table_present": True, "delivery_weeks": 6}


def run_one(gate_id: str, facts):
    gate = next(g for g in GATES if g.gate_id == gate_id)
    (result,) = evaluate_gates([gate], facts)
    return result


# ---------------------------------------------------------------- each gate: pass, fail, null

@pytest.mark.parametrize(
    "gate_id, facts, passed, reason",
    [
        # warranty: years >= 3
        ("warranty", {"warranty_years": 3}, True, "3 years (at least 3 required)"),
        ("warranty", {"warranty_years": 5}, True, "5 years (at least 3 required)"),
        ("warranty", {"warranty_years": 1}, False, "1 year (at least 3 required)"),
        ("warranty", {"warranty_years": 2.5}, False, "2.5 years (at least 3 required)"),
        ("warranty", {"warranty_years": 0}, False, "0 years (at least 3 required)"),
        ("warranty", {"warranty_years": None}, False, "Not stated"),
        # certification: bis_named is True
        ("certification", {"bis_named": True}, True, "BIS registration named"),
        ("certification", {"bis_named": False}, False, "BIS registration not named"),
        ("certification", {"bis_named": None}, False, "Not stated"),
        # price table: price_table_present is True
        ("price_table", {"price_table_present": True}, True, "Price table present"),
        ("price_table", {"price_table_present": False}, False, "No price table"),
        ("price_table", {"price_table_present": None}, False, "Not stated"),
        # delivery: not None and <= 8 weeks
        ("delivery", {"delivery_weeks": 6}, True, "6 weeks (at most 8 allowed)"),
        ("delivery", {"delivery_weeks": 8}, True, "8 weeks (at most 8 allowed)"),
        ("delivery", {"delivery_weeks": 1}, True, "1 week (at most 8 allowed)"),
        ("delivery", {"delivery_weeks": 0}, True, "0 weeks (at most 8 allowed)"),
        ("delivery", {"delivery_weeks": 8.5}, False, "8.5 weeks (at most 8 allowed)"),
        ("delivery", {"delivery_weeks": 10}, False, "10 weeks (at most 8 allowed)"),
        ("delivery", {"delivery_weeks": None}, False, "Not stated"),
    ],
)
def test_each_gate_passes_fails_and_handles_null(gate_id, facts, passed, reason):
    result = run_one(gate_id, facts)
    assert (result.passed, result.reason) == (passed, reason)


@pytest.mark.parametrize("gate_id", [g.gate_id for g in GATES])
def test_a_fact_that_is_absent_fails_with_not_stated(gate_id):
    result = run_one(gate_id, {})
    assert (result.passed, result.reason) == (False, "Not stated")


@pytest.mark.parametrize(
    "gate_id, facts",
    [
        ("warranty", {"warranty_years": "3"}),
        ("warranty", {"warranty_years": True}),
        ("warranty", {"warranty_years": float("nan")}),
        ("delivery", {"delivery_weeks": "6"}),
        ("delivery", {"delivery_weeks": False}),
        ("delivery", {"delivery_weeks": float("inf")}),
    ],
)
def test_a_value_that_is_not_a_real_number_counts_as_not_stated(gate_id, facts):
    # The validator turns these into null before they get here; the gates stay safe if one slips through.
    result = run_one(gate_id, facts)
    assert (result.passed, result.reason) == (False, "Not stated")


@pytest.mark.parametrize("gate_id, key", [("certification", "bis_named"), ("price_table", "price_table_present")])
@pytest.mark.parametrize("value", ["yes", "true", 1])
def test_yes_no_gates_pass_only_for_a_real_true(gate_id, key, value):
    assert run_one(gate_id, {key: value}).passed is False


def test_no_facts_at_all_fails_every_gate():
    results = evaluate_gates(GATES, None)
    assert [r.passed for r in results] == [False, False, False, False]
    assert {r.reason for r in results} == {"Not stated"}


# ---------------------------------------------------------------- whole-supplier outcomes

def test_a_supplier_meeting_everything_is_eligible():
    results = evaluate_gates(GATES, GOOD_FACTS)
    assert all(r.passed for r in results)
    assert is_eligible(results) is True
    assert failed_gates(results) == ()
    assert gate_status(results) == {
        "warranty": "pass", "certification": "pass", "price_table": "pass", "delivery": "pass",
    }


def test_one_failed_gate_makes_a_supplier_not_eligible():
    facts = {**GOOD_FACTS, "warranty_years": 1}  # like BrightPath in the spec's end-to-end check
    results = evaluate_gates(GATES, facts)
    assert is_eligible(results) is False
    assert [r.gate_id for r in failed_gates(results)] == ["warranty"]
    assert gate_status(results)["warranty"] == "fail"
    assert gate_status(results)["delivery"] == "pass"


def test_several_failures_are_all_reported_in_gate_order():
    facts = {"warranty_years": 1, "bis_named": None, "price_table_present": False, "delivery_weeks": 12}
    results = evaluate_gates(GATES, facts)
    assert [r.gate_id for r in failed_gates(results)] == ["warranty", "certification", "price_table", "delivery"]


def test_results_follow_the_order_of_the_gates_given():
    results = evaluate_gates(list(reversed(GATES)), GOOD_FACTS)
    assert [r.gate_id for r in results] == ["delivery", "price_table", "certification", "warranty"]


def test_no_gates_means_nothing_is_mandatory():
    assert evaluate_gates([], GOOD_FACTS) == ()
    assert is_eligible(()) is True


def test_a_gate_with_an_unknown_rule_fails_instead_of_being_skipped():
    gates = [FakeGate("made_up", "Made up", "no_such_rule")]
    (result,) = evaluate_gates(gates, GOOD_FACTS)
    assert result == GateResult("made_up", "Made up", False, "Unknown rule 'no_such_rule'")
    assert is_eligible([result]) is False


def test_facts_are_not_changed_by_checking_them():
    facts = dict(GOOD_FACTS)
    evaluate_gates(GATES, facts)
    assert facts == GOOD_FACTS


def test_results_cannot_be_changed_after_the_fact():
    (result,) = evaluate_gates(GATES[:1], GOOD_FACTS)
    with pytest.raises(Exception):
        result.passed = False  # type: ignore[misc]


# ---------------------------------------------------------------- the seeded gates

def test_every_seeded_gate_has_a_rule():
    ensure_database()
    with repository.connection() as conn:
        seeded = repository.list_gates(conn, active_only=True)
    assert [g.gate_id for g in seeded] == ["warranty", "certification", "price_table", "delivery"]
    assert {g.rule_key for g in seeded} <= set(RULES)


def test_seeded_gates_work_with_real_database_rows():
    ensure_database()
    with repository.connection() as conn:
        seeded = repository.list_gates(conn, active_only=True)
    results = evaluate_gates(seeded, GOOD_FACTS)
    assert is_eligible(results) is True
    results = evaluate_gates(seeded, {**GOOD_FACTS, "delivery_weeks": 9})
    assert [r.gate_id for r in failed_gates(results)] == ["delivery"]


# ---------------------------------------------------------------- guardrail

def test_gates_module_stays_pure():
    """Spec 2.1 and 21: no LLM, I/O, randomness or clock in gates.py. Only these imports are allowed."""
    allowed = {"__future__", "collections", "dataclasses", "math", "typing"}
    source = Path(__file__).resolve().parent.parent / "tools" / "gates.py"
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
    assert imported <= allowed, f"unexpected imports in gates.py: {sorted(imported - allowed)}"
    assert not called & {"open", "print", "input", "now", "today", "utcnow", "random"}, sorted(called)
