"""Connectors from my claude.ai account, reused by the hub (G-CONN-1..4).

Claude Code reaches the connectors I connected on claude.ai (Settings, Connectors) through a proxy
("claudeai-proxy" servers). The hub does not build its own Gmail, Drive or calendar code: a role may
list a connector's tools, as it lists any other tool.

- Reading the list only starts the CLI and asks for the servers' status: no message, no usage. A
  copy is kept in data/connectors.json, so the window has it at once next time.
- Only claude.ai connectors (decision 2026-11-24): never my Claude Code plugins or local MCP
  servers, which would need my personal Claude Code settings in the hub.
- A connector is usable only after I switch it on (config "connectors", on the Connectors page).
- Each tool says whether it only reads (annotation readOnly). A read-only tool runs freely; every
  other tool goes through the approval gate (hub/gate/gate.py, external calls).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from hub.config import PROJECT_ROOT

CACHE = PROJECT_ROOT / "data" / "connectors.json"
PREFIX = "mcp__claude_ai_"  # Claude Code's tool names for claude.ai connectors start with this
log = logging.getLogger("hub.connectors_claude")
_lock = threading.Lock()
_state: dict[str, Any] = {"servers": None, "source": ""}  # source: live | saved | none


def slug(server: str) -> str:
    """Claude Code's form of a server name inside tool names: anything but letters, digits, _ and - becomes _.
    "claude.ai Gmail" -> "claude_ai_Gmail"."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", server)


def tool_id(server: str, tool: str) -> str:
    return f"mcp__{slug(server)}__{tool}"


def _clean(raw: Any) -> list[dict[str, Any]]:
    out = []
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, dict) or not isinstance(s.get("name"), str):
            continue
        cfg = s.get("config") if isinstance(s.get("config"), dict) else {}
        if s.get("scope") != "claudeai" and cfg.get("type") != "claudeai-proxy":
            continue  # only my claude.ai connectors
        tools = []
        for t in s.get("tools") or []:
            if isinstance(t, dict) and isinstance(t.get("name"), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", t["name"]):
                notes = t.get("annotations") if isinstance(t.get("annotations"), dict) else {}
                tools.append({"name": t["name"], "read_only": notes.get("readOnly") is True,
                              "destructive": notes.get("destructive") is True})
        out.append({"name": s["name"][:80], "label": s["name"].removeprefix("claude.ai ")[:80],
                    "status": str(s.get("status") or "")[:20], "tools": tools})
    return out


def _set(servers: list[dict[str, Any]], source: str) -> None:
    with _lock:
        _state.update(servers=servers, source=source)


def current() -> dict[str, Any]:
    """{"servers": [{"name", "label", "status", "tools": [{"name", "read_only", "destructive"}]}], "source"}."""
    with _lock:
        if _state["servers"] is not None:
            return dict(_state)
    try:
        data = json.loads(CACHE.read_text(encoding="utf-8"))
        servers = _clean_saved(data.get("servers"))
        _set(servers, "saved")
    except (OSError, ValueError, AttributeError):
        _set([], "none")
    with _lock:
        return dict(_state)


def _clean_saved(servers: Any) -> list[dict[str, Any]]:
    """A saved copy is our own format; still checked, since it is a file on disk."""
    out = []
    for s in servers if isinstance(servers, list) else []:
        if isinstance(s, dict) and isinstance(s.get("name"), str) and isinstance(s.get("tools"), list):
            tools = [t for t in s["tools"] if isinstance(t, dict) and isinstance(t.get("name"), str)
                     and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", t["name"])]
            out.append({"name": s["name"][:80], "label": str(s.get("label") or s["name"])[:80],
                        "status": str(s.get("status") or "")[:20],
                        "tools": [{"name": t["name"], "read_only": t.get("read_only") is True,
                                   "destructive": t.get("destructive") is True} for t in tools]})
    return out


def update_from(raw: Any, path: Path | None = None) -> None:
    servers = _clean(raw)
    _set(servers, "live")
    try:
        target = path or CACHE
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps({"servers": servers}, indent=1), encoding="utf-8")
        tmp.replace(target)
    except OSError:
        pass


async def refresh() -> bool:
    """Asks Claude Code which claude.ai connectors this login has (no message, no usage). Never raises."""
    import asyncio
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    from hub.chat.claude_session import CHAT_WORKDIR
    try:
        CHAT_WORKDIR.mkdir(parents=True, exist_ok=True)
        options = ClaudeAgentOptions(setting_sources=[], allowed_tools=[], tools=[], cwd=str(CHAT_WORKDIR), skills=[],
                                     env={"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
        async with ClaudeSDKClient(options) as client:
            status: Any = {}
            for _ in range(10):  # connectors connect in the background after start-up
                await asyncio.sleep(1)
                status = await client.get_mcp_status()
                servers = status.get("mcpServers", []) if isinstance(status, dict) else status
                if servers and all(s.get("status") != "pending" for s in servers):
                    break
        update_from(status.get("mcpServers", []) if isinstance(status, dict) else status)
        return True
    except Exception as exc:  # signed out, no network: the saved copy stays
        log.info("could not read the connectors: %s", exc)
        return False


def switched_on() -> list[str]:
    from hub import config
    names = config.load().get("connectors", [])
    return [n for n in names if isinstance(n, str)] if isinstance(names, list) else []


def tools(only_on: bool = True) -> dict[str, dict[str, Any]]:
    """{tool id: {"server", "label", "tool", "read_only", "destructive"}} for switched-on connectors."""
    on = set(switched_on())
    out = {}
    for s in current()["servers"]:
        if only_on and s["name"] not in on:
            continue
        for t in s["tools"]:
            out[tool_id(s["name"], t["name"])] = {"server": s["name"], "label": s["label"], "tool": t["name"],
                                                  "read_only": t["read_only"], "destructive": t["destructive"]}
    return out


def server_prefixes() -> list[str]:
    """"mcp__<server>" for every known claude.ai connector: what a session keeps out of its context."""
    return [f"mcp__{slug(s['name'])}" for s in current()["servers"]]
