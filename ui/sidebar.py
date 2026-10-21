"""Sidebar: LLM settings and reviewer name. In F01 the LLM controls are disabled placeholders."""
from __future__ import annotations

import streamlit as st

# Moves to llm/client.py in F07, which also makes the controls below live.
PROVIDERS: tuple[str, ...] = (
    "OpenAI", "Anthropic", "Google Gemini", "Cohere", "xAI Grok", "DeepSeek",
)


def render_sidebar() -> None:
    """Draw the sidebar as two grouped cards, in the order given by the spec (section 13.2)."""
    with st.sidebar:
        st.header("Settings")
        with st.container(key="side_llm"):
            st.subheader("LLM")
            st.selectbox("Provider", PROVIDERS, disabled=True)
            st.text_input("API key", type="password", disabled=True)
            st.button("Validate key & load models", disabled=True)
            st.selectbox("Model", ["(validate a key first)"], disabled=True)
            st.caption("Provider, key and model controls become active in feature F07.")
        with st.container(key="side_run"):
            st.subheader("Evaluation run")
            st.caption("Scoring temperature: 0 (fixed for reproducible results)")
            st.text_input("Reviewer name", key="reviewer_name")
