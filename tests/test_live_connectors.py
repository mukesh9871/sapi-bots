"""Live check of claude.ai connectors in a role (G-CONN-1, G-CONN-2). Skipped unless HUB_LIVE=1.

Reads my real claude.ai connectors, switches one on, gives a role one read-only tool of it, and asks
the role to call it. The call must reach the connector (so the hub builds Claude Code's tool names
right) and a tool the role does not list must stay unavailable."""

import asyncio
import os

import pytest

from hub import config
from hub.chat import connectors_claude as cc
from hub.chat.claude_session import build_options
from hub.engine.roles import Role

pytestmark = pytest.mark.skipif(os.environ.get("HUB_LIVE") != "1", reason="set HUB_LIVE=1 to run live tests")


def test_a_role_calls_a_read_only_connector_tool(tmp_path, monkeypatch):
    from claude_agent_sdk import AssistantMessage, ClaudeSDKClient, ResultMessage, ToolUseBlock
    monkeypatch.setattr(cc, "CACHE", tmp_path / "connectors.json")
    monkeypatch.setitem(cc._state, "servers", None)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    assert asyncio.run(cc.refresh()) is True
    servers = [s for s in cc.current()["servers"] if s["status"] == "connected" and any(t["read_only"] for t in s["tools"])]
    if not servers:
        pytest.skip("no connected claude.ai connector with a read-only tool on this account")
    server = servers[0]
    tool = next(t["name"] for t in server["tools"] if t["read_only"])
    config.save({**config.load(), "connectors": [server["name"]], "model": "haiku"})
    tid = cc.tool_id(server["name"], tool)
    role = Role("probe", "Probe", "haiku", (tid,), f"When asked, call the tool {tid} once with no or minimal "
                f"arguments and report in one line what came back.", max_turns=4)
    opts = build_options(role=role, folder=tmp_path / "work")

    async def ask():
        from hub.chat.claude_session import ChatSession
        used, text = [], ""
        async with ClaudeSDKClient(opts) as client:
            probe = ChatSession.__new__(ChatSession)  # the hub's own wait, on this client
            probe.client = client
            await ChatSession._wait_for_connectors(probe)
            await client.query(f"Call {tid} now.")
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    used += [b.name for b in msg.content if isinstance(b, ToolUseBlock)]
                if isinstance(msg, ResultMessage):
                    text = msg.result or ""
        return used, text

    used, text = asyncio.run(ask())
    assert tid in used, (used, text)
    assert all(u == tid for u in used if u.startswith("mcp__claude_ai_"))  # nothing else from the connectors
