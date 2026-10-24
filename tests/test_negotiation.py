"""F20: negotiation suggestions. The selection (spec 10.4) is plain Python; the AI only words the chosen points and its lines are
checked; the points are saved with the run; no other supplier's name or price ever reaches the AI. No network, no model."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agents import negotiator, orchestrator
from db import repository, seed
from llm import client
from review_support import APEX, BRIGHT, GOOD_FACTS, HORIZON, KEY, NAMES, NEXA, SETTINGS, evaluated_run, rows
from tools import insights
from tools.insights import (CriterionResult, GateOutcome, NegotiationPoint, format_lakh, negotiation_points)

OK = GateOutcome("warranty", "Warranty", True, "3 years")


def crit(cid, name, score, gap=0.0, weight=20.0, max_score=10.0, **kw):
    return CriterionResult(cid, name, score, max_score, weight, score - gap, gap, 0.0, **kw)


def keys(points):
    return [p.key for p in points]


# ---------------------------------------------------------------- the selection

def test_nothing_to_negotiate_gives_the_startup_levers_only():
    got = negotiation_points([OK], 5000000, 6000000, [crit(1, "Technical Capability", 9)], [])
    assert keys(got) == ["startup:1", "startup:2", "startup:3", "startup:4"]


def test_the_order_is_gates_budget_gaps_risks_proof_then_startup_and_at_most_five():
    gates = [GateOutcome("warranty", "Warranty", False, "1 year"), GateOutcome("certification", "Certification", False, "none")]
    criteria = [crit(1, "Technical Capability", 6, -3, 30), crit(3, "Commercial Value", 7, -2, 20, evidence_found=False)]
    got = negotiation_points(gates, 6500000, 6000000, criteria, ["Late delivery"])
    assert keys(got) == ["gate:warranty", "gate:certification", "budget", "criterion:1", "criterion:3"]
    assert len(got) == insights.NEGOTIATION_MAX


def test_each_failed_requirement_has_its_own_ask():
    gates = [GateOutcome("warranty", "Warranty", False, "x"), GateOutcome("certification", "Certification", False, "x"),
             GateOutcome("delivery", "Delivery", False, "x"), GateOutcome("other", "Price table", False, "x")]
    got = negotiation_points(gates, None, None, [], [])
    assert "at least 3 years" in got[0].text and "BIS certificate" in got[1].text and "8 weeks" in got[2].text and "penalty" in got[2].text
    assert got[3].text == "Ask the supplier to resolve this before signing: Price table."


def test_a_passed_gate_gives_no_point():
    assert not [p for p in negotiation_points([OK], None, None, [], []) if p.topic == insights.TOPIC_GATE]


def test_over_budget_names_the_overrun_and_the_budget_of_the_run():
    (budget, *_), = [negotiation_points([], 6500000, 6000000, [], [])]
    assert budget.key == "budget" and "Rs 65 lakh" in budget.text and "Rs 5 lakh over our Rs 60 lakh budget" in budget.text
    other = negotiation_points([], 5400000, 5000000, [], [])[0]
    assert "over our Rs 50 lakh budget" in other.text and "Rs 4 lakh" in other.text


@pytest.mark.parametrize("total, budget", [(6000000, 6000000), (5000000, 6000000), (None, 6000000), (6500000, None), (0, 6000000)])
def test_within_budget_or_unknown_gives_no_budget_point(total, budget):
    assert "budget" not in keys(negotiation_points([], total, budget, [], []))


def test_criteria_two_behind_or_not_strong_are_raised_by_weight_times_gap():
    criteria = [crit(1, "Technical Capability", 9, 0, 30), crit(2, "Implementation Plan", 7, -2, 20),
                crit(4, "Security & Compliance", 9, -2, 10), crit(5, "Support & Experience", 6, -3, 10)]
    got = negotiation_points([], None, None, criteria, [])
    assert keys(got)[:3] == ["criterion:2", "criterion:5", "criterion:4"]           # 40, 30, 20
    assert "criterion:1" not in keys(got)
    assert "named project manager" in got[0].text and "response-time" in got[1].text and "data-wipe" in got[2].text


def test_a_strong_criterion_close_to_the_best_is_left_alone_and_a_weak_one_is_raised_even_without_a_gap():
    got = negotiation_points([], None, None, [crit(1, "Technical Capability", 9, -1), crit(2, "Implementation Plan", 4, 0)], [])
    assert keys(got)[0] == "criterion:2" and "criterion:1" not in keys(got)


def test_a_criterion_the_ai_did_not_mark_is_not_raised():
    got = negotiation_points([], None, None, [crit(1, "Technical Capability", 0, -9, filled_by_validator=True)], [])
    assert "criterion:1" not in keys(got)


def test_an_unknown_criterion_gets_a_general_ask():
    got = negotiation_points([], None, None, [crit(9, "Sustainability", 5, -3)], [])
    assert got[0].text == "On Sustainability: ask what the supplier can improve on Sustainability."


def test_risks_and_missing_proof_follow_the_gaps():
    got = negotiation_points([], None, None, [crit(1, "Technical Capability", 9, evidence_found=False)], ["  Stock may run  short "])
    assert keys(got)[:2] == ["risk:1", "evidence:1"]
    assert got[0].text == "Add a contract clause that covers this risk: Stock may run short"
    assert "written proof" in got[1].text


def test_the_price_criterions_own_reasoning_is_never_passed_on():
    """Its justification can quote the lowest total of the batch, which is another supplier's price."""
    got = negotiation_points([], None, None, [crit(3, "Commercial Value", 6, -4)], [], details={3: "the lowest total is Rs 50 lakh"})
    assert got[0].key == "criterion:3" and got[0].detail == ""


