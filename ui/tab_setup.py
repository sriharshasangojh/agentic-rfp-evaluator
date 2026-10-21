"""Tab 1: the marking scheme (criteria, weights, gates), read from the database."""
from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from db import repository
from ui.theme import CRITERIA_COLOURS, HEADING, TEXT


def _criteria_table(criteria: Sequence[repository.Criterion]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "What is checked": [c.description for c in criteria],
            "Weight": [f"{c.weight:g}" for c in criteria],
            "Max score": [f"{c.max_score:g}" for c in criteria],
            "Active": ["Yes" if c.is_active else "No" for c in criteria],
        },
        index=pd.Index([c.name for c in criteria], name="Criterion"),
    )


def _gates_table(gates: Sequence[repository.Gate]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Rule": [g.description for g in gates],
            "Active": ["Yes" if g.is_active else "No" for g in gates],
        },
        index=pd.Index([g.name for g in gates], name="Gate"),
    )


def _weight_donut(criteria: Sequence[repository.Criterion]) -> go.Figure:
    """Donut of the active criteria's weights, with the total in the middle."""
    active = [c for c in criteria if c.is_active]
    total = repository.weights_total(criteria)
    figure = go.Figure(
        go.Pie(
            labels=[c.name for c in active],
            values=[c.weight for c in active],
            hole=0.58,
            sort=False,
            direction="clockwise",
            textinfo="value",
            textfont={"color": TEXT, "size": 14},
            marker={
                "colors": [CRITERIA_COLOURS[i % len(CRITERIA_COLOURS)] for i in range(len(active))],
                "line": {"color": "#FFFFFF", "width": 2},
            },
            hovertemplate="%{label}<br>Weight %{value:g}<extra></extra>",
        )
    )
    figure.update_layout(
        height=330,
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": TEXT},
        legend={"orientation": "h", "x": 0.5, "xanchor": "center", "y": -0.02},
        annotations=[
            {"text": f"<b>{total:g}</b><br>total", "showarrow": False, "font": {"size": 22, "color": HEADING}}
        ],
    )
    return figure


def _render_weight_check(criteria: Sequence[repository.Criterion]) -> None:
    total = repository.weights_total(criteria)
    if repository.weights_are_valid(total):
        st.success(f"Active weights total {total:g} out of {repository.WEIGHT_TOTAL:g}.")
    else:
        st.error(
            f"Active weights total {total:g}, but they must add up to {repository.WEIGHT_TOTAL:g}. "
            "An evaluation cannot start until they do."
        )


def render() -> None:
    with repository.connection() as conn:
        template = repository.get_template(conn)
        criteria = repository.list_criteria(conn)
        gates = repository.list_gates(conn)

    if template is None:
        st.warning("The marking scheme is not in the database yet. Reload the page to create it.")
        return

    st.subheader(template.name)
    st.caption(template.description)

    table_column, chart_column = st.columns([3, 2], gap="large")
    with table_column:
        st.markdown("#### Evaluation criteria")
        st.table(_criteria_table(criteria))
        _render_weight_check(criteria)
    with chart_column:
        st.markdown("#### Weights")
        if any(c.is_active for c in criteria):
            st.plotly_chart(_weight_donut(criteria), width="stretch")
        else:
            st.info("No active criteria, so there is nothing to show.")

    st.markdown("#### Mandatory checks (gates)")
    st.table(_gates_table(gates))
    st.caption(
        "A supplier that fails a gate is still scored and ranked, but is marked not eligible "
        "and is never recommended."
    )
