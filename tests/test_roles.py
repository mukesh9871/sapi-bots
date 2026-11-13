"""Milestone 5 offline checks: roles (G-AGENT-1..3) and per-role tools (G-TOOL-1..3). No Claude calls.

- M5 check 1: create a role in the app, give it a task from chat, get its result
  (API + a stand-in session here, the real thing in test_live_roles.py)
- M5 check 2: a role can use only the tools its definition lists (option layers and path check)
"""

import asyncio
import json
import os
import sqlite3
import uuid
from pathlib import Path

import pytest

from helpers.hubhttp import free_port, own, request, start_server
from hub import config
from hub.chat import claude_session
from hub.chat.claude_session import DENIED_TOOLS, build_options, outside_folder
from hub.chat.history import HistoryStore
from hub.engine import roles
from hub.engine.roles import Role, RoleError, RoleStore, parse

RESEARCHER = dict(name="Researcher", model="haiku", tools=["WebSearch", "WebFetch"],
                  instructions="Find facts on the web and answer briefly with sources.")


@pytest.fixture
def work(tmp_path, monkeypatch):
    """Role folders and transcripts in a temp place, never the real projects/ or ~/.claude."""
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr(claude_session, "CHAT_WORKDIR", tmp_path / "chat")
    return tmp_path


@pytest.fixture
def store(work):
    return RoleStore(work / "roles")


# ---- G-AGENT-1, G-AGENT-2: roles as readable files ----------------------------------------

def test_role_is_a_readable_file(store):
    role = store.save(None, **RESEARCHER)
    text = (store.folder / f"{role.id}.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: Researcher\nmodel: haiku\ntools: WebSearch, WebFetch\n---\n")
    assert "Find facts on the web" in text
    assert store.get(role.id) == role


def test_hand_written_file_is_read(store):
    store.folder.mkdir(parents=True)
    (store.folder / "writer.md").write_text("---\r\nName: Writer\r\ntools: Write ,Read\r\n---\r\n\r\nWrite notes.\r\n",
                                            encoding="utf-8")
    r = store.get("writer")
    assert (r.name, r.model, r.tools, r.instructions, r.problem) == ("Writer", "", ("Write", "Read"), "Write notes.", "")


def test_rename_keeps_id_and_copy_and_delete(store):  # G-AGENT-2: create, edit, copy, delete
    r = store.save(None, **RESEARCHER)
    r2 = store.save(r.id, **{**RESEARCHER, "name": "Fact finder"})
    assert r2.id == r.id and store.get(r.id).name == "Fact finder"  # chats stay linked
    c = store.copy(r.id)
    assert c.id != r.id and c.name == "Fact finder (copy)" and c.tools == r2.tools
    assert store.save(None, **RESEARCHER).id == "researcher-2"  # ids never clash
    assert store.delete(c.id) and store.get(c.id) is None and not store.delete(c.id)


@pytest.mark.parametrize("change, problem", [
    ({"tools": ["Bash"]}, "not allowed"),  # G-TOOL-3: no shell, ever
    ({"tools": ["WebSearch", "PowerShell"]}, "not allowed"),
    ({"tools": ["mcp__x__y"]}, "not allowed"),
    ({"tools": "WebSearch"}, "bad tool list"),
    ({"name": ""}, "name"), ({"name": "a\nb"}, "name"), ({"name": "x" * 41}, "name"),
    ({"name": "x\r---"}, "name"), ({"name": "a\tb"}, "name"),  # review M5 round 3
    ({"model": "gpt"}, "model"),
    ({"instructions": "  "}, "empty"),
])
def test_bad_roles_are_refused(store, change, problem):
    with pytest.raises(RoleError, match=problem):
        store.save(None, **{**RESEARCHER, **change})


