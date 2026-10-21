"""Industry score (spec 9.5): how each supplier compares with a planning baseline, separate from the ranking.

Python grades each extracted fact: Above = 100, Meets = 75, Below = 25, Not stated = 0. The grades are
combined into a weighted average where every attribute gets an equal share of its criterion's weight
(Technical 30 with two attributes: battery 15, memory/storage 15). The industry score is shown next to the
ranking but never changes the order.

The price bands follow the buyer's own budget (see `PurchaseTerms`), so any price level works.
The other baseline values are planning assumptions for a fictional startup, not market research.
Pure functions only: no LLM, no I/O, no randomness, no clock.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from typing import Any, Protocol

ABOVE, MEETS, BELOW, NOT_STATED = "Above", "Meets", "Below", "Not stated"
POINTS: dict[str, int] = {ABOVE: 100, MEETS: 75, BELOW: 25, NOT_STATED: 0}

# Baselines used by the grading rules (the rule texts shown to people are seeded in `industry_benchmarks`).
GST_DIVISOR = Fraction("1.18")           # 18% GST, used only to estimate a unit price from a grand total
PRICE_MEETS_FROM_SHARE = Fraction(3, 4)  # unit price: Meets from 75% of the budget per unit up to 100% of it

Facts = Mapping[str, Any]


@dataclass(frozen=True)
class PurchaseTerms:
    """What the buyer is buying: the total budget (Rs) and the number of units. Both come from the run's inputs."""

    budget_rs: float
    quantity: int

    def __post_init__(self) -> None:
        budget = _number(self.budget_rs)
        if budget is None or budget <= 0:
            raise ValueError("The budget must be a number above 0.")
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int) or self.quantity < 1:
            raise ValueError("The quantity must be a whole number of at least 1.")

    @property
    def budget_per_unit(self) -> Fraction:
        return _exact(self.budget_rs) / self.quantity


class BenchmarkLike(Protocol):
    """What this module needs from an industry benchmark row. `db.repository.Benchmark` fits."""

    @property
    def attribute_key(self) -> str: ...

    @property
    def label(self) -> str: ...

    @property
    def criterion_id(self) -> int: ...


class CriterionLike(Protocol):
    """What this module needs from a criterion. `db.repository.Criterion` fits."""

    @property
    def criterion_id(self) -> int: ...

    @property
    def weight(self) -> float: ...


@dataclass(frozen=True)
class Graded:
    grade: str
    reason: str  # the facts the grade is based on, in words
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class AttributeGrade:
    attribute_key: str
    label: str
    criterion_id: int
    grade: str       # Above / Meets / Below / Not stated
    points: int      # 100 / 75 / 25 / 0
    weight: float    # this attribute's share of its criterion's weight
    reason: str


@dataclass(frozen=True)
class IndustryResult:
    score: float                      # 0-100, 2 decimals
    attributes: tuple[AttributeGrade, ...]
    warnings: tuple[str, ...]


# ---------------------------------------------------------------- small helpers

def _exact(value: float | int) -> Fraction:
    """The exact decimal value of a number as written (0.1 stays 1/10)."""
    return Fraction(str(value))


