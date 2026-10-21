"""Gates (spec 5.2): mandatory pass/fail checks on a supplier's extracted facts.

The database says which gates exist and which rule each one uses (`rule_key`); the rules themselves live
here as plain Python. A missing fact (None) always fails the gate with the reason "Not stated". A gate whose
rule is not recognised also fails, so a mandatory check can never be skipped silently.
Pure functions only: no LLM, no I/O, no randomness, no clock.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

NOT_STATED = "Not stated"

Facts = Mapping[str, Any]
Rule = Callable[[Facts], tuple[bool, str]]


class GateLike(Protocol):
    """What this module needs from a gate. `db.repository.Gate` fits."""

    @property
    def gate_id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def rule_key(self) -> str: ...


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    name: str
    passed: bool
    reason: str  # why it passed or failed, in words the Overview tooltip can show


def _number(value: Any) -> float | int | None:
    """A real number, or None. True/False, text and NaN are not numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def _count(value: float | int, unit: str) -> str:
    text = f"{value:g}"
    return f"{text} {unit}" if value == 1 else f"{text} {unit}s"


def _warranty_years_min_3(facts: Facts) -> tuple[bool, str]:
    years = _number(facts.get("warranty_years"))
    if years is None:
        return False, NOT_STATED
    return years >= 3, f"{_count(years, 'year')} (at least 3 required)"


def _bis_named(facts: Facts) -> tuple[bool, str]:
    value = facts.get("bis_named")
    if value is None:
        return False, NOT_STATED
    return value is True, "BIS registration named" if value is True else "BIS registration not named"


def _price_table_present(facts: Facts) -> tuple[bool, str]:
    value = facts.get("price_table_present")
    if value is None:
        return False, NOT_STATED
    return value is True, "Price table present" if value is True else "No price table"


def _delivery_weeks_max_8(facts: Facts) -> tuple[bool, str]:
    weeks = _number(facts.get("delivery_weeks"))
    if weeks is None:
        return False, NOT_STATED
    return weeks <= 8, f"{_count(weeks, 'week')} (at most 8 allowed)"


# rule_key (as seeded in the `gates` table) -> rule
RULES: dict[str, Rule] = {
    "warranty_years_min_3": _warranty_years_min_3,
    "bis_named": _bis_named,
    "price_table_present": _price_table_present,
    "delivery_weeks_max_8": _delivery_weeks_max_8,
}


def evaluate_gates(gates: Sequence[GateLike], facts: Facts | None) -> tuple[GateResult, ...]:
    """Check every gate, in the order given, against one supplier's extracted facts (a plain dict or mapping)."""
    facts = facts or {}
    results = []
    for gate in gates:
        rule = RULES.get(gate.rule_key)
        if rule is None:
            results.append(GateResult(gate.gate_id, gate.name, False, f"Unknown rule '{gate.rule_key}'"))
            continue
        passed, reason = rule(facts)
        results.append(GateResult(gate.gate_id, gate.name, passed, reason))
    return tuple(results)


def is_eligible(results: Sequence[GateResult]) -> bool:
    """Eligible for the recommendation = every gate passed (with no gates at all, nothing is mandatory)."""
    return all(result.passed for result in results)


def failed_gates(results: Sequence[GateResult]) -> tuple[GateResult, ...]:
    return tuple(result for result in results if not result.passed)


def gate_status(results: Sequence[GateResult]) -> dict[str, str]:
    """{"warranty": "pass", "certification": "fail"}: the shape used in the JSON export (spec 15)."""
    return {result.gate_id: "pass" if result.passed else "fail" for result in results}
