"""Tab 6: Re-evaluation, called "Review" in the spec (feature F13, spec sections 11 and 13.3): how satisfied are you, and what is not right for you?

The people who use this tab are buyers, not assessors: they do not read the proposals and cannot mark "Implementation Plan". They know
what they need. So the tab asks one question and, if the answer is "not satisfied", one more:

1. "On a scale of 1 to 5, how satisfied are you with this recommendation?"
2. 4 or 5: they can accept it (the evaluation becomes final). 1 to 3: "What are you not satisfied with?", with plain options (the price
   is too high, the laptops should be better, ...) and "None of the above" where they write what they need in their own words. The AI
   reads those words and proposes ONE area; the person confirms it (or picks another) before anything changes.
3. The area they chose counts for more in this run (+15 points, at most 60), everyone is ranked again from the AI's saved marks, and the
   new result is shown with what changed and why. At most 3 rounds.

The AI's marks are never changed here; only how much each area counts for. The page itself stays short: the recommendation, what changed
last (in words) and the question. Everything that is a table or a list (the to-do list, the full ranking, what counts for how much, the
tables of the last round, the original ranking and the history) sits in a drop-down under "More detail" that starts closed. Nothing is
left out; it is one click away, and in the Audit tab.

The rules live in `tools/reweight.py` and `tools/review.py` and the steps that talk to the database or the AI in `agents/orchestrator.py`;
this file only reads the saved run, words it and draws. A click only queues what was asked (a callback), and the next drawing of the tab
carries it out, the same way the New evaluation tab runs a batch. Nothing here changes the Overview, Scorecards or Audit tabs: they read
the same saved run, so they show the new ranking at once.

The pieces are small HTML strings, like the other tabs; every piece of text that comes from a proposal, the AI or the person goes through
`html.escape`.
"""
from __future__ import annotations

import html
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import streamlit as st

from agents import orchestrator
from db import repository
from tools import insights, review, reweight
from ui import picks as picks_view
from ui import sidebar, tab_audit, tab_overview, theme
from ui.tab_new_evaluation import READ_ONLY_KEY, SHOWN_RUN_KEY

PENDING_KEY = "rv_pending"       # what a button asked for; carried out the next time the tab is drawn
MESSAGE_KEY = "rv_message"       # (ok, text) after an action; a refusal is shown once, a success is already visible on the page
PROPOSAL_KEY = "rv_proposal"     # what the AI understood from the typed need, waiting for the person to confirm
NONE_CHOICE = -1                 # the "None of the above" entry of the options (a criterion id is never negative)

EMPTY_TEXT = ("No evaluation yet. Open **New evaluation**, upload the supplier PDFs and click **Evaluate suppliers**. "
              "Once there is a result, you can tell us here how satisfied you are with it.")
DATABASE_ERROR_TEXT = ("The saved results could not be read. Close any other program using `rfp_evaluation.db`, "
                       "wait a few seconds, then reload the page.")
NOT_FOUND_TEXT = "The results of this run could not be found. Run a new evaluation on the New evaluation tab."
INTRO_TEXT = (
    "Tell us how this result sits with you. If you are happy with it, accept it. If not, say what is not right for you and the "
    "suppliers are ranked again with that in mind. The AI's marks stay as they are: only how much each area counts for changes. "
    "Every round is kept under More detail, and in the Audit tab.")
CLOSED_TEXT = "This run is {status}, so it can no longer be changed here. The history is still under More detail."
FINAL_TEXT = "You accepted {supplier} on {when}. This evaluation is final."
NO_RECOMMENDATION_TEXT = ("No supplier meets every must-have, so there is no recommendation to react to. "
                          "Open To do under More detail to see what is wrong.")
NOTHING_TO_DO_TEXT = "Nothing needs your attention: every supplier meets the must-haves and every evaluation was read."
QUESTION_TEXT = "On a scale of 1 to 5, how satisfied are you with this recommendation?"
SCALE_HINT = "1 = not at all satisfied, 5 = completely satisfied."
CHOOSE_TEXT = "Choose a score to continue."
HAPPY_TEXT = "Glad it fits. Accepting makes this evaluation final."
UNHAPPY_TEXT = "Sorry it does not fit yet. Tell us what is not right and the suppliers are ranked again with that in mind."
WHY_TEXT = "What are you not satisfied with?"
WRITE_LABEL = "Write what you need"
WRITE_HINT = "For example: I need laptops that last a full working day."
NO_KEY_TEXT = ("To use your own words, first choose a provider, validate your API key and pick a model in the sidebar. "
               "Or pick one of the options above.")
CONFIRM_LABEL = "Count this area for more"
CONFIRM_HINT = "Is this what you meant? You can pick a different area before anything changes."
ROUNDS_TEXT = "Round {number} of {total}"

TODO_LABELS = {review.KIND_FAILED: "Evaluation failed", review.KIND_GATE: "Not eligible", review.KIND_EVIDENCE: "Evidence not found"}
TODO_TONES = {review.KIND_FAILED: tab_overview.BAD, review.KIND_GATE: tab_overview.BAD, review.KIND_EVIDENCE: tab_overview.WARN}

