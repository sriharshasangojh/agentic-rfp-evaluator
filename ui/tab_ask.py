"""Tab 7: Ask the RFP (feature F14, spec sections 12 and 13.3): ask a question about the proposals, get a short answer with page references.

The person picks one supplier or all of them and types a question in their own words. A keyword search (`tools/retrieval.py`) picks the
best excerpts of the proposals and the AI answers from those excerpts only, citing "(Supplier, Page N)" or saying "Not stated in the
proposal." The excerpts it was given sit in a drop-down under the answer, so every answer can be checked against the proposal itself.
Asking never ranks anyone, never changes a score and is never used for scoring (spec 12): the evaluator always reads the whole proposal.

The proposal text is kept in this browser session only (DECISIONS 120): the New evaluation tab hands the chunks of the run it just
finished to this tab through the session. After a reload they are gone, and the tab says so in plain words and how to get them back.
Only the question, the sources (supplier, page, score), the answer and the cost are written to the audit log; the excerpts never are.

A click only queues what was asked (a callback); the next drawing of the tab carries it out, the same way the Re-evaluation tab does.
The page is small pieces of HTML, like the other tabs; every piece of text that comes from a proposal, the AI or the person goes
through `html.escape`.
"""
from __future__ import annotations

import html
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import streamlit as st

from agents import orchestrator
from tools import retrieval
from ui import sidebar, tab_overview, theme
from ui.tab_new_evaluation import PROPOSAL_CHUNKS_KEY, SHOWN_RUN_KEY

PENDING_KEY = "ask_pending"        # what the Ask button asked for; carried out the next time the tab is drawn
MESSAGE_KEY = "ask_message"        # (ok, text) after an action; only a refusal is shown, once
HISTORY_KEY = "ask_history"        # {"run_id": ..., "items": [Asked, ...]}, newest first, for the run on screen only
MAX_KEPT = 10                      # questions kept on the page for one run (every question is in the audit log anyway)
ALL_SUPPLIERS = "All suppliers"

EXAMPLES = (
    "Which suppliers give on-site warranty with next-day repair?",
    "Who will hold the same price for laptops we order next year?",
    "What battery life did each supplier actually measure?",
    "What does Vertexa say about data wiping?",
)

EMPTY_TEXT = ("No evaluation yet. Open **New evaluation**, upload the supplier PDFs and click **Evaluate suppliers**. "
              "You can then ask questions about the proposals here and get answers with page references.")
NO_TEXT_TEXT = ("The proposal text for this evaluation is not available here. It is kept in this browser session only and is never "
                "saved, so it is gone after the page is reloaded. To ask questions again, upload the PDFs on **New evaluation** and "
                "evaluate them once more.")
INTRO_TEXT = ("Ask about the proposals in your own words. The answer is written only from short excerpts of the proposals, each with its "
              "supplier and page, and you can read those excerpts under the answer. Asking never changes a score or the ranking.")
NO_KEY_TEXT = "To ask a question, first choose a provider, validate your API key and pick a model in the sidebar."
SUPPLIER_LABEL = "Ask about"
QUESTION_LABEL = "Your question"
QUESTION_HINT = "For example: Which suppliers give on-site warranty with next-day repair?"
ASK_LABEL = "Ask"
UNCITED_TEXT = "The answer gives no page reference. Check it against the excerpts under it."
UNMATCHED_TEXT = ("The answer mentions {what}, which is not one of the excerpts it was written from. "
                  "Check it against the excerpts under it.")

MUTED = tab_overview.MUTED
_CSS = (
    f".ak-panel{{background:{theme.CARD_BACKGROUND};border:1px solid {theme.BORDER};border-left:6px solid {theme.TEAL};"
    "border-radius:0.6rem;padding:0.7rem 1rem;margin:0.25rem 0 0.5rem 0}"
    f".ak-q{{color:{theme.HEADING};font-weight:700;font-size:0.98rem;margin-bottom:0.35rem;overflow-wrap:anywhere}}"
    f".ak-answer{{color:{theme.TEXT};font-size:1rem;line-height:1.55;overflow-wrap:anywhere}}"
    ".ak-list{margin:0.2rem 0 0.2rem 1.2rem;padding:0}.ak-list li{margin:0.2rem 0}"
    f".ak-sub{{color:{MUTED};font-size:0.85rem;margin-top:0.4rem}}"
    f".ak-warn{{display:inline-block;margin-top:0.4rem;padding:0.1rem 0.6rem;border-radius:0.5rem;font-size:0.85rem;"
    f"color:{theme.PILL_WARN[0]};background:{theme.PILL_WARN[1]}}}"
    f".ak-source{{background:{theme.CARD_BACKGROUND};border:1px solid {theme.BORDER};border-radius:0.5rem;padding:0.5rem 0.8rem;margin:0.4rem 0}}"
    f".ak-source-head{{color:{theme.HEADING};font-weight:700;font-size:0.9rem;margin-bottom:0.2rem}}"
    f".ak-source-text{{color:{theme.TEXT};font-size:0.9rem;line-height:1.5;overflow-wrap:anywhere}}"
)


