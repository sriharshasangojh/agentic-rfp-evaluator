"""Review tab tests (feature F13): what the tab works out, words and draws, and what its buttons do.

Most tests build a real batch with the orchestrator and a fake model (`review_support`), then call the tab's own functions on the
saved run. The page is drawn into a recording stand-in for Streamlit, and a click is simulated the way Streamlit does it: the
button's callback runs (it only queues what was asked), then the tab is drawn again and carries it out. The last group drives the
real app headlessly with Streamlit's AppTest (skipped when Streamlit's test runner is missing). No API key or network is used.

The tab asks "how satisfied are you, 1 to 5?": a 4 or 5 can be accepted; a 1 to 3 says what it is not satisfied with (a plain option,
or "None of the above" and their own words, which the AI reads and the person confirms) and the suppliers are ranked again.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("streamlit")

from agents import orchestrator  # noqa: E402
from db import repository, seed  # noqa: E402
from llm import client  # noqa: E402
from review_support import (APEX, BAD_FACTS, BRIGHT, FIVE, GOOD_FACTS, HORIZON, KEY, NAMES, NEXA, ORBIT, SENTINEL, SETTINGS,  # noqa: E402
                            FakeBackend, evaluated_run, rows, whole_database)
from tools import review, reweight  # noqa: E402
from ui import tab_overview, tab_review as T  # noqa: E402
from ui.tab_new_evaluation import SHOWN_RUN_KEY  # noqa: E402

try:
    from streamlit.testing.v1 import AppTest
except ImportError:   # a stand-in for Streamlit, or a very old one
    AppTest = None

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
needs_app_test = pytest.mark.skipif(AppTest is None, reason="Streamlit's test runner is not available")

TECH, PLAN, PRICE, SECURITY, SUPPORT = 1, 2, 3, 4, 5
NEED = "I want a laptop that lasts a full working day"
READING = "I understood: battery life matters more to you."
PRICE_LABEL = "The price is too high or goes over our budget"
TECH_LABEL = "The laptops should be better (battery life, speed, build quality)"
RANK_AGAIN = "Rank the suppliers again with this in mind"
UNDERSTAND = "Understand what I wrote"
YES = "Yes, rank the suppliers again"
NO = "No, let me write it again"


@pytest.fixture(autouse=True)
def seeded_database():
    seed.ensure_database()


@pytest.fixture
def run_id() -> str:
    return evaluated_run().run_id


# ---------------------------------------------------------------- reading a saved run

def view_of(run_id: str) -> T.ReviewView:
    with repository.connection() as conn:
        run = repository.get_run(conn, run_id)
        return T.build_review(run, repository.list_supplier_results(conn, run_id), repository.list_feedback(conn, run_id))


def not_satisfied(run_id, criterion=PRICE, score=2, round_no=1, typed="", actor="Sri", **extra):
    out = orchestrator.change_priorities(run_id, score, criterion, round_no, typed, actor, **extra)
    assert out.ok, out.message
    return out


def accept(run_id, score=5, round_no=1, actor="Sri"):
    out = orchestrator.accept_recommendation(run_id, score, round_no, actor)
    assert out.ok, out.message
    return out


def test_a_fresh_run_shows_the_recommendation_the_ranking_and_what_counts_for_how_much(run_id):
    view = view_of(run_id)
    assert view.status == "Evaluated" and view.recommended == APEX
    assert [(r.rank, r.name, r.ppi, r.eligible) for r in view.board] == [
        (1, APEX, 86.94, True), (2, NEXA, 86.39, True), (3, HORIZON, 86.39, True), (4, ORBIT, 75.28, True), (5, BRIGHT, 55.56, False)]
    assert view.weights == (("Technical Capability", 30.0), ("Implementation Plan", 20.0), ("Commercial Value", 20.0),
                            ("Security & Compliance", 20.0), ("Support & Experience", 10.0))
    assert view.rounds == () and view.accepted is None and view.original == () and view.original_weights == ()
    assert (view.rounds_used, view.rounds_left) == (0, 3) and view.original_recommended is None


def test_the_options_are_the_plain_sentences_one_for_each_criterion(run_id):
    options = view_of(run_id).options
    assert [(o.criterion_id, o.label) for o in options] == [
        (1, TECH_LABEL), (2, "Delivery and setup should be quicker or more dependable"), (3, PRICE_LABEL),
        (4, "We need stronger security and data protection"), (5, "We need better warranty, repair and support")]


def test_a_fresh_run_has_only_the_must_have_failures_on_its_to_do_list(run_id):
    todo = view_of(run_id).todo
    assert todo and {(i.kind, i.supplier) for i in todo} == {(review.KIND_GATE, BRIGHT)}


def test_a_run_where_everyone_is_eligible_has_nothing_to_do():
    assert view_of(evaluated_run(facts={"BrightPath Tech": GOOD_FACTS}).run_id).todo == ()


def test_a_round_shows_what_the_person_chose_what_counts_for_more_and_who_is_recommended_now(run_id):
    not_satisfied(run_id, actor="Asha")
    [r] = view_of(run_id).rounds
    assert (r.number, r.by, r.satisfaction, r.reason, r.typed, r.reading) == (1, "Asha", 2, PRICE_LABEL, False, "")
    assert r.headline == "Commercial Value now counts for 30% of the decision (it was 20%)."
    assert [(w.criterion_id, w.name, w.was, w.now) for w in r.weights] == [
        (1, "Technical Capability", 30.0, 26.25), (2, "Implementation Plan", 20.0, 17.5), (3, "Commercial Value", 20.0, 30.0),
        (4, "Security & Compliance", 20.0, 17.5), (5, "Support & Experience", 10.0, 8.75)]
    assert r.recommended_before == APEX and r.recommendation == "We now recommend NexaWorks instead of Apex Systems."
    assert [(b.name, b.rank_now, b.rank_was, b.ppi_now, b.ppi_was) for b in r.board][:3] == [
        (NEXA, 1, 2, 86.53, 86.39), (HORIZON, 2, 3, 86.53, 86.39), (APEX, 3, 1, 83.89, 86.94)]
    assert [(line.rank, line.name) for line in r.before][:2] == [(1, APEX), (2, NEXA)] and r.when.endswith("UTC")


def test_after_a_round_the_ranking_the_weights_and_the_original_are_all_there(run_id):
    not_satisfied(run_id)
    view = view_of(run_id)
    assert view.recommended == NEXA and [r.name for r in view.board][:3] == [NEXA, HORIZON, APEX]
    assert [w for _n, w in view.weights] == [26.25, 17.5, 30.0, 17.5, 8.75]
    assert [(r.rank, r.name) for r in view.original] == [(1, APEX), (2, NEXA), (3, HORIZON), (4, ORBIT), (5, BRIGHT)]
    assert view.original_recommended == APEX and view.original_weights[2] == ("Commercial Value", 20.0)
    assert (view.rounds_used, view.rounds_left) == (1, 2)


def test_the_original_stays_the_very_first_state_after_a_second_round(run_id):
    not_satisfied(run_id)
    not_satisfied(run_id, TECH, 3, 2)
    view = view_of(run_id)
    assert (view.rounds_used, view.rounds_left) == (2, 1)
    assert view.original[0].name == APEX and dict(view.original_weights) == {
        "Technical Capability": 30.0, "Implementation Plan": 20.0, "Commercial Value": 20.0, "Security & Compliance": 20.0,
        "Support & Experience": 10.0}
    assert view.rounds[1].headline == "Technical Capability now counts for 36.25% of the decision (it was 26.25%)."
    assert view.rounds[1].satisfaction == 3 and [r.number for r in view.rounds] == [1, 2]


def test_after_three_rounds_none_is_left_and_the_original_is_still_the_first_state(run_id):
    not_satisfied(run_id)
    not_satisfied(run_id, TECH, 3, 2)
    not_satisfied(run_id, SECURITY, 1, 3)
    view = view_of(run_id)
    assert (view.rounds_used, view.rounds_left) == (3, 0) and [r.number for r in view.rounds] == [1, 2, 3]
    assert view.original[0].name == APEX and view.original_weights[2] == ("Commercial Value", 20.0)
    assert view.rounds[2].satisfaction == 1 and view.rounds[2].headline.startswith("Security & Compliance now counts for ")


def test_a_typed_need_is_shown_in_the_persons_words_with_what_the_ai_understood(run_id):
    not_satisfied(run_id, TECH, 3, 1, NEED, reading=READING, proposed_id=TECH)
    [r] = view_of(run_id).rounds
    assert (r.typed, r.reason, r.reading) == (True, NEED, READING)


def test_an_acceptance_is_shown_with_who_what_and_whether_it_was_accepted_anyway(run_id):
    accept(run_id, 4, actor="Asha")
    view = view_of(run_id)
    assert view.status == "Final" and view.accepted == T.Acceptance(view.accepted.when, "Asha", 4, APEX, False)
    assert view.accepted.when.endswith("UTC")
    other = evaluated_run().run_id
    not_satisfied(other)
    not_satisfied(other, TECH, 3, 2)
    not_satisfied(other, SECURITY, 2, 3)
    accept(other, 1, 4)
    anyway = view_of(other).accepted
    assert anyway.anyway is True and anyway.satisfaction == 1 and anyway.supplier == APEX


def test_if_a_run_somehow_has_two_acceptances_the_latest_is_shown(run_id):
    with repository.connection() as conn:
        repository.add_feedback(conn, run_id, action="accept", created_at="2026-10-23T10:00:00+00:00", supplier_name=APEX, rating=5, details={"by": "Asha"})
        repository.add_feedback(conn, run_id, action="accept", created_at="2026-10-23T11:00:00+00:00", supplier_name=NEXA, rating=4, details={"by": "Ravi"})
    assert view_of(run_id).accepted == T.Acceptance("23 Oct 2026, 11:00 UTC", "Ravi", 4, NEXA, False)


def test_a_run_with_no_eligible_supplier_has_no_recommendation():
    view = view_of(evaluated_run(facts={n: BAD_FACTS for n in NAMES}).run_id)
    assert view.recommended is None and view.todo and all(not r.eligible for r in view.board)


def test_data_that_is_missing_or_odd_is_shown_without_breaking_the_tab(run_id):
    with repository.connection() as conn:
        repository.add_feedback(conn, run_id, action="reweight", created_at="not a time", details={"weights": "x", "before": [1, None], "after": 3})
        repository.add_feedback(conn, run_id, action="accept", created_at="2026-10-23T10:00:00+00:00")
        repository.add_feedback(conn, run_id, action="reweight", created_at="2026-10-23T10:01:00+00:00", rating=4, comment="  a  b ",
                                details={"weights": [{"name": "X"}, 5], "by": None, "how": "typed", "reading": None, "recommended_before": 7})
    view = view_of(run_id)
    first, second = view.rounds
    assert (first.when, first.satisfaction, first.headline, first.weights, first.board, first.before) == (
        "not a time", None, "The priorities were changed.", (), (), ())
    assert (second.by, second.reason, second.satisfaction, second.typed, second.reading, second.recommended_before) == (
        "reviewer", "a b", 4, True, "", None)
    assert second.weights == (T.WeightLine(0, "X", 0.0, 0.0),)
    assert view.accepted is not None and view.accepted.satisfaction is None and view.accepted.supplier == ""
    assert view.rounds_left == 1
    for markup in (T.round_html(first), T.round_html(second), T.history_html(view), T.weights_html(view.weights, dict(view.original_weights))):
        assert "\n" not in markup


def test_the_board_ignores_lines_it_cannot_read():
    assert T._rank_lines([{"rank": "2", "supplier": "B", "ppi": "x", "eligible": 1}, "no", None,
                          {"rank": 1, "supplier": "A", "ppi": 3.5, "eligible": True}]) == (T.RankLine(1, "A", 3.5, True), T.RankLine(2, "B", 0.0, False))
    assert T._rank_lines(None) == () and T._rank_lines(7) == () and T._rank_lines("rows") == ()
    assert T._board_lines({"after": [1, {"supplier": "Z"}]}) == (T.BoardLine("Z", 0, None, 0.0, None, False),)
    assert T._board_lines({"after": 3, "before": "x"}) == () and T._weight_lines({"weights": 5}) == ()


def test_an_evaluation_that_failed_is_on_the_to_do_list_with_the_way_out(run_id):
    with repository.connection() as conn:
        result = json.loads(conn.execute("SELECT result_json FROM supplier_results WHERE supplier_name = ?", (ORBIT,)).fetchone()[0])
        result["evaluation_failed"] = True
        conn.execute("UPDATE supplier_results SET result_json = ? WHERE supplier_name = ?", (json.dumps(result), ORBIT))
    todo = [i for i in view_of(run_id).todo if i.supplier == ORBIT]
    assert [i.kind for i in todo] == [review.KIND_FAILED] and "Run this evaluation again" in todo[0].text


def test_a_quote_that_was_not_found_is_put_on_the_to_do_list_once_evidence_checking_exists(run_id):
    with repository.connection() as conn:
        result = json.loads(conn.execute("SELECT result_json FROM supplier_results WHERE supplier_name = ?", (NEXA,)).fetchone()[0])
        result["criteria"][2]["evidence_found"] = False
        result["criteria"][1]["evidence_found"] = "maybe"
        conn.execute("UPDATE supplier_results SET result_json = ? WHERE supplier_name = ?", (json.dumps(result), NEXA))
    todo = [i for i in view_of(run_id).todo if i.supplier == NEXA]
    assert [(i.kind, i.text) for i in todo] == [(review.KIND_EVIDENCE, "Commercial Value: the quoted evidence was not found in the proposal.")]


# ---------------------------------------------------------------- the HTML pieces

def assert_safe(markup: str) -> None:
    assert "\n" not in markup and "<script" not in markup.lower() and "<img" not in markup.lower() and "<h1" not in markup.lower()


def test_the_to_do_list_escapes_every_text_and_is_one_line():
    items = (review.TodoItem(review.KIND_GATE, "<script>alert(1)</script>", None, "<img src=x onerror=1> & more"),)
    markup = T.todo_html(items)
    assert_safe(markup)
    assert "&lt;script&gt;" in markup and "&amp; more" in markup


@pytest.mark.parametrize("kind, label", [(review.KIND_FAILED, "Evaluation failed"), (review.KIND_GATE, "Not eligible"),
                                         (review.KIND_EVIDENCE, "Evidence not found")])
def test_every_kind_of_to_do_has_its_own_chip_word(kind, label):
    assert f">{label}</span>" in T.todo_html((review.TodoItem(kind, "Apex", 1, "x"),))


def test_the_ranking_marks_the_recommended_supplier_and_says_who_is_not_eligible(run_id):
    board = view_of(run_id).board
    markup = T.ranking_html(board, APEX)
    assert_safe(markup)
    assert markup.count("(recommended)") == 1 and f"{APEX} (recommended)" in markup and markup.count('class="rv-now"') == 1
    assert markup.count(">Not eligible<") == 1 and markup.count(">Eligible<") == 4 and "86.94" in markup
    assert "(recommended)" not in T.ranking_html(board, None) and 'class="rv-now"' not in T.ranking_html(board)


def test_the_ranking_escapes_supplier_names():
    markup = T.ranking_html((T.RankLine(1, "Evil <script>alert(1)</script> Ltd", 80.0, True),), "Evil <script>alert(1)</script> Ltd")
    assert_safe(markup)
    assert "&lt;script&gt;" in markup


def test_the_weights_table_shows_each_area_and_how_much_it_counts_for(run_id):
    markup = T.weights_html(view_of(run_id).weights)
    assert_safe(markup)
    assert '<td>Commercial Value</td><td class="rv-num">20%</td>' in markup and "started at" not in markup and "unchanged" not in markup
    assert markup.count("<tr>") == 6 and "<th>Counts for</th>" in markup


def test_after_a_round_the_weights_table_shows_where_each_area_started(run_id):
    not_satisfied(run_id)
    view = view_of(run_id)
    markup = T.weights_html(view.weights, dict(view.original_weights))
    assert '<td>Commercial Value</td><td class="rv-num">30%</td><td class="rv-num rv-sub">started at 20%</td>' in markup
    assert '<td>Support &amp; Experience</td><td class="rv-num">8.75%</td><td class="rv-num rv-sub">started at 10%</td>' in markup
    same = T.weights_html((("A", 20.0),), {"A": 20.0})
    assert "unchanged" in same and "started at" not in same
    assert '<td class="rv-num">-</td>' in T.weights_html((("A", None),))


def test_the_what_changed_box_says_what_was_chosen_what_counts_for_more_and_who_is_recommended(run_id):
    not_satisfied(run_id, actor="Asha")
    markup = T.round_html(view_of(run_id).rounds[0])
    assert_safe(markup)
    assert "Commercial Value now counts for 30% of the decision (it was 20%)." in markup
    assert "Round 1 · " in markup and " · Asha</div>" in markup
    assert f"Satisfaction: 2 out of 5. You chose: “{PRICE_LABEL}”." in markup
    assert "<b>We now recommend NexaWorks instead of Apex Systems.</b>" in markup
    assert "(up from 2)" in markup and "(down from 1)" in markup and "86.53 (was 86.39)" in markup and "83.89 (was 86.94)" in markup
    assert markup.count("<td>Eligible</td>") == 4 and markup.count("<td>Not eligible</td>") == 1
    assert "<th>Counted for</th>" in markup and '<td class="rv-num">17.5%</td>' in markup


def test_the_words_and_the_tables_of_a_round_can_be_drawn_apart_and_together_make_the_whole_box(run_id):
    not_satisfied(run_id, actor="Asha")
    round_ = view_of(run_id).rounds[0]
    words, tables = T.round_summary_html(round_), T.round_tables_html(round_)
    assert_safe(words)
    assert_safe(tables)
    assert "<table" not in words and "Commercial Value now counts for 30%" in words and "<b>We now recommend NexaWorks instead of Apex Systems.</b>" in words
    assert "Commercial Value now counts" not in tables and "<th>Counted for</th>" in tables and "<th>Rank now</th>" in tables
    assert tables.count("<table") == 2
    assert T.round_html(round_) == words + tables


def test_a_round_that_changed_nothing_in_the_weights_has_only_the_ranking_table(run_id):
    not_satisfied(run_id)
    round_ = view_of(run_id).rounds[0]
    same = T.Round(round_.number, round_.when, round_.by, round_.satisfaction, round_.reason, round_.typed, round_.reading, round_.headline,
                   tuple(T.WeightLine(w.criterion_id, w.name, w.now, w.now) for w in round_.weights), round_.board, round_.before,
                   round_.recommended_before, round_.recommendation)
    assert T.round_tables_html(same).count("<table") == 1 and "<th>Counted for</th>" not in T.round_tables_html(same)


def test_the_recommendation_panel_names_the_supplier_and_its_score(run_id):
    view = view_of(run_id)
    markup = T.recommendation_html(view.recommended, view.board)
    assert_safe(markup)
    assert "We recommend Apex Systems" in markup and "Score against the other bids: 86.94" in markup and "<table" not in markup


def test_the_recommendation_panel_escapes_the_name_and_is_empty_without_a_recommendation():
    markup = T.recommendation_html("<script>x</script> Ltd", (T.RankLine(1, "<script>x</script> Ltd", 80.0, True),))
    assert_safe(markup)
    assert "&lt;script&gt;" in markup
    assert T.recommendation_html(None, ()) == "" and T.recommendation_html("", ()) == ""
    assert "Score against" not in T.recommendation_html("Someone", (T.RankLine(1, "Other", 80.0, True),))


@pytest.mark.parametrize("rounds, accepted, label", [(1, False, "History (1 round)"), (3, False, "History (3 rounds)"),
                                                     (0, True, "History (accepted)"), (2, True, "History (2 rounds, accepted)")])
def test_the_history_drop_down_says_how_many_rounds_and_whether_it_was_accepted(run_id, rounds, accepted, label):
    for number, criterion in enumerate((PRICE, TECH, SECURITY)[:rounds], start=1):
        not_satisfied(run_id, criterion, 2, number)
    if accepted:
        accept(run_id, 4, rounds + 1)
    assert T.history_label(view_of(run_id)) == label


def test_a_typed_need_is_quoted_with_the_ais_reading_in_the_what_changed_box(run_id):
    not_satisfied(run_id, TECH, 3, 1, NEED, reading=READING, proposed_id=TECH)
    markup = T.round_html(view_of(run_id).rounds[0])
    assert f"Satisfaction: 3 out of 5. You wrote: “{NEED}”. {READING}" in markup


def test_the_what_changed_box_escapes_and_copes_with_nothing_to_show():
    round_ = T.Round(1, "now", "<b>Sri</b>", None, "<script>x</script>", True, "<i>read</i>", "<h1>Headline</h1>", (), (), (), None, "<u>Rec</u>")
    markup = T.round_html(round_)
    assert_safe(markup)
    assert "&lt;script&gt;" in markup and "&lt;b&gt;Sri&lt;/b&gt;" in markup and "&lt;h1&gt;" in markup
    assert "&lt;u&gt;Rec&lt;/u&gt;" in markup and "&lt;i&gt;read&lt;/i&gt;" in markup and "<u>" not in markup and "<i>" not in markup
    assert "<table" not in markup and "Satisfaction:" not in markup


def test_the_history_tells_each_round_in_one_line_and_then_the_acceptance(run_id):
    not_satisfied(run_id, actor="Asha")
    not_satisfied(run_id, TECH, 3, 2, NEED, reading=READING, proposed_id=TECH, actor="Asha")
    not_satisfied(run_id, SECURITY, 1, 3, actor="Asha")
    accept(run_id, 2, 4, actor="Asha")
    markup = T.history_html(view_of(run_id))
    assert_safe(markup)
    assert markup.count("<li>") == 4
    assert f"Satisfaction 2 out of 5, chose “{PRICE_LABEL}”. Commercial Value now counts for 30%" in markup
    assert f"Satisfaction 3 out of 5, wrote “{NEED}”." in markup
    assert "Accepted Apex Systems with a satisfaction of 2 out of 5 (accepted although not satisfied: all rounds were used)." in markup


def test_the_history_escapes_what_the_person_wrote(run_id):
    not_satisfied(run_id, PRICE, 2, 1, "<script>alert(1)</script> too dear")
    markup = T.history_html(view_of(run_id))
    assert_safe(markup)
    assert "&lt;script&gt;" in markup


# ---------------------------------------------------------------- a stand-in for the parts of Streamlit this tab uses

class _Rerun(Exception):
    """Stands for Streamlit stopping the script and starting it again."""


class _Block:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _DropDown:
    """What `st.expander` returns: while it is open, everything drawn is recorded as being inside it."""

    def __init__(self, page: _Page, entry: dict[str, Any]) -> None:
        self.page, self.entry = page, entry

    def __enter__(self):
        self.page.open_block = self.entry
        return self

    def __exit__(self, *_exc):
        self.page.open_block = None
        return False


class _Page:
    def __init__(self, session: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.visible: list[tuple[str, str, dict[str, Any]]] = []      # what is drawn outside every drop-down
        self.drop_downs: list[dict[str, Any]] = []                    # {"label", "expanded", "calls", "widgets"} for each st.expander
        self.open_block: dict[str, Any] | None = None
        self.session_state: dict[str, Any] = dict(session or {})
        self.buttons: dict[str, dict[str, Any]] = {}
        self.selectboxes: list[dict[str, Any]] = []
        self.radios: list[dict[str, Any]] = []
        self.reruns = 0

    def _record(self, kind, text="", **kwargs):
        call = (kind, str(text), kwargs)
        self.calls.append(call)
        if self.open_block is None:
            self.visible.append(call)
        else:
            self.open_block["calls"].append(call)
            if kind in ("button", "text_area"):
                self.open_block["widgets"] += 1

    def _widget(self):
        if self.open_block is not None:
            self.open_block["widgets"] += 1

    def expander(self, label, expanded=False, **kwargs):
        entry = {"label": label, "expanded": expanded, "calls": [], "widgets": 0, **kwargs}
        self.drop_downs.append(entry)
        return _DropDown(self, entry)

    def markdown(self, text, **kwargs):
        self._record("markdown", text, **kwargs)

    def divider(self, **kwargs):
        self._record("divider", "", **kwargs)

    def info(self, text, **kwargs):
        self._record("info", text, **kwargs)

    def error(self, text, **kwargs):
        self._record("error", text, **kwargs)

    def success(self, text, **kwargs):
        self._record("success", text, **kwargs)

    def spinner(self, text="", **kwargs):
        self._record("spinner", text, **kwargs)
        return _Block()

    def columns(self, count, **kwargs):
        self._record("columns", count, **kwargs)
        return [_Block() for _ in range(count)]

    def rerun(self):
        self.reruns += 1
        raise _Rerun

    def radio(self, label, options, index=0, key=None, format_func=str, horizontal=False, **kwargs):
        self._widget()
        self.radios.append({"label": label, "options": list(options), "labels": [format_func(o) for o in options], "key": key,
                            "index": index, "horizontal": horizontal, **kwargs})
        if index is not None:
            self.session_state.setdefault(key, list(options)[index])
        return self.session_state.get(key)

    def selectbox(self, label, options, index=0, key=None, format_func=str, **kwargs):
        self._widget()
        self.selectboxes.append({"label": label, "options": list(options), "labels": [format_func(o) for o in options], "key": key,
                                 "index": index})
        self.session_state.setdefault(key, list(options)[index])
        return self.session_state[key]

    def text_area(self, label, key=None, **kwargs):
        self._record("text_area", label, key=key, **kwargs)
        return self.session_state.get(key, "")

    def button(self, label, key=None, **kwargs):
        self._record("button", label, key=key, **kwargs)
        self.buttons[key] = {"label": label, **kwargs}
        return False

    def download_button(self, label, data=None, key=None, **kwargs):
        self._record("download_button", label, key=key, size=len(data), **kwargs)
        return False

    def link_button(self, label, url, key=None, **kwargs):
        self._record("link_button", label, url=url, key=key, **kwargs)

    def text(self, *kinds: str) -> str:
        return "\n".join(text for kind, text, _kw in self.calls if not kinds or kind in kinds)

    def labels(self, kind: str) -> list[str]:
        return [text for k, text, _kw in self.calls if k == kind]

    def shown(self, *kinds: str) -> str:
        """Only what is on the page itself, not what sits in a drop-down."""
        return "\n".join(text for kind, text, _kw in self.visible if not kinds or kind in kinds)

    def drop_down_labels(self) -> list[str]:
        return [d["label"] for d in self.drop_downs]

    def drop_down(self, label: str) -> dict[str, Any]:
        found = [d for d in self.drop_downs if d["label"] == label]
        assert len(found) == 1, (label, self.drop_down_labels())
        return found[0]

    def inside(self, label: str) -> str:
        return "\n".join(text for _k, text, _kw in self.drop_down(label)["calls"])

    def button_labelled(self, label: str) -> dict[str, Any]:
        found = [b for b in self.buttons.values() if b["label"] == label]
        assert len(found) == 1, (label, [b["label"] for b in self.buttons.values()])
        return found[0]


def open_page(monkeypatch, run_id: str | None, *, session=None, settings=None) -> _Page:
    page = _Page({**({SHOWN_RUN_KEY: run_id} if run_id else {}), **(session or {})})
    monkeypatch.setattr(T, "st", page)
    monkeypatch.setattr(T.sidebar, "get_llm_settings", lambda: settings)
    return page


def ai_page(monkeypatch, run_id: str, *replies, session=None) -> tuple[_Page, FakeBackend]:
    """A page whose sidebar holds a key and model, and a model that returns the prepared replies (the tab never builds a backend)."""
    backend = FakeBackend(*replies)
    monkeypatch.setattr(client, "_LiteLLMBackend", lambda: backend)
    return open_page(monkeypatch, run_id, session=session, settings=SETTINGS), backend


def draw(page: _Page) -> _Page:
    page.calls, page.visible, page.drop_downs, page.open_block = [], [], [], None
    page.buttons, page.selectboxes, page.radios = {}, [], []
    T.render()
    # the negotiation section (F20) has its own tests (test_negotiation.py); here it is taken out of what the page shows
    page.negotiation_buttons = {k: v for k, v in page.buttons.items() if v["label"] == T.NEGOTIATION_BUTTON}
    for key in page.negotiation_buttons:
        del page.buttons[key]
    ours = (f"#### {T.NEGOTIATION_TITLE}", T.NEGOTIATION_BUTTON)
    for group in (page.calls, page.visible):
        group[:] = [c for c in group if c[1] not in ours and T.NEGOTIATION_INTRO not in c[1] and T.NEGOTIATION_LOCKED not in c[1]]
    return page


def keys_of(page: _Page, round_no: int = 1) -> dict[str, str]:
    return T._keys(page.session_state[SHOWN_RUN_KEY], round_no)


def answer(page: _Page, score=None, why=None, text=None, pick=None, round_no: int = 1) -> None:
    """Fill in the widgets the way a person would."""
    keys = keys_of(page, round_no)
    for name, value in (("score", score), ("why", why), ("text", text), ("pick", pick)):
        if value is not None:
            page.session_state[keys[name]] = value


def queue(page: _Page, label: str) -> None:
    """Draw the page (so the button shows its state) and click the button: its callback queues the request."""
    draw(page)
    button = page.button_labelled(label)
    assert not button.get("disabled"), label
    button["on_click"](*button.get("args", ()), **button.get("kwargs", {}))


def click(page: _Page, label: str) -> None:
    """A click followed by the redraw that carries the request out."""
    queue(page, label)
    with pytest.raises(_Rerun):
        draw(page)


def ai(action="reweight", criterion_id=TECH, reading=READING) -> str:
    return json.dumps({"action": action, "supplier": None, "criterion_id": criterion_id, "reading": reading})


def headings(page: _Page) -> list[str]:
    return [t for k, t, _kw in page.calls if k == "markdown" and t.startswith("####")]


def message_of(page: _Page) -> tuple[bool, str]:
    return page.session_state[T.MESSAGE_KEY]


def feedback_rows() -> list[dict[str, Any]]:
    return rows("SELECT * FROM reviewer_feedback ORDER BY id")


# ---------------------------------------------------------------- the page drawn

def test_the_tab_is_called_re_evaluation_in_the_app():
    source = APP_PATH.read_text(encoding="utf-8")
    assert '("Re-evaluation", tab_review)' in source and '("Review"' not in source


def test_before_any_run_the_tab_explains_what_to_do(monkeypatch):
    page = draw(open_page(monkeypatch, None))
    assert [k for k, _t, _kw in page.calls] == ["info"] and page.text() == T.EMPTY_TEXT
    assert "satisfied" in T.EMPTY_TEXT and "New evaluation" in T.EMPTY_TEXT


def test_a_run_that_cannot_be_found_gets_a_friendly_message(monkeypatch):
    page = draw(open_page(monkeypatch, "RUN-DOES-NOT-EXIST"))
    assert [k for k, _t, _kw in page.calls] == ["info"] and page.text() == T.NOT_FOUND_TEXT


def test_a_database_that_cannot_be_read_gets_a_friendly_message_not_a_traceback(monkeypatch, run_id):
    page = open_page(monkeypatch, run_id)

    def broken(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repository, "get_run", broken)
    draw(page)
    assert [k for k, _t, _kw in page.calls] == ["error"] and page.text() == T.DATABASE_ERROR_TEXT and "rfp_evaluation.db" in T.DATABASE_ERROR_TEXT


def test_the_page_shows_the_recommendation_and_the_question_and_keeps_the_tables_in_closed_drop_downs(monkeypatch, run_id):
    page = draw(open_page(monkeypatch, run_id))
    assert headings(page) == ["#### Where things stand", "#### Your answer", "#### More detail", "#### Report"]
    assert T.INTRO_TEXT in page.labels("markdown")
    assert "We recommend Apex Systems" in page.shown("markdown") and "Score against the other bids: 86.94" in page.shown("markdown")
    assert page.drop_down_labels() == ["To do (2)", "Full ranking (5 suppliers)", "What counts for how much"]
    assert [d["expanded"] for d in page.drop_downs] == [False, False, False]
    assert "<table" not in page.shown()                                # no table on the page itself
    assert "Not eligible" in page.inside("To do (2)") and "BrightPath" in page.inside("To do (2)")
    assert "(recommended)" in page.inside("Full ranking (5 suppliers)") and "86.94" in page.inside("Full ranking (5 suppliers)")
    assert "Commercial Value" in page.inside("What counts for how much") and "20%" in page.inside("What counts for how much")
    assert [d["widgets"] for d in page.drop_downs] == [0, 0, 0]       # the question is never hidden in a drop-down
    assert len(page.radios) == 1 and page.buttons == {} and page.selectboxes == []
    radio = page.radios[0]
    assert radio["label"] == T.QUESTION_TEXT == "On a scale of 1 to 5, how satisfied are you with this recommendation?"
    assert radio["options"] == [1, 2, 3, 4, 5] and radio["index"] is None and radio["horizontal"] is True and radio["key"] == keys_of(page)["score"]
    assert T.SCALE_HINT in page.text("markdown") and T.CHOOSE_TEXT in page.text("markdown")
    assert "What changed last" not in page.text("markdown") and not any(l.startswith("History") for l in page.drop_down_labels())
    assert not any(l.startswith("Last round") for l in page.drop_down_labels())


def test_without_anything_to_do_there_is_no_to_do_drop_down_and_the_page_says_so(monkeypatch):
    page = draw(open_page(monkeypatch, evaluated_run(facts={"BrightPath Tech": GOOD_FACTS}).run_id))
    assert not any(label.startswith("To do") for label in page.drop_down_labels()) and T.NOTHING_TO_DO_TEXT in page.shown("markdown")


def test_the_page_never_asks_the_person_to_mark_or_rate_anything_but_their_own_satisfaction(monkeypatch, run_id):
    page = open_page(monkeypatch, run_id)
    banned = re.compile(r"\b(stars?|rate|rates|rating|ratings|override|re-evaluate|re-evaluation)\b|keep the mark|change the mark|change this mark|new mark|★")
    for score, why in ((None, None), (5, None), (2, None), (2, TECH), (2, T.NONE_CHOICE)):
        answer(page, score=score, why=why)
        shown_text = draw(page).text().lower() + " " + " ".join(b["label"].lower() for b in page.buttons.values())
        assert banned.search(shown_text) is None, (score, why, banned.search(shown_text))


@pytest.mark.parametrize("score", [4, 5])
def test_choosing_a_score_of_4_or_5_offers_to_accept_and_nothing_else(monkeypatch, run_id, score):
    page = open_page(monkeypatch, run_id)
    answer(page, score=score)
    draw(page)
    assert page.labels("button") == [f"Accept {APEX}"] and len(page.radios) == 1 and page.selectboxes == []
    assert T.HAPPY_TEXT in page.text("markdown")
    assert page.buttons[f"{keys_of(page)['score']}_accept"]["type"] == "primary"


@pytest.mark.parametrize("score", [1, 2, 3])
def test_choosing_a_score_below_4_asks_what_is_not_right_in_plain_words(monkeypatch, run_id, score):
    page = open_page(monkeypatch, run_id)
    answer(page, score=score)
    draw(page)
    assert T.UNHAPPY_TEXT in page.text("markdown") and f"**{T.WHY_TEXT}**" in page.labels("markdown")
    assert page.buttons == {} and len(page.radios) == 2
    why = page.radios[1]
    assert why["options"] == [1, 2, 3, 4, 5, T.NONE_CHOICE] and why["index"] is None
    assert why["labels"] == [TECH_LABEL, "Delivery and setup should be quicker or more dependable", PRICE_LABEL,
                             "We need stronger security and data protection", "We need better warranty, repair and support",
                             "None of the above, I will write it myself"]
    assert why["key"] == keys_of(page)["why"] and why["label_visibility"] == "collapsed"
    assert "Choose what is closest" in page.text("markdown")


def test_the_options_never_use_the_words_of_the_marking_scheme(monkeypatch, run_id):
    page = open_page(monkeypatch, run_id)
    answer(page, score=2)
    labels = " ".join(draw(page).radios[1]["labels"]).lower()
    for word in ("criterion", "weight", "capability", "implementation", "compliance", "commercial", "mark"):
        assert word not in labels, word


def test_picking_an_option_offers_to_rank_again_and_nothing_is_changed_until_the_click(monkeypatch, run_id):
    page = open_page(monkeypatch, run_id)
    answer(page, score=2, why=PRICE)
    draw(page)
    assert page.labels("button") == [RANK_AGAIN] and page.selectboxes == []
    assert feedback_rows() == [] and view_of(run_id).recommended == APEX


def test_clicking_rank_again_changes_the_priorities_shows_the_new_result_and_starts_a_fresh_question(monkeypatch, run_id):
    page = open_page(monkeypatch, run_id, session={"reviewer_name": "Asha"})
    answer(page, score=2, why=PRICE)
    click(page, RANK_AGAIN)
    ok, message = message_of(page)
    assert ok and message == ("Commercial Value now counts for 30% of the decision (it was 20%). "
                              "We now recommend NexaWorks instead of Apex Systems.")
    assert [k for k in page.session_state if k.startswith(f"rv_{run_id}_1")] == []        # the typed answers were cleared
    draw(page)
    assert page.text("success", "error") == "" and T.MESSAGE_KEY not in page.session_state       # the page itself shows the result
    assert message not in page.shown("markdown") and "Commercial Value now counts for 30% of the decision (it was 20%)." in page.shown("markdown")
    assert headings(page) == ["#### Where things stand", "#### What changed last", "#### Your answer", "#### More detail", "#### Report"]
    assert "We recommend NexaWorks" in page.shown("markdown")
    assert page.drop_down_labels() == ["To do (2)", "Full ranking (5 suppliers)", "What counts for how much", "Last round in detail (round 1)",
                                       "History (1 round)"]
    assert page.radios[0]["key"] == keys_of(page, 2)["score"] and page.radios[0]["index"] is None
    assert T.ROUNDS_TEXT.format(number=2, total=3) == "Round 2 of 3" and "Round 2 of 3" in page.shown("markdown")
    assert json.loads(feedback_rows()[0]["details_json"])["by"] == "Asha"


def test_the_what_changed_box_and_the_history_show_the_round(monkeypatch, run_id):
    page = open_page(monkeypatch, run_id)
    answer(page, score=2, why=PRICE)
    click(page, RANK_AGAIN)
    draw(page)
    shown = page.shown("markdown")
    assert "Commercial Value now counts for 30% of the decision (it was 20%)." in shown and "We now recommend NexaWorks instead of Apex Systems." in shown
    assert f"Satisfaction: 2 out of 5. You chose: “{PRICE_LABEL}”." in shown
    assert "<table" not in page.shown()                                # the tables of the round are one click away, not on the page
    detail = page.inside("Last round in detail (round 1)")
    assert "<th>Counted for</th>" in detail and "<th>Rank now</th>" in detail and "86.53 (was 86.39)" in detail
    assert "started at 20%" in page.inside("What counts for how much") and "(recommended)" in page.inside("Full ranking (5 suppliers)")
    history = page.inside("History (1 round)")
    assert "**The original ranking**" in history and "Apex Systems (recommended)" in history
    assert f"Satisfaction 2 out of 5, chose “{PRICE_LABEL}”" in history and "Satisfaction 2 out of 5" not in shown


def test_a_second_and_third_round_work_from_the_one_before_and_then_only_accepting_is_left(monkeypatch, run_id):
    page = open_page(monkeypatch, run_id)
    answer(page, score=2, why=PRICE)
    click(page, RANK_AGAIN)
    answer(page, score=3, why=TECH, round_no=2)
    click(page, RANK_AGAIN)
    draw(page)
    assert "Round 3 of 3" in page.shown("markdown") and len(page.radios) == 1           # one round is still left
    answer(page, score=1, why=SECURITY, round_no=3)
    draw(page)
    assert page.labels("button") == [RANK_AGAIN]
    click(page, RANK_AGAIN)
    assert [json.loads(r["details_json"])["round"] for r in feedback_rows()] == [1, 2, 3]
    draw(page)
    assert page.radios[0]["key"] == keys_of(page, 4)["score"] and orchestrator.ROUNDS_USED_TEXT in page.text("markdown")
    assert "Last round in detail (round 3)" in page.drop_down_labels() and "History (3 rounds)" in page.drop_down_labels()
    answer(page, score=2, round_no=4)
    draw(page)
    assert page.labels("button") == [f"Accept {APEX} anyway"] and len(page.radios) == 1
    assert page.text("markdown").count(orchestrator.ROUNDS_USED_TEXT) == 1          # said once, not again after the score


def test_even_a_4_or_5_after_all_three_rounds_accepts_the_normal_way(monkeypatch, run_id):
    not_satisfied(run_id)
    not_satisfied(run_id, TECH, 3, 2)
    not_satisfied(run_id, SECURITY, 2, 3)
    page = open_page(monkeypatch, run_id)
    answer(page, score=4, round_no=4)
    draw(page)
    assert page.labels("button") == [f"Accept {APEX}"]


def test_accepting_makes_the_run_final_and_the_page_says_so_without_the_question(monkeypatch, run_id):
    page = open_page(monkeypatch, run_id)
    answer(page, score=5)
    click(page, f"Accept {APEX}")
    assert message_of(page) == (True, "You accepted Apex Systems. This evaluation is now final.")
    draw(page)
    [final] = page.labels("success")                                  # said once, not again as a message above it
    assert final.startswith("You accepted Apex Systems on ") and final.endswith(" UTC. This evaluation is final.")
    assert page.radios == [] and page.buttons == {} and "#### Your answer" not in headings(page)
    assert page.drop_down_labels()[-1] == "History (accepted)" and "Accepted Apex Systems" in page.inside("History (accepted)")
    assert view_of(run_id).status == "Final"


def test_a_run_that_is_no_longer_evaluated_shows_the_history_but_no_question(monkeypatch, run_id):
    with repository.connection() as conn:
        repository.set_run_status(conn, run_id, "Failed")
    page = draw(open_page(monkeypatch, run_id))
    assert T.CLOSED_TEXT.format(status="failed") in page.text("info") and page.buttons == {} and page.radios == []


def test_with_no_eligible_supplier_there_is_nothing_to_react_to(monkeypatch):
    page = draw(open_page(monkeypatch, evaluated_run(facts={n: BAD_FACTS for n in NAMES}).run_id))
    assert T.NO_RECOMMENDATION_TEXT in page.text("info") and page.radios == [] and page.buttons == {}
    assert not any("We recommend" in t for t in page.labels("markdown"))


def test_a_refusal_is_shown_once_and_a_success_or_an_empty_message_is_not_repeated(monkeypatch, run_id):
    page = open_page(monkeypatch, run_id, session={T.MESSAGE_KEY: (True, "Saved it.")})
    assert "Saved it." not in draw(page).text() and T.MESSAGE_KEY not in page.session_state      # the page shows the result itself
    page.session_state[T.MESSAGE_KEY] = (False, "Could not.")
    assert draw(page).text("error") == "Could not." and T.MESSAGE_KEY not in page.session_state
    assert "Could not." not in draw(page).text()
    page.session_state[T.MESSAGE_KEY] = (True, "")
    assert draw(page).text("success", "error") == ""
    page.session_state[T.MESSAGE_KEY] = "odd"
    assert draw(page).text("success", "error") == ""


def test_a_supplier_name_with_html_is_never_drawn_as_html(monkeypatch):
    evil = "Evil <script>alert(1)</script> Ltd"
    page = open_page(monkeypatch, evaluated_run([(evil, *FIVE[0][1:]), *FIVE[1:]]).run_id)
    answer(page, score=5)
    draw(page)
    for kind, text, kw in page.calls:
        if kind == "markdown" and kw.get("unsafe_allow_html"):
            assert "<script" not in text, text
    assert "&lt;script&gt;" in page.text("markdown") and page.labels("button") == [f"Accept {evil}"]


def test_each_piece_with_html_is_drawn_with_html_allowed_and_plain_headings_are_not(monkeypatch, run_id):
    page = open_page(monkeypatch, run_id)
    answer(page, score=2, why=PRICE)
    click(page, RANK_AGAIN)
    draw(page)
    for kind, text, kw in page.calls:
        if kind == "markdown" and "<" in text:
            assert kw.get("unsafe_allow_html") is True, text
        if kind == "markdown" and text.startswith("####"):
            assert not kw.get("unsafe_allow_html")


# ---------------------------------------------------------------- "None of the above": the person's own words

def test_none_of_the_above_opens_a_box_for_their_own_words_and_the_button_waits_for_some(monkeypatch, run_id):
    page = open_page(monkeypatch, run_id)
    answer(page, score=2, why=T.NONE_CHOICE)
    draw(page)
    [box] = [kw for k, _t, kw in page.calls if k == "text_area"]
    assert box["key"] == keys_of(page)["text"] and box["max_chars"] == 300 and box["placeholder"] == T.WRITE_HINT
    assert page.labels("text_area") == [T.WRITE_LABEL]
    read = f"{keys_of(page)['why']}_read"
    assert page.buttons[read]["disabled"] is True and page.buttons[read]["label"] == UNDERSTAND
    answer(page, text="   ")
    assert draw(page).buttons[read]["disabled"] is True
    answer(page, text=NEED)
    assert draw(page).buttons[read]["disabled"] is False


def test_without_a_key_the_person_is_told_what_to_do_and_nothing_is_sent(monkeypatch, run_id):
    page = open_page(monkeypatch, run_id)
    answer(page, score=2, why=T.NONE_CHOICE, text=NEED)
    assert T.NO_KEY_TEXT in draw(page).text("markdown")
    click(page, UNDERSTAND)
    assert message_of(page) == (False, T.NO_KEY_TEXT) and T.PROPOSAL_KEY not in page.session_state
    assert rows("SELECT * FROM audit_log WHERE action LIKE 'need_%'") == []


def test_with_a_key_the_ai_reads_the_words_and_the_person_confirms_before_anything_changes(monkeypatch, run_id):
    page, backend = ai_page(monkeypatch, run_id, ai())
    answer(page, score=3, why=T.NONE_CHOICE, text=NEED)
    assert T.NO_KEY_TEXT not in draw(page).text("markdown")
    click(page, UNDERSTAND)
    assert message_of(page) == (True, "") and len(backend.calls) == 1
    assert page.session_state[T.PROPOSAL_KEY] == {"run_id": run_id, "round_no": 1, "text": NEED, "criterion_id": TECH, "reading": READING, "leaning": None}
    assert feedback_rows() == [] and view_of(run_id).recommended == APEX       # nothing has changed yet
    draw(page)
    assert page.text("success", "error") == ""
    assert any(READING in t for t in page.labels("markdown")) and T.CONFIRM_HINT in page.text("markdown")
    [pick] = page.selectboxes
    assert pick["label"] == T.CONFIRM_LABEL and pick["options"] == [1, 2, 3, 4, 5] and pick["index"] == 0 and pick["labels"][0] == TECH_LABEL
    assert page.session_state[keys_of(page)["pick"]] == TECH
    assert page.buttons[f"{keys_of(page)['pick']}_yes"]["label"] == YES
    assert page.buttons[f"{keys_of(page)['pick']}_no"]["label"] == NO
    assert page.session_state[keys_of(page)["text"]] == NEED                                           # the words stay for editing


def test_the_ai_is_sent_the_words_and_the_areas_but_never_the_key(monkeypatch, run_id):
    page, backend = ai_page(monkeypatch, run_id, ai())
    answer(page, score=3, why=T.NONE_CHOICE, text=NEED)
    click(page, UNDERSTAND)
    sent = backend.sent_text()
    assert NEED in sent and "Technical Capability" in sent and KEY not in sent and SENTINEL not in sent


def test_confirming_ranks_again_with_the_ais_area_and_keeps_the_persons_words(monkeypatch, run_id):
    page, _backend = ai_page(monkeypatch, run_id, ai())
    answer(page, score=3, why=T.NONE_CHOICE, text=NEED)
    click(page, UNDERSTAND)
    click(page, YES)
    ok, message = message_of(page)
    assert ok and message.startswith("Technical Capability now counts for 40% of the decision (it was 30%).")
    [row] = feedback_rows()
    assert (row["action"], row["criterion_id"], row["rating"], row["comment"]) == ("reweight", TECH, 3, NEED)
    details = json.loads(row["details_json"])
    assert (details["how"], details["typed"], details["reading"], details["proposed_criterion_id"]) == ("typed", NEED, READING, TECH)
    assert T.PROPOSAL_KEY not in page.session_state and [k for k in page.session_state if k.startswith(f"rv_{run_id}_1")] == []


def test_the_area_the_ai_proposed_is_the_one_preselected_whatever_its_place_in_the_list(monkeypatch, run_id):
    page, _backend = ai_page(monkeypatch, run_id, ai(criterion_id=PRICE, reading="I understood: the price matters more to you."))
    answer(page, score=2, why=T.NONE_CHOICE, text="It costs too much")
    click(page, UNDERSTAND)
    draw(page)
    [pick] = page.selectboxes
    assert pick["index"] == 2 and pick["labels"][2] == PRICE_LABEL and page.session_state[keys_of(page)["pick"]] == PRICE
    click(page, YES)
    [row] = feedback_rows()
    assert (row["criterion_id"], json.loads(row["details_json"])["proposed_criterion_id"]) == (PRICE, PRICE)


def test_reading_the_words_again_starts_from_the_new_proposal_not_from_an_old_pick(monkeypatch, run_id):
    page, _backend = ai_page(monkeypatch, run_id, ai(criterion_id=TECH), ai(criterion_id=SUPPORT, reading="I understood: support matters more to you."))
    answer(page, score=3, why=T.NONE_CHOICE, text=NEED)
    click(page, UNDERSTAND)
    draw(page)
    answer(page, pick=PRICE)                                        # the person picked another area ...
    answer(page, text="It lasts long, but I also need good repair support")
    click(page, UNDERSTAND)                                         # ... then wrote it a different way and had it read again
    draw(page)
    [pick] = page.selectboxes
    assert page.session_state[keys_of(page)["pick"]] == SUPPORT and pick["index"] == 4
    assert page.session_state[T.PROPOSAL_KEY]["criterion_id"] == SUPPORT


def test_the_person_may_pick_another_area_than_the_one_the_ai_proposed(monkeypatch, run_id):
    page, _backend = ai_page(monkeypatch, run_id, ai())
    answer(page, score=3, why=T.NONE_CHOICE, text="It lasts long but costs too much")
    click(page, UNDERSTAND)
    draw(page)
    answer(page, pick=PRICE)
    click(page, YES)
    [row] = feedback_rows()
    details = json.loads(row["details_json"])
    assert (row["criterion_id"], details["criterion"], details["proposed_criterion_id"], details["how"]) == (PRICE, "Commercial Value", TECH, "typed")


def test_no_let_me_write_it_again_drops_the_proposal_and_keeps_the_words(monkeypatch, run_id):
    page, _backend = ai_page(monkeypatch, run_id, ai())
    answer(page, score=3, why=T.NONE_CHOICE, text=NEED)
    click(page, UNDERSTAND)
    click(page, NO)
    assert T.PROPOSAL_KEY not in page.session_state and page.session_state[keys_of(page)["text"]] == NEED
    draw(page)
    assert page.selectboxes == [] and feedback_rows() == []


def test_a_need_the_ai_cannot_use_says_why_and_offers_the_options_again(monkeypatch, run_id):
    page, _backend = ai_page(monkeypatch, run_id, ai(action="set_aside", criterion_id=None, reading="I understood: drop Vertexa."))
    answer(page, score=3, why=T.NONE_CHOICE, text="I do not like Vertexa")
    click(page, UNDERSTAND)
    ok, message = message_of(page)
    assert not ok and reweight.UNSUPPORTED_ACTIONS["set_aside"] in message and "Pick one of the options" in message
    draw(page)
    assert page.text("error") == message and page.selectboxes == [] and T.PROPOSAL_KEY not in page.session_state


def test_a_provider_problem_is_explained_and_nothing_changes(monkeypatch, run_id):
    error = type("AuthenticationError", (Exception,), {})("bad key " + KEY)
    error.status_code = 401
    page, _backend = ai_page(monkeypatch, run_id, error)
    answer(page, score=3, why=T.NONE_CHOICE, text=NEED)
    click(page, UNDERSTAND)
    ok, message = message_of(page)
    assert not ok and message.startswith("The AI could not be reached:") and KEY not in message
    assert feedback_rows() == [] and [w for _n, w in view_of(run_id).weights] == [30.0, 20.0, 20.0, 20.0, 10.0]


def test_a_confirmation_that_belongs_to_another_question_is_refused(monkeypatch, run_id):
    old = {"run_id": run_id, "round_no": 2, "text": NEED, "criterion_id": TECH, "reading": READING}
    page = open_page(monkeypatch, run_id, session={T.PROPOSAL_KEY: old})
    answer(page, score=3, why=T.NONE_CHOICE, text=NEED)
    draw(page)
    assert page.selectboxes == []                                   # a proposal for round 2 is not shown on round 1
    page.session_state[T.PENDING_KEY] = {"kind": "confirm", "run_id": run_id, "round_no": 1, "score": 3, "why": T.NONE_CHOICE, "text": NEED,
                                         "pick": TECH, "proposal": old, "keys": []}
    with pytest.raises(_Rerun):
        draw(page)
    assert message_of(page)[0] is False and feedback_rows() == []


# ---------------------------------------------------------------- the buttons are careful

def test_a_click_for_another_run_or_a_garbled_request_does_nothing(monkeypatch, run_id):
    page = open_page(monkeypatch, run_id, session={T.PENDING_KEY: {"kind": "accept", "run_id": "RUN-OTHER", "round_no": 1, "score": 5, "keys": []}})
    draw(page)
    assert T.PENDING_KEY not in page.session_state and feedback_rows() == [] and page.reruns == 0
    page.session_state[T.PENDING_KEY] = "garbled"
    draw(page)
    assert T.PENDING_KEY not in page.session_state and feedback_rows() == [] and page.reruns == 0


def test_a_request_of_an_unknown_kind_is_refused_politely(monkeypatch, run_id):
    page = open_page(monkeypatch, run_id, session={T.PENDING_KEY: {"kind": "explode", "run_id": run_id, "round_no": 1, "score": 5, "keys": []}})
    with pytest.raises(_Rerun):
        draw(page)
    assert message_of(page) == (False, "That request was not understood.") and feedback_rows() == []


def test_rank_again_without_a_chosen_area_is_refused_politely(monkeypatch, run_id):
    pending = {"kind": "reweight", "run_id": run_id, "round_no": 1, "score": 2, "why": None, "text": "", "pick": None, "proposal": None, "keys": []}
    page = open_page(monkeypatch, run_id, session={T.PENDING_KEY: pending})
    with pytest.raises(_Rerun):
        draw(page)
    assert message_of(page) == (False, "Choose what you are not satisfied with.") and feedback_rows() == []


def test_a_double_click_changes_the_priorities_once(monkeypatch, run_id):
    page = open_page(monkeypatch, run_id)
    answer(page, score=2, why=PRICE)
    draw(page)
    button = page.button_labelled(RANK_AGAIN)
    button["on_click"](*button["args"], **button.get("kwargs", {}))
    button["on_click"](*button["args"], **button.get("kwargs", {}))        # a second click before the page is drawn again
    with pytest.raises(_Rerun):
        draw(page)
    assert len(feedback_rows()) == 1


def test_an_old_page_cannot_answer_a_question_that_was_already_answered(monkeypatch, run_id):
    page = open_page(monkeypatch, run_id)
    answer(page, score=2, why=PRICE)
    draw(page)
    stale = page.button_labelled(RANK_AGAIN)
    not_satisfied(run_id)                                                   # the same question is answered in another window
    stale["on_click"](*stale["args"], **stale.get("kwargs", {}))
    with pytest.raises(_Rerun):
        draw(page)
    ok, message = message_of(page)
    assert not ok and message == orchestrator.ANSWERED_TEXT and len(feedback_rows()) == 1


def test_a_refused_request_keeps_what_was_typed_and_says_why(monkeypatch, run_id):
    page = open_page(monkeypatch, run_id)
    answer(page, score=2, why=PRICE)
    draw(page)
    button = page.button_labelled(RANK_AGAIN)
    with repository.connection() as conn:
        repository.set_run_status(conn, run_id, "Failed")
    button["on_click"](*button["args"], **button.get("kwargs", {}))
    with pytest.raises(_Rerun):
        draw(page)
    ok, message = message_of(page)
    assert not ok and message == "This run is failed, so it can no longer be changed."
    assert page.session_state[keys_of(page)["why"]] == PRICE and page.session_state[keys_of(page)["score"]] == 2
    assert feedback_rows() == []


def test_the_name_typed_in_the_sidebar_is_who_did_it_and_without_one_it_is_the_reviewer(monkeypatch, run_id):
    page = open_page(monkeypatch, run_id, session={"reviewer_name": "  Asha  "})
    answer(page, score=2, why=PRICE)
    click(page, RANK_AGAIN)
    page.session_state["reviewer_name"] = ""
    answer(page, score=4, round_no=2)
    click(page, f"Accept {NEXA}")
    assert [json.loads(r["details_json"])["by"] for r in feedback_rows()] == ["Asha", "reviewer"]
    assert [r["actor"] for r in rows("SELECT actor FROM audit_log WHERE action IN ('priorities_changed', 'recommendation_accepted') ORDER BY id")] == [
        "Asha", "reviewer"]


def test_nothing_the_tab_draws_or_stores_holds_the_key(monkeypatch, run_id):
    page, _backend = ai_page(monkeypatch, run_id, ai())
    answer(page, score=3, why=T.NONE_CHOICE, text=NEED)
    click(page, UNDERSTAND)
    click(page, YES)
    shown_text = draw(page).text()
    for text in (shown_text, whole_database(), repr(page.session_state)):
        assert KEY not in text and SENTINEL not in text


def test_the_other_tabs_show_the_new_ranking_straight_after_a_round(monkeypatch, run_id):
    page = open_page(monkeypatch, run_id)
    answer(page, score=2, why=PRICE)
    click(page, RANK_AGAIN)
    with repository.connection() as conn:
        overview = tab_overview.build_overview(repository.get_run(conn, run_id), repository.list_supplier_results(conn, run_id))
    assert [r.name for r in overview.rows][:2] == [NEXA, HORIZON] and overview.recommended.name == NEXA
    assert [w for _c, _n, w in overview.criteria] == [26.25, 17.5, 30.0, 17.5, 8.75]


# ---------------------------------------------------------------- the real app (needs Streamlit's AppTest)

def start() -> Any:
    app = AppTest.from_file(str(APP_PATH), default_timeout=60)
    app.run()
    return app


def review_tab(app):
    return app.tabs[5]


def question_buttons(tab) -> list:
    """The buttons of the question, without the negotiation button (F20; it has its own tests)."""
    return [b for b in tab.button if b.label != T.NEGOTIATION_BUTTON]


def shown(block) -> str:
    return "\n".join(str(item.value) for kind in ("markdown", "caption", "info", "error", "warning", "success") for item in getattr(block, kind))


def open_run(run_id: str) -> Any:
    app = start()
    app.session_state[SHOWN_RUN_KEY] = run_id
    app.run()
    return app


@needs_app_test
def test_before_any_run_the_review_tab_explains_what_to_do():
    app = start()
    assert not app.exception
    assert "No evaluation yet" in shown(review_tab(app)) and "satisfied" in shown(review_tab(app))
    assert not review_tab(app).radio


@needs_app_test
def test_a_saved_run_opens_with_the_recommendation_and_the_question():
    app = open_run(evaluated_run().run_id)
    assert not app.exception
    tab = review_tab(app)
    text = shown(tab)
    for expected in ("Not eligible", "Where things stand", "More detail", "Your answer", "We recommend Apex Systems"):
        assert expected in text, expected
    assert [e.label for e in tab.expander] == ["To do (2)", "Full ranking (5 suppliers)", "What counts for how much"]
    assert len(tab.radio) == 1 and tab.radio[0].label == T.QUESTION_TEXT and not question_buttons(tab)
    assert len(app.tabs) == 8 and "Your answer" not in shown(app.tabs[2])


@needs_app_test
def test_choosing_a_score_of_5_and_accepting_makes_the_run_final():
    run_id = evaluated_run().run_id
    app = open_run(run_id)
    review_tab(app).radio[0].set_value(5).run()
    assert not app.exception and [b.label for b in question_buttons(review_tab(app))] == [f"Accept {APEX}"]
    question_buttons(review_tab(app))[0].click().run()
    assert not app.exception and "This evaluation is final." in shown(review_tab(app))
    assert rows("SELECT status FROM rfp_runs") == [{"status": "Final"}]


@needs_app_test
def test_a_low_score_and_an_option_rank_the_suppliers_again():
    run_id = evaluated_run().run_id
    app = start()
    keys = T._keys(run_id, 1)
    app.session_state[SHOWN_RUN_KEY] = run_id
    app.session_state[keys["score"]] = 2
    app.session_state[keys["why"]] = PRICE
    app.run()
    assert not app.exception and [b.label for b in question_buttons(review_tab(app))] == [RANK_AGAIN]
    question_buttons(review_tab(app))[0].click().run()
    assert not app.exception
    text = shown(review_tab(app))
    assert "Commercial Value now counts for 30% of the decision (it was 20%)." in text and "We recommend NexaWorks" in text
