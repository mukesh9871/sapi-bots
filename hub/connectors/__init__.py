"""Connector registry (G-TOOL-4): every module in this folder that lists ACTIONS is a connector.

New connectors are added by dropping a module here; the engine is not changed.
"""

from __future__ import annotations

import importlib
import pkgutil

from hub.connectors.base import Action

_registry: dict[str, Action] | None = None


def actions() -> dict[str, Action]:
    """All connector actions by id ("demo.post_note"). Loaded once."""
    global _registry
    if _registry is None:
        found: dict[str, Action] = {}
        for mod in pkgutil.iter_modules(__path__):
            if mod.name == "base":
                continue
            module = importlib.import_module(f"{__name__}.{mod.name}")
            for action in getattr(module, "ACTIONS", []):
                if action.connector != mod.name:
                    raise ValueError(f"{action.id}: connector name must match its module")
                found[action.id] = action
        _registry = found
    return _registry


def get(action_id: str) -> Action | None:
    return actions().get(action_id)
