"""Milestone 3 server checks: history endpoints and the settings page. No Claude calls.

Transcript deletes are redirected to a temporary folder, so real transcripts are never touched.
"""

import json
import uuid

import pytest

from helpers.hubhttp import free_port, own, request, start_server
from hub import config
from hub.chat import claude_session
from hub.chat.history import HistoryStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr(claude_session, "CHAT_WORKDIR", tmp_path / "work")
    claude_session.transcript_dir().mkdir(parents=True)
    store = HistoryStore(tmp_path / "chats.db")
    port = free_port()
    srv, t = start_server(port, history=store)
    yield port, store
    srv.should_exit = True
    t.join(5)


def fake_transcript() -> str:
    sid = str(uuid.uuid4())
    (claude_session.transcript_dir() / f"{sid}.jsonl").write_text("{}\n")
    return sid


def call(port, method, path, body=None):
    resp, data = request(port, method, path, origin=None if method == "GET" else own(port), body=body)
    return resp.status, json.loads(data or b"{}")


def test_list_search_open(env):  # M3 check 1, F-HIST-2, F-HIST-3
    port, store = env
    a = store.create_chat("Pasta recipes")
    store.add_message(a["id"], "user", "How do I cook pasta?")
    store.add_message(a["id"], "bot", "Boil water.")
    store.create_chat("Trip plan")
    status, data = call(port, "GET", "/api/chats")
    assert status == 200 and [c["title"] for c in data["chats"]] == ["Trip plan", "Pasta recipes"]
    status, data = call(port, "GET", "/api/chats?q=boil")
    assert [c["title"] for c in data["chats"]] == ["Pasta recipes"]
    status, data = call(port, "GET", f"/api/chats/{a['id']}")
    assert status == 200 and [m["text"] for m in data["messages"]] == ["How do I cook pasta?", "Boil water."]
    assert "session_id" not in data["chat"]
    assert call(port, "GET", "/api/chats/999")[0] == 404


def test_rename(env):  # F-HIST-3
    port, store = env
    c = store.create_chat("old")
    assert call(port, "PATCH", f"/api/chats/{c['id']}", {"title": "  new   name "})[0] == 200
    assert store.get_chat(c["id"])["title"] == "new name"
    assert call(port, "PATCH", f"/api/chats/{c['id']}", {"title": "  "})[0] == 400
    assert call(port, "PATCH", f"/api/chats/{c['id']}", {"title": 5})[0] == 400
    assert call(port, "PATCH", "/api/chats/999", {"title": "x"})[0] == 404


def test_delete_removes_chat_and_transcripts(env):  # M3 check 4, D-2
    port, store = env
    c = store.create_chat("bye")
    sids = [fake_transcript(), fake_transcript()]
    for sid in sids:
        store.set_session(c["id"], sid)
    other = fake_transcript()  # belongs to no chat: must stay
    status, data = call(port, "DELETE", f"/api/chats/{c['id']}")
    assert status == 200 and data["transcripts_removed"] == 2
    assert store.get_chat(c["id"]) is None
    folder = claude_session.transcript_dir()
    assert not any((folder / f"{s}.jsonl").exists() for s in sids)
    assert (folder / f"{other}.jsonl").exists()
    assert call(port, "DELETE", f"/api/chats/{c['id']}")[0] == 404


def test_delete_needs_origin(env):  # N-SEC-4 still applies to the new endpoints
    port, store = env
    c = store.create_chat("x")
    resp, _ = request(port, "DELETE", f"/api/chats/{c['id']}")
    assert resp.status == 403 and store.get_chat(c["id"])


def test_delete_all_needs_confirmation(env):  # D-3
    port, store = env
    store.create_chat("a")
    sid = fake_transcript()
    assert call(port, "POST", "/api/history/delete-all", {})[0] == 400
    assert store.list_chats()
    status, data = call(port, "POST", "/api/history/delete-all", {"confirm": "DELETE"})
    assert status == 200 and store.list_chats() == []
    assert not (claude_session.transcript_dir() / f"{sid}.jsonl").exists()


def test_export(env, tmp_path, monkeypatch):  # F-HIST-5
    port, store = env
    from hub.chat import history
    monkeypatch.setattr(history, "EXPORT_DIR", tmp_path / "exports")
    c = store.create_chat("Export me")
    store.add_message(c["id"], "user", "hi")
    status, data = call(port, "POST", f"/api/chats/{c['id']}/export")
    assert status == 200 and data["path"].endswith(".md")
    assert "# Export me" in open(data["path"], encoding="utf-8").read()


