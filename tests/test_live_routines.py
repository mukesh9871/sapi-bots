"""Live Milestone 9 checks against real Claude (Haiku). Skipped unless HUB_LIVE=1.

- P-ROUT-1: a routine described in plain words becomes a draft schedule I can save
- P-ROUT-2: the reminder and tidy routines of the routines pack work for real
(The full M9 check, a week on schedule, needs the real app for a week: see the hand-over.)
"""

import json
import os
import shutil
import time

import pytest

from helpers.hubhttp import free_port, own, request, start_server
from hub import config, connectors
from hub.chat import claude_session
from hub.chat.claude_session import delete_transcript
from hub.chat.history import HistoryStore
from hub.engine import packs as packs_mod
from hub.engine import roles
from hub.engine.packs import PACKS_DIR, PackStore
from hub.engine.roles import RoleStore
from hub.gate.store import GateStore
from hub.scheduler.schedules import ScheduleStore

pytestmark = pytest.mark.skipif(os.environ.get("HUB_LIVE") != "1", reason="set HUB_LIVE=1 to run live tests")


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    config.save({"model": "haiku", "web_enabled": False, "personality": "straight"})
    shutil.copytree(PACKS_DIR / "routines", tmp_path / "packs" / "routines")
    ps = PackStore(tmp_path / "packs", tmp_path / "hub.db")
    store = ScheduleStore(tmp_path / "hub.db")
    port = free_port()
    srv, t = start_server(port, history=HistoryStore(tmp_path / "chats.db"), roles=RoleStore(tmp_path / "roles"),
                          gate_store=GateStore(tmp_path / "hub.db"), pack_store=ps, schedule_store=store,
                          scheduler_tick=0.5)
    assert call(port, "POST", "/api/packs/routines/install")[0] == 200
    yield port, store, tmp_path
    srv.should_exit = True
    t.join(30)
    connectors.pack_dirs = lambda: {}
    packs_mod.active = None
    for d in claude_session.transcript_dirs()[1:]:
        for f in d.glob("*.jsonl"):
            delete_transcript(f.stem)


def call(port, method, path, body=None):
    resp, data = request(port, method, path, origin=None if method == "GET" else own(port), body=body, timeout=120)
    return resp.status, json.loads(data or b"{}")


def wait_for(fn, seconds=240):
    end = time.time() + seconds
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(1)
    raise AssertionError("timed out")


def test_plain_words_to_a_routine_and_a_reminder_notice(hub):  # P-ROUT-1, P-ROUT-2
    port, store, tmp = hub
    st, data = call(port, "POST", "/api/schedules/draft",
                    {"text": "Every day at 3 in the afternoon, remind me to drink water, and tell me when it is ready."})
    assert st == 200, data
    d = data["draft"]
    assert d["problems"] == [], d
    assert d["kind"] == "role" and d["target"] == "routines.reminder" and d["notify"] is True
    assert d["spec"] == {"type": "daily", "time": "15:00"}
    body = {k: d[k] for k in ("name", "kind", "target", "task", "spec", "notify")}
    sid = call(port, "POST", "/api/schedules", body)[1]["schedule"]["id"]
    before = call(port, "GET", "/api/gate/summary")[1]["notice"]
    assert call(port, "POST", f"/api/schedules/{sid}/run")[0] == 200
    wait_for(lambda: call(port, "GET", "/api/gate/summary")[1]["notice"] > before)
    note = call(port, "GET", f"/api/notices?after={before}")[1]["notices"][0]
    text = note["result"].lower()
    assert note["status"] == "done" and any(w in text for w in ("water", "drink", "hydrat", "glass")), text


def test_tidy_routine_moves_files_only_with_my_rule(hub):  # P-ROUT-2, approval gate
    port, store, tmp = hub
    folder = tmp / "Downloads"
    folder.mkdir()
    for n in ("invoice.pdf", "holiday.jpg"):
        (folder / n).write_text("x")
    assert call(port, "POST", "/api/packs/routines/settings", {"values": {"tidy_folder": str(folder)}})[0] == 200
    sch = next(s for s in call(port, "GET", "/api/schedules")[1]["schedules"] if s["name"] == "Tidy my folder")
    # Ask (as installed): the moves wait for me, nothing moves
    assert call(port, "POST", f"/api/schedules/{sch['id']}/run")[0] == 200
    wait_for(lambda: (r := store.last_run(sch["id"])) and r["status"] != "running")
    assert (folder / "invoice.pdf").exists() and (folder / "holiday.jpg").exists()
    assert call(port, "GET", "/api/gate/summary")[1]["pending"] >= 1
    # I approve what it asked for: now those files move
    for item in call(port, "GET", "/api/approvals")[1]["items"]:
        assert call(port, "POST", f"/api/approvals/{item['id']}/approve", {})[0] == 200
    assert not (folder / "invoice.pdf").exists() and not (folder / "holiday.jpg").exists()
    # I switch the rule to Auto (my choice, in Settings): a new file is tidied without asking
    assert call(port, "PUT", "/api/rules", {"pack": "routines", "platform": "files", "action": "tidy_move",
                                            "mode": "auto", "daily_cap": 100})[0] == 200
    (folder / "notes.txt").write_text("x")
    first = store.last_run(sch["id"])["id"]
    assert call(port, "POST", f"/api/schedules/{sch['id']}/run")[0] == 200
    wait_for(lambda: (r := store.last_run(sch["id"])) and r["id"] > first and r["status"] != "running")
    moved = [p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()]
    assert not (folder / "notes.txt").exists(), moved
    assert len(moved) == 3 and all("/" in m for m in moved)  # each file is now in a subfolder