def test_hand_edit_with_a_forbidden_tool_never_runs(store, work):  # G-TOOL-3
    store.folder.mkdir(parents=True)
    (store.folder / "sneaky.md").write_text("---\nname: Sneaky\ntools: Read, Bash\n---\nDo things.", encoding="utf-8")
    r = store.get("sneaky")
    assert "Bash" in r.problem
    with pytest.raises(ValueError):
        build_options(role=r)
    with pytest.raises(RoleError):
        store.copy("sneaky")


@pytest.mark.parametrize("bad", ["../secret", "..", "a/b", "C:x", "", None, "UPPER", "x.md"])
def test_role_ids_never_become_paths(store, bad):
    assert store.get(bad) is None and not store.delete(bad)


def test_deleted_role_id_is_never_reused(store, work):  # review M5
    r = store.save(None, **RESEARCHER)
    store.delete(r.id)
    store.in_use = lambda: {"researcher"}  # an old chat still uses the id
    assert store.save(None, **RESEARCHER).id == "researcher-2"
    store.in_use = lambda: set()
    (work / "projects" / "role-writer").mkdir(parents=True)  # its folder is still there
    assert store.save(None, **{**RESEARCHER, "name": "Writer"}).id == "writer-2"
    (store.folder / "Editor.md").write_text("hand made", encoding="utf-8")  # same file as editor.md on Windows
    assert store.save(None, **{**RESEARCHER, "name": "Editor"}).id == "editor-2"
    assert (store.folder / "Editor.md").read_text(encoding="utf-8") == "hand made"


def test_parse_without_header_is_a_problem():
    assert parse("x", "just text").problem
    assert parse("x", "---\nname: X\ntools: Read\nDo things.").problem  # never closed (review M5 round 3)


def test_hand_written_role_with_bom_and_edit_only(store):  # review M5 round 5
    store.folder.mkdir(parents=True)
    (store.folder / "ed.md").write_text("---\nname: Ed\ntools: Edit\n---\nFix typos.", encoding="utf-8-sig")
    r = store.get("ed")
    assert r.problem == "" and r.tools == ("Read", "Edit")


def test_write_or_edit_brings_read(store):  # review M5 round 3: the CLI edits only files it has read
    assert store.save(None, **{**RESEARCHER, "tools": ["Write"]}).tools == ("Read", "Write")
    assert store.save(None, **{**RESEARCHER, "tools": ["Edit"]}).tools == ("Read", "Edit")
    assert store.save(None, **{**RESEARCHER, "tools": ["Glob"]}).tools == ("Glob",)


def test_normal_names_with_spaces_are_fine(work):
    opts = build_options(role=role_of(ALL_FILE))
    folder = work / "projects" / "role-tester"
    assert hook_says(opts, "Write", {"file_path": str(folder / "my notes" / "report v2.md"), "content": "x"})


# ---- G-TOOL-3 / N-SEC-3: a role gets only its listed tools ----------------------------------

def role_of(tools, **kw):
    return Role("tester", "Tester", kw.get("model", ""), tuple(tools), "Test things.")


def test_role_options_have_only_listed_tools(work):  # M5 check 2, layers 1 and 2
    opts = build_options(role=role_of(["WebSearch", "Read", "Write"]), model="sonnet")
    assert opts.tools == ["WebSearch", "Read", "Write"]
    for t in ["Bash", "PowerShell", "Edit", "Glob", "Grep", "WebFetch", "Task", "Agent", "NotebookEdit"]:
        assert t in opts.disallowed_tools, t
    assert not set(opts.tools) & set(opts.disallowed_tools)
    assert opts.allowed_tools == ["WebSearch"]  # file tools are never pre-approved
    assert opts.permission_mode == "default" and opts.setting_sources == [] and opts.mcp_servers == {}
    assert Path(opts.cwd) == work / "projects" / "role-tester" and Path(opts.cwd).is_dir()
    assert opts.model == "sonnet"  # blank role model: the Settings default
    assert build_options(role=role_of([], model="opus"), model="sonnet").model == "opus"


