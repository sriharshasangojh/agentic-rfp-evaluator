"""Satisfaction and priorities (feature F13, spec section 11.2): the plain-Python rules behind the Review tab's question.

The person who reviews a result is a buyer, not an assessor. They do not read the proposals and they cannot mark "Implementation
Plan"; they know what they need. So the Review tab asks one question, "How satisfied are you with this recommendation, from 1 to 5?":

* 4 or 5: satisfied. The recommendation can be accepted.
* 1 to 3: not satisfied. They say what they are not satisfied with (a plain option, or their own words), the matching criterion
  counts for more in this run, everyone is ranked again from the AI's saved marks, and the new result is shown. At most 3 rounds.

Pure functions only: no I/O, no Streamlit, no LLM, no clock. The Review tab and `agents/orchestrator.py` call these, so the rules are
written once and tested on their own.

The reweight rule (spec 11.2): the chosen criterion's weight becomes min(current + 10, 60); every other weight is scaled by the same
factor so the total is exactly 100 (2 decimals; any rounding remainder goes on the largest other weight). It applies to this run only.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from tools.review import ReviewError, clean_text

SCALE = (1, 2, 3, 4, 5)
SATISFIED_AT = 4                # a score at or above this is "satisfied": the benchmark
MAX_ROUNDS = 3                  # rounds of "not satisfied, change the priorities" per run (spec 11.2 says 2; the user asked for 3)
STEP = Decimal("10")            # points added to the chosen criterion
CAP = Decimal("60")             # a criterion never counts for more than this (percent of the decision)
TOTAL = Decimal("100")
NEED_MAX_CHARS = 300            # the longest typed need; it is also sent to the AI, so it is kept short
CENT = Decimal("0.01")

NONE_OF_THE_ABOVE = "None of the above, I will write it myself"

# What a buyer reads for each criterion of the seeded marking scheme (keyed by the criterion's name, in lower case). A criterion with
# any other name gets "<name> matters more to us".
PLAIN_REASONS = {
    "technical capability": "The laptops should be better (battery life, speed, build quality)",
    "implementation plan": "Delivery and setup should be quicker or more dependable",
    "commercial value": "The price is too high or goes over our budget",
    "security & compliance": "We need stronger security and data protection",
    "support & experience": "We need better warranty, repair and support",
}

ACTION_REWEIGHT = "reweight"
UNSUPPORTED_ACTIONS = {
    "re_evaluate": "That sounds like a mark you think is wrong. Marks are not changed here: they are the AI's reading of the proposals.",
    "set_aside": "That sounds like you want to leave a supplier out. That is not possible here yet.",
}


# ---------------------------------------------------------------- satisfaction

def check_satisfaction(value: object) -> int:
    """The satisfaction score as a whole number from 1 to 5. Raises ReviewError when it is anything else."""
    if isinstance(value, bool) or not isinstance(value, int) or value not in SCALE:
        raise ReviewError("Choose how satisfied you are, from 1 to 5.")
    return value


def is_satisfied(score: int) -> bool:
    return check_satisfaction(score) >= SATISFIED_AT


class ActionLike(Protocol):
    """What the round count needs from a saved reviewer action. `db.repository.FeedbackRow` fits."""

    @property
    def action(self) -> str: ...


def rounds_used(events: Iterable[ActionLike]) -> int:
    """How many "not satisfied" rounds this run has had (one per saved change of priorities)."""
    return sum(1 for e in events if e.action == ACTION_REWEIGHT)


def rounds_left(events: Iterable[ActionLike]) -> int:
    return max(0, MAX_ROUNDS - rounds_used(events))


def clean_need(text: object) -> str:
    """A typed need as saved and sent to the AI: surrounding space removed, at most NEED_MAX_CHARS long."""
    return " ".join(str(text or "").split())[:NEED_MAX_CHARS].strip()


# ---------------------------------------------------------------- what the person can pick

@dataclass(frozen=True)
class Option:
    criterion_id: int
    label: str                  # the plain sentence the person reads


class CriterionLike(Protocol):
    @property
    def criterion_id(self) -> int: ...

    @property
    def name(self) -> str: ...


def options_for(criteria: Sequence[CriterionLike]) -> tuple[Option, ...]:
    """One option per criterion of the run, in marking-scheme order, worded for a buyer."""
    return tuple(Option(c.criterion_id, PLAIN_REASONS.get(c.name.strip().casefold(), f"{c.name.strip()} matters more to us"))
                 for c in criteria)


# ---------------------------------------------------------------- the reweight rule

@dataclass(frozen=True)
class WeightLine:
    criterion_id: int
    name: str
    was: float
    now: float


@dataclass(frozen=True)
class Reweighting:
    target_id: int
    lines: tuple[WeightLine, ...]

    @property
    def target(self) -> WeightLine:
        return next(line for line in self.lines if line.criterion_id == self.target_id)

    def new_weights(self) -> dict[int, float]:
        return {line.criterion_id: line.now for line in self.lines}


class WeightLike(Protocol):
    @property
    def criterion_id(self) -> int: ...

    @property
    def name(self) -> str: ...

    @property
    def weight(self) -> float: ...


def _exact(value: float) -> Decimal:
    """The number as written (0.1 stays 0.1, not its binary approximation)."""
    return Decimal(str(value))


def plan_reweight(criteria: Sequence[WeightLike], target_id: int) -> Reweighting:
    """The new weights after the person is not satisfied with one criterion (spec 11.2).

    Raises ReviewError when the criterion is not part of the run, already counts as much as it may (60), or when there is no other
    weight to take the points from."""
    current = {c.criterion_id: _exact(c.weight) for c in criteria}
    names = {c.criterion_id: c.name for c in criteria}
    if target_id not in current:
        raise ReviewError("That criterion is not part of this run.")
    if current[target_id] >= CAP:
        raise ReviewError(f"{names[target_id]} already counts for as much as it can ({CAP:g}% of the decision). Pick something else.")
    new_target = min(current[target_id] + STEP, CAP).quantize(CENT, rounding=ROUND_HALF_UP)
    others = {i: w for i, w in current.items() if i != target_id}
    others_total = sum(others.values(), Decimal(0))
    if not others or others_total <= 0:
        raise ReviewError("There is no other criterion to take the points from, so the priorities cannot change.")
    remaining = TOTAL - new_target
    new = {i: (w * remaining / others_total).quantize(CENT, rounding=ROUND_HALF_UP) for i, w in others.items()}
    largest = min(others, key=lambda i: (-others[i], i))        # the largest weight; the lowest id on a tie
    new[largest] += remaining - sum(new.values(), Decimal(0))     # the rounding remainder
    new[target_id] = new_target
    return Reweighting(target_id, tuple(WeightLine(c.criterion_id, c.name, float(current[c.criterion_id]), float(new[c.criterion_id]))
                                        for c in criteria))


def percent(value: float) -> str:
    """A weight as people write it: '20', '17.14', '32.5' (never '20.0' or '17.140')."""
    text = f"{Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def describe_reweighting(plan: Reweighting) -> str:
    """One sentence for the screen: which criterion counts for more now, and by how much."""
    t = plan.target
    return f"{t.name} now counts for {percent(t.now)}% of the decision (it was {percent(t.was)}%)."
