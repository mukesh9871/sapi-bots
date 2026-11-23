"""Milestone 6 offline checks: approvals, activity log, caps, budgets, pause. No Claude calls.

- M6 check 1: a test acting tool is held under Ask, runs under Auto, is blocked under Never (G-APPR-1..3)
- M6 check 2: every acting call is logged, and a daily cap stops further Auto runs (G-LOG-1, G-APPR-4)
- M6 check 3: a task stops at its budget, and the pause switch halts all automation (G-BUDGET-1, G-BUDGET-2)
- N-REL-3: nothing runs twice after a crash or a double click
"""

import asyncio
import json
import sqlite3
import uuid

import pytest

from helpers.hubhttp import free_port, own, request, start_server
from hub import config
from hub.chat import claude_session
from hub.chat.claude_session import build_options
from hub.chat.history import HistoryStore
from hub.connectors import demo
from hub.engine import roles
from hub.engine.roles import Role, RoleStore
from hub.gate.gate import Gate
from hub.gate.store import GateStore


@pytest.fixture
def outbox(tmp_path, monkeypatch):
    path = tmp_path / "outbox" / "notes.md"
    monkeypatch.setattr(demo, "OUTBOX", path)
    return path


@pytest.fixture
def gate(tmp_path, outbox):
    state = {"paused": False}
    g = Gate(GateStore(tmp_path / "hub.db"), paused=lambda: state["paused"])
    g.pause_state = state
    return g


def mode(g, m, cap=5):
    g.store.set_rule("local", "demo", "post_note", m, cap)


def post(g, text="hello", agent="Poster"):
    return g.call("demo.post_note", {"text": text}, agent)


def log(g):
    return list(reversed(g.store.activity()))  # oldest first


# ---- M6 check 1: Ask holds, Auto runs, Never blocks ---------------------------------------

def test_new_rule_is_ask_and_holds(gate, outbox):  # G-APPR-3, G-APPR-2
    r = post(gate)
    assert r.decision == "held" and not r.is_error and "item" in r.text
    assert gate.store.rule("local", "demo", "post_note")["mode"] == "ask"
    assert not outbox.exists()  # nothing happened yet
    item = gate.store.approvals()[0]
    assert item["payload"] == {"title": "", "text": "hello"} and item["agent"] == "Poster"


def test_auto_runs(gate, outbox):
    mode(gate, "auto")
    r = post(gate, "auto note")
    assert r.decision == "auto" and not r.is_error
    assert "auto note" in outbox.read_text(encoding="utf-8")


def test_never_blocks(gate, outbox):
    mode(gate, "never")
    r = post(gate)
    assert r.decision == "blocked" and r.is_error
    assert not outbox.exists() and gate.store.approvals() == []


def test_approve_edit_and_reject(gate, outbox):  # G-APPR-2
    post(gate, "first")
    post(gate, "second")
    a, b = gate.store.approvals()
    out = gate.approve(a["id"], {"title": "", "text": "first, edited"})
    assert out["ok"] and out["edited"]
    assert "first, edited" in outbox.read_text(encoding="utf-8")
    assert gate.store.approval(a["id"])["status"] == "done"
    gate.reject(b["id"])
    assert "second" not in outbox.read_text(encoding="utf-8")
    assert gate.store.approval(b["id"])["status"] == "rejected"


def test_bad_input_is_refused_not_held(gate, outbox):
    for bad in [{"text": ""}, {"text": 5}, {"text": "x", "extra": "y"}, "text", {"text": "x" * 2001}]:
        r = gate.call("demo.post_note", bad, "Poster")
        assert r.is_error and r.decision == "blocked", bad
    assert gate.store.approvals() == [] and gate.call("demo.nope", {}, "P").is_error
    # G-LOG-1 (review M6): refused calls are in the log too, with the reason
    assert [e["decision"] for e in log(gate)] == ["blocked"] * 6
    assert "bad input" in log(gate)[0]["detail"] and log(gate)[-1]["detail"] == "unknown action"


