"""What a buyer leans towards (feature F21b): plain-Python rules that turn the AI's reading of a typed wish into weights.

The AI only reads the wish: for each criterion how strongly the person leans on it (-2 to +2), which end of the price range they want
("low" or "high"), and whether price (or another area) is the ONLY thing they care about. Everything else is worked out here:

* "only": that one criterion dominates: it counts for 60% (the cap every criterion already has) and the others share the other 40% in
  the proportions they had, so the rest of the evidence can still decide a close call.
* otherwise each criterion's current weight is multiplied by a factor for its level and the weights are scaled to total 100
  (2 decimals, the rounding remainder goes on the largest weight). No criterion ends above 60%.

Pure functions: no I/O, no Streamlit, no LLM, no clock.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from tools import insights
from tools.reweight import CAP, CENT, TOTAL, WeightLine, Reweighting, WeightLike, _exact

LEVEL_MIN, LEVEL_MAX = -2, 2
FACTORS = {-2: Decimal("0.1"), -1: Decimal("0.5"), 0: Decimal(1), 1: Decimal("1.8"), 2: Decimal(3)}

# A wish for the dearest option. Plain Python checks the words whatever the AI said, so "high" is never guessed.
WANTS_DEAREST = re.compile(r"\b(most expensive|costliest|priciest|dearest|highest[- ]priced?|highest (?:price|cost)|most costly|expensive one)\b", re.I)
WANTS_EXPENSIVE_LOOSE = re.compile(r"\b(want|wants|like|prefer|choose|pick|take|need|go for|give me|looking for|should be|be)\b[^.;]{0,40}\bexpensive\b", re.I)
NOT_A_WISH = re.compile(r"\b(too|not|isn'?t|aren'?t|less|overly|so) (?:so |very |that )?expensive\b", re.I)
WANTS_CHEAPEST = re.compile(r"\b(cheapest|lowest[- ]priced?|lowest (?:price|cost)|least expensive|most affordable|as low as possible)\b", re.I)


@dataclass(frozen=True)
class Leaning:
    price: str | None = None                      # insights.PRICE_LOW, insights.PRICE_HIGH or None (no wish about the price end)
    only_id: int | None = None                    # the one criterion the person cares about and nothing else
    emphasis: tuple[tuple[int, int], ...] = ()    # (criterion_id, level -2..+2), only for criteria that are part of the run

    @property
    def changes_anything(self) -> bool:
        return self.price is not None or self.only_id is not None or any(level != 0 for _, level in self.emphasis)


def clean(raw: object, criterion_ids: Sequence[int], price_id: int | None, text: str) -> Leaning:
    """The AI's `leaning` object, made safe. Anything unreadable is dropped. A price direction of "high" needs the person's own
    words to say so; "low" is accepted from the AI or from the words. Levels are clamped to -2..2 and kept only for real criteria."""
    data = raw if isinstance(raw, Mapping) else {}
    ids = set(criterion_ids)
    emphasis: dict[int, int] = {}
    levels = data.get("emphasis")
    if isinstance(levels, Mapping):
        for key, level in levels.items():
            try:
                cid = int(key)
            except (TypeError, ValueError):
                continue
            if cid in ids and isinstance(level, (int, float)) and not isinstance(level, bool):
                emphasis[cid] = max(LEVEL_MIN, min(LEVEL_MAX, round(level)))
    dearest = bool(WANTS_DEAREST.search(text) or (WANTS_EXPENSIVE_LOOSE.search(text) and not NOT_A_WISH.search(text)))
    if (not dearest and data.get("price") == insights.PRICE_HIGH and re.search(r"\b(expensive|costly|pricey|premium|pricier)\b", text, re.I)
            and not NOT_A_WISH.search(text)):
        dearest = True                              # the AI read a wish for the dearest and the words mention it (and are not a complaint)
    cheapest = bool(WANTS_CHEAPEST.search(text))
    price = data.get("price")
    if dearest and not cheapest:
        direction = insights.PRICE_HIGH
    elif cheapest and not dearest:
        direction = insights.PRICE_LOW
    elif price == insights.PRICE_LOW and not dearest:
        direction = insights.PRICE_LOW
    else:
        direction = None
    only = data.get("only")
    try:
        only_id = int(only) if not isinstance(only, bool) and only not in (None, "") else None
    except (TypeError, ValueError):
        only_id = None
    if only is True and price_id is not None and direction is not None:
        only_id = price_id                         # "only": true with a price wish means price alone
    if only_id not in ids:
        only_id = None
    if direction is not None and price_id is not None and price_id in ids:
        emphasis.setdefault(price_id, 2)           # a stated wish about price means price counts for more
    return Leaning(direction, only_id, tuple(sorted(emphasis.items())))


def _fill(others: Mapping[int, Decimal], rest: Decimal | None, fixed: Mapping[int, Decimal]) -> dict[int, Decimal]:
    """`fixed` weights are kept; the others share what is left of 100 in the proportions they had (equally if they had none).
    Returns every weight, rounded to 2 decimals with the rounding remainder on the largest."""
    others = {i: v for i, v in others.items() if i not in fixed}
    left = TOTAL - sum(fixed.values(), Decimal(0))
    base = sum(others.values(), Decimal(0))
    out = dict(fixed)
    for i, v in others.items():
        out[i] = (v * left / base if base > 0 else left / len(others)).quantize(CENT, rounding=ROUND_HALF_UP)
    if others:
        largest = min(others, key=lambda i: (-out[i], i))
        out[largest] += TOTAL - sum(out.values(), Decimal(0))
    return out


def plan(criteria: Sequence[WeightLike], leaning: Leaning, target_hint: int | None = None) -> Reweighting:
    """The new weights for a leaning. Raises ValueError when it changes nothing."""
    if not leaning.changes_anything:
        raise ValueError("That leaning does not change anything.")
    current = {c.criterion_id: _exact(c.weight) for c in criteria}
    if leaning.only_id is not None:
        target = leaning.only_id
        raw = {i: (w if i != target else Decimal(0)) for i, w in current.items()}
        rest = sum(raw.values(), Decimal(0))
        now = _fill(raw, rest, {target: CAP})
    else:
        levels = dict(leaning.emphasis)
        raw = {i: w * FACTORS[levels.get(i, 0)] for i, w in current.items()}
        total = sum(raw.values(), Decimal(0))
        if total <= 0:
            raise ValueError("The weights cannot be worked out.")
        now = {i: v * TOTAL / total for i, v in raw.items()}
        over = {i: CAP for i, v in now.items() if v > CAP}
        if over:                                    # nothing counts for more than the cap: the surplus goes to the others
            now = _fill({i: v for i, v in now.items() if i not in over}, None, over)
        now = {i: v.quantize(CENT, rounding=ROUND_HALF_UP) for i, v in now.items()}
        largest = min(now, key=lambda i: (-now[i], i))
        now[largest] += TOTAL - sum(now.values(), Decimal(0))
        target = max(levels, key=lambda i: (levels[i], -i)) if levels else (target_hint if target_hint in current else next(iter(current)))
    names = {c.criterion_id: c.name for c in criteria}
    lines = tuple(WeightLine(i, names[i], float(current[i]), float(now[i])) for i in current)
    return Reweighting(target, lines)


def describe(leaning: Leaning, names: Mapping[int, str]) -> str:
    """The sentence the person confirms: what Python understood, in plain words (never the AI's own sentence)."""
    wish = {insights.PRICE_HIGH: "the most expensive option", insights.PRICE_LOW: "the cheapest option"}.get(leaning.price or "")
    if leaning.only_id is not None:
        what = names.get(leaning.only_id, "that")
        text = f"you only care about {what.lower()}" + (f", and you want {wish}" if wish else "")
    else:
        more = [names[i] for i, level in leaning.emphasis if level > 0 and i in names]
        less = [names[i] for i, level in leaning.emphasis if level < 0 and i in names]
        parts = []
        if wish:
            parts.append(f"you want {wish}")
        if more:
            parts.append(f"{_join(more)} should count for more")
        if less:
            parts.append(f"{_join(less)} should count for less")
        text = " and ".join(parts) if parts else "nothing changes"
    return f"I understood: {text}."


def _join(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
