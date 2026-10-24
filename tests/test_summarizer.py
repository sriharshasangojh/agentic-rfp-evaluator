"""Tests for agents/summarizer.py (spec 7.3, feature F16) and the pure helpers in tools/insights.py that apply its wording.
No network and no key: a fake backend answers."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents import summarizer
from agents.summarizer import SummarizerError, Wording, check_reply, names_another
from llm import client
from tools import insights
from tools.insights import ProsCons, Reason, apply_wording, reason_for

SETTINGS = client.LLMSettings("OpenAI", "test-model", "fake-key-wording-1")
PRO1 = Reason("Strong on Laptop specs (9/10, best of all bids)", insights.BASIS_BATCH, insights.KIND_CRITERION, "criterion:1")
PRO2 = Reason("Meets every must-have", insights.BASIS_REQUIREMENT, insights.KIND_ALL_GATES, "all_gates")
CON1 = Reason("Weaker on Price (5/10; best bid 8/10)", insights.BASIS_BATCH, insights.KIND_CRITERION, "criterion:3")
CHOSEN = ProsCons((PRO1, PRO2), (CON1,))
OTHERS = ["NexaWorks", "Horizon Datacom"]


def reply(pros, cons, reason="A fine choice for the price (Page 2)."):
    return json.dumps({"pros": pros, "cons": cons, "reason": reason})


class Backend:
    def __init__(self, *texts, tokens=300, cost=0.002):
        self.texts, self.calls, self.tokens, self.cost_value = list(texts), [], tokens, cost

    def completion(self, **kwargs):
        from types import SimpleNamespace
        self.calls.append(kwargs)
        text = self.texts[min(len(self.calls), len(self.texts)) - 1]
        if isinstance(text, BaseException):
            raise text
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=SimpleNamespace(total_tokens=self.tokens))

    def supports_json_mode(self, settings):
        return True

    def cost(self, response):
        return self.cost_value


# ---------------------------------------------------------------- the prompt

def test_the_prompt_is_the_spec_text_word_for_word():
    spec = Path(__file__).resolve().parent.parent / "prompts" / "summarizer_system.md"
    text = spec.read_text(encoding="utf-8").strip()
    assert text.splitlines()[0] == "You write short, plain-language points for a startup manager."
    assert 'Reply with JSON only: {"pros": ["..."], "cons": ["..."], "reason": "..."}' in text
    assert "Do not add, remove, reorder or re-judge items." in text
    assert "Never name another supplier or quote another supplier's price." in text
    assert "max 25 words" in text and "max 20 words" in text and '"vs requirement" or "vs this batch"' in text
    assert len(text.splitlines()) == 9


def test_a_missing_prompt_file_is_reported_in_words(tmp_path):
    with pytest.raises(SummarizerError, match="missing"):
        summarizer.read_prompt(tmp_path / "nope.md")


def test_a_locked_prompt_file_is_tried_once_more(monkeypatch, tmp_path):
    file = tmp_path / "p.md"
    file.write_text("hello", encoding="utf-8")
    real, state = Path.read_text, {"n": 0}

    def flaky(self, *a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise PermissionError("locked")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", flaky)
    slept = []
    assert summarizer.read_prompt(file, sleep=slept.append) == "hello" and slept == [3]
    state["n"] = -5
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(SummarizerError, match="locked"):
        summarizer.read_prompt(file, sleep=lambda _s: None)


# ---------------------------------------------------------------- what is sent

def test_the_message_carries_only_the_chosen_points_with_their_proof():
    support = {"criterion:1": ("It has the best specs.", "16 GB RAM (Page 2)"), "criterion:3": ("Priced high.", "")}
    messages = summarizer.build_messages(CHOSEN, support, "PROMPT")
    assert [m["role"] for m in messages] == ["system", "user"] and messages[0]["content"] == "PROMPT"
    body = json.loads(messages[1]["content"])
    assert body["pros"][0] == {"label": "vs this batch", "point": PRO1.text, "justification": "It has the best specs.", "evidence": "16 GB RAM (Page 2)"}
    assert body["pros"][1] == {"label": "vs requirement", "point": "Meets every must-have"}
    assert body["cons"] == [{"label": "vs this batch", "point": CON1.text, "justification": "Priced high."}]
    assert set(body) == {"pros", "cons"}


def test_white_space_in_the_proof_is_flattened():
    body = json.loads(summarizer.build_messages(ProsCons((PRO1,), ()), {"criterion:1": ("a\n\n b", " c\td ")}, "P")[1]["content"])
    assert body["pros"][0]["justification"] == "a b" and body["pros"][0]["evidence"] == "c d"


# ---------------------------------------------------------------- the checks on the reply

def good(**changes):
    base = dict(pros=["Best specs in the batch (Page 2)", "Passes every mandatory check"], cons=["Price is higher than the best bid (Page 4)"])
    base.update(changes)
    return reply(base["pros"], base["cons"], base.get("reason", "A fine choice for the price (Page 2)."))


def test_a_good_reply_becomes_wording_keyed_by_point():
    wording = check_reply(good(), CHOSEN, OTHERS)
    assert wording.pros == {"criterion:1": "Best specs in the batch (Page 2)", "all_gates": "Passes every mandatory check"}
    assert wording.cons == {"criterion:3": "Price is higher than the best bid (Page 4)"}
    assert wording.reason == "A fine choice for the price (Page 2)." and wording.reason_for == ("criterion:1", "all_gates")
    assert wording.kept_plain == 0


@pytest.mark.parametrize("text", ["not json at all", "[]", "null", '"text"', "", "{}"])
def test_a_reply_that_is_not_the_expected_object_is_refused(text):
    with pytest.raises(SummarizerError):
        check_reply(text, CHOSEN, OTHERS)


@pytest.mark.parametrize("pros,cons", [(["one"], ["c"]), (["a", "b", "c"], ["c"]), (["a", "b"], []), (["a", "b"], ["c", "d"])])
def test_a_reply_with_the_wrong_number_of_lines_is_refused_whole(pros, cons):
    with pytest.raises(SummarizerError, match="did not match"):
        check_reply(reply(pros, cons), CHOSEN, OTHERS)


def test_pros_and_cons_must_be_lists():
    with pytest.raises(SummarizerError):
        check_reply(json.dumps({"pros": "x", "cons": ["c"], "reason": "r"}), CHOSEN, OTHERS)
    with pytest.raises(SummarizerError):
        check_reply(json.dumps({"cons": ["c"], "reason": "r"}), CHOSEN, OTHERS)


def test_a_reply_in_a_code_fence_or_with_chatter_around_it_is_read():
    assert check_reply("```json\n" + good() + "\n```", CHOSEN, OTHERS).reason
    assert check_reply("Here you go:\n" + good() + "\nHope that helps!", CHOSEN, OTHERS).pros


@pytest.mark.parametrize("bad", ["", "   ", None, 7, ["list"], "word " * 26, "x" * 230])
def test_a_bad_line_keeps_the_template_text_for_that_point_only(bad):
    wording = check_reply(good(pros=[bad, "Passes every mandatory check"]), CHOSEN, OTHERS)
    assert "criterion:1" not in wording.pros and wording.pros["all_gates"] == "Passes every mandatory check"
    assert wording.kept_plain == 1


def test_a_line_of_twenty_five_words_is_still_accepted():
    line = " ".join(["word"] * 25)
    assert check_reply(good(cons=[line]), CHOSEN, OTHERS).cons == {"criterion:3": line}


def test_a_line_that_names_another_supplier_is_dropped():
    wording = check_reply(good(cons=["NexaWorks is cheaper (Page 4)"]), CHOSEN, OTHERS)
    assert wording.cons == {} and wording.kept_plain == 1
    wording = check_reply(good(pros=["Better than Horizon on specs", "Passes every mandatory check"]), CHOSEN, OTHERS)
    assert "criterion:1" not in wording.pros


def test_the_reason_is_dropped_when_it_is_too_long_or_names_another_supplier_or_there_are_no_pros():
    assert check_reply(good(reason="word " * 33), CHOSEN, OTHERS).reason is None
    assert check_reply(good(reason="Cheaper than NexaWorks."), CHOSEN, OTHERS).reason is None
    assert check_reply(good(reason=""), CHOSEN, OTHERS).reason is None
    assert check_reply(good(reason="x" * 270), CHOSEN, OTHERS).reason is None
    only_cons = ProsCons((), (CON1,))
    assert check_reply(reply([], ["c"], "A reason with no pros."), only_cons, OTHERS).reason is None
    assert check_reply(good(reason="word " * 32), CHOSEN, OTHERS).reason is not None


def test_a_point_without_a_key_is_never_worded():
    keyless = ProsCons((Reason("plain", "vs this batch", "criterion"),), ())
    wording = check_reply(reply(["worded"], []), keyless, OTHERS)
    assert wording.pros == {} and wording.kept_plain == 1


@pytest.mark.parametrize("text,hit", [("NexaWorks wins", True), ("nexaworks", True), ("Nexaworks-based", True), ("horizon datacom ships", True),
                                      ("Horizon ships", True), ("the horizon is far", True), ("Nexa", False), ("No names here", False),
                                      ("Apex is fine", False)])
def test_names_another_looks_for_whole_names_and_first_words(text, hit):
    assert names_another(text, ["NexaWorks", "Horizon Datacom"]) is hit


def test_a_short_first_word_is_not_a_name():
    assert names_another("the apex of quality", ["Apex Systems"]) is True       # four letters: counts
    assert names_another("an ibm laptop", ["IBM Corp"]) is False                # three letters: only the full name counts
    assert names_another("ibm corp laptop", ["IBM Corp"]) is True


# ---------------------------------------------------------------- the call

def test_word_points_makes_one_call_with_the_wording_temperature_and_json_mode():
    backend = Backend(good())
    wording = summarizer.word_points(CHOSEN, {}, OTHERS, SETTINGS, backend=backend, sleep=lambda _s: None)
    assert len(backend.calls) == 1 and backend.calls[0]["temperature"] == 0.4 == client.temperature_for("wording")
    assert backend.calls[0].get("response_format") == {"type": "json_object"}
    assert (wording.tokens, wording.cost_usd) == (300, 0.002) and wording.reason
    assert "fake-key-wording-1" not in json.dumps(backend.calls[0]["messages"])


def test_an_unusable_reply_reports_what_it_cost():
    with pytest.raises(SummarizerError) as caught:
        summarizer.word_points(CHOSEN, {}, OTHERS, SETTINGS, backend=Backend("nonsense", tokens=123, cost=0.5), sleep=lambda _s: None)
    assert (caught.value.tokens, caught.value.cost_usd) == (123, 0.5)


def test_a_provider_problem_is_raised_for_the_caller():
    with pytest.raises(client.LLMError):
        summarizer.word_points(CHOSEN, {}, OTHERS, SETTINGS, backend=Backend(Exception("401 Unauthorized")), sleep=lambda _s: None)


def test_wording_is_saved_as_plain_data():
    w = Wording({"a": "x"}, {"b": "y"}, "why", ("a",), 2, 5, 0.1)
    assert w.as_saved() == {"pros": {"a": "x"}, "cons": {"b": "y"}, "reason": "why", "reason_for": ["a"]}
    json.dumps(w.as_saved())


# ---------------------------------------------------------------- applying the wording (pure)

SAVED = {"pros": {"criterion:1": "Best specs (Page 2)"}, "cons": {"criterion:3": "Pricey (Page 4)"}, "reason": "Good value.", "reason_for": ["criterion:1", "all_gates"]}


def test_apply_wording_swaps_the_text_and_keeps_everything_else():
    out = apply_wording(CHOSEN, SAVED)
    assert [r.text for r in out.pros] == ["Best specs (Page 2)", "Meets every must-have"]       # no wording for the second: template text stays
    assert [r.text for r in out.cons] == ["Pricey (Page 4)"]
    assert [(r.basis, r.kind, r.key) for r in out.pros + out.cons] == [(r.basis, r.kind, r.key) for r in CHOSEN.pros + CHOSEN.cons]


@pytest.mark.parametrize("saved", [None, "text", [], {}, {"pros": "x"}, {"pros": {"criterion:1": 5}}, {"pros": {"criterion:1": "  "}}, {"cons": {"criterion:1": "w"}}])
def test_apply_wording_ignores_anything_unusable(saved):
    assert apply_wording(CHOSEN, saved) == CHOSEN


def test_a_point_that_was_not_chosen_when_the_wording_was_written_keeps_its_template():
    other = ProsCons((Reason("Strong on Delivery", "vs this batch", "criterion", "criterion:4"),), ())
    assert apply_wording(other, SAVED) == other


def test_wording_is_cleaned_of_extra_white_space():
    assert apply_wording(CHOSEN, {"pros": {"criterion:1": " a\n b  "}}).pros[0].text == "a b"


def test_reason_for_is_shown_only_while_the_same_pros_stand():
    assert reason_for(CHOSEN, SAVED) == "Good value."
    changed = ProsCons((PRO2, PRO1), ())
    assert reason_for(changed, SAVED) is None                                  # order changed
    assert reason_for(ProsCons((PRO1,), ()), SAVED) is None                    # one pro fewer
    assert reason_for(CHOSEN, {**SAVED, "reason": " "}) is None
    assert reason_for(CHOSEN, {**SAVED, "reason_for": []}) is None
    assert reason_for(ProsCons((), ()), {**SAVED, "reason_for": []}) is None
    assert reason_for(CHOSEN, None) is None and reason_for(CHOSEN, {"reason": "x"}) is None


def test_the_reason_is_cleaned_of_extra_white_space():
    assert reason_for(CHOSEN, {**SAVED, "reason": " Good\n value. "}) == "Good value."


def test_selection_gives_every_point_a_key():
    crit = [insights.CriterionResult(1, "Laptop specs", 9, 10, 30, 9, 0, 100.0, False, True),
            insights.CriterionResult(2, "Price", 4, 10, 20, 9, -5, 44.4, False, False),
            insights.CriterionResult(3, "Support", 0, 10, 10, 9, -9, 0.0, True, None)]
    gates = [insights.GateOutcome("bis", "BIS", False, "not named")]
    chosen = insights.select_pros_cons(crit, gates)
    assert [r.key for r in chosen.pros] == ["criterion:1"]
    assert [r.key for r in chosen.cons] == ["gate:bis", "criterion:2", "criterion:3"][:3]
    ok = insights.select_pros_cons(crit[:1], [insights.GateOutcome("bis", "BIS", True, "ok")])
    assert [r.key for r in ok.pros] == ["criterion:1", "all_gates"]
    ev = insights.select_pros_cons([insights.CriterionResult(2, "Price", 8, 10, 20, 9, -1, 88.0, False, False)], [])
    assert [r.key for r in ev.cons] == ["evidence:2"]
    ns = insights.select_pros_cons([insights.CriterionResult(2, "Price", 0, 10, 20, 0, 0, None, True, None)], [])
    assert [r.key for r in ns.cons] == ["not_stated:2"]


def test_the_key_is_not_part_of_a_points_identity():
    assert Reason("a", "b", "c", "k1") == Reason("a", "b", "c", "k2")


def test_a_name_inside_a_longer_word_is_not_a_name():
    assert names_another("a pinnacle of apexes", ["Apex Systems"]) is False
    assert names_another("an apex of quality", ["Apex Systems"]) is True
    assert names_another("xnexaworks", ["NexaWorks"]) is False and names_another("nexaworksx", ["NexaWorks"]) is False
