"""Milestone 9 offline checks: the routine automation pack (P-ROUT-1, P-ROUT-2, P-ROUT-3). No Claude calls.

- M9 check: a routine described in plain words runs on schedule for a week. Offline this is
  simulated: a routine is drafted from plain words and saved through the API, then the scheduler
  runs it on 7 days with a fake clock.
  The real week needs the real app (see the hand-over).
"""

import asyncio
import json
import shutil
import time
import uuid
from types import SimpleNamespace

import pytest

from helpers.hubhttp import free_port, own, request, start_server
from hub import config, connectors
from hub.chat import claude_session
from hub.chat.claude_session import Event, build_options
from hub.chat.history import HistoryStore
from hub.engine import packs as packs_mod
from hub.engine import roles
from hub.engine.packs import PACKS_DIR, PackError, PackStore, install, read_pack
from hub.engine.roles import RoleStore
from hub.gate.gate import Gate
from hub.gate.store import GateStore
from hub.scheduler.runner import Scheduler
from hub.scheduler.schedules import ScheduleStore, next_run


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")
    root = tmp_path / "packs"
    shutil.copytree(PACKS_DIR / "routines", root / "routines")
    ps = PackStore(root, tmp_path / "hub.db")
    monkeypatch.setattr(connectors, "pack_dirs", ps.enabled_dirs)
    monkeypatch.setattr(packs_mod, "active", ps)
    gate = Gate(GateStore(tmp_path / "hub.db"), paused=lambda: False)
    ss = ScheduleStore(tmp_path / "hub.db")
    rs = RoleStore(tmp_path / "roles")
    rs.pack_dirs = ps.enabled_dirs
    return tmp_path, ps, gate, ss, rs


def test_routines_pack_has_the_starter_routines():  # P-ROUT-2
    pack = read_pack("routines", PACKS_DIR / "routines")
    assert pack.problem == ""
    assert pack.roles == ["briefer", "reminder", "reporter", "tidier"]
    assert {s["name"] for s in pack.schedules} == {"Morning briefing", "Weekly summary", "Stretch reminder",
                                                  "Tidy my folder"}
    assert pack.settings == [{"key": "tidy_folder", "type": "folder",
                              "label": "Folder the tidier may sort (for example your Downloads folder)",
                              "required": False, "chat": False}]  # G-PACK-4: a folder is never set from chat


def test_install_gives_roles_rules_and_switched_off_schedules(env):
    tmp, ps, gate, ss, rs = env
    install(ps, gate, ss, "routines")
    assert all(rs.get(f"routines.{r}").problem == "" for r in ("briefer", "reminder", "reporter", "tidier"))
    assert gate.store.rule("routines", "files", "tidy_move")["mode"] == "ask"
    assert all(not s["enabled"] for s in ss.list())
    assert {s["name"]: s["notify"] for s in ss.list()}["Stretch reminder"] is True


def test_pack_settings(env):  # Milestone 9: settings I fill in on the Packs page
    tmp, ps, gate, ss, rs = env
    with pytest.raises(PackError, match="not installed"):
        ps.set_settings("routines", {"tidy_folder": str(tmp)})
    install(ps, gate, ss, "routines")
    for bad in ({"tidy_folder": "relative\\path"}, {"tidy_folder": str(tmp / "nope")}, {"other": "x"},
                {"tidy_folder": 5}, "x", {"tidy_folder": "\\\\server\\share"}):
        with pytest.raises(PackError):
            ps.set_settings("routines", bad)
    assert ps.set_settings("routines", {"tidy_folder": str(tmp)}) == {"tidy_folder": str(tmp.resolve())}
    assert packs_mod.setting("routines", "tidy_folder") == str(tmp.resolve())
    assert packs_mod.setting("routines", "missing") == ""


@pytest.fixture
def tidy(env):
    tmp, ps, gate, ss, rs = env
    install(ps, gate, ss, "routines")
    folder = tmp / "Downloads"
    (folder / "Images").mkdir(parents=True)
    for n in ("report.pdf", "photo.jpg", "setup.exe"):
        (folder / n).write_text("x")
    (folder / "Images" / "old.png").write_text("x")
    return env, folder


