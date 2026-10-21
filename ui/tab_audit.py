"""Tab 8: run details, history, change trail and JSON download."""
from __future__ import annotations

import streamlit as st


def render() -> None:
    st.info(
        "No evaluation yet. Open **New evaluation**, upload the supplier PDFs and click "
        "**Evaluate suppliers**. The run details, history, change trail and JSON download "
        "will appear here."
    )
