"""Audit tab tests (feature F12: run details and the JSON download).

Most tests call the tab's own functions on saved runs that are written straight into a temporary database, so every
combination (no budget, no tokens, odd data, a warning every supplier shares) can be set up exactly. One group pushes a real
batch through the orchestrator and checks the file it produces, so the two sides cannot drift apart. The page is drawn into a
recording stand-in for Streamlit, to check what is drawn and in what order. The last group drives the real app headlessly with
Streamlit's AppTest (it cannot click a download button, so the click is tested through `note_export`). No API key or network
is used. Needs Streamlit to import; the AppTest group is skipped when Streamlit's test runner is missing.
"""
from __future__ import annotations

import itertools
import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("streamlit")

from db import audit, repository, seed  # noqa: E402
from llm import client  # noqa: E402
from tools import industry, insights, pdf_tool  # noqa: E402
from ui import tab_audit as A, tab_new_evaluation, tab_overview  # noqa: E402

try:
    from streamlit.testing.v1 import AppTest
except ImportError:   # a stand-in for Streamlit, or a very old one
    AppTest = None

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
needs_app_test = pytest.mark.skipif(AppTest is None, reason="Streamlit's test runner is not available")

# Spec section 15, typed out here on purpose: the file must have exactly these keys in exactly this order.
SPEC_TOP_LEVEL = ["rfp_run_id", "created_at", "status", "provider", "model", "temperature", "template", "criteria_snapshot",
                  "suppliers", "recommendation", "tie_break_explanations", "feedback_rounds", "negotiation_points", "totals"]
SPEC_SNAPSHOT = ["criterion_id", "name", "weight", "max_score"]
SPEC_SUPPLIER = ["rank", "supplier_name", "submission_date", "experience_rating", "absolute_score", "ppi", "industry_score",
                 "eligible", "gates", "criteria", "extracted_facts", "industry_breakdown", "pros", "cons", "risks", "warnings",
                 "document_sha256"]
SPEC_CRITERION = ["criterion_id", "score", "max_score", "benchmark", "gap", "relative_pct", "weight", "strength", "justification",
                  "evidence", "evidence_found"]
SPEC_RECOMMENDATION = ["recommended", "runner_up", "reason", "industry_warning"]
SPEC_TOTALS = ["tokens", "cost_usd", "duration_seconds"]

CRITERIA = [(1, "Technical Capability", 30.0), (2, "Implementation Plan", 20.0), (3, "Commercial Value", 20.0),
            (4, "Security & Compliance", 20.0), (5, "Support & Experience", 10.0)]
SEEDED_GATES = [("warranty", "Warranty", "3 years (at least 3 required)"), ("certification", "Certification", "BIS registration named"),
                ("price_table", "Price table", "Price table present"), ("delivery", "Delivery", "6 weeks (at most 8 allowed)")]
FACTS = {"grand_total_rs": 5900000, "unit_price_rs": 59000, "warranty_years": 3, "warranty_type": "on-site", "delivery_weeks": 6,
         "battery_hours": 11.5, "battery_measured": True, "ram_gb": 16, "ssd_gb": 512}
BEST = (9, 9, 8, 9, 9)
KEY = "test-key-abc123"
SENTINEL = "ZXQ-PROPOSAL-BODY-SENTINEL-7781"


@pytest.fixture(autouse=True)
def seeded_database():
    seed.ensure_database()


# ---------------------------------------------------------------- saved runs written by hand

def criterion(i: int, name: str, weight: float, score: float, benchmark: float) -> dict[str, Any]:
    return {"criterion_id": i, "name": name, "score": score, "max_score": 10.0, "benchmark": benchmark, "gap": score - benchmark,
            "relative_pct": round(score / benchmark * 100, 2), "weight": weight, "strength": insights.strength_label(score, 10.0),
            "justification": f"why {i}", "evidence": f"quote {i} (Page {i})", "evidence_found": None, "filled_by_validator": False}


def result_for(scores=(9, 7, 5, 9, 8), *, failed: dict[str, str] | None = None, warnings=(), facts=None, attributes=None,
               tie_break=None, **changes) -> dict[str, Any]:
    """A saved result in the shape the orchestrator writes, including keys the file must NOT copy."""
    failed = failed or {}
    result = {
        "rank": 1, "supplier_name": "ignored", "file_name": "ignored.pdf", "submission_date": "2026-10-05", "experience_rating": 3,
        "criteria": [criterion(i, n, w, s, b) for (i, n, w), s, b in zip(CRITERIA, scores, BEST)],
        "gates": {gid: "fail" if gid in failed else "pass" for gid, _n, _r in SEEDED_GATES},
        "gate_details": [{"gate_id": gid, "name": name, "passed": gid not in failed, "reason": failed.get(gid, ok)}
                         for gid, name, ok in SEEDED_GATES],
        "extracted_facts": dict(FACTS if facts is None else facts),
        "industry_breakdown": {"score": 75.0, "attributes": attributes if attributes is not None else [
            {"attribute_key": "warranty", "label": "Warranty", "grade": "Above", "points": 100, "weight": 5, "reason": "3 years, on-site"},
            {"attribute_key": "unit_price", "label": "Unit price", "grade": "Meets", "points": 60, "weight": 20, "reason": "Rs 59,000"}]},
        "pros": [], "cons": [], "risks": ["Delivery depends on one factory"], "overall_summary": "A solid offer.",
        "warnings": list(warnings), "evaluation_failed": False, "tie_break_explanation": tie_break,
        "document_sha256": "ab" * 32, "pages": 12, "eligible": not failed,
    }
    result.update(changes)
    return result


def supplier(name: str, rank: int, *, eligible: bool = True, industry_score: float = 75.0, ppi: float = 80.0, overall: float = 70.0,
             rating: int | None = 3, when: str = "2026-10-05", result: dict[str, Any] | None = None, **result_options) -> dict[str, Any]:
    if result is None:
        result_options.setdefault("failed", {} if eligible else {"warranty": "1 year (at least 3 required)"})
        result = result_for(**result_options)
    return dict(name=name, rank=rank, eligible=eligible, industry=industry_score, ppi=ppi, overall=overall, rating=rating, when=when,
                result=result)


_run_numbers = itertools.count(1)
NO_USAGE: tuple[Any, Any, Any] = (None, None, None)


