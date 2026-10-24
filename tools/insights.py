"""Insights (spec section 10): decisions that are made in Python and only worded by the LLM.

F05 adds the strength labels (spec 9.6). F10 adds the recommendation (spec 10.2). Change pack R1a adds the pros and
cons selection (spec 10.1, worded here by fixed Python templates until F16 lets the LLM word them), the plain
labels shown on screen, and the price facts. The what-if table and the negotiation selection are added by later
features. Pure functions only: no LLM, no I/O, no randomness, no clock.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from fractions import Fraction

from tools import ranking

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


def _exact(value: float | int) -> Fraction:
    return Fraction(str(value))


def _is_amount(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) != float("inf")


METER_DOTS = 5   # the dots in the small meter beside each criterion on a scorecard


def meter_dots(score: object, max_score: object, dots: int = METER_DOTS) -> int | None:
    """How many of the `dots` are filled for a mark: score / max_score x dots, rounded to the nearest whole dot.

    Exactly half a dot rounds up (5 out of 10 on 5 dots is 2.5, so 3 filled), which keeps the meter in step with the strength
    label (50% is Average, not Weak). A mark above the maximum fills every dot and a negative mark fills none. None when
    there is no meter to draw: the mark or the maximum is missing or not a number, or the maximum is 0 or less.
    """
    if not _is_amount(score) or not _is_amount(max_score) or max_score <= 0:    # type: ignore[operator]
        return None
    share = min(max(_exact(score) / _exact(max_score), Fraction(0)), Fraction(1))   # type: ignore[arg-type]
    return int(share * dots + Fraction(1, 2))     # the share is not negative, so int() rounds down: this is "half up"


# ---------------------------------------------------------------- price facts

LAKH = 100_000


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
    key: str = field(default="", compare=False)    # what it is about ("criterion:3", "gate:bis"): how the AI's wording finds its item again


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
    pros = [Reason(_pro_text(c), BASIS_BATCH, KIND_CRITERION, f"criterion:{c.criterion_id}") for c in pro_items]
    if gates and all(g.passed for g in gates):
        pros.append(Reason("Meets every must-have", BASIS_REQUIREMENT, KIND_ALL_GATES, "all_gates"))

    cons = [Reason(f"Not eligible: {_flat(g.name)} \u2014 {_flat(g.reason)}", BASIS_REQUIREMENT, KIND_GATE, f"gate:{g.gate_id}")
            for g in gates if not g.passed]
    used: set[int] = set()
    behind = sorted((c for c in criteria if c.gap is not None and -c.gap >= BEHIND_BY), key=lambda c: -weighted(c, -c.gap))   # type: ignore[operator]
    for item in behind:
        cons.append(Reason(_con_text(item), BASIS_BATCH, KIND_CRITERION, f"criterion:{item.criterion_id}"))
        used.add(item.criterion_id)
    for item in criteria:
        if item.evidence_found is False and item.criterion_id not in used:
            cons.append(Reason(f"Evidence for {plain_label(item.name)} was not found in the proposal", BASIS_EVIDENCE, KIND_EVIDENCE,
                            f"evidence:{item.criterion_id}"))
            used.add(item.criterion_id)
    for item in criteria:
        if item.filled_by_validator and item.score == 0 and item.criterion_id not in used:
            cons.append(Reason(f"{plain_label(item.name)} not stated in the proposal", BASIS_NOT_STATED, KIND_NOT_STATED,
                            f"not_stated:{item.criterion_id}"))
            used.add(item.criterion_id)
    return ProsCons(tuple(pros[:PROS_MAX]), tuple(cons[:CONS_MAX]))


# ---------------------------------------------------------------- the AI's wording of the chosen points (spec 10.1, feature F16)

def apply_wording(chosen: ProsCons, wording: object) -> ProsCons:
    """The same points, in the AI's words where it gave some. `wording` is what the run saved: {"pros": {key: text}, "cons": {key: text}}.
    A point whose key has no wording (the choice changed after a re-rank, or the AI left it out) keeps its plain template text, so
    nothing is ever shown that was not chosen by Python and nothing is lost. Which points there are, their order and their label
    never come from the AI."""
    if not isinstance(wording, dict):
        return chosen

    def swap(items: tuple[Reason, ...], words: object) -> tuple[Reason, ...]:
        if not isinstance(words, dict):
            return items
        return tuple(replace(r, text=" ".join(str(words[r.key]).split())) if r.key and isinstance(words.get(r.key), str) and str(words[r.key]).strip()
                     else r for r in items)

    return ProsCons(swap(chosen.pros, wording.get("pros")), swap(chosen.cons, wording.get("cons")))


def reason_for(chosen: ProsCons, wording: object) -> str | None:
    """The AI's one-sentence reason for recommending a supplier, only while it still fits: it was written from certain pros, so it is
    shown only when the supplier's pros are still exactly those (a re-rank that changes them hides it)."""
    if not isinstance(wording, dict):
        return None
    reason, basis = wording.get("reason"), wording.get("reason_for")
    if not isinstance(reason, str) or not reason.strip() or not isinstance(basis, list):
        return None
    return " ".join(reason.split()) if [r.key for r in chosen.pros] == basis and basis else None


