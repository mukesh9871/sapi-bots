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
import os
import re
import shutil
import sys
import uuid
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


# ---- SDK transcripts (F-HIST-4, D-2) -----------------------------------------------
# The CLI writes each session to ~/.claude/projects/<chat folder, non-alphanumerics as "-">/<id>.jsonl.
# We work out that folder ourselves instead of using the SDK's delete_session: given a folder,
# the SDK also searches its git worktrees, and the chat folder sits inside this git repo, so
# it could reach the repo's own Claude Code transcripts. Here a delete can only touch
# <uuid>.jsonl files in the chat transcript folder.

def transcript_dir() -> Path:
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    return base / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(CHAT_WORKDIR.resolve()))


def _transcript_path(session_id: str) -> Path | None:
    try:
        uuid.UUID(session_id)  # never build a path from anything but a UUID
    except (ValueError, TypeError, AttributeError):
        return None
    return transcript_dir() / f"{session_id}.jsonl"


def transcript_exists(session_id: str | None) -> bool:
    path = _transcript_path(session_id) if session_id else None
    return bool(path and path.is_file() and path.stat().st_size > 0)


def delete_transcript(session_id: str) -> bool:
    """D-2: remove a session's transcript and its subagent folder. True if a file was removed."""
    path = _transcript_path(session_id)
    if path is None:
        return False
    removed = False
    try:
        path.unlink()
        removed = True
    except FileNotFoundError:
        pass
    shutil.rmtree(path.with_suffix(""), ignore_errors=True)  # sibling <id>/ folder, often absent
    return removed


def all_transcript_ids() -> list[str]:
    """D-3: every session in the chat transcript folder (it is used only by this chat app)."""
    folder = transcript_dir()
    return [p.stem for p in folder.glob("*.jsonl") if _transcript_path(p.stem)] if folder.is_dir() else []


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

    def __init__(self, *, ctrl_c_shield: bool = True, **option_kwargs: Any):
        # ctrl_c_shield: keep claude.exe alive when Ctrl+C is pressed in our console. The terminal
        # chat needs it (there Ctrl+C means "stop this reply"). The server turns it off: there
        # Ctrl+C means "quit", and while the shield is up our own process ignores Ctrl+C too,
        # so a press during Claude's start-up would be lost (found in M3 testing).
        self.ctrl_c_shield = ctrl_c_shield
        self.option_kwargs = option_kwargs
        self.options = build_options(**option_kwargs)
        self.client = ClaudeSDKClient(self.options)
        # F-HIST-4: a reopened chat starts by resuming its stored session.
        self.session_id: str | None = option_kwargs.get("resume")
        self.pending_replay: str | None = None  # saved history to send when the transcript is gone
        self.api_key_source: str | None = None
        self.init_tools: list[str] = []
        self.last_rate_limit: dict[str, Any] | None = None
        self.pending_personality: str | None = None  # set by reconfigure() mid-chat
        self.stop_requested = False  # Stop pressed before the message reached Claude
        self.query_sent = False  # True only while a sent message is being answered

    async def __aenter__(self) -> "ChatSession":
        await self._connect()
        return self

    async def connect(self) -> None:
        await self._connect()

    async def _connect(self) -> None:
        # Windows sends console Ctrl+C to every process on the console, so claude.exe
        # would exit and the chat would die. Children inherit the "ignore Ctrl+C" flag,
        # so set it only while the child starts, then restore it for our own Stop handler.
        if sys.platform == "win32" and self.ctrl_c_shield:
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

    async def reconfigure(self, **option_kwargs: Any) -> bool:
        """F-PERS-3 / F-CHAT-6 / F-WEB-3: new personality, model or web setting from the next message.

        The system prompt and tool set are fixed when the CLI starts, so a change means a
        new CLI process that resumes this session. Returns True if anything changed.
        """
        merged = {**self.option_kwargs, **option_kwargs}
        if merged == self.option_kwargs:
            return False
        old_prompt = self.options.system_prompt
        self.option_kwargs = merged
        new_prompt = system_prompt(merged.get("personality", "straight"), merged.get("custom_prompt"))
        if self.session_id and new_prompt != old_prompt:
            # A resumed session keeps its original system prompt (tested: the CLI ignores the
            # new one), so the new personality travels with the next message instead.
            self.pending_personality = new_prompt
        try:
            await self.client.disconnect()
        except Exception:
            pass
        self.options = build_options(**{**merged, "resume": self.session_id})
        self.client = ClaudeSDKClient(self.options)
        await self._connect()
        return True

    async def close(self) -> None:
        await self.client.disconnect()

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def stop(self) -> None:
        """F-CHAT-4: cancel the reply in progress. The caller keeps draining `send`."""
        if not self.query_sent:
            # CLI still starting (first turn or reconfigure): an interrupt now would reach an
            # idle or half-closed CLI and be lost, so remember it and stop right after query.
            self.stop_requested = True
            return
        try:
            await self.client.interrupt()
        except Exception:
            pass  # the CLI is gone; the reply stream ends on its own

    async def send(self, text: str) -> AsyncIterator[Event]:
        if self.pending_personality:
            text = (
                "[Message from the chat app, not from any web page: the user switched your personality. "
                "From now on, follow these instructions instead of the earlier ones:\n"
                f"{self.pending_personality}]\n\n{text}"
            )
        if self.pending_replay:
            # F-HIST-4: the old transcript is gone, so the saved messages travel with this one.
            text = f"{self.pending_replay}\n\n{text}"
        try:
            await self.client.query(text)
        except CLIConnectionError:
            await self._reconnect()
            await self.client.query(text)  # same message, nothing lost
        self.query_sent = True
        # The replay is in the new transcript now that Claude has the message, even if the reply
        # fails, is stopped or the CLI dies before a result, so never send it twice (review M3 round 4).
        self.pending_replay = None
        if self.stop_requested:
            self.stop_requested = False
            await self.client.interrupt()
        try:
            async for event in self._receive():
                yield event
        finally:
            self.query_sent = False

    async def _receive(self) -> AsyncIterator[Event]:
        """Turn SDK messages for the current reply into Events."""
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
                if not msg.is_error:
                    self.pending_personality = None  # delivered; kept for a retry otherwise
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