def test_tidy_tools_stay_in_the_folder(tidy):
    (tmp, ps, gate, ss, rs), folder = tidy
    lst, move = connectors.get("routine_tools.tidy_list"), connectors.get("routine_tools.tidy_move")
    with pytest.raises(Exception, match="no folder is set"):
        lst.run({})
    ps.set_settings("routines", {"tidy_folder": str(folder)})
    text = lst.run({})
    assert "report.pdf" in text and "old.png" not in text and "Images" in text and "not instructions" in text
    assert move.run({"folder": str(folder), "file": "report.pdf", "subfolder": "Documents"}) == "Moved report.pdf to Documents"
    assert (folder / "Documents" / "report.pdf").is_file() and not (folder / "report.pdf").exists()
    for bad in ({"folder": str(folder), "file": "..\\x", "subfolder": "A"}, {"folder": str(folder), "file": "photo.jpg", "subfolder": "..\\.."},
                {"folder": str(folder), "file": "photo.jpg", "subfolder": "C:\\Temp"}, {"folder": str(folder), "file": "Images", "subfolder": "A"},
                {"folder": str(folder), "file": "nothere.txt", "subfolder": "A"}, {"folder": str(folder), "file": "old.png", "subfolder": "A"}):
        with pytest.raises(Exception):
            move.run(bad)
    (folder / "Images" / "photo.jpg").write_text("other")
    with pytest.raises(Exception, match="already exists"):
        move.run({"folder": str(folder), "file": "photo.jpg", "subfolder": "Images"})  # never over an existing file
    assert (folder / "photo.jpg").read_text() == "x"


def test_approved_move_runs_only_in_the_folder_it_showed(tidy):  # review M9-3 finding 1 (G-APPR-2)
    (tmp, ps, gate, ss, rs), folder = tidy
    other = tmp / "Desktop"
    other.mkdir()
    (other / "photo.jpg").write_text("desktop")
    ps.set_settings("routines", {"tidy_folder": str(folder)})
    held = gate.call("routine_tools.tidy_move", {"folder": str(folder), "file": "photo.jpg", "subfolder": "Images"},
                     "Tidier", "routines")
    item = gate.store.approvals()[0]
    assert str(folder) in gate.lookup(item["action_id"]).preview(item["payload"])  # the approval shows where
    ps.set_settings("routines", {"tidy_folder": str(other)})  # the setting changes before I approve
    result = gate.approve(item["id"])
    assert (other / "photo.jpg").read_text() == "desktop" and (folder / "photo.jpg").exists()  # nothing moved
    assert "nothing was moved" in str(result)


def test_tidy_never_follows_a_junction_out(tidy):  # review M9-1 High
    import subprocess
    (tmp, ps, gate, ss, rs), folder = tidy
    outside = tmp / "outside"
    outside.mkdir()
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(folder / "Documents"), str(outside)],
                          capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip("cannot make a junction here")
    ps.set_settings("routines", {"tidy_folder": str(folder)})
    lst, move = connectors.get("routine_tools.tidy_list"), connectors.get("routine_tools.tidy_move")
    assert "Documents" not in lst.run({}).split("Subfolders:")[1]  # never offered as a subfolder
    with pytest.raises(Exception, match="not a plain subfolder"):
        move.run({"folder": str(folder), "file": "report.pdf", "subfolder": "Documents"})
    assert (folder / "report.pdf").exists() and list(outside.iterdir()) == []


def test_folder_setting_refuses_network_paths_in_any_form(env):  # review M9-1 Low
    tmp, ps, gate, ss, rs = env
    install(ps, gate, ss, "routines")
    for bad in ("//host/share", "\\\\host\\share"):
        with pytest.raises(PackError, match="full path"):
            ps.set_settings("routines", {"tidy_folder": bad})


def test_notices_follow_the_order_runs_finish(tmp_path):  # review M9-1 Medium
    store = ScheduleStore(tmp_path / "hub.db")
    a = store.add("Slow briefing", "role", "r", "x", {"type": "daily", "time": "07:30"}, notify=True)
    b = store.add("Quick reminder", "role", "r", "x", {"type": "daily", "time": "07:31"}, notify=True)
    ra, rb = store.start_run(a["id"], 0), store.start_run(b["id"], 0)
    store.end_run(rb, "done", "drink water")
    seen = store.last_notice()
    assert [n["name"] for n in store.notices(0)] == ["Quick reminder"]
    store.end_run(ra, "done", "news")  # the earlier run finishes later
    assert store.last_notice() > seen
    assert [n["name"] for n in store.notices(seen)] == ["Slow briefing"]


