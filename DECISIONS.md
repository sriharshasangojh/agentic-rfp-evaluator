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

## F05: Industry score and strength labels

57. **Grading rules where the spec table leaves a gap.**
    - *Warranty:* 3+ years on-site is Meets; add a repair time of 1 day or less and it is Above (Above is checked
      first, so exactly 3 years on-site with a 1-day repair is Above). Under 3 years or carry-in is Below. Years known but
      cover type not stated is Below ("on-site cover not confirmed"), because Meets needs on-site. No years: Not stated.
    - *Repair time:* replacement within 24 h is Above. The spec's "2 to 5 days" leaves a gap between 1 and 2 days; I put
      it in Meets. Over 5 days is Below.
    - *Battery:* under 8 h, or not confirmed as measured (false or not stated), is Below. Measured 8 to 10 h is Meets,
      over 10 h is Above. No hours: Not stated.
    - *Memory and storage:* falling short of either minimum is Below even if the other is above it (32 GB RAM with a
      256 GB SSD is Below). If one value is missing and the other isn't short, it is Not stated.
    - *Delivery:* over 8 weeks Below, 6 to 8 Meets, under 6 Above.
    - *Certifications:* BIS and energy rating is Meets; plus a security certification is Above; anything less is Below
      ("none named" or only some). If all three are null it is Not stated.
    - *Data wiping:* "certified_standard" is Above, "described" is Meets, null is Not stated (0 points). The spec's
      "Below = not described" can't be told apart from "not stated" with the facts we extract, so Below never occurs here.
