"""Insights (spec section 10): decisions that are made in Python and only worded by the LLM.

F05 adds the strength labels (spec 9.6). F10 adds the recommendation (spec 10.2). Change pack R1a adds the pros and
cons selection (spec 10.1, worded here by fixed Python templates until F16 lets the LLM word them), the plain
labels shown on screen, and the price facts. The what-if table and the negotiation selection are added by later
features. Pure functions only: no LLM, no I/O, no randomness, no clock.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

STRONG = "Strong"
AVERAGE = "Average"
WEAK = "Weak"

STRONG_FROM = Fraction(4, 5)   # score / max_score >= 0.80
AVERAGE_FROM = Fraction(1, 2)  # 0.50 up to (not including) 0.80; below 0.50 is Weak


def strength_label(score: float, max_score: float) -> str:
    """Strong (>= 80% of the maximum), Average (50% to under 80%) or Weak (under 50%).

    Calculated on the numbers as written, so 2.4 out of 3 is exactly 80% and counts as Strong.
    Call it again after any override so the label follows the new score.
    """
    maximum = Fraction(str(max_score))
    if maximum <= 0:
        raise ValueError("max_score must be above 0.")
    share = Fraction(str(score)) / maximum
    if share >= STRONG_FROM:
        return STRONG
    if share >= AVERAGE_FROM:
        return AVERAGE
    return WEAK


# ---------------------------------------------------------------- recommendation (spec 10.2)

INDUSTRY_WARNING_BELOW = 60   # a recommended supplier with an industry score under this gets a warning


@dataclass(frozen=True)
class Contender:
    """A supplier as the recommendation sees it: where it ranks and whether it may be recommended.

    "May be recommended" means it passes every mandatory check. Later features also mark a supplier the buyer set
    aside as not eligible, so this function does not need to change.
    """

    name: str
    rank: int
    eligible: bool


@dataclass(frozen=True)
class Recommendation:
    recommended: str | None   # the highest-ranked eligible supplier, or None when nobody is eligible
    runner_up: str | None     # the next eligible supplier after it (None when there is no other eligible one)
    top_ranked: str | None    # the supplier ranked 1, eligible or not (None only when there are no suppliers)

    @property
    def none_eligible(self) -> bool:
        return self.top_ranked is not None and self.recommended is None


def choose_recommendation(contenders: Sequence[Contender]) -> Recommendation:
    """Recommended = the highest-ranked eligible supplier; runner-up = the next eligible one after it.

    Suppliers that are not eligible are still ranked, but are never recommended and never the runner-up
    (a runner-up is the fallback if the recommendation is not accepted, so it has to be recommendable too).
    """
    ordered = sorted(contenders, key=lambda c: c.rank)
    eligible = [c for c in ordered if c.eligible]
    return Recommendation(
        recommended=eligible[0].name if eligible else None,
        runner_up=eligible[1].name if len(eligible) > 1 else None,
        top_ranked=ordered[0].name if ordered else None,
    )


def industry_warning(industry_score: float, weak_attributes: Sequence[str]) -> str | None:
    """The warning for a recommended supplier whose industry score is under 60, else None.

    `weak_attributes` are the labels of the attributes graded Below or Not stated (the caller picks them).
    """
    if industry_score >= INDUSTRY_WARNING_BELOW:
        return None
    names = ", ".join(weak_attributes) if weak_attributes else "several attributes"
    return f"Below industry standard on: {names}"


# ---------------------------------------------------------------- plain wording for the screen (change pack R1)

# Screen text only. The marking scheme, prompts, database and JSON export keep the real names.
CRITERION_LABELS: dict[str, str] = {
    "Technical Capability": "Laptop specs",
    "Implementation Plan": "Delivery plan",
    "Commercial Value": "Value for money",
    "Security & Compliance": "Security",
    "Support & Experience": "Support",
}

# A saved run keeps each gate's id and name, not its rule key. The seeded gate ids map one to one to the rules.
GATE_LABELS: dict[str, str] = {
    "warranty": "3-yr warranty",
    "certification": "BIS certified",
    "price_table": "Price list",
    "delivery": "Delivery \u2264 8 wks",
}

# gate_id -> (what is wrong when the proposal gives a value that fails, what is wrong when the value is not stated)
_GATE_PROBLEMS: dict[str, tuple[str, str]] = {
    "warranty": ("warranty under 3 years", "warranty not stated"),
    "certification": ("BIS certificate not named", "BIS certificate not named"),
    "price_table": ("no price list", "no price list"),
    "delivery": ("delivery over 8 weeks", "delivery time not stated"),
}
_GATE_NOT_STATED = "not stated"   # the reason tools/gates.py gives when the proposal does not say (tests keep the two in step)


def _flat(text: object) -> str:
    return " ".join(str(text).split())


def plain_label(criterion_name: str) -> str:
    """The everyday name of a criterion for the screen; a criterion without one shows its own name."""
    return CRITERION_LABELS.get(criterion_name, criterion_name)


def gate_label(gate_id: str, gate_name: str) -> str:
    """The short column name of a must-have; a gate without one shows its own name."""
    return GATE_LABELS.get(gate_id, gate_name)


def gate_problem(gate_id: str, gate_name: str, reason: str) -> str:
    """What is wrong, in a few lower-case words ("no price list"). An unknown gate gives its name and reason."""
    known = _GATE_PROBLEMS.get(gate_id)
    if known is None:
        detail = _flat(reason)
        return f"{_flat(gate_name)}: {detail}" if detail else _flat(gate_name)
    return known[1] if _flat(reason).lower() == _GATE_NOT_STATED else known[0]


# ---------------------------------------------------------------- price facts

LAKH = 100_000


def _exact(value: float | int) -> Fraction:
    return Fraction(str(value))


def _is_amount(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) != float("inf")


def format_lakh(rupees: float | int) -> str:
    """Rupees as lakh with at most two decimals: 5600000 -> "Rs 56 lakh", 1398600 -> "Rs 13.99 lakh".

    A positive amount never shows as 0 (Rs 1 over a budget is "Rs 0.01 lakh"). Not for negative amounts.
    """
    if rupees < 0:
        raise ValueError("rupees must not be negative.")
    exact = _exact(rupees)
    hundredths = int(exact * 100 / LAKH + Fraction(1, 2))     # rounds half up (the value is not negative)
    if hundredths == 0 and exact > 0:
        hundredths = 1
    whole, part = divmod(hundredths, 100)
    return f"Rs {whole}" + (f".{part:02d}".rstrip("0") if part else "") + " lakh"


@dataclass(frozen=True)
class PriceFacts:
    total_rs: float | None          # the grand total in the proposal; None when it is not stated
    budget_rs: float | None         # the budget of the run; None for a run saved without one
    within_budget: bool | None      # None when the total or the budget is unknown
    over_by_rs: float | None        # how far the total is above the budget; None unless it is above


def price_facts(extracted_facts: object, budget_rs: object) -> PriceFacts:
    """The proposal's grand total against the run's budget. A total that is missing, zero or not a number is "not stated"."""
    raw = extracted_facts.get("grand_total_rs") if isinstance(extracted_facts, dict) else None
    total = float(raw) if _is_amount(raw) and raw > 0 else None   # type: ignore[operator]
    budget = float(budget_rs) if _is_amount(budget_rs) and budget_rs > 0 else None   # type: ignore[operator]
    if total is None or budget is None:
        return PriceFacts(total, budget, None, None)
    over = _exact(total) > _exact(budget)
    return PriceFacts(total, budget, not over, float(_exact(total) - _exact(budget)) if over else None)


# ---------------------------------------------------------------- pros and cons (spec 10.1)

PROS_MAX = 3
CONS_MAX = 3
NEAR_BEST_PCT = 90      # relative % from which a supplier counts as close to the best bid
BEHIND_BY = 3           # points behind the benchmark from which a criterion is a weakness

BASIS_BATCH = "vs this batch"
BASIS_REQUIREMENT = "vs requirement"
BASIS_EVIDENCE = "evidence"
BASIS_NOT_STATED = "not stated"

KIND_CRITERION = "criterion"
KIND_ALL_GATES = "all_gates"
KIND_GATE = "gate"
KIND_EVIDENCE = "evidence"
KIND_NOT_STATED = "not_stated"


@dataclass(frozen=True)
class CriterionResult:
    """One criterion of one supplier, with its benchmark figures from the ranking."""

    criterion_id: int
    name: str
    score: float
    max_score: float
    weight: float
    benchmark: float | None = None
    gap: float | None = None                   # score - benchmark: 0 for the leader, negative otherwise
    relative_pct: float | None = None
    filled_by_validator: bool = False          # the model's reply had no result for it, so it was marked 0
    evidence_found: bool | None = None         # None until the evidence checker (F15) has run


@dataclass(frozen=True)
class GateOutcome:
    gate_id: str
    name: str
    passed: bool
    reason: str


@dataclass(frozen=True)
class Reason:
    text: str
    basis: str      # what it is measured against: BASIS_*
    kind: str       # which rule selected it: KIND_*


@dataclass(frozen=True)
class ProsCons:
    pros: tuple[Reason, ...]
    cons: tuple[Reason, ...]


def _n(value: float) -> str:
    return f"{value:g}"


def _marks(item: CriterionResult) -> str:
    return f"{_n(item.score)}/{_n(item.max_score)}"


def _best(item: CriterionResult) -> str:
    return f"best bid {_n(item.benchmark or 0)}/{_n(item.max_score)}"


def _strong(item: CriterionResult) -> bool:
    return item.max_score > 0 and strength_label(item.score, item.max_score) == STRONG


def _pro_text(item: CriterionResult) -> str:
    label = plain_label(item.name)
    if item.gap is not None and item.gap == 0:
        if _strong(item):
            return f"Strong on {label} ({_marks(item)}, best of all bids)"
        return f"Best of all bids on {label} ({_marks(item)})"
    return f"Close to the best bid on {label} ({_marks(item)}; {_best(item)})"


def _con_text(item: CriterionResult) -> str:
    label = plain_label(item.name)
    if item.filled_by_validator:
        return f"No result for {label} (counted as {_marks(item)}; {_best(item)})"
    return f"Weaker on {label} ({_marks(item)}; {_best(item)})"


def _is_pro(item: CriterionResult) -> bool:
    if item.benchmark is None or item.benchmark <= 0:      # with a benchmark of 0 everybody is "100%", which says nothing
        return False
    leader = item.gap is not None and item.gap == 0
    close = item.relative_pct is not None and item.relative_pct >= NEAR_BEST_PCT
    return leader or close


def select_pros_cons(criteria: Sequence[CriterionResult], gates: Sequence[GateOutcome]) -> ProsCons:
    """Choose what is good and what to watch for one supplier (spec 10.1). Python selects; the wording is a fixed template.

    Pros (at most 3): criteria where the supplier is the benchmark leader or has a relative % of 90 or more, ordered by
    weight x score (highest first); then "Meets every must-have" when there are gates and every one passed (it has no
    score, so it follows the criteria).
    Cons (at most 3), in this priority: failed gates; criteria 3 or more points behind the benchmark, ordered by
    weight x |gap|; evidence not found; criteria the validator filled with 0. A criterion appears once among the cons.
    """
    def weighted(item: CriterionResult, amount: float) -> Fraction:
        return _exact(item.weight) * _exact(amount)

    pro_items = sorted((c for c in criteria if _is_pro(c)), key=lambda c: -weighted(c, c.score))
    pros = [Reason(_pro_text(c), BASIS_BATCH, KIND_CRITERION) for c in pro_items]
    if gates and all(g.passed for g in gates):
        pros.append(Reason("Meets every must-have", BASIS_REQUIREMENT, KIND_ALL_GATES))

    cons = [Reason(f"Not eligible: {_flat(g.name)} \u2014 {_flat(g.reason)}", BASIS_REQUIREMENT, KIND_GATE)
            for g in gates if not g.passed]
    used: set[int] = set()
    behind = sorted((c for c in criteria if c.gap is not None and -c.gap >= BEHIND_BY), key=lambda c: -weighted(c, -c.gap))   # type: ignore[operator]
    for item in behind:
        cons.append(Reason(_con_text(item), BASIS_BATCH, KIND_CRITERION))
        used.add(item.criterion_id)
    for item in criteria:
        if item.evidence_found is False and item.criterion_id not in used:
            cons.append(Reason(f"Evidence for {plain_label(item.name)} was not found in the proposal", BASIS_EVIDENCE, KIND_EVIDENCE))
            used.add(item.criterion_id)
    for item in criteria:
        if item.filled_by_validator and item.score == 0 and item.criterion_id not in used:
            cons.append(Reason(f"{plain_label(item.name)} not stated in the proposal", BASIS_NOT_STATED, KIND_NOT_STATED))
            used.add(item.criterion_id)
    return ProsCons(tuple(pros[:PROS_MAX]), tuple(cons[:CONS_MAX]))
