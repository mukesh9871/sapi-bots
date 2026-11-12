"""The only module that talks to Claude (risk "Policy change": keep the Claude call in one place).

A ChatSession wraps one ClaudeSDKClient (one live Claude Code process) and turns
SDK messages into simple events the terminal (and later the web window) can show.

Security (N-SEC-3, N-SEC-5):
  1. `tools` limits the *available* set to WebSearch + WebFetch (or nothing if web is off).
  2. `disallowed_tools` also removes the dangerous built-ins by name, as a second layer.
  3. `can_use_tool` refuses any call that is not on the chat allow-list, as a third layer.
  4. Never permission bypass mode. Never load user/project settings, CLAUDE.md, skills or MCP servers.
"""

from __future__ import annotations

import ctypes
import dataclasses
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    CLIConnectionError,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    RateLimitEvent,
    ResultMessage,
    ServerToolUseBlock,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ToolPermissionContext,
    ToolUseBlock,
)

from hub.chat.personalities import system_prompt

# The web tools are pre-approved on purpose, so the SDK's "callback shadowed" warning is expected.
warnings.filterwarnings("ignore", message="can_use_tool will not be invoked")

WEB_TOOLS = ["WebSearch", "WebFetch"]

# Built-ins plain chat must never have. Listed by bare name so the CLI removes them (N-SEC-3).
DENIED_TOOLS = [
    "Bash", "BashOutput", "KillShell", "PowerShell", "Monitor",
    "Write", "Edit", "MultiEdit", "NotebookEdit",
    "Read", "Glob", "Grep", "LS",  # no local file access in v1 (F-LATER-4 is later)
    "Task", "Agent", "TodoWrite", "Skill", "SlashCommand", "ExitPlanMode",
]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHAT_WORKDIR = PROJECT_ROOT / "data" / "chat_workdir"  # empty folder, N-SEC-5


def build_options(
    *,
    personality: str = "straight",
    custom_prompt: str | None = None,
    model: str | None = None,
    web_enabled: bool = True,
    resume: str | None = None,
    cwd: Path = CHAT_WORKDIR,
) -> ClaudeAgentOptions:
    allowed = list(WEB_TOOLS) if web_enabled else []
    cwd.mkdir(parents=True, exist_ok=True)

    async def gate(tool: str, _input: dict[str, Any], _ctx: ToolPermissionContext):
        # Layer 3: anything that reaches the prompt stage and is not allowed is refused.
        if tool in allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(message=f"The tool '{tool}' is not available in chat.")

    return ClaudeAgentOptions(
        tools=allowed,                       # layer 1: available set
        allowed_tools=allowed,               # pre-approve the web tools only
        disallowed_tools=DENIED_TOOLS + ([] if web_enabled else WEB_TOOLS),  # layer 2
        can_use_tool=gate,                   # layer 3
        permission_mode="default",           # never "bypassPermissions"
        system_prompt=system_prompt(personality, custom_prompt),
        setting_sources=[],                  # N-SEC-5: no user/project settings or CLAUDE.md
        skills=[],
        mcp_servers={},
        strict_mcp_config=True,              # ignore any MCP servers in the user's config
        cwd=str(cwd),
        model=model,
        resume=resume,
        include_partial_messages=True,       # F-CHAT-1: word-by-word streaming
        # N-SEC-2: no telemetry, error reporting or auto-update traffic from the child CLI.
        env={"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
    )


# ---- events yielded to the UI -------------------------------------------------

@dataclass
class Event:
    kind: str  # "init" | "text" | "tool" | "rate_limit" | "error" | "done"
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class ChatSession:
    """One conversation = one SDK client. Context is kept across `send` calls (F-CHAT-2)."""

    def __init__(self, **option_kwargs: Any):
        self.options = build_options(**option_kwargs)
        self.client = ClaudeSDKClient(self.options)
        self.session_id: str | None = None
        self.api_key_source: str | None = None
        self.init_tools: list[str] = []
        self.last_rate_limit: dict[str, Any] | None = None

    async def __aenter__(self) -> "ChatSession":
        await self._connect()
        return self

    async def _connect(self) -> None:
        # Windows sends console Ctrl+C to every process on the console, so claude.exe
        # would exit and the chat would die. Children inherit the "ignore Ctrl+C" flag,
        # so set it only while the child starts, then restore it for our own Stop handler.
        if sys.platform == "win32":
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleCtrlHandler(None, True)
            try:
                await self.client.connect()
            finally:
                kernel32.SetConsoleCtrlHandler(None, False)
        else:
            await self.client.connect()

    async def _reconnect(self) -> None:
        """Safety net: the child died anyway. Start a new one that resumes this session (keeps context)."""
        try:
            await self.client.disconnect()
        except Exception:
            pass  # already dead
        self.options = dataclasses.replace(self.options, resume=self.session_id)
        self.client = ClaudeSDKClient(self.options)
        await self._connect()

    async def __aexit__(self, *exc: Any) -> None:
        await self.client.disconnect()

    async def stop(self) -> None:
        """F-CHAT-4: cancel the reply in progress. The caller keeps draining `send`."""
        await self.client.interrupt()

    async def send(self, text: str) -> AsyncIterator[Event]:
        try:
            await self.client.query(text)
        except CLIConnectionError:
            await self._reconnect()
            await self.client.query(text)  # same message, nothing lost
        streamed = False  # True once text deltas arrived, so we do not print the full text twice
        async for msg in self.client.receive_response():
            if isinstance(msg, SystemMessage) and msg.subtype == "init":
                self.session_id = msg.data.get("session_id")
                self.api_key_source = msg.data.get("apiKeySource")
                self.init_tools = list(msg.data.get("tools", []))
                yield Event("init", data=msg.data)

            elif isinstance(msg, StreamEvent):
                ev = msg.event
                if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                    streamed = True
                    yield Event("text", ev["delta"]["text"])

            elif isinstance(msg, AssistantMessage):
                if msg.error:
                    yield Event("error", msg.error)
                for block in msg.content:
                    if isinstance(block, (ToolUseBlock, ServerToolUseBlock)):
                        yield Event("tool", block.name, data={"input": block.input})
                    elif isinstance(block, TextBlock) and not streamed:
                        yield Event("text", block.text)
                streamed = False  # next assistant message streams afresh

            elif isinstance(msg, RateLimitEvent):
                info = msg.rate_limit_info
                self.last_rate_limit = {
                    "status": info.status,
                    "resets_at": info.resets_at,
                    "utilization": info.utilization,
                    "type": info.rate_limit_type,
                }
                yield Event("rate_limit", data=self.last_rate_limit)

            elif isinstance(msg, ResultMessage):
                self.session_id = msg.session_id
                yield Event(
                    "done",
                    data={
                        "subtype": msg.subtype,
                        "is_error": msg.is_error,
                        "usage": msg.usage or {},
                        "model_usage": msg.model_usage or {},
                        "total_cost_usd": msg.total_cost_usd,  # API-equivalent cost; NOT billed on a subscription
                        "duration_ms": msg.duration_ms,
                        "permission_denials": msg.permission_denials or [],
                        "terminal_reason": msg.terminal_reason,
                    },
                )
