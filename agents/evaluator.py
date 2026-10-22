"""Evaluation agent (feature F08, spec 7.1): one supplier proposal in, one validated scorecard out.

The LLM judges; Python decides. This module builds the blind prompt, makes the call, checks the reply with the
validator, retries once if the reply is not usable JSON, and puts the real supplier name back. It never ranks
anything and never compares suppliers (that is pure Python, later).

Rules kept here:
* Blind evaluation: the supplier's real name is replaced by "Supplier A" before anything is sent, and is
  re-attached afterwards in Python.
* The proposal is untrusted text: it cannot close the <proposal> block early.
* The API key is only handed to ``call_llm``; it is never put in a prompt, a warning or a message.
* Budget and quantity come from the run (``PurchaseTerms``), never from constants.

Try it without any key:   python -m agents.evaluator data/proposals/apex_systems.pdf --show-prompt
Try it with a key:        set the provider's key in an environment variable, then
                          python -m agents.evaluator data/proposals/apex_systems.pdf --provider OpenAI --model <model>
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path, PurePath
from typing import Protocol

from llm import client
from tools import validator
from tools.industry import PurchaseTerms

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "evaluation_system.md"
ALIAS = "Supplier A"
RETRY_INSTRUCTION = "Your previous reply was not valid JSON matching the required shape. Reply again with the JSON object only."
MAX_ECHO_CHARS = 8000          # how much of a bad reply is sent back with the retry request
PROMPT_TAGS = ("buyer_requirements", "criteria", "reviewer_feedback", "proposal")

# The Meridian laptop RFP, spec 1.2. Quantity and budget are NOT here: they come from the run (DECISIONS 69).
# The rest still follows this RFP (DECISIONS 70).
FIXED_REQUIREMENTS: tuple[tuple[str, str], ...] = (
    ("Specification", "14-inch, Intel Core i5 / AMD Ryzen 5 class or better, 16 GB RAM, 512 GB SSD, 8+ hours real battery, Windows 11 Pro"),
    ("Delivery", "All units within 8 weeks of order"),
    ("Setup", "Company software image pre-installed, asset tags, device-management enrolment, directory sign-in"),
    ("Security", "Disk encryption, TPM 2.0, certified data wiping for returned devices"),
    ("Certification", "BIS registration and an energy rating"),
    ("Warranty", "At least 3 years, with defined repair or replacement times"),
)


class EvaluatorError(Exception):
    """A problem with the inputs or the prompt file. ``user_message`` is friendly and safe to show."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class PromptCriterion(Protocol):
    """What the prompt and the validator need from a criterion. ``db.repository.Criterion`` fits."""

    @property
    def criterion_id(self) -> int: ...

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def max_score(self) -> float: ...


@dataclass(frozen=True)
class Prompt:
    messages: tuple[dict[str, str], ...]   # system, then user
    name_replacements: int                 # how many times the supplier's name was hidden


@dataclass(frozen=True)
class EvaluationOutcome:
    """The result for one supplier. ``failed`` means the reply was unusable twice: every score is 0."""

    supplier_name: str                      # the real name, re-attached in Python
    scorecard: validator.EvaluationScorecard
    warnings: tuple[str, ...]
    failed: bool
    attempts: int                           # 1, or 2 when the JSON retry was needed
    tokens: int | None
    cost_usd: float | None
    name_replacements: int
    raw_reply: str = ""                     # the model's last reply exactly as received (stored by the orchestrator, F09)


# ---------------------------------------------------------------- blind naming

def _name_pattern(supplier_name: str) -> re.Pattern[str]:
    """Matches the name in any case, with or without spaces ("Nexa Works", "NexaWorks", a line break in between)."""
    pieces = [piece for piece in re.split(r"\s+|(?<=[a-z0-9])(?=[A-Z])", supplier_name.strip()) if piece]
    body = r"\s*".join(re.escape(piece) for piece in pieces)
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.IGNORECASE)


