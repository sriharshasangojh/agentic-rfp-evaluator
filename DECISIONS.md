# Decisions

Assumptions and choices made while building, newest last. Each is also reported in that feature's snapshot.

## F01: Project skeleton and theme

1. **Where it is built.** The files are written straight into the project folder
   (`...\RPF Evaluator\rfp_evaluator`). The build workspace could not install packages (PyPI was
   blocked), so Streamlit was never run there: the app has not been started and there is no screenshot.
   The user runs it locally (README, "Run locally").
2. **`requirements.txt` grows with the features.** F01 lists only `streamlit` and `pytest`; each later
   feature adds the packages it imports. Exact versions are pinned before the first push (milestone M1),
   from the user's own clean install, because versions could not be read in the build workspace.
3. **`pytest.ini`** (`pythonpath = .`, `testpaths = tests`) was added so tests can import `ui`, `tools`
   and the other packages from the project root. It is not in the section 4 folder list.
4. **Smoke test.** `tests/test_app_smoke.py` uses Streamlit's `AppTest` to check the app starts and has
   the eight tab names in order. It is not in the section 4 test list. It could not be run in the
   build workspace.
5. **Theme config keys.** Checked against the user's installed Streamlit 1.66.1: `chartCategoricalColors`,
   `[theme.sidebar]`, `blueColor` and `blueTextColor` are all supported, so no CSS fallback is needed for them.
6. **Wide layout.** `st.set_page_config(layout="wide")`, because later tabs hold wide tables and charts.
7. **Sidebar placeholders.** The provider, key and model controls are shown disabled so the final layout
   is visible. They become live in F07; the provider list then moves to `llm/client.py`. Nothing typed
   in them is stored anywhere.
8. **Package files.** `__init__.py` files exist in `agents/`, `tools/`, `llm/`, `db/` and `ui/`.
   `.gitkeep` files keep otherwise empty folders in git. Modules for later features are not created yet.
9. **Git identity.** Local commits are made with the author "Claude" (`-c user.name`, `-c user.email`)
   without changing any git config. The user can set their own identity for later commits.

## F01 round 2: mint and teal colour scheme (user request)

10. **Mint page and sidebar background.** `backgroundColor`, `[theme.sidebar] backgroundColor`, `PAGE_BACKGROUND` and
    `SIDEBAR_BACKGROUND` are `#ADDBD8` (page was soft yellow `#FFF8E1`, sidebar light blue `#E3F2FD`). Round 2 used
    `#91D9D2` for the page; round 3 changed it to `#ADDBD8` at the user's request after seeing it. This changes the
    spec's section 13.1 colour roles; the table is otherwise unchanged. (Page colour superseded in round 4, item 17.)
11. **Teal replaces blue.** Streamlit's palette blue (`blueColor`, used by the info boxes) is `#14A89E`. In
    `ui/theme.py` the chart blues become teal too: supplier 1, "score vs best" supplier bars and "within budget"
    bars use `#14A89E`; the heatmap now runs from `#E0F5F3` to `#075A55` (still a single hue).
12. **Third supplier colour** changed from `#00897B` to `#7CB342`, because `#00897B` is too close to teal for
    chart lines to be told apart.
13. **Darker teal for text.** `#14A89E` on `#ADDBD8` has a contrast ratio of only about 2.0:1, so text on mint
    (info-box text, hovered tab labels) uses `#075A55` (`blueTextColor`, `TEAL_TEXT`). The accents (icons,
    borders, highlights) are exactly `#14A89E`.
14. **Selected tab.** Streamlit paints the selected tab with the primary colour (pink). CSS in
    `inject_theme_css()` makes it a `#14A89E` highlight with dark slate text, for all 8 tabs (so Setup and
    Overview included). The CSS targets `[data-testid="stTabs"]`, `role="tab"`, `data-selected` and
    `.react-aria-SelectionIndicator`, which exist in Streamlit 1.66.1. `primaryColor` stays pink, so
    buttons and focus rings are unchanged. (Superseded in round 4, item 21.)
15. **Left as they were.** Input and card boxes (`#F0F7FE`) are backgrounds, not accents, and stay pale blue.
    Warning boxes keep Streamlit's yellow so warnings still read as warnings. The sidebar now matches the page
    colour (round 3), so only Streamlit's own thin divider separates them. (Superseded in round 4, item 17.)
16. **Python version.** The user's `.venv` uses Python 3.14.6; the spec and Streamlit Cloud use 3.12. Later
    features will check each package installs on both.

## F01 round 4: layout and palette redesign (design left to Claude by the user)

The user found the flat single-colour look "really bad" and asked for partitions where needed and green/teal
tones that are not overpowering. Nothing in the wiring or tab order changed.

