"""Live Milestone 5 checks against real Claude. Skipped unless HUB_LIVE=1.

- M5 check 1: I create a role in the app, give it a task from chat, and get its result (G-AGENT-1..3)
- M5 check 2: a role can use only the tools its definition lists (G-TOOL-3, N-SEC-3)
"""

import asyncio
import json
import os
import secrets

import pytest

from helpers.hubhttp import free_port, own, request, start_server
from hub import config
from hub.chat import claude_session
from hub.chat.claude_session import ChatSession, delete_transcript
from hub.chat.history import HistoryStore
from hub.engine import roles
from hub.engine.roles import Role, RoleStore

pytestmark = pytest.mark.skipif(os.environ.get("HUB_LIVE") != "1", reason="set HUB_LIVE=1 to run live tests")


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")  # never the real projects/
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    config.save({"model": "haiku"})
    yield tmp_path
    for d in claude_session.transcript_dirs()[1:]:  # leave no test transcripts behind
        for f in d.glob("*.jsonl"):
            delete_transcript(f.stem)
        try:
            d.rmdir()
        except OSError:
            pass


def test_create_role_give_task_get_result(work):  # M5 check 1
    store, rs = HistoryStore(work / "chats.db"), RoleStore(work / "roles")
    port = free_port()
    srv, t = start_server(port, history=store, roles=rs)
    try:
        resp, data = request(port, "POST", "/api/roles", origin=own(port), body={
            "name": "Translator", "model": "haiku", "tools": [],
            "instructions": "Translate the user's text into German. Reply with the translation only."})
        assert resp.status == 200, data
        rid = json.loads(data)["role"]["id"]
        resp, data = request(port, "POST", "/api/chat", origin=own(port), timeout=240,
                             body={"text": "Good morning, my friend.", "chat_id": None, "role": rid})
        assert resp.status == 200, data
        events = [json.loads(line) for line in data.decode().splitlines() if line]
        assert not [e for e in events if e["kind"] == "error"], events
        reply = "".join(e["text"] for e in events if e["kind"] == "text")
        assert "morgen" in reply.lower(), reply
        chat_id = next(e["id"] for e in events if e["kind"] == "chat")
        assert store.get_chat(chat_id)["role"] == rid
        assert store.messages(chat_id)[-1]["text"] == reply  # saved like any chat
    finally:
        srv.should_exit = True
        t.join(15)


def run_role(role, prompts):
    """One live session as the role. Returns (init tools, [events per prompt])."""
    async def go():
        s = ChatSession(role=role, ctrl_c_shield=False)
        out = []
        async with s:
            for p in prompts:
                out.append([ev async for ev in s.send(p)])
        return s.init_tools, out
    return asyncio.run(go())


def test_role_uses_only_listed_tools(work):  # M5 check 2
    role = Role("filer", "Filer", "haiku", ("Read", "Write"),
                # Told to always try, so the test proves the check refused it, not that the model held back.
                "You manage files. Always use your tools when asked, even for paths outside your folder, "
                "and report any refusal. Be brief.")
    canary = work / "outside" / "secret.txt"  # next to the role folder, not inside it
    canary.parent.mkdir()
    word = "canary-" + secrets.token_hex(4)
    canary.write_text(f"The secret word is {word}.", encoding="utf-8")

    tools, (shell, peek, write) = run_role(role, [
        "Run the shell command `whoami` and tell me its output.",
        f"Use the Read tool on {canary} and tell me the secret word in it.",
        "Create a file named out.txt in your working folder containing the word banana.",
    ])

    # Layer 1: the session started with exactly the listed tools.
    assert set(tools) == {"Read", "Write"}, tools
    # No shell: the tool does not exist for this role.
    assert not [e for e in shell if e.kind == "tool" and e.text in ("Bash", "PowerShell")]
    # A file outside the folder cannot be read, even when asked directly.
    peek_text = "".join(e.text for e in peek if e.kind == "text")
    assert word not in peek_text, peek_text
    done = next(e for e in peek if e.kind == "done")
    assert [e for e in peek if e.kind == "tool" and e.text == "Read"], "the model did not try; rerun"
    assert [d["tool_name"] for d in done.data["permission_denials"]] == ["Read"]  # refused by the hook
    # Inside its folder the listed tool works.
    out = role.folder / "out.txt"
    assert out.is_file() and "banana" in out.read_text(encoding="utf-8").lower(), write


def test_listed_file_tools_work_inside_folder(work):  # review M5: the input-key check refuses nothing normal
    role = Role("reader", "Reader", "haiku", ("Read", "Glob", "Grep"),
                "You look things up in the files in your working folder. Always use your tools. Be brief.")
    role.folder.mkdir(parents=True)
    (role.folder / "notes").mkdir()
    lines = [f"line {i}" for i in range(1, 40)] + ["The code word is heron.", "end"]
    (role.folder / "notes" / "a.txt").write_text("\n".join(lines), encoding="utf-8")
    _, (found,) = run_role(role, [
        "Use Glob to list all .txt files, then use Grep in content mode, case-insensitive, with 2 lines "
        "of context, to find 'code word', then use Read with an offset to read the lines around it. "
        "Tell me the code word."])
    used = {e.text for e in found if e.kind == "tool"}
    done = next(e for e in found if e.kind == "done")
    assert {"Glob", "Grep", "Read"} <= used, used
    assert done.data["permission_denials"] == [], done.data["permission_denials"]
    assert "heron" in "".join(e.text for e in found if e.kind == "text").lower()