def test_notice_ids_are_never_reused_and_ticking_later_shows_no_old_result(tmp_path):  # review M9-2
    store = ScheduleStore(tmp_path / "hub.db")
    a = store.add("A", "role", "r", "x", {"type": "daily", "time": "07:30"}, notify=True)
    b = store.add("B", "role", "r", "x", {"type": "daily", "time": "07:31"}, notify=True)
    store.end_run(store.start_run(a["id"], 0), "done", "a")
    cursor = store.last_notice()
    store.delete(a["id"])
    store.end_run(store.start_run(b["id"], 0), "done", "b")
    assert store.last_notice() > cursor and [n["name"] for n in store.notices(cursor)] == ["B"]
    c = store.add("C", "role", "r", "x", {"type": "daily", "time": "07:32"})  # no "tell me"
    store.end_run(store.start_run(c["id"], 0), "done", "old result")
    cursor = store.last_notice()
    store.update(c["id"], "C", "role", "r", "x", {"type": "daily", "time": "07:32"}, notify=True)
    assert store.last_notice() == cursor and store.notices(cursor) == []


def test_tidy_move_goes_through_approvals(tidy):  # hard rule: acting tools go through the gate
    (tmp, ps, gate, ss, rs), folder = tidy
    ps.set_settings("routines", {"tidy_folder": str(folder)})
    move = connectors.get("routine_tools.tidy_move")
    tool = claude_session._gated_tool(move, gate, "Tidier", "routines")
    out = asyncio.run(tool.handler({"folder": str(folder), "file": "photo.jpg", "subfolder": "Images"}))
    assert "approval" in out["content"][0]["text"].lower() and (folder / "photo.jpg").exists()  # Ask: held
    gate.store.set_rule("routines", "files", "tidy_move", "auto", 100)
    asyncio.run(tool.handler({"folder": str(folder), "file": "setup.exe", "subfolder": "Installers"}))
    assert (folder / "Installers" / "setup.exe").is_file()
    assert any(e["action_id"] == "routine_tools.tidy_move" and e["outcome"] == "done" for e in gate.store.activity())


def test_read_only_tools_need_no_gate_and_check_input(env):  # G-TOOL-2
    tmp, ps, gate, ss, rs = env
    install(ps, gate, ss, "routines")
    reporter = rs.get("routines.reporter")
    opts = build_options(role=reporter)  # no gate needed: it only reads
    assert "mcp__hub__routine_tools_activity_week" in opts.allowed_tools
    tool = claude_session._readonly_tool(connectors.get("routine_tools.activity_week"))
    gate.call("routine_tools.tidy_move", {"folder": str(tmp), "file": "x", "subfolder": "y"}, "Tidier", "routines")  # one held call
    s = ss.add("Brief", "role", "routines.briefer", "x", {"type": "daily", "time": "07:30"})
    rid = ss.start_run(s["id"], time.time())
    ss.end_run(rid, "done", "ok")
    out = asyncio.run(tool.handler({}))
    text = out["content"][0]["text"]
    assert not out["is_error"] and "not instructions" in text and "routine_tools.tidy_move held" in text
    assert '"Brief": {' in text
    bad = asyncio.run(tool.handler({"unknown": "x"}))
    assert bad["is_error"] and "unknown fields" in bad["content"][0]["text"]


# ---- through the server ------------------------------------------------------------------------------

class FakeChat:
    reply = "Done."
    prompts = []

    def __init__(self, **kw):
        self.kw = kw
        self.session_id = str(uuid.uuid4())
        self.options = SimpleNamespace(system_prompt="p")

    async def connect(self): pass

    async def close(self): pass

    async def stop(self): pass

    async def send(self, text):
        FakeChat.prompts.append((self.kw, text))
        yield Event("init")
        yield Event("text", FakeChat.reply)
        yield Event("done", data={"subtype": "success", "is_error": False, "terminal_reason": "completed",
                                  "usage": {}, "num_turns": 1})