def test_other_criteria_carry_the_suppliers_own_words():
    got = negotiation_points([], None, None, [crit(1, "Technical Capability", 6, -4)], [], details={1: "16 GB RAM only"})
    assert got[0].detail == "16 GB RAM only"


# ---------------------------------------------------------------- checking the AI's lines

POINTS = (NegotiationPoint("gate:warranty", "gate", "Ask for 3 years."), NegotiationPoint("budget", "budget", "Ask for a discount."))


def lines(*text):
    return json.dumps({"points": list(text)})


def test_good_lines_are_kept_by_key():
    got = negotiator.check_reply(lines("Could you extend the warranty to three years?", "Could you bring the total within our budget?"),
                                 POINTS, ["Zeta Corp"])
    assert got.lines == {"gate:warranty": "Could you extend the warranty to three years?",
                         "budget": "Could you bring the total within our budget?"} and got.kept_plain == 0


@pytest.mark.parametrize("bad", [
    "", "   ", 7, None, "word " * 40,
    "Zeta Corp offered a better price.", "Please match another bid.", "Other suppliers charge less.", "A competitor is cheaper.",
    "x" * 300, "Please beat the competing offer.", "Rival quotes are lower."])
def test_a_bad_line_is_replaced_by_the_plain_text_and_the_rest_are_kept(bad):
    good = "Could you bring the total within our budget?"
    got = negotiator.check_reply(json.dumps({"points": [bad, good]}), POINTS, ["Zeta Corp"])
    assert got.lines == {"budget": good} and got.kept_plain == 1


@pytest.mark.parametrize("text", ["not json", "[1]", json.dumps({"points": ["one"]}), json.dumps({"points": "x"}), json.dumps({})])
def test_a_reply_that_is_not_usable_as_a_whole_is_refused(text):
    with pytest.raises(negotiator.NegotiatorError):
        negotiator.check_reply(text, POINTS, [])


def test_the_call_carries_only_the_chosen_points_the_priorities_and_the_suppliers_own_words():
    messages = negotiator.build_messages([NegotiationPoint("k", "criterion", "On X: ask.", "its own words")], "PROMPT", ["Price"], ["Strong on X"])
    assert messages[0] == {"role": "system", "content": "PROMPT"}
    assert json.loads(messages[1]["content"]) == {"buyer_priorities": ["Price"], "supplier_strengths": ["Strong on X"],
                                                  "points": [{"topic": "criterion", "point": "On X: ask.", "supplier_evidence": "its own words"}]}


def test_the_fit_note_is_kept_only_when_it_passes_the_checks():
    ok = json.dumps({"points": ["a", "b"], "fit": "A good match for the price you asked for."})
    assert negotiator.check_reply(ok, POINTS, ["Zeta Corp"]).fit == "A good match for the price you asked for."
    for bad in ("", "word " * 60, "Zeta Corp is cheaper.", "Beats every competitor.", "x" * 400, 4, None):
        assert negotiator.check_reply(json.dumps({"points": ["a", "b"], "fit": bad}), POINTS, ["Zeta Corp"]).fit is None
    assert negotiator.check_reply(json.dumps({"points": ["a", "b"]}), POINTS, []).fit is None


