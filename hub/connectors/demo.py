"""Demo connector (Milestone 6 test acting tool).

post_note appends a note to data/outbox/notes.md. It is harmless, but it is marked acting,
so it goes through the approval gate, the daily cap and the activity log exactly like a
real post would.
"""

from __future__ import annotations

import time
from pathlib import Path

from hub.connectors.base import Action

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTBOX = PROJECT_ROOT / "data" / "outbox" / "notes.md"  # tests point this elsewhere


def post_note(payload: dict[str, str]) -> str:
    OUTBOX.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    title = payload.get("title", "").strip() or "Note"
    with OUTBOX.open("a", encoding="utf-8") as f:
        f.write(f"## {title} ({stamp})\n\n{payload['text'].strip()}\n\n")
    return f"Posted note \"{title}\" to {OUTBOX.name}"


ACTIONS = [
    Action(
        connector="demo", name="post_note", platform="demo",
        description="Post a short note to the demo outbox (a test of a public post).",
        fields={"title": {"description": "Short title (optional)", "max": 100},
                "text": {"description": "The note text", "max": 2000}},
        required=("text",),
        run=post_note,
    ),
]