NEGOTIATION_TITLE = "Final recommendation and negotiation"
NEGOTIATION_INTRO = ("You used all the rounds, so here is one final recommendation, the best fit for what you asked for, and what to ask "
                     "that supplier for before you sign. These are suggestions only: they never change a score or a rank, and they never "
                     "mention another supplier or its price.")
NEGOTIATION_DONE = "Done. Your final recommendation and the negotiation points are just below."
NEGOTIATION_NONE = "No supplier meets every must-have, so there is no supplier to recommend or to negotiate with."
FIT_HEAD = "Best fit for what you asked for: {supplier}"
POINTS_TITLE = "Negotiation points for {supplier}"
POINTS_SUB = "Ask {supplier} for these before you sign. They are suggestions only, shown in order of importance."
NEGOTIATION_LOCKED = orchestrator.NEGOTIATION_LOCKED_TEXT
NEGOTIATION_BUTTON = "Make the final recommendation and negotiation points"
NEGOTIATION_TOPICS = {insights.TOPIC_GATE: ("Must-have", tab_overview.BAD), insights.TOPIC_BUDGET: ("Budget", tab_overview.BAD),
                      insights.TOPIC_CRITERION: ("Gap", tab_overview.WARN), insights.TOPIC_RISK: ("Risk", tab_overview.WARN),
                      insights.TOPIC_EVIDENCE: ("Proof", tab_overview.WARN), insights.TOPIC_STARTUP: ("Startup", tab_overview.GOOD)}
NEGOTIATION_PLAIN_TEXT = "Plain wording. Choose a provider, validate your key and pick a model in the sidebar to have the AI word them."
NEGOTIATION_AI_TEXT = "Worded by the AI from the points Python chose. Lines that did not pass the checks show the plain wording."
MUTED = tab_overview.MUTED
_CSS = (
    ".rv-list{list-style:none;margin:0.25rem 0 0.5rem 0;padding:0}"
    f".rv-list li{{color:{theme.TEXT};font-size:0.95rem;margin:0.35rem 0;line-height:1.5}}"
    ".rv-chip{display:inline-block;margin-right:0.5rem;padding:0.05rem 0.6rem;border-radius:999px;font-weight:600;font-size:0.8rem;"
    "white-space:nowrap}"
    f".rv-panel{{background:{theme.CARD_BACKGROUND};border:1px solid {theme.BORDER};border-left:6px solid {theme.TEAL};border-radius:0.6rem;"
    "padding:0.7rem 1rem;margin:0.25rem 0 0.5rem 0}"
    f".rv-panel-head{{color:{theme.HEADING};font-weight:700;font-size:1.05rem;margin-bottom:0.25rem}}"
    f".rv-sub{{color:{MUTED};font-size:0.85rem}}"
    f".rv-why{{color:{theme.TEXT};font-size:0.92rem;margin:0.3rem 0}}"
    ".rv-wrap{overflow-x:auto;margin:0.25rem 0 0.5rem 0}"
    f".rv-table{{width:100%;border-collapse:collapse;font-size:0.92rem;color:{theme.TEXT}}}"
    f".rv-table th{{background:{theme.TAB_SELECTED_BACKGROUND};color:{theme.HEADING};text-align:left;padding:0.45rem 0.6rem;"
    f"border-bottom:2px solid {theme.TEAL};white-space:nowrap;font-weight:600}}"
    f".rv-table td{{padding:0.45rem 0.6rem;border-bottom:1px solid {theme.BORDER};vertical-align:top}}"
    ".rv-num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}"
    f".rv-now td{{background:{theme.TAB_HOVER_BACKGROUND};font-weight:600}}"
    f".rv-up{{color:{theme.PILL_GOOD[0]};font-weight:600}}"
    f".rv-down{{color:{theme.PILL_BAD[0]};font-weight:600}}"
)


# ---------------------------------------------------------------- what the tab shows, worked out from the saved run

@dataclass(frozen=True)
class _Area:
    """A criterion as `reweight.options_for` needs it."""

    criterion_id: int
    name: str


@dataclass(frozen=True)
class RankLine:
    """One supplier on a leaderboard."""

    rank: int
    name: str
    ppi: float
    eligible: bool


@dataclass(frozen=True)
class BoardLine:
    """One supplier in a before-and-after table."""

    name: str
    rank_now: int
    rank_was: int | None
    ppi_now: float
    ppi_was: float | None
    eligible: bool


@dataclass(frozen=True)
class WeightLine:
    criterion_id: int
    name: str
    was: float
    now: float