def test_chat_to_missing_chat_is_404(env):
    port, _ = env
    assert call(port, "POST", "/api/chat", {"text": "hi", "chat_id": 999})[0] == 404
    assert call(port, "POST", "/api/chat", {"text": "hi", "chat_id": "1"})[0] == 400
    assert call(port, "POST", "/api/chat", {"text": "hi", "chat_id": True})[0] == 400


# ---- Settings page (M3 check 3) ---------------------------------------------------

def test_settings_page_saves_to_config_json(env):  # M3 check 3, F-PERS-4, N-USE-2
    port, _ = env
    body = {"bot_name": " Sparky ", "avatar": "🦊", "theme": "dark", "model": "haiku",
            "web_enabled": False, "prompts": {"fun": "Be a pirate.", "straight": "  "}}
    status, data = call(port, "POST", "/api/settings", body)
    assert status == 200
    saved = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["bot_name"] == "Sparky" and saved["avatar"] == "🦊" and saved["theme"] == "dark"
    assert saved["model"] == "haiku" and saved["web_enabled"] is False
    assert saved["prompts"] == {"fun": "Be a pirate."}  # empty = built-in text again
    assert data["settings"]["bot_name"] == "Sparky"


@pytest.mark.parametrize("body", [
    {"theme": "neon"}, {"bot_name": ""}, {"bot_name": "x" * 41}, {"avatar": "toolongavatar"},
    {"prompts": {"evil": "x"}}, {"prompts": {"fun": 5}}, {"prompts": "x"}, {"custom_prompt": "x" * 20001},
])
def test_bad_settings_rejected(env, body):
    port, _ = env
    assert call(port, "POST", "/api/settings", body)[0] == 400


def test_status_lists_builtin_prompts(env):  # for "Reset" on the settings page
    port, _ = env
    _, data = call(port, "GET", "/api/status")
    assert set(data["builtin_prompts"]) == {"fun", "straight", "explainer"}
    assert {"bot_name", "avatar", "theme", "prompts"} <= set(data["settings"])


# ---- review M3 finding 1: no message may slip in while a process closes -------------

class SlowCloseSession:
    """Stands in for ChatSession: no Claude, close() takes 0.5 s like the real CLI."""
    count = 0
    made: list = []  # every instance, so tests can look at what the server set
    fail = False  # True: the reply fails with an error and no text

    def __init__(self, **kwargs):
        from hub.chat.personalities import system_prompt
        SlowCloseSession.count += 1
        SlowCloseSession.made.append(self)
        self.name = f"proc{SlowCloseSession.count}"
        self.session_id = kwargs.get("resume") or str(uuid.uuid4())
        self.options = type("O", (), {"system_prompt": system_prompt(kwargs.get("personality", "straight"),
                                                                     kwargs.get("custom_prompt")),
                                      "resume": kwargs.get("resume")})()
        self.pending_personality = self.pending_replay = None
        self.stop_requested = False
        self.last_rate_limit = None
        self.api_key_source = "none"

    async def connect(self): pass

    async def reconfigure(self, **kw): return False

    async def stop(self): pass

    async def close(self):
        import asyncio
        await asyncio.sleep(0.5)

    async def send(self, text):
        from hub.chat.claude_session import Event
        yield Event("init")
        if self.stop_requested:  # like the real session: a Stop from before the query ends the turn
            yield Event("done", data={"subtype": "success", "terminal_reason": "aborted_streaming",
                                      "usage": {}, "is_error": False})
            return
        if SlowCloseSession.fail:
            yield Event("error", "server_error")
            yield Event("done", data={"subtype": "error", "terminal_reason": "error", "usage": {}, "is_error": True})
            return
        self.pending_personality = None  # delivered, like the real session
        yield Event("text", self.name)
        yield Event("done", data={"subtype": "success", "terminal_reason": "completed", "usage": {}, "is_error": False})


@pytest.fixture
def slow_env(env, monkeypatch):
    import hub.server
    monkeypatch.setattr(hub.server, "ChatSession", SlowCloseSession)
    monkeypatch.setattr(SlowCloseSession, "made", [])
    monkeypatch.setattr(SlowCloseSession, "fail", False)
    return env


def post_chat(port, text, chat_id):
    resp, data = request(port, "POST", "/api/chat", origin=own(port), body={"text": text, "chat_id": chat_id})
    return resp.status, data