def mask_supplier_name(text: str, supplier_name: str) -> tuple[str, int]:
    """Replace the supplier's name with "Supplier A". Returns the new text and how many places were changed."""
    if not supplier_name.strip():
        raise EvaluatorError("Enter the supplier's name before evaluating.")
    return _name_pattern(supplier_name).subn(ALIAS, text)


_ALIAS_PATTERN = re.compile(r"(?<![A-Za-z0-9])Supplier\s+A(?![A-Za-z0-9])", re.IGNORECASE)


def restore_supplier_name(text: str, supplier_name: str) -> str:
    """Put the real name back where the reply says "Supplier A"."""
    return _ALIAS_PATTERN.sub(lambda _match: supplier_name.strip(), text)


def _defuse_tags(text: str) -> str:
    """Untrusted text may not open or close the prompt's own blocks: <proposal> and </proposal> become [proposal] and [/proposal]."""
    names = "|".join(PROMPT_TAGS)
    return re.sub(rf"<\s*(/?)\s*({names})\b[^>]*>", r"[\1\2]", text, flags=re.IGNORECASE)


# ---------------------------------------------------------------- building the prompt

def format_rupees(value: float | int) -> str:
    """Whole rupees with Indian digit grouping: 6000000 -> 'Rs 60,00,000'."""
    whole = str(int(Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP)))
    if len(whole) <= 3:
        return f"Rs {whole}"
    head, tail = whole[:-3], whole[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return "Rs " + ",".join([*groups, tail])


def build_requirements_text(terms: PurchaseTerms) -> str:
    """The buyer's requirements table (spec 1.2) as plain text, with this run's quantity and budget."""
    units = f"{terms.quantity} laptop{'' if terms.quantity == 1 else 's'}, one model"
    lines = [f"Quantity: {units}"]
    lines += [f"{name}: {value}" for name, value in FIXED_REQUIREMENTS]
    lines.append(f"Budget: About {format_rupees(terms.budget_rs)} including GST and services")
    return "\n".join(lines)


def _number(value: float) -> str:
    return f"{value:g}"


def build_criteria_text(criteria: Sequence[PromptCriterion]) -> str:
    """One line per active criterion: criterion_id | name | max_score | description."""
    return "\n".join(
        f"{c.criterion_id} | {c.name} | {_number(c.max_score)} | {' '.join(c.description.split())}" for c in criteria)


def read_prompt(path: Path = PROMPT_PATH, sleep: Callable[[float], None] = time.sleep) -> str:
    """The evaluation system prompt. A file briefly locked by OneDrive is tried once more after a pause."""
    for attempt in (1, 2):
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            raise EvaluatorError(f"The evaluation prompt file is missing ({path.name}). Restore it from the project folder.") from None
        except PermissionError:
            if attempt == 2:
                raise EvaluatorError(f"The evaluation prompt file ({path.name}) is locked. Wait a moment and try again.") from None
            sleep(3)
    raise AssertionError("unreachable")


def build_prompt(
    supplier_name: str,
    proposal_text: str,
    criteria: Sequence[PromptCriterion],
    terms: PurchaseTerms,
    reviewer_feedback: str | None = None,
    *,
    system_prompt: str | None = None,
) -> Prompt:
    """The two messages for one blind evaluation (spec 7.1). The real supplier name is not in either of them."""
    if not proposal_text.strip():
        raise EvaluatorError("This proposal has no readable text, so it cannot be evaluated.")
    if not criteria:
        raise EvaluatorError("There are no active criteria to evaluate against.")
    blind_proposal, replaced = mask_supplier_name(_defuse_tags(proposal_text), supplier_name)
    feedback = "None"
    if reviewer_feedback and reviewer_feedback.strip():
        feedback = mask_supplier_name(_defuse_tags(reviewer_feedback.strip()), supplier_name)[0]
    user = (
        f"<buyer_requirements>\n{build_requirements_text(terms)}\n</buyer_requirements>\n\n"
        f"<criteria>\n{build_criteria_text(criteria)}\n</criteria>\n\n"
        f"<reviewer_feedback>\n{feedback}\n</reviewer_feedback>\n\n"
        f"<proposal>\n{blind_proposal}\n</proposal>"
    )
    system = system_prompt if system_prompt is not None else read_prompt()
    return Prompt(({"role": "system", "content": system}, {"role": "user", "content": user}), replaced)


# ---------------------------------------------------------------- the evaluation

def _restore_scorecard(card: validator.EvaluationScorecard, supplier_name: str) -> validator.EvaluationScorecard:
    def back(text: str) -> str:
        return restore_supplier_name(text, supplier_name)

    facts = card.extracted_facts.model_copy(update={
        name: back(value) for name in validator.ExtractedFacts.model_fields
        if isinstance(value := getattr(card.extracted_facts, name), str)})
    return card.model_copy(update={
        "criteria": tuple(c.model_copy(update={"justification": back(c.justification), "evidence": back(c.evidence)})
                          for c in card.criteria),
        "extracted_facts": facts,
        "risks": tuple(back(risk) for risk in card.risks),
        "overall_summary": back(card.overall_summary),
    })


def _total(values: Sequence[float | int | None]) -> float | int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _unique(items: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def evaluate_supplier(
    supplier_name: str,
    proposal_text: str,
    criteria: Sequence[PromptCriterion],
    terms: PurchaseTerms,
    settings: client.LLMSettings,
    *,
    reviewer_feedback: str | None = None,
    backend: client.Backend | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> EvaluationOutcome:
    """Evaluate ONE supplier. ``criteria`` are the active criteria read from the database for this run.

    A reply that is not usable JSON is retried once with a fixed instruction; if it fails again the supplier gets
    all scores 0 and an "Evaluation failed" warning, and the caller carries on with the next supplier.
    Provider problems (bad key, no network, timeout after the one retry) are raised as ``client.LLMError``:
    the orchestrator decides what a failed call means for the run.
    """
    prompt = build_prompt(supplier_name, proposal_text, criteria, terms, reviewer_feedback)
    temperature = client.temperature_for("scoring")
    messages: list[dict[str, str]] = list(prompt.messages)
    notes: list[str] = []
    tokens: list[int | None] = []
    costs: list[float | None] = []
    checked: validator.ValidationResult | None = None
    attempts = 0
    for attempts in (1, 2):
        result = client.call_llm(settings, messages, temperature, json_mode=True, backend=backend, sleep=sleep)
        tokens.append(result.tokens)
        costs.append(result.cost_usd)
        notes.extend(result.warnings)
        checked = validator.validate_evaluation(result.text, criteria)
        if checked.usable:
            break
        messages = [*prompt.messages,
                    {"role": "assistant", "content": result.text[:MAX_ECHO_CHARS]},
                    {"role": "user", "content": RETRY_INSTRUCTION}]
    assert checked is not None
    failed = not checked.usable
    if failed:
        checked = validator.failed_evaluation(criteria)
    assert checked.scorecard is not None
    return EvaluationOutcome(
        supplier_name=supplier_name.strip(),
        scorecard=checked.scorecard if failed else _restore_scorecard(checked.scorecard, supplier_name),
        warnings=_unique([*notes, *checked.warnings]),
        failed=failed,
        attempts=attempts,
        tokens=_total(tokens),   # type: ignore[arg-type]
        cost_usd=_total(costs),  # type: ignore[arg-type]
        name_replacements=prompt.name_replacements,
        raw_reply=result.text,
    )


# ---------------------------------------------------------------- command line (no UI yet: F09 adds the New evaluation tab)

def _show(value: object) -> str:
    return "not stated" if value is None else str(value)


def render_outcome(outcome: EvaluationOutcome, criteria: Sequence[PromptCriterion]) -> str:
    """A plain-text view of the validated scorecard."""
    card = outcome.scorecard
    names = {c.criterion_id: c for c in criteria}
    lines = [f"Supplier: {outcome.supplier_name}   (name hidden {outcome.name_replacements} times in what was sent)",
             f"Attempts: {outcome.attempts}   Tokens: {_show(outcome.tokens)}   Cost (USD): {_show(outcome.cost_usd)}",
             f"Status: {'EVALUATION FAILED, all scores 0' if outcome.failed else 'evaluated'}", "", "Criteria"]
    for item in card.criteria:
        criterion = names[item.criterion_id]
        lines += [f"  [{item.criterion_id}] {criterion.name}: {_number(item.score)} / {_number(criterion.max_score)}",
                  f"      Why: {item.justification}", f"      Evidence: {item.evidence}"]
    lines += ["", "Extracted facts"]
    lines += [f"  {name}: {_show(getattr(card.extracted_facts, name))}" for name in validator.ExtractedFacts.model_fields]
    lines += ["", "Risks"] + ([f"  - {risk}" for risk in card.risks] or ["  none reported"])
    lines += ["", f"Summary: {card.overall_summary or 'none'}", "", "Warnings"]
    lines += [f"  - {warning}" for warning in outcome.warnings] or ["  none"]
    return "\n".join(lines)


def title_from_filename(path: Path | PurePath) -> str:
    """'apex_systems.pdf' -> 'Apex Systems' (the name suggested for a file)."""
    return " ".join(word.capitalize() for word in re.split(r"[_\-\s]+", path.stem) if word)


def _main(
    argv: Sequence[str] | None = None,
    *,
    backend: client.Backend | None = None,
    environ: Mapping[str, str] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m agents.evaluator", description="Evaluate one proposal PDF (feature F08).")
    parser.add_argument("pdf", type=Path, help="the proposal PDF")
    parser.add_argument("--supplier", help="supplier name (default: taken from the file name)")
    parser.add_argument("--provider", default="OpenAI", choices=client.PROVIDER_NAMES)
    parser.add_argument("--model", help="model name, as the sidebar lists it")
    parser.add_argument("--budget", type=float, default=6000000, help="total budget in rupees (default 6000000)")
    parser.add_argument("--quantity", type=int, default=100, help="number of laptops (default 100)")
    parser.add_argument("--show-prompt", action="store_true", help="print the exact messages that would be sent, and stop (no key needed)")
    args = parser.parse_args(argv)

    from db import repository, seed
    from tools import pdf_tool

    try:
        terms = PurchaseTerms(budget_rs=args.budget, quantity=args.quantity)
        pdf = pdf_tool.extract_pdf_file(args.pdf)
        if not pdf.has_text:
            raise EvaluatorError("This PDF has no readable text, so it cannot be evaluated.")
        seed.ensure_database()
        with repository.connection() as conn:
            criteria = repository.list_criteria(conn, active_only=True)
        supplier = args.supplier or title_from_filename(args.pdf)
        if args.show_prompt:
            prompt = build_prompt(supplier, pdf.text, criteria, terms)
            out(f"The real name '{supplier}' was hidden {prompt.name_replacements} times. Messages that would be sent:\n")
            for message in prompt.messages:
                out(f"--- {message['role']} ---\n{message['content']}\n")
            return 0
        if not args.model:
            raise EvaluatorError("Pass --model with the model name (the same names the sidebar lists).")
        provider = client.provider_named(args.provider)
        api_key = client.default_key_for(args.provider, environ=environ)
        if not api_key:
            names = " or ".join(provider.key_names)
            raise EvaluatorError(f"No API key found. Set the environment variable {names} in this terminal, then run again.")
        settings = client.LLMSettings(args.provider, args.model, api_key)
        outcome = evaluate_supplier(supplier, pdf.text, criteria, terms, settings, backend=backend)
    except (EvaluatorError, client.LLMError, pdf_tool.PdfError, ValueError) as exc:
        out(f"Error: {getattr(exc, 'user_message', None) or exc}")
        return 1
    except OSError as exc:
        out(f"Error: could not read a file ({exc.__class__.__name__}).")
        return 1
    out(render_outcome(outcome, criteria))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(errors="replace")  # a Windows console may not show every character
    except (AttributeError, ValueError):
        pass
    raise SystemExit(_main())