@dataclass(frozen=True)
class Round:
    """One "not satisfied" round, as it was saved."""

    number: int
    when: str
    by: str
    satisfaction: int | None
    reason: str                        # the plain sentence the person picked, or the words they wrote
    typed: bool
    reading: str                       # what the AI understood from the typed words (empty for an option)
    headline: str                      # "Commercial Value now counts for 30% of the decision (it was 20%)."
    weights: tuple[WeightLine, ...]
    board: tuple[BoardLine, ...]
    before: tuple[RankLine, ...]
    recommended_before: str | None     # who was recommended before this round
    recommendation: str                # who is recommended now, and whether that changed


@dataclass(frozen=True)
class Acceptance:
    when: str
    by: str
    satisfaction: int | None
    supplier: str
    anyway: bool                       # accepted although not satisfied (all rounds were used)


@dataclass(frozen=True)
class ReviewView:
    run_line: str
    status: str
    recommended: str | None
    board: tuple[RankLine, ...]                        # the ranking now
    weights: tuple[tuple[str, float | None], ...]      # what counts for how much now, in marking-scheme order
    options: tuple[reweight.Option, ...]
    todo: tuple[review.TodoItem, ...]
    rounds: tuple[Round, ...]
    accepted: Acceptance | None
    original: tuple[RankLine, ...]                     # the ranking before the first round (empty when there was none)
    original_recommended: str | None                   # who was recommended before the first round
    original_weights: tuple[tuple[str, float], ...]    # the weights before the first round (empty when there was none)

    @property
    def rounds_used(self) -> int:
        return len(self.rounds)

    @property
    def rounds_left(self) -> int:
        return max(0, reweight.MAX_ROUNDS - len(self.rounds))


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value else None


def _whole(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _flat(text: Any) -> str:
    return " ".join(str(text).split())


def _dicts(value: Any) -> list[dict[str, Any]]:
    """The entries of a saved list that are objects; anything else (a saved value that is not a list, a stray entry) is ignored."""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, (list, tuple)) else []


def _rank_lines(rows: Any) -> tuple[RankLine, ...]:
    lines = [RankLine(_whole(r.get("rank")), _flat(r.get("supplier")), _number(r.get("ppi")) or 0.0, r.get("eligible") is True)
             for r in _dicts(rows)]
    return tuple(sorted(lines, key=lambda line: (line.rank, line.name)))


def _board_lines(details: Mapping[str, Any]) -> tuple[BoardLine, ...]:
    before = {str(r.get("supplier")): r for r in _dicts(details.get("before"))}
    lines: list[BoardLine] = []
    for row in _dicts(details.get("after")):
        name = str(row.get("supplier"))
        was = before.get(name, {})
        lines.append(BoardLine(name, _whole(row.get("rank")), _whole(was["rank"]) if "rank" in was else None,
                               _number(row.get("ppi")) or 0.0, _number(was.get("ppi")), row.get("eligible") is True))
    return tuple(sorted(lines, key=lambda line: line.rank_now))


def _weight_lines(details: Mapping[str, Any]) -> tuple[WeightLine, ...]:
    return tuple(WeightLine(_whole(w.get("criterion_id")), _flat(w.get("name")), _number(w.get("was")) or 0.0, _number(w.get("now")) or 0.0)
                 for w in _dicts(details.get("weights")))


def _round_of(row: repository.FeedbackRow, number: int) -> Round:
    details = row.details
    weights = _weight_lines(details)
    target = next((w for w in weights if w.criterion_id == row.criterion_id), None)
    headline = (f"{target.name} now counts for {reweight.percent(target.now)}% of the decision (it was {reweight.percent(target.was)}%)."
                if target is not None else "The priorities were changed.")
    before, after = details.get("recommended_before"), details.get("recommended_after")
    before = before if isinstance(before, str) else None
    after = after if isinstance(after, str) else None
    return Round(number, tab_audit.started_text(row.created_at), _flat(details.get("by") or orchestrator.REVIEWER),
                 row.rating if isinstance(row.rating, int) else None, _flat(row.comment), details.get("how") == "typed",
                 _flat(details.get("reading") or ""), headline, weights, _board_lines(details), _rank_lines(details.get("before")),
                 before, review.describe_recommendation(before, after))


def _acceptance_of(row: repository.FeedbackRow) -> Acceptance:
    details = row.details
    return Acceptance(tab_audit.started_text(row.created_at), _flat(details.get("by") or orchestrator.REVIEWER),
                      row.rating if isinstance(row.rating, int) else None, _flat(row.supplier_name or details.get("recommended") or ""),
                      details.get("accepted_anyway") is True)


def _todo_criteria(result: Mapping[str, Any]) -> tuple[review.TodoCriterion, ...]:
    items = _dicts(result.get("criteria"))
    return tuple(review.TodoCriterion(_whole(i.get("criterion_id")), _flat(i.get("name") or ""),
                                      i["evidence_found"] if isinstance(i.get("evidence_found"), bool) else None) for i in items)


