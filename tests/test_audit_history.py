"""F21: what the Audit tab reads from saved runs (history, AI-quality tiles, change trail, fingerprints), opening a past run
read-only, and the rounds in the JSON file. Real database, real pipeline, a recording stand-in for Streamlit."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from agents import orchestrator
from db import audit, repository, seed
from review_support import APEX, BRIGHT, FIVE, GOOD_FACTS, HORIZON, NAMES, NEXA, SETTINGS, evaluated_run, rows
from tools import audit_view
from ui import tab_audit as A
from ui import tab_new_evaluation


@pytest.fixture(autouse=True)
def seeded_database():
    seed.ensure_database()


def three_rows():
    return [r for r in FIVE if r[0] in (APEX, NEXA, HORIZON)]


def run_with_rounds(rounds: int = 1, facts=None):
    result = evaluated_run(three_rows(), facts=facts or {})
    for round_no, criterion in enumerate((1, 2, 3)[:rounds], start=1):
        assert orchestrator.change_priorities(result.run_id, 2, criterion, round_no).ok
    return result


def summaries():
    with repository.connection() as conn:
        return {s.run.rfp_run_id: s for s in repository.list_run_summaries(conn)}


# ---------------------------------------------------------------- the database side

def test_a_run_is_listed_with_its_winner_its_supplier_count_and_whether_priorities_were_changed():
    plain, changed = evaluated_run(three_rows()), run_with_rounds(1)
    got = summaries()
    assert got[plain.run_id].suppliers == 3 and got[plain.run_id].winner and not got[plain.run_id].changed_priorities
    assert got[changed.run_id].changed_priorities
    assert got[plain.run_id].winner in (APEX, NEXA, HORIZON)


def test_the_winner_is_the_best_ranked_supplier_that_meets_every_must_have():
    from review_support import BAD_FACTS
    rows_ = [(n, w, r, [10, 10, 10, 10, 10] if n == BRIGHT else s) for n, w, r, s in FIVE]
    result = evaluated_run(rows_, facts={BRIGHT: BAD_FACTS})
    best = summaries()[result.run_id].winner
    assert best and best != BRIGHT
    with repository.connection() as conn:
        ranked = repository.list_supplier_results(conn, result.run_id)
    assert ranked[0].supplier_name == BRIGHT and not ranked[0].gates_passed


def test_a_run_with_nobody_eligible_has_no_winner():
    from review_support import BAD_FACTS
    rows_ = [r for r in FIVE if r[0] in (BRIGHT, "Orbit Digital")]
    result = evaluated_run(rows_, facts={BRIGHT: BAD_FACTS, "Orbit Digital": BAD_FACTS})
    assert summaries()[result.run_id].winner is None


def test_runs_come_newest_first():
    first = evaluated_run(three_rows())
    with repository.connection() as conn:
        conn.execute("UPDATE rfp_runs SET created_at = '2026-01-01T00:00:00+00:00' WHERE rfp_run_id = ?", (first.run_id,))
    second = evaluated_run(three_rows())
    order = [s.run.rfp_run_id for s in summaries().values()]
    assert order.index(second.run_id) < order.index(first.run_id)


def test_evidence_counts_are_the_quotes_not_found_and_the_quotes_checked_over_all_runs():
    evaluated_run(three_rows())
    with repository.connection() as conn:
        missing, checked = repository.evidence_counts(conn)
        expected_checked = conn.execute("SELECT COUNT(*) FROM evaluation_attempts WHERE evidence_found IS NOT NULL").fetchone()[0]
        expected_missing = conn.execute("SELECT COUNT(*) FROM evaluation_attempts WHERE evidence_found = 0").fetchone()[0]
    assert checked == expected_checked > 0 and missing == expected_missing and 0 <= missing <= checked


def test_the_events_of_a_run_come_back_oldest_first_with_their_details():
    result = run_with_rounds(1)
    with repository.connection() as conn:
        events = repository.list_events(conn, result.run_id)
        other = repository.list_events(conn, "RUN-NOPE")
    actions = [e.action for e in events]
    assert actions[0] == "run_created" and "priorities_changed" in actions and actions.index("run_created") < actions.index("run_evaluated")
    assert other == [] and next(e for e in events if e.action == "priorities_changed").details["round"] == 1


def test_an_event_whose_details_cannot_be_read_comes_back_with_empty_details():
    result = evaluated_run(three_rows())
    with repository.connection() as conn:
        conn.execute("INSERT INTO audit_log (rfp_run_id, timestamp, actor, action, details_json) VALUES (?, 't', 'x', 'odd', 'not json')", (result.run_id,))
        last = repository.list_events(conn, result.run_id)[-1]
    assert (last.action, last.details) == ("odd", {})


# ---------------------------------------------------------------- the trail from a real run

def test_a_real_run_with_a_round_has_a_trail_whose_score_changes_are_the_marks_the_priorities_and_the_ranks():
    result = run_with_rounds(2)
    with repository.connection() as conn:
        trail = audit_view.trail_rows(repository.list_events(conn, result.run_id), A.started_text)
    changes = audit_view.only_score_changes(trail)
    assert len(changes) < len(trail) and all(r.score_change for r in changes)
    assert [r.text for r in changes if r.text.startswith("Round")] == [r.text for r in changes if "now counts for more" in r.text]
    assert sum("now counts for more" in r.text for r in changes) == 2
    assert trail[0].text.startswith("Run started with 3 suppliers") and trail[0].when.endswith("UTC")


# ---------------------------------------------------------------- the JSON file

def export_of(run_id: str):
    from ui import tab_overview
    with repository.connection() as conn:
        run = repository.get_run(conn, run_id)
        suppliers = repository.list_supplier_results(conn, run_id)
        feedback = repository.list_feedback(conn, run_id)
    return A.build_export(run, suppliers, tab_overview.build_overview(run, suppliers), (), feedback)


def test_the_file_lists_each_round_of_not_satisfied_with_what_changed():
    result = run_with_rounds(2)
    rounds = export_of(result.run_id)["feedback_rounds"]
    assert [r["round"] for r in rounds] == [1, 2] and [r["criterion"] for r in rounds] == ["Technical Capability", "Implementation Plan"]
    assert rounds[0]["satisfaction"] == 2 and rounds[0]["by"] == "reviewer" and rounds[0]["recommended_after"]
    assert isinstance(rounds[0]["weights"], list) and rounds[0]["weights"][0]["name"]
    assert json.loads(A.export_text(export_of(result.run_id)))["feedback_rounds"][1]["round"] == 2


def test_a_run_without_rounds_has_an_empty_list_and_an_accepted_run_adds_no_round():
    result = evaluated_run(three_rows())
    assert export_of(result.run_id)["feedback_rounds"] == []
    assert orchestrator.accept_recommendation(result.run_id, 5, 1).ok
    assert export_of(result.run_id)["feedback_rounds"] == []


# ---------------------------------------------------------------- the page

class Block:
    def __init__(self, page):
        self.page = page

    def __enter__(self):
        return self.page

    def __exit__(self, *exc):
        return False


class Page:
    def __init__(self, session=None, picks=None):
        self.calls, self.session_state, self.picks = [], dict(session or {}), dict(picks or {})

    def _r(self, kind, text="", **kw):
        self.calls.append((kind, str(text), kw))

    def markdown(self, text, **kw):
        self._r("markdown", text, **kw)

    def info(self, text, **kw):
        self._r("info", text, **kw)

    def error(self, text, **kw):
        self._r("error", text, **kw)

    def warning(self, text, **kw):
        self._r("warning", text, **kw)

    def download_button(self, label, **kw):
        self._r("download_button", label, **kw)
        return False

    def columns(self, count, **kw):
        return [Block(self) for _ in range(count)]

    def expander(self, label, **kw):
        self._r("expander", label, **kw)
        return Block(self)

    def date_input(self, label, value=None, key=None, **kw):
        self._r("date_input", label, key=key)
        return self.session_state.get(key, value)

    def selectbox(self, label, options, key=None, format_func=str, **kw):
        options = list(options)
        self._r("selectbox", label, key=key, options=options, labels=[format_func(o) for o in options])
        self.session_state.setdefault(key, options[0])
        return self.session_state[key]

    def checkbox(self, label, key=None, **kw):
        self._r("checkbox", label, key=key)
        return bool(self.session_state.get(key, False))

    def button(self, label, key=None, on_click=None, args=(), **kw):
        self._r("button", label, key=key, on_click=on_click, args=args)
        return False

    def text(self, *kinds):
        return "\n".join(t for k, t, _ in self.calls if not kinds or k in kinds)

    def of(self, kind):
        return [(t, kw) for k, t, kw in self.calls if k == kind]


def draw(monkeypatch, session=None, picks=None):
    page = Page(session)
    monkeypatch.setattr(A, "st", page)
    A.render()
    return page


def test_with_no_saved_run_the_tab_only_explains_what_to_do(monkeypatch):
    page = draw(monkeypatch)
    assert [k for k, _t, _kw in page.calls] == ["info"] and page.calls[0][1] == A.EMPTY_TEXT


def test_the_tiles_show_the_three_rates_with_the_counts_behind_them(monkeypatch):
    run_with_rounds(1)
    evaluated_run(three_rows())
    page = draw(monkeypatch)
    tiles = page.text("markdown")
    assert "Runs where priorities were changed" in tiles and "50%" in tiles and "1 of 2 runs" in tiles
    assert "Quotes not found in the proposal" in tiles and "quotes checked" in tiles
    assert "Average cost per run" in tiles and "USD 0.0" in tiles and "that reported a cost" in tiles


def test_the_history_lists_the_runs_and_offers_the_filters(monkeypatch):
    first, second = evaluated_run(three_rows()), evaluated_run(three_rows())
    page = draw(monkeypatch)
    table = next(t for t, _ in page.of("markdown") if "<th>Winner</th>" in t)
    assert first.run_id in table and second.run_id in table and "test-model" in table
    assert [l for l, _ in page.of("date_input")] == ["From date", "To date"]
    boxes = {l: kw for l, kw in page.of("selectbox")}
    assert boxes["Model"]["options"] == [audit_view.ALL, "test-model"] and audit_view.ALL in boxes["Winner"]["options"]
    assert set(boxes[A.OPEN_PICK_LABEL]["options"]) == {first.run_id, second.run_id}


def test_the_model_filter_narrows_the_table_and_a_filter_with_no_match_says_so(monkeypatch):
    evaluated_run(three_rows())
    page = draw(monkeypatch, {A.MODEL_KEY: "nobody-model"})
    assert A.NO_MATCH_TEXT in page.text("markdown") and not [t for t, _ in page.of("markdown") if "<th>Winner</th>" in t]
    page = draw(monkeypatch, {A.WINNER_KEY: audit_view.ALL})
    assert any("<th>Winner</th>" in t for t, _ in page.of("markdown"))


def test_the_run_on_screen_is_marked_in_the_history(monkeypatch):
    shown = evaluated_run(three_rows())
    evaluated_run(three_rows())
    page = draw(monkeypatch, {tab_new_evaluation.SHOWN_RUN_KEY: shown.run_id})
    table = next(t for t, _ in page.of("markdown") if "<th>Winner</th>" in t)
    assert table.count('class="au-now"') == 1 and table.index('class="au-now"') < table.index(shown.run_id)


def test_opening_a_past_run_shows_it_everywhere_and_makes_it_read_only(monkeypatch):
    old, current = evaluated_run(three_rows()), evaluated_run(three_rows())
    page = Page({tab_new_evaluation.SHOWN_RUN_KEY: current.run_id})
    monkeypatch.setattr(A, "st", page)
    A._open_run(old.run_id)
    assert page.session_state[tab_new_evaluation.SHOWN_RUN_KEY] == old.run_id and page.session_state[tab_new_evaluation.READ_ONLY_KEY] == old.run_id
    drawn = draw(monkeypatch, page.session_state)
    assert A.READ_ONLY_TEXT in drawn.text("info") and old.run_id in drawn.text("markdown")


def test_opening_the_run_that_is_already_on_screen_does_not_lock_it(monkeypatch):
    current = evaluated_run(three_rows())
    page = Page({tab_new_evaluation.SHOWN_RUN_KEY: current.run_id})
    monkeypatch.setattr(A, "st", page)
    A._open_run(current.run_id)
    assert tab_new_evaluation.READ_ONLY_KEY not in page.session_state


def test_the_open_button_opens_the_picked_run(monkeypatch):
    old = evaluated_run(three_rows())
    evaluated_run(three_rows())
    page = draw(monkeypatch, {A.OPEN_KEY: old.run_id})
    (label, kw), = [(l, kw) for l, kw in page.of("button") if l == A.OPEN_LABEL]
    assert kw["args"] == (old.run_id,) and kw["on_click"] is A._open_run


def test_a_remembered_pick_that_the_filter_hides_is_dropped(monkeypatch):
    shown = evaluated_run(three_rows())
    page = draw(monkeypatch, {A.OPEN_KEY: "RUN-GONE"})
    assert page.session_state[A.OPEN_KEY] == shown.run_id


def test_the_trail_lists_the_events_and_the_filter_keeps_only_score_changes(monkeypatch):
    result = run_with_rounds(1)
    sid = {tab_new_evaluation.SHOWN_RUN_KEY: result.run_id}
    page = draw(monkeypatch, sid)
    assert [l for l, _ in page.of("checkbox")] == [A.SCORE_ONLY_LABEL]
    full = next(t for t, _ in page.of("markdown") if "<th>What happened</th>" in t)
    page = draw(monkeypatch, {**sid, A.SCORE_ONLY_KEY: True})
    only = next(t for t, _ in page.of("markdown") if "<th>What happened</th>" in t)
    assert "The AI evaluated" in full and "The AI evaluated" not in only and "now counts for more" in only
    assert only.count("<tr>") < full.count("<tr>") and "Run started with" in full and "Run started with" not in only


def test_a_run_with_no_score_changes_says_so_when_filtered(monkeypatch):
    result = evaluated_run(three_rows())
    with repository.connection() as conn:
        conn.execute("DELETE FROM audit_log WHERE rfp_run_id = ? AND action IN ('ranking_computed')", (result.run_id,)) if False else None
    page = draw(monkeypatch, {tab_new_evaluation.SHOWN_RUN_KEY: result.run_id, A.SCORE_ONLY_KEY: True})
    assert "ranking worked out" in page.text("markdown").lower()                          # a plain run still has its ranking event


def test_the_fingerprints_are_listed_with_file_pages_and_the_full_hash(monkeypatch):
    result = evaluated_run(three_rows())
    page = draw(monkeypatch, {tab_new_evaluation.SHOWN_RUN_KEY: result.run_id})
    table = next(t for t, _ in page.of("markdown") if "SHA-256 fingerprint" in t)
    with repository.connection() as conn:
        docs = repository.list_documents(conn, result.run_id)
    assert len(docs) == 3 and all(len(d.sha256) == 64 and d.sha256 in table and d.file_name in table for d in docs)


def test_text_from_a_proposal_or_the_ai_is_never_drawn_as_html(monkeypatch):
    result = evaluated_run(three_rows())
    with repository.connection() as conn:
        audit.log_event(conn, result.run_id, "<b>me</b>", "supplier_evaluated", {"supplier": "<script>x</script>"})
        conn.execute("INSERT INTO run_documents (rfp_run_id, supplier_name, file_name, sha256, pages) VALUES (?, '<i>S</i>', '<u>f.pdf</u>', 'abc', 1)", (result.run_id,))
    page = draw(monkeypatch, {tab_new_evaluation.SHOWN_RUN_KEY: result.run_id})
    text = page.text("markdown")
    assert "<script>" not in text and "<b>me</b>" not in text and "<u>f.pdf</u>" not in text and "<i>S</i>" not in text
    assert "&lt;script&gt;x&lt;/script&gt;" in text and "&lt;u&gt;f.pdf&lt;/u&gt;" in text


def test_a_database_that_cannot_be_read_gives_a_friendly_error(monkeypatch):
    def broken():
        raise OSError("locked")
    monkeypatch.setattr(A.repository, "connection", broken)
    page = draw(monkeypatch)
    assert [k for k, *_ in page.calls] == ["error"] and page.calls[0][1] == A.DATABASE_ERROR_TEXT


# ---------------------------------------------------------------- read-only on the Re-evaluation tab

def review_page(monkeypatch, run_id, read_only):
    import test_tab_review as R
    session = {tab_new_evaluation.READ_ONLY_KEY: run_id} if read_only else {}
    page = R.open_page(monkeypatch, run_id, session=session)
    R.draw(page)
    return page, R


def test_a_past_run_opened_from_the_history_has_no_question_and_no_button_on_the_review_tab(monkeypatch):
    result = run_with_rounds(3)
    page, R = review_page(monkeypatch, result.run_id, True)
    assert A.READ_ONLY_TEXT in page.shown("info")
    assert not page.radios and not [b for b in page.buttons.values()] and not page.negotiation_buttons
    page, R = review_page(monkeypatch, result.run_id, False)
    assert page.negotiation_buttons


def test_a_click_queued_before_the_run_was_locked_is_ignored(monkeypatch):
    result = run_with_rounds(0)
    import test_tab_review as R
    page = R.open_page(monkeypatch, result.run_id, session={tab_new_evaluation.READ_ONLY_KEY: result.run_id})
    page.session_state[R.T.PENDING_KEY] = {"kind": "reweight", "run_id": result.run_id, "round_no": 1, "score": 2, "why": 1, "text": "",
                                           "pick": None, "proposal": None, "keys": []}
    R.draw(page)
    with repository.connection() as conn:
        assert repository.list_feedback(conn, result.run_id) == []
    assert R.T.PENDING_KEY not in page.session_state


def test_an_open_run_that_is_not_locked_still_asks_the_question(monkeypatch):
    result = run_with_rounds(0)
    page, R = review_page(monkeypatch, result.run_id, False)
    assert page.radios and A.READ_ONLY_TEXT not in page.shown("info")
