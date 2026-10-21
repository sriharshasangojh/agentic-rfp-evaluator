"""Tab 4: one supplier's scorecard in detail."""
from __future__ import annotations

import streamlit as st


def render() -> None:
    st.info(
        "No evaluation yet. Open **New evaluation**, upload the supplier PDFs and click "
        "**Evaluate suppliers**. Each supplier's scores, evidence and key facts "
        "will appear here."
    )