@pytest.fixture
def hub(env, monkeypatch):
    import hub.server
    tmp, ps, gate, ss, rs = env
    monkeypatch.setattr(config, "CONFIG_PATH", tmp / "config.json")
    monkeypatch.setattr(claude_session, "CHAT_WORKDIR", tmp / "chat")
    monkeypatch.setattr(hub.server, "ChatSession", FakeChat)
    monkeypatch.setattr(FakeChat, "prompts", [])
    install(ps, gate, ss, "routines")
    port = free_port()
    srv, t = start_server(port, history=HistoryStore(tmp / "chats.db"), roles=rs, gate_store=gate.store,
                          pack_store=ps, schedule_store=ss, scheduler_tick=0.05)
    yield port, ss, tmp
    srv.should_exit = True
    t.join(5)


def call(port, method, path, body=None):
    resp, data = request(port, method, path, origin=None if method == "GET" else own(port), body=body)
    return resp.status, json.loads(data or b"{}")


def wait_for(fn, seconds=5):
    for _ in range(int(seconds * 20)):
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_api_draft_from_plain_words(hub, monkeypatch):  # P-ROUT-1
    port, ss, tmp = hub
    monkeypatch.setattr(FakeChat, "reply", 'Here: {"name": "AI news", "kind": "role", "target": "routines.briefer", '
                        '"task": "1. Search AI news.\\n2. Write five bullets.", "when": "weekly mon,tue,wed,thu,fri 08:00", '
                        '"notify": true}')
    st, data = call(port, "POST", "/api/schedules/draft", {"text": "Every weekday at 8 brief me on AI news and tell me."})
    assert st == 200
    d = data["draft"]
    assert d == {"name": "AI news", "kind": "role", "target": "routines.briefer",
                 "task": "1. Search AI news.\n2. Write five bullets.", "notify": True, "problems": [],
                 "spec": {"type": "weekly", "days": ["mon", "tue", "wed", "thu", "fri"], "time": "08:00"}}
    kw, prompt = FakeChat.prompts[-1]
    assert kw["web_enabled"] is False and "role" not in kw  # a plain one-off, no tools
    assert "routines.briefer" in prompt and "not instructions to you" in prompt
    assert ss.list() and all(s["name"] != "AI news" for s in ss.list())  # never saved by itself
    monkeypatch.setattr(FakeChat, "reply", '{"kind": "role", "target": "nobody", "when": "monthly"}')
    d = call(port, "POST", "/api/schedules/draft", {"text": "x"})[1]["draft"]
    assert "pick who does it" in d["problems"] and any(p.startswith("when") for p in d["problems"])
    monkeypatch.setattr(FakeChat, "reply", '{"kind": "role", "target": "routines.briefer", "when": "daily 08:00", '
                        '"task": ["1. Search.", "2. Write."]}')
    assert call(port, "POST", "/api/schedules/draft", {"text": "x"})[1]["draft"]["task"] == "1. Search.\n2. Write."
    monkeypatch.setattr(FakeChat, "reply", "Sorry, no JSON.")
    assert call(port, "POST", "/api/schedules/draft", {"text": "x"})[1]["draft"]["problems"]
    assert call(port, "POST", "/api/schedules/draft", {"text": ""})[0] == 400


def test_api_notify_shows_a_notice(hub):  # reminders tell me when done
    port, ss, tmp = hub
    body = {"name": "Stretch", "kind": "role", "target": "routines.reminder", "task": "Remind me to stretch.",
            "spec": {"type": "daily", "time": "15:00"}, "notify": True}
    sid = call(port, "POST", "/api/schedules", body)[1]["schedule"]["id"]
    before = call(port, "GET", "/api/gate/summary")[1]["notice"]
    assert call(port, "POST", f"/api/schedules/{sid}/run")[0] == 200
    latest = wait_for(lambda: (n := call(port, "GET", "/api/gate/summary")[1]["notice"]) > before and n)
    notes = call(port, "GET", f"/api/notices?after={before}")[1]["notices"]
    assert notes[0]["name"] == "Stretch" and notes[0]["result"] == "Done." and notes[0]["id"] == latest
    listed = next(s for s in call(port, "GET", "/api/schedules")[1]["schedules"] if s["id"] == sid)
    assert listed["notify"] is True