def build_review(run: repository.RunRecord, suppliers: list[repository.StoredSupplier], feedback: Sequence[repository.FeedbackRow]) -> ReviewView:
    """Work out everything the tab shows from a saved run and its history. No Streamlit and no database access here."""
    overview = tab_overview.build_overview(run, suppliers)
    by_name = {s.supplier_name: s for s in suppliers}
    board = tuple(RankLine(r.rank, r.name, r.ppi, r.eligible) for r in overview.rows)
    todo = review.build_todo([review.TodoSupplier(r.name, r.rank, r.why_not, r.evaluation_failed, _todo_criteria(by_name[r.name].result))
                              for r in overview.rows])
    options = reweight.options_for([_Area(cid, name) for cid, name, _w in overview.criteria])
    rounds = tuple(_round_of(row, number) for number, row in enumerate((f for f in feedback if f.action == repository.ACTION_REWEIGHT), start=1))
    accepted_rows = [f for f in feedback if f.action == repository.ACTION_ACCEPT]
    original_weights = tuple((w.name, w.was) for w in rounds[0].weights) if rounds else ()
    return ReviewView(overview.run_line, run.status, overview.recommended.name if overview.recommended else None, board,
                      tuple((name, weight) for _cid, name, weight in overview.criteria), options, todo, rounds,
                      _acceptance_of(accepted_rows[-1]) if accepted_rows else None, rounds[0].before if rounds else (),
                      rounds[0].recommended_before if rounds else None, original_weights)


# ---------------------------------------------------------------- the HTML pieces