def test_repeats_never_run_or_queue_twice(gate, outbox):  # N-REL-3, review M6 round 2
    first = post(gate, "same")
    again = post(gate, "same")
    assert again.item_id == first.item_id and len(gate.store.approvals()) == 1  # Ask: not queued twice
    gate.reject(first.item_id)
    mode(gate, "auto")
    assert post(gate, "once").decision == "auto"
    r = post(gate, "once")  # the agent retries an action that already happened
    assert r.decision == "held" and "repeat" in gate.store.approvals()[-1]["reason"]
    assert outbox.read_text(encoding="utf-8").count("once") == 1


def test_repeat_of_a_waiting_item_is_logged(gate):  # review M6 round 3, G-LOG-1
    post(gate, "dup")
    post(gate, "dup")
    assert [e["decision"] for e in log(gate)] == ["held", "held"]
    assert "repeat of inbox item" in log(gate)[1]["detail"]


def test_repeats_are_flagged_under_ask_and_pause(gate, outbox):  # review M6 round 3
    mode(gate, "auto")
    post(gate, "ran once")
    mode(gate, "ask")
    post(gate, "ran once")
    assert "repeat" in gate.store.approvals()[-1]["reason"]
    mode(gate, "auto")
    post(gate, "ran twice?")
    gate.pause_state["paused"] = True
    post(gate, "ran twice?")
    reason = gate.store.approvals()[-1]["reason"]
    assert "automation paused" in reason and "repeat" in reason


def test_rejected_content_never_runs_automatically(gate, outbox):  # review M6 round 3
    gate.reject(post(gate, "no thanks").item_id)
    mode(gate, "auto")
    r = post(gate, "no thanks")
    assert r.decision == "held" and "rejected this exact content" in gate.store.approvals()[-1]["reason"]
    assert not outbox.exists()


def test_original_text_of_an_edited_approval_is_never_posted_too(gate, outbox):  # review M6 round 4
    mode(gate, "auto")
    gate.pause_state["paused"] = True
    item = post(gate, "teh typo").item_id
    gate.approve(item, {"title": "", "text": "the typo fixed"})
    gate.pause_state["paused"] = False
    r = post(gate, "teh typo")  # the agent sends its original again
    assert r.decision == "held" and "edited version" in gate.store.approvals()[-1]["reason"]
    assert "teh typo" not in outbox.read_text(encoding="utf-8")


def test_crash_during_an_edited_approval_keeps_the_edit(gate, outbox, monkeypatch):  # review M6 round 5
    item = post(gate, "draft A").item_id
    def crash(payload):
        raise KeyboardInterrupt  # the app stops mid-run
    real_lookup = gate.lookup
    from dataclasses import replace
    crashing = replace(demo.ACTIONS[0], run=crash)
    gate.lookup = lambda a: crashing
    with pytest.raises(KeyboardInterrupt):
        gate.approve(item, {"title": "", "text": "final B"})
    gate.lookup = real_lookup
    assert gate.recover() == 1
    mode(gate, "auto")
    r = post(gate, "draft A")  # the agent's original again
    assert r.decision == "held" and "edited version" in gate.store.approvals()[-1]["reason"]
    assert not outbox.exists()


def test_recover_sees_a_run_that_finished(gate, outbox):  # review M6 round 5
    item = post(gate, "done but not recorded").item_id
    gate.store.claim(item, "running")
    call_id = "c1"
    gate.store.log(call_id, "P", "local", "demo", "demo.post_note", {"text": "x"}, "approved", "started",
                   f"inbox item {item}")
    gate.store.log(call_id, "P", "local", "demo", "demo.post_note", {"text": "x"}, "approved", "done", "ok")
    gate.recover()
    assert gate.store.approval(item)["status"] == "done"


@pytest.mark.parametrize("content, paused, notice", [
    ('{"automation_paused": true,', True, True),  # a typo from a hand edit (review M6 round 6)
    ('{"automation_paused": "true"}', True, True),  # not true or false
    ('{"automation_paused": true}', True, False),
    ('{"automation_paused": false}', False, False),
    ('{"theme": "dark"}', False, False),
    ("busy", True, True),  # cannot be read at all (review M6 round 5)
])
def test_start_up_pause_fails_closed(work, outbox, monkeypatch, content, paused, notice):
    path = work / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    path.write_text("{}" if content == "busy" else content, encoding="utf-8")
    if content == "busy":
        real = type(path).read_text
        monkeypatch.setattr(type(path), "read_text", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("busy"))
                            if self == path else real(self, *a, **k))
    from hub.server import create_app
    app = create_app("t", free_port(), check_login_on_start=False, history=HistoryStore(work / "c.db"),
                     roles=RoleStore(work / "roles"), gate_store=GateStore(work / "hub.db"))
    assert app.state.hub.paused is paused
    assert any("starts paused" in n for n in app.state.hub.notices) is notice


