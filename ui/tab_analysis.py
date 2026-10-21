"""Tab 5: technical view with heatmap, price vs quality and what-if charts."""
from __future__ import annotations

import streamlit as st


def render() -> None:
    st.caption("Technical view")
    st.info(
        "No evaluation yet. Open **New evaluation**, upload the supplier PDFs and click "
        "**Evaluate suppliers**. The score heatmap, price vs quality chart and the "
        "what-if weight chart will appear here."
    )
