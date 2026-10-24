"""The two picks of a run where the buyer asked for something (F21b): the best pick for their priorities and the best fit.

Shared by the New evaluation, Overview and Re-evaluation tabs. Pure drawing helpers: all text goes through html.escape."""
from __future__ import annotations

import html
from typing import Any

from agents import orchestrator

SAME_HEAD = "Based on your priorities and the best fit"
REQUEST_HEAD = "Best pick based on your priorities"
IDEAL_HEAD = "Best pick based on the best fit"
NOTE = ("Your priorities change how much each area counts (and the price wish, if you made one). The best fit uses the original "
        "weights set up in Setup and the AI's own marks. The ranking, scorecards and charts follow your priorities.")


def _rank(rank: int | None, total: int) -> str:
    return f"rank {rank} of {total}" if rank is not None and total else "not ranked"


def picks_html(picks: orchestrator.Picks) -> str:
    """One line when both ways of choosing give the same supplier; two labelled lines, each with both ranks, when they differ."""
    e = html.escape
    if picks.same:
        body = (f'<div>\u2713 <b>{e(SAME_HEAD)}:</b> {e(picks.request)}</div>'
                f'<div style="color:#4B5563;font-size:0.9rem">It is {e(_rank(picks.request_rank, picks.total))} with your priorities '
                f'and {e(_rank(picks.request_rank_ideal, picks.total))} with the best-fit weights.</div>')
    else:
        body = (f'<div>\u26a0 <b>{e(REQUEST_HEAD)}:</b> {e(picks.request)}</div>'
                f'<div style="color:#4B5563;font-size:0.9rem">{e(_rank(picks.request_rank, picks.total))} with your priorities, '
                f'{e(_rank(picks.request_rank_ideal, picks.total))} with the best-fit weights.</div>'
                f'<div style="margin-top:0.5rem">\u2713 <b>{e(IDEAL_HEAD)}:</b> {e(picks.ideal)}</div>'
                f'<div style="color:#4B5563;font-size:0.9rem">{e(_rank(picks.ideal_rank, picks.total))} with the best-fit weights, '
                f'{e(_rank(picks.ideal_rank_request, picks.total))} with your priorities.</div>'
                f'<div style="color:#4B5563;font-size:0.85rem;margin-top:0.4rem">{e(NOTE)}</div>')
    return f'<div style="border:1px solid #cfe5df;border-radius:10px;padding:0.7rem 0.9rem;margin:0.5rem 0;background:#f4fbf9">{body}</div>'


def show(st: Any, run_id: str) -> None:
    """Draw the picks (and the red overpay warning, when there is one) with the given Streamlit module. Nothing when nothing was asked."""
    picks = orchestrator.picks_of(run_id)
    if picks is None:
        return
    st.markdown(picks_html(picks), unsafe_allow_html=True)
    if picks.overpay:
        st.error(picks.overpay)
