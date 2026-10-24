"""Ask the RFP tab tests (feature F14, spec 12 and 13.3): what the tab shows, what its buttons do, and how New evaluation hands it the text.

The page is drawn into a recording stand-in for Streamlit and a click is simulated the way Streamlit does it: the button's callback runs
(it only queues what was asked), then the tab is drawn again and carries it out. The model is a fake that returns prepared replies, so no
API key or network is used. The proposal text (chunks) is given to the tab through the session, exactly as the New evaluation tab does.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("streamlit")

from agents import orchestrator  # noqa: E402
from db import seed  # noqa: E402
from llm import client  # noqa: E402
from review_support import (APEX, ASK_TEXTS, EXCERPT_SENTINEL, FIVE, GOOD_FACTS, KEY, NEXA, SETTINGS, TERMS, FakeBackend,  # noqa: E402
                            ask_proposals, evaluated_run, reply, rows, whole_database)
from tools import retrieval  # noqa: E402
from ui import tab_ask as T, tab_new_evaluation as NE  # noqa: E402
from ui.tab_new_evaluation import PROPOSAL_CHUNKS_KEY, SHOWN_RUN_KEY  # noqa: E402

try:
    from streamlit.testing.v1 import AppTest
except ImportError:   # a stand-in for Streamlit, or a very old one
    AppTest = None

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
needs_app_test = pytest.mark.skipif(AppTest is None, reason="Streamlit's test runner is not available")

GOOD = "Apex Systems gives it (Apex Systems, Page 1). NexaWorks gives next-business-day repair (NexaWorks, Page 2)."
QUESTION = "Which suppliers give on-site warranty with next-day repair?"


@pytest.fixture(autouse=True)
def seeded_database():
    seed.ensure_database()


@pytest.fixture
def run_id() -> str:
    return evaluated_run().run_id


class _Rerun(Exception):
    """Stands for Streamlit stopping the script and starting it again."""


class _Block:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _DropDown:
    def __init__(self, page: _Page, entry: dict[str, Any]) -> None:
        self.page, self.entry = page, entry

    def __enter__(self):
        self.page.open_block = self.entry
        return self

    def __exit__(self, *_exc):
        self.page.open_block = None
        return False


class _Page:
    """A recording stand-in for the `st` module. What is drawn inside an expander is kept apart from what is drawn on the page itself."""

    def __init__(self, session: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.visible: list[tuple[str, str, dict[str, Any]]] = []
        self.drop_downs: list[dict[str, Any]] = []
        self.open_block: dict[str, Any] | None = None
        self.session_state: dict[str, Any] = dict(session or {})
        self.buttons: dict[str, dict[str, Any]] = {}
        self.selectboxes: list[dict[str, Any]] = []
        self.text_inputs: list[dict[str, Any]] = []
        self.reruns = 0

    def _record(self, kind, text="", **kwargs):
        call = (kind, str(text), kwargs)
        self.calls.append(call)
        (self.visible if self.open_block is None else self.open_block["calls"]).append(call)

    def expander(self, label, expanded=False, **kwargs):
        entry = {"label": label, "expanded": expanded, "calls": [], **kwargs}
        self.drop_downs.append(entry)
        return _DropDown(self, entry)

    def markdown(self, text, **kwargs):
        self._record("markdown", text, **kwargs)

    def info(self, text, **kwargs):
        self._record("info", text, **kwargs)

    def error(self, text, **kwargs):
        self._record("error", text, **kwargs)

    def success(self, text, **kwargs):
        self._record("success", text, **kwargs)

    def spinner(self, text="", **kwargs):
        self._record("spinner", text, **kwargs)
        return _Block()

    def rerun(self):
        self.reruns += 1
        raise _Rerun

    def selectbox(self, label, options, index=0, key=None, **kwargs):
        self.selectboxes.append({"label": label, "options": list(options), "key": key, "index": index})
        self.session_state.setdefault(key, list(options)[index])
        return self.session_state[key]

    def text_input(self, label, key=None, **kwargs):
        self.text_inputs.append({"label": label, "key": key, **kwargs})
        return self.session_state.setdefault(key, "")

    def button(self, label, key=None, **kwargs):
        self._record("button", label, key=key, **kwargs)
        self.buttons[key] = {"label": label, **kwargs}
        return False

    def shown(self, *kinds: str) -> str:
        return "\n".join(text for kind, text, _kw in self.visible if not kinds or kind in kinds)

    def everything(self, *kinds: str) -> str:
        return "\n".join(text for kind, text, _kw in self.calls if not kinds or kind in kinds)

    def drop_down(self, label: str) -> dict[str, Any]:
        found = [d for d in self.drop_downs if d["label"] == label]
        assert len(found) == 1, (label, [d["label"] for d in self.drop_downs])
        return found[0]

    def inside(self, label: str) -> str:
        return "\n".join(text for _k, text, _kw in self.drop_down(label)["calls"])

    def button_labelled(self, label: str) -> dict[str, Any]:
        found = [b for b in self.buttons.values() if b["label"] == label]
        assert len(found) == 1, (label, [b["label"] for b in self.buttons.values()])
        return found[0]

    def html(self) -> list[str]:
        """Every piece of HTML drawn (inside or outside a drop-down)."""
        return [text for kind, text, kw in self.calls if kind == "markdown" and kw.get("unsafe_allow_html")]


def assert_safe(markup: str) -> None:
    assert "\n" not in markup and "<script" not in markup.lower() and "<img" not in markup.lower() and "<h1" not in markup.lower()


def chunks_for(run: str):
    return (run, orchestrator.chunks_of(ask_proposals()))


def open_page(monkeypatch, run_id: str | None, *replies, chunks: Any = "default", session=None, settings=SETTINGS) -> tuple[_Page, FakeBackend]:
    """A page whose sidebar holds a key and model (unless `settings` is None) and whose model returns the prepared replies."""
    backend = FakeBackend(*(replies or [GOOD]))
    monkeypatch.setattr(client, "_LiteLLMBackend", lambda: backend)
    state: dict[str, Any] = {**({SHOWN_RUN_KEY: run_id} if run_id else {}), **(session or {})}
    if chunks == "default" and run_id:
        state[PROPOSAL_CHUNKS_KEY] = chunks_for(run_id)
    elif chunks != "default":
        state[PROPOSAL_CHUNKS_KEY] = chunks
    page = _Page(state)
    monkeypatch.setattr(T, "st", page)
    monkeypatch.setattr(T.sidebar, "get_llm_settings", lambda: settings)
    return page, backend


def draw(page: _Page) -> _Page:
    page.calls, page.visible, page.drop_downs, page.open_block = [], [], [], None
    page.buttons, page.selectboxes, page.text_inputs = {}, [], []
    try:
        T.render()
    except _Rerun:
        pass
    return page


def settle(page: _Page) -> _Page:
    """Draw until the tab stops asking for another drawing (what Streamlit does after st.rerun())."""
    for _ in range(4):
        before = page.reruns
        draw(page)
        if page.reruns == before:
            return page
    raise AssertionError("the tab keeps redrawing")


def type_question(page: _Page, text: str, supplier: str | None = None) -> None:
    run = page.session_state[SHOWN_RUN_KEY]
    page.session_state[T.keys_of(run)["question"]] = text
    if supplier is not None:
        page.session_state[T.keys_of(run)["supplier"]] = supplier


def click(page: _Page, label: str = T.ASK_LABEL) -> _Page:
    """Draw the page (so the button shows its state), click the button (its callback queues the request), and draw until it settles."""
    draw(page)
    button = page.button_labelled(label)
    assert not button.get("disabled"), f"{label} is disabled"
    button["on_click"](*button.get("args", ()))
    return settle(page)


def ask_one(page: _Page, question: str = QUESTION, supplier: str | None = None) -> _Page:
    type_question(page, question, supplier)
    return click(page)


# ---------------------------------------------------------------- when there is nothing to ask about

def test_without_an_evaluation_the_tab_says_what_to_do_and_draws_nothing_else(monkeypatch):
    page, _ = open_page(monkeypatch, None)
    draw(page)
    assert page.shown("info") == T.EMPTY_TEXT and page.selectboxes == [] and page.text_inputs == [] and page.buttons == {}


def test_without_the_proposal_text_the_tab_explains_why_and_how_to_get_it_back(monkeypatch, run_id):
    page, _ = open_page(monkeypatch, run_id, chunks=None)
    draw(page)
    assert page.shown("info") == T.NO_TEXT_TEXT
    assert "browser session only" in T.NO_TEXT_TEXT and "never saved" in T.NO_TEXT_TEXT and "New evaluation" in T.NO_TEXT_TEXT
    assert page.selectboxes == [] and page.buttons == {}


@pytest.mark.parametrize("stored", [None, (), ("another-run", ()), ("another-run", "chunks"), "not a tuple", (1,)])
def test_text_that_is_missing_empty_or_from_another_run_is_not_used(monkeypatch, run_id, stored):
    page, _ = open_page(monkeypatch, run_id, chunks=stored)
    draw(page)
    assert page.shown("info") == T.NO_TEXT_TEXT


def test_a_run_whose_text_came_back_empty_is_treated_as_having_no_text(monkeypatch, run_id):
    page, _ = open_page(monkeypatch, run_id, chunks=(run_id, ()))
    draw(page)
    assert page.shown("info") == T.NO_TEXT_TEXT and page.buttons == {}


def test_the_text_of_another_run_is_not_used(monkeypatch, run_id):
    page, _ = open_page(monkeypatch, run_id, chunks=("some-other-run", orchestrator.chunks_of(ask_proposals())))
    draw(page)
    assert page.shown("info") == T.NO_TEXT_TEXT and page.buttons == {}


# ---------------------------------------------------------------- the controls

def test_the_page_shows_the_intro_a_supplier_picker_a_question_box_and_an_ask_button(monkeypatch, run_id):
    page, _ = open_page(monkeypatch, run_id)
    draw(page)
    assert T.INTRO_TEXT in page.shown("markdown") and "Asking never changes a score or the ranking" in T.INTRO_TEXT
    (picker,) = page.selectboxes
    assert picker["label"] == "Ask about" and picker["options"] == ["All suppliers", *[n for n, *_ in FIVE]] and picker["index"] == 0
    (box,) = page.text_inputs
    assert box["label"] == "Your question" and box["max_chars"] == retrieval.QUESTION_MAX_CHARS == 300 and box["placeholder"] == T.QUESTION_HINT
    assert picker["key"] == T.keys_of(run_id)["supplier"] and box["key"] == T.keys_of(run_id)["question"]
    ask = page.button_labelled("Ask")
    assert ask["disabled"] is False and ask["type"] == "primary"
    assert "#### Answer" not in page.shown("markdown")


def test_every_widget_belongs_to_the_run_on_screen(monkeypatch):
    first, second = evaluated_run().run_id, evaluated_run().run_id
    assert T.keys_of(first) != T.keys_of(second) and set(T.keys_of(first).values()).isdisjoint(T.keys_of(second).values())


def test_the_ask_button_needs_a_model_but_not_a_question_so_a_click_right_after_typing_is_never_lost(monkeypatch, run_id):
    page, _ = open_page(monkeypatch, run_id)
    for typed in ("", "   ", QUESTION):
        type_question(page, typed)
        assert draw(page).button_labelled("Ask")["disabled"] is False
    assert T.NO_KEY_TEXT not in page.shown("markdown")

    page, _ = open_page(monkeypatch, run_id, settings=None)
    type_question(page, QUESTION)
    draw(page)
    assert page.button_labelled("Ask")["disabled"] is True and T.NO_KEY_TEXT in page.shown("markdown")


@pytest.mark.parametrize("empty", ["", "   ", "\n"])
def test_clicking_ask_with_nothing_typed_says_so_and_sends_nothing(monkeypatch, run_id, empty):
    page, backend = open_page(monkeypatch, run_id)
    type_question(page, empty)
    click(page)
    assert page.shown("error") == "Type a question first." and backend.calls == [] and "#### Answer" not in page.shown("markdown")


def test_the_example_questions_are_in_a_closed_drop_down_and_fill_the_question_box(monkeypatch, run_id):
    page, _ = open_page(monkeypatch, run_id)
    draw(page)
    drop = page.drop_down("Example questions")
    assert drop["expanded"] is False
    labels = [text for kind, text, _kw in drop["calls"] if kind == "button"]
    assert labels == list(T.EXAMPLES) and len(labels) == 4
    assert [q for q in T.EXAMPLES][3] == "What does Vertexa say about data wiping?"
    assert not any(label in page.shown("button") for label in labels)                     # none of them is on the page itself
    button = page.button_labelled(T.EXAMPLES[1])
    button["on_click"](*button["args"])
    assert page.session_state[T.keys_of(run_id)["question"]] == T.EXAMPLES[1]
    assert draw(page).button_labelled("Ask")["disabled"] is False
    assert page.session_state[T.keys_of(run_id)["question"]] == T.EXAMPLES[1]


# ---------------------------------------------------------------- asking

def test_a_click_queues_the_question_and_the_next_drawing_answers_it(monkeypatch, run_id):
    page, backend = open_page(monkeypatch, run_id)
    type_question(page, QUESTION)
    draw(page)
    button = page.button_labelled("Ask")
    button["on_click"](*button["args"])
    queued = page.session_state[T.PENDING_KEY]
    assert queued == {"run_id": run_id, "question": QUESTION, "supplier": None} and backend.calls == []     # nothing is sent by the click itself
    settle(page)
    assert len(backend.calls) == 1 and page.reruns == 1 and T.PENDING_KEY not in page.session_state


def test_the_answer_the_question_and_where_it_came_from_are_shown(monkeypatch, run_id):
    page, backend = open_page(monkeypatch, run_id)
    ask_one(page)
    assert "#### Answer" in page.shown("markdown")
    panel = next(h for h in page.html() if "ak-panel" in h)
    assert QUESTION in panel and "Apex Systems gives it (Apex Systems, Page 1)." in panel and "(NexaWorks, Page 2)" in panel
    assert "Answered from" in panel and 'class="ak-warn"' not in panel
    hits = retrieval.Index(orchestrator.chunks_of(ask_proposals())).search(QUESTION)
    label = f"Where this came from ({len(hits)} excerpts)"
    drop = page.drop_down(label)
    assert drop["expanded"] is False
    inside = page.inside(label)
    assert "Apex Systems, page 1" in inside and "NexaWorks, page 2" in inside and "three year on-site warranty with next-day repair" in inside


def test_a_success_is_not_announced_twice_and_a_second_drawing_shows_the_same_page(monkeypatch, run_id):
    page, _ = open_page(monkeypatch, run_id)
    ask_one(page)
    assert page.everything("error", "success") == ""
    again = draw(page)
    assert "#### Answer" in again.shown("markdown") and again.everything("error", "success") == ""


def test_a_picked_supplier_is_the_only_one_searched(monkeypatch, run_id):
    page, backend = open_page(monkeypatch, run_id, "NexaWorks gives next-business-day repair (NexaWorks, Page 2).")
    ask_one(page, "What is the warranty?", supplier=NEXA)
    panel = next(h for h in page.html() if "ak-panel" in h)
    assert "the proposal of NexaWorks" in panel
    sent = backend.calls[0]["messages"][1]["content"]
    assert "[NexaWorks, Page" in sent and "[Apex Systems" not in sent and "BrightPath" not in sent


def test_a_question_that_names_one_supplier_says_it_searched_only_that_one(monkeypatch, run_id):
    page, backend = open_page(monkeypatch, run_id, "It is on-site (NexaWorks, Page 2).")
    ask_one(page, "What does NexaWorks say about its warranty?")
    panel = next(h for h in page.html() if "ak-panel" in h)
    assert "Your question names it, so only that proposal was searched." in panel
    assert "Apex" not in backend.calls[0]["messages"][1]["content"]


def test_the_question_is_kept_in_the_box_after_asking(monkeypatch, run_id):
    page, _ = open_page(monkeypatch, run_id)
    ask_one(page)
    assert page.session_state[T.keys_of(run_id)["question"]] == QUESTION


def test_the_button_uses_what_was_in_the_boxes_when_it_was_clicked(monkeypatch, run_id):
    page, backend = open_page(monkeypatch, run_id)
    type_question(page, QUESTION)
    draw(page)
    button = page.button_labelled("Ask")
    button["on_click"](*button["args"])
    type_question(page, "something typed after the click", NEXA)
    settle(page)
    assert QUESTION in backend.calls[0]["messages"][1]["content"] and "something typed" not in backend.calls[0]["messages"][1]["content"]


def test_a_question_that_matches_nothing_is_not_stated_and_says_the_ai_was_not_asked(monkeypatch, run_id):
    page, backend = open_page(monkeypatch, run_id)
    ask_one(page, "What does Orbit Digital say about quantum entanglement?", supplier="Orbit Digital")
    panel = next(h for h in page.html() if "ak-panel" in h)
    assert "Not stated in the proposal." in panel and "No excerpt of the proposal of Orbit Digital shares a word with your question" in panel
    assert "the AI was not asked" in panel and backend.calls == []
    assert not any(d["label"].startswith("Where this came from") for d in page.drop_downs)


def test_the_tab_asks_with_the_reviewers_name(monkeypatch, run_id):
    page, _ = open_page(monkeypatch, run_id, session={"reviewer_name": "  Sri  "})
    ask_one(page)
    (row,) = rows("SELECT details_json FROM audit_log WHERE action = 'question_answered'")
    assert json.loads(row["details_json"])["requested_by"] == "Sri"


# ---------------------------------------------------------------- refusals

def test_a_refusal_is_shown_once_as_an_error(monkeypatch, run_id):
    page, backend = open_page(monkeypatch, run_id)
    page.session_state[T.PENDING_KEY] = {"run_id": run_id, "question": "   ", "supplier": None}
    settle(page)
    assert page.shown("error") == "Type a question first." and backend.calls == []
    assert draw(page).shown("error") == ""
    assert "#### Answer" not in page.shown("markdown")


def test_without_a_model_the_question_is_refused_and_nothing_is_sent(monkeypatch, run_id):
    page, backend = open_page(monkeypatch, run_id, settings=None)
    page.session_state[T.PENDING_KEY] = {"run_id": run_id, "question": QUESTION, "supplier": None}
    settle(page)
    assert page.shown("error") == T.NO_KEY_TEXT and backend.calls == []


def test_a_provider_problem_is_shown_and_an_earlier_answer_stays(monkeypatch, run_id):
    page, backend = open_page(monkeypatch, run_id, GOOD, Exception("401 Unauthorized " + KEY))
    ask_one(page)
    ask_one(page, "Another question about the warranty?")
    assert page.shown("error").startswith("The AI could not be reached:") and KEY not in page.everything()
    assert "#### Answer" in page.shown("markdown")
    panel = next(h for h in page.html() if "ak-panel" in h)
    assert QUESTION in panel and "Another question" not in panel                      # the earlier answer is still the one on show
    assert not any(d["label"].startswith("Earlier questions") for d in page.drop_downs)


def test_a_request_queued_for_another_run_is_ignored(monkeypatch, run_id):
    page, backend = open_page(monkeypatch, run_id)
    page.session_state[T.PENDING_KEY] = {"run_id": "another-run", "question": QUESTION, "supplier": None}
    draw(page)
    assert backend.calls == [] and page.reruns == 0 and T.PENDING_KEY not in page.session_state


@pytest.mark.parametrize("junk", ["text", 5, None, ["a"]])
def test_a_request_that_is_not_a_request_is_ignored(monkeypatch, run_id, junk):
    page, backend = open_page(monkeypatch, run_id)
    page.session_state[T.PENDING_KEY] = junk
    draw(page)
    assert backend.calls == [] and page.reruns == 0


# ---------------------------------------------------------------- earlier questions

def test_earlier_questions_are_kept_in_a_closed_drop_down_newest_answer_first(monkeypatch, run_id):
    page, _ = open_page(monkeypatch, run_id, "First answer (Apex Systems, Page 1).", "Second answer (Apex Systems, Page 1).",
                        "Third answer (Apex Systems, Page 1).")
    ask_one(page, "First question about warranty?")
    assert not any(d["label"].startswith("Earlier questions") for d in page.drop_downs)
    ask_one(page, "Second question about warranty?")
    ask_one(page, "Third question about warranty?")
    panel = next(h for h in page.html() if "ak-panel" in h)
    assert "Third answer" in panel and "Third question" in panel
    drop = page.drop_down("Earlier questions (2)")
    assert drop["expanded"] is False
    inside = page.inside("Earlier questions (2)")
    assert inside.index("Second question") < inside.index("First question") and "Second answer" in inside and "First answer" in inside
    assert "Third question" not in inside and "Third answer" not in inside                  # the newest one is on show above, not repeated here


def test_only_the_last_ten_questions_are_kept_on_the_page(monkeypatch, run_id):
    page, _ = open_page(monkeypatch, run_id, *[f"Answer {n} (Apex Systems, Page 1)." for n in range(1, 13)])
    for n in range(1, 13):
        ask_one(page, f"Question number {n} about the warranty?")
    assert len(page.session_state[T.HISTORY_KEY]["items"]) == T.MAX_KEPT == 10
    assert page.drop_down("Earlier questions (9)")
    assert "Question number 12" in next(h for h in page.html() if "ak-panel" in h) and "Question number 2 " not in page.everything()


def test_the_answers_of_another_run_are_not_shown(monkeypatch, run_id):
    page, _ = open_page(monkeypatch, run_id)
    ask_one(page)
    other = evaluated_run().run_id
    page.session_state[SHOWN_RUN_KEY] = other
    page.session_state[PROPOSAL_CHUNKS_KEY] = chunks_for(other)
    shown = draw(page)
    assert "#### Answer" not in shown.shown("markdown") and shown.selectboxes[0]["key"] == T.keys_of(other)["supplier"]


# ---------------------------------------------------------------- words

def asked_with(question="Q?", scope=None, *, hits=(), searched=None, narrowed=False, asked_ai=True, answer=None):
    outcome = orchestrator.AskOutcome(True, "", answer, tuple(hits), searched, narrowed, asked_ai)
    return T.Asked(question, scope, outcome)


def hits_of(*pairs):
    return [retrieval.Hit(rank, 1.0, supplier, page, rank, "text") for rank, (supplier, page) in enumerate(pairs, start=1)]


@pytest.mark.parametrize("case, expected", [
    (dict(hits=hits_of(("A", 1), ("A", 2), ("B", 1))), "Answered from 3 excerpts across 2 proposals."),
    (dict(hits=hits_of(("A", 1))), "Answered from 1 excerpt across 1 proposal."),
    (dict(hits=hits_of(("A", 1), ("A", 2)), searched="A"), "Answered from the 2 best excerpts of the proposal of A."),
    (dict(hits=hits_of(("A", 1)), searched="A"), "Answered from the best excerpt of the proposal of A."),
    (dict(hits=hits_of(("A", 1)), searched="A", narrowed=True),
     "Answered from the best excerpt of the proposal of A. Your question names it, so only that proposal was searched."),
    (dict(asked_ai=False), "No excerpt of any proposal shares a word with your question, so the AI was not asked."),
    (dict(asked_ai=False, searched="A"), "No excerpt of the proposal of A shares a word with your question, so the AI was not asked."),
])
def test_one_sentence_says_where_the_answer_came_from(case, expected):
    assert T.scope_note(asked_with(**case)) == expected


def test_a_statement_without_a_page_reference_is_flagged():
    answer = T.orchestrator.qa_agent.Answer("Apex gives three years.", False, ())
    assert T.warnings_of(asked_with(answer=answer)) == (T.UNCITED_TEXT,)
    markup = T.answer_html(asked_with(answer=answer, hits=hits_of(("A", 1))))
    assert T.UNCITED_TEXT in markup and markup.count('class="ak-warn"') == 1


def test_a_page_reference_that_is_not_one_of_the_excerpts_is_flagged_with_what_it_says():
    qa = T.orchestrator.qa_agent
    answer = qa.Answer("Three years (Apex Systems, Page 9).", False, qa.check_citations("(Apex Systems, Page 9)", hits_of(("Apex Systems", 1))))
    (warning,) = T.warnings_of(asked_with(answer=answer))
    assert "Apex Systems, page 9" in warning and "not one of the excerpts" in warning


def test_a_clean_answer_has_nothing_to_double_check():
    qa = T.orchestrator.qa_agent
    answer = qa.Answer("Yes (A, Page 1).", False, qa.check_citations("(A, Page 1)", hits_of(("A", 1))))
    assert T.warnings_of(asked_with(answer=answer)) == () and 'class="ak-warn"' not in T.answer_html(asked_with(answer=answer, hits=hits_of(("A", 1))))
    assert T.warnings_of(asked_with(answer=None)) == () and T.answer_html(asked_with(answer=None)) == ""


def test_the_labels_count_in_plain_words():
    assert T.sources_label(hits_of(("A", 1))) == "Where this came from (1 excerpt)"
    assert T.sources_label(hits_of(("A", 1), ("B", 2))) == "Where this came from (2 excerpts)"
    assert T.earlier_label(3) == "Earlier questions (3)"


# ---------------------------------------------------------------- safe HTML

HOSTILE = "<script>alert(1)</script><img src=x onerror=1> & <b>bold</b>"


def test_every_text_from_the_person_the_ai_or_a_proposal_is_escaped_and_the_html_is_one_line(monkeypatch, run_id):
    hostile_chunks = (run_id, (orchestrator.pdf_tool.Chunk(APEX, 1, 1, f"Warranty line one.\n\n\nWarranty line two {HOSTILE}"),
                               orchestrator.pdf_tool.Chunk(f"Evil {HOSTILE} Co", 2, 2, f"Warranty {HOSTILE}")))
    page, _ = open_page(monkeypatch, run_id, f"Answer {HOSTILE}\n\n\n(Apex Systems, Page 1)", chunks=hostile_chunks)
    ask_one(page, f"What is the warranty? {HOSTILE}")
    markup = page.html()
    assert markup and all(h.startswith("<style>") or h.startswith("<p") for h in markup)
    for piece in markup:
        assert_safe(piece)
    joined = "\n".join(markup)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in joined and "&lt;b&gt;bold&lt;/b&gt;" in joined and "&amp;" in joined
    assert "<br>" in joined                                                          # the line breaks of an excerpt are kept, blank lines dropped
    assert "Warranty line one.<br>Warranty line two" in joined


def test_a_short_list_in_the_answer_is_drawn_as_a_list_and_other_lines_stay_as_they_are():
    answer = T.orchestrator.qa_agent.Answer("Two suppliers give it:\n- Apex Systems (Apex Systems, Page 1)\n* NexaWorks <b>(NexaWorks, Page 2)</b>\n"
                                            "• Orbit & Co\n\nThe others do not state it.\n1. Not a bullet", False, ())
    markup = T.answer_html(asked_with(answer=answer))
    assert_safe(markup)
    assert ('<div class="ak-answer">Two suppliers give it:<ul class="ak-list"><li>Apex Systems (Apex Systems, Page 1)</li>'
            '<li>NexaWorks &lt;b&gt;(NexaWorks, Page 2)&lt;/b&gt;</li><li>Orbit &amp; Co</li></ul>'
            'The others do not state it.<br>1. Not a bullet</div>') in markup


def test_two_lists_with_a_line_between_them_stay_two_lists_in_reading_order():
    answer = T.orchestrator.qa_agent.Answer("Gives it:\n- Apex (Apex Systems, Page 1)\nBut not these:\n- Orbit\nThat is all.", False, ())
    markup = T.answer_html(asked_with(answer=answer))
    assert ('<div class="ak-answer">Gives it:<ul class="ak-list"><li>Apex (Apex Systems, Page 1)</li></ul>But not these:'
            '<ul class="ak-list"><li>Orbit</li></ul>That is all.</div>') in markup


def test_an_answer_without_a_list_has_no_list_and_a_dash_inside_a_line_is_not_a_bullet():
    answer = T.orchestrator.qa_agent.Answer("It is on-site - and next-day (Apex Systems, Page 1).\n-not a bullet either", False, ())
    markup = T.answer_html(asked_with(answer=answer))
    assert '<ul class="ak-list">' not in markup.split("</style>", 1)[1]
    assert "It is on-site - and next-day (Apex Systems, Page 1).<br>-not a bullet either" in markup


def test_the_page_never_shows_the_key(monkeypatch, run_id):
    page, _ = open_page(monkeypatch, run_id)
    ask_one(page)
    assert KEY not in page.everything() and KEY not in repr(page.session_state.get(T.HISTORY_KEY)) and KEY not in whole_database()


def test_the_excerpts_are_never_saved_by_the_tab(monkeypatch, run_id):
    page, _ = open_page(monkeypatch, run_id)
    ask_one(page)
    assert EXCERPT_SENTINEL not in whole_database()


# ---------------------------------------------------------------- New evaluation hands the proposal text over

class _Status:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def update(self, **kwargs):
        self.updates.append(kwargs)


class _RunPage:
    """Just enough of `st` for `tab_new_evaluation._run`."""

    def __init__(self, session: dict[str, Any] | None = None) -> None:
        self.session_state: dict[str, Any] = dict(session or {})
        self.reruns = 0

    def status(self, label, expanded=False):
        return _Status()

    def text(self, *args, **kwargs):
        pass

    def rerun(self):
        self.reruns += 1
        raise _Rerun


def run_batch(monkeypatch, *outcomes, session=None) -> _RunPage:
    backend = FakeBackend(*outcomes)
    monkeypatch.setattr(client, "_LiteLLMBackend", lambda: backend)
    page = _RunPage({"reviewer_name": "Sri", **(session or {})})
    monkeypatch.setattr(NE, "st", page)
    with pytest.raises(_Rerun):
        NE._run(ask_proposals(), TERMS, SETTINGS)
    return page


def test_a_finished_run_leaves_its_proposal_chunks_in_the_session_for_the_ask_tab(monkeypatch):
    page = run_batch(monkeypatch, *[reply(scores, GOOD_FACTS) for _n, _w, _r, scores in FIVE])
    run_id = page.session_state[SHOWN_RUN_KEY]
    stored_run, chunks = page.session_state[PROPOSAL_CHUNKS_KEY]
    assert stored_run == run_id and chunks == orchestrator.chunks_of(ask_proposals()) and len(chunks) == 10
    assert page.session_state[NE.RESULT_KEY].status == "Evaluated"


def test_a_run_that_did_not_finish_leaves_the_earlier_text_and_run_alone(monkeypatch):
    earlier = ("earlier-run", ("kept",))
    page = run_batch(monkeypatch, Exception("401 Unauthorized"), session={SHOWN_RUN_KEY: "earlier-run", PROPOSAL_CHUNKS_KEY: earlier})
    assert page.session_state[NE.RESULT_KEY].status != "Evaluated"
    assert page.session_state[PROPOSAL_CHUNKS_KEY] == earlier and page.session_state[SHOWN_RUN_KEY] == "earlier-run"


def test_the_text_handed_over_is_what_the_ask_tab_can_search(monkeypatch):
    page = run_batch(monkeypatch, *[reply(scores, GOOD_FACTS) for _n, _w, _r, scores in FIVE])
    run_id = page.session_state[SHOWN_RUN_KEY]
    ask_page, backend = open_page(monkeypatch, run_id, chunks=page.session_state[PROPOSAL_CHUNKS_KEY])
    ask_one(ask_page, "What is the warranty of NexaWorks?")
    assert "NexaWorks" in backend.calls[0]["messages"][1]["content"] and ASK_TEXTS[NEXA][1] in backend.calls[0]["messages"][1]["content"]
    assert "#### Answer" in ask_page.shown("markdown")


# ---------------------------------------------------------------- the real app (needs Streamlit's AppTest)

def ask_tab(app):
    return app.tabs[6]


def tab_text(block) -> str:
    return "\n".join(str(item.value) for kind in ("markdown", "caption", "info", "error", "warning", "success") for item in getattr(block, kind))


@needs_app_test
def test_before_any_run_the_ask_tab_explains_what_to_do():
    app = AppTest.from_file(str(APP_PATH), default_timeout=60)
    app.run()
    assert not app.exception and len(app.tabs) == 8
    assert "No evaluation yet" in tab_text(ask_tab(app))
    assert not ask_tab(app).text_input and not ask_tab(app).selectbox


@needs_app_test
def test_with_a_run_and_its_text_the_ask_tab_offers_a_picker_a_question_box_and_examples():
    run_id = evaluated_run().run_id
    app = AppTest.from_file(str(APP_PATH), default_timeout=60)
    app.run()
    app.session_state[SHOWN_RUN_KEY] = run_id
    app.session_state[PROPOSAL_CHUNKS_KEY] = chunks_for(run_id)
    app.run()
    assert not app.exception
    tab = ask_tab(app)
    assert [s.label for s in tab.selectbox] == ["Ask about"] and [t.label for t in tab.text_input] == ["Your question"]
    assert "Ask" in [b.label for b in tab.button] and set(T.EXAMPLES) <= {b.label for b in tab.button}
    assert "Example questions" in [e.label for e in tab.expander]


@needs_app_test
def test_with_a_run_but_no_text_the_ask_tab_says_the_text_is_gone():
    app = AppTest.from_file(str(APP_PATH), default_timeout=60)
    app.run()
    app.session_state[SHOWN_RUN_KEY] = evaluated_run().run_id
    app.run()
    assert not app.exception and "kept in this browser session only" in tab_text(ask_tab(app)) and not ask_tab(app).text_input