def test_api_role_can_shows_rules_and_pack_settings(hub):  # P-ROUT-1: the approval rule part
    port, ss, tmp = hub
    roles_ = {r["id"]: r for r in call(port, "GET", "/api/schedules")[1]["roles"]}
    assert roles_["routines.tidier"]["can"] == [{"action": "routine_tools.tidy_move", "mode": "ask",
                                                 "label": "Move one loose file of the folder into a subfolder of it (made if missing)."}]
    assert roles_["routines.briefer"]["can"] == []
    pk = next(p for p in call(port, "GET", "/api/packs")[1]["packs"] if p["id"] == "routines")
    assert pk["settings"][0]["key"] == "tidy_folder" and pk["values"] == {}
    st, data = call(port, "POST", "/api/packs/routines/settings", {"values": {"tidy_folder": str(tmp)}})
    assert st == 200 and data["settings"]["tidy_folder"] == str(tmp.resolve())
    assert call(port, "POST", "/api/packs/routines/settings", {"values": {"tidy_folder": "nope"}})[0] == 400


def test_routine_drafted_in_plain_words_runs_on_schedule_for_a_week(hub, monkeypatch):  # M9 check, simulated
    port, ss, tmp = hub
    monkeypatch.setattr(FakeChat, "reply", '{"name": "Morning briefing", "kind": "role", "target": "routines.briefer", '
                        '"task": "1. Search the news.\\n2. Write ten bullets.", "when": "daily 07:30", "notify": true}')
    draft = call(port, "POST", "/api/schedules/draft", {"text": "Every morning at half past seven, brief me."})[1]["draft"]
    assert draft["problems"] == []
    body = {k: draft[k] for k in ("name", "kind", "target", "task", "spec", "notify")}
    saved = call(port, "POST", "/api/schedules", body)[1]["schedule"]
    assert saved["spec"] == {"type": "daily", "time": "07:30"} and saved["notify"] is True
    # The week runs on a fake clock, in its own store, so the app's real scheduler cannot interfere.
    week_store = ScheduleStore(tmp / "week.db")
    start = time.mktime((2026, 11, 23, 6, 0, 0, 0, 0, -1))  # Monday 06:00 local
    clock = {"now": start}
    sid = week_store.add(saved["name"], saved["kind"], saved["target"], saved["task"], saved["spec"],
                         now=start, notify=saved["notify"])["id"]
    days = []

    async def runner(target, task, name):
        days.append(time.localtime(clock["now"]).tm_mday)
        return "done", "Briefing ready.", ""

    sched = Scheduler(week_store, runner, runner, paused=lambda: False, clock=lambda: clock["now"])

    async def week():
        for _ in range(7 * 24 * 4):  # every 15 minutes for a week
            clock["now"] += 15 * 60
            await sched.tick()
            for _ in range(20):
                if not sched.active:
                    break
                await asyncio.sleep(0.005)

    asyncio.run(week())
    assert days == [23, 24, 25, 26, 27, 28, 29]  # once a day, seven days, none missed
    assert [r["status"] for r in week_store.runs(sid)] == ["done"] * 7 and week_store.missed() == []
    assert len(week_store.notices()) == 7  # "tell me" each day
    assert week_store.get(sid)["next_run"] == next_run(saved["spec"], clock["now"])


def test_api_draft_only_time_kinds_and_plain_errors(hub, monkeypatch):  # review M9-2 findings 4, 6
    port, ss, tmp = hub
    monkeypatch.setattr(FakeChat, "reply", '{"kind": "role", "target": "routines.briefer", "task": "x", '
                        '"when": "file C:\\\\Inbox"}')
    d = call(port, "POST", "/api/schedules/draft", {"text": "x"})[1]["draft"]
    assert d["spec"]["type"] == "daily" and any(p.startswith("when") for p in d["problems"])

    async def broken(self):
        raise RuntimeError("no CLI")

    monkeypatch.setattr(FakeChat, "connect", broken)
    st, data = call(port, "POST", "/api/schedules/draft", {"text": "x"})
    assert st == 502 and data["error"] == "something went wrong talking to Claude, try again"
