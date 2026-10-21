"""Tab 1: the marking scheme (criteria, weights, gates)."""
from __future__ import annotations

import streamlit as st


def render() -> None:
    st.info(
        "The marking scheme will appear here: the evaluation criteria with their weights, "
        "the weight chart, the mandatory checks and a check that the weights total 100. "
        "It is read from the database, which is built in feature F02."
    )