def store_run(*suppliers: dict[str, Any], run_id: str | None = None, budget_rs: float | None = 6000000, quantity: int | None = 100,
              reviewer: str = "Sri", provider: str | None = "OpenAI", model: str | None = "gpt-test", temperature: float | None = 0,
              usage: tuple[Any, Any, Any] = (5000, 0.05, 42.5), created_at: str = "2026-10-23T09:30:00+00:00",
              snapshot: Any = "default") -> tuple[repository.RunRecord, list[repository.StoredSupplier]]:
    run_id = run_id or f"RUN-TEST-{next(_run_numbers):03d}"
    if snapshot == "default":
        snapshot = [{"criterion_id": i, "name": n, "weight": w, "max_score": 10.0} for i, n, w in CRITERIA]
    with repository.connection() as conn:
        repository.create_run(
            conn, run_id=run_id, created_at=created_at, provider=provider, model=model, temperature=temperature,   # type: ignore[arg-type]
            template_id=repository.DEFAULT_TEMPLATE_ID, criteria_snapshot_json=json.dumps(snapshot), reviewer_name=reviewer,
            budget_rs=budget_rs, quantity=quantity)   # type: ignore[arg-type]
        for s in suppliers:
            repository.save_supplier_result(
                conn, run_id=run_id, supplier_name=s["name"], submission_date=s["when"], experience_rating=s["rating"],
                absolute_score=s["overall"], ppi=s["ppi"], industry_score=s["industry"], final_rank=s["rank"],
                gates_passed=s["eligible"], result_json=json.dumps(s["result"]))
        if usage != NO_USAGE:
            repository.finish_run(conn, run_id, status="Evaluated", total_tokens=usage[0], total_cost_usd=usage[1], duration_seconds=usage[2])
        run = repository.get_run(conn, run_id)
        stored = repository.list_supplier_results(conn, run_id)
    assert run is not None
    return run, stored


def made(*suppliers: dict[str, Any], **options):
    """(run, stored suppliers, overview, export, details) of a run saved by hand."""
    run, stored = store_run(*suppliers, **options)
    overview = tab_overview.build_overview(run, stored)
    return run, stored, overview, A.build_export(run, stored, overview), A.build_details(run, stored, overview, "Startup laptops")


def export_of(*suppliers: dict[str, Any], **options) -> dict[str, Any]:
    return made(*suppliers, **options)[3]


def details_of(*suppliers: dict[str, Any], **options) -> A.RunDetails:
    return made(*suppliers, **options)[4]


def fact(details: A.RunDetails, label: str) -> A.Fact:
    return next(f for f in details.facts if f.label == label)


def three() -> tuple[dict[str, Any], ...]:
    return supplier("Apex Systems", 1), supplier("NexaWorks", 2), supplier("Orbit Digital", 3)


# ---------------------------------------------------------------- the file has the shape of spec section 15

def test_the_file_has_the_keys_of_section_15_in_the_same_order_and_no_others():
    export = export_of(*three())
    assert list(export) == SPEC_TOP_LEVEL
    assert list(export["recommendation"]) == SPEC_RECOMMENDATION
    assert list(export["totals"]) == SPEC_TOTALS
    for item in export["criteria_snapshot"]:
        assert list(item) == SPEC_SNAPSHOT
    for s in export["suppliers"]:
        assert list(s) == SPEC_SUPPLIER
        for c in s["criteria"]:
            assert list(c) == SPEC_CRITERION


def test_the_constants_in_the_tab_are_the_spec_lists():
    assert list(A.TOP_LEVEL_KEYS) == SPEC_TOP_LEVEL and list(A.SNAPSHOT_KEYS) == SPEC_SNAPSHOT
    assert list(A.SUPPLIER_KEYS) == SPEC_SUPPLIER and list(A.CRITERION_KEYS) == SPEC_CRITERION
    assert list(A.RECOMMENDATION_KEYS) == SPEC_RECOMMENDATION and list(A.TOTALS_KEYS) == SPEC_TOTALS


def test_the_header_of_the_file_is_the_run_as_saved():
    run, _stored, _overview, export, _details = made(*three(), run_id="RUN-20261023-093000-ab12")
    assert export["rfp_run_id"] == "RUN-20261023-093000-ab12" and export["created_at"] == "2026-10-23T09:30:00+00:00"
    assert export["status"] == "Evaluated" and export["provider"] == "OpenAI" and export["model"] == "gpt-test"
    assert export["temperature"] == 0 and export["template"] == "startup_laptops"
    assert export["totals"] == {"tokens": 5000, "cost_usd": 0.05, "duration_seconds": 42.5}
    assert run.template_id == export["template"]


def test_the_temperature_in_the_file_is_the_one_the_run_used():
    assert export_of(supplier("Apex Systems", 1), temperature=0.2)["temperature"] == 0.2
    assert export_of(supplier("Apex Systems", 1), temperature=None)["temperature"] is None


def test_the_marking_scheme_of_the_run_is_in_the_file_as_it_was_when_the_run_started():
    export = export_of(*three())
    assert export["criteria_snapshot"] == [{"criterion_id": i, "name": n, "weight": w, "max_score": 10.0} for i, n, w in CRITERIA]


def test_a_snapshot_with_junk_entries_keeps_only_the_objects_and_only_the_four_keys():
    run, stored, overview, _export, _details = made(supplier("Apex Systems", 1))
    junk = replace(run, criteria_snapshot=[{"criterion_id": 1, "name": "A", "weight": 50, "max_score": 10, "extra": "x"}, "junk", 7, None])
    assert A.build_export(junk, stored, overview)["criteria_snapshot"] == [{"criterion_id": 1, "name": "A", "weight": 50, "max_score": 10}]


def test_the_suppliers_are_in_rank_order_with_the_saved_scores():
    export = export_of(supplier("Orbit Digital", 3, ppi=60.5, overall=66.0, industry_score=50.0), supplier("Apex Systems", 1, ppi=86.94, overall=77.0),
                       supplier("NexaWorks", 2, rating=None, when="2026-10-06"))
    assert [s["rank"] for s in export["suppliers"]] == [1, 2, 3]
    apex, nexa, orbit = export["suppliers"]
    assert (apex["supplier_name"], apex["ppi"], apex["absolute_score"], apex["submission_date"], apex["experience_rating"]) == (
        "Apex Systems", 86.94, 77.0, "2026-10-05", 3)
    assert nexa["experience_rating"] is None and nexa["submission_date"] == "2026-10-06"
    assert (orbit["industry_score"], orbit["eligible"]) == (50.0, True)


def test_eligibility_and_the_gate_results_are_the_saved_ones():
    export = export_of(supplier("Apex Systems", 1), supplier("BrightPath Tech", 2, eligible=False, failed={"warranty": "1 year", "certification": "Not stated"}))
    apex, bright = export["suppliers"]
    assert apex["eligible"] is True and apex["gates"] == {"warranty": "pass", "certification": "pass", "price_table": "pass", "delivery": "pass"}
    assert bright["eligible"] is False and bright["gates"] == {"warranty": "fail", "certification": "fail", "price_table": "pass", "delivery": "pass"}


def test_each_criterion_carries_its_saved_marks_benchmark_gap_evidence_and_justification():
    apex = export_of(supplier("Apex Systems", 1, scores=(9, 7, 5, 9, 8)))["suppliers"][0]
    assert len(apex["criteria"]) == 5
    second = apex["criteria"][1]
    assert second == {"criterion_id": 2, "score": 7, "max_score": 10.0, "benchmark": 9, "gap": -2, "relative_pct": 77.78, "weight": 20.0,
                      "strength": "Average", "justification": "why 2", "evidence": "quote 2 (Page 2)", "evidence_found": None}
    assert [c["strength"] for c in apex["criteria"]] == ["Strong", "Average", "Average", "Strong", "Strong"]


