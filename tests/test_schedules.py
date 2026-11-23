"""Milestone 8 offline checks: schedules and triggers (G-SCHED-1, G-SCHED-2). No Claude calls.

- M8 check 1: a scheduled task runs at its time; a missed run is listed (and I choose Run late
  or Dismiss).
The scheduler is driven with a fake clock; the API tests use a stand-in Claude session.
"""

import asyncio
import datetime as dt
import json
import time
import uuid
from types import SimpleNamespace

import pytest

from helpers.hubhttp import free_port, own, request, start_server
from hub import config
from hub.chat import claude_session
from hub.chat.claude_session import Event
from hub.chat.history import HistoryStore
from hub.engine import roles
from hub.engine.roles import RoleStore
from hub.gate.store import GateStore
from hub.scheduler.runner import GRACE, Scheduler
from hub.scheduler.schedules import ScheduleError, ScheduleStore, describe, next_run, parse_when


def at(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi).timestamp()


# ---- when ------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, spec", [
    ("daily 08:00", {"type": "daily", "time": "08:00"}),
    ("daily 7:05", {"type": "daily", "time": "07:05"}),
    ("weekly fri,mon 09:30", {"type": "weekly", "days": ["mon", "fri"], "time": "09:30"}),
    ("every 90 minutes", {"type": "interval", "minutes": 90}),
    ("every 2 hours", {"type": "interval", "minutes": 120}),
    ("file C:\\Inbox", {"type": "file", "folder": "C:\\Inbox"}),
    ("after 3", {"type": "after", "schedule": 3}),
])
def test_parse_when(text, spec):
    assert parse_when(text) == spec


@pytest.mark.parametrize("text", ["daily 25:00", "daily", "weekly 09:00", "weekly xyz 09:00", "every 2 minutes",
                                  "every 8 days", "file relative\\path", "file \\\\server\\share", "monthly 1", ""])
def test_bad_when_refused(text):
    with pytest.raises(ScheduleError):
        parse_when(text)


def test_next_run_times():
    mon_7 = at(2026, 11, 23, 7, 0)  # a Monday
    assert next_run({"type": "daily", "time": "08:00"}, mon_7) == at(2026, 11, 23, 8, 0)
    assert next_run({"type": "daily", "time": "08:00"}, at(2026, 11, 23, 8, 0)) == at(2026, 11, 24, 8, 0)
    assert next_run({"type": "weekly", "days": ["fri"], "time": "09:30"}, mon_7) == at(2026, 11, 27, 9, 30)
    assert next_run({"type": "interval", "minutes": 30}, mon_7) == mon_7 + 1800
    assert next_run({"type": "file", "folder": "C:\\x"}, mon_7) is None
    assert describe({"type": "weekly", "days": ["mon", "fri"], "time": "09:30"}) == "Mon, Fri at 09:30"


# ---- the scheduler with a fake clock ---------------------------------------------------------------

class Harness:
    def __init__(self, tmp_path, slow=0.0):
        self.store = ScheduleStore(tmp_path / "hub.db")
        self.now = at(2026, 11, 23, 7, 0)
        self.paused = False
        self.calls = []
        self.slow = slow
        self.fail = False

        async def runner(target, task, name):
            self.calls.append((target, task, name))
            if self.slow:
                await asyncio.sleep(self.slow)
            if self.fail:
                return "failed", "it broke", ""
            return "done", f"did {name}", f"chat:{len(self.calls)}"

        self.sched = Scheduler(self.store, runner, runner, paused=lambda: self.paused, tick=0.01,
                               clock=lambda: self.now)

    def add(self, spec, name="Morning", kind="role", enabled=True):
        return self.store.add(name, kind, "writer", "Write the briefing.", spec, enabled=enabled, now=self.now)

    async def settle(self):
        for _ in range(50):
            if not self.sched.active:
                return
            await asyncio.sleep(0.01)


def test_task_runs_at_its_time(tmp_path):  # M8 check 1 (part 1), G-SCHED-1
    h = Harness(tmp_path)
    s = h.add({"type": "daily", "time": "08:00"})
    assert s["next_run"] == at(2026, 11, 23, 8, 0)

    async def scenario():
        h.now = at(2026, 11, 23, 7, 59)
        await h.sched.tick()
        assert h.calls == []  # not yet
        h.now = at(2026, 11, 23, 8, 0) + 10  # its time (the loop ticks every few seconds)
        await h.sched.tick()
        await h.settle()

    asyncio.run(scenario())
    assert h.calls == [("writer", "Write the briefing.", "Morning")]
    run = h.store.last_run(s["id"])
    assert run["status"] == "done" and run["result"] == "did Morning" and run["ref"] == "chat:1"
    assert h.store.get(s["id"])["next_run"] == at(2026, 11, 24, 8, 0)  # P-ROUT-3: the next run is known


