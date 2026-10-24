"""Streamlit entry point: page setup, theme, sidebar and the eight tabs. No business logic."""
from __future__ import annotations

import sqlite3

import streamlit as st

from db.seed import ensure_database
from ui import (
    tab_analysis,
    tab_ask,
    tab_audit,
    tab_new_evaluation,
    tab_overview,
    tab_review,
    tab_scorecards,
    tab_setup,
)
from ui.sidebar import render_sidebar
from ui.theme import inject_theme_css

# Exactly eight tabs, in this order (spec section 13.3). The sixth is called "Re-evaluation" at the user's request; the spec calls it "Review".
TABS = (
    ("Setup", tab_setup),
    ("New evaluation", tab_new_evaluation),
    ("Overview", tab_overview),
    ("Scorecards", tab_scorecards),
    ("Analysis", tab_analysis),
    ("Re-evaluation", tab_review),
    ("Ask the RFP", tab_ask),
    ("Audit", tab_audit),
)


def open_database() -> None:
    """Create and seed the database. Stops the page with a friendly message if that is not possible."""
    try:
        ensure_database()
    except (sqlite3.Error, OSError):
        st.error(
            "The database could not be opened or created. Close any other program using "
            "`rfp_evaluation.db`, wait a few seconds for OneDrive to finish syncing, then reload the page."
        )
        st.stop()


def main() -> None:
    st.set_page_config(page_title="RFP Evaluator", layout="wide")
    inject_theme_css()
    open_database()
    render_sidebar()

    with st.container(key="hero"):
        st.title("Agentic RFP Evaluation and Supplier Ranking")
        st.caption("Meridian Analytics Pvt. Ltd. · RFP MA-RFP-2026-017 · 100 business laptops")

    streamlit_tabs = st.tabs([label for label, _ in TABS])
    for index, (streamlit_tab, (_, module)) in enumerate(zip(streamlit_tabs, TABS)):
        with streamlit_tab:
            with st.container(key=f"card_{index}"):
                module.render()


main()