def test_the_extracted_facts_the_industry_breakdown_the_risks_and_the_fingerprint_are_copied():
    apex = export_of(supplier("Apex Systems", 1))["suppliers"][0]
    assert apex["extracted_facts"] == FACTS
    assert apex["industry_breakdown"]["score"] == 75.0 and [a["attribute_key"] for a in apex["industry_breakdown"]["attributes"]] == ["warranty", "unit_price"]
    assert apex["risks"] == ["Delivery depends on one factory"] and apex["document_sha256"] == "ab" * 32


def test_the_warnings_of_a_supplier_are_all_in_the_file_even_when_every_supplier_shares_them():
    shared = "Unit price estimated from total"
    export = export_of(supplier("Apex Systems", 1, warnings=[shared, "Criterion 2 was missing"]), supplier("NexaWorks", 2, warnings=[shared]))
    assert export["suppliers"][0]["warnings"] == [shared, "Criterion 2 was missing"] and export["suppliers"][1]["warnings"] == [shared]


def test_pros_and_cons_are_the_same_ones_the_overview_shows():
    run, stored, overview, export, _details = made(supplier("Apex Systems", 1), supplier("BrightPath Tech", 2, eligible=False, scores=(4, 3, 8, 3, 3)))
    for exported, row in zip(export["suppliers"], overview.rows):
        assert exported["pros"] == [r.text for r in row.pros] and exported["cons"] == [r.text for r in row.cons]
    assert export["suppliers"][0]["pros"] and export["suppliers"][1]["cons"]
    assert all(isinstance(text, str) for s in export["suppliers"] for text in s["pros"] + s["cons"])


def test_feedback_rounds_and_negotiation_points_are_empty_lists_until_those_features_exist():
    export = export_of(*three())
    assert export["feedback_rounds"] == [] and export["negotiation_points"] == []


def test_the_totals_are_null_when_the_run_did_not_report_them():
    export = export_of(supplier("Apex Systems", 1), usage=(None, None, None))
    assert export["totals"] == {"tokens": None, "cost_usd": None, "duration_seconds": None}
    assert json.loads(A.export_text(export))["totals"] == {"tokens": None, "cost_usd": None, "duration_seconds": None}


# ---------------------------------------------------------------- the recommendation and the tie-breaks in the file

def test_the_recommendation_is_the_top_eligible_supplier_and_the_runner_up_skips_the_ineligible():
    export = export_of(supplier("Apex Systems", 1, eligible=False), supplier("NexaWorks", 2), supplier("Horizon Datacom", 3, eligible=False),
                       supplier("Orbit Digital", 4))
    assert export["recommendation"]["recommended"] == "NexaWorks" and export["recommendation"]["runner_up"] == "Orbit Digital"


def test_the_reason_is_the_sentence_the_overview_banner_shows():
    run, stored, overview, export, _details = made(*three())
    assert export["recommendation"]["reason"] == overview.verdict.sentence
    assert "meets every must-have" in export["recommendation"]["reason"] and "budget" in export["recommendation"]["reason"]


def test_when_nobody_is_eligible_there_is_no_recommendation_and_the_reason_says_why():
    export = export_of(supplier("Apex Systems", 1, eligible=False), supplier("NexaWorks", 2, eligible=False))
    recommendation = export["recommendation"]
    assert recommendation["recommended"] is None and recommendation["runner_up"] is None
    assert "Apex Systems" in recommendation["reason"] and "not eligible" in recommendation["reason"]


def test_a_recommended_supplier_under_60_on_industry_gets_the_industry_warning_in_the_file():
    weak = [{"attribute_key": "w", "label": "Warranty", "grade": "Below"}, {"attribute_key": "d", "label": "Delivery", "grade": "Not stated"}]
    export = export_of(supplier("Apex Systems", 1, industry_score=45.0, attributes=weak), supplier("NexaWorks", 2))
    assert export["recommendation"]["industry_warning"] == "Below industry standard on: Warranty, Delivery"
    assert export_of(supplier("Apex Systems", 1, industry_score=75.0))["recommendation"]["industry_warning"] is None


def test_the_tie_break_explanations_are_listed_in_rank_order_and_empty_without_ties():
    export = export_of(supplier("Apex Systems", 1), supplier("NexaWorks", 2, tie_break="NexaWorks above Horizon Datacom: higher experience rating (4 vs 3)"),
                       supplier("Horizon Datacom", 3), supplier("Orbit", 4, tie_break="Orbit above Zed: earlier submission"))
    assert export["tie_break_explanations"] == ["NexaWorks above Horizon Datacom: higher experience rating (4 vs 3)", "Orbit above Zed: earlier submission"]
    assert export_of(*three())["tie_break_explanations"] == []


# ---------------------------------------------------------------- the text of the file

def test_the_text_is_readable_json_that_reads_back_to_the_same_data():
    export = export_of(*three())
    text = A.export_text(export)
    assert json.loads(text) == export
    lines = text.splitlines()
    assert lines[0] == "{" and lines[1] == '  "rfp_run_id": "RUN-TEST-' + export["rfp_run_id"][len("RUN-TEST-"):] + '",'
    assert text.index('"rfp_run_id"') < text.index('"created_at"') < text.index('"suppliers"') < text.index('"totals"')


def test_real_characters_are_written_not_escapes():
    export = export_of(supplier("Zenora Systems", 1, facts={"note": "Price ₹5,90,000 — incl. GST"}), supplier("Müller & Söhne", 2))
    text = A.export_text(export)
    assert "₹5,90,000 — incl. GST" in text and "Müller & Söhne" in text and "\\u" not in text


def test_numbers_that_are_not_finite_become_null_so_the_file_is_always_valid_json():
    result = result_for(facts={"unit_price_rs": float("nan"), "list": [1.5, float("inf")], "nested": {"x": float("-inf")}})
    result["criteria"][0]["gap"] = float("nan")
    export = export_of(supplier("Apex Systems", 1, result=result))
    text = A.export_text(export)
    assert "NaN" not in text and "Infinity" not in text
    parsed = json.loads(text, parse_constant=lambda name: pytest.fail(f"{name} in the file"))
    apex = parsed["suppliers"][0]
    assert apex["extracted_facts"]["unit_price_rs"] is None and apex["extracted_facts"]["list"] == [1.5, None]
    assert apex["extracted_facts"]["nested"] == {"x": None} and apex["criteria"][0]["gap"] is None


def test_the_text_builder_refuses_to_write_invalid_json_rather_than_write_nan():
    with pytest.raises(ValueError):
        A.export_text({"x": float("nan")})
    with pytest.raises(ValueError):
        A.export_text({"x": [float("inf")]})


def test_the_bytes_are_utf_8_and_text_that_cannot_be_encoded_does_not_stop_the_download():
    export = export_of(supplier("Apex Systems", 1, facts={"note": "café \ud800 end"}))
    data = A.export_bytes(export)
    assert isinstance(data, bytes) and "café".encode("utf-8") in data and json.loads(data.decode("utf-8"))["suppliers"][0]["rank"] == 1


