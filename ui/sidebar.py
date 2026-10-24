"""Sidebar (spec section 13.2): provider, API key, Validate key & load models, model, fixed temperature, reviewer name.

All the logic lives in llm/client.py; this file only draws the controls and keeps their state in
``st.session_state``. The API key stays in this browser session only: it is never written to the database,
a file, a log or a message (spec 2.2).
"""
from __future__ import annotations

import streamlit as st

from llm import client

# Session-state keys. Only the first two are widgets the visitor types into.
PROVIDER_KEY = "llm_provider"
API_KEY_KEY = "llm_api_key"
MODEL_KEY = "llm_model"
MODELS_KEY = "llm_models"            # model names loaded for the current provider + key
LOADED_FOR_KEY = "llm_loaded_for"    # fingerprint of the provider + key those names belong to
CACHE_KEY = "llm_model_cache"        # {fingerprint: model names}, for this session only (spec 6.2)
MESSAGE_KEY = "llm_message"          # ("error" | "success", text) shown under the button
RUN_KEY = "run_in_progress"          # set to True by the New evaluation tab (F09) while a batch runs


def _secret(name: str) -> str | None:
    """A value from the app's Streamlit secrets, or None when there are no secrets or no such name."""
    try:
        value = st.secrets.get(name)
    except Exception:   # no secrets file at all raises an error that is not worth showing
        return None
    return value if isinstance(value, str) else None


def _effective_key(provider: str) -> str:
    """The key typed in the box, else a default key the app's owner set up, else empty."""
    typed = (st.session_state.get(API_KEY_KEY) or "").strip()
    return typed or client.default_key_for(provider, secret_lookup=_secret) or ""


def _forget_stale_choices(provider: str, key: str) -> None:
    """When the provider or key changes, drop the old model list, choice and message (a model from another
    provider must never be used with this key). A list loaded earlier for the same provider and key is restored."""
    fingerprint = client.cache_key(provider, key) if key else None
    if st.session_state.get(LOADED_FOR_KEY) == fingerprint:
        return
    st.session_state[LOADED_FOR_KEY] = fingerprint
    st.session_state.pop(MODEL_KEY, None)
    st.session_state.pop(MESSAGE_KEY, None)
    cache = st.session_state.setdefault(CACHE_KEY, {})
    st.session_state[MODELS_KEY] = cache.get(fingerprint, ()) if fingerprint else ()


def _validate(provider: str, key: str) -> None:
    """The "Validate key & load models" button: ask the provider, then remember the list or the problem."""
    st.session_state.pop(MODEL_KEY, None)
    try:
        models = client.list_models(provider, key)
    except client.LLMError as error:
        st.session_state[MODELS_KEY] = ()
        st.session_state[MESSAGE_KEY] = ("error", error.user_message)
        return
    fingerprint = client.cache_key(provider, key)
    st.session_state.setdefault(CACHE_KEY, {})[fingerprint] = models
    st.session_state[LOADED_FOR_KEY] = fingerprint
    st.session_state[MODELS_KEY] = models
    count = len(models)
    st.session_state[MESSAGE_KEY] = ("success", f"Key accepted. {count} chat {'model' if count == 1 else 'models'} loaded.")


def get_llm_settings() -> client.LLMSettings | None:
    """The provider, model and key to use for a run, or None until a key has been validated and a model chosen."""
    provider = st.session_state.get(PROVIDER_KEY)
    model = st.session_state.get(MODEL_KEY)
    if not provider or not model:
        return None
    key = _effective_key(provider)
    if not key or st.session_state.get(LOADED_FOR_KEY) != client.cache_key(provider, key):
        return None
    return client.LLMSettings(provider=provider, model=model, api_key=key)


def render_sidebar() -> None:
    """Draw the sidebar as two grouped cards, in the order given by the spec (section 13.2)."""
    with st.sidebar:
        st.header("\u2699\ufe0f Settings")
        with st.container(key="side_llm"):
            st.subheader("LLM")
            provider = st.selectbox("Provider", client.PROVIDER_NAMES, key=PROVIDER_KEY)
            typed_key = st.text_input(
                "API key", type="password", key=API_KEY_KEY,
                help="Kept only in this browser session. It is never saved to a file or the database, and never shown.")
            link = client.provider_named(provider).key_url
            if link:
                st.markdown(f"Don't have a key? [Get a key \u2197]({link})")
            key = _effective_key(provider)
            if key and not (typed_key or "").strip():
                st.caption("Using the key set up for this app. Type your own key above to use that instead.")
            _forget_stale_choices(provider, key)
            if st.button("Validate key & load models", key="llm_validate"):
                _validate(provider, key)
            kind, text = st.session_state.get(MESSAGE_KEY, (None, None))
            if kind == "error":
                st.error(text)
            elif kind == "success":
                st.success(text)
            models = st.session_state.get(MODELS_KEY, ())
            running = bool(st.session_state.get(RUN_KEY, False))
            st.selectbox(
                "Model", models, index=None, key=MODEL_KEY, disabled=running or not models,
                placeholder="Choose a model" if models else "Validate a key first")
            if running:
                st.caption("The model is locked while a batch runs, so every supplier has the same judge.")
        with st.container(key="side_run"):
            st.subheader("Evaluation run")
            st.caption("Scoring temperature: 0 (fixed for reproducible results)")
            st.text_input("Reviewer name (required)", key="reviewer_name", help="Your name is saved with every decision in the audit trail, so it is needed before you evaluate.")
