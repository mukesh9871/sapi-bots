"""One readable settings file: <app folder>/config.json (N-MAINT-1).

Milestone 2 stores the chat choices and the custom prompt (F-PERS-2).
Milestone 6 adds the automation pause switch (G-BUDGET-2).
Milestone 3 adds the settings page: bot name and avatar (F-PERS-4), theme (N-USE-2)
and edited built-in prompts.
Never put secrets here (N-SEC-6): platform logins live in Windows Credential Manager.
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

from hub.chat.personalities import PERSONALITIES

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config.json"

DEFAULTS: dict[str, Any] = {
    "personality": "straight",  # fun | straight | explainer | custom
    "custom_prompt": "",
    "model": "",  # "" = plan default
    "web_enabled": True,
    "bot_name": "Bot",  # F-PERS-4
    "avatar": "🤖",  # F-PERS-4: an emoji or up to 4 letters
    "theme": "system",  # system | light | dark (N-USE-2)
    "prompts": {},  # edited built-in prompts, e.g. {"fun": "..."}; missing = built-in text
    "port": 8765,
    "automation_paused": False,  # G-BUDGET-2: pauses role tasks and acting tools, never plain chat
}

# Only these keys can be changed from the window (keeps junk out of the file).
EDITABLE = {"personality", "custom_prompt", "model", "web_enabled", "bot_name", "avatar", "theme", "prompts",
            "automation_paused"}

# Expected type per key. Wrong types (from a hand edit) fall back to the default.
TYPES: dict[str, type] = {
    "personality": str, "custom_prompt": str, "model": str, "web_enabled": bool, "port": int,
    "bot_name": str, "avatar": str, "theme": str, "prompts": dict, "automation_paused": bool,
}


def _retry(fn: Any) -> Any:
    """Windows refuses to read a file while it is being swapped in by save(), and to swap it while
    it is being read. Such a clash lasts milliseconds, so try again briefly (review M6)."""
    for attempt in range(20):
        try:
            return fn()
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.01)


def load(path: Path | None = None, *, strict: bool = False) -> dict[str, Any]:
    """strict: raise if the file exists but cannot be read, instead of using defaults
    (save() uses it, so a busy file never leads to the defaults being written over it)."""
    path = path or CONFIG_PATH  # looked up at call time so tests can redirect it
    data = copy.deepcopy(DEFAULTS)  # deep: "prompts" is a dict and must never be shared
    if path.exists():
        try:
            raw = json.loads(_retry(lambda: path.read_text(encoding="utf-8")))
            if isinstance(raw, dict):
                for key, value in raw.items():
                    expected = TYPES.get(key)
                    # bool is a subclass of int, so never accept true/false as a port.
                    if expected and isinstance(value, expected) and not (expected is int and isinstance(value, bool)):
                        data[key] = value
                # Keep only text prompts for built-in personalities.
                data["prompts"] = {k: v for k, v in data["prompts"].items()
                                   if k in PERSONALITIES and isinstance(v, str)}
        except OSError:
            if strict:
                raise
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass  # a broken file falls back to defaults rather than crashing the app
    return data


def start_paused(path: Path | None = None) -> tuple[bool, str]:
    """G-BUDGET-2 at start-up: (paused, notice). Fails closed: a file that exists but cannot be
    read, is not valid JSON, or has a pause value that is not true/false starts paused (review M6)."""
    path = path or CONFIG_PATH
    if not path.exists():
        return False, ""
    try:
        raw = json.loads(_retry(lambda: path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return True, "config.json could not be read, so automation starts paused. Fix the file, then resume."
    value = raw.get("automation_paused", False) if isinstance(raw, dict) else None
    if not isinstance(value, bool):
        return True, "automation_paused in config.json is not true or false, so automation starts paused."
    return value, ""


def save(updates: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    path = path or CONFIG_PATH
    data = load(path, strict=True)
    data.update({k: v for k, v in updates.items() if k in EDITABLE})
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    _retry(lambda: tmp.replace(path))  # atomic swap, so a crash never leaves half a file (N-REL-2)
    return data
