"""Live Milestone 4 checks against real Claude. Skipped unless HUB_LIVE=1.

- M4 check 1: web toggle works, web answers show (and save) source links (F-WEB-1..3)
- M4 check 2: an outage gives a plain error quickly, with retries shown (N-REL-1)
"""

import asyncio
import dataclasses
import json
import os
import socket
import time

import pytest
from claude_agent_sdk import ClaudeSDKClient

from helpers.hubhttp import free_port, own, request, start_server
from hub import config
from hub.chat.claude_session import ChatSession, delete_transcript
from hub.chat.history import HistoryStore

pytestmark = pytest.mark.skipif(os.environ.get("HUB_LIVE") != "1", reason="set HUB_LIVE=1 to run live tests")

WEB_QUESTION = "Search the web for the current stable Python version and answer in one sentence."


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    store = HistoryStore(tmp_path / "chats.db")
    port = free_port()
    srv, t = start_server(port, history=store)
    yield port, store
    srv.should_exit = True
    t.join(15)
    for chat in store.list_chats():  # leave no test transcripts behind
        for sid in store.delete_chat(chat["id"]) or []:
            delete_transcript(sid)


def ask(port, text):
    resp, data = request(port, "POST", "/api/chat", origin=own(port), body={"text": text}, timeout=240)
    assert resp.status == 200, data
    return [json.loads(line) for line in data.decode().splitlines() if line]


def test_web_answer_shows_and_saves_sources(hub):  # M4 check 1, F-WEB-1, F-WEB-2
    port, store = hub
    config.save({"model": "haiku", "web_enabled": True, "personality": "straight"})
    events = ask(port, WEB_QUESTION)
    assert not [e for e in events if e["kind"] == "error"], events
    assert "WebSearch" in [e["text"] for e in events if e["kind"] == "tool"]
    sources = [e["sources"] for e in events if e["kind"] == "sources"][-1]
    assert sources and all(s["url"].startswith(("http://", "https://")) for s in sources)
    chat_id = next(e["id"] for e in events if e["kind"] == "chat")
    saved = store.messages(chat_id)[-1]
    assert saved["role"] == "bot" and saved["sources"] == sources  # still there when reopened


def test_web_off_means_no_web_and_no_sources(hub):  # M4 check 1, F-WEB-3
    port, store = hub
    config.save({"model": "haiku", "web_enabled": False, "personality": "straight"})
    events = ask(port, WEB_QUESTION)
    assert not [e for e in events if e["kind"] in ("tool", "sources")], events
    chat_id = next(e["id"] for e in events if e["kind"] == "chat")
    assert store.messages(chat_id)[-1]["sources"] == []


def test_outage_gives_plain_error_fast():  # M4 check 2, N-REL-1
    """Claude unreachable: an API address on a local port this test holds without listening
    (nothing can receive the request, so no login data leaves the PC). Measured before the fix: 3 silent minutes, then the CLI's error
    text saved as the reply."""
    # Hold the port for the whole test: bound but never listening, so a connect is refused and no
    # other process can take the port and receive the login (review M4).
    hold = socket.socket()
    hold.bind(("127.0.0.1", 0))
    port = hold.getsockname()[1]

    async def run():
        s = ChatSession(model="haiku", web_enabled=False)
        s.options = dataclasses.replace(s.options, env={**s.options.env, "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}"})
        s.client = ClaudeSDKClient(s.options)
        t = time.time()
        try:
            async with s:
                evs = [ev async for ev in s.send("Say hi.")]
        finally:
            if s.session_id:
                delete_transcript(s.session_id)  # leave no test transcript behind
        return evs, time.time() - t

    try:
        evs, took = asyncio.run(run())
    finally:
        hold.close()
    kinds = [e.kind for e in evs]
    assert "retry" in kinds and "text" not in kinds, kinds
    assert next(e for e in evs if e.kind == "error").text == "server_error"
    assert took < 45, took  # about 15 s with 5 retries; was 3 minutes
