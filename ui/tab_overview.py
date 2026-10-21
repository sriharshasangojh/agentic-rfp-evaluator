"""Tab 3: manager view with the recommendation and leaderboard."""
from __future__ import annotations

import streamlit as st


def render() -> None:
    st.caption("Manager view")
    st.info(
        "No evaluation yet. Open **New evaluation**, upload the supplier PDFs and click "
        "**Evaluate suppliers**. The recommendation, leaderboard, strengths and pros and cons "
        "will appear here."
    )