17. **Palette.** Main area `#EEF6F4` (very light mint), sidebar `#ADDBD8` (the user's chosen mint, now only on the
    sidebar so the two areas are visibly separate), cards and inputs white `#FFFFFF`, borders `#C5DEDA`
    (sidebar widget borders `#8FC4C0`). Body text stays `#1F2937`.
18. **Headings were never pink.** The F01 rule `h1, h2, h3 { color }` was overridden by Streamlit's own heading
    styles, so headings showed dark slate. It is now `.stApp h1, h2, h3 { ... !important }` and the colour is
    deep teal `#0B4F4A` (pink no longer fits the teal scheme). This replaces the spec's pink headings.
19. **Primary colour.** `primaryColor` is deep teal `#0B7A72` (buttons, focus rings), white text on it is 5.2:1.
    Pink survives only as the "over budget" chart colour, where it signals a problem.
20. **Partitions.** The header (title and subtitle) is a white card with a teal left stripe. Each tab's content
    sits in a white card. The sidebar is split into two white cards ("LLM" and "Evaluation run"). Cards are
    `st.container(key=...)` blocks styled through the `st-key-<key>` class in `ui/theme.py`
    (keys: `hero`, `card_0` to `card_7`, `side_llm`, `side_run`). The sidebar border is switched on with
    `showSidebarBorder`, widget borders with `showWidgetBorder`, and corners with `baseRadius = "0.75rem"`.
    All of these options exist in Streamlit 1.66.1.
21. **Selected tab.** Softer than round 2: a pale teal tint `#D5ECE8`, bold deep-teal text and a `#14A89E`
    underline, instead of a solid teal block. Hovered tabs get a lighter tint. The CSS hooks are the same
    as before (`role="tab"`, `data-selected`, `.react-aria-SelectionIndicator`), which worked in the user's
    screenshot.
22. **Sidebar text.** "Scoring temperature: 0 ..." is now a caption inside the "Evaluation run" card, and the
    "Settings" header sits above the cards.
23. **Not verified visually.** Streamlit can't run in the build workspace, so the card, header and sidebar CSS
    was checked against the installed Streamlit's code, not on screen. Contrast ratios were calculated
    (all text at least 5:1; see the round 4 snapshot).

## F02: Database and seed

24. **Setup tab is read-only.** Section 13.3 lists a criteria table, weight donut, gate list and weight-total check, but
    no editing controls, so none were built. To change a weight or switch a criterion off, edit the database
    (`evaluation_criteria.weight` / `is_active`); the tab, the weight check and later the prompts follow it.
    An edit form can be added if wanted.
25. **Tests added** (not in the section 4 list): `tests/test_database.py` (23 tests) and `tests/conftest.py`, which
    gives every test its own empty database through the `RFP_DB_PATH` environment variable, so tests never touch
    the real `rfp_evaluation.db`.
26. **Nullable feedback columns.** `reviewer_feedback.supplier_name` and `attempt_no` may be NULL, because run-wide
    actions (reweight, accept) have no supplier or attempt. The spec only marks criterion_id, rating and
    override_score as nullable.
27. **Where things live.** `create_schema()` is in `db/repository.py` (it reads `db/schema.sql`); `db/seed.py` calls it.
    Each seed table is filled only while it is empty, so reseeding adds nothing and never overwrites edits.
28. **Gate and benchmark text.** Gate descriptions are plain language; the rules themselves are coded in
    `tools/gates.py` (F04). Benchmark rule texts copy section 9.5. Its warranty "Above" rule says "3+ years
    on-site and repair of 1 day or less" while "Meets" says "3 years on-site"; F05 will state exactly how
    those two are told apart.
29. **Append-only audit log.** Two database triggers make UPDATE and DELETE on `audit_log` fail, and
    `audit.log_event()` refuses any details that contain a key such as `api_key`, `secret` or `password`
    (golden rule 6).
30. **Setup tab display.** Tables use `st.table` (it wraps the long descriptions; `st.dataframe` cuts them off).
    The donut has its own five teal/green colours, separate from the supplier colours, and uses
    `width="stretch"`, which this Streamlit version supports. Headings of level 4 get the heading colour too.
31. **Startup.** `app.py` creates and seeds `rfp_evaluation.db` (project root) on every start. If that fails
    (for example OneDrive holds the file), the page shows a friendly message instead of a stack trace.
    SQLite waits up to 10 seconds for a locked file.
32. **Requirements.** `pandas` and `plotly` were added. The user must run `pip install -r requirements.txt` again.
33. **Bug found by the tests.** A weight total of exactly 100.01 or 99.99 was rejected because of floating-point
    rounding; the check now rounds before comparing.
34. **What was and was not verified.** The 23 database tests were run in the build workspace. The Setup tab's logic
    was run against a real seeded database with stand-in Streamlit, pandas and Plotly modules, so the data it
    shows is verified, but the real rendering (and the Plotly options) is not. Those are checked on the user's
    machine (smoke test and screenshot).