# ---------------------------------------------------------------- what-if (spec 10.3, feature F18)

WHAT_IF_WEIGHTS: tuple[int, ...] = (10, 15, 20, 25, 30, 35, 40)     # the price weights tried, in percent


@dataclass(frozen=True)
class WhatIfLine:
    """One supplier's Peer score (PPI) at each price weight, in the order of `WhatIf.weights`."""

    name: str
    ppis: tuple[float, ...]


@dataclass(frozen=True)
class LeaderChange:
    """The leader differs between two neighbouring price weights."""

    from_weight: int
    to_weight: int
    from_leader: str | None
    to_leader: str | None


@dataclass(frozen=True)
class WhatIf:
    weights: tuple[int, ...]
    lines: tuple[WhatIfLine, ...]
    leaders: tuple[str | None, ...]        # the top-ranked eligible supplier at each weight; None when nobody is eligible
    changes: tuple[LeaderChange, ...]
    current_weight: float                  # the price weight this run was ranked with


def what_if(suppliers: Sequence[ranking.SupplierInput], eligible: Sequence[str], criteria: Sequence[ranking.CriterionWeight],
            price_id: int, weights: Sequence[int] = WHAT_IF_WEIGHTS) -> WhatIf | None:
    """Rank everyone again with the price criterion at each weight in `weights` and the other weights scaled to keep the total at 100.

    The marks are the saved ones: nothing is marked again. `eligible` names the suppliers that meet every must-have; the leader is the
    top-ranked of them (a supplier that fails a must-have is never the leader, as in the recommendation). Returns None when there is
    no price criterion or no other weight to scale, so the question cannot be asked.
    """
    price = next((c for c in criteria if c.criterion_id == price_id), None)
    others = [c for c in criteria if c.criterion_id != price_id]
    other_total = sum((Fraction(c.weight) for c in others), Fraction(0))
    if price is None or not suppliers or other_total <= 0:
        return None
    allowed = {n.lower() for n in eligible}
    leaders: list[str | None] = []
    ppis: dict[str, list[float]] = {s.name: [] for s in suppliers}
    for p in weights:
        factor = Fraction(100 - p) / other_total
        scaled = [ranking.CriterionWeight(price.criterion_id, float(p), price.max_score)]
        scaled += [ranking.CriterionWeight(c.criterion_id, float(Fraction(c.weight) * factor), c.max_score) for c in others]
        ranked = ranking.rank_suppliers(suppliers, scaled).suppliers
        for r in ranked:
            ppis[r.name].append(r.ppi)
        leaders.append(next((r.name for r in ranked if r.name.lower() in allowed), None))
    changes = tuple(LeaderChange(weights[i], weights[i + 1], leaders[i], leaders[i + 1])
                    for i in range(len(weights) - 1) if leaders[i] != leaders[i + 1])
    return WhatIf(tuple(weights), tuple(WhatIfLine(s.name, tuple(ppis[s.name])) for s in suppliers), tuple(leaders), changes, price.weight)


