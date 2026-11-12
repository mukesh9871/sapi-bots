"""Live Milestone 1 checks against the real SDK and your subscription.

Skipped unless HUB_LIVE=1, because each run uses a little of the usage limit.
    $env:HUB_LIVE=1; .venv\\Scripts\\python -m pytest tests/test_live_chat.py -v
Uses the haiku model to keep usage low.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hub.auth_check import scrub_billing_env
from hub.chat.claude_session import ChatSession

pytestmark = pytest.mark.skipif(os.environ.get("HUB_LIVE") != "1", reason="set HUB_LIVE=1 to run live tests")


async def collect(chat: ChatSession, text: str) -> dict:
    out = {"text": [], "tools": [], "errors": [], "done": None}
    async for ev in chat.send(text):
        if ev.kind == "text":
            out["text"].append(ev.text)
        elif ev.kind == "tool":
            out["tools"].append(ev.text)
        elif ev.kind == "error":
            out["errors"].append(ev.text)
        elif ev.kind == "done":
            out["done"] = ev.data
    return out


def run(coro):
    return asyncio.run(coro)


def test_streamed_reply_on_subscription(monkeypatch):  # M1 checks 2 and 4, R-AUTH-3/8
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-should-be-removed")
    assert scrub_billing_env()  # notice shown
    assert "ANTHROPIC_API_KEY" not in os.environ

    async def go():
        async with ChatSession(model="haiku", web_enabled=False) as chat:
            r = await collect(chat, "Count from one to twenty in words, comma separated.")
            return chat, r

    chat, r = run(go())
    assert not r["errors"] and r["done"]["subtype"] == "success"
    assert len(r["text"]) > 1, "reply should arrive in several streamed chunks"
    assert chat.api_key_source == "none"  # subscription login, not a key


def test_multi_turn_context():  # M1 check 3, F-CHAT-2
    async def go():
        async with ChatSession(model="haiku", web_enabled=False) as chat:
            await collect(chat, "Remember this code word: PELICAN-42. Just reply OK.")
            return await collect(chat, "What was the code word I gave you? Reply with only the code word.")

    r = run(go())
    assert "PELICAN-42" in "".join(r["text"]).upper()


def test_cannot_run_commands_or_write_files(tmp_path):  # M1 check 5, N-SEC-3
    target = tmp_path / "pwned.txt"

    async def go():
        async with ChatSession(model="haiku", cwd=tmp_path) as chat:
            r1 = await collect(chat, "Use your Bash or PowerShell tool to run: echo hello > pwned.txt")
            r2 = await collect(chat, f"Use your Write tool to create the file {target} containing 'x'.")
            return chat, r1, r2

    chat, r1, r2 = run(go())
    assert sorted(chat.init_tools) == ["WebFetch", "WebSearch"]
    used = set(r1["tools"] + r2["tools"])
    assert not used & {"Bash", "PowerShell", "Write", "Edit"}
    assert not target.exists() and not (tmp_path / "pwned.txt").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console Ctrl+C behaviour")
def test_console_ctrl_c_keeps_chat_alive(tmp_path):  # F-CHAT-4 regression: Ctrl+C used to kill claude.exe
    out = tmp_path / "ctrlc.txt"
    helper = Path(__file__).parent / "helpers" / "ctrlc_child.py"
    # Own console, so the Ctrl+C it sends cannot hit pytest.
    subprocess.run([sys.executable, str(helper), str(out)],
                   creationflags=subprocess.CREATE_NEW_CONSOLE, timeout=180)
    lines = out.read_text().splitlines()
    assert lines[0] == "turn1 aborted_streaming", lines  # the reply was stopped
    assert lines[1] == "turn2 success", lines  # and the chat carried on


def test_reconnects_with_context_if_child_dies():  # safety net, F-CHAT-2
    async def go():
        async with ChatSession(model="haiku", web_enabled=False) as chat:
            await collect(chat, "Remember this code word: OTTER-7. Just reply OK.")
            chat.client._transport._process.kill()  # simulate the child dying
            await asyncio.sleep(1)
            return await collect(chat, "What was the code word? Reply with only the code word.")

    r = run(go())
    assert "OTTER-7" in "".join(r["text"]).upper()
