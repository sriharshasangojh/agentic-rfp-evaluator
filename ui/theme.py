"""Colour constants and the CSS that Streamlit's theme config cannot express."""
from __future__ import annotations

import streamlit as st

# Palette: calm teal tones on a light mint page, white cards, one stronger tone for the sidebar.
PAGE_BACKGROUND = "#EEF6F4"      # main area: very light mint
SIDEBAR_BACKGROUND = "#ADDBD8"   # sidebar: the soft mint the user chose
CARD_BACKGROUND = "#FFFFFF"      # cards and inputs
BORDER = "#C5DEDA"               # soft teal-grey borders
HEADING = "#0B4F4A"              # deep teal headings
PRIMARY = "#0B7A72"              # deep teal: buttons and focus (white text on it is about 5.2:1)
TEXT = "#1F2937"                 # dark slate body text
TEAL = "#14A89E"                 # accent: tab indicator, header stripe, chart colour
TEAL_TEXT = "#075A55"            # darker teal for text on tinted backgrounds
TAB_SELECTED_BACKGROUND = "#D5ECE8"
TAB_HOVER_BACKGROUND = "#E3F1EE"

# One fixed colour per supplier, in upload order, shared by every chart.
SUPPLIER_COLOURS: tuple[str, ...] = (
    TEAL, "#D81B60", "#7CB342", "#6A1B9A", "#EF6C00", "#455A64", "#8D6E63",
)

# Weight donut: one calm colour per criterion (criteria are not suppliers, so these are separate).
CRITERIA_COLOURS: tuple[str, ...] = ("#14A89E", "#6CCBC3", "#A9DDC5", "#8FBF8F", "#CFE6A8")

# Chart colours
HEATMAP_LOW = "#E0F5F3"
HEATMAP_HIGH = TEAL_TEXT
SCORE_SUPPLIER = TEAL
SCORE_BEST = "#B0BEC5"
WITHIN_BUDGET = TEAL
OVER_BUDGET = "#D81B60"
BUDGET_LINE = "#1F2937"

# Strength table: (text colour, background colour). The word is always printed too.
STRENGTH_COLOURS: dict[str, tuple[str, str]] = {
    "Strong": ("#2E7D32", "#E8F5E9"),
    "Average": ("#B26A00", "#FFF3E0"),
    "Weak": ("#C62828", "#FFEBEE"),
}


# Verdict banner, pills and the must-haves grid (change pack R1): (text colour, background colour). The symbol and the
# words always carry the meaning too, so colour is never the only signal.
PILL_GOOD = ("#2E7D32", "#E8F5E9")
PILL_WARN = ("#8A5300", "#FFF3E0")
PILL_BAD = ("#C62828", "#FFEBEE")
EDGE_GOOD = "#2E7D32"            # the coloured edge of the verdict banner
EDGE_WARN = "#E09100"
EDGE_BAD = "#C62828"
TILE_BAD_BORDER = "#F1B8B8"      # the outline of a key-fact tile that is red-tinted (a fact not stated, or a price over budget)
METER_EMPTY = "#7F9A96"          # an empty dot of the 5-dot meter on a scorecard (the shape differs from a filled dot too)


def inject_theme_css() -> None:
    """Style what Streamlit's theme config can't: headings, cards, the header and the tabs.

    Cards are containers created with ``st.container(key=...)``: Streamlit gives them the CSS
    class ``st-key-<key>``, so the keys "hero", "card_<n>" and "side_<name>" are styled here.
    """
    st.markdown(
        f"""
<style>
/* Headings (Streamlit's own heading styles win unless this is !important) */
.stApp h1, .stApp h2, .stApp h3, .stApp h4 {{ color: {HEADING} !important; }}

/* Cards: header, tab content and sidebar groups */
.st-key-hero, [class*="st-key-card_"], [class*="st-key-side_"] {{
    background: {CARD_BACKGROUND};
    border: 1px solid {BORDER};
    border-radius: 0.75rem;
    box-shadow: 0 1px 3px rgba(11, 79, 74, 0.08);
    padding: 1.25rem 1.5rem;
}}
.st-key-hero {{ border-left: 6px solid {TEAL}; }}
.st-key-hero h1 {{ font-size: 1.9rem; line-height: 1.25; padding: 0; }}
[class*="st-key-side_"] {{ padding: 1rem 1.1rem; }}
[class*="st-key-side_"] h3 {{ font-size: 1.05rem; padding: 0 0 0.25rem 0; }}

/* Info boxes get a soft teal outline */
[data-testid="stAlertContainer"] {{ border: 1px solid rgba(20, 168, 158, 0.35); }}

/* Tabs: soft teal tint and a teal underline for the selected one */
[data-testid="stTabs"] [role="tab"] {{ border-radius: 0.5rem 0.5rem 0 0; }}
[data-testid="stTabs"] [role="tab"][data-selected] {{ background-color: {TAB_SELECTED_BACKGROUND} !important; }}
[data-testid="stTabs"] [role="tab"][data-selected],
[data-testid="stTabs"] [role="tab"][data-selected] * {{ color: {HEADING} !important; font-weight: 600; }}
[data-testid="stTabs"] .react-aria-SelectionIndicator {{ background-color: {TEAL} !important; }}
[data-testid="stTabs"] [role="tab"][data-hovered]:not([data-selected]) {{ background-color: {TAB_HOVER_BACKGROUND} !important; }}
[data-testid="stTabs"] [role="tab"][data-hovered]:not([data-selected]),
[data-testid="stTabs"] [role="tab"][data-hovered]:not([data-selected]) * {{ color: {TEAL_TEXT} !important; }}
</style>
""",
        unsafe_allow_html=True,
    )