def test_run_missed_while_the_app_was_off_is_listed(tmp_path):  # M8 check 1 (part 2), G-SCHED-2
    h = Harness(tmp_path)
    s = h.add({"type": "daily", "time": "08:00"})

    async def scenario():
        h.now = at(2026, 11, 25, 12, 0)  # the app was off for two days
        await h.sched.tick()

    asyncio.run(scenario())
    assert h.calls == []  # never run by itself
    missed = h.store.missed()
    assert len(missed) == 1 and missed[0]["name"] == "Morning" and "not running" in missed[0]["reason"]
    assert h.store.get(s["id"])["next_run"] == at(2026, 11, 26, 8, 0)  # never a past time

    async def again():  # it misses again: still one row, counted
        h.now = at(2026, 11, 27, 12, 0)
        await h.sched.tick()
    asyncio.run(again())
    assert [m["missed_count"] for m in h.store.missed()] == [2]


def test_run_late_and_dismiss(tmp_path):  # G-SCHED-2: I choose
    h = Harness(tmp_path)
    s1 = h.add({"type": "daily", "time": "08:00"}, name="A")
    s2 = h.add({"type": "daily", "time": "08:00"}, name="B")

    async def scenario():
        h.now = at(2026, 11, 23, 8, 0) + GRACE + 60  # a bit too late: missed
        await h.sched.tick()
        missed = {m["name"]: m["id"] for m in h.store.missed()}
        assert await h.sched.run_now(s1["id"], late_of=missed["A"]) == ""
        await h.settle()
        assert await h.sched.run_now(s1["id"], late_of=missed["A"]) == "this missed run was already handled"
        assert h.store.close_missed(missed["B"], "dismissed")

    asyncio.run(scenario())
    assert h.calls == [("writer", "Write the briefing.", "A")]
    assert h.store.missed() == []
    statuses = {r["status"] for r in h.store.runs(s1["id"])}
    assert statuses == {"ran-late", "done"}
    assert h.store.runs(s2["id"])[0]["status"] == "dismissed"


def test_pause_lists_the_run_as_missed(tmp_path):  # G-BUDGET-2 with G-SCHED-2
    h = Harness(tmp_path)
    s = h.add({"type": "interval", "minutes": 30})
    h.paused = True

    async def scenario():
        h.now += 1800
        await h.sched.tick()
        assert await h.sched.run_now(s["id"]) == "automation is paused"

    asyncio.run(scenario())
    assert h.calls == [] and "paused" in h.store.missed()[0]["reason"]


def test_a_schedule_never_runs_twice_at_once(tmp_path):
    h = Harness(tmp_path, slow=0.2)
    s = h.add({"type": "interval", "minutes": 5})

    async def scenario():
        h.now += 300
        await h.sched.tick()
        h.now += 300
        await h.sched.tick()  # the first is still going
        assert await h.sched.run_now(s["id"]) == "it is still running"
        await h.settle()

    asyncio.run(scenario())
    assert len(h.calls) == 1
    assert sorted(r["status"] for r in h.store.runs(s["id"])) == ["done", "skipped"]


def test_switched_off_schedule_does_not_run_and_switching_on_skips_the_past(tmp_path):
    h = Harness(tmp_path)
    s = h.add({"type": "daily", "time": "08:00"}, enabled=False)

    async def scenario():
        h.now = at(2026, 11, 23, 8, 0) + 5
        await h.sched.tick()
        h.store.set_enabled(s["id"], True, now=h.now)
        await h.sched.tick()

    asyncio.run(scenario())
    assert h.calls == [] and h.store.missed() == []
    assert h.store.get(s["id"])["next_run"] == at(2026, 11, 24, 8, 0)