def junked(result: dict[str, Any]) -> dict[str, Any]:
    """The file built from a saved result that was edited by hand into junk. The Overview tab is not built from it (it is known
    not to survive hand-edited data), only the file builder, which has to."""
    run, stored, overview, _export, _details = made(supplier("Apex Systems", 1))
    return A.build_export(run, [replace(stored[0], result=result)], overview)["suppliers"][0]


def test_junk_in_a_saved_result_gives_empty_values_not_an_error():
    junk = {"criteria": "none", "gates": ["a"], "extracted_facts": "x", "industry_breakdown": 7, "risks": "oops", "warnings": 5, "document_sha256": None}
    apex = junked(junk)
    assert list(apex) == SPEC_SUPPLIER
    assert apex["criteria"] == [] and apex["gates"] == {} and apex["extracted_facts"] == {} and apex["industry_breakdown"] == {}
    assert apex["risks"] == [] and apex["warnings"] == [] and apex["document_sha256"] is None


def test_a_supplier_with_nothing_saved_still_has_every_key():
    apex = export_of(supplier("Apex Systems", 1, result={}))["suppliers"][0]
    assert list(apex) == SPEC_SUPPLIER and apex["criteria"] == [] and apex["rank"] == 1 and apex["pros"] == [] and apex["cons"] == []


def test_criteria_that_are_not_objects_are_left_out_and_missing_values_are_null():
    result = result_for()
    result["criteria"] = [result["criteria"][0], "junk", {"criterion_id": 9}]
    criteria = junked(result)["criteria"]
    assert len(criteria) == 2 and list(criteria[1]) == SPEC_CRITERION and criteria[1]["criterion_id"] == 9 and criteria[1]["score"] is None


@pytest.mark.parametrize("run_id, expected", [
    ("RUN-20261023-093000-ab12", "RUN-20261023-093000-ab12.json"),
    ("RUN/..\\evil name?", "RUN_.._evil_name.json"),
    ("", "run.json"),
    ("...", "run.json"),
])
def test_the_file_name_is_the_run_id_made_safe(run_id, expected):
    assert A.export_file_name(run_id) == expected


# ---------------------------------------------------------------- nothing secret, nothing extra, no proposal text

def test_only_the_spec_keys_are_copied_whatever_else_the_saved_result_holds():
    result = result_for(api_key="sk-live-123", raw_reply="the whole model reply", full_text="the whole proposal", prompt="a prompt")
    result["criteria"][0]["api_key"] = "sk-live-456"
    result["criteria"][0]["raw"] = "raw"
    text = A.export_text(export_of(supplier("Apex Systems", 1, result=result)))
    for leaked in ("sk-live", "the whole model reply", "the whole proposal", "a prompt", '"raw"', "file_name", "gate_details", "filled_by_validator",
                   "overall_summary", "evaluation_failed", "tie_break_explanation\"", "pages"):
        assert leaked not in text, leaked


def test_a_field_that_looks_like_a_secret_is_dropped_wherever_it_is_inside_the_free_form_parts():
    facts = {"warranty_years": 3, "api_key": "sk-1", "nested": {"OPENAI_API_KEY": "sk-2", "keep": 1}, "items": [{"password": "sk-3", "ok": True}]}
    breakdown = {"score": 70, "attributes": [{"label": "Warranty", "client_secret": "sk-4"}]}
    apex = export_of(supplier("Apex Systems", 1, facts=facts, result=result_for(facts=facts, industry_breakdown=breakdown)))["suppliers"][0]
    assert apex["extracted_facts"] == {"warranty_years": 3, "nested": {"keep": 1}, "items": [{"ok": True}]}
    assert apex["industry_breakdown"] == {"score": 70, "attributes": [{"label": "Warranty"}]}
    assert "sk-" not in A.export_text({"suppliers": [apex]})


def test_values_that_are_not_plain_json_become_text():
    facts = {"when": date(2026, 10, 5), "pair": (1, 2), "blob": {7}}
    assert A._clean(facts) == {"when": "2026-10-05", "pair": [1, 2], "blob": "{7}"}
    assert A._clean({1: "a"}) == {"1": "a"} and A._clean(None) is None and A._clean(True) is True


# ---------------------------------------------------------------- a real batch, through the orchestrator

class _FakeBackend:
    def __init__(self, *replies: str) -> None:
        self.replies, self.calls = list(replies), 0

    def completion(self, **kwargs):
        text = self.replies[self.calls]
        self.calls += 1
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=SimpleNamespace(total_tokens=1000))

    def supports_json_mode(self, settings) -> bool:
        return True

    def cost(self, response) -> float:
        return 0.01


FIVE = [("Apex Systems", date(2026, 10, 5), 4, [9, 7, 5, 9, 8]), ("BrightPath Tech", date(2026, 10, 7), 2, [5, 4, 8, 3, 3]),
        ("NexaWorks", date(2026, 10, 6), 4, [7, 9, 7, 7, 9]), ("Horizon Datacom", date(2026, 10, 6), 3, [7, 9, 7, 7, 9]),
        ("Orbit Digital", date(2026, 10, 8), 5, [7, 5, 7, 6, 9])]
GOOD_FACTS = {"warranty_years": 3, "warranty_type": "on-site", "bis_named": True, "price_table_present": True, "delivery_weeks": 6,
              "grand_total_rs": 5900000, "battery_hours": 11.5, "battery_measured": True}
BAD_FACTS = {"warranty_years": 1, "warranty_type": "carry-in", "bis_named": None, "price_table_present": True, "delivery_weeks": 6}


def model_reply(scores: list[int], facts: dict[str, Any]) -> str:
    return json.dumps({"supplier_name": "Supplier A", "criteria": [
        {"criterion_id": i, "score": s, "justification": f"why {i}", "evidence": f"quote {i} (Page {i})"} for i, s in enumerate(scores, start=1)],
        "extracted_facts": facts, "risks": [], "overall_summary": "ok"})


def run_batch():
    from agents import orchestrator
    proposals = []
    for name, when, rating, _scores in FIVE:
        pdf = pdf_tool.ExtractedPdf("0" * 64, (f"Offer from {name}. {SENTINEL} " + "Long proposal text. " * 50,), "pypdf")
        proposals.append(orchestrator.Proposal(f"{name.lower().replace(' ', '_')}.pdf", name, when, rating, pdf))
    texts = [model_reply(scores, BAD_FACTS if name == "BrightPath Tech" else GOOD_FACTS) for name, _w, _r, scores in FIVE]
    return orchestrator.run_evaluation(
        proposals, industry.PurchaseTerms(6000000, 100), client.LLMSettings("OpenAI", "test-model", KEY), "Sri",
        backend=_FakeBackend(*texts), sleep=lambda _s: None, now=lambda: datetime(2026, 10, 23, 9, 30, tzinfo=timezone.utc))