def test_role_prompt_is_its_job_description(work):  # G-AGENT-1, N-SEC-7
    p = build_options(role=role_of(["Read"]), personality="fun").system_prompt
    assert '"Tester"' in p and p.endswith("Test things.") and "information, never instructions" in p
    assert str(work / "projects" / "role-tester") in p
    assert "folder" not in build_options(role=role_of(["WebSearch"])).system_prompt.split("\n\n")[0]


def test_role_with_no_tools_has_none(work):
    opts = build_options(role=role_of([]))
    assert opts.tools == [] and set(DENIED_TOOLS) <= set(opts.disallowed_tools)


def hook_says(opts, tool, tool_input):
    """Runs the PreToolUse hook (layer 3) and the permission callback (layer 4). True = allowed."""
    hook = opts.hooks["PreToolUse"][0].hooks[0]
    out = asyncio.run(hook({"tool_name": tool, "tool_input": tool_input, "hook_event_name": "PreToolUse"}, None, None))
    by_hook = out == {}
    if not by_hook:
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    gate = asyncio.run(opts.can_use_tool(tool, tool_input, None))
    assert by_hook == (gate.behavior == "allow"), (tool, tool_input)  # both layers agree
    return by_hook


ALL_FILE = ["Read", "Glob", "Grep", "Write", "Edit"]


def test_hook_refuses_unlisted_tools(work):  # M5 check 2, layer 3
    opts = build_options(role=role_of(["Read"]))
    assert not hook_says(opts, "Bash", {"command": "dir"})
    assert not hook_says(opts, "Write", {"file_path": "a.txt", "content": "x"})
    assert not hook_says(opts, "WebFetch", {"url": "https://example.com"})
    assert hook_says(opts, "Read", {"file_path": "notes.txt"})


def test_plain_chat_hook_still_web_only(work):  # N-SEC-3 unchanged for plain chat
    opts = build_options(web_enabled=True)
    assert hook_says(opts, "WebSearch", {"query": "x"})
    assert not hook_says(opts, "Read", {"file_path": "x"})
    assert not hook_says(build_options(web_enabled=False), "WebSearch", {"query": "x"})


def test_files_inside_the_folder_are_allowed(work):
    opts = build_options(role=role_of(ALL_FILE))
    folder = work / "projects" / "role-tester"
    for tool, inp in [("Read", {"file_path": str(folder / "a.txt")}), ("Read", {"file_path": "sub/a.txt"}),
                      ("Write", {"file_path": "report.md", "content": "x"}),
                      ("Edit", {"file_path": "./report.md", "old_string": "a", "new_string": "b"}),
                      ("Glob", {"pattern": "**/*.md"}), ("Glob", {"pattern": "*.md", "path": str(folder)}),
                      ("Grep", {"pattern": "x", "glob": "*.txt"}), ("Grep", {"pattern": "..", "path": "sub"}),
                      ("Read", {"file_path": "claude.md.bak", "offset": 1, "limit": 5}),
                      ("Grep", {"pattern": "x", "output_mode": "content", "-i": True, "-C": 2, "head_limit": 5}),
                      ("Grep", {"pattern": "x", "output_mode": "content", "-o": True}),  # review M5 round 4
                      ("Read", {"file_path": "big.log", "allow_large": True})]:
        assert hook_says(opts, tool, inp), (tool, inp)