# ---------------------------------------------------------------- a real run

@pytest.fixture(autouse=True)
def seeded_database():
    seed.ensure_database()


class Words:
    """A model that words every point it is sent, in order (or says what the test tells it to)."""

    def __init__(self, make=None, error=None, fit=lambda body: "Fits what you asked for: " + ", ".join(body["buyer_priorities"]) + "."):
        self.make, self.error, self.fit, self.sent = make, error, fit, []

    def supports_json_mode(self, settings):
        return True

    def cost(self, response):
        return 0.01

    def completion(self, **kwargs):
        self.sent.append(kwargs)
        if self.error:
            raise self.error
        body = json.loads(kwargs["messages"][1]["content"])
        items = body["points"]
        out = self.make(items) if self.make else [f"Please consider: {i['point']}" for i in items]
        reply = {"points": out, **({"fit": self.fit(body)} if self.fit else {})}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(reply)))],
                               usage=SimpleNamespace(total_tokens=300))


def use_up_the_rounds(run_id: str) -> str:
    """The person was not satisfied three times: every round of changing the priorities is used (the only time negotiation opens)."""
    for round_no, criterion in enumerate((1, 2, 3), start=1):
        assert orchestrator.change_priorities(run_id, 2, criterion, round_no).ok
    return run_id


def final_of(run_id: str) -> str:
    """The supplier Python recommends now (the best-ranked one that meets every must-have)."""
    from tools import review
    with repository.connection() as conn:
        stored = {s.supplier_name: s for s in repository.list_supplier_results(conn, run_id)}
    return review.recommended_of(orchestrator._board(stored))


def run_with_gaps(rounds: bool = True):
    """Five suppliers: BrightPath fails its must-haves; the others are all Rs 65 lakh, over the Rs 60 lakh budget."""
    facts = {BRIGHT: {"warranty_years": 1, "warranty_type": "carry-in", "bis_named": None, "price_table_present": True, "delivery_weeks": 6,
                      "grand_total_rs": 6500000}}
    facts.update({n: {**GOOD_FACTS, "grand_total_rs": 6500000} for n in NAMES if n != BRIGHT})
    result = evaluated_run(facts=facts)
    if rounds:
        use_up_the_rounds(result.run_id)
    return result


def test_the_points_come_from_the_final_suppliers_own_saved_result_and_the_runs_budget():
    result = run_with_gaps()
    final = final_of(result.run_id)
    out = orchestrator.suggest_negotiation(result.run_id, final)
    assert out.ok and out.used_ai is False and out.supplier == final
    texts = [ln.text for ln in out.lines]
    assert 0 < len(out.lines) <= 5 and "Rs 5 lakh over our Rs 60 lakh budget" in texts[0]
    assert all(not ln.worded and ln.text == ln.plain for ln in out.lines)


def test_the_final_pick_is_python_s_and_has_to_meet_every_must_have():
    result = run_with_gaps()
    final = final_of(result.run_id)
    assert final != BRIGHT
    out = orchestrator.suggest_negotiation(result.run_id, BRIGHT)
    assert not out.ok and final in out.message and out.lines == ()
    other = next(n for n in NAMES if n not in (final, BRIGHT))
    assert not orchestrator.suggest_negotiation(result.run_id, other).ok


def test_the_pick_follows_the_rounds_the_person_used():
    """Different choices in the rounds can end with a different final supplier; the points are always for the one Python picks."""
    a = run_with_gaps(rounds=False)
    for round_no, criterion in enumerate((1, 1, 1), start=1):
        orchestrator.change_priorities(a.run_id, 2, criterion, round_no)
    b = run_with_gaps(rounds=False)
    for round_no, criterion in enumerate((3, 3, 3), start=1):
        orchestrator.change_priorities(b.run_id, 2, criterion, round_no)
    for run in (a, b):
        out = orchestrator.suggest_negotiation(run.run_id, final_of(run.run_id))
        assert out.ok and out.supplier == final_of(run.run_id)


def test_the_plain_fit_note_says_what_the_person_asked_for_and_that_python_picked_by_rank():
    result = run_with_gaps()
    final = final_of(result.run_id)
    out = orchestrator.suggest_negotiation(result.run_id, final)
    assert out.fit is not None and not out.fit.worded and out.fit.text == out.fit.plain
    assert out.fit.priorities == ("Technical Capability", "Implementation Plan", "Commercial Value")
    assert out.fit.plain.startswith("You asked for more weight on Technical Capability, Implementation Plan and Commercial Value. ")
    assert f"{final} ranks first among the suppliers that meet every must-have with a score of " in out.fit.plain
    assert out.fit.plain.endswith("so it is the best fit for what you asked for.")


