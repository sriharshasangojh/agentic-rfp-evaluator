"""Tab 7: ask questions about the proposals."""
from __future__ import annotations

import streamlit as st


def render() -> None:
    st.info(
        "No evaluation yet. Open **New evaluation**, upload the supplier PDFs and click "
        "**Evaluate suppliers**. You can then ask questions about the proposals here "
        "and get answers with page references."
    )
