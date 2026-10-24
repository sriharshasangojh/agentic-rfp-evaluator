"""The "Your priorities" box of the New evaluation tab, drawn with a recording stand-in for Streamlit (no browser, no API key).

The AppTest file checks the real page; these tests check the box's own rules: what it asks the AI, what it asks the buyer to
confirm, what a "yes" and a "no" do, and what request goes to the run.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

pytest.importorskip("streamlit")

from agents import orchestrator  # noqa: E402
from db import seed  # noqa: E402
from review_support import SETTINGS, FakeBackend  # noqa: E402
from ui import tab_new_evaluation as tab  # noqa: E402

READING = "I understood: battery life matters more to you."


@pytest.fixture(autouse=True)
def seeded_database():
    seed.ensure_database()


class Page:
    """Records what the box draws. Checkboxes and the text area read and write `state`, the way the real widgets do."""

    def __init__(self, state: dict | None = None) -> None:
        self.state: dict = state if state is not None else {}
        self.calls: list[tuple[str, str]] = []
        self.buttons: dict[str, dict] = {}

    @contextmanager
    def expander(self, label, expanded=False):
        self.calls.append(("expander", label))
        yield

    @contextmanager
    def spinner(self, text):
        self.calls.append(("spinner", text))
        yield

    def _say(self, kind, text, **_kw):
        self.calls.append((kind, str(text)))

    caption = lambda self, text, **kw: self._say("caption", text)          # noqa: E731
    info = lambda self, text, **kw: self._say("info", text)                # noqa: E731
    warning = lambda self, text, **kw: self._say("warning", text)          # noqa: E731

    def checkbox(self, label, key, disabled=False, **_kw):
        self.calls.append(("checkbox", label))
        self.state.setdefault(key, False)
        return self.state[key]

    def text_area(self, label, key, **kw):
        self.calls.append(("text_area", label))
        self.state.setdefault(key, "")
        return self.state[key]

    def button(self, label, key=None, on_click=None, disabled=False, **_kw):
        self.calls.append(("button", label))
        self.buttons[key] = {"label": label, "on_click": on_click, "disabled": disabled}
        return False

    def columns(self, count):
        return [self for _ in range(count)]

    @property
    def session_state(self):
        return self.state

    def click(self, key):
        self.buttons[key]["on_click"]()




@pytest.fixture
def page(monkeypatch):
    page = Page()
    monkeypatch.setattr(tab, "st", page)
    return page


def texts(page, kind):
    return [t for k, t in page.calls if k == kind]


def reading_ok(criterion=1):
    typed = orchestrator.TypedPriority("I want laptops that last a day", criterion, READING, 100, 0.1, 1)
    return orchestrator.PriorityReading(True, "", typed)


def test_the_box_is_one_closed_expander_with_a_tick_per_criterion_a_writing_box_and_the_read_button(page):
    request = tab._priorities(False, SETTINGS)
    assert texts(page, "expander") == [tab.PRIORITIES_LABEL]
    assert [l.split(":")[0] for l in texts(page, "checkbox")] == [
        "Technical Capability", "Implementation Plan", "Commercial Value", "Security & Compliance", "Support & Experience"]
    assert texts(page, "text_area") == [tab.PRIORITIES_WRITE_LABEL] and page.buttons["ne_prio_read"]["label"] == "Understand what I wrote"
    assert request == orchestrator.PriorityRequest((), ())
    assert tab.PRIORITIES_INTRO in texts(page, "caption")
    assert "Nothing ticked: every criterion keeps the weight set on the Criteria tab." in texts(page, "caption")


def test_ticks_become_the_request_in_marking_scheme_order_and_the_new_weights_are_shown(page):
    page.state.update({"ne_prio_3": True, "ne_prio_1": True})
    request = tab._priorities(False, SETTINGS)
    assert request.ticked == (1, 3)
    assert any(c.startswith("With your priorities, for this run only:") for c in texts(page, "caption"))


def test_the_read_button_is_never_greyed_out_so_the_first_click_is_not_lost(page):
    tab._priorities(False, SETTINGS)
    assert page.buttons["ne_prio_read"]["disabled"] is False


def test_everything_is_disabled_while_a_run_is_in_progress(page):
    page.state[tab.PRIORITY_READING_KEY] = reading_ok()
    tab._priorities(True, SETTINGS)
    assert page.buttons["ne_prio_read"]["disabled"] is True and page.buttons["ne_prio_yes"]["disabled"] is True and page.buttons["ne_prio_no"]["disabled"] is True


def test_clicking_read_queues_one_reading_and_the_next_draw_asks_the_ai_once(page, monkeypatch):
    asked = []
    monkeypatch.setattr(orchestrator, "understand_priority", lambda text, settings, **kw: asked.append(text) or reading_ok())
    page.state[tab.PRIORITY_TEXT_KEY] = "I want laptops that last a day"
    tab._priorities(False, SETTINGS)
    assert asked == []                                             # drawing alone asks nothing
    page.click("ne_prio_read")
    tab._priorities(False, SETTINGS)
    assert asked == ["I want laptops that last a day"] and ("spinner", "Reading what you wrote...") in page.calls
    tab._priorities(False, SETTINGS)
    assert len(asked) == 1                                         # a later redraw does not ask again


def test_a_proposed_criterion_is_shown_with_its_reading_and_a_yes_and_a_no(page):
    page.state[tab.PRIORITY_READING_KEY] = reading_ok(3)
    tab._priorities(False, SETTINGS)
    [shown] = texts(page, "info")
    assert shown == f"{READING} That would tick **Commercial Value**. Is that right?"
    assert page.buttons["ne_prio_yes"]["label"] == "Yes, tick it" and page.buttons["ne_prio_no"]["label"] == "No, that is not it"


def test_yes_ticks_the_criterion_keeps_the_sentence_and_clears_the_box(page):
    page.state[tab.PRIORITY_READING_KEY] = reading_ok(3)
    page.state[tab.PRIORITY_TEXT_KEY] = "I want laptops that last a day"
    tab._priorities(False, SETTINGS)
    page.click("ne_prio_yes")
    assert page.state["ne_prio_3"] is True and page.state[tab.PRIORITY_TEXT_KEY] == "" and tab.PRIORITY_READING_KEY not in page.state
    request = tab._priorities(False, SETTINGS)
    assert request.ticked == (3,) and [t.criterion_id for t in request.typed] == [3]
    assert 'You wrote: “I want laptops that last a day”' in texts(page, "caption")


def test_no_changes_nothing(page):
    page.state[tab.PRIORITY_READING_KEY] = reading_ok(3)
    page.state[tab.PRIORITY_TEXT_KEY] = "keep this"
    tab._priorities(False, SETTINGS)
    page.click("ne_prio_no")
    assert page.state.get("ne_prio_3") is False and tab.PRIORITY_READING_KEY not in page.state and page.state[tab.PRIORITY_TEXT_KEY] == "keep this"
    assert tab._priorities(False, SETTINGS).ticked == ()


def test_a_sentence_the_ai_could_not_place_shows_its_message_and_no_buttons(page):
    page.state[tab.PRIORITY_READING_KEY] = orchestrator.PriorityReading(False, orchestrator.PRIORITY_UNCLEAR)
    tab._priorities(False, SETTINGS)
    assert texts(page, "warning") == [orchestrator.PRIORITY_UNCLEAR] and "ne_prio_yes" not in page.buttons


def test_without_a_key_the_box_says_what_to_do_and_asks_nothing(page, monkeypatch):
    monkeypatch.setattr(orchestrator, "understand_priority", lambda *a, **k: pytest.fail("the AI must not be asked without a key"))
    page.state[tab.PRIORITY_TEXT_KEY] = "battery"
    tab._priorities(False, None)
    page.click("ne_prio_read")
    tab._priorities(False, None)
    assert texts(page, "warning") == [tab.NEED_A_KEY_TEXT]


def test_unticking_hides_the_sentence_that_belonged_to_the_tick(page):
    page.state[tab.PRIORITY_TYPED_KEY] = [reading_ok(3).typed]
    page.state["ne_prio_3"] = False
    tab._priorities(False, SETTINGS)
    assert not any(c.startswith("You wrote") for c in texts(page, "caption"))


def test_a_yes_with_nothing_waiting_does_nothing(page):
    tab._priorities(False, SETTINGS)
    tab._confirm_priority()
    assert tab.PRIORITY_TYPED_KEY not in page.state


def test_the_run_gets_the_request_the_buyer_built(page, monkeypatch):
    seen = {}

    class Rerun(Exception):
        pass

    @contextmanager
    def status(label, expanded=False):
        yield SimpleNamespace(update=lambda **kw: None)

    def run(proposals, terms, settings, reviewer, priorities, **kw):
        seen["priorities"] = priorities
        return orchestrator.RunResult("", "Failed", "stopped")

    def rerun():
        raise Rerun

    page.status, page.text, page.rerun = status, lambda *a, **k: None, rerun
    monkeypatch.setattr(orchestrator, "run_evaluation", run)
    request = orchestrator.PriorityRequest((3,), ())
    with pytest.raises(Rerun):
        tab._run([], None, SETTINGS, request)
    assert seen["priorities"] is request
    assert page.state[tab.sidebar.RUN_KEY] is False
