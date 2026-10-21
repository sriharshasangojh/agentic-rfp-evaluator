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

## F03: Ranking engine

35. **Exact arithmetic.** Scores are turned into exact fractions from their written form (0.1 stays 1/10), and
    rounding is done half up to 2 decimals. Python's own `round()` works on binary numbers and would turn
    62.505 into 62.5 instead of 62.51. The result cannot change with the machine or the order of the input.
36. **Plain inputs.** `rank_suppliers` takes small frozen data classes (`CriterionWeight`, `SupplierInput`), not
    database rows. That keeps `tools/ranking.py` free of I/O, and later features convert rows into these.
37. **Bad input stops the ranking** with a `ValueError` (missing score, score outside 0 to max, duplicate
    supplier name ignoring capitals, duplicate criterion id, max score of 0, negative weight, weights totalling 0).
    The validator (F04) and the run-start checks catch these first; this is the last safety net, so nothing is
    silently repaired. The "weights must total 100" block stays at run start, not here.
38. **What is rounded.** Only the absolute score and the Peer score (PPI) are rounded before sorting, as the spec
    says. The PPI is calculated from the exact relative %, not from the rounded ones shown in the table, so a
    hand calculation from the displayed numbers can differ by 0.01 in rare cases.
39. **Tie-break wording.** One line per adjacent pair with equal (rounded) PPI, naming the first rule that
    separated them: earlier submission date, then higher experience rating, then name A to Z. With a three-way
    tie you get two lines (A over B, B over C).
40. **Warnings.** Two plain-text warnings: only one supplier (Peer score is 100), and a criterion where every
    supplier scored 0 (relative % set to 100 for all). They come back in the result for the UI to show later.
41. **Eligibility is not in this module.** The spec says suppliers that fail a gate or are set aside are still
    ranked. This engine ranks everyone; the "Not eligible" / "Set aside" marking and the recommendation skipping
    them come with gates (F04) and insights (F10), so nothing here depends on them.
42. **Purity is tested.** One test reads `tools/ranking.py` and fails if it imports anything beyond the standard
    maths and data-class modules, or calls `open`, `print`, a clock or a random function.
43. **Bonus check.** The spec's what-if numbers (price weight 30%: NexaWorks 86.53, Apex 83.89) are reproduced by
    a test with hand-written weights. The what-if feature itself is not built; this only confirms the engine
    handles non-whole weights correctly.
44. **What was and was not verified.** All 29 ranking tests (52 in total with the database tests) ran in the build
    workspace on Python 3.10. The code is plain standard-library Python, so the same results are expected on your
    Python 3.14. The app smoke test still needs your machine.

## F04: Validator and gates

45. **Pydantic added.** `requirements.txt` now lists `pydantic` (v2, unpinned until M1). The models are
    `ExtractedFacts` (the 21 facts), `ScoredCriterion` and `EvaluationScorecard`. Types are strict: `true`,
    `"16"` and `NaN` are not numbers, and whole numbers stay whole (16, not 16.0).
46. **Unusable replies.** The reply must be a JSON object with a `criteria` list. Anything else (broken JSON,
    a bare list, no `criteria`) gives no scorecard and an `error`, so the caller can retry once as the spec says,
    then call `failed_evaluation()`. Valid JSON of the wrong shape is treated like broken JSON, because the retry
    message in section 7.1 already says "matching the required shape".
47. **Code fences are accepted** (not in the spec). A reply wrapped in a ```json block is used, with a warning.
    Reason: models often add the fence, and without this every such supplier would burn the retry and could end
    with all scores 0. Text before or after the JSON is still invalid. Easy to remove if you want it strict.
48. **Score text.** "8" and "8/10" are read. A fraction is scaled to the criterion's own maximum ("4/5" on a max
    of 10 is 8; "8/10" on a max of 20 is 16). "8/0", "eight", "8 out of 10", true, null and NaN become 0.
49. **Order of fixes for a score:** read, then clip to 0..max, then round half up to 1 decimal. Rounding never
    goes above the maximum. `max_score` always comes from the criteria passed in; a `max_score` in the reply is
    ignored.
50. **Every repair has a warning**, including rounding and "text read as a number", because spec 9.1 says each
    rule produces a warning. A clean reply has none.
51. **Small extra repairs** so a scorecard is always complete: a blank justification becomes "No justification
    returned"; blank evidence becomes "Not stated in the proposal"; a criteria entry that is not an object is
    dropped; an id written as "2" or 2.0 is accepted as 2; `risks` that is not a list is ignored and non-text
    entries dropped; an `overall_summary` that is not text is ignored. Missing `risks` / `overall_summary` is fine.
52. **Extracted facts.** The spec fixes the type only for `warranty_type` and `data_wipe`. I chose: yes/no for
    battery_measured, replacement_within_24h, price_table_present, bis_named, energy_rating_named,
    security_certification_named, encryption_before_delivery, price_lock_later_orders; text for laptop_model and
    processor; plain numbers for the rest. A fact of the wrong type becomes null with a warning. If
    `extracted_facts` itself is missing or not an object, every fact is null with one warning (the scorecard
    always carries an `ExtractedFacts`, never None). Unknown fact names are ignored without a warning.
53. **`filled_by_validator` flag** on each criterion. Spec 10.1 says cons include "criteria filled with 0 by the
    validator", so the scorecard has to remember which ones those were. `failed_evaluation()` marks all of them.
54. **Blind evaluation.** The `supplier_name` in the reply ("Supplier A") is dropped; the real name is attached
    later in Python.
55. **Gates.** Each gate's `rule_key` picks a rule in `tools/gates.py` (the four seeded keys). A missing,
    null or wrong-type fact fails with "Not stated". A gate with an unknown `rule_key` fails ("Unknown rule ...")
    rather than being skipped, so a mandatory check can't vanish silently. With no gates at all, everyone is
    eligible. Gates take a plain dict (`scorecard.extracted_facts.model_dump()`), so `gates.py` needs no pydantic.
56. **What was and was not verified.** pydantic cannot be installed in the build workspace, so the 108 validator
    tests and the 47 gate tests were run in a separate cloud container with pydantic 2.13.5 on Python 3.13. The
    gate tests, ranking tests and database tests also ran in the build workspace on Python 3.10 (99 passed). Not
    yet run on your Python 3.14. Recent pydantic versions support 3.14.
