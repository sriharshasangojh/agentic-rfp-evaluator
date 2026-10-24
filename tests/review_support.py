"""Shared set-up for the review tests (feature F13): a real batch pushed through the orchestrator with a fake model, so every
review test starts from exactly what the app saves. No API key or network is used."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from agents import orchestrator
from db import repository
from llm import client
from tools import industry, pdf_tool

KEY = "fake-key-review-123"
SENTINEL = "ZXQ-REVIEW-PROPOSAL-SENTINEL-4471"
SETTINGS = client.LLMSettings("OpenAI", "test-model", KEY)
TERMS = industry.PurchaseTerms(6000000, 100)
START = datetime(2026, 10, 23, 9, 30, tzinfo=timezone.utc)

# name, submission date, experience rating, marks for the five criteria (the spec's ranking fixture, 16.1)
FIVE = [("Apex Systems", date(2026, 10, 5), 4, [9, 7, 5, 9, 8]), ("BrightPath Tech", date(2026, 10, 7), 2, [5, 4, 8, 3, 3]),
        ("NexaWorks", date(2026, 10, 6), 4, [7, 9, 7, 7, 9]), ("Horizon Datacom", date(2026, 10, 6), 3, [7, 9, 7, 7, 9]),
        ("Orbit Digital", date(2026, 10, 8), 5, [7, 5, 7, 6, 9])]
NAMES = [n for n, *_ in FIVE]
APEX, BRIGHT, NEXA, HORIZON, ORBIT = NAMES
GOOD_FACTS = {"warranty_years": 3, "warranty_type": "on-site", "bis_named": True, "price_table_present": True, "delivery_weeks": 6,
              "unit_price_rs": 59000, "battery_hours": 11.5, "battery_measured": True}   # no grand total: the price mark stays as the AI gave it
BAD_FACTS = {"warranty_years": 1, "warranty_type": "carry-in", "bis_named": None, "price_table_present": True, "delivery_weeks": 6}


QUOTE = "We offer laptops. (Page 1)"          # a sentence that is really in `pdf_for`, so the evidence check finds it


def reply(scores, facts=None, *, why: str = "", evidence: str = "") -> str:
    return json.dumps({"supplier_name": "Supplier A", "criteria": [
        {"criterion_id": i, "score": s, "justification": why or f"why {i}", "evidence": evidence or QUOTE}
        for i, s in enumerate(scores, start=1)], "extracted_facts": GOOD_FACTS if facts is None else facts, "risks": [],
        "overall_summary": "ok"})


def response(text: str, tokens: int = 1000):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=SimpleNamespace(total_tokens=tokens))


SUMMARY_PROMPT_START = "You write short, plain-language points"
REASON = "It meets every must-have and leads where it counts."


def is_summary_call(kwargs) -> bool:
    """True for the wording call of feature F16 (the system prompt is the summarizer's)."""
    messages = kwargs.get("messages") or [{}]
    return str(messages[0].get("content", "")).startswith(SUMMARY_PROMPT_START)


def summary_response(kwargs, *, reason: str = REASON):
    """A well-behaved summarizer: every point comes back in the template's own words, plus a reason. It reports no tokens or cost, so
    the many older tests that count tokens, cost and calls keep their numbers; the wording has its own tests."""
    body = json.loads(kwargs["messages"][1]["content"])
    text = json.dumps({"pros": [i["point"] for i in body["pros"]], "cons": [i["point"] for i in body["cons"]], "reason": reason})
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=None, summary=True)


