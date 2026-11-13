"""Live Milestone 3 checks against real Claude. Skipped unless HUB_LIVE=1.

- M3 check 2: close and reopen the app, continue an old chat (resume, F-HIST-4)
- F-HIST-4: continue a chat whose SDK transcript is gone (replay)
- M3 check 4: deleting a chat removes its transcript from disk (D-2)
- F-HIST-1: a stopped reply is saved too
- Switching chats mid-reply: other chats stay usable, the reply is still saved
"""

import http.client
import json
import os
import time

import pytest

from helpers.hubhttp import TOKEN, free_port, own, request, start_server
from hub import config
from hub.chat.claude_session import delete_transcript, transcript_dir, transcript_exists
from hub.chat.history import HistoryStore

pytestmark = pytest.mark.skipif(os.environ.get("HUB_LIVE") != "1", reason="set HUB_LIVE=1 to run live tests")


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Starts hub servers on one temporary history database. Call app() again to 'reopen the app'."""
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    config.save({"model": "haiku", "web_enabled": False, "personality": "straight"})
    db = tmp_path / "chats.db"
    running = []

    def start():
        for srv, t in running:  # close the previous window and server first
            srv.should_exit = True
            t.join(15)
        running.clear()
        port = free_port()
        running.append(start_server(port, history=HistoryStore(db)))
        return port

    yield start, db
    for srv, t in running:
        srv.should_exit = True
        t.join(15)
    for chat in HistoryStore(db).list_chats():  # leave no test transcripts behind
        for sid in HistoryStore(db).delete_chat(chat["id"]) or []:
            delete_transcript(sid)


def chat(port, text, chat_id=None):
    resp, data = request(port, "POST", "/api/chat", origin=own(port),
                         body={"text": text, "chat_id": chat_id}, timeout=180)
    assert resp.status == 200, data
    events = [json.loads(line) for line in data.decode().splitlines() if line]
    assert not [e for e in events if e["kind"] == "error"], events
    return events


def reply_of(events):
    return "".join(e["text"] for e in events if e["kind"] == "text")


def chat_id_of(events):
    return next(e["id"] for e in events if e["kind"] == "chat")


def test_close_reopen_and_continue_resumes(app):  # M3 check 2, F-HIST-4, N-PERF-3
    start, db = app
    port = start()
    events = chat(port, "Remember the code word KESTREL-4. Reply with only OK.")
    cid = chat_id_of(events)
    sid = HistoryStore(db).get_chat(cid)["session_id"]
    assert transcript_exists(sid)

    # The personality changes while the app is closed; it must apply to the resumed chat.
    request(port, "POST", "/api/settings", origin=own(port),
            body={"personality": "custom", "custom_prompt": "Always answer in UPPERCASE letters only."})
    port = start()  # close and reopen the app
    _, data = request(port, "GET", "/api/chats")
    assert [c["id"] for c in json.loads(data)["chats"]] == [cid]  # chat still listed

    events = chat(port, "What was the code word? One short sentence.", chat_id=cid)
    assert "chat" not in [e["kind"] for e in events]  # same chat, not a new one
    assert "note" not in [e["kind"] for e in events]  # resumed, not replayed
    reply = reply_of(events)
    assert "KESTREL-4" in reply and reply == reply.upper(), reply
    texts = [m["text"] for m in HistoryStore(db).messages(cid)]
    assert len(texts) == 4 and texts[0].startswith("Remember")


def test_continue_when_transcript_is_gone_replays(app):  # F-HIST-4, D-4 (new PC)
    start, db = app
    port = start()
    cid = chat_id_of(chat(port, "Remember the code word OSPREY-7. Reply with only OK."))
    port = start()  # close the app, then lose the transcript (like restoring on a new PC)
    store = HistoryStore(db)
    sid = store.get_chat(cid)["session_id"]
    assert delete_transcript(sid) and not transcript_exists(sid)

    events = chat(port, "What was the code word? Reply with only the code word.", chat_id=cid)
    notes = [e["text"] for e in events if e["kind"] == "note"]
    assert notes and "Restored" in notes[0]
    assert "OSPREY-7" in reply_of(events)
    new_sid = HistoryStore(db).get_chat(cid)["session_id"]
    assert new_sid != sid and transcript_exists(new_sid)  # resumes from the new session next time


def test_delete_chat_removes_transcript_from_disk(app):  # M3 check 4, D-2
    start, db = app
    port = start()
    cid = chat_id_of(chat(port, "Say hi in one word."))
    chat(port, "And again, one word.", chat_id=cid)
    sid = HistoryStore(db).get_chat(cid)["session_id"]
    path = transcript_dir() / f"{sid}.jsonl"
    assert path.is_file()
    resp, data = request(port, "DELETE", f"/api/chats/{cid}", origin=own(port))
    assert resp.status == 200 and json.loads(data)["transcripts_removed"] >= 1
    time.sleep(1)  # nothing may write it again after the delete
    assert not path.exists()
    assert HistoryStore(db).get_chat(cid) is None


def test_stopped_reply_is_saved(app):  # F-HIST-1, F-CHAT-4
    start, db = app
    port = start()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    conn.request("POST", "/api/chat", body=json.dumps({"text": "Write a 600-word story about a lighthouse."}),
                 headers={"Host": f"127.0.0.1:{port}", "Origin": own(port),
                          "Cookie": f"hub_token={TOKEN}", "Content-Type": "application/json"})
    resp = conn.getresponse()
    events = []
    for line in resp:  # read the stream; press Stop as soon as the first text arrives
        ev = json.loads(line)
        events.append(ev)
        if ev["kind"] == "text" and sum(e["kind"] == "text" for e in events) == 1:
            request(port, "POST", "/api/stop", origin=own(port))
    conn.close()
    done = [e for e in events if e["kind"] == "done"]
    assert done and str(done[0]["terminal_reason"]).startswith("aborted"), events[-1]
    msgs = HistoryStore(db).messages(chat_id_of(events))
    assert [m["role"] for m in msgs] == ["user", "bot"]
    assert msgs[1]["status"] == "stopped" and msgs[1]["text"] == reply_of(events)


def test_other_chat_usable_while_reply_streams(app):  # F-HIST-2/F-HIST-4 while busy, N-PERF-3
    """The window may switch chats mid-reply: reading, renaming and deleting another chat
    must work, deleting the replying chat must not, and the reply is still saved to its chat."""
    start, db = app
    port = start()
    other = HistoryStore(db).create_chat("an older chat")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=180)
    conn.request("POST", "/api/chat", body=json.dumps({"text": "Count from 1 to 40, one number per line."}),
                 headers={"Host": f"127.0.0.1:{port}", "Origin": own(port),
                          "Cookie": f"hub_token={TOKEN}", "Content-Type": "application/json"})
    resp = conn.getresponse()
    events, checked = [], False
    for line in resp:  # keep reading the stream, like the window does in the background
        events.append(json.loads(line))
        if not checked and events[-1]["kind"] == "text":
            checked = True
            busy_id = chat_id_of(events)
            r, d = request(port, "GET", f"/api/chats/{other['id']}")
            assert r.status == 200 and json.loads(d)["chat"]["title"] == "an older chat"
            r, _ = request(port, "PATCH", f"/api/chats/{other['id']}", origin=own(port), body={"title": "renamed"})
            assert r.status == 200
            r, _ = request(port, "DELETE", f"/api/chats/{busy_id}", origin=own(port))
            assert r.status == 409  # the replying chat cannot be deleted
            r, _ = request(port, "DELETE", f"/api/chats/{other['id']}", origin=own(port))
            assert r.status == 200
    conn.close()
    assert checked and [e for e in events if e["kind"] == "done"], events[-1]
    msgs = HistoryStore(db).messages(busy_id)
    assert [m["role"] for m in msgs] == ["user", "bot"] and msgs[1]["text"] == reply_of(events)
    r, _ = request(port, "POST", "/api/new", origin=own(port))  # what the window sends after a background reply
    assert r.status == 200
