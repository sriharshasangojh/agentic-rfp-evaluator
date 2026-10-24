"""F16: what a run does with the AI's wording - saved with the result, shown on the Overview and in the JSON file, and what happens when
the AI misbehaves. The run never stops because of the wording, and no point is ever added, removed or re-judged by it."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agents import orchestrator
from db import repository, seed
from llm import client
from review_support import (APEX, BRIGHT, FIVE, GOOD_FACTS, NEXA, REASON, SETTINGS, START, TERMS, FakeBackend, evaluated_run, pdf_for, reply,
                            rows, saved)
from tools import insights
from ui import tab_audit, tab_overview


@pytest.fixture(autouse=True)
def seeded_database():
    seed.ensure_database()


def text_response(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=SimpleNamespace(total_tokens=400))


class Worded(FakeBackend):
    """A model that rewrites every point, adding a marker, so the tests can see exactly what came from the AI."""

    def __init__(self, *outcomes, mutate=None, **kw):
        super().__init__(*outcomes, **kw)
        self.mutate = mutate

    def summary_reply(self, kwargs):
        body = json.loads(kwargs["messages"][1]["content"])
        out = {"pros": [f"AI: {i['point']} (Page 1)" for i in body["pros"]], "cons": [f"AI: {i['point']} (Page 2)" for i in body["cons"]],
               "reason": "AI reason."}
        if self.mutate:
            out = self.mutate(out, body, kwargs)
        if isinstance(out, BaseException):
            raise out
        return text_response(out if isinstance(out, str) else json.dumps(out))


def run(backend_cls=Worded, rows_=FIVE, **kw):
    proposals = [orchestrator.Proposal(f"{n.lower().replace(' ', '_')}.pdf", n, when, rating, pdf_for(n)) for n, when, rating, _s in rows_]
    facts = {"BrightPath Tech": {"warranty_years": 1, "warranty_type": "carry-in", "bis_named": None, "price_table_present": True, "delivery_weeks": 6}}
    backend = backend_cls(*[reply(scores, facts.get(n, GOOD_FACTS)) for n, _w, _r, scores in rows_], **kw)
    result = orchestrator.run_evaluation(proposals, TERMS, SETTINGS, "Sri", backend=backend, sleep=lambda _s: None, now=lambda: START)
    assert result.status == "Evaluated", result.message
    return result, backend


def wording_of(run_id, name):
    return saved(run_id)[name].result["wording"]


def overview_of(run_id):
    with repository.connection() as conn:
        run_record = repository.get_run(conn, run_id)
        stored = repository.list_supplier_results(conn, run_id)
    return run_record, stored, tab_overview.build_overview(run_record, stored)


# ---------------------------------------------------------------- the run

def test_every_supplier_gets_one_wording_call_with_only_its_own_points():
    result, backend = run()
    assert len(backend.calls) == 5 and len(backend.summary_calls) == 5
    for call in backend.summary_calls:
        sent = call["messages"][1]["content"]
        assert not any(name in sent for name in ("Apex", "NexaWorks", "Horizon", "BrightPath", "Orbit"))   # no supplier name goes out at all
        assert call["temperature"] == 0.4 and call["messages"][0]["content"].startswith("You write short, plain-language points")


def test_the_ai_words_are_saved_and_shown_but_the_points_are_pythons():
    result, _ = run()
    plain = evaluated_run()
    stored, plain_stored = saved(result.run_id)[APEX].result, saved(plain.run_id)[APEX].result
    assert stored["pros"] and all(p.startswith("AI: ") for p in stored["pros"])
    assert [p.removeprefix("AI: ").removesuffix(" (Page 1)") for p in stored["pros"]] == plain_stored["pros"]
    assert [c.removeprefix("AI: ").removesuffix(" (Page 2)") for c in stored["cons"]] == plain_stored["cons"]


def test_the_overview_shows_the_ai_words_and_the_banner_shows_the_reason():
    result, _ = run()
    _run, _stored, overview = overview_of(result.run_id)
    apex = next(r for r in overview.rows if r.name == APEX)
    assert all(r.text.startswith("AI: ") for r in apex.pros + apex.cons)
    assert overview.verdict.reason == "AI reason." and overview.recommended.name == APEX
    banner = tab_overview.verdict_html(overview.verdict)
    assert "Why:" in banner and "AI reason." in banner


def test_the_banner_has_no_why_line_without_a_reason():
    result, _ = run(mutate=lambda out, body, kw: {**out, "reason": ""})
    _r, _s, overview = overview_of(result.run_id)
    assert overview.verdict.reason == "" and "Why:" not in tab_overview.verdict_html(overview.verdict)


def test_the_json_file_carries_the_ai_words_and_the_reason():
    result, _ = run()
    run_record, stored, overview = overview_of(result.run_id)
    export = tab_audit.build_export(run_record, stored, overview)
    apex = next(s for s in export["suppliers"] if s["supplier_name"] == APEX)
    assert apex["pros"] and all(p.startswith("AI: ") for p in apex["pros"])
    assert export["recommendation"]["reason"] == "AI reason."


def test_without_a_reason_the_json_file_keeps_the_banner_sentence():
    result, _ = run(mutate=lambda out, body, kw: {**out, "reason": ""})
    run_record, stored, overview = overview_of(result.run_id)
    assert tab_audit.build_export(run_record, stored, overview)["recommendation"]["reason"] == overview.verdict.sentence


def test_tokens_cost_and_an_audit_row_are_added_for_each_wording_call():
    plain = evaluated_run()
    result, _ = run()
    with repository.connection() as conn:
        a, b = repository.get_run(conn, plain.run_id), repository.get_run(conn, result.run_id)
    assert b.total_tokens == a.total_tokens + 5 * 400
    events = [json.loads(r["details_json"]) for r in rows("SELECT details_json FROM audit_log WHERE rfp_run_id = ? AND action = 'points_worded'", result.run_id)]
    assert len(events) == 5 and {e["supplier"] for e in events} == {n for n, *_ in FIVE}
    assert all(e["tokens"] == 400 and e["model"] == "test-model" and e["reason"] is True and e["kept_plain"] == 0 for e in events)


def test_a_wrong_number_of_lines_falls_back_to_the_template_and_the_run_goes_on():
    result, _ = run(mutate=lambda out, body, kw: {"pros": ["only one"], "cons": [], "reason": "r"})
    plain = evaluated_run()
    assert saved(result.run_id)[APEX].result["pros"] == saved(plain.run_id)[APEX].result["pros"]
    assert saved(result.run_id)[APEX].result["wording"] is None
    failed = [json.loads(r["details_json"]) for r in rows("SELECT details_json FROM audit_log WHERE rfp_run_id = ? AND action = 'wording_failed'", result.run_id)]
    assert len(failed) == 5 and all(f["kind"] == "unusable_reply" and f["tokens"] == 400 for f in failed)
    assert any("could not be used" in line for line in result.steps)


def test_an_unusable_reply_still_counts_its_tokens():
    plain = evaluated_run()
    result, _ = run(mutate=lambda out, body, kw: "I cannot do that")
    with repository.connection() as conn:
        assert repository.get_run(conn, result.run_id).total_tokens == repository.get_run(conn, plain.run_id).total_tokens + 2000


def test_a_provider_error_during_wording_does_not_stop_the_run():
    plain = evaluated_run()
    result, _ = run(mutate=lambda out, body, kw: Exception("503 Service Unavailable"))
    assert result.status == "Evaluated"
    assert saved(result.run_id)[APEX].result["pros"] == saved(plain.run_id)[APEX].result["pros"]
    assert any("Could not word the points" in line for line in result.steps)
    assert len(rows("SELECT id FROM audit_log WHERE rfp_run_id = ? AND action = 'wording_failed'", result.run_id)) == 5
    assert [(s.rank, s.name) for s in result.suppliers] == [(s.rank, s.name) for s in plain.suppliers]


def test_a_line_that_names_another_supplier_is_replaced_by_the_template_line():
    def mutate(out, body, kw):
        out["pros"][0] = "Better than NexaWorks on specs"
        return out
    result, _ = run(mutate=mutate)
    pros = saved(result.run_id)[APEX].result["pros"]
    assert not pros[0].startswith("AI: ") and "NexaWorks" not in " ".join(pros) and pros[1].startswith("AI: ")
    event = json.loads(rows("SELECT details_json FROM audit_log WHERE rfp_run_id = ? AND action = 'points_worded' ORDER BY id", result.run_id)[0]["details_json"])
    assert event["kept_plain"] >= 1


def test_the_ai_cannot_add_a_point():
    def mutate(out, body, kw):
        out["pros"] = out["pros"] + ["An extra invented strength"]
        return out
    result, _ = run(mutate=mutate)
    assert all("invented" not in p for p in saved(result.run_id)[APEX].result["pros"])
    assert saved(result.run_id)[APEX].result["wording"] is None


def test_a_failed_evaluation_is_not_worded():
    proposals = [orchestrator.Proposal(f"{n.lower().replace(' ', '_')}.pdf", n, when, rating, pdf_for(n)) for n, when, rating, _s in FIVE[:2]]
    backend = Worded("not json", "not json", reply(FIVE[1][3], GOOD_FACTS))
    result = orchestrator.run_evaluation(proposals, TERMS, SETTINGS, "Sri", backend=backend, sleep=lambda _s: None, now=lambda: START)
    results = saved(result.run_id)
    failed = [s for s in results.values() if s.result["evaluation_failed"]]
    assert failed and all(s.result["wording"] is None and s.result["pros"] == [] and s.result["cons"] == [] for s in failed)
    assert len(backend.summary_calls) == len(results) - len(failed)


def test_no_call_is_made_for_a_supplier_with_nothing_to_word():
    flat = [(n, w, r, [5, 5, 5, 5, 5]) for n, w, r, _s in FIVE[:2]]
    result, backend = run(rows_=flat)
    nothing = [s for s in saved(result.run_id).values() if not s.result["pros"] and not s.result["cons"]]
    assert len(backend.summary_calls) == 2 - len(nothing)
    assert all(s.result["wording"] is None for s in nothing)


# ---------------------------------------------------------------- after a re-rank

def test_a_re_rank_keeps_the_words_of_points_that_are_still_chosen_and_hides_a_reason_that_no_longer_fits():
    result, _ = run()
    with repository.connection() as conn:
        run_record = repository.get_run(conn, result.run_id)
        stored = repository.list_supplier_results(conn, result.run_id)
    best = next(s for s in stored if s.supplier_name == APEX)
    fitted = tab_overview.build_overview(run_record, stored)
    assert fitted.verdict.reason == "AI reason."
    changed = {**best.result, "wording": {**best.result["wording"], "reason_for": ["criterion:99"]}}
    stored = [repository.StoredSupplier(**{**s.__dict__, "result": changed}) if s.supplier_name == APEX else s for s in stored]
    overview = tab_overview.build_overview(run_record, stored)
    assert overview.verdict.reason == ""                                                      # the sentence was written from other pros
    assert all(r.text.startswith("AI: ") for r in next(x for x in overview.rows if x.name == APEX).pros)   # the points keep their words


def test_a_point_that_is_chosen_only_after_a_re_rank_shows_the_template_text():
    chosen = insights.ProsCons((insights.Reason("Strong on Delivery (9/10, best of all bids)", "vs this batch", "criterion", "criterion:9"),), ())
    wording = {"pros": {"criterion:1": "AI: something else"}, "cons": {}}
    assert insights.apply_wording(chosen, wording).pros[0].text == "Strong on Delivery (9/10, best of all bids)"


# ---------------------------------------------------------------- the plain path

def test_runs_saved_before_this_feature_still_open():
    plain = evaluated_run()
    stored = saved(plain.run_id)[APEX].result
    stored.pop("wording")
    with repository.connection() as conn:
        run_record = repository.get_run(conn, plain.run_id)
        everyone = repository.list_supplier_results(conn, plain.run_id)
    older = [repository.StoredSupplier(**{**s.__dict__, "result": {k: v for k, v in s.result.items() if k != "wording"}}) for s in everyone]
    overview = tab_overview.build_overview(run_record, older)
    assert overview.verdict.reason == "" and next(r for r in overview.rows if r.name == APEX).pros


def test_the_default_test_model_gives_the_template_wording_and_a_reason():
    plain = evaluated_run()
    assert wording_of(plain.run_id, APEX)["reason"] == REASON
    assert saved(plain.run_id)[APEX].result["pros"][0] == "Strong on Laptop specs (9/10, best of all bids)"


def test_a_key_in_the_wording_call_never_reaches_the_database():
    result, backend = run()
    dump = json.dumps([s.result for s in saved(result.run_id).values()]) + "".join(r["details_json"] for r in rows("SELECT details_json FROM audit_log"))
    assert SETTINGS.api_key not in dump
    assert SETTINGS.api_key not in json.dumps([c["messages"] for c in backend.summary_calls])
    assert BRIGHT and NEXA


# ---------------------------------------------------------------- what the AI is shown

def sent_points(backend, index=0):
    return json.loads(backend.summary_calls[index]["messages"][1]["content"])


def test_each_point_is_sent_with_its_justification_and_evidence_the_right_way_round():
    _result, backend = run()
    items = [i for call in range(5) for side in ("pros", "cons") for i in sent_points(backend, call)[side] if "justification" in i]
    assert items and all(i["justification"].startswith("why ") and i["evidence"] == "We offer laptops. (Page 1)" for i in items)
    gate_item = [i for call in range(5) for i in sent_points(backend, call)["pros"] if i["point"] == "Meets every must-have"]
    assert gate_item and all("justification" not in i and i["label"] == "vs requirement" for i in gate_item)


def test_a_quote_that_was_not_found_is_one_of_the_points_the_ai_is_asked_to_word():
    from review_support import QUOTE
    import json as _json
    proposals = [orchestrator.Proposal(f"{n.lower().replace(' ', '_')}.pdf", n, when, rating, pdf_for(n)) for n, when, rating, _s in FIVE[:2]]
    replies = []
    for n, _w, _r, scores in FIVE[:2]:
        body = _json.loads(reply(scores, GOOD_FACTS))
        if n == APEX:
            body["criteria"][1]["evidence"] = "A sentence the proposal does not contain at all. (Page 1)"
        replies.append(_json.dumps(body))
    backend = Worded(*replies)
    orchestrator.run_evaluation(proposals, TERMS, SETTINGS, "Sri", backend=backend, sleep=lambda _s: None, now=lambda: START)
    points = [i["point"] for call in range(len(backend.summary_calls)) for i in sent_points(backend, call)["cons"]]
    assert any("was not found in the proposal" in p for p in points)
    assert QUOTE


def test_a_criterion_the_ai_left_out_is_sent_as_having_no_result():
    import json as _json
    proposals = [orchestrator.Proposal(f"{n.lower().replace(' ', '_')}.pdf", n, when, rating, pdf_for(n)) for n, when, rating, _s in FIVE[:2]]
    replies = []
    for n, _w, _r, scores in FIVE[:2]:
        body = _json.loads(reply(scores, GOOD_FACTS))
        if n == APEX:
            body["criteria"] = body["criteria"][:4]
        replies.append(_json.dumps(body))
    backend = Worded(*replies)
    orchestrator.run_evaluation(proposals, TERMS, SETTINGS, "Sri", backend=backend, sleep=lambda _s: None, now=lambda: START)
    points = [i["point"] for call in range(len(backend.summary_calls)) for i in sent_points(backend, call)["cons"]]
    assert any(p.startswith("No result for ") for p in points)


def test_a_supplier_with_nothing_chosen_is_not_asked_about(monkeypatch):
    monkeypatch.setattr(orchestrator.insights, "select_pros_cons", lambda criteria, gates: insights.ProsCons((), ()))
    result, backend = run()
    assert backend.summary_calls == [] and all(s.result["wording"] is None for s in saved(result.run_id).values())
    assert not rows("SELECT id FROM audit_log WHERE rfp_run_id = ? AND action IN ('points_worded', 'wording_failed')", result.run_id)


def test_a_line_may_name_the_supplier_it_is_about():
    def mutate(out, body, kw):
        out["pros"][0] = "Apex Systems leads on specs (Page 2)"
        return out
    result, _ = run(mutate=mutate)
    assert saved(result.run_id)[APEX].result["pros"][0] == "Apex Systems leads on specs (Page 2)"
