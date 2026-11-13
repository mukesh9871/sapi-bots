"""One readable settings file: <app folder>/config.json (N-MAINT-1).

Milestone 2 stores the chat choices and the custom prompt (F-PERS-2).
Milestone 3 adds the settings page: bot name and avatar (F-PERS-4), theme (N-USE-2)
and edited built-in prompts.
Never put secrets here (N-SEC-6): platform logins live in Windows Credential Manager.
"""

from __future__ import annotations

import copy
import json
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
}

# Only these keys can be changed from the window (keeps junk out of the file).
EDITABLE = {"personality", "custom_prompt", "model", "web_enabled", "bot_name", "avatar", "theme", "prompts"}

# Expected type per key. Wrong types (from a hand edit) fall back to the default.
TYPES: dict[str, type] = {
    "personality": str, "custom_prompt": str, "model": str, "web_enabled": bool, "port": int,
    "bot_name": str, "avatar": str, "theme": str, "prompts": dict,
}


def load(path: Path | None = None) -> dict[str, Any]:
    path = path or CONFIG_PATH  # looked up at call time so tests can redirect it
    data = copy.deepcopy(DEFAULTS)  # deep: "prompts" is a dict and must never be shared
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for key, value in raw.items():
                    expected = TYPES.get(key)
                    # bool is a subclass of int, so never accept true/false as a port.
                    if expected and isinstance(value, expected) and not (expected is int and isinstance(value, bool)):
                        data[key] = value
                # Keep only text prompts for built-in personalities.
                data["prompts"] = {k: v for k, v in data["prompts"].items()
                                   if k in PERSONALITIES and isinstance(v, str)}
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass  # a broken file falls back to defaults rather than crashing the app
    return data


def save(updates: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    path = path or CONFIG_PATH
    data = load(path)
    data.update({k: v for k, v in updates.items() if k in EDITABLE})
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # atomic swap, so a crash never leaves half a file (N-REL-2)
    return data