def test_a_criterion_asked_for_twice_is_named_once():
    from agents.orchestrator import fit_plain
    assert fit_plain("X", ["A"], None).startswith("You asked for more weight on A. X ranks first")
    assert "score of" not in fit_plain("X", ["A", "B"], None) and fit_plain("X", [], 3.5).startswith("X ranks first")
    result = run_with_gaps(rounds=False)
    for round_no in (1, 2, 3):
        orchestrator.change_priorities(result.run_id, 2, 3, round_no)
    out = orchestrator.suggest_negotiation(result.run_id, final_of(result.run_id))
    assert out.fit is not None and out.fit.priorities == ("Commercial Value",)


def test_nothing_is_changed_in_the_scores_or_the_ranking():
    result = run_with_gaps()
    final = final_of(result.run_id)

    def snapshot():
        with repository.connection() as conn:
            return [(s.supplier_name, s.final_rank, s.ppi, s.result) for s in repository.list_supplier_results(conn, result.run_id)]
    before = snapshot()
    orchestrator.suggest_negotiation(result.run_id, final, SETTINGS, backend=Words())
    assert before == snapshot()


def test_the_points_and_the_fit_note_are_saved_and_audited_each_time():
    result = run_with_gaps()
    final = final_of(result.run_id)
    orchestrator.suggest_negotiation(result.run_id, final, actor="Asha")
    orchestrator.suggest_negotiation(result.run_id, final)
    with repository.connection() as conn:
        saved = repository.list_negotiations(conn, result.run_id)
    assert [r.supplier_name for r in saved] == [final, final]
    assert set(saved[0].points[0]) == {"key", "topic", "text", "plain", "worded"}
    assert set(saved[0].fit) == {"text", "plain", "worded", "priorities"} and saved[0].fit["priorities"][0] == "Technical Capability"
    events = rows("SELECT actor, details_json FROM audit_log WHERE rfp_run_id = ? AND action = 'negotiation_suggested'", result.run_id)
    detail = json.loads(events[0]["details_json"])
    assert [e["actor"] for e in events] == ["Asha", "reviewer"] and detail["asked_ai"] is False and detail["supplier"] == final
    assert detail["priorities"][0] == "Technical Capability" and detail["fit_worded"] is False


def test_saved_sets_cannot_be_changed_or_removed():
    import sqlite3
    result = run_with_gaps()
    orchestrator.suggest_negotiation(result.run_id, final_of(result.run_id))
    with repository.connection() as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE negotiation_points SET supplier_name = 'x'")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM negotiation_points")


def test_with_a_model_the_lines_and_the_fit_note_are_the_ais_and_the_cost_joins_the_run_totals():
    result = run_with_gaps()
    backend = Words()
    with repository.connection() as conn:
        before = repository.get_run(conn, result.run_id).total_tokens
    out = orchestrator.suggest_negotiation(result.run_id, final_of(result.run_id), SETTINGS, backend=backend)
    assert out.ok and out.used_ai and all(ln.worded and ln.text.startswith("Please consider: ") for ln in out.lines)
    assert out.fit is not None and out.fit.worded and out.fit.text == "Fits what you asked for: Technical Capability, Implementation Plan, Commercial Value."
    assert out.fit.plain != out.fit.text
    with repository.connection() as conn:
        assert repository.get_run(conn, result.run_id).total_tokens == before + 300
        record = repository.list_negotiations(conn, result.run_id)[0]
    assert record.points[0]["worded"] is True and record.fit["worded"] is True


def test_the_model_is_told_what_was_asked_for_and_the_suppliers_strengths_but_not_who_picked_it():
    result = run_with_gaps()
    backend = Words()
    orchestrator.suggest_negotiation(result.run_id, final_of(result.run_id), SETTINGS, backend=backend)
    body = json.loads(backend.sent[0]["messages"][1]["content"])
    assert body["buyer_priorities"] == ["Technical Capability", "Implementation Plan", "Commercial Value"]
    assert 0 < len(body["supplier_strengths"]) <= 3 and set(body) == {"buyer_priorities", "supplier_strengths", "points"}


