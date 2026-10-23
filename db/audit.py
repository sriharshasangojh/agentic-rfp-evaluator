"""Append-only audit log writer (spec section 14). Rows can be added, never changed or removed."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

# API keys must never reach the audit log (golden rule 6): refuse any details that look like one.
_FORBIDDEN_KEY_FRAGMENTS = ("api_key", "apikey", "secret", "password")


def looks_like_secret_key(name: object) -> bool:
    """True for a field name that suggests a secret ("api_key", "OPENAI_API_KEY", "password"). The JSON export uses it too."""
    return any(fragment in str(name).lower() for fragment in _FORBIDDEN_KEY_FRAGMENTS)


def _reject_secrets(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, inner in value.items():
            if looks_like_secret_key(key):
                raise ValueError(f"Refusing to write '{key}' to the audit log: secrets are never stored.")
            _reject_secrets(inner)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            _reject_secrets(inner)


def log_event(
    conn: sqlite3.Connection,
    rfp_run_id: str,
    actor: str,
    action: str,
    details: Mapping[str, Any] | None = None,
) -> int:
    """Append one audit row and return its id. `actor` is "AI", "system" or the reviewer's name."""
    payload = dict(details or {})
    _reject_secrets(payload)
    cursor = conn.execute(
        "INSERT INTO audit_log (rfp_run_id, timestamp, actor, action, details_json) VALUES (?, ?, ?, ?, ?)",
        (
            rfp_run_id,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            actor,
            action,
            json.dumps(payload, sort_keys=True, default=str),
        ),
    )
    return int(cursor.lastrowid)