class FakeBackend:
    """Returns the prepared replies in order (the last one repeats). An exception in the list is raised instead.
    The wording call (F16) is answered by `summary_response` and kept in `summary_calls`, not in `calls`."""

    def __init__(self, *outcomes, cost: float = 0.01) -> None:
        self.outcomes, self.calls, self.cost_value, self.summary_calls = list(outcomes), [], cost, []

    def completion(self, **kwargs):
        if is_summary_call(kwargs):
            self.summary_calls.append(kwargs)
            return self.summary_reply(kwargs)
        self.calls.append(kwargs)
        outcome = self.outcomes[min(len(self.calls), len(self.outcomes)) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome if not isinstance(outcome, str) else response(outcome)

    def summary_reply(self, kwargs):
        """The answer to a wording call; a test overrides this to misbehave."""
        return summary_response(kwargs)

    def supports_json_mode(self, settings) -> bool:
        return True

    def cost(self, response) -> float | None:
        return None if getattr(response, "summary", False) else self.cost_value

    def sent_text(self) -> str:
        return "\n".join(m["content"] for call in self.calls for m in call["messages"])


def pdf_for(name: str) -> pdf_tool.ExtractedPdf:
    text = f"Offer from {name}. {SENTINEL} We offer laptops. " + "Long proposal text. " * 40
    return pdf_tool.ExtractedPdf(sha256=hashlib.sha256(name.encode()).hexdigest(), pages=(text,), backend="pypdf")


def evaluated_run(rows=FIVE, facts: dict[str, dict[str, Any]] | None = None,
                  priorities: orchestrator.PriorityRequest | None = None) -> orchestrator.RunResult:
    """A real run of the pipeline over `rows`, with a fake model that gives each supplier its prepared marks."""
    facts = facts or {"BrightPath Tech": BAD_FACTS}
    proposals = [orchestrator.Proposal(f"{n.lower().replace(' ', '_')}.pdf", n, when, rating, pdf_for(n)) for n, when, rating, _s in rows]
    backend = FakeBackend(*[reply(scores, facts.get(n, GOOD_FACTS)) for n, _w, _r, scores in rows])
    result = orchestrator.run_evaluation(proposals, TERMS, SETTINGS, "Sri", priorities, backend=backend, sleep=lambda _s: None, now=lambda: START)
    assert result.status == "Evaluated", result.message
    return result


class Clock:
    """A clock for review actions that moves one minute at every reading, so the order of events is always clear."""

    def __init__(self) -> None:
        self.moment = START + timedelta(hours=1)

    def __call__(self) -> datetime:
        self.moment += timedelta(minutes=1)
        return self.moment


def rows(sql: str, *args):
    with repository.connection() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def saved(run_id: str):
    with repository.connection() as conn:
        return {s.supplier_name: s for s in repository.list_supplier_results(conn, run_id)}


def marks(stored, name: str) -> list[float]:
    return [c["score"] for c in stored[name].result["criteria"]]


def whole_database() -> str:
    with repository.connection() as conn:
        return "\n".join(conn.iterdump())


# ---------------------------------------------------------------- proposals with real, searchable text (feature F14, Ask the RFP)

EXCERPT_SENTINEL = "ZXQ-ASK-EXCERPT-SENTINEL-8812"
ASK_TEXTS = {
    APEX: (f"Apex offers a three year on-site warranty with next-day repair. {EXCERPT_SENTINEL}",
           "Battery life measured at eleven hours of video playback."),
    BRIGHT: ("BrightPath offers a one year carry-in warranty.", "Delivery takes eight weeks."),
    NEXA: ("NexaWorks keeps its price for orders placed next year.", "Warranty is on-site with repair on the next business day."),
    HORIZON: ("Horizon Datacom ships within three weeks.", "The support desk is open on weekdays."),
    ORBIT: ("Orbit Digital has supplied many business laptops.", "Orbit gives a three year warranty."),
}


def extracted_pdf(pages: tuple[str, ...], seed_text: str = "x") -> pdf_tool.ExtractedPdf:
    return pdf_tool.ExtractedPdf(sha256=hashlib.sha256((seed_text + "".join(pages)).encode()).hexdigest(), pages=pages, backend="pypdf")


def ask_proposals() -> list[orchestrator.Proposal]:
    """The five suppliers of `evaluated_run`, each with two short pages of text that a question can find."""
    return [orchestrator.Proposal(f"{n.lower().replace(' ', '_')}.pdf", n, when, rating, extracted_pdf(ASK_TEXTS[n], n)) for n, when, rating, _s in FIVE]