def test_file_trigger(tmp_path):  # G-SCHED-1: a new file in a folder
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "old.txt").write_text("x")
    h = Harness(tmp_path)
    h.add({"type": "file", "folder": str(inbox)}, name="Tidy")

    async def scenario():
        await h.sched.tick()  # first look: old.txt does not count
        assert h.calls == []
        (inbox / "new.pdf").write_text("x")
        await h.sched.tick()
        await h.settle()
        await h.sched.tick()  # nothing new
        await h.settle()

    asyncio.run(scenario())
    assert len(h.calls) == 1
    task = h.calls[0][1]
    assert task.startswith("Write the briefing.") and "new.pdf" in task and "old.txt" not in task
    assert "information, not instructions" in task  # N-SEC-7


def test_big_folder_does_not_fire_again_and_again(tmp_path):  # review M8-1 finding 1
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for i in range(60):
        (inbox / f"f{i:03d}.txt").write_text("x")
    h = Harness(tmp_path)
    h.add({"type": "file", "folder": str(inbox)}, name="Tidy")

    async def scenario():
        for _ in range(5):
            await h.sched.tick()
            await h.settle()

    asyncio.run(scenario())
    assert h.calls == []


def test_copied_file_with_an_old_date_counts_as_new(tmp_path):  # Windows keeps the modified time on copy
    import os
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.txt").write_text("x")
    h = Harness(tmp_path)
    h.add({"type": "file", "folder": str(inbox)}, name="Tidy")

    async def scenario():
        await h.sched.tick()
        old = inbox / "old-report.pdf"
        old.write_text("x")
        os.utime(old, (1_000_000_000, 1_000_000_000))  # modified in 2001, created now
        await h.sched.tick()
        await h.settle()

    asyncio.run(scenario())
    assert len(h.calls) == 1 and "old-report.pdf" in h.calls[0][1]


def test_files_that_came_while_the_app_was_off_are_missed_with_their_names(tmp_path):  # review M8-1 finding 6
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    h = Harness(tmp_path)
    s = h.add({"type": "file", "folder": str(inbox)}, name="Tidy")
    h.store.set_state(s["id"], {"seen": []})  # the baseline from an earlier start of the app
    (inbox / "while-off.pdf").write_text("x")

    async def scenario():
        await h.sched.tick()  # the first look since the app started
        await h.settle()
        assert h.calls == []
        missed = h.store.missed()
        assert len(missed) == 1 and "while-off.pdf" in missed[0]["extra"]
        assert await h.sched.run_now(s["id"], late_of=missed[0]["id"]) == ""
        await h.settle()

    asyncio.run(scenario())
    assert "while-off.pdf" in h.calls[0][1]  # Run late tells the agent which files


