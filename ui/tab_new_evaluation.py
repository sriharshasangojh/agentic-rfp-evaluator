"""Tab 2: upload supplier PDFs and start an evaluation."""
from __future__ import annotations

import streamlit as st


def render() -> None:
    st.info(
        "Upload 1 to 10 supplier proposal PDFs here, enter each supplier's submission date "
        "and experience rating, then click **Evaluate suppliers**. "
        "This tab is built in feature F09."
    )
