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
              "grand_total_rs": 5900000, "unit_price_rs": 59000, "battery_hours": 11.5, "battery_measured": True}
BAD_FACTS = {"warranty_years": 1, "warranty_type": "carry-in", "bis_named": None, "price_table_present": True, "delivery_weeks": 6}


def reply(scores, facts=None, *, why: str = "", evidence: str = "") -> str:
    return json.dumps({"supplier_name": "Supplier A", "criteria": [
        {"criterion_id": i, "score": s, "justification": why or f"why {i}", "evidence": evidence or f"quote {i} (Page {i})"}
        for i, s in enumerate(scores, start=1)], "extracted_facts": GOOD_FACTS if facts is None else facts, "risks": [],
        "overall_summary": "ok"})


def response(text: str, tokens: int = 1000):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=SimpleNamespace(total_tokens=tokens))


class FakeBackend:
    """Returns the prepared replies in order (the last one repeats). An exception in the list is raised instead."""

    def __init__(self, *outcomes, cost: float = 0.01) -> None:
        self.outcomes, self.calls, self.cost_value = list(outcomes), [], cost

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes[min(len(self.calls), len(self.outcomes)) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome if not isinstance(outcome, str) else response(outcome)

    def supports_json_mode(self, settings) -> bool:
        return True

    def cost(self, response) -> float:
        return self.cost_value

    def sent_text(self) -> str:
        return "\n".join(m["content"] for call in self.calls for m in call["messages"])


def pdf_for(name: str) -> pdf_tool.ExtractedPdf:
    text = f"Offer from {name}. {SENTINEL} We offer laptops. " + "Long proposal text. " * 40
    return pdf_tool.ExtractedPdf(sha256=hashlib.sha256(name.encode()).hexdigest(), pages=(text,), backend="pypdf")


def evaluated_run(rows=FIVE, facts: dict[str, dict[str, Any]] | None = None) -> orchestrator.RunResult:
    """A real run of the pipeline over `rows`, with a fake model that gives each supplier its prepared marks."""
    facts = facts or {"BrightPath Tech": BAD_FACTS}
    proposals = [orchestrator.Proposal(f"{n.lower().replace(' ', '_')}.pdf", n, when, rating, pdf_for(n)) for n, when, rating, _s in rows]
    backend = FakeBackend(*[reply(scores, facts.get(n, GOOD_FACTS)) for n, _w, _r, scores in rows])
    result = orchestrator.run_evaluation(proposals, TERMS, SETTINGS, "Sri", backend=backend, sleep=lambda _s: None, now=lambda: START)
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