# ---------------------------------------------------------------- the price score (Commercial Value from the real totals)

PRICE_CRITERION = "commercial value"     # the criterion that stands for price in the seeded marking scheme (compared in lower case)


def is_price_criterion(name: object) -> bool:
    return isinstance(name, str) and name.strip().casefold() == PRICE_CRITERION


@dataclass(frozen=True)
class PriceMarks:
    """The price marks of one batch: `marks` and `totals_rs` by supplier name, for the suppliers that state a total."""

    reference_rs: float                     # the lowest total in the batch: it gets the full mark
    marks: dict[str, float]
    totals_rs: dict[str, float]
    without_total: tuple[str, ...]          # suppliers with no usable total: they keep the AI's mark


def price_marks(totals: Mapping[str, object], max_score: float) -> PriceMarks | None:
    """The Commercial Value mark worked out from the totals: the lowest total gets `max_score`, every other supplier gets
    max_score x lowest / its total (2 decimals, half up). The AI reads a price table well but does not weigh how much cheaper
    one offer is, so the price gap is worked out here. Needs at least two suppliers with a usable total (positive number);
    otherwise returns None and every mark stays as the AI gave it."""
    usable = {name: float(v) for name, v in totals.items() if _is_amount(v) and v > 0}      # type: ignore[operator]
    if len(usable) < 2 or not _is_amount(max_score) or max_score <= 0:
        return None
    lowest = min(usable.values(), key=_exact)
    marks = {}
    for name, total in usable.items():
        hundredths = int(_exact(max_score) * _exact(lowest) / _exact(total) * 100 + Fraction(1, 2))
        marks[name] = hundredths / 100
    return PriceMarks(lowest, marks, usable, tuple(n for n in totals if n not in usable))


def _lakh_number(rs: float) -> str:
    return format_lakh(rs).removeprefix("Rs ").removesuffix(" lakh")


def price_reason(total_rs: float, reference_rs: float, mark: float, max_score: float) -> str:
    """Why a supplier got its price mark, in plain words (shown as the justification of the Commercial Value mark)."""
    top = f"{max_score:g}"
    if total_rs == reference_rs:
        return (f"Price mark worked out from the totals, not marked by the AI: this is the lowest total in this batch "
                f"({format_lakh(total_rs)}), so it gets the full {top} marks.")
    return (f"Price mark worked out from the totals, not marked by the AI: the lowest total in this batch is {format_lakh(reference_rs)} "
            f"and this total is {format_lakh(total_rs)}, so {top} x {_lakh_number(reference_rs)} / {_lakh_number(total_rs)} = {mark:g}.")


# ---------------------------------------------------------------- negotiation selection (spec 10.4)

NEGOTIATION_MAX = 5
GAP_FROM = 2                                  # a criterion this many points behind the best offer in the batch is worth raising
TOPIC_GATE, TOPIC_BUDGET, TOPIC_CRITERION, TOPIC_RISK, TOPIC_EVIDENCE, TOPIC_STARTUP = (
    "gate", "budget", "criterion", "risk", "evidence", "startup")

GATE_ASKS = {
    "warranty": "Ask the supplier to extend the warranty to at least 3 years.",
    "certification": "Ask for the BIS certificate before signing.",
    "delivery": "Ask for delivery within 8 weeks, with a penalty clause for late delivery.",
}
CRITERION_LEVERS = {
    "technical capability": "Ask for a better configuration at the same price, for example 32 GB RAM or a larger SSD.",
    "implementation plan": "Ask for a named project manager, a 5-unit pilot, and milestone dates with penalties.",
    "commercial value": "Ask for a discount, free imaging and setup, and 10% of the payment held until acceptance.",
    "security & compliance": "Ask for a data-wipe certificate per device, encryption before delivery, and audit logs.",
    "support & experience": "Ask for on-site warranty, a response-time commitment, and spare replacement units.",
}
STARTUP_LEVERS = (
    "Ask for a 12-month price lock for follow-on orders.",
    "Ask whether payment in instalments or leasing is possible.",
    "Ask for free bags and chargers.",
    "Ask for delivery to remote hires.",
)
RISK_CHARS_MAX = 160


