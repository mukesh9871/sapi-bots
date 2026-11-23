"""Connector registry (G-TOOL-4): every module in this folder that lists ACTIONS is a connector.

New connectors are added by dropping a module here; the engine is not changed.
Milestone 8 (G-PACK-1): an installed, switched-on pack adds its own connectors from
packs/<pack>/connectors/*.py. They are loaded only while the pack is on, and a pack
connector can never replace a built-in one or another pack's.
"""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import sys
from pathlib import Path
from typing import Callable

from hub.connectors.base import Action

_builtin: dict[str, Action] | None = None
_pack_cache: dict[Path, tuple[float, list[Action], object]] = {}  # module file -> (mtime, its actions, module)
problems: dict[str, str] = {}  # pack id -> why one of its connectors was not loaded (shown on the Packs page)

# {pack id: pack folder} for installed and switched-on packs. Set by the server (hub/engine/packs.py).
pack_dirs: Callable[[], dict[str, Path]] = lambda: {}


def _module_actions(module: object, name: str) -> list[Action]:
    acts = list(getattr(module, "ACTIONS", []))
    for action in acts:
        if not isinstance(action, Action) or action.connector != name:
            raise ValueError(f"{getattr(action, 'id', action)}: connector name must match its module")
    return acts


def _builtins() -> dict[str, Action]:
    global _builtin
    if _builtin is None:
        found: dict[str, Action] = {}
        for mod in pkgutil.iter_modules(__path__):
            if mod.name == "base":
                continue
            module = importlib.import_module(f"{__name__}.{mod.name}")
            for action in _module_actions(module, mod.name):
                found[action.id] = action
        _builtin = found
    return _builtin


def _load_pack_file(pack: str, path: Path) -> list[Action]:
    """Loads one pack connector module (again only if the file changed)."""
    mtime = path.stat().st_mtime
    cached = _pack_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    mod_name = f"hub_pack_{pack.replace('-', '_')}_{path.stem}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    acts = _module_actions(module, path.stem)
    _pack_cache[path] = (mtime, acts, module)
    return acts


def pack_actions(pack: str, root: Path) -> list[Action]:
    """The actions of one pack (loads its connector files). Raises on a broken file."""
    folder = root / "connectors"
    files = sorted(f for f in folder.glob("*.py") if not f.stem.startswith("_")) if folder.is_dir() else []
    return [a for f in files for a in _load_pack_file(pack, f)]


def keys(action: Action) -> list[str]:
    """What must be unique across the hub: id, tool name, and (platform, action), which keys approval rules."""
    return [f"id:{action.id}", f"tool:{action.tool_name}", f"rule:{action.platform}/{action.name}"]


def builtin_keys() -> set[str]:
    return {k for a in _builtins().values() for k in keys(a)}


def actions() -> dict[str, Action]:
    """All connector actions by id ("demo.post_note"): built-in ones, then those of switched-on packs."""
    found = dict(_builtins())
    problems.clear()
    for pack, root in pack_dirs().items():  # in install order: on a clash the pack installed first keeps it
        folder = root / "connectors"
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.py")):
            if path.stem.startswith("_"):
                continue
            try:
                acts = _load_pack_file(pack, path)
            except Exception as exc:  # a broken pack file never breaks the hub
                problems[pack] = f"{path.name}: {type(exc).__name__}: {exc}"
                continue
            # A clash is the same id, the same tool name Claude sees, or the same (platform, action):
            # approval rules are keyed by the last, so pack code can never use another action's rule (review M8).
            ids = {a.id for a in found.values()}
            tools = {a.tool_name for a in found.values()}
            keys = {(a.platform, a.name) for a in found.values()}
            clash = [a.id for a in acts if a.id in ids or a.tool_name in tools or (a.platform, a.name) in keys]
            if clash:
                problems[pack] = f"{path.name}: {', '.join(clash)} clashes with an action that already exists"
                continue
            for action in acts:
                found[action.id] = action
    return found


def get(action_id: str) -> Action | None:
    return actions().get(action_id)


def pack_hooks(name: str) -> list[tuple[str, Callable]]:
    """Milestone 14: module-level functions a switched-on pack's connectors offer, by name ("TICK" for
    plain code the hub runs every few seconds, "BUSY" for "work is going on"). Only modules that load
    without a clash count (actions() decides that). Returns [(pack id, function)]."""
    found = []
    loaded = {a.run.__module__ for a in actions().values()}  # the modules actions() accepted
    for pack, root in pack_dirs().items():
        folder = root / "connectors"
        for path in sorted(folder.glob("*.py")) if folder.is_dir() else []:
            cached = _pack_cache.get(path)
            if path.stem.startswith("_") or not cached or getattr(cached[2], "__name__", "") not in loaded:
                continue
            fn = getattr(cached[2], name, None)
            if callable(fn):
                found.append((pack, fn))
    return found
