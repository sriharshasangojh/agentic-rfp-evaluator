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


GRADIENT_DEEP = "#0A3F3B"
GRADIENT_MID = "#0B7A72"
GRADIENT_LIGHT = "#2FA37F"
GOLD = "#E9C46A"                 # accent: headings and the Validate button in the sidebar, the winner on the podium
GOLD_SOFT = "#F3DA9B"
GOLD_TINT = "#FBF1D6"            # verdict banner start
SIDEBAR_GRADIENT = "linear-gradient(200deg, #0A3F3B 0%, #0B5A52 60%, #14705F 100%)"
BUTTON_GRADIENT = "linear-gradient(120deg, #0B7A72, #2FA37F)"
HERO_GRADIENT = "linear-gradient(115deg, #0A3F3B 0%, #0B7A72 62%, #2FA37F 100%)"
TAB_ON_BACKGROUND = "#EAF5F0"

# The page background, "Spotlight": a soft white glow behind the content, a little colour in the four corners.
def _glow(x: int, y: int, rgb: str, alpha: float) -> str:
    return f"radial-gradient(900px 560px at {x}% {y}%, rgba({rgb}, {alpha}), transparent 70%)"


SPOTLIGHT_BACKGROUND = ", ".join((
    "radial-gradient(900px 620px at 58% 42%, rgba(255, 255, 255, 0.9), transparent 72%)",
    _glow(20, 0, "47, 163, 127", 0.32), _glow(100, 5, "125, 196, 230", 0.34),
    _glow(85, 100, "233, 196, 106", 0.30), _glow(15, 100, "20, 168, 158", 0.26)))