@dataclass(frozen=True)
class NegotiationPoint:
    key: str                 # "gate:warranty", "budget", "criterion:3", "risk:1", "evidence:3", "startup:1"
    topic: str               # TOPIC_*
    text: str                # the plain wording (always shown when the AI is not used or its line is rejected)
    detail: str = ""         # the supplier's own words behind it, for the AI (never another supplier's)


def _flat_text(text: object) -> str:
    return " ".join(str(text or "").split())


def negotiation_points(
    gates: Sequence[GateOutcome],
    grand_total_rs: object,
    budget_rs: object,
    criteria: Sequence[CriterionResult],
    risks: Sequence[str],
    *,
    details: Mapping[int, str] | None = None,
) -> tuple[NegotiationPoint, ...]:
    """At most five points for ONE supplier, in the order of the spec: unmet requirements, the budget, criterion gaps (by
    weight x gap), the AI's risks, evidence not found, then the startup levers while there is room. Nothing here uses another
    supplier's name or price. A criterion's gap is measured against the batch best, but only the size of the gap is used."""
    found: list[NegotiationPoint] = []
    for gate in gates:
        if not gate.passed:
            found.append(NegotiationPoint(f"gate:{gate.gate_id}", TOPIC_GATE, GATE_ASKS.get(
                gate.gate_id, f"Ask the supplier to resolve this before signing: {gate.name}."), _flat_text(gate.reason)))
    price = price_facts({"grand_total_rs": grand_total_rs}, budget_rs)
    if price.within_budget is False and price.over_by_rs is not None and price.budget_rs is not None:
        found.append(NegotiationPoint(
            "budget", TOPIC_BUDGET,
            f"The total is {format_lakh(price.total_rs)}, {format_lakh(price.over_by_rs)} over our {format_lakh(price.budget_rs)} budget. "
            "Ask for a reduction to within budget, a volume discount, or bundled services.",
            f"total {format_lakh(price.total_rs)}; budget {format_lakh(price.budget_rs)}; over by {format_lakh(price.over_by_rs)}"))
    behind = []
    for order, c in enumerate(criteria):
        label = strength_label(c.score, c.max_score)
        gap = c.gap if c.gap is not None else 0
        if (-gap >= GAP_FROM or label in (WEAK, AVERAGE)) and not c.filled_by_validator:
            behind.append((float(_exact(c.weight) * abs(_exact(gap))), float(_exact(c.weight) * (_exact(c.max_score) - _exact(c.score))), -order, c))
    for _w, _s, _o, c in sorted(behind, key=lambda t: (t[0], t[1], t[2]), reverse=True):
        lever = CRITERION_LEVERS.get(c.name.strip().casefold(), f"Ask what the supplier can improve on {c.name}.")
        note = "" if is_price_criterion(c.name) else _flat_text((details or {}).get(c.criterion_id))
        found.append(NegotiationPoint(f"criterion:{c.criterion_id}", TOPIC_CRITERION, f"On {c.name}: {lever[0].lower()}{lever[1:]}", note))
    for number, risk in enumerate((r for r in risks if _flat_text(r)), start=1):
        said = _flat_text(risk)[:RISK_CHARS_MAX]
        found.append(NegotiationPoint(f"risk:{number}", TOPIC_RISK, f"Add a contract clause that covers this risk: {said}", said))
    for c in criteria:
        if c.evidence_found is False:
            found.append(NegotiationPoint(f"evidence:{c.criterion_id}", TOPIC_EVIDENCE,
                                          f"On {c.name}: ask for written proof of the claim, or make it a contract commitment.", ""))
    for number, lever in enumerate(STARTUP_LEVERS, start=1):
        found.append(NegotiationPoint(f"startup:{number}", TOPIC_STARTUP, lever, ""))
    return tuple(found[:NEGOTIATION_MAX])
