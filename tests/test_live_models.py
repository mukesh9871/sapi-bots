"""Live check of the model list and effort (R-MODEL-1, R-MODEL-2). Skipped unless HUB_LIVE=1.

Reads the real list from Claude Code (no message, no usage), then sends one short message on a
model from that list with an effort level, through the hub's own chat route."""

import asyncio
import json
import os

import pytest

from helpers.hubhttp import free_port, own, request, start_server
from hub import config
from hub.chat import models
from hub.chat.claude_session import delete_transcript
from hub.chat.history import HistoryStore

pytestmark = pytest.mark.skipif(os.environ.get("HUB_LIVE") != "1", reason="set HUB_LIVE=1 to run live tests")


def test_real_list_and_a_reply_with_effort(tmp_path, monkeypatch, model_list):
    assert asyncio.run(models.refresh()) is True
    state = models.current()
    values = [m["value"] for m in state["models"]]
    assert state["source"] == "live" and "haiku" in values and state["default"]
    haiku = models.known("haiku")
    assert "low" in haiku["efforts"]
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    config.save({"model": "haiku", "effort": "low", "web_enabled": False, "personality": "straight"})
    port = free_port()
    history = HistoryStore(tmp_path / "chats.db")
    srv, t = start_server(port, history=history)
    try:
        resp, data = request(port, "POST", "/api/chat", origin=own(port), timeout=300,
                             body={"text": "Reply with the single word: ready", "chat_id": None})
        events = [json.loads(line) for line in data.decode().splitlines() if line]
        assert resp.status == 200 and not [e for e in events if e["kind"] == "error"], events
        reply = "".join(e["text"] for e in events if e["kind"] == "text").lower()
        assert "ready" in reply
    finally:
        srv.should_exit = True
        t.join(60)
        for chat in history.list_chats():
            if chat.get("session_id"):
                delete_transcript(chat["session_id"])