def test_no_other_supplier_name_or_price_is_sent_to_the_model():
    result = run_with_gaps()
    final = final_of(result.run_id)
    backend = Words()
    orchestrator.suggest_negotiation(result.run_id, final, SETTINGS, backend=backend)
    sent = json.dumps(backend.sent[0]["messages"])
    with repository.connection() as conn:
        others = [s for s in repository.list_supplier_results(conn, result.run_id) if s.supplier_name != final]
    for s in others:
        assert s.supplier_name not in sent and s.supplier_name.split()[0] not in sent
    assert "lowest total" not in sent and "benchmark" not in sent and "Price mark worked out" not in sent


def test_the_price_reasoning_of_a_run_with_worked_out_marks_is_not_sent():
    rows_ = [r for r in __import__("review_support").FIVE if r[0] in (APEX, NEXA, HORIZON)]
    result = evaluated_run(rows_, facts={n: {**GOOD_FACTS, "grand_total_rs": t} for n, t in
                                         ((APEX, 5900000), (NEXA, 5000000), (HORIZON, 5900000))})
    use_up_the_rounds(result.run_id)
    backend = Words()
    out = orchestrator.suggest_negotiation(result.run_id, final_of(result.run_id), SETTINGS, backend=backend)
    assert out.ok
    sent = json.dumps(backend.sent[0]["messages"])
    assert "Price mark worked out" not in sent and "50.00 lakh" not in sent and "Rs 50 lakh" not in sent


def test_lines_and_a_fit_note_that_name_another_supplier_or_bid_are_replaced_by_the_plain_text():
    result = run_with_gaps()
    final = final_of(result.run_id)
    other = next(n for n in NAMES if n != final)
    out = orchestrator.suggest_negotiation(result.run_id, final, SETTINGS, backend=Words(
        lambda items: [f"{other} is cheaper, so {i['point']}" for i in items], fit=lambda body: f"Better than {other}."))
    assert out.ok and all(not ln.worded and ln.text == ln.plain and other not in ln.text for ln in out.lines)
    assert out.fit is not None and not out.fit.worded and out.fit.text == out.fit.plain
    out = orchestrator.suggest_negotiation(result.run_id, final, SETTINGS, backend=Words(fit=lambda body: "Cheaper than every other bid we saw."))
    assert out.fit is not None and not out.fit.worded


@pytest.mark.parametrize("fit", ["", "word " * 60, "x" * 400, 5, None])
def test_an_unusable_fit_note_falls_back_to_the_plain_one(fit):
    result = run_with_gaps()
    out = orchestrator.suggest_negotiation(result.run_id, final_of(result.run_id), SETTINGS, backend=Words(fit=lambda body: fit))
    assert out.ok and out.fit is not None and not out.fit.worded and out.fit.text == out.fit.plain


def test_a_provider_problem_or_an_unusable_reply_falls_back_to_the_plain_text():
    result = run_with_gaps()
    final = final_of(result.run_id)
    out = orchestrator.suggest_negotiation(result.run_id, final, SETTINGS, backend=Words(make=lambda items: ["only one"]))
    assert out.ok and not out.used_ai and "plain wording" in out.message and all(ln.text == ln.plain for ln in out.lines)
    assert out.fit is not None and out.fit.text == out.fit.plain
    down = orchestrator.suggest_negotiation(result.run_id, final, SETTINGS, backend=Words(error=ConnectionError("down")), sleep=lambda _s: None)
    assert down.ok and not down.used_ai and "plain wording" in down.message and all(ln.text == ln.plain for ln in down.lines)


def test_an_unknown_supplier_or_run_is_refused():
    result = run_with_gaps()
    assert not orchestrator.suggest_negotiation(result.run_id, "Nobody Ltd").ok
    assert not orchestrator.suggest_negotiation("RUN-NOPE", BRIGHT).ok


def test_when_no_supplier_meets_every_must_have_there_is_nothing_to_recommend():
    rows_ = [r for r in __import__("review_support").FIVE if r[0] in (BRIGHT, "Orbit Digital")]
    from review_support import BAD_FACTS
    result = evaluated_run(rows_, facts={BRIGHT: BAD_FACTS, "Orbit Digital": BAD_FACTS})
    use_up_the_rounds(result.run_id)
    out = orchestrator.suggest_negotiation(result.run_id, BRIGHT)
    assert not out.ok and "No supplier meets every must-have" in out.message


