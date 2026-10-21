"""Tab 6: human review, re-evaluation, "Not happy?" flow and negotiation suggestions."""
from __future__ import annotations

import streamlit as st


def render() -> None:
    st.info(
        "No evaluation yet. Open **New evaluation**, upload the supplier PDFs and click "
        "**Evaluate suppliers**. Your to-do list, ratings, overrides and negotiation "
        "suggestions will appear here."
    )