def _e(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _chip(text: str, tone: str) -> str:
    colour, background = tab_overview.TONE_COLOURS[tone]
    return f'<span class="rv-chip" style="color:{colour};background:{background}">{_e(text)}</span>'


def todo_html(items: Sequence[review.TodoItem]) -> str:
    rows = "".join(f'<li>{_chip(TODO_LABELS[i.kind], TODO_TONES[i.kind])}<b>{_e(i.supplier)}</b>: {_e(i.text)}</li>' for i in items)
    return f"<style>{_CSS}</style><ul class=\"rv-list\">{rows}</ul>"


def _movement(now: int, was: int | None) -> str:
    if was is None or was == now:
        return f"{now}" if was is None else f"{now} (no change)"
    word, cls = ("up", "rv-up") if now < was else ("down", "rv-down")
    return f'{now} <span class="{cls}">({word} from {was})</span>'


def ranking_html(lines: Sequence[RankLine], recommended: str | None = None) -> str:
    """The ranking as it stands: rank, supplier, score against the other bids, must-haves. The recommended supplier is marked."""
    rows = ""
    for line in lines:
        chosen = line.name == recommended
        mark = ' class="rv-now"' if chosen else ""
        rows += (f'<tr{mark}><td class="rv-num">{line.rank}</td>'
                 f'<td>{_e(line.name)}{" (recommended)" if chosen else ""}</td><td class="rv-num">{line.ppi:.2f}</td>'
                 f"<td>{'Eligible' if line.eligible else 'Not eligible'}</td></tr>")
    return (f'<style>{_CSS}</style><div class="rv-wrap"><table class="rv-table"><thead><tr><th>Rank</th><th>Supplier</th>'
            f"<th>Score vs other bids</th><th>Must-haves</th></tr></thead><tbody>{rows}</tbody></table></div>")


def weights_html(weights: Sequence[tuple[str, float | None]], started: Mapping[str, float] | None = None) -> str:
    """What counts for how much in this run. After a round, the weight each area started with is shown next to the new one."""
    def cell(name: str, weight: float | None) -> str:
        now = "-" if weight is None else f"{reweight.percent(weight)}%"
        was = (started or {}).get(name)
        tail = ""
        if was is not None and weight is not None and reweight.percent(was) != reweight.percent(weight):
            tail = f'<td class="rv-num rv-sub">started at {reweight.percent(was)}%</td>'
        elif started:
            tail = '<td class="rv-num rv-sub">unchanged</td>'
        return f'<tr><td>{_e(name)}</td><td class="rv-num">{now}</td>{tail}</tr>'

    extra = "<th></th>" if started else ""
    return (f'<style>{_CSS}</style><div class="rv-wrap"><table class="rv-table"><thead><tr><th>Area</th><th>Counts for</th>{extra}'
            f"</tr></thead><tbody>{''.join(cell(n, w) for n, w in weights)}</tbody></table></div>")


def round_summary_html(round_: Round) -> str:
    """The words of one round: what the person said, what now counts for more and who is recommended. No tables."""
    said = (f"You wrote: “{round_.reason}”. {round_.reading}".strip() if round_.typed else f"You chose: “{round_.reason}”.")
    score = f"Satisfaction: {round_.satisfaction} out of 5. " if round_.satisfaction is not None else ""
    return (f'<style>{_CSS}</style><div class="rv-panel"><div class="rv-panel-head">{_e(round_.headline)}</div>'
            f'<div class="rv-sub">Round {round_.number} · {_e(round_.when)} · {_e(round_.by)}</div>'
            f'<div class="rv-why">{_e(score + said)}</div><div class="rv-why"><b>{_e(round_.recommendation)}</b></div></div>')


def round_tables_html(round_: Round) -> str:
    """The two tables of one round: how much each area counted for before and now (only when something changed), and the new ranking."""
    changed = [w for w in round_.weights if reweight.percent(w.was) != reweight.percent(w.now)]
    shares = "".join(f'<tr><td>{_e(w.name)}</td><td class="rv-num">{reweight.percent(w.was)}%</td>'
                     f'<td class="rv-num">{reweight.percent(w.now)}%</td></tr>' for w in round_.weights)
    weights = (f'<div class="rv-wrap"><table class="rv-table"><thead><tr><th>Area</th><th>Counted for</th><th>Counts for now</th></tr></thead>'
               f"<tbody>{shares}</tbody></table></div>") if changed else ""
    rows = "".join(
        f'<tr><td class="rv-num">{_movement(line.rank_now, line.rank_was)}</td><td>{_e(line.name)}</td>'
        f'<td class="rv-num">{line.ppi_now:.2f}' + (f" (was {line.ppi_was:.2f})" if line.ppi_was is not None and line.ppi_was != line.ppi_now else "")
        + f"</td><td>{'Eligible' if line.eligible else 'Not eligible'}</td></tr>" for line in round_.board)
    table = (f'<div class="rv-wrap"><table class="rv-table"><thead><tr><th>Rank now</th><th>Supplier</th><th>Score vs other bids</th>'
             f"<th>Must-haves</th></tr></thead><tbody>{rows}</tbody></table></div>") if round_.board else ""
    return f"<style>{_CSS}</style>{weights}{table}"


def round_html(round_: Round) -> str:
    """The what-changed box of one round in one piece: the words, then the two tables."""
    return round_summary_html(round_) + round_tables_html(round_)


def recommendation_html(recommended: str | None, board: Sequence[RankLine]) -> str:
    """The panel that says who is recommended and with what score. Empty when nobody is."""
    if not recommended:
        return ""
    line = next((l for l in board if l.name == recommended), None)
    score = f'<div class="rv-sub">Score against the other bids: {line.ppi:.2f}</div>' if line is not None else ""
    return f'<style>{_CSS}</style><div class="rv-panel"><div class="rv-panel-head">We recommend {_e(recommended)}</div>{score}</div>'


def negotiation_html(record: repository.NegotiationRecord) -> str:
    """The saved points of one set, numbered, each with a small label for what it is about."""
    rows = []
    for point in record.points[:insights.NEGOTIATION_MAX]:
        label, tone = NEGOTIATION_TOPICS.get(str(point.get("topic")), ("Point", tab_overview.WARN))
        rows.append(f'<li>{_chip(label, tone)}{_e(point.get("text", ""))}</li>')
    return f'<style>{_CSS}</style><ol class="rv-list" style="list-style:decimal;padding-left:1.2rem">{"".join(rows)}</ol>'


def history_html(view: ReviewView) -> str:
    """Every round in one line each, then the acceptance, if there was one."""
    items = []
    for r in view.rounds:
        said = f"wrote “{r.reason}”" if r.typed else f"chose “{r.reason}”"
        score = f"{r.satisfaction} out of 5, " if r.satisfaction is not None else ""
        items.append(f'<li><span class="rv-sub">Round {r.number} · {_e(r.when)} · {_e(r.by)}</span><br>'
                     f"Satisfaction {_e(score)}{_e(said)}. {_e(r.headline)} {_e(r.recommendation)}</li>")
    if view.accepted is not None:
        a = view.accepted
        score = f"{a.satisfaction} out of 5" if a.satisfaction is not None else "no score"
        anyway = " (accepted although not satisfied: all rounds were used)" if a.anyway else ""
        items.append(f'<li><span class="rv-sub">{_e(a.when)} · {_e(a.by)}</span><br>Accepted {_e(a.supplier)} with a satisfaction of '
                     f"{_e(score)}{_e(anyway)}.</li>")
    return f'<style>{_CSS}</style><ul class="rv-list">{"".join(items)}</ul>'


# ---------------------------------------------------------------- buttons: they only queue what was asked

def _keys(run_id: str, round_no: int) -> dict[str, str]:
    """The widget keys of one question. They hold the round, so every new question starts empty."""
    base = f"rv_{run_id}_{round_no}"
    return {"score": f"{base}_score", "why": f"{base}_why", "text": f"{base}_text", "pick": f"{base}_pick"}


def _queue(kind: str, run_id: str, round_no: int) -> None:
    """Button callback: remember what was asked, with what the widgets held at the click. The next drawing carries it out."""
    state, keys = st.session_state, _keys(run_id, round_no)
    st.session_state[PENDING_KEY] = {
        "kind": kind, "run_id": run_id, "round_no": round_no, "score": state.get(keys["score"]), "why": state.get(keys["why"]),
        "text": str(state.get(keys["text"]) or ""), "pick": state.get(keys["pick"]), "proposal": state.get(PROPOSAL_KEY),
        "keys": list(keys.values())}


def _queue_negotiation(run_id: str, supplier: str) -> None:
    st.session_state[PENDING_KEY] = {"kind": "negotiate", "run_id": run_id, "round_no": 0, "score": None, "supplier": supplier, "keys": []}


def _actor() -> str:
    return str(st.session_state.get("reviewer_name") or "").strip() or orchestrator.REVIEWER


def _understand(pending: dict[str, Any], run_id: str) -> orchestrator.ReviewOutcome:
    """The person wrote their own words: ask the AI what they mean. The answer waits for the person to confirm it."""
    settings = sidebar.get_llm_settings()
    if settings is None:
        return orchestrator.ReviewOutcome(False, NO_KEY_TEXT)
    with st.spinner("Reading what you wrote..."):
        understood = orchestrator.understand_need(run_id, pending["text"], settings, _actor())
    if not understood.ok:
        return orchestrator.ReviewOutcome(False, understood.message)
    st.session_state.pop(_keys(run_id, int(pending["round_no"]))["pick"], None)
    st.session_state[PROPOSAL_KEY] = {"run_id": run_id, "round_no": int(pending["round_no"]), "text": reweight.clean_need(pending["text"]),
                                      "criterion_id": understood.criterion_id, "reading": understood.reading,
                                      "leaning": understood.leaning}
    return orchestrator.ReviewOutcome(True, "")


def _carry_out(pending: dict[str, Any], run_id: str) -> tuple[orchestrator.ReviewOutcome, bool]:
    """Do what a button asked. Returns what happened and whether the typed text can be cleared (it was saved)."""
    kind, round_no, score, actor = pending["kind"], pending["round_no"], pending["score"], _actor()
    if kind == "accept":
        outcome = orchestrator.accept_recommendation(run_id, score, round_no, actor)
    elif kind == "reweight":
        criterion = pending["why"]
        outcome = orchestrator.change_priorities(run_id, score, criterion, round_no, "", actor) if isinstance(criterion, int) and \
            not isinstance(criterion, bool) else orchestrator.ReviewOutcome(False, "Choose what you are not satisfied with.")
    elif kind == "confirm":
        proposal = pending["proposal"]
        valid = isinstance(proposal, dict) and proposal.get("run_id") == run_id and proposal.get("round_no") == round_no
        criterion = pending["pick"] if isinstance(pending["pick"], int) and not isinstance(pending["pick"], bool) else None
        if not valid or criterion is None:
            return orchestrator.ReviewOutcome(False, "That request was not understood. Write what you need again."), False
        outcome = orchestrator.change_priorities(run_id, score, criterion, round_no, proposal["text"], actor,
                                                 reading=proposal["reading"], proposed_id=proposal["criterion_id"],
                                                 leaning=proposal.get("leaning") if criterion == proposal["criterion_id"] else None)
    elif kind == "understand":
        outcome = _understand(pending, run_id)
        return outcome, False
    elif kind == "negotiate":
        supplier = str(pending.get("supplier") or "")
        with st.spinner("Making the final recommendation and the negotiation points..."):
            outcome = orchestrator.suggest_negotiation(run_id, supplier, sidebar.get_llm_settings(), actor)
        done = f"{NEGOTIATION_DONE} {outcome.message}".strip() if outcome.ok else outcome.message
        return orchestrator.ReviewOutcome(outcome.ok, done), False
    elif kind == "discard":
        st.session_state.pop(PROPOSAL_KEY, None)
        return orchestrator.ReviewOutcome(True, ""), False
    else:
        return orchestrator.ReviewOutcome(False, "That request was not understood."), False
    return outcome, outcome.ok


def _do_pending(run_id: str) -> None:
    """Carry out the button that was clicked, then redraw the tab. Runs before any widget is drawn, so the typed text can be
    cleared after it was saved."""
    pending = st.session_state.pop(PENDING_KEY, None)
    if not isinstance(pending, dict) or pending.get("run_id") != run_id:
        return
    if st.session_state.get(READ_ONLY_KEY) == run_id:          # a past run opened from the history is only looked at (F21)
        return
    outcome, clear = _carry_out(pending, run_id)
    st.session_state[MESSAGE_KEY] = (outcome.ok, outcome.message)
    if clear:
        st.session_state.pop(PROPOSAL_KEY, None)
        for key in pending.get("keys") or ():
            st.session_state.pop(key, None)
    st.rerun()


# ---------------------------------------------------------------- drawing

def _small(text: str) -> None:
    st.markdown(f'<p style="color:{MUTED};font-size:0.9rem;margin:0.25rem 0">{_e(text)}</p>', unsafe_allow_html=True)


def _label_of(view: ReviewView, criterion_id: int) -> str:
    return next((o.label for o in view.options if o.criterion_id == criterion_id), f"Area {criterion_id}")


def _confirm(run_id: str, view: ReviewView, round_no: int, keys: dict[str, str]) -> None:
    """The AI's reading of the typed words: confirm it, pick another area, or start again."""
    proposal = st.session_state.get(PROPOSAL_KEY)
    st.markdown(f'<style>{_CSS}</style><div class="rv-panel"><div class="rv-why">{_e(proposal["reading"])}</div></div>', unsafe_allow_html=True)
    _small(CONFIRM_HINT)
    ids = [o.criterion_id for o in view.options]
    start = ids.index(proposal["criterion_id"]) if proposal["criterion_id"] in ids else 0
    st.selectbox(CONFIRM_LABEL, ids, index=start, key=keys["pick"], format_func=lambda cid: _label_of(view, cid))
    yes, no = st.columns(2)
    with yes:
        st.button("Yes, rank the suppliers again", key=f"{keys['pick']}_yes", type="primary", on_click=_queue, args=("confirm", run_id, round_no))
    with no:
        st.button("No, let me write it again", key=f"{keys['pick']}_no", on_click=_queue, args=("discard", run_id, round_no))


def _why(run_id: str, view: ReviewView, round_no: int, keys: dict[str, str]) -> None:
    """Not satisfied: what with? A plain option, or "None of the above" and the person's own words."""
    st.markdown(f"**{WHY_TEXT}**")
    choices = [o.criterion_id for o in view.options] + [NONE_CHOICE]
    choice = st.radio(WHY_TEXT, choices, index=None, key=keys["why"], label_visibility="collapsed",
                      format_func=lambda cid: reweight.NONE_OF_THE_ABOVE if cid == NONE_CHOICE else _label_of(view, cid))
    proposal = st.session_state.get(PROPOSAL_KEY)
    proposed = isinstance(proposal, dict) and proposal.get("run_id") == run_id and proposal.get("round_no") == round_no
    if choice is None:
        _small("Choose what is closest to what you are not satisfied with.")
    elif choice != NONE_CHOICE:
        st.button("Rank the suppliers again with this in mind", key=f"{keys['why']}_go", type="primary", on_click=_queue,
                  args=("reweight", run_id, round_no))
    else:
        st.text_area(WRITE_LABEL, key=keys["text"], max_chars=reweight.NEED_MAX_CHARS, height=90, placeholder=WRITE_HINT)
        written = reweight.clean_need(st.session_state.get(keys["text"]))
        if sidebar.get_llm_settings() is None:
            _small(NO_KEY_TEXT)
        st.button("Understand what I wrote", key=f"{keys['why']}_read", disabled=not written, on_click=_queue,
                  args=("understand", run_id, round_no))
        if proposed:
            _confirm(run_id, view, round_no, keys)


def _question(run_id: str, view: ReviewView) -> None:
    """The satisfaction question, and what follows from the answer."""
    round_no = view.rounds_used + 1
    keys = _keys(run_id, round_no)
    st.markdown("#### Your answer")
    if view.rounds_used:
        _small(ROUNDS_TEXT.format(number=min(round_no, reweight.MAX_ROUNDS), total=reweight.MAX_ROUNDS) if view.rounds_left else
               orchestrator.ROUNDS_USED_TEXT)
    score = st.radio(QUESTION_TEXT, list(reweight.SCALE), index=None, horizontal=True, key=keys["score"])
    _small(SCALE_HINT)
    if score is None:
        _small(CHOOSE_TEXT)
        return
    satisfied = reweight.is_satisfied(score)
    if satisfied or not view.rounds_left:
        if satisfied:
            _small(HAPPY_TEXT)
        label = f"Accept {view.recommended}" if satisfied else f"Accept {view.recommended} anyway"
        st.button(label, key=f"{keys['score']}_accept", type="primary", on_click=_queue, args=("accept", run_id, round_no))
        return
    _small(UNHAPPY_TEXT)
    _why(run_id, view, round_no, keys)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def history_label(view: ReviewView) -> str:
    """The title of the History drop-down: how many rounds there were and whether the run was accepted."""
    parts = ([_plural(view.rounds_used, "round")] if view.rounds else []) + (["accepted"] if view.accepted is not None else [])
    return f"History ({', '.join(parts)})"


def _details(view: ReviewView) -> None:
    """Everything that is a table or a list lives in a drop-down that starts closed, so the page stays short and calm."""
    st.markdown("#### More detail")
    if view.todo:
        with st.expander(f"To do ({len(view.todo)})", expanded=False):
            st.markdown(todo_html(view.todo), unsafe_allow_html=True)
    else:
        _small(NOTHING_TO_DO_TEXT)
    with st.expander(f"Full ranking ({_plural(len(view.board), 'supplier')})", expanded=False):
        st.markdown(ranking_html(view.board, view.recommended), unsafe_allow_html=True)
    with st.expander("What counts for how much", expanded=False):
        st.markdown(weights_html(view.weights, dict(view.original_weights) or None), unsafe_allow_html=True)
    if view.rounds:
        last = view.rounds[-1]
        with st.expander(f"Last round in detail (round {last.number})", expanded=False):
            st.markdown(round_tables_html(last), unsafe_allow_html=True)
    if view.rounds or view.accepted is not None:
        with st.expander(history_label(view), expanded=False):
            if view.original:
                st.markdown("**The original ranking**")
                st.markdown(ranking_html(view.original, view.original_recommended), unsafe_allow_html=True)
            st.markdown(history_html(view), unsafe_allow_html=True)


def fit_html(record: repository.NegotiationRecord) -> str:
    """The panel that says which supplier is the final pick and why it fits what the person asked for."""
    text = _e(record.fit.get("text", "")) if record.fit else ""
    body = f'<div class="rv-why">{text}</div>' if text else ""
    return (f'<style>{_CSS}</style><div class="rv-panel"><div class="rv-panel-head">{_e(FIT_HEAD.format(supplier=record.supplier_name))}'
            f'</div>{body}</div>')


def _negotiation(run_id: str, view: ReviewView, records: Sequence[repository.NegotiationRecord], read_only: bool = False) -> None:
    """After every round: the final recommendation (Python's pick), why it fits, and the points to negotiate."""
    if not view.board:
        return
    st.markdown(f"#### {NEGOTIATION_TITLE}")
    if view.rounds_left > 0:
        _small(NEGOTIATION_LOCKED)
        return
    if view.recommended is None:
        _small(NEGOTIATION_NONE)
        return
    _small(NEGOTIATION_INTRO)
    newest = next((r for r in reversed(records) if r.supplier_name == view.recommended and r.points), None)
    if newest is not None:
        st.markdown(fit_html(newest), unsafe_allow_html=True)
        st.divider()
        st.markdown(f"#### {POINTS_TITLE.format(supplier=newest.supplier_name)}")
        _small(POINTS_SUB.format(supplier=newest.supplier_name))
        st.markdown(negotiation_html(newest), unsafe_allow_html=True)
        _small(NEGOTIATION_AI_TEXT if (any(p.get("worded") for p in newest.points) or newest.fit.get("worded")) else NEGOTIATION_PLAIN_TEXT)
    if not read_only:
        st.button(NEGOTIATION_BUTTON, key=f"rv_neg_{run_id}", on_click=_queue_negotiation, args=(run_id, view.recommended))


def _show(run_id: str, view: ReviewView, negotiations: Sequence[repository.NegotiationRecord] = (), read_only: bool = False) -> None:
    _small(view.run_line)
    st.markdown(INTRO_TEXT)
    message = st.session_state.pop(MESSAGE_KEY, None)
    if isinstance(message, tuple) and len(message) == 2 and message[1] and not message[0]:
        st.error(str(message[1]))                      # only a refusal is announced; a success is not said twice: the page itself shows it
    elif isinstance(message, tuple) and len(message) == 2 and message[0] and str(message[1]).startswith(NEGOTIATION_DONE):
        st.success(str(message[1]))                    # the one success that is said: the new points are further down the page

    st.markdown("#### Where things stand")
    if view.recommended:
        st.markdown(recommendation_html(view.recommended, view.board), unsafe_allow_html=True)
        picks_view.show(st, run_id)                      # F21b: the pick for the priorities and the best fit, side by side
    if view.rounds:
        st.markdown("#### What changed last")
        st.markdown(round_summary_html(view.rounds[-1]), unsafe_allow_html=True)

    if view.rounds_left == 0:                          # every round used: the final recommendation is what is left to do, so it comes first
        _negotiation(run_id, view, negotiations, read_only)

    if read_only:
        st.info(tab_audit.READ_ONLY_TEXT)
    elif view.accepted is not None and view.status == repository.RUN_FINAL:
        st.success(FINAL_TEXT.format(supplier=view.accepted.supplier, when=view.accepted.when))
    elif view.status != repository.RUN_EVALUATED:
        st.info(CLOSED_TEXT.format(status=view.status.lower()))
    elif view.recommended is None:
        st.info(NO_RECOMMENDATION_TEXT)
    else:
        _question(run_id, view)

    if view.rounds_left > 0:
        _negotiation(run_id, view, negotiations, read_only)
    _details(view)


def render() -> None:
    run_id = st.session_state.get(SHOWN_RUN_KEY)
    if not run_id:
        st.info(EMPTY_TEXT)
        return
    _do_pending(run_id)
    try:
        with repository.connection() as conn:
            run = repository.get_run(conn, run_id)
            suppliers = repository.list_supplier_results(conn, run_id) if run is not None else []
            feedback = repository.list_feedback(conn, run_id) if run is not None else []
            negotiations = repository.list_negotiations(conn, run_id) if run is not None else []
    except (sqlite3.Error, OSError):
        st.error(DATABASE_ERROR_TEXT)
        return
    if run is None or not suppliers:
        st.info(NOT_FOUND_TEXT)
        return
    _show(run_id, build_review(run, suppliers, feedback), negotiations, st.session_state.get(READ_ONLY_KEY) == run_id)