@pytest.mark.parametrize("action", ["new", "delete"])
def test_message_during_close_is_refused(slow_env, action):
    import threading
    import time
    port, store = slow_env
    status, data = post_chat(port, "first", None)
    cid = json.loads(data.decode().splitlines()[0])["id"]
    path = ("POST", "/api/new") if action == "new" else ("DELETE", f"/api/chats/{cid}")
    t = threading.Thread(target=lambda: request(port, *path, origin=own(port)))
    t.start()
    time.sleep(0.15)  # the close is in progress
    assert post_chat(port, "sneaky", cid)[0] in (409, 404)  # refused, never resumes the old chat
    t.join(5)
    status, data = post_chat(port, "fresh chat", None)
    new_id = json.loads(data.decode().splitlines()[0])["id"]
    assert new_id != cid  # ids are never reused
    sessions = {store.get_chat(c["id"])["session_id"] for c in store.list_chats()}
    assert len(sessions) == len(store.list_chats())  # no two chats share a process


def test_delete_of_chat_whose_process_is_closing_is_refused(slow_env, monkeypatch):  # review 2 finding 1
    import threading
    import time
    port, store = slow_env
    ends = []
    orig = SlowCloseSession.close

    async def close(self):
        await orig(self)
        ends.append(time.time())

    monkeypatch.setattr(SlowCloseSession, "close", close)
    a = json.loads(post_chat(port, "first", None)[1].decode().splitlines()[0])["id"]
    t = threading.Thread(target=lambda: post_chat(port, "second chat", None))  # closes chat A's process
    t.start()
    time.sleep(0.15)
    status, _ = call(port, "DELETE", f"/api/chats/{a}")
    t.join(5)
    assert status == 409 and store.get_chat(a)  # refused while closing, chat kept
    assert call(port, "DELETE", f"/api/chats/{a}")[0] == 200  # fine once the close is done


def test_stop_while_old_process_closes_stops_the_reply(slow_env):  # review 2 finding 2
    import threading
    import time
    port, store = slow_env
    post_chat(port, "first", None)
    out = {}
    t = threading.Thread(target=lambda: out.update(r=post_chat(port, "second chat", None)))
    t.start()
    time.sleep(0.15)
    assert call(port, "POST", "/api/stop")[1]["stopped"]
    t.join(5)
    events = [json.loads(line) for line in out["r"][1].decode().splitlines()]
    done = [e for e in events if e["kind"] == "done"]
    assert done and done[0]["terminal_reason"].startswith("aborted"), events
    assert not [e for e in events if e["kind"] == "text"]


def test_personality_note_only_when_it_changed(slow_env, monkeypatch):  # review 3 finding 2, F-PERS-3
    port, store = slow_env
    notes = []  # the personality note each send carried (None = no note)
    orig = SlowCloseSession.send

    async def spy(self, text):
        notes.append(self.pending_personality)
        async for ev in orig(self, text):
            yield ev

    monkeypatch.setattr(SlowCloseSession, "send", spy)
    cid = json.loads(post_chat(port, "first", None)[1].decode().splitlines()[0])["id"]
    sid = store.get_chat(cid)["session_id"]
    (claude_session.transcript_dir() / f"{sid}.jsonl").write_text("{}\n")  # so the chat resumes

    def reopen_and_send():
        call(port, "POST", "/api/new")  # closes the process, like reopening the app
        post_chat(port, "again", cid)
        return notes[-1]

    assert reopen_and_send() is None  # same personality as before: no note
    call(port, "POST", "/api/settings", {"personality": "fun"})  # changed while the chat is closed
    note = reopen_and_send()
    assert note and "witty" in note
    assert store.get_chat(cid)["system_prompt"] == note  # delivered and remembered
    assert reopen_and_send() is None  # not repeated after that


def test_failed_first_message_leaves_no_empty_chat(slow_env):  # review 3 finding 4
    port, store = slow_env
    SlowCloseSession.fail = True
    status, data = post_chat(port, "this fails", None)
    events = [json.loads(line) for line in data.decode().splitlines()]
    kinds = [e["kind"] for e in events]
    assert "error" in kinds and "chat_removed" in kinds
    assert store.list_chats() == []


def test_failed_turn_on_fresh_session_records_its_prompt(slow_env):  # review 4 finding 1
    port, store = slow_env
    cid = json.loads(post_chat(port, "first", None)[1].decode().splitlines()[0])["id"]
    old = store.get_chat(cid)
    call(port, "POST", "/api/new")
    call(port, "POST", "/api/settings", {"personality": "fun"})
    SlowCloseSession.fail = True  # no transcript on disk: a fresh session with replay, then an error
    post_chat(port, "again", cid)
    chat = store.get_chat(cid)
    assert chat["session_id"] != old["session_id"]
    assert chat["system_prompt"] == SlowCloseSession.made[-1].options.system_prompt != old["system_prompt"]