def read_back(run_id: str):
    with repository.connection() as conn:
        run = repository.get_run(conn, run_id)
        stored = repository.list_supplier_results(conn, run_id)
    assert run is not None
    overview = tab_overview.build_overview(run, stored)
    return run, stored, overview, A.build_export(run, stored, overview)


def test_a_batch_that_was_really_run_exports_every_supplier_with_the_spec_keys():
    result = run_batch()
    assert result.status == "Evaluated"
    run, stored, _overview, export = read_back(result.run_id)
    assert list(export) == SPEC_TOP_LEVEL and export["rfp_run_id"] == result.run_id and export["status"] == "Evaluated"
    assert [s["supplier_name"] for s in export["suppliers"]] == [s.supplier_name for s in stored]
    for exported, saved in zip(export["suppliers"], stored):
        assert list(exported) == SPEC_SUPPLIER and len(exported["criteria"]) == 5 and exported["document_sha256"] == "0" * 64
        assert exported["ppi"] == saved.ppi and exported["rank"] == saved.final_rank and exported["eligible"] == saved.gates_passed
        assert [c["justification"] for c in exported["criteria"]] == [f"why {i}" for i in range(1, 6)]
    assert export["totals"]["tokens"] == 5000 and export["provider"] == "OpenAI" and export["model"] == "test-model"
    assert export["tie_break_explanations"] == ["NexaWorks above Horizon Datacom: higher experience rating (4 vs 3)"]
    assert export["recommendation"]["recommended"] == "Apex Systems"
    assert {s["supplier_name"]: s["gates"]["warranty"] for s in export["suppliers"]}["BrightPath Tech"] == "fail"