58. **Prices.** A unit price or grand total of 0 or less is treated as not stated, because a model may write 0 instead
    of null. The estimate is `grand_total_rs / 1.18 / 100` as in the spec (18% GST; the 100 laptops is now the run's own quantity, see item 65; the fixed price limits are gone too) and the warning text is exactly "Unit price estimated from total". A stated unit price always wins.
59. **Weights.** Each attribute gets an equal share of its criterion's weight, using the active criteria passed in
    (seeded: unit price 20, warranty 5, repair time 5, battery 15, memory and storage 15, delivery 20, certifications
    10, data wiping 10). An attribute whose criterion is not active is left out and the average is taken over what
    remains, so the score is still out of 100.
60. **Rounding.** Exact fractions, half up to 2 decimals (78.125 becomes 78.13). The small rounding helper is copied
    into `industry.py` instead of shared: `ranking.py` and `validator.py` are approved, and the spec's folder list
    has no shared-helpers file.
61. **Unknown attribute.** A benchmark row with no grading rule is graded Not stated with a warning, not a crash.
62. **Strength labels** use exact fractions on the numbers as written, so 2.4 out of 3 is exactly 80% and Strong.
    `insights.py` holds only the labels for now; pros and cons, recommendation, what-if and negotiation selection are
    added by their own features. A maximum of 0 or less raises an error.
63. **Rupee text** in the breakdown uses Indian digit grouping (Rs 1,20,000).
64. **What was and was not verified.** 155 new tests (131 industry, 24 strength labels). With the existing ones, 254
    tests pass in the build workspace on Python 3.10 (the validator and app smoke tests can't run there). On your
    machine `pytest` should now report 364 passed (23 database, 29 ranking, 108 validator, 47 gates, 131 industry,
    24 insights, 2 smoke). No new packages. (Round 2 changed these counts: see item 71.)

### F05 round 2: price grading follows the budget (user request)

65. **User request: any price level must work.** The fixed Rs 45,000 / 60,000 limits and the fixed 100 laptops are
    gone from `industry.py`. `industry_score(facts, benchmarks, criteria, terms)` takes `terms`, a `PurchaseTerms`
    (the run's total budget in Rs and number of units), as a required argument. It supersedes the fixed constants in item 58.
66. **The bands:** Below = over the budget per unit (budget / quantity); Meets = 75% to 100% of it; Above = under 75%.
    At the spec's own scenario (Rs 60,00,000 for 100 laptops) that is exactly 45,000 and 60,000, and the 7 test
    proposals grade as before (Apex Below, BrightPath Above, four Meets, Vertexa Not stated). The 75% is my
    generalisation of the spec's 45,000 / 60,000 and lives in one named constant, `PRICE_MEETS_FROM_SHARE`.
67. **Estimating from the total** uses the run's quantity; the 18% GST divisor is still a constant. As in the spec, the
    estimate is before GST and is compared with the budget per unit (which includes GST).
68. **No budget given** (`terms=None`): the price is graded Not stated with a warning and every other attribute is graded
    as usual. An unusable budget or quantity (not a number above 0, not a whole number of at least 1) raises an error
    when the terms are created, so the input checks in F09 must block the run with a friendly message first.
69. **Standing requirement for later features (user request): budget and quantity are inputs of each run, never constants.**
    - F09: the New evaluation form gets "Budget (Rs)" and "Quantity" fields, pre-filled with the spec scenario
      (Rs 60,00,000 and 100), checked before the run, and stored with the run (the F02 `rfp_runs` table has no column
      for them yet, so F09 decides: new columns or the run's settings JSON).
    - F08/F09: the buyer requirements text given to the evaluator uses the entered budget and quantity.
    - F17 price-vs-budget bar, F18 scatter budget line, F20 negotiation ("over budget by ...", "within our Rs X
      budget") and the over/under colouring use the same values.
70. **Not made dynamic (yet):** the other baselines (RAM and SSD, battery hours, delivery weeks, warranty years, repair
    days) and the four gates (warranty at least 3 years, delivery within 8 weeks, and so on) still follow the Meridian
    laptop RFP. To reuse the app for a different product or RFP these would need to be editable too; ask the user
    whether that is wanted once the core features are done.
71. **Verification (round 2).** 47 new tests (178 industry, 24 strength labels). A deliberate-break check confirmed the
    tests fail if a fixed Rs 60,000 budget or a fixed quantity of 100 is put back, and one test fails if any fixed price
    limit appears in the source. 301 tests pass in the build workspace (Python 3.10; validator and smoke tests excluded).
    On your machine `pytest` should report 411 passed (23 database, 29 ranking, 108 validator, 47 gates, 178 industry,
    24 insights, 2 smoke).

## F06: PDF extraction

72. **Two PDF readers.** PyMuPDF is used when installed, pypdf otherwise (the spec names PyMuPDF; pypdf is a pure-Python
    fallback so the app still works where PyMuPDF will not install). Both are in `requirements.txt`. Anyone who already
    set up the project needs to run `pip install -r requirements.txt` again.
73. **Page marker format** is `[Page N]` on its own line before each page, pages separated by a blank line. A page with no
    text still gets its marker, so page numbers always match the real PDF and the evaluator can cite them.
74. **Chunks only, no search index yet.** The spec lists BM25 chunks under F06, but the index and search belong to F14
    (the "Ask the proposals" feature), so F06 only splits the text into chunks. This keeps `rank_bm25` out of
    `requirements.txt` until it is used.
75. **Chunk rules.** About 300 words per chunk, cut at line breaks (never mid-line), never crossing a page, so a citation
    such as "(Apex Systems, page 3)" is exact. A single line longer than 300 words is split by words. Trade-off: a page
    that ends with a short passage gives a small chunk (the smallest across the 7 proposals is 3 words, in BrightPath).
76. **Fingerprint** is the SHA-256 of the raw file bytes, not of the extracted text, so it is the same with either reader
    and changes if even one byte of the file changes.
77. **Text is lightly tidied, the same way for both readers:** line endings unified, spaces trimmed at both ends of every
    line, 3 or more newlines squeezed to one blank line, and the PDF bullet code 0x7F turned into a real bullet (see
    81). No Unicode normalisation and no removal of repeated page headers (each page of these PDFs carries a
    "Supplier | RFP ... | Page N" line; it stays, which is harmless and keeps the page text faithful).
78. **Friendly errors.** An unreadable file raises `PdfError` with a plain message (empty file, not a PDF, password
    protected, damaged, no pages, no reader installed, file cannot be opened). Programming mistakes (wrong type, unknown
    reader name, chunk size below 1) still raise normal errors. A scanned/image-only PDF is not an error: it reads as
    empty and returns a warning (OCR is out of scope, spec section 18).
79. **Nothing is printed or logged.** Proposal text is never logged (spec 2.2). Only the developer command
    `python -m tools.pdf_tool` prints, and only the first 80 characters per file; a test enforces this.
80. **Verification.** 61 tests run with one reader installed (51 in round 1, 10 added in round 2), plus 7 that run only
    when both readers are installed. They include the 7 real PDFs: page counts, supplier names, grand totals found in
    the text, fingerprints, chunk totals equal the page word counts. They pass on Python 3.10 and 3.13 with pypdf. A
    deliberate-break check (12 changes to the code) is caught by the tests. On your machine, with both readers
    installed, `pytest` should report 509 passed (484 after round 1, plus 25 in round 2).
81. **Round 2: both readers now give the same text.** Running `python -m tools.pdf_tool` on the user's machine
    (PyMuPDF) showed character counts 0 to 20 lower than the pypdf run. A word-level comparison found two causes in
    the pypdf fallback: (a) the PDFs store each bullet as code 0x7F, which the PDF rules display as a bullet; PyMuPDF
    and poppler return a bullet, pypdf returned an invisible control character; (b) pypdf left a space at the start of
    some lines. Both are now handled in `tidy_text`. After the fix the pypdf character counts equal the PyMuPDF counts
    from the user's machine for all 7 proposals (21,921 / 16,114 / 19,009 / 19,417 / 17,651 / 17,033 / 17,189). A
    new test compares the two readers page by page whenever both are installed. Other control characters are left
    as they are (none appear in the 7 proposals). The `Page1` seen once in the Orbit row of the user's pasted table
    was a copy-paste slip, not a reader difference: the full comparison for Orbit showed bullets only.
82. **Standing requirement: PDFs are uploaded at runtime, never built in.** Proposals reach the app only through the
    upload box on the New evaluation tab (F09, 1 to 10 PDFs). The file's bytes are held in memory and passed to
    `extract_pdf`; nothing is read from disk or saved into the project. No app code reads `data/proposals/` (checked: only
    the developer command `python -m tools.pdf_tool` and the tests do). Each uploaded PDF gets its supplier name,
    submission date and experience rating from the upload form. The 7 files in `data/proposals/` are sample material for
    the tests and for trying the app by uploading them yourself. Anyone with the app's link can upload their own PDFs.
    Open points to settle at F07 and F09 (not decided now): (a) on a shared deployment all visitors would share one
    SQLite file, so runs from different visitors would mix unless runs are kept per visitor; (b) a default key in
    the deployed `st.secrets` would let every visitor spend the owner's credits, while a key typed in the sidebar stays
    in that visitor's own session only.

## F07: LLM settings sidebar

83. **Provider addresses, headers and LiteLLM prefixes.** Read from each provider's own documentation on 2026-10-23.
    The key is sent only in a header, never in the address. Lists are asked for in the largest page size, and
    further pages are followed (at most 20).
    - OpenAI: `GET https://api.openai.com/v1/models`, `Authorization: Bearer`. Prefix `openai/`.
    - Anthropic: `GET https://api.anthropic.com/v1/models?limit=1000` (next page: `has_more` and `last_id` become
      `after_id`), `Authorization: Bearer` plus `anthropic-version: 2026-10-21`. Prefix `anthropic/`.
    - Google Gemini: `GET https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000` (next page:
      `nextPageToken` becomes `pageToken`), `x-goog-api-key` header. Prefix `gemini/`.
    - Cohere: `GET https://api.cohere.com/v1/models?endpoint=chat&page_size=1000` (`next_page_token` becomes
      `page_token`), `Authorization: Bearer`. Prefix `cohere_chat/`.
    - xAI Grok: `GET https://api.x.ai/v1/language-models`, `Authorization: Bearer`. Prefix `xai/`.
    - DeepSeek: `GET https://api.deepseek.com/models`, `Authorization: Bearer`. Prefix `deepseek/`.
    The `openai/` and `anthropic/` prefixes are the standard LiteLLM ones and were not re-read from LiteLLM's pages
    (those pages did not load); the other four were confirmed from LiteLLM's docs or examples.
84. **The spec says `cohere/`; I used `cohere_chat/`.** LiteLLM's Cohere page documents only `cohere_chat/` for chat
    models (it uses Cohere's v2 chat API by default) and does not mention `cohere/`.
85. **Anthropic header.** Anthropic's current docs show `Authorization: Bearer` as the main way and call `x-api-key`
    a legacy fallback that still works. I send Bearer plus the version header from the List Models page's own
    example (`ANTHROPIC_VERSION` in `llm/client.py`; the older `2023-06-01` is still listed in their version
    history). If a valid Anthropic key is ever refused, these two lines are the first place to look.
86. **Gemini key in a header.** The models.list page shows the key as `?key=...`, other Gemini pages show the
    `x-goog-api-key` header. I use the header so the key can never appear in an address (HTTP libraries quote
    addresses in their error messages). Not tried against the live service.
87. **xAI uses `/v1/language-models`** because it says what each model outputs; plain `/v1/models` has no such field.
88. **Which models count as chat models** (spec 6.2). Where a provider gives structured information I use it:
    Gemini `supportedGenerationMethods` contains `generateContent`; Cohere `endpoints` contains `chat` and the model
    is not deprecated; xAI and DeepSeek `output_modalities` contains `text`. For OpenAI, Gemini, xAI and DeepSeek I
    also drop names containing the whole words embedding, moderation, whisper, tts, dall-e, image, imagen, audio,
    transcribe, realtime, live, davinci, babbage, instruct, sora, veo, codex, computer or rerank. Anthropic's
    list is all chat models, so nothing is dropped. A future model with an unusual name could slip through or be
    dropped wrongly; the user always chooses the model, and a wrong choice gives a clear message.
89. **No model is hard-coded or pre-selected.** The list always comes from the provider (DeepSeek's docs already
    list different names from a year ago), and the dropdown starts with nothing chosen, so the person picks the
    judge on purpose.
90. **`call_llm` signature.** The spec's `call_llm(messages, temperature, json_mode, timeout)` has no way to say
    which model or key to use, so the first argument is `settings` (provider, model, key). It returns
    `LLMResult(text, tokens, cost_usd, warnings)`; `warnings` carries the "temperature was dropped" note, and tokens
    or cost are `None` when unknown (never 0). `backend` and `sleep` are optional extra arguments that let the tests
    use fakes.
91. **Errors and retries.** Provider errors are sorted by their class name and status code, not by their text
    (LiteLLM gives connection errors status 500, so names are checked first). Timeout, connection problem, 5xx and
    rate limit: one retry after 2 seconds, then an `LLMError`. Bad key, unknown model and other 4xx: no retry. If a
    provider refuses `temperature` (a 400 that mentions it), the call is repeated once without it and a warning is
    returned (spec 6.3). I did the same for `response_format` (JSON mode), since the spec says to rely on the
    prompt when JSON mode is not supported. Every failure becomes an `LLMError` with a fixed friendly message that
    names the provider. The provider's own wording is never shown, because providers sometimes echo part of a key
    in a 401 message, and errors are raised `from None` so no earlier error is attached.
92. **Libraries load on first use.** `httpx` and `litellm` are imported only when a model list or a call is made
    (LiteLLM is slow to import). Starting the app and running the tests need neither. If one is missing the message
    says to run `pip install -r requirements.txt`. Both are now in `requirements.txt`.
93. **How the key is handled.** It lives only in the password box (`st.session_state`). The per-session model cache is
    filed under a SHA-256 fingerprint of provider and key, never under the key itself. A key containing spaces,
    line breaks or non-ASCII characters is refused before any request is made. Printing the settings object never
    shows the key. A test fails if the client ever prints, logs, or puts a key in an address.
94. **Owner-provided default key** (spec 2.2 rule 6; this settles the open point (b) in item 82). If the app's owner
    sets one of `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), `COHERE_API_KEY`,
    `XAI_API_KEY`, `DEEPSEEK_API_KEY` in Streamlit secrets or the environment, the sidebar uses it when the key
    box is empty and says so; a typed key always wins; the key is never displayed. Nothing is set by default, so on a
    shared link each visitor must bring their own key unless the owner chooses otherwise.
95. **Stale choices are cleared.** Changing the provider or the key empties the model list, the chosen model and the
    message, so a model from one provider can never be used with another's key. Going back to a provider and key
    already validated in this session restores its list from the session cache without another request.
96. **Run lock and the accessor for later features.** The model dropdown is disabled while
    `st.session_state["run_in_progress"]` is true; F09 sets that flag while a batch runs. `get_llm_settings()` in
    `ui/sidebar.py` is what F08 and F09 call: it returns `None` until a key has been validated and a model chosen.
    The temperature note stays as plain text; temperatures are fixed in `llm/client.py` (`temperature_for`).
97. **What was not tested live.** There was no API key and no way to reach providers from the build workspace, so
    the requests were built from the documentation and the tests use fakes shaped like the documented replies
    (`tests/fixtures/models_*.json`, written by hand, not recorded). Streamlit cannot be installed in the build
    workspace either, so the sidebar's logic was run against a stand-in for Streamlit and the real-app tests in
    `tests/test_sidebar.py` run only on the user's machine. Fake keys in the tests avoid the `sk-` pattern so the
    milestone secret scan has no false alarms. That scan will still list a few code lines such as
    `api_key=key`; those are variables, not secrets.
98. **Verification.** 139 client tests and 16 sidebar tests. A deliberate-break check (26 changes to the client, such
    as a wrong address, a dropped page, a leaked error text, a removed pause) was caught every time. On the user's
    machine `pytest` should report 664 passed (509 before, plus 155).

## F08: Evaluation agent (one supplier)

99. **What F08 is.** `prompts/evaluation_system.md` is the spec 7.1 prompt copied word for word (cut out of the spec by
    a script, not retyped); round 2 added two sentences to it (111). `agents/evaluator.py` builds the two messages, calls `call_llm` (temperature 0, JSON mode
    when the model supports it), checks the reply with the F04 validator and returns a scorecard. It does not rank,
    compare suppliers or touch the database (the caller passes the active criteria). A test checks that every fact name
    the validator knows is asked for in the prompt, so the two cannot drift apart.
100. **Blind naming (spec 7.1).** The name is hidden with a whole-word, case-insensitive match. Spaces are optional
    between the words and at a lower-to-upper letter change, so "NexaWorks", "Nexa Works", "NEXA WORKS" and a name
    split over two lines all match. This is slightly narrower than "every occurrence": the name is not hidden inside a
    longer word (so a short name cannot damage "technical"). It is applied to the proposal and to reviewer feedback.
    Limits: only the name as typed is hidden. A product name such as "Apex ProBook" stays, and an e-mail address becomes
    "rhea@Supplier A.example". A shortened form of the name that was not typed (for example "Apex" when the name typed
    is "Apex Systems") is not hidden.
101. **Putting the real name back.** The reply is written about "Supplier A". In Python, "Supplier A" is replaced by the
    typed name in the justifications, evidence, risks, summary and the two free-text facts (model, processor). The
    spec only says "re-attach the real name"; showing "Supplier A" under a real name would be confusing. Evidence
    quotes therefore match the PDF again, except where the PDF spells the name differently from what was typed. F15
    (evidence checker) should ignore spaces and letter case when it compares.
102. **The proposal is untrusted.** Besides the prompt's own security rules, the words `<proposal>`, `</proposal>`,
    `<criteria>`, `<buyer_requirements>` and `<reviewer_feedback>` inside the proposal or the feedback are changed to
    `[proposal]` and so on, so a proposal cannot close its own block and add instructions outside it.
103. **JSON retry.** If the validator cannot use the reply, the same messages are sent again with the bad reply (cut
    at 8,000 characters) as an assistant message and the exact spec sentence as the next user message. At most two
    calls. If the second reply is also unusable the supplier gets all scores 0 and the warning "Evaluation failed:
    the reply was not valid JSON after one retry." (`failed=True`). A reply that is valid JSON but needs repairs
    (missing criterion, score out of range) is NOT retried: the validator repairs it and lists the warnings.
104. **Provider problems are raised, not hidden.** A wrong key, no network or a timeout (after the one retry inside
    `call_llm`) raises `LLMError`. A zero scorecard would look like a real result. F09 decides what a failed call means
    for the run, for example stop the whole run on a bad key but continue after one timeout.
105. **Requirements text.** Quantity and budget come from the run's `PurchaseTerms` (DECISIONS 69), for example
    "Budget: About Rs 45,00,000 including GST and services" and "Quantity: 50 laptops, one model". The other rows
    (specification, delivery, setup, security, certification, warranty) are the Meridian RFP and still fixed (70).
    The Indian-grouping rupee formatter is copied from `tools/industry.py` (as with 60), because that one is private.
106. **`reviewer_feedback`.** Only fills the prompt's block ("None" when empty). F13 decided: the comment is sent only with an AI
    re-check (`orchestrator.re_evaluate`), which no screen calls yet (DECISIONS 208).
107. **Tokens, cost, warnings.** Tokens and cost are added over both attempts (unknown stays unknown). A warning that
    appears in both attempts, such as "the provider did not accept the temperature setting", is listed once.
108. **How to see F08 before there is a screen.** `python -m agents.evaluator <pdf> [--supplier] [--provider]
    [--model] [--budget] [--quantity] [--show-prompt]`. `--show-prompt` needs no key and prints exactly what would be
    sent. A real call reads the key only from the provider's environment variable (for example `OPENAI_API_KEY`), never
    from an argument or a prompt, so it cannot end up in shell history or in this chat. The supplier name defaults to the
    file name. The real run is wired to the upload box in F09; the app itself never reads `data/proposals/` (82).
109. **The stand-in reply.** `tests/fixtures/eval_reply_apex_standin.json` was written by hand by the build agent from the
    Apex PDF. It is NOT output from a model. It lets the whole path (prompt, reply, validator, scorecard, name restored)
    be tested offline. A test checks that every evidence quote really is on the stated page of the Apex PDF. The sample
    PDFs are used by tests only, as test material (82).
110. **Verification.** 71 evaluator tests. A deliberate-break check (55 changes to the evaluator and the prompt file,
    such as a name match that ignores case, a missing retry, a constant budget, a leaked name, a changed security
    heading) was caught every time; on the first pass 5 slipped through, the tests were tightened and all 55 are caught
    now. On the user's machine `pytest` should report 735 passed (664 before, plus 71). Not done: no call to a real
    model, and no look at how real replies score. (Round 1 only; see 111 for round 2.)
111. **Round 2: two sentences added to the evaluation prompt (user approved: "if it enhances evaluation then go ahead").**
    The user ran F08 with `gpt-4o-mini` on the Apex PDF. It worked (1 attempt, 7,279 tokens, about USD 0.0015), and it
    showed two problems that come from the spec prompt's wording:
    - The prompt never says which facts are yes/no. The model answered `bis_named: "IS 13252"`, `energy_rating_named:
      "ENERGY STAR 8.0"` and `battery_measured: "measured"`. The validator correctly turned them into "not stated"
      (3 warnings), but then the certification gate (`bis_named is True`) would have failed Apex wrongly as "Not
      stated". Added to FACTS: the eight yes/no facts "must be exactly true, false or null, never text", with a BIS
      example. A test reads the yes/no fact names from the validator, so a ninth one cannot be forgotten.
    - The model added the risk "Proposal contains instructions aimed at the evaluator" although the Apex proposal has
      none. Added to SECURITY RULES: add that risk only if the proposal literally contains text telling the evaluator
      (or an AI) how to score or what to do; ordinary wording, buyer requirements and the supplier's own commitments
      are not instructions.
    Not changed (noted for later): 2 of 5 evidence page numbers were wrong (Page 2 instead of Page 3) and one quote lost
    a space ("NISTSP"); the F15 evidence checker is the place for that, and it must ignore spacing (101). The prompt's own
    example "(Page 2)" may nudge the model towards "Page 2"; not changed because it is spec text. `gpt-5-nano` timed out
    twice at the 60 second limit (reasoning model); the user chose to leave the timeout for later (it is one constant,
    `CALL_TIMEOUT` in `llm/client.py`, and a per-call argument). If it is changed, record it here and update the one test
    that expects 60.
    Verification (round 2): 73 evaluator tests (2 new). 58 deliberate breaks (3 new, on the prompt) all caught. On the
    user's machine `pytest` should report 737 passed (664 + 73). Whether the two sentences really fix the model's
    behaviour needs the user's re-run with a real key.

## F09: Full run (orchestrator)

112. **Where the code is.** `agents/orchestrator.py` holds the input checks, the run and the saving (no Streamlit, no
    printing). `ui/tab_new_evaluation.py` only draws the tab and shows the result. The run follows spec section 8 up to
    "save": input checks, create the run, read the documents, evaluate each supplier, gates, ranking, industry score,
    strength labels, save. The evidence check (F15) and the pros/cons wording (F16) are not in yet, so
    `evidence_found` stays empty (NULL) and `pros` / `cons` are empty lists in each supplier's saved result.
113. **Budget and quantity are saved with the run (user request, 69).** Two new empty-allowed columns on `rfp_runs`:
    `budget_rs` and `quantity`. A database made by F02 to F08 gets them the next time the app starts
    (`_add_missing_columns`, safe to repeat; old runs show empty). The tab pre-fills Rs 60,00,000 and 100 and shows the
    per-laptop figure under them. Both reach the model's requirements text, the price grade and the saved run.
114. **Submission date and experience rating (round 1: both typed by the user, starting empty).** Replaced in round 2:
    see 126 to 129.
115. **Input checks (spec step 1), each a plain sentence.** 1 to 10 files; a name for every file; no duplicate names
    (capitals and spaces ignored); every PDF readable and with text; a submission date for every file and, if a rating
    is given, a whole number from 1 to 5 (the rating became optional in round 2, 128); at least one active criterion; active weights total 100. All problems are listed together. Fewer than
    4 files only shows the info note. The button stays off while anything is wrong, so a blocked run cannot normally be
    started; the same checks run again inside `run_evaluation` as a second guard (status "Blocked", nothing created).
116. **Failure policy (my decision).** A model reply that is not valid JSON twice gives that supplier all zeros and the
    run continues (spec 7.1). A provider problem (wrong key, no network, rate limit or timeout after the retry) stops
    the whole run: status "Failed", a message naming the supplier, the work already saved stays saved, and nothing is
    ranked, because a ranking that includes a supplier who was never really evaluated, or leaves one out, would mislead.
    Any other unexpected error gives a friendly sentence with the error's type only. The audit log keeps the type and the
    file and line where it happened, not the error's own text, because a library could put a key into that text.
117. **A stopped run is marked "Interrupted".** If the page is closed, or the visitor clicks something while a run is
    going, Streamlit stops the script. The run is marked "Interrupted" with an audit event and the stop is passed on.
    To make that unlikely the uploader, the forms and the budget inputs are switched off during a run. The sidebar model
    lock is cleared in a `finally`, so nothing stays locked.
118. **What is saved per run.** `rfp_runs` (settings, totals), `run_documents` (file name, fingerprint, pages),
    `evaluation_attempts` (5 rows per supplier; the model's raw reply is kept once, on the first row), `supplier_results`
    (one row each; `result_json` holds rank, scores, gate details, every criterion with benchmark, gap, relative score,
    strength, justification and evidence, the extracted facts, the industry breakdown, risks, warnings, the tie-break note,
    PDF fingerprint and page count) and `audit_log` (run created, documents read, each supplier evaluated, validation
    warnings, gate results, ranking computed, run evaluated / failed / interrupted). The proposal text itself is not saved.
    The API key is not saved anywhere: a test searches a dump of the whole database for it.
119. **Run ID** is `RUN-YYYYMMDD-HHMMSS-<4 hex>` in UTC, like every stored time. A visitor in India sees a time 5 hours
    30 minutes behind their clock.
120. **Proposal text is kept in the visitor's session only.** The tab keeps each file's extracted text in
    `st.session_state` (`ne_pdf_cache`) so it is read once. F14 (Ask the RFP) will need that text and should read it from
    there; after a page reload the files must be uploaded again.
121. **Open point 82a (shared database) is still open.** On one shared link every visitor writes to the same SQLite file,
    so run history and scorecards would be visible across visitors. For a class demo that is fine and simplest; keeping
    runs per visitor can be done on request. Not changed.
122. **Suppliers are evaluated one after another**, not in parallel: the step log reads in order and provider rate limits
    are less likely. With a fast model, 7 suppliers take a few minutes at most.
123. **Small supporting changes.** `title_from_filename` in `agents/evaluator.py` is now public (it was
    `_title_from_filename`; two tests updated) because the tab suggests names from file names. `EvaluationOutcome`
    carries the model's `raw_reply`. The results table on this tab is a plain table; the Overview tab (F10) will have the
    badges and the recommendation card. The two tooltip texts for the peer and industry columns are the spec's.
124. **The page header still says "100 business laptops" and the Meridian RFP number** (static text in `app.py`),
    although budget and quantity can now differ per run. Not changed in F09; tell me if you want that line made
    neutral.
125. **Verification (round 1; see 131 for round 2).** 60 orchestrator tests, 6 new database tests, 1 new evaluator test and 8 tab tests. 55 deliberate
    breaks to the orchestrator and the database functions: on the first pass 3 slipped through (a float rating was
    accepted, spaces around a name were kept, a migrated column got the wrong type); the tests were tightened and all 55
    are caught. On the user's machine `pytest` should report 812 passed (737 + 75). The 8 tab tests (AppTest) and the
    tab itself were never run by the build agent because Streamlit cannot be installed in its workspace; the tab's logic
    was checked against a throwaway stand-in for Streamlit. Not done: no full run with a real model yet.

126. **Round 2 (user request): the date should come from the RFP response, and the rating should be optional.** The user
    asked why they should type the submission date when it is in the proposal, and then asked to keep the experience
    rating optional. The spec (1.3) says both are typed in the form; the user's request overrides that line. Every one of
    the 7 sample PDFs states "Submission date: ..." on page 1, which the build agent had not checked in round 1.
127. **The date is read from the PDF by plain text matching, no model.** `find_submission_date` in
    `agents/orchestrator.py` looks for a label ("Submission date", "Date of submission", "Submitted on", "Date
    submitted") and reads the date written right after it: "5 October 2026", "5th Oct, 2026", "October 5, 2026",
    "2026-10-05", or "05/10/2026" (day first, as written in India). An impossible date (31 February) is skipped and the
    next label is tried. A date with no label is never used, so "valid for 60 days from 5 October 2026" cannot be
    mistaken for it. The box is pre-filled and can be changed; a date outside 2000 to 2100 is not pre-filled. When no date
    is found the box stays empty and the run is blocked until one is picked. (Round 3 replaced the last two rules; see 132.)
128. **The experience rating is optional.** Left empty it means "not rated": it is stored as empty (NULL), the results
    table shows "Not rated", and it is only used as the third tie-break (after score and submission date): a rated
    supplier, even rated 1, comes before an unrated one, and the note reads "higher experience rating (4 vs not rated)".
    Two unrated suppliers that tie are ordered by name. A given rating must still be a whole number from 1 to 5. The
    spec's input check "every PDF has ... experience rating" is relaxed accordingly. The tooltip gives a simple guide:
    1 = new, no references; 3 = some relevant projects and a few references; 5 = many years, many similar projects and
    repeat clients.
129. **Code and database changes for 128.** `tools/ranking.py` (F03, still a pure function) accepts a missing rating
    (type `int | None`, one small sort helper); 3 tests added to `tests/test_ranking.py`. `supplier_results.experience_rating`
    is no longer NOT NULL (the 1 to 5 check stays). A database made earlier is rebuilt once on the next start (rename,
    copy the rows, drop the old table, in one transaction), and a second start leaves it alone; tests cover both.
130. **Date and rating boxes on the tab.** Each card says "Submission date read from the PDF; change it if it is wrong." or
    "No submission date found in the PDF, so pick one." The rating box reads "Experience rating (1 to 5, optional)" and
    shows "Not rated" until a number is chosen. Round 1's red messages for a missing date and rating no longer appear
    when the PDF has a date. (The notes under each card were reworded in round 3: see 132.)
131. **Verification (round 2).** 87 orchestrator tests (27 new: date finder and rating rules, including all 7 sample
    PDFs giving the dates in the spec table), 32 ranking tests, 31 database tests, 74 evaluator tests and 10 tab tests.
    28 more deliberate breaks (date reading, rating rules, the rebuild of the table): one slipped through on the first
    pass (rebuilding the table on every start), a test was added, and all 28 are caught. The user's own run of this
    round reported 846 tests, 843 passed and 3 failed (causes and fixes in 134 and 135). The tab and its 10 tests were
    still never run by the build agent (no Streamlit here).
132. **Round 3 (user request): the date is read and filled in for any PDF.** The user pointed out that the date box must
    be filled for any uploaded PDF, not only for PDFs with a "Submission date:" label. `suggest_submission_date` in
    `agents/orchestrator.py` tries three things in order, with plain text matching and no model. (1) A labelled date
    anywhere in the file: "Submission date", "Date of submission", "Submitted on", "Date submitted", "Submitted",
    "Proposal date", "Date of proposal/response/offer/quotation", "Response date", "Quotation/Quote/Offer date",
    "Dated", or a line that starts with "Date:". (2) Else the first date written on page 1 that is not a validity
    period, deadline or delivery date (a date is skipped when, earlier on its own line, there is "valid", "until",
    "expires", "deadline", "due", "closing", "before", "issued", "issue date", "opening", "within", a "deliver" word or
    "by"). (3) Else today's date (the visitor's browser time zone when known, otherwise this computer's date). The box
    is therefore never empty, and the "pick a date" message no longer appears. Each card says which of the three
    happened: "Submission date read from the PDF; change it if it is wrong.", "No submission date label found, so the first
    date on page 1 is filled in; change it if it is wrong." or "No date found in the PDF, so today's date is filled in;
    change it if it is wrong." Dates are understood day first (05/10/2026) and in the usual written forms ("5 October
    2026", "5th Oct, 2026", "5-Oct-2026", "October 5, 2026", "2026-10-05"). Years 1990 to 2100 count as dates; the date box
    accepts the same range. This replaces the "never use a date without a label" and "leave the box empty" rules of 127.
133. **The trade-off in 132 (please read).** Filling the box for every PDF means a wrong date can be filled in silently
    by step 2 or 3, which is why each card names the step that was used and the box stays editable. The date only
    matters when two suppliers have exactly the same peer score (it is the second tie-break, ahead of experience), so a
    wrong guess changes the order only in that case. A labelled date is still preferred to any guess. If you would
    rather have the box left empty (and the run blocked) when no label is found, that is a one-line change.
134. **The sidebar lock (found by the user's pytest run).** `tests/test_sidebar.py::test_the_model_box_is_locked_while_a_run_is_in_progress`
    failed on the user's machine. Cause: the New evaluation tab used the shared `run_in_progress` flag to decide "run now",
    and cleared it when the inputs were not ready, so a flag set elsewhere (as that F07 test does) was wiped. Fix: the
    Evaluate button's callback now also sets a private `ne_run_requested` flag, the tab runs the batch only when that
    flag is present (and removes it at once), and `run_in_progress` is set and cleared only for a run this tab started.
    One test added to `tests/test_tab_new_evaluation.py` (the tab leaves a flag it did not set alone).
135. **A file I forgot to deliver (my mistake).** The two other failures (two `tests/test_evaluator.py` prompt tests) were
    because the F08 round-2 prompt sentences (DECISIONS 111) were in the build workspace copy of `prompts/evaluation_system.md`
    but had never been copied to the user's machine. They are there now (`git diff` shows exactly the 7 added lines).
    Check made this round: the checksums of every changed file in the build workspace were compared with the user's
    machine, and all 8 files that tests depend on and that were not changed this round match.
136. **Verification (round 3).** 140 orchestrator tests (53 more than round 2) and 11 tab tests (13 cases). 29 deliberate
    breaks of the date code (first or last date, reading order, same-line context, the "delivery" word, label order, the
    first and last year, each date format): 7 slipped through the first time, tests were added, and all 29 are now caught.
    The tab was run only against a stand-in for Streamlit here (upload, forms, click, live log, redraw, lock released), never
    in real Streamlit. On the user's machine `pytest` should now report 902 passed (846 from the last run + 53 orchestrator
    + 3 tab).

## F10: Overview tab

137. **Where the code is.** `ui/tab_overview.py` draws the tab; its `build_overview` works out everything shown from a
    saved run and uses no Streamlit and no database. `tools/insights.py` (still pure) holds the recommendation rule and
    the industry warning. `db/repository.py` has two new reads, `get_run` and `list_supplier_results`. `agents/orchestrator.py`
    gains two tiny helpers (`format_submitted`, `format_experience`) so the New evaluation table and the Overview write
    dates and ratings the same way; the F09 table itself is unchanged.
138. **Which run the Overview shows.** The run that just finished in this browser session. New evaluation stores its id in
    `shown_run_id` when the status is "Evaluated", and the Overview reads that run back from the database, so what is
    shown is exactly what was saved. A run that stops or fails does not replace what is shown. After a page reload the
    Overview is empty until the run history (F21) can reopen a run. I did not use "the newest run in the database" on
    purpose: with open point 82a (everyone sharing one database on a public link) that could show another visitor's run.
139. **Recommended and runner-up (spec 10.2).** Recommended = the highest-ranked supplier that passes every mandatory
    check. The spec says the runner-up is "the next one"; I read that as the next supplier that also passes every
    check, not simply the next rank, because the runner-up is the fallback if the recommendation is rejected and a
    supplier that fails a check cannot be recommended. If you want the next rank whatever its checks, it is a one-line
    change. A supplier the buyer sets aside (F19) will be passed in as not eligible, so this rule needs no change then.
140. **The sentence under the recommended supplier.** The spec says the reason is worded by the summarizer, which is F16.
    Until then Python writes one factual sentence: "Ranked 1 of 7 and passes every mandatory check.", or, when better-ranked
    suppliers fail a check, "Ranked 3 of 7. A and B rank higher but do not pass every mandatory check (hover over the Not
    eligible badge in the table to see why)." F16 adds the worded reason next to it.
141. **Nobody passes every check.** The card says "No supplier meets every mandatory requirement." and names the top-ranked
    supplier with the checks it failed (spec 10.2). No supplier is recommended and no industry warning is shown.
142. **The industry warning.** Only for the recommended supplier, and only when its industry score is under 60: "Below industry
    standard on: <the attributes graded Below or Not stated>". The spec also asks for a link to negotiation; negotiation is
    F20, so the link is not there yet.
143. **The leaderboard and the strength table are small HTML tables.** The spec wants an Eligible badge whose hover shows the
    failed gate, and Streamlit's data table cannot do a coloured badge or a hover on one cell. Every piece of text from a
    proposal or from the buyer goes through `html.escape`; each table is one line of HTML with its own style, because
    Streamlit ends an HTML block at a blank line. Touch screens cannot hover, so a caption says where to hover, and when
    nobody is eligible the failed checks are also written in the card. The badge always prints the word.
144. **Strength cells show the word and the marks**, for example "Strong (9/10)". The spec only requires the word; the marks are
    my addition so a manager can see what is behind the label. The label is worked out again from the saved marks when the
    page is drawn (`strength_label`), not read from the stored label, so it always follows the saved marks (F13 changed no marks; see DECISIONS 198).
145. **Tooltips.** The spec's PPI and Industry score tooltips are on the column headings and on the card's numbers. "Overall /100"
    got one too, my wording: "The weighted total of the marks for each criterion, out of 100."
146. **Left for later features (by the backlog).** Pros and cons and the worded reason (F16), the price vs budget bar (F17),
    evidence found marks (F15), warnings per supplier (Scorecards, F11). No chart is added in F10.
147. **Verification.** 11 new insights tests, 9 new database tests, 2 new orchestrator tests and the new
    `tests/test_tab_overview.py` (29 tests on the Overview's own functions, 5 on the real app). 51 deliberate breaks of the new
    code: 3 slipped through the first time. One was a real gap (the database test used names whose alphabetical order equals
    their rank order, so sorting by name passed); the test now uses reversed names and catches it. The other two are in the
    code that draws the screen and are only reachable by the real-app tests, which cannot run in the build workspace;
    I checked those two screens against the Streamlit stand-in instead (run not found, nobody eligible). Round 2: the user's
    own run reported 958 tests, 956 passed and 2 failed. Both failures were a mistake in my new real-app tests, not in the tab:
    the helper made a new fake model for every call, so every supplier received the first supplier's reply (all scores equal,
    every peer score 100) and the "stopped run" test never saw its error. It now uses one fake model for the whole batch. The
    same run also confirms the other three real-app tests (empty tab, run not found, nobody eligible), which covered the two
    screen mutations that I could only check against the stand-in. `pytest` should now report 958 passed.
148. **No screenshot of the real app.** Streamlit cannot be installed in the build workspace, so the snapshot shows the
    real table HTML drawn in a browser on a page with the theme colours; the recommendation card there only imitates the
    Streamlit number boxes. Please check the live tab (steps in the snapshot).
149. **Round 3 (user request): the runner-up looked grey.** It was a line of small grey note text. It is now its own strip
    under the recommended supplier: a "RUNNER-UP" label in dark teal, the supplier name in bold deep teal, and
    "Ranked 2 · Peer score 85.56" in dark slate (the page's normal text colour), on the soft mint tint used for the
    recommended row of the table, with a teal bar on the left. It stays smaller than the recommended supplier (1.15 rem against
    1.7 rem) so the recommendation is still the first thing seen. Only the runner-up changed; the sentence under the
    recommended supplier and the other notes keep the small grey style. Two tests added (the strip's colours and content,
    and that a name cannot inject HTML); 6 deliberate breaks of the strip, all caught. `pytest` should now report 960 passed
    (958 + 2).

## F11: Scorecards tab

150. **Where the code is.** `ui/tab_scorecards.py` (tests in `tests/test_tab_scorecards.py`). No other source file changed. It
    shows the same finished run as the Overview (`shown_run_id` in the session, DECISIONS 138) and reads it back from the
    database, so it shows exactly what was saved. Nothing is calculated here: the benchmark, gap and relative % were worked
    out by `tools/ranking.py` when the run was saved.
151. **The supplier picker.** One dropdown, best rank first, written "1. Zenora Systems". It starts on the recommended supplier
    (or the top-ranked one when nobody is eligible). The widget key contains the run id, so a new run starts on its own
    default instead of keeping the last choice.
152. **The header.** Name, rank ("Ranked 2 of 7"), a Recommended pill when it is the pick, the Eligible / Not eligible badge
    (hover shows the failed checks, as on the Overview), submission date, experience rating and the three scores. These come
    from the Overview's own row, so the two tabs can never disagree.
153. **The key-facts strip.** Five tiles in the spec's order: Total price (the unit price under it), Warranty (years, and on-site
    or carry-in), Delivery (weeks), Battery (hours, and measured or claimed), RAM / SSD. A fact the model did not find, or
    returned in the wrong form, shows "Not stated" in grey italics. Prices use Indian digit grouping like the rest of the app.
    I left out the other facts the model reads (laptop model, processor, weight and so on); the spec lists five.
154. **The score-vs-best chart.** Horizontal bars, one pair per criterion, in marks (not percentages): this supplier in the theme
    teal and the best mark in this run in grey (#B0BEC5). The spec's blue #1565C0 was replaced by the teal because of the
    colour scheme you chose. The axis runs from 0 to the largest maximum mark plus 8% so the number labels fit, and the hover
    shows "7 of 10". The supplier's name is not part of the hover text (it comes from a file name). This is the app's second
    chart of the six allowed. I could not draw it in real plotly (it cannot be installed here), so it is tested against a
    stand-in and the real drawing is checked by the AppTest on your machine.
155. **The criterion table.** The spec's columns (score, benchmark, gap, relative %, weight, evidence, justification) in that
    order, with a hover help on each calculated column. The gap is shown as stored (0 for the best, negative otherwise), the
    relative % with one decimal. The "found / not found" tick is not here: it belongs to F15. A criterion whose evidence says
    "Not stated in the proposal" gets a "Not stated" flag, and one the model gave no mark for gets a "No result returned"
    flag, so missing criteria are easy to spot (spec 16.3 step 4).
156. **The industry comparison.** One row per attribute: grade (the word, always, in a colour: Above green, Meets teal, Below
    red, Not stated grey), points, weight (its share of the 100 industry points) and the reason. A note under it gives the point
    values and says the baseline values are planning assumptions for a fictional startup, not market research (spec 9.5).
157. **The mandatory checks.** Every check is listed as Passed or Not passed (the word, in green or red) with its reason, so a
    failed check can be seen without hovering. A check whose saved result is missing or unclear is shown as Not passed. This
    only affects what is shown; eligibility still comes from the saved flag.
158. **The warnings.** Listed exactly as saved (validation notes, the unit-price estimate, ranking notes). They are drawn in my
    own amber box from escaped text, not with `st.warning`, because a warning can quote the model's reply and `st.warning`
    renders Markdown (links and images). A supplier whose evaluation failed also gets a red message at the top. With no
    warnings the tab says so.
159. **Left for later.** The evidence tick (F15), pros and cons (F16) and price vs budget (F17). The model's overall summary and
    risks are saved in the result but are not on this tab, because the spec's Scorecards list does not include them.
160. **Odd saved data.** A result with parts missing shows friendly messages ("No criterion results were saved for this
    supplier.") and numbers it cannot read show "n/a". The Overview's row-building (F10, approved) still assumes that
    criterion ids and check entries are well formed, which they always are in runs saved by this app, so a hand-edited
    `result_json` with junk there would stop both tabs. I did not change F10 for this.
161. **Verification.** 76 tests (67 run in my workspace with stand-ins for Streamlit and plotly; 9 need real Streamlit and run on
    your machine). 107 deliberate breaks of the code (78 checked by the tests, 29 of the screen code checked with a throwaway
    script), all caught. The tests found one real bug while I wrote them (a warnings field holding plain text was read letter
    by letter); fixed. A browser preview of the tables for Apex and Vertexa looked right. The page and the chart have not been
    seen in real Streamlit by me. `pytest` should report 1036 passed (960 + 76).
162. **Round 2 (user request): no second leaderboard on New evaluation.** The ranked table that F09 put under the run
    result repeated the Overview leaderboard, so it is gone, with its tie-break notes and its warnings list (both are on the
    Overview and Scorecards tabs). In its place is a "Top three" podium, one line per place, e.g. "Rank 1 · Gold – Zenora
    Systems [Winning RFP] Peer score 87.78", with a small angel wing in gold, silver or bronze instead of a medal. The wing is a
    drawn image (an SVG), not the wing emoji, because older Windows fonts do not have that emoji. Wings follow the rank; the
    "Winning RFP" tag follows the recommendation (the highest-ranked supplier that passes every mandatory check), so a
    top-ranked supplier that fails a check keeps its gold wing, gets a "Not eligible" badge (hover shows why) and no tag. If
    the winner is ranked 4th or lower, or nobody passes, a line under the podium says so. The success message, run ID,
    tokens, cost and step log stay. A line points to the Overview and Scorecards tabs (and counts the warnings). The table
    helper `orchestrator.leaderboard_rows` is no longer used by any screen, so it was removed and its four tests now check the
    supplier summaries directly. 9 new podium tests; 14 deliberate breaks of the podium, all caught. `pytest` should now
    report 1045 passed (1036 + 9).
163. **Round 3 (user request): the podium lines are only rank, wing and supplier.** Each line now reads "Rank 1, [gold
    wing], Zenora Systems" and nothing else: no Gold / Silver / Bronze word, no peer score, no "Winning RFP" tag and no
    "Not eligible" badge. The wing colour alone tells the place. Because the tag is gone, a line under the podium names the
    winning RFP whenever it is not the rank 1 supplier ("Winning RFP: NexaWorks (rank 2). The supplier ranked above it does
    not pass every mandatory check."), or says that nobody passes. The podium tests went from 9 to 7 (the tag and badge tests
    are gone, a test now checks that the visible words are only the ranks and names); 11 deliberate breaks, all caught.
    `pytest` should now report 1043 passed (1036 + 7).

## R1: Readable results (change pack R1)

### R1a: Overview

164. **Plain labels, on screen only.** Criterion names read Laptop specs, Delivery plan, Value for money, Security and
    Support (`insights.CRITERION_LABELS`); any other criterion name shows as itself. The Strength by criterion table, the
    marking scheme, prompts, database and JSON export keep the real names. In R1a the plain names appear in the wording of
    the good points and things to watch; the Scorecards use them in R1b. "Mandatory checks" is now "Must-haves" and the
    Peer score is "Score vs other bids (PPI)" (the tooltip text is unchanged).
165. **Must-have column names are keyed by the stored gate id, not by `rule_key`.** The pack said to key them by `rule_key`,
    but a saved run keeps each gate's `gate_id` and `name`, not its rule key, and the pack forbids a schema change. The four
    seeded ids map one to one to the rules: warranty = "3-yr warranty", certification = "BIS certified", price_table =
    "Price list", delivery = "Delivery <= 8 wks" (a test checks the label table covers exactly the seeded gates). A gate
    without a short name shows its own name. If gates ever become editable (open point 70) the short names must be derived
    from the rule instead.
166. **Short plain reasons for "Not eligible".** `insights.gate_problem` turns a failed must-have into a few lower-case
    words: "warranty under 3 years", "warranty not stated", "BIS certificate not named", "no price list", "delivery over 8
    weeks", "delivery time not stated" (the reason "Not stated" means the proposal did not say). An unknown gate gives
    "Name: reason". A failed evaluation gives "the evaluation failed"; a supplier that is not eligible but has no details
    saved gives "a must-have was not met". The spec's badge word "Not eligible" is kept (spec 9.3), with the short reason
    beside it; the hover still lists the full reasons.
167. **Verdict banner.** One bordered card whose left edge is green when everything is good, amber when there is any
    catch, red when nobody is eligible. Heading "We recommend <name>"; one sentence built only from facts of the saved run:
    "Ranked K of N", the names of suppliers ranked higher (they are all not eligible, by the definition of the
    recommendation), "meets every must-have", then the price: "and fits the Rs 60 lakh budget", ", but is Rs 13.99 lakh over
    the Rs 60 lakh budget" or ", but its price is not stated". A run saved without a budget says nothing about the budget.
    Up to 4 pills, each with a symbol and words so that colour is never the only signal: the rank (green tick when rank 1,
    amber warning when not), "Meets every must-have", the price pill (Within budget / Over budget by Rs X lakh / Price not
    stated) and the industry catch "Below typical deals on: A, B (+N more)" (at most 2 names, generic wording when none is
    named; the full warning is under Show details). Pill text is amber-brown `#8A5300` on `#FFF3E0` (contrast about 5.8:1), darker
    than the Average cell colour, which is about 3.9:1.
168. **Nobody eligible.** Red banner "No supplier meets every must-have" (the plain-language form of spec 10.2's "No
    supplier meets every mandatory requirement"), a sentence naming the top-ranked supplier and what it failed, and one red
    pill per failed must-have (up to 4).
169. **Budget line, lakh and price facts.** "Budget for this run: Rs 60 lakh for 100 laptops" comes from the run (left out
    for a run saved without a budget; "1 laptop" in the singular). Lakh display: at most two decimals, rounded half up on
    the exact amount, trailing zeros dropped, and a positive amount never shows as 0 (Rs 0.01 lakh at least). The price is
    `extracted_facts.grand_total_rs`; a total that is missing, zero, negative, text, a boolean, NaN or infinite is "not
    stated"; a total equal to the budget is within it. The price vs budget chart stays F17.
170. **Must-haves grid.** One row per supplier in rank order (the recommended one carries the pill), one column per
    must-have found in the saved run (order of first appearance, best-ranked supplier first). A green tick only when the
    saved value is exactly `True`; any other value is a red cross (fails closed). A supplier with no entry for a column shows
    a grey dash and "Not checked" on hover; a failed evaluation shows a cross in every column with "Evaluation failed". The
    reason is the hover text. A hint line sits under the grid; a run with no must-haves shows one line instead of an empty
    grid.
171. **Pros and cons (spec 10.1) are selected in Python now** (`insights.select_pros_cons`, the Python half of F16); F16 later
    replaces only the wording. Where the spec leaves room I decided: (a) a benchmark of 0 never makes a pro, because
    everybody is "100%" of 0; (b) "Meets every must-have" has no score to order by, so it follows the criteria pros, and
    only when at least one must-have exists and all passed; (c) a criterion appears once among the cons (gap first, then
    evidence, then not stated); (d) weight x score and weight x |gap| are worked out exactly (fractions), so ties keep the
    marking-scheme order; (e) evidence not found has no effect until F15 fills `evidence_found`; (f) a failed evaluation has
    no pros or cons. Wording (fixed Python templates, plain labels): "Strong on Security (9/10, best of all bids)" for a
    leader with 80% or more, "Best of all bids on X (6/10)" for a leader below that, "Close to the best bid on X (9/10; best
    bid 10/10)", "Weaker on X (5/10; best bid 8/10)", "No result for X (counted as 0/10; best bid 8/10)" when the
    validator filled it, "Not eligible: Warranty - 1 year (at least 3 required)", "Evidence for X was not found in the
    proposal", "X not stated in the proposal". The spec's labels "vs this batch" / "vs requirement" are shown small after
    each item.
172. **Why the top three.** A card for each of ranks 1 to 3 in a grid that wraps on a narrow screen: name (and the
    Recommended pill), "Ranked N - Score vs other bids 87.78" (hover = the PPI text), a red "Not eligible: <short reason>"
    flag, then at most 2 good points and 1 thing to watch. A failed must-have is not repeated among the points because the flag
    already says it. A card with nothing to say says so; a failed evaluation says why there is nothing.
173. **What moved under "Show details (full leaderboard, scores out of 100, tie-breaks)"** (closed): the run line, three
    one-line explanations (built from the existing tooltips; the PPI one adds "This decides the ranking."), the leaderboard
    (header "Score vs other bids (PPI)", badge "Meets must-haves" or "Not eligible" plus the short reason, tooltips
    unchanged), the tie-break notes, the hover tip (only when somebody is not eligible), the runner-up strip, and the full
    industry warning. The Strength by criterion table and its note are unchanged and stay visible, after the grid. The old
    recommendation card is gone: its three big numbers are in the leaderboard, and its one-sentence reason
    (`Overview.reason`) was dropped because the banner sentence replaces it. The pack does not list the sentence among
    what moves, so it is not kept. The AppTest checks on the three metrics were rewritten.
174. **Left as it was, on purpose.** `tab_overview.ELIGIBLE` still reads "Eligible" because the Scorecards header badge
    prints it until R1b. No schema change, no new package, no LLM call, no new chart (still 2 of 6) and nothing from spec
    Section 18: no ranking bars, stacked bars or what-if sliders (the user said the ranking bars from the mockup can be
    revisited later).
175. **Small hardening.** `_number` in the Overview now refuses NaN and infinity. The new readers (gate details, criteria
    for the points) ignore anything that is not a list or a dictionary of the right shape and leave out a criterion without
    a score, a maximum and a weight. The older F10 readers (`_strengths`, `_why_not`, `_weak_attributes`) are untouched, so
    the limit in item 160 for hand-edited data still stands. 143 new tests (83 in `test_insights.py`, 60 in
    `test_tab_overview.py`); 71 deliberate breaks of the new code, all caught (7 slipped through the first time and each
    got a test). `pytest` should now report 1186 passed (1043 + 143).

### R1b: Scorecards

176. **Header card.** One white card with a coloured left edge: the small line "Supplier scorecard - Ranked N of M", the
    supplier's name, and under it a status chip. Green "tick Meets every must-have", red "cross Not eligible: <first problem
    in plain words> (+N more)" with every failed must-have on hover (one per line), or grey "No must-haves were checked".
    The grey one is my decision: a supplier with no must-have on record (an odd or hand-edited run) is not told it "meets every
    must-have" when nothing was checked. The Recommended pill sits next to the chip (the pack puts it with the status). A
    failed evaluation reads "Not eligible: the evaluation failed". The pack says "first failed must-have"; I added "(+N more)"
    so that nobody reads one problem as the only one. On the right, the Score vs other bids is one big number with two
    decimals ("87.78 / 100"), hover = the PPI text unchanged. The old "Eligible" badge word is gone, so `tab_overview.ELIGIBLE`
    (item 174) was removed; "Not eligible" stays (spec 9.3). The line "Submitted ... Experience rating" stays under the card.
177. **Key-fact tiles.** The same five tiles in the same order, each with a small picture (money bag, shield, truck, battery,
    disk). The pictures are decoration only (hidden from screen readers); the label is always printed. A tile turns red-tinted
    when the proposal does not say ("Not stated" is printed too, so colour is never the only signal). The price tile also
    turns red-tinted when the total is over the run's budget (the run's own, DECISIONS 69, worked out by
    `insights.price_facts`) and says "Rs 13.99 lakh over budget" above the per-laptop line. No over-budget line when the price
    is inside the budget, exactly on it, not stated, or when the run was saved without a budget; only the price tile can say
    it. When only one half of RAM / SSD is stated the tile is not tinted and its detail line says which half is missing
    (my choice: a value is there, so the tile is not an empty one).
178. **What's good / Watch out.** Two cards side by side (they stack on a narrow screen), filled by `insights.select_pros_cons`
    exactly as the Overview cards (item 171), at most 3 each (spec 10.1), each point followed by the small spec label
    ("vs this batch" ...). Unlike the Overview cards, a failed must-have IS listed here, as a red cross, because the header
    names only the first; every other weak point is an amber warning sign. An empty card says so calmly ("Nothing stands out
    against the other bids." / "Nothing stands out as a weak spot."); a failed evaluation shows one line instead of both
    cards. The wording stays Python's until F16.
179. **How it did on each area.** One row per criterion in marking-scheme order: the plain label (the real name on hover, only
    when it differs), a 5-dot meter and the Strong / Average / Weak word (spec 9.6, the same function as the Overview
    table). Dots = score / maximum x 5, rounded to the nearest dot with exactly half rounding UP, worked out in
    `insights.meter_dots` with exact fractions. I did not use Python's `round()`: it rounds 2.5 to 2, so 5 out of 10 (which
    is Average) would show 2 dots. With half-up it shows 3. Result, tested at every tenth of a point: Weak has 0 to 2 dots,
    Average 3 or 4, Strong 4 or 5, so a meter never contradicts the word beside it. 0, 4, 7 and 10 out of 10 give 0, 2, 4
    and 5 dots; 5 out of 5 gives 5. A mark above the maximum fills every dot, a negative one none. Filled dots take the
    strength colour, empty ones a grey (the shape differs as well). Hovering over the dots shows the exact mark ("7 out of
    10"). A criterion with no usable mark or maximum shows "n/a" with no dots and no word. "- not stated" follows the row
    when the evidence says "Not stated in the proposal" or the validator filled the criterion (the pack's rule); the hover
    tells the two apart ("the proposal does not address this area" / "the model gave no mark for this area"). A note under
    the rows explains the dots.
180. **Show the calculation (closed).** Holds, in this order: the three scores as metrics (Overall /100, Score vs other bids
    (PPI), Industry score: the numbers that used to be at the top, so nothing is lost), "Criterion by criterion" with its note,
    "Compared with typical laptop deals" with its note, "Must-haves", and "Warnings". The three tables are the F11 tables
    unchanged. The Score vs best chart stays outside, between the area rows and this section, unchanged (it keeps the real
    criterion names, which the pack does not rename). The failed-evaluation message stays red at the top, outside; its text now
    says the warnings are "under Show the calculation" (it used to say "below"). Screen words: "Mandatory checks" is now
    "Must-haves", "Peer score (PPI)" is now "Score vs other bids (PPI)" (tooltip unchanged), "Industry comparison" is now
    "Compared with typical laptop deals"; the two empty-state sentences were reworded to match. The Setup tab's heading
    "Mandatory checks (gates)" is not in the pack and was left alone.
181. **Left as it was, on purpose.** The supplier picker and its keys. No schema change, no new package, no LLM call, no new
    chart (still 2 of 6) and nothing from spec Section 18: no ranking bars, stacked bars or sliders. Known limit: warnings that
    are not a failed evaluation are now only inside the closed section (the pack puts them there); the "not stated" note on a
    row and the red tiles are what a reader sees without opening it. Offered if you want it: a small "N warnings" hint next to
    the section's title.
182. **Checks.** 107 new tests (45 in `test_insights.py` for the meter, 62 net in `test_tab_scorecards.py`); the 9 AppTest
    checks of this tab (2 of them adjusted for the new words) cannot be run in the build container, so your own `pytest` run
    is their first real run. 63 deliberate breaks of the new code; 6 got through the first time (a wrong "+N more" count, an
    unescaped problem text in the chip, a price tile that ignored the budget in two places, a tile claiming "over budget"
    with no price, the missing note under the dots) and each got a test; all were caught in the final run. `pytest` should now
    report 1293 passed (1043 + 143 + 107).

### F12: Audit, run details and JSON download

183. **Where the file is built.** The spec's folder list has no export module, so `build_export` lives in `ui/tab_audit.py` next to the page, as the Overview and Scorecards tabs do with `build_overview` and `build_scorecards`. It returns plain Python data and makes no Streamlit calls, so F22 can call it to write `samples/sample_run.json`.
184. **Exactly the shape of Section 15.** Same keys, same order, nothing added, nothing left out. The key lists in the code (`TOP_LEVEL_KEYS`, `SUPPLIER_KEYS`, ...) are copied from the spec, and the tests type the spec's lists out again, so changing one side alone fails. A few things are saved with each supplier but are not in Section 15, so they are not in the file: the PDF file name, the reason each must-have passed or failed, "filled by the validator", the overall summary, "evaluation failed" and the page count. They stay in the database. A failed evaluation still shows in the file, because its warning is in `warnings`. Say so if you want any of them added.
185. **Nothing secret, nothing extra.** Keys are copied from the fixed lists above, never "everything saved". The two free-form parts (`extracted_facts`, `industry_breakdown`) are cleaned on the way out: a field whose name looks like a secret (`api_key`, `secret`, `password` ...) is dropped at any depth, a number that is not finite becomes `null`, and a value that is not plain JSON becomes text. The name check is the audit log's own (`audit.looks_like_secret_key`, made public so there is one list). The saved results never held proposal text, only the short evidence quotes; a test with a real batch checks that neither the key nor a sentence from the proposal reaches the file or the audit trail.
186. **Pros and cons in the file** are the ones the Overview and Scorecards show (chosen by `insights.select_pros_cons`), because the saved results still hold empty lists until F16. F16 will change where they come from on screen and in the file together.
187. **`recommendation`.** `recommended` and `runner_up` follow the Overview's rule (a supplier that is not eligible is skipped); `reason` is the sentence in the Overview banner ("Ranked 1 of 5, meets every must-have and fits the Rs 60 lakh budget."); `industry_warning` is the same warning, or `null`. With nobody eligible, `recommended` and `runner_up` are `null` and `reason` says who ranks first and why it is out. `tie_break_explanations` are the tie notes in rank order.
188. **Empty or null on purpose.** `feedback_rounds` and `negotiation_points` are empty lists until F19 and F20 save them. `totals` values are `null` when the provider did not report them (not 0, which would claim a free run). `status` is whatever was saved ("Evaluated" now; "Final" once a run is accepted). Decimals stay as saved (`"temperature": 0.0`). The file is UTF-8 with real characters (Rs, accents), indented by two spaces, and never contains NaN or infinity.
189. **Download JSON adds a "json_exported" row to the audit trail** (spec Section 14). Actor: the sidebar's reviewer name, or "reviewer" when it is empty. Details: file name, number of suppliers, size in bytes. Every click is a new row. The file does not contain the audit trail (it is not in Section 15), so the click does not change the next download. The click handler never raises: a problem writing the row shows a warning line and never a stack trace, and the download itself does not depend on it. After a click the page says "Exported as ... This export was added to the audit trail."
190. **Run details.** Ten tiles: Run ID (RFP_RUN_ID), Status, Started (in UTC), AI model (with provider and temperature), Marking scheme (template name and id), Reviewer, Purchase (budget in lakh and number of laptops), Suppliers, Time taken, AI usage (tokens and cost). The Run ID keeps the spec's name `RFP_RUN_ID` on screen because this is the tab auditors use. Anything an older run did not save says "Not recorded", "Not entered" or "not reported".
191. **Warnings.** Grouped by supplier. A warning that every supplier has (the ranking's own notes are saved with each supplier) is listed once under "All suppliers" when there are two or more suppliers. The file keeps each supplier's list exactly as saved.
192. **Which run.** The run just evaluated, the same one the Overview and Scorecards show. Opening an older run, and a failed run (it has no saved results), are part of the run history in F21.
193. **Left alone.** Overview, Scorecards and every other tab are unchanged. Known limit (DECISIONS 160): the Overview reader still stops on hand-edited junk in a saved result, so the Audit tab, which reads the run through it, does too. The file builder itself copes with junk and is tested on its own.
194. **Checks.** 103 new tests in `tests/test_tab_audit.py` (3 of them need Streamlit's AppTest, which cannot click a download button, so the click is tested through `note_export`) and 15 in `tests/test_database.py` for `looks_like_secret_key`. Deliberate-break check: 63 one-line changes to `ui/tab_audit.py` and `db/audit.py`; 2 got through the first time (the NaN guard in the text builder and the warning count) and each got a test; all were caught in the final run. `pytest` should now report 1411 passed (1293 + 103 + 15).

### F13: Human review, satisfaction and priorities

195. **What the user asked for (round 3), and the two rounds before it.** Round 1 had a 1 to 5 star rating for every mark; the user said a non-technical member cannot rate "Commercial Value" and the stars were redundant. Round 2 replaced them with "keep or change this mark" for each criterion; the user said again that the person reviewing has not read the proposals and only knows their own requirements, so the tab must not ask them to judge criteria at all. Round 3 is their design: one question ("On a scale of 1 to 5, how satisfied are you with this recommendation?"); satisfied (4 or 5) means they can accept; not satisfied means "What are you not satisfied with?" with plain options (too expensive, the laptops should be better, ...) and "None of the above" where they write what they need; the answer changes how much each area counts for and the suppliers are ranked again. It sits in the sixth tab (called Review in the spec, now named Re-evaluation, see 211), right after the recommendation.
196. **The benchmark is 4.** The user said "if it's below 3.5 something, then we will keep 4 and 4 above as the benchmark". The scale is whole numbers 1 to 5 (a radio row, nothing preselected), so 4 and 5 are satisfied and 1, 2 and 3 are not; 3.5 cannot be picked, it only fits between 3 and 4. `SATISFIED_AT = 4` in `tools/reweight.py`. A satisfied person cannot change the priorities and a not-satisfied person cannot accept before the rounds are used up (the screen and the engine both refuse).
197. **Where the rules live.** `tools/reweight.py` (new, plain Python: the scale, the benchmark, the round count, the plain options, the reweight rule, the sentences), `tools/review.py` (pruned: to-do list, re-ranking, before and after, `describe_recommendation`), `agents/feedback.py` (new: the AI reading of a typed need, spec 7.5), the second half of `agents/orchestrator.py` (`understand_need`, `change_priorities`, `accept_recommendation`, and `re_evaluate`, see 208), `ui/tab_review.py` (reads the saved run, words it, draws; a click only queues what was asked and the next drawing carries it out, like the New evaluation tab).
198. **Only the weights change, never the AI's marks.** A "re-evaluation" here is: one area counts for more in this run, everyone is ranked again, the new result is shown. The AI is not asked to read the PDFs again and no mark moves. The saved marks are the AI's reading of the proposals; what the buyer controls is how much each area matters to them. Ranking, the PPI and the industry score all depend on the weights, so all three are worked out again from the saved marks, benchmarks and the facts the AI extracted (the industry score needs the run's budget and quantity, DECISIONS 69). Must-haves do not depend on weights and are untouched.
199. **The reweight rule (spec 11.2).** The chosen area's weight becomes the smaller of (its weight + 10) and 60; every other weight is multiplied by the same factor so the total is exactly 100 (two decimals, halves up; a rounding leftover goes on the largest other weight, the lowest criterion id on a tie). An area already at 60 is refused ("Pick something else"); nothing is ever lowered on purpose. The new weights are saved with this run only (`rfp_runs.criteria_snapshot_json`); the marking scheme in Setup does not change. Second round from the demo: Technical Capability 26.25 to 36.25, the others scaled to 15.13, 25.93, 15.13 and 7.56 (total 100).
200. **Three rounds at most (the user's change to spec 11.2), one reason per round.** Spec 11.2 allows two rounds of "not satisfied"; the user asked for three ("make it three rounds"), so `MAX_ROUNDS = 3` in `tools/reweight.py`. This is a deliberate difference from the spec, like the tab name (211). The tab says "Round 2 of 3" and "Round 3 of 3". After the third round, a person who is still not satisfied cannot change the priorities again; they can accept the recommendation anyway ("Accept Apex Systems anyway"). That last part is my addition (without it they would be stuck); it is recorded as "accepted although not satisfied: all rounds were used". The spec says negotiation suggestions and "Choose a supplier yourself" come once the rounds are used (11.2); they are F20 and F19 and will appear in this tab, not here yet. The user can veto the accept-anyway.
201. **Accepting.** The run becomes "Final": no more changes, the question is replaced by "You accepted X on <date>. This evaluation is final." The JSON file's status becomes "Final" (DECISIONS 188). Accepting needs a recommended supplier; with nobody eligible the tab explains why there is nothing to react to.
202. **The plain options.** One per area of the run, in the marking scheme's order, worded for a buyer and not with the area's name: Technical Capability becomes "The laptops should be better (battery life, speed, build quality)", Implementation Plan "Delivery and setup should be quicker or more dependable", Commercial Value "The price is too high or goes over our budget", Security & Compliance "We need stronger security and data protection", Support & Experience "We need better warranty, repair and support". An area with any other name (a custom marking scheme) gets "<name> matters more to us". The list always ends with "None of the above, I will write it myself". Wording is mine; change it by editing `PLAIN_REASONS`.
203. **"None of the above": their own words.** Up to 300 characters. The AI (spec 7.5 prompt, verbatim in `prompts/feedback_system.md`) reads the words and proposes ONE action: `re_evaluate` (a score looks wrong), `reweight` (an area matters more), `set_aside` (a supplier is not wanted) or `unclear`. Only a `reweight` that points at an area of this run can be carried out here; anything else is explained in plain words ("That sounds like you want to leave a supplier out. That is not possible here yet.") and the person picks an option instead. The AI never changes anything: the person sees its one-sentence reading ("I understood: battery life matters more to you."), can pick a different area, and must click "Yes, rank the suppliers again" first, as the spec says the client confirms the reading. This needs a provider, a validated key and a model in the sidebar; the tab says so when they are missing, and the options keep working without a key.
204. **The typed words are untrusted.** They are sent inside `<client_text>` with the area list in `<criteria>`; text that tries to open or close those blocks is neutralised, the AI is told never to follow it, the temperature is 0, an unreadable reply is retried once and then treated as "unclear", and the supplier name is never involved. Text that contains the API key is refused before anything is sent (the check is `LLMSettings.key_is_in` in `llm/client.py`, so only that module ever looks at the key and the orchestrator guard test stays as it was). The reading's tokens and cost are added to the run's totals; the audit trail gets `need_interpreted` or `need_interpretation_failed`.
205. **Old pages and double clicks.** Every question is numbered (round 1, 2, ...). A click that belongs to a question that was already answered, in another window or by a second click, is refused: "This question was already answered, here or in another window. Reload the page to see the latest result." Everything one click changes (weights, ranking, decision, audit rows) is written in one go, so a stopped script leaves nothing half done.
206. **History is never overwritten.** `reviewer_feedback` rows are added, not changed: `reweight` (satisfaction, what they chose or wrote, the AI's reading, the weights before and after, the ranking before and after, who was recommended before and after, who did it) and `accept`. The audit trail gets `priorities_changed`, `recommendation_accepted` and `ranking_computed` (with the reason). `supplier_results` is the CURRENT result and is updated in place, so Overview, Scorecards and Audit show the new ranking at once; the original ranking and weights stay recoverable from the first round's "before", and the tab shows them under "The original ranking". The database still accepts the old `rate` and `override` actions and the `rating` column (they are in the spec's schema); nothing writes them any more.
207. **What the tab shows, in order (changed in round 4, see 212).** On the page: the run line and a short introduction; Where things stand (a panel: "We recommend X" and its score against the other bids); What changed last (after a round, in words: what the person said, what counts for more, who is recommended now); Your answer (the question, or the "final", "closed" or "nobody is eligible" notice); then More detail. Under More detail, each one closed until clicked: To do (n) (evaluations that failed, suppliers that are not eligible with the reason; "quote not found" appears once F15 checks evidence; the earlier note "quotes not checked yet" is gone; when there is nothing to do the page says so in one line and has no drop-down), Full ranking (n suppliers) (rank, supplier, score against the other bids, must-haves), What counts for how much (after a round, with "started at 30%" next to each), Last round in detail (round n) (what each area counted for before and now, and the new ranking with who moved up or down) and History (n rounds, accepted) (the original ranking and one line per round and acceptance). All text from proposals, the AI or the person goes through `html.escape`. Data that was saved oddly is shown as far as it can be read and never stops the tab.
208. **Removed, and kept.** Removed from earlier rounds: stars, the rating comment, "keep or change a mark" for each criterion, the supplier picker and per-mark history, the attempts table, and the per-mark code (`accept_score`, `override_score`, the change check) and its tests. `orchestrator.re_evaluate` (the AI reads one whole supplier again, twice at most, with a comment) is kept and tested but no screen calls it. F19's "A score looks wrong" will, so the spec 7.5 `re_evaluate` action is not lost. The REVIEWER FEEDBACK section in `prompts/evaluation_system.md` belongs to it. Question for the user: keep it for F19, or take it out until then?
209. **Not in F13.** "Your priorities" before a run and the "A score looks wrong" and "Choose a supplier yourself" parts of "Not happy?" (F19), leaving a supplier out (`set_aside`, F19), negotiation suggestions (F20), the change trail across runs (F21), evidence checking (F15), the Ask tab (F14). After the third round the spec offers negotiation and "Choose a supplier yourself" (11.2); they arrive with F20 and F19 and will sit in this tab, not in a new one.
210. **Checks.** New or rewritten tests: `test_feedback_reweight.py` (76), `test_feedback_agent.py` (65), `test_review_rules.py` (46), `test_review_flow.py` (124), `test_tab_review.py` (86, of which 4 AppTest checks only run on your machine) and 1 in `test_llm_client.py`; one old guard test was widened by one word (`copy`, used to copy a saved result before its industry score is worked out again). The page is tested through a recording stand-in for Streamlit that simulates a click the way Streamlit does it (the button callback queues, the next drawing carries out) and, since round 4, records what is drawn inside each drop-down, so the tests can tell what is on the page from what is behind a click (and that no button or input is ever hidden in a drop-down). The real numbers match the demo run: price option moves NexaWorks to first (86.53), then Technical Capability by typed words puts Apex Systems back (86.07). Deliberate-break check: 119 one-line changes across the rules, the feedback agent, the review steps, the tab (including the round-4 drop-downs and messages), the client and the prompt. In round 3, 7 got through the first time: 6 got a test each and are now caught, and the seventh is not a real change (the Overview sorts suppliers by rank itself, so the order they are read in cannot matter). In round 4 every new break was caught the first time, and the final run of all 119 on this code caught 118; the one left is that same Overview-order change, which changes nothing. `pytest` should now report 1809 passed (1411 + 398), a count worked out here (1713 passed, 28 skipped in the build workspace) and not yet seen on your machine. The tab has never run in real Streamlit; the first `streamlit run` is the real check, and it is also the only place the drop-downs can be seen working.
211. **The tab is named "Re-evaluation".** The user asked for the sixth tab to be renamed from "Review" to "Re-evaluation", a deliberate difference from spec 13.3, which names it "Review". Only the tab label changed (in `app.py` and the smoke test). The file `ui/tab_review.py`, the other module and test names, and the word "Review" in older notes above are unchanged.
212. **Drop-downs for the tables (round 4).** The user said the tab looked very messy and asked for drop-downs so that table information appears only when wanted, while still containing all the information. Nothing was removed. Every table and list moved into a drop-down (`st.expander`, closed when the page opens) under one heading, "More detail". The page itself keeps the words and the question: the recommendation with its score, what changed last in words, and Your answer. My choices, each easy to change: (a) the question stays on the page and never in a drop-down (a test fails if any button or input is drawn inside one); (b) the drop-down titles say what is inside and how much ("To do (2)", "Full ranking (5 suppliers)", "History (3 rounds, accepted)") so nobody has to open one to find out; (c) the to-do list is a drop-down too, so a failed evaluation or a not-eligible supplier no longer sits at the top of the page (the Overview still shows eligibility; say so if you want the to-do list open by default); (d) what changed last is shown in words on the page, and its two tables are in "Last round in detail"; (e) the recommendation panel now shows its score against the other bids (one line), which the ranking table used to show.
213. **A success is not said twice.** After a click the page showed a green box at the top and then the same sentence again in the "What changed last" panel (or in the "You accepted X on ..." line). Now only a refusal is shown as a message at the top; the new result is on the page itself. The message is still kept in the session so the tests can read it. This is a small change you did not ask for; it came from the same complaint about a messy page.

### F14: Ask the RFP

214. **What the tab does.** Pick one proposal or "All suppliers", type a question (up to 300 characters) and click **Ask**. The answer is a few sentences or a short list, each fact followed by "(Supplier, Page N)", or "Not stated in the proposal." (spec 12 and 7.2). The excerpts the answer was written from sit in a closed drop-down under it, "Where this came from (n excerpts)", each under its supplier and page, so every answer can be checked against the proposal. The last ten questions of the run are kept on the page, newest answer first, the older ones in a closed drop-down "Earlier questions (n)". The four demo questions of spec 12 sit in a closed drop-down "Example questions" and fill the box when clicked. Each question stands alone (no chat follow-ups).
215. **The search is BM25 written by hand, not the `rank_bm25` package.** `tools/retrieval.py` (about 120 lines) has the same Okapi formula and settings as the package the spec names (k1 1.5, b 0.75, epsilon 0.25). One package fewer to install and pin at M2 (DECISIONS 74 had left it for F14), and it can be tested here, where pip has no network. `tests/test_retrieval.py` checks the numbers against two worked-by-hand examples, and against `rank_bm25` itself when that package happens to be installed (skipped otherwise). One deliberate difference: a word whose weight comes out as zero or below (it is in half of the chunks or more, which only happens with very few chunks) gets a small positive weight (0.01), so that a one-page proposal can still be searched; the package would give it none and find nothing. Words are lower-cased, filler words dropped and lightly stemmed ("wiping", "wiped" and "wipe" meet; "battery" and "batteries" meet).
216. **How many excerpts (a deviation from spec 12, which says the top 5).** One supplier picked: its 5 best chunks, as in the spec. All suppliers: the 3 best chunks of EACH supplier (at most 30 with 10 proposals), best first. With seven proposals the five best chunks usually come from one or two suppliers, so "which suppliers ..." could not be answered for the rest, and a two-part question ("on-site warranty with next-day repair") often has its parts in two different chunks of one proposal. It was first 2 per supplier. Checking demo question 2 ("Who will hold the same price for laptops we order next year?") against the 7 real PDFs showed that the sentence that answers it ("same unit price ... for 12 months from the date of this proposal", Apex page 2 and NexaWorks page 2) is the 3rd best chunk of each of those suppliers, so with 2 it was never sent to the AI; with 3 it is. A test pins this on the real PDFs (skipped where they are missing). With the 7 sample PDFs that is 21 excerpts (about 5,500 to 6,100 words, roughly 7,500 to 8,000 tokens) per all-suppliers question; a question about one supplier sends at most 5 (about 1,400 words). A chunk that shares no word with the question is never sent. Known limit: ranking is by shared words, so a proposal that says "12 months" is only found for "next year" because it also shares "order", "same", "price" and "laptop"; a question made only of synonyms would find nothing (see 218).
217. **A question that names one supplier is narrowed to it, and the screen says so.** "What does Vertexa say about data wiping?" searches only Vertexa's proposal (found by the full name or by the first word when it has 4 or more letters, whole words only). Two suppliers named, or none: all are searched. A supplier picked in the box always wins over a name in the question. Inside one proposal the supplier's own name is left out of the words searched (as a rare word it would outweigh the topic); if nothing else is left ("Tell me about Vertexa") the name is used.
218. **No shared word, no AI call.** If no excerpt shares a word with the question, the answer is the spec's sentence "Not stated in the proposal." and the AI is not asked; the page says "No excerpt of ... shares a word with your question, so the AI was not asked." This is exactly what happens for demo question 4 on the 7 sample proposals (Vertexa's text never mentions data or wiping). Known limit of any keyword search: it does not know synonyms, so a proposal that says "drives are erased" is not found by "wiping". A semantic search would fix that but needs a second model and was not asked for.
219. **What the AI is told, and what Python checks afterwards.** `prompts/qa_system.md` is the spec 7.2 text word for word (a test compares them). Temperature 0.1 (`client.temperature_for("qa")`), no JSON mode. Each excerpt goes under "[Supplier, Page N]" inside `<excerpts>`, the question inside `<question>`; text that tries to open or close those blocks, or to fake another excerpt's label (square brackets in an excerpt become round), is neutralised. The AI's reply is cut at 1,500 characters. Python, not the AI, then reads every "(Supplier, Page N)" in the answer ("pages 3, 4", "p. 2" and several references in one bracket are understood) and checks that it points at an excerpt that was sent (same supplier, same page). A statement with no reference at all, or a reference that is not one of the excerpts, gets a yellow "check this against the excerpts" line under the answer; an answer that only says which suppliers do not state something needs no reference. An empty reply is reported in words instead of shown.
220. **The proposal text stays in the visitor's session (DECISIONS 120 still holds).** When a run finishes, New evaluation puts that run's chunks in the session (`ne_proposal_chunks`: the run id and the chunks, with the supplier names as saved). It does not reuse `ne_pdf_cache` because that is emptied when a file is removed from the upload box and holds the file names, not the names the person typed. Consequence you may not expect: after a page reload, or for any run but the latest of this session, the proposal text is gone, and the tab says so in plain words and how to get it back (upload the PDFs again on New evaluation and evaluate once more). Nothing from the proposals is saved: a test checks that no excerpt and no key is in the database. Offered, not done: saving the chunks in the database so Ask works after a reload (that would change 120 and put the full proposal text on the shared database, 82a).
221. **What is saved.** The audit log gets one row per question: `question_answered` (actor "AI"; the question, the supplier searched and whether the question narrowed it, the sources as supplier, page, chunk number and score, the answer, each reference with a verified flag, number of AI calls, model, provider, tokens, cost, and who asked) or `question_failed` (the same without an answer, with the kind of problem). Tokens and cost are added to the run's totals, like every other AI call. Asking never changes scores, ranking, weights or status: a test compares the saved evaluation before and after. A run the reviewer has accepted (Final) can still be asked about; a run that did not finish cannot. A question that contains the API key is refused before anything is sent or saved.
222. **The Ask button is only greyed out when no model is chosen.** It is not greyed out while the question box is empty, on purpose: Streamlit hands a text box's words over when the cursor leaves it, and a click on a button that is still greyed out at that moment is lost, so the first click after typing would do nothing. An empty question gets "Type a question first." instead. (The F13 tab has the same grey-out on "Understand what I wrote" with its text box; not changed, see the snapshot.)
223. **Scoring never uses the search (spec 12).** A test takes the search away and runs a full evaluation, which still works, and checks that the evaluator module has no link to it.
224. **Not in F14.** The evidence check on quotes (F15), pros and cons wording (F16), Ask about an earlier run or after a reload (see 220), follow-up questions that remember the last answer, exporting or saving answers, negotiation points (F20). The Overview, Scorecards, Re-evaluation and Audit tabs are unchanged.
225. **Checks.** New tests: `test_retrieval.py` (73, one of them the `rank_bm25` comparison, skipped when the package is missing), `test_qa_agent.py` (65), `test_ask_flow.py` (37), `test_tab_ask.py` (60, of which 3 AppTest checks only run on your machine). `pytest` should now report 2043 passed and 1 skipped (1809 after F13 + 235 new, minus the one comparison), a number worked out in the build workspace (1944 passed, 32 skipped there), not seen on your machine. Deliberate breaks: 130 small changes to the F14 code were tried one at a time, and the tests failed for 128 of them; the other 2 change nothing that anyone can see (an `or` in a different order where one side is always empty, and `tuple(())` for an empty text, which is `()` anyway).

### F15: Evidence checker

226. **What it does.** For every mark the AI gives, it quotes the proposal. After each supplier is evaluated, Python checks whether that quote is really in the proposal (`tools/evidence_checker.py`, spec 9.2). The answer is saved per mark as found, not found, or nothing to check. No AI is involved.
227. **The rule.** The trailing "(Page N)" is taken off. Both the quote and the proposal text are cleaned the same way (lower case, one space between words, the same quote marks and dashes everywhere, bullets and soft hyphens removed). The quote is FOUND if it is a piece of the proposal text, or if a close match scores 90 or more (`rapidfuzz.fuzz.partial_ratio`, the threshold of the spec). The page the quote names is looked at first, but a quote that is on another page still counts as found: the words are in the proposal, and a wrong page number is not an invented quote.
228. **Details the spec does not give.** A quote with "..." or "…" in it is cut into pieces, and it is found when every piece is. A piece of fewer than 3 words must match exactly (a loose match on two words proves nothing). "Not stated in the proposal" (also with extra words after it) and an empty quote mean nothing to check: the saved flag is empty, not "not found".
229. **A quote that is not found never changes a mark or the ranking** (spec 9.2). It is flagged and listed, nothing more. A test runs the same evaluation with honest and invented quotes and compares every score and rank.
230. **Where it shows.** Scorecards, in "Show the calculation": a green "✓ Found in the proposal" or an amber "✗ Not found in the proposal" above the quote; nothing is added when there was nothing to check. The Re-evaluation tab's to-do list already had a slot for this since F13: one item per missing quote, "<criterion>: the quoted evidence was not found in the proposal." The Overview's cons list (F10) already prefers criteria whose evidence was not found. The Audit tab's run-detail already reads the flag. New audit row per supplier with at least one missing quote: `evidence_not_found` (supplier, criterion numbers, how many quotes were checked). The step log says how many were missing for each supplier.
231. **`rapidfuzz` is in `requirements.txt` (not pinned yet, pinned at M3), but the checker also works without it.** If the package is missing, a slower built-in comparison with the same threshold is used, so the app never fails to start for this reason and the checker can be tested on a machine without the package (the build workspace has none). One test checks that the real package is used when it is installed; it is skipped otherwise. The two can give slightly different scores for borderline quotes.
232. **A re-evaluation by the AI (`orchestrator.re_evaluate`) does not run the check yet.** It is unused and is going to be removed (the user said F13's re-ranking replaces the old F19 "Not happy?" flow), so nothing was added to it.
233. **Test data.** The shared fake model in the tests used to quote text that was not in the fake proposal ("quote 1 (Page 1)"); it now quotes a sentence that is (`review_support.QUOTE`), so the many older tests keep their meaning. One older test that expected every flag to be empty now expects them found.
234. **Checks.** New tests: `test_evidence_checker.py` (33, one needing the `rapidfuzz` package) and `test_evidence_flow.py` (11). The tests ran 1987 passed, 33 skipped in the build workspace. On your machine `pytest` should report 2087 passed and 1 skipped (2043 + 44 new, with `rapidfuzz` installed; 2086 passed and 2 skipped without it). Worked out, not seen. 33 deliberate breaks: 32 caught, 1 changes nothing anyone can see (searching every page when the quote names page 1 is what the code does anyway).

### F16: Pros and cons wording

235. **What changed.** Python still chooses the good and bad points (spec 10.1, built in R1a) and the order and the "vs requirement / vs this batch" label of each. New: after ranking, one AI call per supplier words those points in plain English with the page in brackets, and writes one sentence on why the recommended supplier is recommended (`prompts/summarizer_system.md`, spec 7.3 word for word, `agents/summarizer.py`). The Overview cards and the Scorecards "What's good / Watch out" use the AI's words; the Overview banner has a new line "Why: ...".
236. **The AI only words.** The call gets that supplier's chosen points (the template wording, the label, the justification and the evidence quote) and nothing else: no supplier name, nothing about other suppliers. Temperature 0.4 (spec 6.4, "wording"), JSON mode. Python then checks the reply: the number of pros and cons must match exactly, or the whole reply is refused and the template wording is kept (the AI cannot add, drop or reorder a point). Each line must be non-empty, at most 25 words (the prompt asks for 20; a little slack) and 220 characters, and must not name another supplier (full name, or first word of 4 or more letters, whole words); a line that fails is replaced by its template text, the others are kept. The reason: at most 32 words (prompt: 25), same name check, and only when there are pros.
237. **Each point has a key** (`criterion:3`, `gate:bis`, `evidence:2`, `not_stated:4`, `all_gates`), set by the selection. The AI's words are saved by key, not by position. After a re-rank on the Re-evaluation tab the points are chosen again; a point that is still chosen keeps its AI wording, a point that is new after the re-rank shows the template text (no new AI call; it costs nothing and nothing stale is shown). The "Why" sentence was written from certain pros, so it is shown only while the recommended supplier's pros are still exactly those, in the same order; otherwise the line is hidden.
238. **A problem never stops the run.** If the call fails (provider error, bad key, timeout after the one retry) or the reply cannot be used, that supplier keeps the template wording, the step log says so, and the audit log gets `wording_failed` (kind, tokens). A failed evaluation (all marks 0) is not worded, and a supplier with no points to word is not asked about. Cost: 5 to 10 more small calls per run (about 300 to 500 tokens each).
239. **What is saved.** In each supplier's saved result: `wording` (the AI's lines by key, the reason, the pros it was written from) and `pros` / `cons` (the lines as shown, so the JSON file has them). Audit row `points_worded` per supplier (how many lines, how many kept plain, whether there is a reason, tokens, cost, model). Tokens and cost are added to the run's totals. Runs saved before F16 have no `wording` and show the template wording as before.
240. **The JSON file (spec 15).** `pros` / `cons` are the AI's lines; `recommendation.reason` is the AI's sentence when there is one, the banner sentence otherwise.
241. **Tests.** The shared fake model in the older tests now also answers the wording call: it returns the template words unchanged plus a short reason, reports no tokens and cost, and is counted apart (`summary_calls`), so every older test keeps its meaning. A few older tests changed on purpose: the saved pros/cons are no longer empty, the audit log and the step log have the new rows and line.
242. **Checks.** New tests: `test_summarizer.py` (59) and `test_wording_flow.py` (24). The tests ran 2070 passed, 33 skipped in the build workspace; on your machine `pytest` should report 2170 passed and 1 skipped (2087 + 83 new, with `rapidfuzz` installed), worked out, not seen. 57 deliberate breaks of the F16 code: all caught in the end (7 slipped through the first time; tests were added for each).

243. **Price against budget.** A new chart on the Overview, after "Must-haves" and before "Strength by criterion": one horizontal bar per supplier that states a total price, in rank order, in Rs lakh, with a dashed line at the run's own budget (never a constant, DECISIONS 69). It is the third of the spec's 6 charts.
244. **Colours.** Within the budget uses the teal theme colour, over the budget pink (#D81B60), the budget line dark slate (#1F2937), all from `ui/theme.py`. A total exactly equal to the budget counts as within. The amount is written inside the start of each bar so the dashed line never cuts a label, and the hover shows the full rupee amount.
245. **Nothing to draw.** A supplier with no stated total gets no bar and is named under the chart ("No total price stated, so no bar: ..."). With no budget for the run, or no supplier stating a total, the heading stays and one grey line says why.
246. **Checks.** The Overview's old test "never shows a chart" became "exactly one chart and no slider"; 8 new tests in `tests/test_tab_overview.py`. 14 of 14 deliberate breaks caught. The container's plotly is a stand-in, so the screenshot is a drawing of the same data and colours; on your machine `pytest` should report 2178 passed and 1 skipped (2170 + 8), worked out, not seen.

247. **Analysis tab.** Three charts, in the spec's order, each with a one-line note and a plain line when there is nothing to draw: "Marks by supplier and criterion" (numeric heatmap), "Price against quality" (scatter), "What if price mattered more or less?" (what-if lines and table). With these, the app has all 6 of the spec's charts. The tab only reads the saved run; it never marks, ranks or calls the AI.
248. **Heatmap.** Rows are suppliers best rank first, columns are the run's criteria; the number is the mark, the colour is the share of the marks available (light teal to deep teal, never red-green); white numbers on dark cells. A criterion a supplier has no mark for is an empty cell. A supplier whose evaluation failed has no row and is named under the chart.
249. **Price against quality.** x = grand total in lakh, y = overall score out of 100, dashed budget line. A supplier that does not meet every must-have is a cross instead of a circle. A supplier with no stated total has no point and is named; with no budget or no total at all the chart is replaced by one grey line.
250. **What-if (spec 10.3).** `insights.what_if` ranks the same saved marks again with the price criterion at 10, 15, 20, 25, 30, 35 and 40 percent and every other weight scaled by the same factor so the total is 100. The price criterion is the one named Commercial Value (any capitals). The leader at each weight is the top-ranked supplier that meets every must-have, as in the recommendation. With the spec fixture, Apex leads at 20% and NexaWorks at 30% (PPI 86.53 against 83.89). The table tags the row where the leader changes and a sentence above it says so. A dashed line marks the weight this run used (read from the weights saved with the run).
251. **Colours.** One fixed colour per supplier from the theme list, given in name order (A to Z), because a saved run has no upload order and rank order would change the colours when a re-ranking moves a supplier. More than 7 suppliers start the list again.
252. **Limits.** Suppliers with identical marks (NexaWorks and Horizon in the spec fixture) draw on top of each other in the what-if chart; the note says so and the table shows each. A supplier whose marks do not cover the same criteria as the first one's is left out of the what-if and named. A saved mark outside its range gives no what-if instead of an error. `tools/insights.py` now imports the pure ranking engine; its purity test allows exactly that.
253. **Checks.** New: `tests/test_what_if.py` (16) and `tests/test_tab_analysis.py` (32); `test_insights.py` purity test widened. 30 of 30 deliberate breaks caught. The container's plotly is a stand-in, so the screenshot redraws the same data and colours with another library. On your machine `pytest` should report 2226 passed and 1 skipped (2178 + 48), worked out, not seen.

254. **Your priorities (before a run).** A closed "Your priorities (optional)" box on New evaluation, under budget and quantity: one tick box per active criterion with a plain label, and a writing box. Each tick gives that criterion the spec 11.2 rule once (+10 points, at most 60, the others scaled so the total stays 100), one after the other in marking-scheme order, so the same ticks always give the same weights. It applies to this run only; Setup and the next run are untouched. This replaces the planned F19.
255. **What a tick changes.** Only the weights: the AI's marks do not depend on them (the evaluation prompt carries no weights), so the same marks are weighted differently. The run's saved weights, every supplier's saved criteria, the ranking and the industry score all use the buyer's weights. A later "not satisfied" round on the Review tab starts from them.
256. **Typed sentence.** "Understand what I wrote" asks the AI (the F13 prompt, spec 7.5, unchanged) which criterion the sentence points at, and shows "I understood: ... That would tick X. Is that right?". Only "Yes, tick it" ticks the box; "No" changes nothing. Anything the AI cannot place as "this criterion matters more" is explained in one line and the buyer ticks instead. The button is never greyed out while the box is empty, so the first click after typing is not lost (the F13 heads-up does not apply here).
257. **Cost and record.** The call that read a sentence is added to the run's tokens and cost when the run starts, only if its tick is still on; a sentence whose tick was removed is dropped and not charged. The run's audit log gets `priorities_set` (ticks, typed sentences with the AI's reading, every weight before and after, by the reviewer name) and `need_interpreted` with `before_run: true`.
258. **Edge cases.** Ticking every criterion changes nothing and says so. A criterion already at 60% is skipped and named. Ticks for criteria that are not in the run are ignored. Nothing ticked shows "Nothing ticked: every criterion keeps the weight set up in Setup."
259. **Removed.** `orchestrator.re_evaluate`, `_re_evaluated_result` and their tests are gone (the user approved F14 and decided no separate F19). Marks are the AI's reading and are not changed afterwards. Left in place because other code and tests still use them: the helper rules in `tools/review.py`, the `re_evaluate` action name in the database and the `reviewer_feedback` option of the evaluator.
260. **Break-check correction.** The F17 and F18 break checks first reported all caught because the runner could not start pytest. Re-run properly: F17 14 of 15 (a missing "best rank on top" check, now tested) and F18 28 of 31 (three gaps, now tested), all caught afterwards. Priorities: 24 breaks, 22 caught first time, the rest equivalent or fixed by a new test.
261. **Checks.** New: `test_priorities.py` (52) and `test_tab_priorities.py` (13), 2 AppTest checks in `test_tab_new_evaluation.py`, small additions to the F17 and F18 tests; the tests of the removed code are gone with `re_evaluate`. The container's total is 2161 passed, 33 skipped; on your machine `pytest` should report 2263 passed and 1 skipped (2226 + 35 + 2), worked out, not seen.

262. **At least two proposals.** The run needs 2 to 10 PDFs: one alone is refused with "Upload at least 2 supplier proposal PDFs. A ranking compares suppliers, so one alone is not evaluated." (user decision; the spec said 1 to 10). The ranking engine itself still copes with one supplier.

263. **The price mark is worked out in Python.** The Commercial Value mark used to come from the AI, which judged the price table but not the price level, so a cheaper offer was not rewarded. Now, when at least two suppliers state a grand total, the lowest total gets the full mark and every other supplier gets `max_score x lowest total / its total` (2 decimals, half up). Example: Rs 56.17 lakh gets 10, Rs 59.47 lakh gets 9.44. (user asked that price priority should reflect how much cheaper an offer is)
264. **What is left alone.** A supplier with no stated total (or a failed evaluation) keeps the AI's mark, and the run log and audit name it. With fewer than two totals nothing changes and the log says so. The criterion used is the one named "Commercial Value" (any capitals). No over-budget penalty: the Overview already flags over-budget offers, and the user did not ask for one.
265. **Where it shows.** The Scorecards tab shows the arithmetic as the justification of the Commercial Value mark ("Price mark worked out from the totals, not marked by the AI: ..."), with no quote. The ranking, the pros and cons, the saved results and the re-rank after "Your priorities" all use the worked-out mark.
266. **The AI's own marks are kept.** `evaluation_attempts` still holds what the AI gave, and the audit event `price_marks_set` records the lowest total, every total, the new marks and the AI's marks.
267. **Tests.** 29 new in `test_price_marks.py`. The shared test supplier facts no longer carry a grand total, so the older tests keep their AI marks (three tests that read the total back were adjusted). Container total: 2190 passed, 33 skipped; on your machine `pytest` should report 2294 passed and 1 skipped (2265 + 29), worked out, not seen. A deliberate-break check (15 changes) was caught every time.

268. **Final recommendation and negotiation (F20).** Opens only after all 3 rounds of changing priorities are used (the user's rule; spec 11.2 says after the rounds). One button for one supplier: the final pick, which Python chooses (the best-ranked supplier that meets every must-have, with the weights of the person's rounds; the AI never chooses). No supplier picker. If no supplier meets every must-have there is nothing to recommend or negotiate.
269. **What is shown.** A panel "Best fit for what you asked for: <supplier>" with a short note (plain text by Python from the areas the person raised and the supplier's score; the AI's wording when it passes the checks), then at most 5 negotiation points chosen by `tools/insights.negotiation_points` in the order of spec 10.4 (failed must-haves, budget overrun against the run's own budget, criterion gaps by weight x gap with the spec's levers, the AI's risks, evidence not found, startup levers while there is room). Criteria the AI did not mark are not raised. The page says "Done" once after the click, shows the result above the button, and puts the section above the question once all rounds are used.
270. **AI wording and confidentiality.** `agents/negotiator.py` and `prompts/negotiator_system.md` (spec 7.4, plus a "fit" note). The AI gets the chosen points, the areas the person raised, up to 3 of the supplier's own strengths and its own quotes; never another supplier's name, price, mark or benchmark, and never the price-score reasoning (it quotes the batch's lowest total). A line or note is replaced by the plain text if empty, too long, or naming another supplier or "another bid / competitors". Without a key or on an AI problem the plain text is shown.
271. **Saving.** Append-only table `negotiation_points` (run, supplier, `{"fit", "points"}` JSON, time); the newest set for the final supplier is shown; the audit log gets `negotiation_suggested`; AI tokens and cost join the run totals; the Audit JSON's `negotiation_points` holds the newest set (fit note and points) for each supplier. Scores and ranks are never touched. Not built: "choose a supplier yourself with a written reason" (user: the final pick plus negotiation is enough); the spec's link from the industry warning to negotiation (Streamlit cannot jump between tabs).
272. **Tests.** `test_negotiation.py` (79) plus a helper change in `test_tab_review.py`. Container 2269 passed, 33 skipped. A deliberate-break check caught every change that alters behaviour.

273. **Audit tab history and trail (F21).** The Audit tab shows three AI-quality tiles (runs where priorities were changed, quotes not found in the proposal, average cost per run; the spec's "override rate" does not exist in this app), a history table of every saved run with date, model and winner filters, "Open this run" (shows a past run on every tab, read-only), a change trail of the audit events with a "Score changes only" filter, and the PDF fingerprints (SHA-256). A past run opened from the history has no re-evaluation question and no negotiation button, and queued clicks are ignored. The JSON file now fills `feedback_rounds`.
274. **Not satisfied with a supplier is not "leave it out".** "I want the cheapest one" was read as set_aside. The feedback prompt now says a wish for a kind of supplier (cheaper, dearer, faster, better supported) is a reweight, and set_aside only when a supplier is rejected by name. A deliberate addition to the spec 7.5 prompt, so `test_feedback_agent.py` checks the file's wording instead of a copy.
275. **Leaning (F21b, an extension beyond spec 11.2).** The AI reads a typed wish as `leaning`: the end of the price range ("low" or "high"), whether one area is the only thing that matters, and a level from -2 to +2 per area (tone: a strong or repeated wish counts more). Python turns it into weights (`tools/leaning.py`): factors 0.1, 0.5, 1, 1.8, 3, scaled to total 100, no area above 60%; "only price" gives price 60% and the others share the other 40% as they were. "High" is accepted only when the buyer's own words say so ("most expensive", "costliest", ...); "too expensive" is a complaint, not a wish. Python writes the sentence the buyer confirms. Ticks and options still follow spec 11.2 (+10, cap 60).
276. **The price formula only on request.** Spec Section 18 lists formula-based price scoring under "do not build", so the default run uses the AI's own Commercial Value marks. The formula (`max x lowest / total`, or `max x total / highest` for "most expensive") is used only when the buyer states a price wish; the direction is saved in the run's criteria snapshot; the audit event `price_marks_set` records it. The AI's own marks stay in `evaluation_attempts` and come back when a run returns to the best fit.
277. **Two picks.** When priorities or a price wish were given, the New evaluation result, the Overview and the Re-evaluation tab show "Based on your priorities and the best fit: X" with both ranks when both ways give the same supplier, or "Best pick based on your priorities: X" and "Best pick based on the best fit: Y", each with both ranks, when they differ. The best fit = best-ranked supplier that meets every must-have with the original weights (Setup) and the AI's own marks. A red warning ("You may be overpaying...") shows when the buyer asked for the most expensive and the best fit costs less. Scorecards and charts follow the priorities.
278. **After all 3 rounds and still not satisfied** (pressing the final-recommendation button, or "accept anyway"), the run returns to the original weights and the AI's own marks (`orchestrator.restore_original`, idempotent, audit event `returned_to_best_fit`); the rounds stay in the history. Someone satisfied (4 or 5) keeps what they asked for.
279. **Reviewer name is required** before evaluating (sidebar label "Reviewer name (required)"). The Evaluate button can be clicked without a name or with fewer than 2 PDFs; it then shows a warning and runs nothing. The priorities box says it is optional and that an objective best-fit run needs none.
280. **Negotiation layout.** After the click: the "Best fit" panel, a divider, then the heading "Negotiation points for <supplier>" with a one-line explanation and the numbered points.
281. **Known differences from the build prompt (to state in the README).** 3 re-evaluation rounds (spec 2); "A score looks wrong" and "We don't want this supplier" are not offered; "Choose a supplier yourself" is replaced by the final pick plus negotiation; mixed-wish weights and the price wish are extensions.
