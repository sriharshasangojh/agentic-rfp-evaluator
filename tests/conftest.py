"""Shared test setup: every test gets its own empty database file, never the real one."""
from __future__ import annotations

from pathlib import Path

import pytest

from db import repository


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "test_rfp_evaluation.db"
    monkeypatch.setenv(repository.DB_PATH_ENV, str(path))
    return path
