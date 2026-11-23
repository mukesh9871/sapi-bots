"""Software team review tool (Milestone 12, P-DEV-4): the reviewer accepts or rejects the exact
commit at the tip of a branch. Nobody can accept a commit they made themselves.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from hub.connectors.base import Action, ActionError

_spec = importlib.util.spec_from_file_location("hub_pack_devteam__state_review", Path(__file__).with_name("_state.py"))
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)


def _decide(payload: dict[str, str], accepted: bool) -> str:
    folder = S.folder_of(payload)
    if not S.is_repo(folder):
        raise ActionError("there is no repository yet")
    name = S.branch_name(payload["name"])
    tip = S.head(folder, name)
    me = S.author_name(payload.get("agent", ""))
    if S.author(folder, name) == me:
        raise ActionError("you made this commit: someone else must review it (P-DEV-4)")
    notes = payload.get("notes", "").strip()
    if not accepted and not notes:
        raise ActionError("say what must change")
    S.record_review(folder, tip, accepted, me, notes)
    return f"{'Accepted' if accepted else 'Rejected'} {name} at {tip[:8]}." + ("" if accepted else f" To change: {notes}")


def accept(payload: dict[str, str]) -> str:
    return _decide(payload, True)


def reject(payload: dict[str, str]) -> str:
    return _decide(payload, False)


FIELDS = {"folder": {"hub": "folder", "description": "The project folder (filled in by the hub)", "max": 400},
          "agent": {"hub": "agent", "description": "Who asks (filled in by the hub)", "max": 80},
          "name": {"description": "The branch you reviewed", "max": 60},
          "notes": {"description": "What you checked, or what must change", "max": 2000}}
ACTIONS = [
    Action(connector="dev_review", name="accept", platform="git", acting=False, fields=FIELDS, required=("name",),
           run=accept, description="Accept the latest commit of a branch after reviewing it (not your own)."),
    Action(connector="dev_review", name="reject", platform="git", acting=False, fields=FIELDS, required=("name", "notes"),
           run=reject, description="Reject the latest commit of a branch, saying what must change."),
]