ESCAPES = [
    ("Read", {"file_path": "../role-other/secret.txt"}),
    ("Read", {"file_path": "sub/../../x.txt"}),
    ("Read", {"file_path": "C:/Windows/win.ini"}),
    ("Read", {"file_path": "/etc/passwd"}),
    ("Read", {"file_path": "~/.claude/.credentials.json"}),
    ("Read", {"file_path": "//server/share/x"}),
    ("Read", {"file_path": "D:x"}),
    ("Read", {"file_path": ""}),
    ("Read", {}),
    ("Read", {"file_path": 5}),
    ("Write", {"file_path": "../../start.bat", "content": "x"}),
    ("Edit", {"file_path": str(Path.home() / "x.txt"), "old_string": "a", "new_string": "b"}),
    ("Glob", {"pattern": "../**/*"}),
    ("Glob", {"pattern": "C:/Users/**"}),
    ("Glob", {"pattern": "/**"}),
    ("Glob", {"pattern": "*", "path": ".."}),
    ("Glob", {"pattern": "~/*"}),
    ("Grep", {"pattern": "x", "path": "C:/"}),
    ("Grep", {"pattern": "x", "glob": "..\\**"}),
    ("Grep", {"pattern": "x", "path": 3}),
    ("Grep", {"pattern": "..", "glob": "../*"}),
    # review M5: the CLI trims paths, so spaces at either end must not hide ".." or a drive
    ("Grep", {"pattern": ".", "path": " ../.."}), ("Glob", {"pattern": "*", "path": " .."}),
    ("Glob", {"pattern": "*", "path": "..  "}), ("Grep", {"pattern": ".", "path": "\t.."}),
    ("Grep", {"pattern": ".", "path": "..\n"}), ("Glob", {"pattern": "*", "path": " C:\\Windows"}),
    ("Glob", {"pattern": "*", "path": " /etc"}), ("Glob", {"pattern": "*", "path": " \\\\host\\share"}),
    ("Read", {"file_path": " ../../config.json"}), ("Glob", {"pattern": " ../*"}),
    ("Glob", {"pattern": "a/ .. /b"}), ("Grep", {"pattern": "x", "glob": " ..\\*"}),
    # review M5: no files that steer the user's coding agent, no unknown input keys
    ("Write", {"file_path": "CLAUDE.md", "content": "x"}), ("Write", {"file_path": "sub/.claude/settings.json", "content": "x"}),
    ("Edit", {"file_path": ".git/config", "old_string": "a", "new_string": "b"}),
    ("Glob", {"pattern": "*", "machine": "other"}), ("Read", {"file_path": "a.txt", "host": "x"}),
    # review M5 round 2: NTFS streams, trailing dots, control marks inside a path
    ("Write", {"file_path": "CLAUDE.md::$DATA", "content": "x"}), ("Write", {"file_path": "CLAUDE.md.", "content": "x"}),
    ("Edit", {"file_path": ".git./config", "old_string": "a", "new_string": "b"}),
    ("Write", {"file_path": ".claude. /settings.json", "content": "x"}),
    ("Read", {"file_path": "a.txt:hidden"}), ("Read", {"file_path": "x/..\t/secret.txt"}),
    ("Glob", {"pattern": "a\nb"}),
    # review M5 round 3: JavaScript's trim() also removes U+FEFF, Python's strip() does not
    ("Glob", {"pattern": "*", "path": "﻿.."}), ("Grep", {"pattern": ".", "path": "﻿/"}),
    ("Grep", {"pattern": ".", "path": "﻿//example.invalid/share"}), ("Glob", {"pattern": "﻿../*"}),
    ("Glob", {"pattern": "*", "path": " .."}), ("Glob", {"pattern": "*", "path": "..​"}),
    ("Read", {"file_path": " ../x"}),
    # review M5 round 4: other agents' and editors' config
    ("Write", {"file_path": "AGENTS.md", "content": "x"}), ("Write", {"file_path": ".vscode/tasks.json", "content": "x"}),
    ("Write", {"file_path": ".github/workflows/x.yml", "content": "x"}),
    # review M5 round 5: files Explorer opens on its own
    ("Write", {"file_path": "notes.url", "content": "x"}), ("Write", {"file_path": "go.LNK", "content": "x"}), ("Write", {"file_path": "a.theme", "content": "x"}),
    ("Write", {"file_path": "sub/desktop.ini", "content": "x"}),
    # review M5 round 6: files pytest runs on its own, rooted paths the CLI maps to another drive
    ("Write", {"file_path": "conftest.py", "content": "x"}), ("Write", {"file_path": "sub/test_x.py", "content": "x"}),
    ("Edit", {"file_path": "x_test.py", "old_string": "a", "new_string": "b"}),
    ("Write", {"file_path": "pyproject.toml", "content": "x"}),
    ("Glob", {"pattern": "*", "path": "/w/hub/projects/role-tester"}), ("Grep", {"pattern": "x", "path": "\\x"}),
]