@dataclass(frozen=True)
class Asked:
    """One question on the page: what was typed, which proposal was picked (None: all of them) and what came back."""

    question: str
    scope: str | None
    outcome: orchestrator.AskOutcome


# ---------------------------------------------------------------- words and pieces

def _flat(text: Any) -> str:
    return " ".join(str(text).split())


def _e(text: Any) -> str:
    return html.escape(_flat(text), quote=True)


def _lines(text: Any) -> str:
    """Text with its line breaks kept (as <br>) and its blank lines dropped: Streamlit ends an HTML block at a blank line."""
    return "<br>".join(_e(line) for line in str(text).splitlines() if line.strip())


_BULLET = re.compile(r"^\s*[-*•–]\s+(.*)$")


def _answer_lines(text: Any) -> str:
    """The answer with its short list (lines that start with "-", "*" or a bullet) drawn as a list and its other lines kept as they are.
    Every piece of text is escaped; the result is one line of HTML."""
    parts: list[str] = []
    items: list[str] = []
    prose: list[str] = []

    def close_list() -> None:
        if items:
            parts.append('<ul class="ak-list">' + "".join(f"<li>{item}</li>" for item in items) + "</ul>")
            items.clear()

    def close_prose() -> None:
        if prose:
            parts.append("<br>".join(prose))
            prose.clear()

    for line in str(text).splitlines():
        if not line.strip():
            continue
        bullet = _BULLET.match(line)
        if bullet:
            close_prose()
            items.append(_e(bullet.group(1)))
        else:
            close_list()
            prose.append(_e(line))
    close_list()
    close_prose()
    return "".join(parts)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _small(text: str) -> None:
    st.markdown(f'<p style="color:{MUTED};font-size:0.9rem;margin:0.25rem 0">{_e(text)}</p>', unsafe_allow_html=True)


def _the_proposal_of(supplier: str | None) -> str:
    return "any proposal" if supplier is None else f"the proposal of {supplier}"


def scope_note(asked: Asked) -> str:
    """One sentence on where the answer came from, or why the AI was not asked."""
    outcome = asked.outcome
    if not outcome.asked_ai:
        return f"No excerpt of {_the_proposal_of(outcome.searched)} shares a word with your question, so the AI was not asked."
    count = len(outcome.hits)
    if outcome.searched is None:
        return f"Answered from {_plural(count, 'excerpt')} across {_plural(len({hit.supplier for hit in outcome.hits}), 'proposal')}."
    best = "the best excerpt" if count == 1 else f"the {count} best excerpts"
    because = " Your question names it, so only that proposal was searched." if outcome.narrowed else ""
    return f"Answered from {best} of the proposal of {outcome.searched}.{because}"


def warnings_of(asked: Asked) -> tuple[str, ...]:
    """What to double-check in an answer: a statement without a page reference, or a reference that is not one of the excerpts used."""
    answer = asked.outcome.answer
    if answer is None:
        return ()
    found: list[str] = []
    if answer.uncited:
        found.append(UNCITED_TEXT)
    unmatched = answer.unverified
    if unmatched:
        what = "; ".join(f"{c.supplier}, page {', '.join(str(p) for p in c.pages)}" if c.pages else c.supplier for c in unmatched)
        found.append(UNMATCHED_TEXT.format(what=what))
    return tuple(found)


def answer_html(asked: Asked) -> str:
    """The question, the answer, and one grey line on where it came from (plus a yellow line for anything to double-check)."""
    answer = asked.outcome.answer
    if answer is None:
        return ""
    warnings = "".join(f'<div class="ak-warn">{_e(text)}</div>' for text in warnings_of(asked))
    return (f'<style>{_CSS}</style><div class="ak-panel"><div class="ak-q">{_e(asked.question)}</div>'
            f'<div class="ak-answer">{_answer_lines(answer.text)}</div><div class="ak-sub">{_e(scope_note(asked))}</div>{warnings}</div>')


def sources_html(hits: Sequence[retrieval.Hit]) -> str:
    """The excerpts the answer was written from, best first, each under its supplier and page."""
    parts = [f'<div class="ak-source"><div class="ak-source-head">{_e(hit.supplier)}, page {hit.page}</div>'
             f'<div class="ak-source-text">{_lines(hit.text)}</div></div>' for hit in hits]
    return f'<style>{_CSS}</style>{"".join(parts)}'


def sources_label(hits: Sequence[retrieval.Hit]) -> str:
    return f"Where this came from ({_plural(len(hits), 'excerpt')})"


def earlier_label(count: int) -> str:
    return f"Earlier questions ({count})"


# ---------------------------------------------------------------- state

def keys_of(run_id: str) -> dict[str, str]:
    """Widget keys of one run, so a new run starts with an empty question box and its own supplier list."""
    return {"supplier": f"ask_supplier_{run_id}", "question": f"ask_question_{run_id}"}