def _round2(value: Fraction) -> float:
    """Round to 2 decimals, halves away from zero (78.125 -> 78.13)."""
    with localcontext() as context:
        context.prec = 60
        exact = Decimal(value.numerator) / Decimal(value.denominator)
        return float(exact.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _number(value: Any) -> float | int | None:
    """A real number, or None. True/False, text and NaN are not numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def _n(value: float | int | Fraction) -> str:
    return f"{float(value):g}"


def _plural(value: float | int, unit: str) -> str:
    return f"{_n(value)} {unit}" if value == 1 else f"{_n(value)} {unit}s"


def _rupees(value: Fraction) -> str:
    """Whole rupees with Indian digit grouping: 4999999 -> 'Rs 49,99,999'."""
    whole = str(math.floor(value + Fraction(1, 2)))
    if len(whole) <= 3:
        return f"Rs {whole}"
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return "Rs " + ",".join(groups + [tail])


# ---------------------------------------------------------------- grading rules, one per attribute_key

def _grade_unit_price(facts: Facts, terms: PurchaseTerms | None) -> Graded:
    """Below: over the budget per unit. Meets: 75% to 100% of it. Above: under 75% of it."""
    if terms is None:
        return Graded(
            NOT_STATED,
            "No budget given, so price was not compared",
            ("No budget and quantity given, so unit price was not graded.",),
        )
    price = _number(facts.get("unit_price_rs"))
    estimated = False
    if price is None or price <= 0:
        total = _number(facts.get("grand_total_rs"))
        if total is None or total <= 0:
            return Graded(NOT_STATED, "Unit price not stated")
        value = _exact(total) / GST_DIVISOR / terms.quantity
        estimated = True
    else:
        value = _exact(price)
    budget = terms.budget_per_unit
    if value > budget:
        grade = BELOW
    elif value >= budget * PRICE_MEETS_FROM_SHARE:
        grade = MEETS
    else:
        grade = ABOVE
    how = ", estimated from the total" if estimated else ""
    reason = f"{_rupees(value)} per laptop{how} (budget {_rupees(budget)} each)"
    return Graded(grade, reason, ("Unit price estimated from total",) if estimated else ())


def _grade_warranty(facts: Facts, _terms: PurchaseTerms | None) -> Graded:
    years = _number(facts.get("warranty_years"))
    if years is None:
        return Graded(NOT_STATED, "Warranty length not stated")
    kind = facts.get("warranty_type")
    kind_text = kind if kind in ("on-site", "carry-in") else "cover type not stated"
    reason = f"{_plural(years, 'year')}, {kind_text}"
    if years < 3 or kind == "carry-in":
        return Graded(BELOW, reason)
    if kind != "on-site":
        return Graded(BELOW, reason + " (on-site cover not confirmed)")
    repair = _number(facts.get("repair_time_days"))
    if repair is not None and repair <= 1:
        return Graded(ABOVE, reason + f", repair in {_plural(repair, 'day')}")
    return Graded(MEETS, reason)


def _grade_repair_time(facts: Facts, _terms: PurchaseTerms | None) -> Graded:
    if facts.get("replacement_within_24h") is True:
        return Graded(ABOVE, "Replacement within 24 hours")
    days = _number(facts.get("repair_time_days"))
    if days is None:
        return Graded(NOT_STATED, "Repair time not stated")
    reason = f"Repair in {_plural(days, 'day')}"
    if days <= 1:
        return Graded(ABOVE, reason)
    return Graded(MEETS if days <= 5 else BELOW, reason)


def _grade_battery(facts: Facts, _terms: PurchaseTerms | None) -> Graded:
    hours = _number(facts.get("battery_hours"))
    if hours is None:
        return Graded(NOT_STATED, "Battery life not stated")
    measured = facts.get("battery_measured")
    how = "measured" if measured is True else "not measured" if measured is False else "measurement not stated"
    reason = f"{_n(hours)} h, {how}"
    if hours < 8 or measured is not True:
        return Graded(BELOW, reason)
    return Graded(ABOVE if hours > 10 else MEETS, reason)


def _grade_memory_storage(facts: Facts, _terms: PurchaseTerms | None) -> Graded:
    ram, ssd = _number(facts.get("ram_gb")), _number(facts.get("ssd_gb"))
    if ram is None and ssd is None:
        return Graded(NOT_STATED, "RAM and SSD size not stated")
    reason = ", ".join(
        [f"{_n(ram)} GB RAM" if ram is not None else "RAM not stated",
         f"{_n(ssd)} GB SSD" if ssd is not None else "SSD not stated"]
    )
    if (ram is not None and ram < 16) or (ssd is not None and ssd < 512):
        return Graded(BELOW, reason)  # falling short of either minimum outweighs having more of the other
    if ram is None or ssd is None:
        return Graded(NOT_STATED, reason)
    return Graded(ABOVE if ram > 16 or ssd > 512 else MEETS, reason)


def _grade_delivery(facts: Facts, _terms: PurchaseTerms | None) -> Graded:
    weeks = _number(facts.get("delivery_weeks"))
    if weeks is None:
        return Graded(NOT_STATED, "Delivery time not stated")
    reason = _plural(weeks, "week")
    if weeks > 8:
        return Graded(BELOW, reason)
    return Graded(MEETS if weeks >= 6 else ABOVE, reason)


def _grade_certifications(facts: Facts, _terms: PurchaseTerms | None) -> Graded:
    bis, energy, security = (
        facts.get("bis_named"), facts.get("energy_rating_named"), facts.get("security_certification_named")
    )
    if bis is None and energy is None and security is None:
        return Graded(NOT_STATED, "No certifications stated")
    named = [label for label, value in (("BIS", bis), ("energy rating", energy), ("security certification", security)) if value is True]
    reason = (", ".join(named) + " named") if named else "None named"
    if bis is True and energy is True:
        return Graded(ABOVE if security is True else MEETS, reason)
    return Graded(BELOW, reason)


def _grade_data_wiping(facts: Facts, _terms: PurchaseTerms | None) -> Graded:
    wipe = facts.get("data_wipe")
    if wipe == "certified_standard":
        return Graded(ABOVE, "Certificate per device to a named standard")
    if wipe == "described":
        return Graded(MEETS, "Described")
    return Graded(NOT_STATED, "Data wiping not described")


# attribute_key (as seeded in `industry_benchmarks`) -> grading rule
GRADERS: dict[str, Callable[[Facts, PurchaseTerms | None], Graded]] = {
    "unit_price": _grade_unit_price,
    "warranty": _grade_warranty,
    "repair_time": _grade_repair_time,
    "battery": _grade_battery,
    "memory_storage": _grade_memory_storage,
    "delivery": _grade_delivery,
    "certifications": _grade_certifications,
    "data_wiping": _grade_data_wiping,
}


def industry_score(
    facts: Facts | None,
    benchmarks: Sequence[BenchmarkLike],
    criteria: Sequence[CriterionLike],
    terms: PurchaseTerms | None,
) -> IndustryResult:
    """Grade every benchmark attribute from one supplier's extracted facts and combine them.

    `criteria` are the active criteria (their weights decide each attribute's share); a benchmark whose
    criterion is not in that list is skipped. `terms` is the run's budget and quantity, which set the price
    bands; with None the price is simply not graded (Not stated, with a warning). Score = sum(points x share) /
    sum(shares), rounded to 2 decimals.
    """
    facts = facts or {}
    weights = {c.criterion_id: _exact(c.weight) for c in criteria}
    applicable = [b for b in benchmarks if b.criterion_id in weights]
    per_criterion: dict[int, int] = {}
    for benchmark in applicable:
        per_criterion[benchmark.criterion_id] = per_criterion.get(benchmark.criterion_id, 0) + 1

    warnings: list[str] = []
    grades: list[AttributeGrade] = []
    weighted_points = Fraction(0)
    total_share = Fraction(0)
    for benchmark in applicable:
        share = weights[benchmark.criterion_id] / per_criterion[benchmark.criterion_id]
        grader = GRADERS.get(benchmark.attribute_key)
        if grader is None:
            graded = Graded(NOT_STATED, f"No grading rule for '{benchmark.attribute_key}'")
            warnings.append(f"No grading rule for industry attribute '{benchmark.attribute_key}'; it was graded Not stated.")
        else:
            graded = grader(facts, terms)
            warnings.extend(graded.warnings)
        points = POINTS[graded.grade]
        weighted_points += points * share
        total_share += share
        grades.append(
            AttributeGrade(
                attribute_key=benchmark.attribute_key,
                label=benchmark.label,
                criterion_id=benchmark.criterion_id,
                grade=graded.grade,
                points=points,
                weight=float(share),
                reason=graded.reason,
            )
        )

    if total_share == 0:
        warnings.append("No industry attributes carry any weight, so the industry score is 0.")
        score = 0.0
    else:
        score = _round2(weighted_points / total_share)
    return IndustryResult(score=score, attributes=tuple(grades), warnings=tuple(warnings))