def test_pytest_never_collects_role_folders():  # review M5 round 6, first layer
    import configparser
    ini = configparser.ConfigParser()
    ini.read(Path(__file__).parent.parent / "pytest.ini")
    assert ini["pytest"]["testpaths"].split() == ["tests"]
    assert "projects" in ini["pytest"]["norecursedirs"].split()


@pytest.mark.parametrize("path", ["\\\\attacker.example\\share\\x.txt", "\\\\?\\UNC\\attacker.example\\s\\x",
                                  "//attacker.example/share", "\\/attacker.example/share"])
def test_network_paths_refused_without_touching_them(work, monkeypatch, path):  # review M5 round 5
    opened = []
    real = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **k: (opened.append(str(self)), real(self, *a, **k))[1])
    folder = work / "projects" / "role-tester"
    folder.mkdir(parents=True)
    for tool, key in [("Read", "file_path"), ("Glob", "path"), ("Grep", "path")]:
        assert outside_folder(tool, {key: path, "pattern": "x"} if tool != "Read" else {key: path}, folder)
    assert not [p for p in opened if "attacker" in p], opened


@pytest.mark.parametrize("tool, inp", ESCAPES)
def test_paths_outside_the_folder_are_refused(work, tool, inp):  # M5 check 2, layers 3 and 4
    assert not hook_says(build_options(role=role_of(ALL_FILE)), tool, inp)