def test_switching_a_file_trigger_on_starts_a_new_baseline(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    h = Harness(tmp_path)
    s = h.add({"type": "file", "folder": str(inbox)}, name="Tidy")

    async def scenario():
        await h.sched.tick()
        h.store.set_enabled(s["id"], False, now=h.now)
        (inbox / "during-off.txt").write_text("x")
        h.store.set_enabled(s["id"], True, now=h.now)
        await h.sched.tick()
        await h.sched.tick()
        await h.settle()

    asyncio.run(scenario())
    assert h.calls == []


def test_after_loop_never_runs_forever(tmp_path):  # review M8-1 finding 2
    h = Harness(tmp_path)
    a = h.add({"type": "interval", "minutes": 5}, name="A")
    b = h.add({"type": "after", "schedule": a["id"]}, name="B")
    h.store.update(a["id"], "A", "role", "writer", "x", {"type": "after", "schedule": b["id"]}, now=h.now)
    assert h.store.would_loop(a["id"], {"type": "after", "schedule": b["id"]})

    async def scenario():
        assert await h.sched.run_now(a["id"]) == ""
        for _ in range(5):
            await h.settle()
            await asyncio.sleep(0.02)

    asyncio.run(scenario())
    assert [c[2] for c in h.calls] == ["A", "B"]


def test_switched_off_pack_schedules_do_not_run(tmp_path):  # review M8-1 finding 5
    h = Harness(tmp_path)
    on = {"local": True, "sample": False}
    h.sched.pack_on = lambda pack: on.get(pack, False)
    s = h.store.add("Pack note", "role", "sample.notetaker", "x", {"type": "interval", "minutes": 5}, pack="sample",
                    now=h.now)
    mine = h.add({"type": "interval", "minutes": 5}, name="Mine")
    h.store.update(mine["id"], "Mine", "role", "sample.notetaker", "x", {"type": "interval", "minutes": 5}, now=h.now)

    async def scenario():
        h.now += 300
        await h.sched.tick()
        assert await h.sched.run_now(s["id"]) == "its pack is switched off"

    asyncio.run(scenario())
    assert h.calls == [] and h.store.runs(s["id"]) == []


def test_moved_in_file_counts_and_edited_file_does_not(tmp_path):  # review M8-2 findings 1, 2
    import os
    inbox, elsewhere = tmp_path / "inbox", tmp_path / "elsewhere"
    inbox.mkdir()
    elsewhere.mkdir()
    (elsewhere / "moved.pdf").write_text("x")  # made before the watch started
    (inbox / "notes.txt").write_text("x")
    h = Harness(tmp_path)
    h.add({"type": "file", "folder": str(inbox)}, name="Tidy")

    async def scenario():
        await h.sched.tick()  # baseline
        (inbox / "notes.txt").write_text("edited")  # saving a file already there
        await h.sched.tick()
        await h.settle()
        assert h.calls == []
        os.replace(elsewhere / "moved.pdf", inbox / "moved.pdf")  # a move keeps the old dates
        await h.sched.tick()
        await h.settle()

    asyncio.run(scenario())
    assert len(h.calls) == 1 and "moved.pdf" in h.calls[0][1]


def test_file_that_comes_during_a_run_is_picked_up_after_it(tmp_path):  # review M8-2 finding 3
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    h = Harness(tmp_path, slow=0.2)
    h.add({"type": "file", "folder": str(inbox)}, name="Tidy")

    async def scenario():
        await h.sched.tick()
        (inbox / "one.pdf").write_text("x")
        await h.sched.tick()
        (inbox / "two.pdf").write_text("x")  # while the first run is going
        await h.sched.tick()
        await h.settle()
        await h.sched.tick()
        await h.settle()

    asyncio.run(scenario())
    assert len(h.calls) == 2 and "one.pdf" in h.calls[0][1] and "two.pdf" in h.calls[1][1]
    assert "one.pdf" not in h.calls[1][1]


def test_files_that_came_while_its_pack_was_off_are_missed(tmp_path):  # review M8-2 small item
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    h = Harness(tmp_path)
    on = {"local": True, "sample": True}
    h.sched.pack_on = lambda pack: on.get(pack, False)
    h.store.add("Pack watch", "role", "sample.notetaker", "x", {"type": "file", "folder": str(inbox)}, pack="sample",
                now=h.now)

    async def scenario():
        await h.sched.tick()
        on["sample"] = False
        await h.sched.tick()
        (inbox / "meanwhile.pdf").write_text("x")
        on["sample"] = True
        await h.sched.tick()
        await h.settle()

    asyncio.run(scenario())
    assert h.calls == [] and "meanwhile.pdf" in h.store.missed()[0]["extra"]


def test_after_trigger_follows_a_good_run(tmp_path):  # G-SCHED-1: on a finished task
    h = Harness(tmp_path)
    first = h.add({"type": "interval", "minutes": 5}, name="Render")
    h.add({"type": "after", "schedule": first["id"]}, name="Post")

    async def scenario():
        h.now += 300
        await h.sched.tick()
        await h.settle()
        await h.settle()

    asyncio.run(scenario())
    assert [c[2] for c in h.calls] == ["Render", "Post"]


def test_after_trigger_does_not_follow_a_failed_run(tmp_path):
    h = Harness(tmp_path)
    h.fail = True
    first = h.add({"type": "interval", "minutes": 5}, name="Render")
    h.add({"type": "after", "schedule": first["id"]}, name="Post")

    async def scenario():
        h.now += 300
        await h.sched.tick()
        await h.settle()

    asyncio.run(scenario())
    assert [c[2] for c in h.calls] == ["Render"]


def test_stop_a_running_run(tmp_path):
    h = Harness(tmp_path, slow=5)
    s = h.add({"type": "interval", "minutes": 5})

    async def scenario():
        assert await h.sched.run_now(s["id"]) == ""
        await asyncio.sleep(0.05)
        assert await h.sched.stop(s["id"], "automation was paused")
        assert not await h.sched.stop(s["id"])

    asyncio.run(scenario())
    run = h.store.last_run(s["id"])
    assert run["status"] == "stopped" and run["reason"] == "automation was paused"


def test_restart_marks_running_runs_stopped(tmp_path):  # never re-run by itself
    h = Harness(tmp_path)
    s = h.add({"type": "interval", "minutes": 5})
    h.store.start_run(s["id"], h.now)
    assert h.store.stop_unfinished() == 1
    assert h.store.last_run(s["id"])["status"] == "stopped"


# ---- through the server ------------------------------------------------------------------------------

class FakeChat:
    """Stand-in Claude session for scheduled role tasks."""
    made = []

    def __init__(self, **kw):
        self.kw = kw
        self.session_id = str(uuid.uuid4())
        self.options = SimpleNamespace(system_prompt="prompt", max_turns=kw["role"].max_turns if kw.get("role") else None)
        FakeChat.made.append(self)

    async def connect(self): pass

    async def close(self): pass

    async def stop(self): pass

    async def send(self, text):
        yield Event("init")
        yield Event("turn", data={"turn": 1})
        yield Event("text", "Briefing written.")
        yield Event("done", data={"subtype": "success", "is_error": False, "terminal_reason": "completed",
                                  "usage": {}, "num_turns": 1})


@pytest.fixture
def hub(tmp_path, monkeypatch):
    import hub.server
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(claude_session, "CHAT_WORKDIR", tmp_path / "chat")
    monkeypatch.setattr(hub.server, "ChatSession", FakeChat)
    monkeypatch.setattr(FakeChat, "made", [])
    rs = RoleStore(tmp_path / "roles")
    rs.save(None, "Writer", "haiku", ["Write"], "Write briefings.")
    history = HistoryStore(tmp_path / "chats.db")
    store = ScheduleStore(tmp_path / "hub.db")
    port = free_port()
    srv, t = start_server(port, history=history, roles=rs, gate_store=GateStore(tmp_path / "hub.db"),
                          schedule_store=store, scheduler_tick=0.05)
    yield port, store, history
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


DAILY = {"name": "Morning", "kind": "role", "target": "writer", "task": "Write the briefing.",
         "spec": {"type": "daily", "time": "08:00"}}


def test_api_schedule_runs_and_becomes_a_chat(hub):  # M8 check 1 through the API
    port, store, history = hub
    st, data = call(port, "POST", "/api/schedules", DAILY)
    assert st == 200
    sid = data["schedule"]["id"]
    store.set_next(sid, time.time() - 1)  # its time has come
    run = wait_for(lambda: (r := store.last_run(sid)) and r["status"] == "done" and r)
    assert run["result"] == "Briefing written." and run["ref"].startswith("chat:")
    chat = history.get_chat(int(run["ref"][5:]))
    assert chat["role"] == "writer" and chat["title"] == "Scheduled: Morning"
    assert [m["role"] for m in history.messages(chat["id"])] == ["user", "bot"]
    listed = call(port, "GET", "/api/schedules")[1]["schedules"][0]
    assert listed["when"] == "every day at 08:00" and listed["last"]["status"] == "done" and listed["next_run"]


def test_api_missed_run_listed_then_run_late(hub):  # M8 check 1, G-SCHED-2
    port, store, _ = hub
    sid = call(port, "POST", "/api/schedules", DAILY)[1]["schedule"]["id"]
    store.set_next(sid, time.time() - 3600)  # it came due an hour ago, while the app was off
    missed = wait_for(lambda: call(port, "GET", "/api/schedules")[1]["missed"])
    assert missed[0]["name"] == "Morning" and FakeChat.made == []
    assert call(port, "GET", "/api/gate/summary")[1]["missed"] == 1  # the badge updates without opening Schedules
    assert call(port, "POST", f"/api/missed/{missed[0]['id']}/run")[0] == 200
    wait_for(lambda: (r := store.last_run(sid)) and r["status"] == "done")
    assert call(port, "GET", "/api/schedules")[1]["missed"] == []
    assert call(port, "POST", f"/api/missed/{missed[0]['id']}/dismiss")[0] == 404  # already handled


def test_api_pause_and_validation(hub):
    port, store, _ = hub
    bad = [{**DAILY, "target": "nobody"}, {**DAILY, "kind": "shell"}, {**DAILY, "spec": {"type": "daily", "time": "8"}},
           {**DAILY, "task": ""}, {**DAILY, "spec": {"type": "after", "schedule": 999}}, {**DAILY, "name": ""}]
    for body in bad:
        assert call(port, "POST", "/api/schedules", body)[0] == 400, body
    sid = call(port, "POST", "/api/schedules", DAILY)[1]["schedule"]["id"]
    call(port, "POST", "/api/settings", {"automation_paused": True})
    assert call(port, "POST", f"/api/schedules/{sid}/run")[0] == 409
    call(port, "POST", "/api/settings", {"automation_paused": False})
    assert call(port, "POST", f"/api/schedules/{sid}/enable", {"enabled": False})[1]["schedule"]["next_run"] is None
    assert call(port, "PUT", f"/api/schedules/{sid}", {**DAILY, "name": "Evening"})[1]["schedule"]["name"] == "Evening"
    assert call(port, "DELETE", f"/api/schedules/{sid}")[0] == 200
    assert call(port, "DELETE", f"/api/schedules/{sid}")[0] == 404


def test_schedule_endpoints_need_origin(hub):  # N-SEC-4
    port = hub[0]
    for method, path in [("POST", "/api/schedules"), ("POST", "/api/schedules/1/run"), ("POST", "/api/missed/1/run"),
                         ("POST", "/api/packs/sample/install"), ("DELETE", "/api/schedules/1")]:
        resp, _ = request(port, method, path, body={})
        assert resp.status == 403, path


def test_api_after_loop_refused(hub):  # review M8-1 finding 2
    port, store, _ = hub
    a = call(port, "POST", "/api/schedules", DAILY)[1]["schedule"]["id"]
    b = call(port, "POST", "/api/schedules", {**DAILY, "name": "B", "spec": {"type": "after", "schedule": a}})[1]["schedule"]["id"]
    st, data = call(port, "PUT", f"/api/schedules/{a}", {**DAILY, "spec": {"type": "after", "schedule": b}})
    assert st == 400 and "follow itself" in data["error"]


def test_api_scheduled_chat_is_protected_while_it_runs(hub, monkeypatch):  # review M8-1 finding 3
    port, store, history = hub
    gate_open = []

    async def slow_send(self, text):
        yield Event("init")
        while not gate_open:
            await asyncio.sleep(0.02)
        yield Event("text", "done")
        yield Event("done", data={"subtype": "success", "is_error": False, "terminal_reason": "completed",
                                  "usage": {}, "num_turns": 1})

    monkeypatch.setattr(FakeChat, "send", slow_send)
    sid = call(port, "POST", "/api/schedules", DAILY)[1]["schedule"]["id"]
    assert call(port, "POST", f"/api/schedules/{sid}/run")[0] == 200
    chat_id = wait_for(lambda: (r := store.last_run(sid)) and history.list_chats() and history.list_chats()[0]["id"])
    resp, _ = request(port, "POST", "/api/chat", origin=own(port), body={"text": "hi", "chat_id": chat_id})
    assert resp.status == 409
    assert call(port, "DELETE", f"/api/chats/{chat_id}")[0] == 409
    assert call(port, "POST", "/api/history/delete-all", {"confirm": "DELETE"})[0] == 409
    gate_open.append(1)
    wait_for(lambda: store.last_run(sid)["status"] == "done")
    assert call(port, "DELETE", f"/api/chats/{chat_id}")[0] == 200


def test_api_budget_run_does_not_start_the_after_chain(hub, monkeypatch):  # review M8-3 finding 1
    port, store, history = hub

    async def budget_send(self, text):
        yield Event("init")
        yield Event("text", "half")
        yield Event("done", data={"subtype": "error_max_turns", "is_error": True, "terminal_reason": "max_turns",
                                  "usage": {}, "num_turns": 30})

    monkeypatch.setattr(FakeChat, "send", budget_send)
    a = call(port, "POST", "/api/schedules", DAILY)[1]["schedule"]["id"]
    b = call(port, "POST", "/api/schedules", {**DAILY, "name": "B", "spec": {"type": "after", "schedule": a}})[1]["schedule"]["id"]
    assert call(port, "POST", f"/api/schedules/{a}/run")[0] == 200
    run = wait_for(lambda: (r := store.last_run(a)) and r["status"] != "running" and r)
    time.sleep(0.3)
    assert run["status"] == "budget" and store.last_run(b) is None
    chat = int(run["ref"][5:])
    assert history.messages(chat)[-1]["status"] == "budget"


def test_api_pack_with_missing_folder_is_listed(hub, tmp_path):  # review M8-3 finding 3
    port, store, _ = hub
    import hub.engine.packs as packs_mod
    ps = packs_mod.PackStore(tmp_path / "nopacks", tmp_path / "hub.db")
    ps.mark_installed("gone")
    packs = call(port, "GET", "/api/packs")[1]["packs"]
    gone = next(p for p in packs if p["id"] == "gone")
    assert gone["installed"] and "missing" in gone["problem"]
    assert call(port, "POST", "/api/packs/gone/remove")[0] == 200