def test_the_audit_export_lists_the_newest_set_and_fit_note_of_each_supplier():
    from ui import tab_audit
    result = run_with_gaps()
    final = final_of(result.run_id)
    orchestrator.suggest_negotiation(result.run_id, final)
    orchestrator.suggest_negotiation(result.run_id, final, SETTINGS, backend=Words())
    with repository.connection() as conn:
        records = repository.list_negotiations(conn, result.run_id)
    got = tab_audit.negotiation_export(records)
    assert [g["supplier"] for g in got] == [final] and got[0]["points"][0].startswith("Please consider: ")
    assert got[0]["fit"].startswith("Fits what you asked for") and set(got[0]) == {"supplier", "created_at", "fit", "points"}
    assert tab_audit.negotiation_export([]) == []


# ---------------------------------------------------------------- the section on the Re-evaluation tab

def page_for(monkeypatch, run_id, settings=None, backend=None):
    import test_tab_review as R
    page = R.open_page(monkeypatch, run_id, settings=settings)
    if backend is not None:
        monkeypatch.setattr(client, "_LiteLLMBackend", lambda: backend)

    def draw():
        page.calls, page.visible, page.drop_downs, page.open_block = [], [], [], None
        page.buttons, page.selectboxes, page.radios = {}, [], []
        R.T.render()
        return page
    return page, draw, R


def click_make(page, draw, R):
    draw()
    button = page.button_labelled(R.T.NEGOTIATION_BUTTON)
    button["on_click"](*button["args"])
    with pytest.raises(R._Rerun):
        draw()
    return draw()


def test_the_section_has_one_button_for_the_final_supplier_and_no_supplier_picker(monkeypatch):
    result = run_with_gaps()
    page, draw, R = page_for(monkeypatch, result.run_id)
    draw()
    T = R.T
    assert f"#### {T.NEGOTIATION_TITLE}" in [t for _k, t, _kw in page.visible] and T.NEGOTIATION_INTRO in page.shown("markdown")
    assert page.button_labelled(T.NEGOTIATION_BUTTON)["args"] == (result.run_id, final_of(result.run_id))
    assert not [b for b in page.selectboxes if b["label"] == "Supplier"]
    assert "<ol" not in page.shown("markdown")                   # nothing is made until the button is clicked


def test_clicking_the_button_shows_the_final_supplier_why_it_fits_and_the_points(monkeypatch):
    result = run_with_gaps()
    final = final_of(result.run_id)
    page, draw, R = page_for(monkeypatch, result.run_id)
    T = R.T
    click_make(page, draw, R)
    shown = page.shown("markdown")
    assert T.FIT_HEAD.format(supplier=final) in shown and "You asked for more weight on Technical Capability" in shown
    assert "over our Rs 60 lakh budget" in shown and ">Budget<" in shown and T.NEGOTIATION_PLAIN_TEXT in shown
    with repository.connection() as conn:
        assert [r.supplier_name for r in repository.list_negotiations(conn, result.run_id)] == [final]


def test_with_a_key_the_ai_wording_is_shown_and_said_so(monkeypatch):
    result = run_with_gaps()
    page, draw, R = page_for(monkeypatch, result.run_id, settings=SETTINGS, backend=Words())
    T = R.T
    click_make(page, draw, R)
    shown = page.shown("markdown")
    assert "Please consider: " in shown and "Fits what you asked for: Technical Capability" in shown and T.NEGOTIATION_AI_TEXT in shown


def test_text_in_a_point_or_the_fit_note_is_never_drawn_as_html(monkeypatch):
    result = run_with_gaps()
    final = final_of(result.run_id)
    page, draw, R = page_for(monkeypatch, result.run_id)
    with repository.connection() as conn:
        repository.add_negotiation(conn, result.run_id, final, [{"key": "k", "topic": "risk", "text": "<script>x</script>", "plain": "p", "worded": True}],
                                   "2026-10-24T00:00:00+00:00", {"text": "<b>fit</b>", "plain": "p", "worded": True, "priorities": []})
    draw()
    shown = page.shown("markdown")
    assert "<script>" not in shown and "&lt;script&gt;" in shown and "<b>fit</b>" not in shown and "&lt;b&gt;fit&lt;/b&gt;" in shown


def test_a_final_run_still_shows_the_section(monkeypatch):
    result = run_with_gaps()
    assert orchestrator.accept_recommendation(result.run_id, 5, 4).ok
    page, draw, R = page_for(monkeypatch, result.run_id)
    draw()
    assert page.button_labelled(R.T.NEGOTIATION_BUTTON)


