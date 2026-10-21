"""Streamlit entry point: page setup, theme, sidebar and the eight tabs. No business logic."""
from __future__ import annotations

import streamlit as st

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

# Exactly eight tabs, in this order (spec section 13.3).
TABS = (
    ("Setup", tab_setup),
    ("New evaluation", tab_new_evaluation),
    ("Overview", tab_overview),
    ("Scorecards", tab_scorecards),
    ("Analysis", tab_analysis),
    ("Review", tab_review),
    ("Ask the RFP", tab_ask),
    ("Audit", tab_audit),
)


def main() -> None:
    st.set_page_config(page_title="RFP Evaluator", layout="wide")
    inject_theme_css()
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