def test_saving_settings_keeps_the_real_pause_state(work, outbox, monkeypatch):  # review M6 round 7
    path = work / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    path.write_text('{"automation_paused": true,', encoding="utf-8")  # broken: the app starts paused
    port = free_port()
    srv, t = start_server(port, history=HistoryStore(work / "c.db"), roles=RoleStore(work / "roles"),
                          gate_store=GateStore(work / "hub.db"))
    try:
        assert call(port, "POST", "/api/settings", {"theme": "dark"})[0] == 200
        assert json.loads(path.read_text(encoding="utf-8"))["automation_paused"] is True
        assert call(port, "GET", "/api/gate/summary")[1]["paused"] is True
    finally:
        srv.should_exit = True
        t.join(5)


def test_two_approvals_of_the_same_content_at_once_ask_first(gate, outbox, monkeypatch):  # review M6 round 7
    import threading
    import time as _time
    a, b = post(gate, "same words", "A").item_id, post(gate, "other", "B").item_id
    real = gate.store.recent_run
    monkeypatch.setattr(gate.store, "recent_run", lambda *x: (_time.sleep(0.2), real(*x))[1])  # widen the race
    results = []
    def approve(item, payload):
        try:
            results.append(gate.approve(item, payload)["ok"])
        except Exception as exc:
            results.append(type(exc).__name__)
    ts = [threading.Thread(target=approve, args=(a, None)),
          threading.Thread(target=approve, args=(b, {"title": "", "text": "same words"}))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(map(str, results)) == ["RepeatRun", "True"]
    assert outbox.read_text(encoding="utf-8").count("same words") == 1


def test_approving_content_that_already_ran_asks_first(gate, outbox):  # review M6 round 6
    a = post(gate, "draft one").item_id
    b = post(gate, "final").item_id
    gate.approve(a, {"title": "", "text": "final"})
    from hub.gate.gate import RepeatRun
    with pytest.raises(RepeatRun):
        gate.approve(b)
    assert gate.store.approval(b)["status"] == "pending"
    assert gate.approve(b, confirm_repeat=True)["ok"]  # I confirmed: posted again
    assert outbox.read_text(encoding="utf-8").count("final") == 2


def test_old_inbox_table_gets_the_proposed_column(tmp_path):  # a hub.db from before this fix
    db = tmp_path / "hub.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE approvals (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL, "
                  "agent TEXT NOT NULL, pack TEXT NOT NULL, platform TEXT NOT NULL, action_id TEXT NOT NULL, "
                  "payload TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', "
                  "result TEXT NOT NULL DEFAULT '', decided_at REAL)")
    s = GateStore(db)
    s.hold("A", "local", "demo", "demo.post_note", {"text": "x"}, "")
    assert s.approvals()[0]["proposed"] == '{"text": "x"}'


def test_invalid_input_reaches_the_gate_and_is_logged(work, gate):  # review M6 round 4
    schema = demo.ACTIONS[0].schema()
    assert "required" not in schema and all("type" not in p for p in schema["properties"].values())
    tool = claude_session._gated_tool(demo.ACTIONS[0], gate, "Poster")
    for bad in [{"title": "x"}, {"text": 5}]:
        assert asyncio.run(tool.handler(bad))["is_error"]
    assert [e["decision"] for e in log(gate)] == ["blocked", "blocked"]


