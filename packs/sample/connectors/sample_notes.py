"""Sample pack connector: add_note appends a note to data/outbox/sample-notes.md.

It is harmless, but it is marked acting, so it goes through the approval gate, the daily
cap and the activity log like any real outside action (G-APPR-1, G-LOG-1).
HUB_OUTBOX (a folder) moves the file, which the tests use.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from hub.connectors.base import Action

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def outbox() -> Path:
    return Path(os.environ.get("HUB_OUTBOX") or PROJECT_ROOT / "data" / "outbox") / "sample-notes.md"


def add_note(payload: dict[str, str]) -> str:
    path = outbox()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- {time.strftime('%Y-%m-%d %H:%M')}: {payload['text'].strip()}\n")
    return f"Added a note to {path.name}"


ACTIONS = [
    Action(
        connector="sample_notes", name="add_note", platform="sample",
        description="Add one short note to the sample notes file.",
        fields={"text": {"description": "The note (one or two sentences)", "max": 500}},
        required=("text",),
        run=add_note,
    ),
]