def _actor() -> str:
    return str(st.session_state.get("reviewer_name") or "").strip() or orchestrator.REVIEWER


def _chunks_for(run_id: str) -> tuple[Any, ...]:
    """The proposal chunks that belong to the run on screen; empty when there are none (after a reload, say)."""
    stored = st.session_state.get(PROPOSAL_CHUNKS_KEY)
    if isinstance(stored, tuple) and len(stored) == 2 and stored[0] == run_id and stored[1]:
        return tuple(stored[1])
    return ()


def _history(run_id: str) -> list[Asked]:
    stored = st.session_state.get(HISTORY_KEY)
    if isinstance(stored, dict) and stored.get("run_id") == run_id and isinstance(stored.get("items"), list):
        return list(stored["items"])
    return []


def _queue(run_id: str) -> None:
    """Button callback: remember what was asked. The next drawing of the tab carries it out."""
    keys = keys_of(run_id)
    picked = st.session_state.get(keys["supplier"])
    st.session_state[PENDING_KEY] = {"run_id": run_id, "question": str(st.session_state.get(keys["question"]) or ""),
                                     "supplier": picked if isinstance(picked, str) and picked != ALL_SUPPLIERS else None}


def _use_example(run_id: str, question: str) -> None:
    """Button callback: put an example question in the question box (a callback runs before the box is drawn)."""
    st.session_state[keys_of(run_id)["question"]] = question


def _do_pending(run_id: str) -> None:
    """Answer the question that was queued, then redraw the tab."""
    pending = st.session_state.pop(PENDING_KEY, None)
    if not isinstance(pending, dict) or pending.get("run_id") != run_id:
        return
    settings = sidebar.get_llm_settings()
    if settings is None:
        outcome = orchestrator.AskOutcome(False, NO_KEY_TEXT)
    else:
        with st.spinner("Searching the proposals and writing the answer..."):
            outcome = orchestrator.ask_question(run_id, pending["question"], _chunks_for(run_id), settings, pending["supplier"], _actor())
    if outcome.ok and outcome.answer is not None:
        items = [Asked(" ".join(str(pending["question"]).split()), pending["supplier"], outcome), *_history(run_id)][:MAX_KEPT]
        st.session_state[HISTORY_KEY] = {"run_id": run_id, "items": items}
    st.session_state[MESSAGE_KEY] = (outcome.ok, outcome.message)
    st.rerun()


# ---------------------------------------------------------------- drawing

def _controls(run_id: str, chunks: Sequence[Any]) -> None:
    keys = keys_of(run_id)
    suppliers = tuple(dict.fromkeys(c.supplier for c in chunks))
    st.selectbox(SUPPLIER_LABEL, [ALL_SUPPLIERS, *suppliers], index=0, key=keys["supplier"])
    st.text_input(QUESTION_LABEL, key=keys["question"], max_chars=retrieval.QUESTION_MAX_CHARS, placeholder=QUESTION_HINT)
    settings = sidebar.get_llm_settings()
    if settings is None:
        _small(NO_KEY_TEXT)
    # Not greyed out while the box is empty: a text box only hands its words over when the person leaves it, and a click on a button that is
    # still greyed out at that moment is lost. An empty question is answered with "Type a question first." instead.
    st.button(ASK_LABEL, key=f"ask_go_{run_id}", type="primary", disabled=settings is None, on_click=_queue, args=(run_id,))
    with st.expander("Example questions", expanded=False):
        for number, example in enumerate(EXAMPLES, start=1):
            st.button(example, key=f"ask_example_{number}_{run_id}", on_click=_use_example, args=(run_id, example))


def _answers(run_id: str) -> None:
    history = _history(run_id)
    if not history:
        return
    latest = history[0]
    st.markdown("#### Answer")
    st.markdown(answer_html(latest), unsafe_allow_html=True)
    if latest.outcome.hits:
        with st.expander(sources_label(latest.outcome.hits), expanded=False):
            st.markdown(sources_html(latest.outcome.hits), unsafe_allow_html=True)
    if len(history) > 1:
        with st.expander(earlier_label(len(history) - 1), expanded=False):
            for earlier in history[1:]:
                st.markdown(answer_html(earlier), unsafe_allow_html=True)


def render() -> None:
    run_id = st.session_state.get(SHOWN_RUN_KEY)
    if not run_id:
        st.info(EMPTY_TEXT)
        return
    _do_pending(run_id)
    chunks = _chunks_for(run_id)
    if not chunks:
        st.info(NO_TEXT_TEXT)
        return
    st.markdown(INTRO_TEXT)
    message = st.session_state.pop(MESSAGE_KEY, None)
    if isinstance(message, tuple) and len(message) == 2 and message[1] and not message[0]:
        st.error(str(message[1]))                      # only a refusal is announced; an answer is on the page itself
    _controls(run_id, chunks)
    _answers(run_id)
