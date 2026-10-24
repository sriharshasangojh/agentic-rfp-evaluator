"""Documentation checks (feature F22): the README covers spec section 19 and the sample run matches the export format (section 15)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ui import tab_audit

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
SAMPLE = json.loads((ROOT / "samples" / "sample_run.json").read_text(encoding="utf-8"))


def test_readme_has_every_section_19_topic():
    for topic in ("Run locally", "The 8 tabs", "Agents and tools", "Golden rules", "Formulas", "Tie-break order", "Edge-case rules",
                  "Assumptions", "Screenshots", "Tests", "Deployment", "Future scope", "Extensions and known differences"):
        assert topic in README, topic
    for name in ("Absolute score", "Benchmark", "Gap", "Relative %", "PPI", "Industry score", "Strength label"):
        assert name in README, name
    for risk in ("SQLite", "typed name", "planning assumptions", "3-year total cost", "multi-reviewer", "side-by-side run comparison"):
        assert risk in README, risk


def test_readme_names_each_of_the_eight_tabs_in_order():
    labels = ["Setup", "New evaluation", "Overview", "Scorecards", "Analysis", "Re-evaluation", "Ask the RFP", "Audit"]
    positions = [README.index(f"| {label} |") for label in labels]
    assert positions == sorted(positions) and len(labels) == 8


def test_readme_mentions_only_files_that_exist():
    for path in re.findall(r"`((?:agents|tools|llm|db|ui|samples)/[A-Za-z_./]+\.(?:py|sql|json))`", README):
        assert (ROOT / path).exists(), path


def test_sample_run_has_the_export_keys_and_no_secret():
    assert tuple(SAMPLE) == tab_audit.TOP_LEVEL_KEYS
    for supplier in SAMPLE["suppliers"]:
        assert tuple(supplier) == tab_audit.SUPPLIER_KEYS
    text = json.dumps(SAMPLE)
    assert not re.search(r"sk-[A-Za-z0-9]{10,}|api_key", text, re.I)
    assert "<proposal>" not in text