def test_link_pointing_out_is_refused(work):
    folder = work / "projects" / "role-tester"
    opts = build_options(role=role_of(ALL_FILE))
    outside = work / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    try:
        os.symlink(outside, folder / "door", target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        import _winapi  # a junction needs no admin rights, unlike a symbolic link
        _winapi.CreateJunction(str(outside), str(folder / "door"))
    assert not hook_says(opts, "Read", {"file_path": "door/secret.txt"})


def test_outside_folder_ignores_other_tools(work):
    assert outside_folder("WebFetch", {"url": "file:///C:/x"}, work) is None  # not a file tool


# ---- chats remember their role, transcripts in role folders (D-2) ---------------------------

def test_chat_role_saved_and_old_database_migrated(tmp_path):
    db = tmp_path / "c.db"
    with sqlite3.connect(db) as c:  # a chats.db from Milestone 4
        c.executescript("""
            CREATE TABLE chats (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, session_id TEXT, system_prompt TEXT);
            INSERT INTO chats VALUES (1, 'old', 0, 0, NULL, NULL);
        """)
    h = HistoryStore(db)
    assert h.get_chat(1)["role"] is None
    new = h.create_chat("find x", "researcher")
    assert h.get_chat(new["id"])["role"] == "researcher"
    assert {c["id"]: c["role"] for c in h.list_chats()} == {1: None, new["id"]: "researcher"}


def test_role_transcripts_are_found_and_deleted(work):  # D-2, D-3
    sid, other = str(uuid.uuid4()), str(uuid.uuid4())
    role_dir = claude_session.transcript_dir(role_of([]).folder)
    gone_role_dir = claude_session.transcript_dir(work / "projects" / "role-deleted")
    for d, s in [(role_dir, sid), (gone_role_dir, other)]:
        d.mkdir(parents=True)
        (d / f"{s}.jsonl").write_text("{}\n")
    assert claude_session.transcript_exists(sid, role_of([]).folder)
    assert not claude_session.transcript_exists(sid)  # not in the plain chat folder
    assert set(claude_session.all_transcript_ids()) == {sid, other}
    assert claude_session.delete_transcript(sid) and not (role_dir / f"{sid}.jsonl").exists()
    assert claude_session.delete_transcript(other)


# ---- API (G-AGENT-2) and role chats (G-AGENT-3) --------------------------------------------

class FakeSession:
    """Stands in for ChatSession: records how the server configured it, answers with its prompt."""
    made: list = []

    def __init__(self, **kwargs):
        kwargs.pop("ctrl_c_shield", None)
        self.kwargs = kwargs
        FakeSession.made.append(self)
        self.session_id = str(uuid.uuid4())
        self.options = build_options(**kwargs)
        self.pending_personality = self.pending_replay = None
        self.stop_requested = False
        self.last_rate_limit = None
        self.api_key_source = "none"

    async def connect(self): pass

    async def reconfigure(self, **kw):
        changed = {**self.kwargs, **kw} != self.kwargs
        self.kwargs = {**self.kwargs, **kw}
        self.options = build_options(**self.kwargs)
        return changed

    async def stop(self): pass

    async def close(self): pass

    async def send(self, text):
        from hub.chat.claude_session import Event
        yield Event("init")
        yield Event("text", f"result for: {text}")
        yield Event("done", data={"subtype": "success", "terminal_reason": "completed", "usage": {}, "is_error": False})


@pytest.fixture
def hub(work, monkeypatch):
    import hub.server
    monkeypatch.setattr(config, "CONFIG_PATH", work / "config.json")
    monkeypatch.setattr(hub.server, "ChatSession", FakeSession)
    monkeypatch.setattr(FakeSession, "made", [])
    rs = RoleStore(work / "roles")
    hist = HistoryStore(work / "chats.db")
    port = free_port()
    srv, t = start_server(port, history=hist, roles=rs)
    yield port, hist, rs
    srv.should_exit = True
    t.join(5)


def call(port, method, path, body=None):
    resp, data = request(port, method, path, origin=None if method == "GET" else own(port), body=body)
    return resp.status, json.loads(data or b"{}")


def chat_events(port, body):
    resp, data = request(port, "POST", "/api/chat", origin=own(port), body=body)
    return resp.status, [json.loads(l) for l in data.decode().splitlines() if l] if resp.status == 200 else data


def test_roles_api_create_edit_copy_delete(hub):  # G-AGENT-2, no coding needed
    port, _, rs = hub
    assert call(port, "GET", "/api/roles")[1]["roles"] == []
    st, data = call(port, "POST", "/api/roles", RESEARCHER)
    assert st == 200 and data["role"]["id"] == "researcher"
    assert (rs.folder / "researcher.md").is_file()
    listing = call(port, "GET", "/api/roles")[1]
    assert [r["name"] for r in listing["roles"]] == ["Researcher"]
    assert "Bash" not in listing["tools"] and listing["tools"]["Write"]["kind"] == "own-folder"
    st, data = call(port, "PUT", "/api/roles/researcher", {**RESEARCHER, "tools": ["WebSearch"]})
    assert st == 200 and rs.get("researcher").tools == ("WebSearch",)
    st, data = call(port, "POST", "/api/roles/researcher/copy")
    assert st == 200 and data["role"]["id"] == "researcher-copy"
    assert call(port, "DELETE", "/api/roles/researcher-copy")[0] == 200
    assert call(port, "DELETE", "/api/roles/researcher-copy")[0] == 404
    assert call(port, "PUT", "/api/roles/nope", RESEARCHER)[0] == 404
    assert call(port, "POST", "/api/roles", {**RESEARCHER, "tools": ["Bash"]})[0] == 400


def test_roles_api_needs_origin(hub):  # N-SEC-4 covers the new endpoints too
    port = hub[0]
    resp, _ = request(port, "POST", "/api/roles", body=RESEARCHER)
    assert resp.status == 403
    resp, _ = request(port, "DELETE", "/api/roles/x", origin="https://evil.example")
    assert resp.status == 403


def test_task_to_a_role_from_chat(hub):  # M5 check 1 (stand-in Claude), G-AGENT-3
    port, hist, rs = hub
    rid = call(port, "POST", "/api/roles", {**RESEARCHER, "model": ""})[1]["role"]["id"]
    st, evs = chat_events(port, {"text": "Find X", "chat_id": None, "role": rid})
    assert st == 200
    chat = next(e for e in evs if e["kind"] == "chat")
    assert chat["role"] == rid
    assert "".join(e["text"] for e in evs if e["kind"] == "text") == "result for: Find X"
    s = FakeSession.made[-1]
    assert s.kwargs["role"].id == rid and "personality" not in s.kwargs  # runs as the role
    assert s.options.tools == ["WebSearch", "WebFetch"] and "Bash" in s.options.disallowed_tools
    assert hist.get_chat(chat["id"])["role"] == rid
    assert [m["text"] for m in hist.messages(chat["id"])] == ["Find X", "result for: Find X"]
    # Follow-up in the same chat keeps the role, even if the window sends another one.
    st, evs = chat_events(port, {"text": "More", "chat_id": chat["id"], "role": "ignored"})
    assert st == 200 and hist.get_chat(chat["id"])["role"] == rid


def test_edited_role_applies_from_next_message(hub):
    port, hist, rs = hub
    rid = call(port, "POST", "/api/roles", RESEARCHER)[1]["role"]["id"]
    chat_id = next(e["id"] for e in chat_events(port, {"text": "a", "role": rid})[1] if e["kind"] == "chat")
    call(port, "PUT", f"/api/roles/{rid}", {**RESEARCHER, "tools": ["WebSearch"]})
    chat_events(port, {"text": "b", "chat_id": chat_id})
    assert FakeSession.made[-1].options.tools == ["WebSearch"]


def test_unknown_or_broken_role_is_refused(hub):
    port, hist, rs = hub
    assert chat_events(port, {"text": "x", "role": "nope"})[0] == 404
    assert chat_events(port, {"text": "x", "role": 5})[0] == 404
    rs.folder.mkdir(parents=True, exist_ok=True)
    (rs.folder / "bad.md").write_text("---\nname: Bad\ntools: Bash\n---\nx", encoding="utf-8")
    assert chat_events(port, {"text": "x", "role": "bad"})[0] == 400
    assert hist.list_chats() == []


def test_deleted_role_chat_cannot_continue(hub):
    port, hist, rs = hub
    rid = call(port, "POST", "/api/roles", RESEARCHER)[1]["role"]["id"]
    chat_id = next(e["id"] for e in chat_events(port, {"text": "a", "role": rid})[1] if e["kind"] == "chat")
    call(port, "POST", "/api/new")  # close its process, like switching away
    call(port, "DELETE", f"/api/roles/{rid}")
    st, evs = chat_events(port, {"text": "b", "chat_id": chat_id})
    assert [e["code"] for e in evs if e["kind"] == "error"] == ["role_missing"]
    assert [m["text"] for m in hist.messages(chat_id)] == ["a", "result for: a"]  # kept, readable, not doubled


def test_export_names_the_role(hub, tmp_path, monkeypatch):
    import hub.chat.history as history
    port, hist, rs = hub
    monkeypatch.setattr(history, "EXPORT_DIR", tmp_path / "exports")
    rid = call(port, "POST", "/api/roles", RESEARCHER)[1]["role"]["id"]
    chat_id = next(e["id"] for e in chat_events(port, {"text": "a", "role": rid})[1] if e["kind"] == "chat")
    path = call(port, "POST", f"/api/chats/{chat_id}/export")[1]["path"]
    assert "## Researcher (" in Path(path).read_text(encoding="utf-8")