def test_identical_calls_at_once_run_once(gate, outbox, monkeypatch):  # review M6 round 3
    import threading
    import time as _time
    mode(gate, "auto")
    real = gate.store.earlier_same
    monkeypatch.setattr(gate.store, "earlier_same", lambda *a: (_time.sleep(0.2), real(*a))[1])  # widen the race
    results = []
    threads = [threading.Thread(target=lambda: results.append(post(gate, "race").decision)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["auto", "held", "held"]
    assert outbox.read_text(encoding="utf-8").count("race") == 1


def test_failed_auto_run_is_not_retried_automatically(gate, monkeypatch):  # review M6 round 2
    mode(gate, "auto")
    monkeypatch.setattr(demo, "OUTBOX", gate.store.path.parent)  # writing fails, maybe after acting
    r = post(gate, "flaky")
    assert r.is_error and "do not retry" in r.text
    assert post(gate, "flaky").decision == "held"  # the retry waits for the user


def test_approval_respects_a_rule_now_never(gate, outbox):  # review M6 round 2
    item = post(gate, "later").item_id
    mode(gate, "never")
    with pytest.raises(ValueError, match="Never"):
        gate.approve(item)
    assert gate.store.approval(item)["status"] == "pending" and not outbox.exists()


def test_line_endings_alone_are_not_an_edit(gate, outbox):  # review M6 round 2
    item = post(gate, "a\r\nb").item_id
    out = gate.approve(item, {"title": "", "text": "a\nb"})
    assert not out["edited"] and gate.store.approval(item)["payload"]["text"] == "a\r\nb"


def test_odd_digits_in_a_role_file_do_not_break_roles(tmp_path):  # review M6 round 2
    rs = RoleStore(tmp_path / "roles")
    rs.folder.mkdir(parents=True)
    (rs.folder / "odd.md").write_text("---\nname: Odd\nmax_turns: \u00b2\n---\nx", encoding="utf-8")
    assert rs.get("odd").problem and [r.id for r in rs.list()] == ["odd"]


def test_approved_run_names_its_inbox_item(gate, outbox):  # review M6
    post(gate, "x")
    item = gate.store.approvals()[0]["id"]
    gate.approve(item, {"title": "", "text": "y"})
    started = [e for e in log(gate) if e["decision"] == "approved" and e["outcome"] == "started"][0]
    assert started["detail"] == f"inbox item {item}, edited by the user"


def test_cap_is_shared_by_actions_under_one_rule(gate, outbox):  # review M6: counted like the rule
    from dataclasses import replace
    twin = replace(demo.ACTIONS[0], connector="demo2")  # same platform and action name
    lookup = {"demo.post_note": demo.ACTIONS[0], "demo2.post_note": twin}.get
    g = Gate(gate.store, paused=lambda: False, lookup=lookup)
    mode(g, "auto", cap=2)
    got = [g.call(a, {"text": f"t{i}"}, "P").decision
           for i, a in enumerate(["demo.post_note", "demo2.post_note", "demo2.post_note"])]
    assert got == ["auto", "auto", "held"]


def test_cap_holds_under_concurrent_calls(gate, outbox):  # review M6: count and start in one step
    import threading
    mode(gate, "auto", cap=3)
    results = []
    threads = [threading.Thread(target=lambda i=i: results.append(post(gate, f"c{i}").decision)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("auto") == 3 and results.count("held") == 9


# ---- M6 check 2: log and daily cap --------------------------------------------------------

def test_every_acting_call_is_logged(gate, outbox):  # G-LOG-1
    post(gate, "held one")
    mode(gate, "auto")
    post(gate, "auto one")
    mode(gate, "never")
    post(gate, "blocked one")
    mode(gate, "ask")  # an item cannot be approved while its rule is Never
    gate.approve(gate.store.approvals()[0]["id"])
    rows = [(e["decision"], e["outcome"]) for e in log(gate)]
    assert rows == [("held", ""), ("auto", "started"), ("auto", "done"), ("blocked", ""),
                    ("approved", "started"), ("approved", "done")]
    e = log(gate)[2]
    assert e["agent"] == "Poster" and e["platform"] == "demo" and e["action_id"] == "demo.post_note"
    assert e["payload"]["text"] == "auto one" and e["at"] > 0  # time, agent, what, where, how


def test_daily_cap_holds_further_auto_runs(gate, outbox):  # G-APPR-4
    mode(gate, "auto", cap=2)
    assert [post(gate, f"n{i}").decision for i in range(4)] == ["auto", "auto", "held", "held"]
    assert {i["reason"] for i in gate.store.approvals()} == {"daily cap reached"}
    assert outbox.read_text(encoding="utf-8").count("## ") == 2


def test_log_is_append_only(gate):  # G-LOG-1, data storage table
    post(gate)
    with sqlite3.connect(gate.store.path) as db:
        for sql in ["UPDATE activity SET detail = 'x'", "DELETE FROM activity"]:
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                db.execute(sql)


def test_failed_action_is_logged(gate, monkeypatch):
    mode(gate, "auto")
    monkeypatch.setattr(demo, "OUTBOX", gate.store.path.parent)  # a folder: writing fails
    r = post(gate)
    assert r.is_error and "failed" in r.text
    assert [e["outcome"] for e in log(gate)] == ["started", "failed"]


def test_log_search_and_filters(gate):  # UI 12
    post(gate, "about pandas")
    post(gate, "about owls")
    assert len(gate.store.activity(q="pandas")) == 1
    assert len(gate.store.activity(platform="demo")) == 2 and gate.store.activity(platform="x") == []
    assert gate.store.activity(pack="other") == []
    from datetime import date
    assert len(gate.store.activity(day=date.today().isoformat())) == 2
    assert gate.store.activity(day="2000-01-01") == []
    assert len(gate.store.activity(q="50%_")) == 0  # wildcards are text
    with pytest.raises(ValueError):
        gate.store.activity(day="yesterday")


def test_early_dates_in_the_log_filter_are_a_plain_error(hub):  # review M6 round 2
    for day in ["1969-12-31", "0001-01-01"]:
        assert call(hub[0], "GET", f"/api/activity?day={day}")[0] == 400


@pytest.mark.parametrize("m, cap", [("sometimes", 5), ("auto", 0), ("auto", 1001), ("auto", True), ("auto", "5")])
def test_bad_rules_refused(gate, m, cap):
    with pytest.raises(ValueError):
        gate.store.set_rule("local", "demo", "post_note", m, cap)


# ---- N-REL-3: never twice -----------------------------------------------------------------

def test_double_approve_runs_once(gate, outbox):
    post(gate)
    item = gate.store.approvals()[0]["id"]
    gate.approve(item)
    with pytest.raises(ValueError, match="already decided"):
        gate.approve(item)
    with pytest.raises(ValueError, match="already decided"):
        gate.reject(item)
    assert outbox.read_text(encoding="utf-8").count("## ") == 1


def test_crash_mid_run_is_marked_unknown_and_not_rerun(gate, outbox):
    # A call that logged "started" but never finished, as after a crash.
    gate.store.log(str(uuid.uuid4()), "Poster", "local", "demo", "demo.post_note", {"text": "x"}, "auto", "started")
    post(gate)
    stuck = gate.store.approvals()[0]["id"]
    gate.store.claim(stuck, "running")
    assert gate.recover() == 1
    assert log(gate)[-1]["outcome"] == "unknown"
    assert gate.store.approval(stuck)["status"] == "failed"
    assert gate.recover() == 0  # marked once
    assert not outbox.exists()  # nothing re-run


# ---- M6 check 3 (part): pause holds every action ------------------------------------------

def test_pause_holds_even_auto(gate, outbox):  # G-BUDGET-2
    mode(gate, "auto")
    gate.pause_state["paused"] = True
    r = post(gate)
    assert r.decision == "held" and gate.store.approvals()[0]["reason"] == "automation paused"
    assert not outbox.exists()
    mode(gate, "never")
    assert post(gate).decision == "blocked"  # Never still wins


# ---- roles with acting tools, and their budget ---------------------------------------------

def poster(tools=("demo.post_note",), turns=30):
    return Role("poster", "Poster", "haiku", tuple(tools), "Post notes when asked.", max_turns=turns)


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr(claude_session, "CHAT_WORKDIR", tmp_path / "chat")
    return tmp_path


def test_role_gets_only_its_gated_actions(work, gate):  # G-TOOL-3, G-APPR-1
    opts = build_options(role=poster(("WebSearch", "demo.post_note"), turns=7), gate=gate)
    assert opts.tools == ["WebSearch"]  # built-ins only
    assert list(opts.mcp_servers) == ["hub"] and opts.strict_mcp_config
    assert "mcp__hub__demo_post_note" in opts.allowed_tools
    assert opts.max_turns == 7  # G-BUDGET-1
    hook = opts.hooks["PreToolUse"][0].hooks[0]
    deny = asyncio.run(hook({"tool_name": "mcp__hub__demo_other", "tool_input": {}}, None, None))
    assert deny["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert asyncio.run(hook({"tool_name": "mcp__hub__demo_post_note", "tool_input": {"text": "x"}}, None, None)) == {}
    assert build_options(role=poster(("WebSearch",)), gate=gate).mcp_servers == {}
    assert build_options(web_enabled=True).mcp_servers == {} and build_options().max_turns is None


def test_acting_tools_never_run_without_the_gate(work):
    with pytest.raises(ValueError, match="approval gate"):
        build_options(role=poster())


def test_gated_tool_goes_through_the_gate(work, gate, outbox):
    mode(gate, "auto")
    tool = claude_session._gated_tool(demo.ACTIONS[0], gate, "Poster")
    out = asyncio.run(tool.handler({"text": "via tool"}))
    assert not out["is_error"] and "via tool" in outbox.read_text(encoding="utf-8")
    mode(gate, "never")
    assert asyncio.run(tool.handler({"text": "no"}))["is_error"]


def test_role_budget_in_file(tmp_path, work):  # G-BUDGET-1
    rs = RoleStore(tmp_path / "roles")
    r = rs.save(None, "Poster", "haiku", ["demo.post_note"], "Post.", max_turns=12)
    assert rs.get(r.id).max_turns == 12 and "max_turns: 12" in (rs.folder / f"{r.id}.md").read_text(encoding="utf-8")
    for bad in [0, 201, "5", True]:
        with pytest.raises(roles.RoleError):
            rs.save(None, "X", "", [], "x", max_turns=bad)
    (rs.folder / "old.md").write_text("---\nname: Old\ntools: WebSearch\n---\nx", encoding="utf-8")
    assert rs.get("old").max_turns == 30  # a Milestone 5 file gets the default
    (rs.folder / "bad.md").write_text("---\nname: Bad\nmax_turns: lots\n---\nx", encoding="utf-8")
    assert rs.get("bad").problem


def test_catalog_lists_connector_actions():
    cat = roles.catalog()
    assert cat["demo.post_note"]["kind"] == "acting" and cat["Read"]["kind"] == "read-only"


# ---- server: inbox, log, rules and pause APIs ----------------------------------------------

class FakeSession:
    made: list = []
    script: list = []  # events to send, as (kind, text, data)

    def __init__(self, **kwargs):
        kwargs.pop("ctrl_c_shield", None)
        self.kwargs = kwargs
        FakeSession.made.append(self)
        self.session_id = str(uuid.uuid4())
        self.options = build_options(**kwargs)
        self.pending_personality = self.pending_replay = None
        self.stop_requested = False
        self.stopped = False
        self.last_rate_limit = None
        self.api_key_source = "none"

    async def connect(self): pass

    async def reconfigure(self, **kw): return False

    async def stop(self): self.stopped = True

    async def close(self): pass

    async def send(self, text):
        from hub.chat.claude_session import Event
        yield Event("init")
        for kind, t, data in FakeSession.script:
            if kind == "wait":
                for _ in range(100):
                    if self.stopped:
                        break
                    await asyncio.sleep(0.05)
                continue
            yield Event(kind, t, data)
        yield Event("done", data={"subtype": "error_max_turns" if "budget" in text else "success",
                                  "terminal_reason": "aborted_streaming" if self.stopped else "completed",
                                  "usage": {}, "is_error": "budget" in text, "num_turns": 3})


@pytest.fixture
def hub(work, outbox, monkeypatch):
    import hub.server
    monkeypatch.setattr(config, "CONFIG_PATH", work / "config.json")
    monkeypatch.setattr(hub.server, "ChatSession", FakeSession)
    monkeypatch.setattr(FakeSession, "made", [])
    monkeypatch.setattr(FakeSession, "script", [("text", "ok", {})])
    gs = GateStore(work / "hub.db")
    hist, rs = HistoryStore(work / "chats.db"), RoleStore(work / "roles")
    port = free_port()
    srv, t = start_server(port, history=hist, roles=rs, gate_store=gs)
    yield port, hist, rs, gs
    srv.should_exit = True
    t.join(5)


def call(port, method, path, body=None):
    resp, data = request(port, method, path, origin=None if method == "GET" else own(port), body=body)
    return resp.status, json.loads(data or b"{}")


def chat(port, body):
    resp, data = request(port, "POST", "/api/chat", origin=own(port), body=body)
    return resp.status, [json.loads(l) for l in data.decode().splitlines() if l] if resp.status == 200 else json.loads(data)


def test_rules_listed_ask_by_default_and_changed_from_settings(hub):  # G-APPR-3
    port = hub[0]
    rules = call(port, "GET", "/api/rules")[1]["rules"]
    assert rules == [{"pack": "local", "platform": "demo", "action": "post_note", "mode": "ask", "daily_cap": 5}]
    st, data = call(port, "PUT", "/api/rules", {"pack": "local", "platform": "demo", "action": "post_note",
                                                "mode": "auto", "daily_cap": 3})
    assert st == 200 and data["rule"]["mode"] == "auto"
    assert call(port, "PUT", "/api/rules", {"pack": "local", "platform": "demo", "action": "post_note",
                                            "mode": "yes", "daily_cap": 3})[0] == 400
    assert call(port, "PUT", "/api/rules", {"pack": "x", "platform": "demo", "action": "post_note",
                                            "mode": "auto", "daily_cap": 3})[0] == 404


def test_inbox_api_approve_edit_reject(hub, outbox):  # G-APPR-2
    port, _, _, gs = hub
    g = Gate(gs, paused=lambda: False)
    g.call("demo.post_note", {"text": "one"}, "Poster")
    g.call("demo.post_note", {"text": "two"}, "Poster")
    items = call(port, "GET", "/api/approvals")[1]["items"]
    assert [i["payload"]["text"] for i in items] == ["one", "two"]
    assert "one" in items[0]["preview"] and "text" in items[0]["fields"]
    assert call(port, "GET", "/api/gate/summary")[1] == {"pending": 2, "paused": False, "missed": 0, "notice": 0}
    st, out = call(port, "POST", f"/api/approvals/{items[0]['id']}/approve", {"payload": {"title": "T", "text": "one!"}})
    assert st == 200 and out["ok"] and out["edited"] and "one!" in outbox.read_text(encoding="utf-8")
    assert call(port, "POST", f"/api/approvals/{items[0]['id']}/approve", {})[0] == 409  # never twice
    assert call(port, "POST", f"/api/approvals/{items[1]['id']}/approve", {"payload": {"text": ""}})[0] == 400
    assert call(port, "POST", f"/api/approvals/{items[1]['id']}/reject")[0] == 200
    assert call(port, "POST", "/api/approvals/999/reject")[0] == 404
    entries = call(port, "GET", "/api/activity?q=one")[1]["entries"]
    assert {e["decision"] for e in entries} >= {"held", "approved"}
    assert call(port, "GET", "/api/activity?day=nope")[0] == 400


def test_gate_endpoints_need_origin(hub):  # N-SEC-4
    port = hub[0]
    for method, path in [("POST", "/api/approvals/1/approve"), ("POST", "/api/approvals/1/reject"), ("PUT", "/api/rules")]:
        resp, _ = request(port, method, path, body={})
        assert resp.status == 403, path


def test_role_chat_gets_the_gate_and_reports_budget(hub):  # G-BUDGET-1
    port, _, rs, _ = hub
    rid = rs.save(None, "Poster", "haiku", ["demo.post_note"], "Post notes.", max_turns=3).id
    st, evs = chat(port, {"text": "use the whole budget", "role": rid})
    assert st == 200
    s = FakeSession.made[-1]
    assert s.kwargs["gate"] is not None and s.options.max_turns == 3
    done = next(e for e in evs if e["kind"] == "done")
    assert done["subtype"] == "error_max_turns" and done["num_turns"] == 3  # the window says so
    chat_id = next(e["id"] for e in evs if e["kind"] == "chat")
    assert hub[1].messages(chat_id)[-1]["status"] == "budget"  # still said after reopening (review M6 round 3)
    FakeSession.script = [("tool", "Write", {})]  # stopped before writing any text (review M6 round 4)
    st, evs = chat(port, {"text": "budget again", "chat_id": chat_id})
    last = hub[1].messages(chat_id)[-1]
    assert (last["role"], last["text"], last["status"]) == ("bot", "", "budget")


def test_pause_halts_roles_but_not_plain_chat(hub):  # M6 check 3, G-BUDGET-2
    port, hist, rs, _ = hub
    rid = rs.save(None, "Poster", "haiku", ["WebSearch"], "Post notes.").id
    assert call(port, "POST", "/api/settings", {"automation_paused": True})[0] == 200
    assert call(port, "GET", "/api/gate/summary")[1]["paused"] is True
    st, err = chat(port, {"text": "task", "role": rid})
    assert st == 409 and "paused" in err["error"]
    st, evs = chat(port, {"text": "plain hello"})  # plain chat stays available
    assert st == 200 and any(e["kind"] == "text" for e in evs)
    call(port, "POST", "/api/settings", {"automation_paused": False})
    chat_id = next(e["id"] for e in chat(port, {"text": "task", "role": rid})[1] if e["kind"] == "chat")
    call(port, "POST", "/api/settings", {"automation_paused": True})
    st, evs = chat(port, {"text": "more", "chat_id": chat_id})  # an existing role chat too
    assert [e["code"] for e in evs if e["kind"] == "error"] == ["automation_paused"]


def test_pausing_stops_a_running_role_task(hub):  # G-BUDGET-2
    import threading
    port, _, rs, _ = hub
    rid = rs.save(None, "Poster", "haiku", ["WebSearch"], "Work slowly.").id
    FakeSession.script = [("text", "working", {}), ("wait", "", {})]
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", chat(port, {"text": "long task", "role": rid})))
    t.start()
    for _ in range(100):
        if FakeSession.made:
            break
        asyncio.run(asyncio.sleep(0.05))
    asyncio.run(asyncio.sleep(0.3))
    call(port, "POST", "/api/settings", {"automation_paused": True})
    t.join(10)
    assert FakeSession.made[-1].stopped
    done = next(e for e in out["r"][1] if e["kind"] == "done")
    assert done["terminal_reason"].startswith("aborted")


def test_pause_survives_an_unreadable_config(hub, monkeypatch):  # review M6: fail closed, never open
    port, _, rs, gs = hub
    call(port, "POST", "/api/settings", {"automation_paused": True})
    monkeypatch.setattr(config, "load", lambda *a, **k: dict(config.DEFAULTS))  # as if the file were busy
    assert call(port, "GET", "/api/gate/summary")[1]["paused"] is True
    rid = rs.save(None, "Poster", "haiku", ["WebSearch"], "x").id
    assert chat(port, {"text": "t", "role": rid})[0] == 409


def test_busy_settings_file_gives_a_plain_error_and_pause_still_holds(hub, monkeypatch):  # review M6 round 2
    port = hub[0]
    def busy(*a, **k):
        raise PermissionError("busy")
    monkeypatch.setattr(config, "save", busy)
    st, err = call(port, "POST", "/api/settings", {"automation_paused": True})
    assert st == 503 and "busy" in err["error"] and "paused for now" in err["error"]
    assert call(port, "GET", "/api/gate/summary")[1]["paused"] is True


def test_save_never_writes_defaults_over_a_busy_file(tmp_path, monkeypatch):  # review M6
    path = tmp_path / "config.json"
    config.save({"bot_name": "Mine"}, path)
    real = type(path).read_text
    monkeypatch.setattr(type(path), "read_text", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("busy"))
                        if self == path else real(self, *a, **k))
    with pytest.raises(PermissionError):
        config.save({"theme": "dark"}, path)
    monkeypatch.undo()
    assert config.load(path)["bot_name"] == "Mine"
    assert config.load(path)["theme"] == "system"  # the failed save changed nothing


def test_recover_on_start_adds_a_notice(work, outbox, monkeypatch):  # N-REL-3
    monkeypatch.setattr(config, "CONFIG_PATH", work / "config.json")  # never read the real one
    gs = GateStore(work / "hub.db")
    gs.log(str(uuid.uuid4()), "Poster", "local", "demo", "demo.post_note", {"text": "x"}, "auto", "started")
    from hub.server import create_app
    app = create_app("t", free_port(), check_login_on_start=False, history=HistoryStore(work / "c.db"),
                     roles=RoleStore(work / "roles"), gate_store=gs)
    assert any("interrupted" in n for n in app.state.hub.notices)