def test_with_no_eligible_supplier_the_section_says_so_and_has_no_button(monkeypatch):
    from review_support import BAD_FACTS
    rows_ = [r for r in __import__("review_support").FIVE if r[0] in (BRIGHT, "Orbit Digital")]
    result = evaluated_run(rows_, facts={BRIGHT: BAD_FACTS, "Orbit Digital": BAD_FACTS})
    use_up_the_rounds(result.run_id)
    page, draw, R = page_for(monkeypatch, result.run_id)
    draw()
    assert R.T.NEGOTIATION_NONE in page.shown("markdown") and not [b for b in page.buttons.values() if b["label"] == R.T.NEGOTIATION_BUTTON]


# ---------------------------------------------------------------- only after every round was used (the person's rule)

@pytest.mark.parametrize("rounds_done", [0, 1, 2])
def test_before_all_three_rounds_are_used_there_are_no_points_and_nothing_is_saved(rounds_done):
    result = run_with_gaps(rounds=False)
    for round_no, criterion in enumerate((1, 2, 3)[:rounds_done], start=1):
        assert orchestrator.change_priorities(result.run_id, 2, criterion, round_no).ok
    out = orchestrator.suggest_negotiation(result.run_id, final_of(result.run_id), SETTINGS, backend=(backend := Words()))
    assert not out.ok and out.message == orchestrator.NEGOTIATION_LOCKED_TEXT and out.lines == () and out.fit is None and backend.sent == []
    with repository.connection() as conn:
        assert repository.list_negotiations(conn, result.run_id) == []
    assert not rows("SELECT 1 FROM audit_log WHERE rfp_run_id = ? AND action = 'negotiation_suggested'", result.run_id)


def test_a_run_accepted_before_the_rounds_were_used_gets_no_points():
    result = run_with_gaps(rounds=False)
    assert orchestrator.accept_recommendation(result.run_id, 5, 1).ok
    assert not orchestrator.suggest_negotiation(result.run_id, final_of(result.run_id)).ok


def test_the_third_round_opens_it():
    result = run_with_gaps(rounds=False)
    for round_no, criterion in enumerate((1, 2), start=1):
        orchestrator.change_priorities(result.run_id, 2, criterion, round_no)
    assert not orchestrator.suggest_negotiation(result.run_id, final_of(result.run_id)).ok
    orchestrator.change_priorities(result.run_id, 2, 3, 3)
    assert orchestrator.suggest_negotiation(result.run_id, final_of(result.run_id)).ok


def test_the_tab_says_when_it_opens_and_has_no_button_until_then(monkeypatch):
    result = run_with_gaps(rounds=False)
    page, draw, R = page_for(monkeypatch, result.run_id)
    draw()
    T = R.T
    assert T.NEGOTIATION_LOCKED in page.shown("markdown") and not [b for b in page.buttons.values() if b["label"] == T.NEGOTIATION_BUTTON]
    assert T.NEGOTIATION_INTRO not in page.shown("markdown")


def test_a_long_risk_is_cut_to_a_sentence_sized_point():
    got = negotiation_points([], None, None, [], ["word " * 100])
    assert got[0].key == "risk:1" and len(got[0].detail) <= insights.RISK_CHARS_MAX and len(got[0].text) < 260


def test_a_supplier_whose_evaluation_failed_has_no_points():
    from review_support import FIVE, FakeBackend, START, TERMS, pdf_for, reply
    proposals = [orchestrator.Proposal(f"{n.lower().replace(' ', '_')}.pdf", n, when, rating, pdf_for(n)) for n, when, rating, _s in FIVE]
    texts = [reply(FIVE[0][3]), "not json", "not json"] + [reply(s) for *_x, s in FIVE[2:]]
    result = orchestrator.run_evaluation(proposals, TERMS, SETTINGS, "Sri", backend=FakeBackend(*texts), sleep=lambda _s: None, now=lambda: START)
    assert result.status == "Evaluated"
    use_up_the_rounds(result.run_id)
    out = orchestrator.suggest_negotiation(result.run_id, BRIGHT)
    assert not out.ok
    with repository.connection() as conn:
        assert repository.list_negotiations(conn, result.run_id) == []