def css() -> str:
    """The whole stylesheet as text (kept apart from `st.markdown` so that a test can read it)."""
    return f"""
<style>
/* Page: the Spotlight background stays put while the content scrolls */
.stApp {{ background-color: #EDF6F2; background-image: {SPOTLIGHT_BACKGROUND}; background-repeat: no-repeat; background-attachment: fixed; }}
[data-testid="stMain"], [data-testid="stHeader"] {{ background: transparent !important; }}
.stApp h1, .stApp h2, .stApp h3, .stApp h4 {{ color: {HEADING} !important; }}
.stApp h4 {{ margin-top: 0.6rem; }}

/* Header banner: deep teal to green with a faint diagonal texture and a four-step journey */
.st-key-hero {{
    background: {HERO_GRADIENT};
    border: none; border-radius: 1.4rem; padding: 1.7rem 2rem; position: relative; overflow: hidden;
    box-shadow: 0 14px 30px rgba(10, 63, 59, 0.25);
}}
.st-key-hero::before {{ content: ""; position: absolute; inset: 0; pointer-events: none;
    background: repeating-linear-gradient(135deg, rgba(255,255,255,0.05) 0 2px, transparent 2px 18px); }}
.st-key-hero > * {{ position: relative; }}
.hero-scenario {{ color: rgba(255,255,255,0.75); font-size: 0.78rem; letter-spacing: 0.14em; text-transform: uppercase; margin-bottom: 0.35rem; }}
.hero-title {{ color: #FFFFFF; font-size: 2rem; font-weight: 800; line-height: 1.2; letter-spacing: -0.01em; margin-bottom: 0.3rem; }}
.hero-sub {{ color: rgba(255,255,255,0.92); font-size: 1rem; max-width: 46rem; margin-bottom: 1.1rem; }}
.hero-steps {{ display: flex; gap: 0.65rem; flex-wrap: wrap; }}
.hero-step {{ flex: 1 1 9rem; background: rgba(255,255,255,0.12); border: 1px solid rgba(255,255,255,0.25);
    border-radius: 0.9rem; padding: 0.6rem 0.9rem; color: #FFFFFF; font-size: 0.8rem; }}
.hero-step b {{ display: block; font-size: 0.88rem; margin-top: 0.1rem; }}
.hero-step.done {{ background: rgba(233,196,106,0.22); border-color: rgba(233,196,106,0.7); }}
.hero-step.now {{ background: #FFFFFF; color: {GRADIENT_DEEP}; border-color: #FFFFFF; }}

/* Cards: tab content and sidebar groups */
[class*="st-key-card_"] {{
    background: {CARD_BACKGROUND}; border: 1px solid #E1EEEA; border-radius: 1.2rem;
    box-shadow: 0 6px 16px rgba(10, 63, 59, 0.07); padding: 1.4rem 1.6rem;
}}

/* Sidebar: deep teal to green with gold headings */
[data-testid="stSidebar"] {{ background: {SIDEBAR_GRADIENT} !important; border-right: none; }}
[data-testid="stSidebar"] > div:first-child {{ background: transparent !important; }}
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3, [data-testid="stSidebar"] h4 {{
    color: {GOLD} !important; letter-spacing: 0.08em; }}
[data-testid="stSidebar"] label, [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
[data-testid="stSidebar"] [data-testid="stCaptionContainer"], [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {{ color: #FFFFFF !important; }}
[data-testid="stSidebar"] a {{ color: {GOLD_SOFT} !important; text-decoration: underline; }}
[data-testid="stSidebar"] [data-testid="stAlertContainer"], [data-testid="stSidebar"] [data-testid="stAlertContainer"] * {{ color: #1F2937 !important; }}
[data-testid="stSidebar"] [data-testid="stTooltipIcon"] svg {{ color: #FFFFFF; }}
[class*="st-key-side_"] {{ background: transparent; border: none; box-shadow: none; padding: 0.2rem 0; }}
[class*="st-key-side_"] h3 {{ font-size: 0.8rem; text-transform: uppercase; padding: 0 0 0.25rem 0; }}
[data-testid="stSidebar"] .stButton button {{ background: {GOLD}; border: none; border-radius: 0.7rem; }}
[data-testid="stSidebar"] .stButton button, [data-testid="stSidebar"] .stButton button * {{ color: {GRADIENT_DEEP} !important; font-weight: 800; }}
[data-testid="stSidebar"] .stButton button:hover {{ background: {GOLD_SOFT}; }}
/* Dropdown arrows, the show-password eye and help icons keep their shape but get no fill at all */
[data-testid="stSidebar"] button:not(.stButton button), [data-testid="stSidebar"] [data-baseweb="select"] *:not(input),
[data-testid="stSidebar"] [data-testid="stTextInput"] *:has(> svg), [data-testid="stSidebar"] [data-testid="stTooltipIcon"],
[data-testid="stSidebar"] [data-testid="stTooltipIcon"] * {{ background-color: transparent !important; background-image: none !important; box-shadow: none !important; }}
[data-testid="stSidebar"] [data-baseweb="select"] > div {{ background-color: #FFFFFF !important; }}
[data-testid="stSidebar"] [data-baseweb="select"] svg, [data-testid="stSidebar"] [data-testid="stTextInput"] button svg {{ color: #4B5563 !important; fill: #4B5563 !important; }}
[data-testid="stSidebar"] [data-testid="stTooltipIcon"] svg {{ color: #FFFFFF !important; fill: #FFFFFF !important; }}

/* Info boxes */
[data-testid="stAlertContainer"] {{ border: 1px solid rgba(20, 168, 158, 0.35); border-radius: 0.9rem; }}

/* Main buttons: the same green gradient as the banner */
.stApp [data-testid="stMain"] button[kind="primary"], .stApp [data-testid="stMain"] [data-testid="stBaseButton-primary"] {{
    background: {BUTTON_GRADIENT}; border: none; border-radius: 0.8rem; font-weight: 700;
    box-shadow: 0 6px 16px rgba(11, 122, 114, 0.3);
}}
.stApp [data-testid="stMain"] [data-testid="stBaseButton-secondary"] {{ border-radius: 0.8rem; border-color: #CFE3DE; }}

/* Tabs: a white rounded bar; the selected tab is tinted, bold and underlined */
[data-testid="stTabs"] [role="tablist"] {{
    background: #FFFFFF; border: 1px solid #D5E7E1; border-radius: 1rem; padding: 0.4rem;
    gap: 0.15rem; box-shadow: 0 4px 14px rgba(10, 63, 59, 0.07);
}}
[data-testid="stTabs"] [role="tab"] {{ border-radius: 0.7rem; padding: 0.5rem 0.8rem; }}
[data-testid="stTabs"] [role="tab"][data-selected] {{ background: {TAB_ON_BACKGROUND} !important; box-shadow: inset 0 -3px 0 {GRADIENT_MID}; }}
[data-testid="stTabs"] [role="tab"][data-selected], [data-testid="stTabs"] [role="tab"][data-selected] * {{ color: {GRADIENT_DEEP} !important; font-weight: 800; }}
[data-testid="stTabs"] .react-aria-SelectionIndicator {{ display: none !important; }}
[data-testid="stTabs"] [role="tab"][data-hovered]:not([data-selected]) {{ background-color: {TAB_HOVER_BACKGROUND} !important; }}
[data-testid="stTabs"] [role="tab"][data-hovered]:not([data-selected]),
[data-testid="stTabs"] [role="tab"][data-hovered]:not([data-selected]) * {{ color: {TEAL_TEXT} !important; }}
</style>
"""


def inject_theme_css() -> None:
    """Style what Streamlit's theme config can't: the banner, cards, sidebar, buttons and tabs.

    Cards are containers created with ``st.container(key=...)``: Streamlit gives them the CSS
    class ``st-key-<key>``, so the keys "hero", "card_<n>" and "side_<name>" are styled here.
    """
    st.markdown(css(), unsafe_allow_html=True)
