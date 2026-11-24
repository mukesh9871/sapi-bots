"""The models my plan offers, read from Claude Code itself (R-MODEL-1..3).

Claude Code tells a connecting client which models this login may use (the same list as the
model picker in VS Code), with each one's name, description and effort levels. Reading it only
starts the CLI and asks for its start-up info: no message is sent, so it uses no plan usage.

- At start-up the hub reads the list in the background and keeps a copy in data/models.json,
  so the window has it at once next time, also when Claude cannot be reached.
- With no list at all (first start while signed out), the four short names that always work.
- A model value is "" (the plan default, or for a role: the Settings model) or a name from the
  list. The value goes to the CLI as one argument, so it must also look like a model name
  (never an option).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from hub.config import PROJECT_ROOT

CACHE = PROJECT_ROOT / "data" / "models.json"
# "opus", "claude-haiku-4-5-20251001", "claude-fable-5-2[1m]" (1M context): never an option like "-x"
NAME_RE = re.compile(r"[a-z][a-z0-9.\-]{0,79}(\[[a-z0-9]{1,8}\])?")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
BUILTIN = [  # used only until Claude Code has given its list once
    {"value": "haiku", "name": "Haiku", "description": "Fastest for quick answers", "efforts": list(EFFORTS)},
    {"value": "sonnet", "name": "Sonnet", "description": "Most efficient for simpler tasks", "efforts": list(EFFORTS)},
    {"value": "opus", "name": "Opus", "description": "For complex work and everyday tasks", "efforts": list(EFFORTS)},
]
log = logging.getLogger("hub.models")
_lock = threading.Lock()
_state: dict[str, Any] = {"models": None, "default": None, "source": ""}  # source: live | saved | builtin


def _clean(raw: Any) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Claude Code's list, kept to what the window needs. The "default" entry describes the plan
    default (shown as "Plan default (Opus 5.5)"); every other entry is a choice."""
    out, default = [], None
    for m in raw if isinstance(raw, list) else []:
        if not isinstance(m, dict) or not isinstance(m.get("value"), str):
            continue
        efforts = [e for e in m.get("supportedEffortLevels") or [] if e in EFFORTS] if m.get("supportsEffort") else []
        name = str(m.get("displayName") or m["value"])[:60]
        lead = str(m.get("description") or "").split("·")[0].strip()
        if not any(ch.isdigit() for ch in name) and lead.startswith(name + " ") and len(lead) <= 40:
            name = lead  # "Fable" with "Fable 5.2 · For your toughest..." shows as "Fable 5.2"
        item = {"value": m["value"], "name": name,
                "description": str(m.get("description") or "")[:120], "efforts": efforts,
                "resolved": str(m.get("resolvedModel") or "")[:80]}
        if m["value"] == "default":
            default = item
        elif NAME_RE.fullmatch(m["value"]) and all(x["value"] != m["value"] for x in out):
            out.append(item)
    return out, default


def _set(models: list[dict[str, Any]], default: dict[str, Any] | None, source: str) -> None:
    with _lock:
        _state.update(models=models, default=default, source=source)


def _load_saved(path: Path | None = None) -> bool:
    try:
        data = json.loads((path or CACHE).read_text(encoding="utf-8"))
        models, default = data.get("models"), data.get("default")
        if isinstance(models, list) and models:
            _set(models, default if isinstance(default, dict) else None, "saved")
            return True
    except (OSError, ValueError, AttributeError):
        pass
    return False


def current() -> dict[str, Any]:
    """{"models": [...], "default": {...} or None, "source": "live" | "saved" | "builtin"}."""
    with _lock:
        if _state["models"] is not None:
            return dict(_state)
    if not _load_saved():
        _set(list(BUILTIN), None, "builtin")
    with _lock:
        return dict(_state)


def update_from(raw: Any, path: Path | None = None) -> bool:
    """Takes Claude Code's list (from the CLI's start-up info) and keeps a copy. False if empty."""
    models, default = _clean(raw)
    if not models:
        return False
    _set(models, default, "live")
    try:
        target = path or CACHE
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps({"models": models, "default": default}, indent=1), encoding="utf-8")
        tmp.replace(target)
    except OSError:
        pass  # the list still works for this run
    return True


async def refresh() -> bool:
    """Asks Claude Code for the list (no message sent, no usage). Never raises."""
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    from hub.chat.claude_session import CHAT_WORKDIR
    try:
        CHAT_WORKDIR.mkdir(parents=True, exist_ok=True)
        options = ClaudeAgentOptions(setting_sources=[], allowed_tools=[], tools=[], cwd=str(CHAT_WORKDIR),
                                     strict_mcp_config=True, skills=[],
                                     env={"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
        async with ClaudeSDKClient(options) as client:
            info = await client.get_server_info()
        return update_from((info or {}).get("models"))
    except Exception as exc:  # signed out, no network, old CLI: the saved or built-in list stays
        log.info("could not read the model list: %s", exc)
        return False


def known(value: str) -> dict[str, Any] | None:
    for m in current()["models"]:
        if m["value"] == value:
            return m
    return None


def problem(value: Any, effort: Any = "") -> str:
    """Why a model (and effort) chosen in the window cannot be used ("" if it can)."""
    if not isinstance(value, str) or not isinstance(effort, str):
        return "bad model"
    if value and not NAME_RE.fullmatch(value):
        return "unknown model"
    # The short names always work in Claude Code (review M15: a pack role says "sonnet")
    if value and known(value) is None and value not in ("haiku", "sonnet", "opus"):
        return "this model is not offered by your plan (the list comes from Claude Code)"
    if effort and effort not in EFFORTS:
        return "unknown effort level"
    return ""


def effort_for(model: str | None, effort: str | None) -> str | None:
    """The effort to send, or None: only for a model that supports that level (Haiku 4.5 has none).
    An unknown model (the list not read yet) gets it as asked; Claude Code ignores what it cannot use."""
    if not effort or effort not in EFFORTS:
        return None
    entry = known(model) if model else current().get("default")
    if entry is None:
        return effort
    return effort if effort in entry.get("efforts", []) else None
