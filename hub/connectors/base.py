"""What a connector action looks like (G-TOOL-1, G-TOOL-2, G-TOOL-4).

A connector is one module in hub/connectors/ that lists ACTIONS. The engine finds it by
itself (see hub/connectors/__init__.py), so adding a connector never changes the engine.

An acting action changes something outside the app (post, submit, send, delete). It never
runs directly: the approval gate (hub/gate/gate.py) decides, runs and logs it.
Connectors use logins on the agent's behalf (N-SEC-6); an agent never sees them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class ActionError(ValueError):
    """Bad input for an action. The message is shown to the agent or to the user."""


@dataclass(frozen=True)
class Action:
    connector: str  # module name, e.g. "demo"
    name: str  # e.g. "post_note"
    platform: str  # where it acts, e.g. "demo" (rules are set per platform and action)
    description: str  # what the agent is told
    fields: dict[str, dict[str, Any]]  # input field -> {"type": "string", "description": ..., "max": ...}
    run: Callable[[dict[str, Any]], str]  # does it; returns a short result for the log
    acting: bool = True  # False = read-only (no approval needed)
    required: tuple[str, ...] = field(default=())
    # Optional: the exact text that will be published, when the connector builds it from the fields
    # (Milestone 10, a routine announcement). Shown in the approval so it is exactly what happens (G-APPR-2).
    show: Callable[[dict[str, Any]], str] | None = None
    # Optional: a rule the content must meet (for example "never post about these topics"). The gate runs
    # it before deciding, so content that can never be published is refused, not held for approval.
    check: Callable[[dict[str, Any]], None] | None = None

    @property
    def id(self) -> str:
        """How a role lists it, e.g. "demo.post_note"."""
        return f"{self.connector}.{self.name}"

    @property
    def tool_name(self) -> str:
        """The name Claude sees (an in-process MCP tool on the "hub" server)."""
        return f"mcp__hub__{self.connector}_{self.name}"

    def schema(self) -> dict[str, Any]:
        """What the agent is told. No "required" or types here on purpose: the SDK would refuse a
        bad call before the gate sees it, and the gate must check and log every call (review M6)."""
        props = {k: {"description": v.get("description", "") + (" (required)" if k in self.required else "")
                     + " Text."} for k, v in self.fields.items()}
        return {"type": "object", "properties": props}

    def clean(self, payload: Any) -> dict[str, str]:
        """Checks input from the agent or from an Edit in the inbox. Raises ActionError."""
        if not isinstance(payload, dict):
            raise ActionError("input must be an object")
        unknown = set(payload) - set(self.fields)
        if unknown:
            raise ActionError(f"unknown fields: {', '.join(sorted(unknown))}")
        out = {}
        for key, spec in self.fields.items():
            value = payload.get(key, "")
            if not isinstance(value, str):
                raise ActionError(f"{key} must be text")
            if key in self.required and not value.strip():
                raise ActionError(f"{key} is required")
            if len(value) > spec.get("max", 10_000):
                raise ActionError(f"{key} is too long")
            out[key] = value.strip()  # "Video " and "Video" are the same request (the repeat check, review M10)
        return out

    def preview(self, payload: dict[str, str]) -> str:
        """Exactly what will be done (G-APPR-2), as plain text."""
        lines = [f"{self.platform}: {self.name.replace('_', ' ')}"]
        if self.show is not None:
            try:
                lines.append(f"Will publish exactly:\n{self.show(payload)}")
            except Exception as exc:  # a field the connector would refuse: say so in the preview
                lines.append(f"(This cannot be published as it is: {exc})")
        for key in self.fields:
            if payload.get(key):
                lines.append(f"{key}:\n{payload[key]}")
        return "\n\n".join(lines)