def test_the_button_is_for_the_recommended_supplier_even_when_a_supplier_that_fails_a_must_have_ranks_first(monkeypatch):
    from review_support import BAD_FACTS, FIVE
    rows_ = [(n, w, r, [10, 10, 10, 10, 10] if n == BRIGHT else s) for n, w, r, s in FIVE]
    result = evaluated_run(rows_, facts={BRIGHT: BAD_FACTS})
    use_up_the_rounds(result.run_id)
    page, draw, R = page_for(monkeypatch, result.run_id)
    draw()
    view = R.view_of(result.run_id)
    assert view.board[0].name == BRIGHT and view.recommended != BRIGHT
    assert page.button_labelled(R.T.NEGOTIATION_BUTTON)["args"] == (result.run_id, view.recommended)
    assert orchestrator.suggest_negotiation(result.run_id, view.recommended).ok and not orchestrator.suggest_negotiation(result.run_id, BRIGHT).ok


def test_only_the_newest_set_for_the_final_supplier_is_shown(monkeypatch):
    result = run_with_gaps()
    final = final_of(result.run_id)
    other = next(n for n in NAMES if n != final)
    page, draw, R = page_for(monkeypatch, result.run_id)
    with repository.connection() as conn:
        repository.add_negotiation(conn, result.run_id, final, [{"key": "k", "topic": "risk", "text": "OLD SET", "plain": "p", "worded": False}],
                                   "2026-10-24T00:00:00+00:00", {"text": "old fit", "plain": "p", "worded": False, "priorities": []})
        repository.add_negotiation(conn, result.run_id, other, [{"key": "k", "topic": "risk", "text": "OTHER SUPPLIER SET", "plain": "p", "worded": False}],
                                   "2026-10-24T00:01:00+00:00", {"text": "other fit", "plain": "p", "worded": False, "priorities": []})
    draw()
    assert "OLD SET" in page.shown("markdown") and "OTHER SUPPLIER SET" not in page.shown("markdown")


def test_the_fit_panel_escapes_the_supplier_name_and_the_text():
    from ui import tab_review as T
    record = repository.NegotiationRecord(1, "<i>X</i> Ltd", [], "t", {"text": "<u>why</u>"})
    drawn = T.fit_html(record)
    assert "<i>" not in drawn and "<u>" not in drawn and "&lt;i&gt;X&lt;/i&gt; Ltd" in drawn and "&lt;u&gt;why&lt;/u&gt;" in drawn


def test_after_the_click_the_page_says_it_is_done_once_and_the_result_is_above_the_button(monkeypatch):
    result = run_with_gaps()
    page, draw, R = page_for(monkeypatch, result.run_id)
    T = R.T
    click_make(page, draw, R)
    assert T.NEGOTIATION_DONE in page.text("success")
    kinds = [(k, t) for k, t, _kw in page.visible]
    fit = next(i for i, (k, t) in enumerate(kinds) if "Best fit for what you asked for" in t)
    button = next(i for i, (k, t) in enumerate(kinds) if k == "button" and t == T.NEGOTIATION_BUTTON)
    assert fit < button
    draw()
    assert T.NEGOTIATION_DONE not in page.text("success")            # said once, not on every redraw


def test_a_refusal_is_shown_as_an_error_and_not_as_done(monkeypatch):
    result = run_with_gaps(rounds=False)
    page, draw, R = page_for(monkeypatch, result.run_id)
    page.session_state[R.T.PENDING_KEY] = {"kind": "negotiate", "run_id": result.run_id, "round_no": 0, "score": None,
                                           "supplier": final_of(result.run_id), "keys": []}
    with pytest.raises(R._Rerun):
        draw()
    draw()
    assert R.T.NEGOTIATION_DONE not in page.text("success", "error")


def test_once_every_round_is_used_the_final_section_comes_before_the_question(monkeypatch):
    result = run_with_gaps()
    page, draw, R = page_for(monkeypatch, result.run_id)
    draw()
    heads = [t for k, t, _kw in page.visible if k == "markdown" and t.startswith("####")]
    assert heads.count(f"#### {R.T.NEGOTIATION_TITLE}") == 1
    assert heads.index(f"#### {R.T.NEGOTIATION_TITLE}") < heads.index("#### Your answer")
    early = run_with_gaps(rounds=False)
    page, draw, R = page_for(monkeypatch, early.run_id)
    draw()
    heads = [t for k, t, _kw in page.visible if k == "markdown" and t.startswith("####")]
    assert heads.count(f"#### {R.T.NEGOTIATION_TITLE}") == 1
    assert heads.index("#### Your answer") < heads.index(f"#### {R.T.NEGOTIATION_TITLE}")