def all_keys(value: Any):
    """Every field name anywhere in a piece of JSON-like data."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from all_keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from all_keys(inner)


def test_the_api_key_and_the_full_proposal_text_are_not_in_the_file_of_a_real_run():
    result = run_batch()
    _run, _stored, _overview, export = read_back(result.run_id)
    text = A.export_text(export)
    assert KEY not in text and not any(audit.looks_like_secret_key(k) for k in all_keys(export))
    assert SENTINEL not in text and "Long proposal text" not in text


def test_exporting_does_not_put_the_key_or_the_proposal_text_in_the_audit_trail_either(monkeypatch):
    result = run_batch()
    page = _Page({"reviewer_name": "Sri"})
    monkeypatch.setattr(A, "st", page)
    A.note_export(result.run_id, A.export_file_name(result.run_id), 5, 1234)
    with repository.connection() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM audit_log WHERE rfp_run_id = ?", (result.run_id,))]
    assert any(r["action"] == A.EXPORT_ACTION for r in rows)
    whole = json.dumps(rows)
    assert KEY not in whole and SENTINEL not in whole


# ---------------------------------------------------------------- the run details

def test_the_facts_are_in_a_fixed_order_with_the_spec_names_for_the_run_id():
    details = details_of(*three())
    assert [f.label for f in details.facts] == ["Run ID (RFP_RUN_ID)", "Status", "Started", "AI model", "Marking scheme (template)", "Reviewer",
                                                "Purchase", "Suppliers", "Time taken", "AI usage"]


def test_the_facts_say_who_ran_what_with_which_model_when_and_for_how_much():
    details = details_of(*three(), run_id="RUN-20261023-093000-ab12")
    assert fact(details, "Run ID (RFP_RUN_ID)") == A.Fact("Run ID (RFP_RUN_ID)", "RUN-20261023-093000-ab12", mono=True)
    assert fact(details, "Status").value == "Evaluated" and fact(details, "Started").value == "23 Oct 2026, 09:30 UTC"
    assert (fact(details, "AI model").value, fact(details, "AI model").detail) == ("gpt-test", "OpenAI · temperature 0")
    assert (fact(details, "Marking scheme (template)").value, fact(details, "Marking scheme (template)").detail) == ("Startup laptops", "startup_laptops")
    assert fact(details, "Reviewer").value == "Sri" and fact(details, "Purchase").value == "Rs 60 lakh for 100 laptops"
    assert fact(details, "Suppliers").value == "3" and fact(details, "Time taken").value == "42.5 seconds"
    assert (fact(details, "AI usage").value, fact(details, "AI usage").detail) == ("5,000 tokens", "About USD 0.0500")


def test_only_the_run_id_is_shown_in_a_fixed_width_font():
    assert [f.label for f in details_of(*three()).facts if f.mono] == ["Run ID (RFP_RUN_ID)"]


def test_a_run_saved_without_the_optional_details_says_so_plainly():
    details = details_of(supplier("Apex Systems", 1), budget_rs=None, quantity=None, reviewer="", provider=None, model=None, temperature=None, usage=NO_USAGE)
    assert fact(details, "Purchase").value == A.NOT_RECORDED and fact(details, "Reviewer").value == A.NOT_ENTERED
    assert (fact(details, "AI model").value, fact(details, "AI model").detail) == (A.NOT_RECORDED, None)
    assert fact(details, "Time taken").value == A.NOT_RECORDED
    assert (fact(details, "AI usage").value, fact(details, "AI usage").detail) == ("Tokens not reported", "Cost not reported")


def test_a_template_that_is_not_in_the_database_shows_its_id():
    run, stored = store_run(supplier("Apex Systems", 1))
    details = A.build_details(run, stored, tab_overview.build_overview(run, stored), None)
    assert (fact(details, "Marking scheme (template)").value, fact(details, "Marking scheme (template)").detail) == ("startup_laptops", None)


def test_the_provider_and_temperature_are_each_optional_in_the_model_tile():
    assert fact(details_of(supplier("A", 1), provider=None), "AI model").detail == "temperature 0"
    assert fact(details_of(supplier("A", 1), temperature=None), "AI model").detail == "OpenAI"
    assert fact(details_of(supplier("A", 1), temperature=0.2), "AI model").detail == "OpenAI · temperature 0.2"


def test_one_laptop_is_singular_and_a_missing_quantity_names_only_the_budget():
    assert fact(details_of(supplier("A", 1), quantity=1), "Purchase").value == "Rs 60 lakh for 1 laptop"
    assert fact(details_of(supplier("A", 1), quantity=None), "Purchase").value == "Rs 60 lakh"
    assert fact(details_of(supplier("A", 1), budget_rs=0), "Purchase").value == A.NOT_RECORDED


def test_zero_tokens_are_reported_as_zero_not_as_missing():
    assert fact(details_of(supplier("A", 1), usage=(0, 0.0, 1.0)), "AI usage").value == "0 tokens"
    assert fact(details_of(supplier("A", 1), usage=(0, 0.0, 1.0)), "AI usage").detail == "About USD 0.0000"


@pytest.mark.parametrize("stamp, expected", [
    ("2026-10-23T09:30:00+00:00", "23 Oct 2026, 09:30 UTC"),
    ("2026-10-23T09:30:00+05:30", "23 Oct 2026, 04:00 UTC"),
    ("2026-10-23T23:30:00-02:00", "24 Oct 2026, 01:30 UTC"),
    ("2026-10-05T09:30:00", "5 Oct 2026, 09:30 UTC"),
    ("yesterday afternoon", "yesterday afternoon"),
    ("", A.NOT_RECORDED),
])
def test_the_start_time_is_shown_in_utc_and_unreadable_text_is_shown_as_it_is(stamp, expected):
    assert A.started_text(stamp) == expected


@pytest.mark.parametrize("seconds, expected", [
    (0, "0.0 seconds"), (42.34, "42.3 seconds"), (59.9, "59.9 seconds"), (59.96, "1 min 00 s"), (60, "1 min 00 s"),
    (61.4, "1 min 01 s"), (125.4, "2 min 05 s"), (3600, "60 min 00 s"),
    (-1, A.NOT_RECORDED), (None, A.NOT_RECORDED), (float("nan"), A.NOT_RECORDED), ("42", A.NOT_RECORDED), (True, A.NOT_RECORDED),
])
def test_the_time_taken_is_readable(seconds, expected):
    assert A.duration_text(seconds) == expected


# ---------------------------------------------------------------- warnings, grouped

def stored_with(*warning_lists) -> list[repository.StoredSupplier]:
    suppliers = [supplier(f"Supplier {n}", n, warnings=w) for n, w in enumerate(warning_lists, start=1)]
    return store_run(*suppliers)[1]


def test_a_warning_every_supplier_has_is_listed_once_under_all_suppliers():
    shared = "Criterion 2: every supplier scored 0, so relative % is set to 100 for all."
    groups = A.group_warnings(stored_with([shared, "Supplier 1 only"], [shared], [shared, "Supplier 3 only"]))
    assert [(g.label, g.warnings) for g in groups] == [(A.ALL_SUPPLIERS, (shared,)), ("Supplier 1", ("Supplier 1 only",)), ("Supplier 3", ("Supplier 3 only",))]


def test_a_warning_only_some_suppliers_have_stays_with_those_suppliers():
    groups = A.group_warnings(stored_with(["same"], ["same"], []))
    assert [(g.label, g.warnings) for g in groups] == [("Supplier 1", ("same",)), ("Supplier 2", ("same",))]


def test_with_one_supplier_its_warnings_are_its_own_not_all_suppliers():
    groups = A.group_warnings(stored_with(["Only one supplier was evaluated.", "Another"]))
    assert [(g.label, g.warnings) for g in groups] == [("Supplier 1", ("Only one supplier was evaluated.", "Another"))]


def test_a_supplier_whose_only_warnings_are_shared_gets_no_group_of_its_own():
    groups = A.group_warnings(stored_with(["x"], ["x"]))
    assert [g.label for g in groups] == [A.ALL_SUPPLIERS]


def test_a_warning_repeated_inside_one_supplier_is_kept_as_often_as_it_was_raised():
    groups = A.group_warnings(stored_with(["x", "x"], ["x"]))
    assert [(g.label, g.warnings) for g in groups] == [(A.ALL_SUPPLIERS, ("x",)), ("Supplier 1", ("x",))]


def test_warnings_that_are_not_text_or_are_blank_are_ignored_and_line_breaks_are_flattened():
    stored = store_run(supplier("Apex", 1, result=result_for(warnings=["  spaced   out\nline ", "", "   ", 5, None, {"a": 1}])))[1]
    assert A.warnings_of(stored[0]) == ("spaced out line",)
    assert A.group_warnings(stored)[0].warnings == ("spaced out line",)


def test_the_warning_count_counts_each_shared_warning_once():
    details = details_of(supplier("S1", 1, warnings=["shared", "own 1a", "own 1b"]), supplier("S2", 2, warnings=["shared", "own 2"]),
                         supplier("S3", 3, warnings=["shared"]))
    assert details.warning_count == 4 and [g.label for g in details.warning_groups] == [A.ALL_SUPPLIERS, "S1", "S2"]
    assert [len(g.warnings) for g in details.warning_groups] == [1, 2, 1]
    assert details_of(*three()).warning_count == 0 and details_of(*three()).warning_groups == ()


def test_the_summary_line_counts_and_uses_the_right_number():
    assert A.warning_summary(0) == A.NO_WARNINGS_TEXT
    assert A.warning_summary(1) == "1 warning in this run." and A.warning_summary(7) == "7 warnings in this run."


def test_the_tie_breaks_of_the_details_are_the_ones_in_the_file():
    run, stored, overview, export, details = made(supplier("A", 1), supplier("B", 2, tie_break="B above C: earlier submission"), supplier("C", 3))
    assert details.tie_breaks == ("B above C: earlier submission",) and list(details.tie_breaks) == export["tie_break_explanations"]
    assert details_of(*three()).tie_breaks == ()


# ---------------------------------------------------------------- the HTML

def test_the_facts_html_is_one_line_with_its_own_style_and_every_label_and_value():
    markup = A.facts_html(details_of(*three(), run_id="RUN-X-1").facts)
    assert "\n" not in markup and markup.startswith("<style>") and markup.count('class="au-fact"') == 10
    for expected in ("Run ID (RFP_RUN_ID)", "RUN-X-1", "Marking scheme (template)", "Startup laptops", "startup_laptops", "5,000 tokens", "About USD 0.0500"):
        assert expected in markup
    assert markup.count('class="au-value au-mono"') == 1 and markup.count('class="au-detail"') == 3 and "None" not in markup


def test_text_from_the_buyer_or_a_model_cannot_inject_html_into_the_tiles():
    details = details_of(supplier("Apex", 1), reviewer="<script>alert(1)</script>", model="<img src=x onerror=1>")
    markup = A.facts_html(details.facts)
    assert "<script" not in markup and "<img" not in markup and "&lt;script&gt;" in markup and "&lt;img" in markup


def test_warnings_html_names_each_group_escapes_every_warning_and_stays_on_one_line():
    groups = (A.WarningGroup(A.ALL_SUPPLIERS, ("shared <b>bold</b>",)), A.WarningGroup("Evil <i>Ltd</i>", ("line one\nline two", 'quote " & more')))
    markup = A.warnings_html(groups)
    assert "\n" not in markup and "<b>" not in markup and "<i>" not in markup
    assert "All suppliers" in markup and "Evil &lt;i&gt;Ltd&lt;/i&gt;" in markup and "line one line two" in markup and "quote &quot; &amp; more" in markup
    assert markup.count('class="au-group"') == 2 and markup.count("<li>") == 3


def test_tie_breaks_html_escapes_supplier_names():
    markup = A.tie_breaks_html(("A<script> above B: earlier submission",))
    assert "<script" not in markup and "&lt;script&gt;" in markup and "\n" not in markup and markup.count("<li>") == 1


# ---------------------------------------------------------------- the page, drawn into a recording stand-in

class _Page:
    """A recording stand-in for the parts of Streamlit that this tab is drawn with."""

    def __init__(self, session: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.session_state: dict[str, Any] = dict(session or {})

    def _record(self, kind: str, text: Any, kwargs: dict[str, Any]) -> None:
        self.calls.append((kind, str(text), kwargs))

    def markdown(self, text, **kwargs):
        self._record("markdown", text, kwargs)

    def info(self, text, **kwargs):
        self._record("info", text, kwargs)

    def error(self, text, **kwargs):
        self._record("error", text, kwargs)

    def warning(self, text, **kwargs):
        self._record("warning", text, kwargs)

    def download_button(self, label, **kwargs):
        self._record("download_button", label, kwargs)
        return False

    @property
    def kinds(self) -> list[str]:
        return [kind for kind, _text, _kw in self.calls]

    def text(self) -> str:
        return "\n".join(text for _kind, text, _kw in self.calls)


def drawn(monkeypatch, *suppliers: dict[str, Any], session: dict[str, Any] | None = None, run_id: str = "RUN-PAGE-1", **options) -> _Page:
    store_run(*suppliers, run_id=run_id, **options)
    page = _Page({tab_new_evaluation.SHOWN_RUN_KEY: run_id, **(session or {})})
    monkeypatch.setattr(A, "st", page)
    A.render()
    return page


def test_the_page_order_is_details_download_warnings_then_tie_breaks(monkeypatch):
    page = drawn(monkeypatch, supplier("Apex", 1, warnings=["Look at this"]), supplier("Nexa", 2, tie_break="Nexa above Orbit: earlier submission"), supplier("Orbit", 3))
    headings = [text for kind, text, _kw in page.calls if kind == "markdown" and text.startswith("####")]
    assert headings == ["#### Run details", "#### Warnings", "#### Tie-breaks"]

    def at(kind: str, text: str) -> int:
        return next(i for i, (k, t, _kw) in enumerate(page.calls) if k == kind and t.startswith(text))

    order = [at("markdown", "#### Run details"), at("markdown", "<style>"), at("download_button", A.DOWNLOAD_LABEL),
             at("markdown", "#### Warnings"), at("markdown", "#### Tie-breaks")]
    assert order == sorted(order) and len(set(order)) == 5
    assert 'class="au-facts"' in page.calls[order[1]][1]


def test_the_download_button_hands_over_the_file_for_this_run(monkeypatch):
    page = drawn(monkeypatch, *three(), run_id="RUN-20261023-093000-ab12")
    buttons = [(text, kw) for kind, text, kw in page.calls if kind == "download_button"]
    assert len(buttons) == 1
    label, kw = buttons[0]
    assert label == "Download JSON" and kw["file_name"] == "RUN-20261023-093000-ab12.json" and kw["mime"] == "application/json"
    assert kw["key"] == A.DOWNLOAD_KEY and kw["on_click"] is A.note_export
    exported = json.loads(kw["data"].decode("utf-8"))
    assert list(exported) == SPEC_TOP_LEVEL and exported["rfp_run_id"] == "RUN-20261023-093000-ab12" and len(exported["suppliers"]) == 3
    assert kw["args"] == ("RUN-20261023-093000-ab12", "RUN-20261023-093000-ab12.json", 3, len(kw["data"]))


def test_the_page_says_what_the_file_holds_and_what_it_never_holds(monkeypatch):
    text = drawn(monkeypatch, *three()).text()
    assert A.JSON_NOTE in text and "never contains an API key" in A.JSON_NOTE and "full text of a proposal" in A.JSON_NOTE


def test_a_run_without_warnings_or_ties_says_so_instead_of_drawing_empty_boxes(monkeypatch):
    page = drawn(monkeypatch, *three())
    text = page.text()
    assert A.NO_WARNINGS_TEXT in text and A.NO_TIES_TEXT in text
    assert 'class="au-group"' not in text and "<li>" not in text


def test_warnings_and_tie_breaks_are_drawn_when_there_are_some(monkeypatch):
    page = drawn(monkeypatch, supplier("Apex", 1, warnings=["Criterion 2 was missing"]), supplier("Nexa", 2, tie_break="Nexa above Orbit: earlier submission"))
    text = page.text()
    assert "1 warning in this run." in text and "Criterion 2 was missing" in text and "Nexa above Orbit: earlier submission" in text
    assert A.NO_WARNINGS_TEXT not in text and A.NO_TIES_TEXT not in text


def test_every_piece_of_html_is_drawn_with_html_allowed_and_nothing_else_is(monkeypatch):
    page = drawn(monkeypatch, supplier("Apex", 1, warnings=["w"]), supplier("Nexa", 2, tie_break="Nexa above Orbit: x"))
    for kind, text, kw in page.calls:
        if kind == "markdown" and "<" in text:
            assert kw.get("unsafe_allow_html") is True, text
        if kind == "markdown" and text.startswith("####"):
            assert not kw.get("unsafe_allow_html")


def test_before_any_run_the_tab_explains_what_to_do(monkeypatch):
    page = _Page()
    monkeypatch.setattr(A, "st", page)
    A.render()
    assert page.kinds == ["info"] and page.text() == A.EMPTY_TEXT and "New evaluation" in A.EMPTY_TEXT and "JSON download" in A.EMPTY_TEXT


def test_a_run_that_cannot_be_found_gets_a_friendly_message(monkeypatch):
    page = _Page({tab_new_evaluation.SHOWN_RUN_KEY: "RUN-DOES-NOT-EXIST"})
    monkeypatch.setattr(A, "st", page)
    A.render()
    assert page.kinds == ["info"] and page.text() == A.NOT_FOUND_TEXT


def test_a_run_with_no_saved_suppliers_gets_the_same_friendly_message(monkeypatch):
    store_run(run_id="RUN-EMPTY")
    page = _Page({tab_new_evaluation.SHOWN_RUN_KEY: "RUN-EMPTY"})
    monkeypatch.setattr(A, "st", page)
    A.render()
    assert page.kinds == ["info"] and page.text() == A.NOT_FOUND_TEXT


@pytest.mark.parametrize("failure", [sqlite3.OperationalError("database is locked"), OSError("locked")])
def test_a_database_that_cannot_be_read_gives_a_friendly_error_not_a_stack_trace(monkeypatch, failure):
    def broken(*_a, **_k):
        raise failure

    page = _Page({tab_new_evaluation.SHOWN_RUN_KEY: "RUN-X"})
    monkeypatch.setattr(A, "st", page)
    monkeypatch.setattr(A.repository, "connection", broken)
    A.render()
    assert page.kinds == ["error"] and page.text() == A.DATABASE_ERROR_TEXT and "rfp_evaluation.db" in A.DATABASE_ERROR_TEXT


def test_the_page_confirms_an_export_that_was_noted_in_the_audit_trail(monkeypatch):
    page = drawn(monkeypatch, *three(), session={A.EXPORTED_KEY: ("RUN-PAGE-1", "RUN-PAGE-1.json", True)})
    assert "Exported as RUN-PAGE-1.json. This export was added to the audit trail." in page.text() and "warning" not in page.kinds


def test_the_page_warns_when_an_export_could_not_be_noted(monkeypatch):
    page = drawn(monkeypatch, *three(), session={A.EXPORTED_KEY: ("RUN-PAGE-1", "RUN-PAGE-1.json", False)})
    assert page.kinds.count("warning") == 1 and "could not be added to the audit trail" in page.text()
    assert "This export was added" not in page.text()


def test_an_export_of_another_run_is_not_mentioned(monkeypatch):
    page = drawn(monkeypatch, *three(), session={A.EXPORTED_KEY: ("RUN-OTHER", "RUN-OTHER.json", True)})
    assert "Exported as" not in page.text() and "warning" not in page.kinds
    page = drawn(monkeypatch, *three(), run_id="RUN-PAGE-2", session={A.EXPORTED_KEY: "junk"})
    assert "Exported as" not in page.text()


def test_the_page_draws_no_chart_and_no_tab_content_that_belongs_to_a_later_feature(monkeypatch):
    text = drawn(monkeypatch, *three()).text()
    for later in ("Override rate", "Evidence-not-found", "Run history", "Change trail", "fingerprint"):
        assert later.lower() not in text.lower(), later


# ---------------------------------------------------------------- the click: "JSON exported" in the audit trail

def audit_rows(run_id: str, action: str = A.EXPORT_ACTION) -> list[dict[str, Any]]:
    with repository.connection() as conn:
        rows = conn.execute("SELECT actor, action, details_json FROM audit_log WHERE rfp_run_id = ? AND action = ? ORDER BY id", (run_id, action)).fetchall()
    return [{"actor": r["actor"], "action": r["action"], "details": json.loads(r["details_json"])} for r in rows]


def test_a_click_adds_one_json_exported_row_with_the_reviewer_as_actor(monkeypatch):
    store_run(*three(), run_id="RUN-CLICK-1")
    page = _Page({"reviewer_name": "  Sri  "})
    monkeypatch.setattr(A, "st", page)
    A.note_export("RUN-CLICK-1", "RUN-CLICK-1.json", 3, 4321)
    assert audit_rows("RUN-CLICK-1") == [{"actor": "Sri", "action": "json_exported", "details": {"bytes": 4321, "file_name": "RUN-CLICK-1.json", "suppliers": 3}}]
    assert page.session_state[A.EXPORTED_KEY] == ("RUN-CLICK-1", "RUN-CLICK-1.json", True)


def test_without_a_reviewer_name_the_row_names_the_reviewer_role(monkeypatch):
    store_run(*three(), run_id="RUN-CLICK-2")
    monkeypatch.setattr(A, "st", _Page({"reviewer_name": "   "}))
    A.note_export("RUN-CLICK-2", "x.json", 3, 10)
    monkeypatch.setattr(A, "st", _Page())
    A.note_export("RUN-CLICK-2", "x.json", 3, 10)
    assert [r["actor"] for r in audit_rows("RUN-CLICK-2")] == ["reviewer", "reviewer"]


def test_every_click_is_a_new_row_and_other_audit_rows_are_left_alone(monkeypatch):
    store_run(*three(), run_id="RUN-CLICK-3")
    with repository.connection() as conn:
        audit.log_event(conn, "RUN-CLICK-3", "system", "run_created", {"n": 1})
    monkeypatch.setattr(A, "st", _Page({"reviewer_name": "Sri"}))
    for _ in range(3):
        A.note_export("RUN-CLICK-3", "x.json", 3, 10)
    assert len(audit_rows("RUN-CLICK-3")) == 3 and len(audit_rows("RUN-CLICK-3", "run_created")) == 1


def test_the_file_is_the_same_before_and_after_a_click(monkeypatch):
    run, stored = store_run(*three(), run_id="RUN-CLICK-4")
    overview = tab_overview.build_overview(run, stored)
    before = A.export_text(A.build_export(run, stored, overview))
    monkeypatch.setattr(A, "st", _Page({"reviewer_name": "Sri"}))
    A.note_export("RUN-CLICK-4", "x.json", 3, len(before))
    assert A.export_text(A.build_export(run, stored, overview)) == before


@pytest.mark.parametrize("failure", [sqlite3.OperationalError("database is locked"), OSError("disk"), ValueError("refused")])
def test_a_problem_writing_the_audit_row_never_stops_the_click_and_is_remembered(monkeypatch, failure):
    store_run(*three(), run_id="RUN-CLICK-5")
    page = _Page({"reviewer_name": "Sri"})
    monkeypatch.setattr(A, "st", page)

    def broken(*_a, **_k):
        raise failure

    monkeypatch.setattr(A.audit, "log_event", broken)
    A.note_export("RUN-CLICK-5", "x.json", 3, 10)
    assert page.session_state[A.EXPORTED_KEY] == ("RUN-CLICK-5", "x.json", False)
    assert audit_rows("RUN-CLICK-5") == []


def test_a_click_for_a_run_that_is_not_saved_is_reported_not_raised(monkeypatch):
    page = _Page({"reviewer_name": "Sri"})
    monkeypatch.setattr(A, "st", page)
    A.note_export("RUN-NOT-SAVED", "x.json", 0, 0)
    assert page.session_state[A.EXPORTED_KEY] == ("RUN-NOT-SAVED", "x.json", False)


# ---------------------------------------------------------------- the real app (needs Streamlit's AppTest)

def start() -> Any:
    app = AppTest.from_file(str(APP_PATH), default_timeout=60)
    app.run()
    return app


def audit_tab(app):
    return app.tabs[7]


def shown(block) -> str:
    return "\n".join(str(item.value) for kind in ("markdown", "caption", "info", "error", "warning", "success") for item in getattr(block, kind))


def open_run(*suppliers: dict[str, Any], run_id: str = "RUN-TEST-AUDIT", **options) -> Any:
    store_run(*suppliers, run_id=run_id, **options)
    app = start()
    app.session_state[tab_new_evaluation.SHOWN_RUN_KEY] = run_id
    app.run()
    return app


@needs_app_test
def test_before_any_run_the_audit_tab_explains_what_to_do():
    app = start()
    assert not app.exception
    text = shown(audit_tab(app))
    assert "No evaluation yet" in text and "JSON download" in text
    assert not audit_tab(app).get("download_button")


@needs_app_test
def test_a_saved_run_shows_its_details_and_one_download_button():
    app = open_run(supplier("Apex Systems", 1, warnings=["Criterion 2 was missing"]), supplier("NexaWorks", 2, tie_break="NexaWorks above Orbit: earlier submission"))
    assert not app.exception
    tab = audit_tab(app)
    text = shown(tab)
    for expected in ("Run details", "Run ID (RFP_RUN_ID)", "RUN-TEST-AUDIT", "Startup laptops", "Rs 60 lakh for 100 laptops", "Warnings",
                     "1 warning in this run.", "Criterion 2 was missing", "Tie-breaks", "NexaWorks above Orbit: earlier submission", A.JSON_NOTE):
        assert expected in text, expected
    assert len(tab.get("download_button")) == 1 and not tab.get("plotly_chart")


@needs_app_test
def test_the_other_tabs_are_not_changed_by_the_audit_tab():
    app = open_run(*three())
    assert not app.exception
    assert "Run details" not in shown(app.tabs[2]) and "Run details" not in shown(app.tabs[3])
    assert len(app.tabs) == 8
