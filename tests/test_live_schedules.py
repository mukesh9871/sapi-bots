"""Live Milestone 8 checks against real Claude (Haiku). Skipped unless HUB_LIVE=1.

- M8 check 1: a scheduled task runs at its time (and a missed run is listed)
- M8 check 2: the sample pack installs, its role works through the approval gate, and it removes
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
from hub.engine import roles
from hub.engine.packs import PACKS_DIR, PackStore
from hub.engine.roles import RoleStore
from hub.engine.teams import TeamStore
from hub.gate.store import GateStore
from hub.scheduler.schedules import ScheduleStore

pytestmark = pytest.mark.skipif(os.environ.get("HUB_LIVE") != "1", reason="set HUB_LIVE=1 to run live tests")


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setenv("HUB_OUTBOX", str(tmp_path / "outbox"))
    config.save({"model": "haiku", "web_enabled": False, "personality": "straight"})
    shutil.copytree(PACKS_DIR / "sample", tmp_path / "packs" / "sample")
    ps = PackStore(tmp_path / "packs", tmp_path / "hub.db")
    rs = RoleStore(tmp_path / "roles")
    rs.save(None, "Writer", "haiku", ["Write"], "You write very short texts into files in your folder.")
    store = ScheduleStore(tmp_path / "hub.db")
    history = HistoryStore(tmp_path / "chats.db")
    port = free_port()
    srv, t = start_server(port, history=history, roles=rs, gate_store=GateStore(tmp_path / "hub.db"),
                          team_store=TeamStore(tmp_path / "teams", roles_get=rs.get), pack_store=ps,
                          schedule_store=store, scheduler_tick=0.5)
    yield port, store, history, tmp_path
    srv.should_exit = True
    t.join(30)
    connectors.pack_dirs = lambda: {}
    for d in claude_session.transcript_dirs()[1:]:  # leave no test transcripts behind
        for f in d.glob("*.jsonl"):
            delete_transcript(f.stem)


def call(port, method, path, body=None):
    resp, data = request(port, method, path, origin=None if method == "GET" else own(port), body=body, timeout=30)
    return resp.status, json.loads(data or b"{}")


def wait_for(fn, seconds=180):
    end = time.time() + seconds
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(1)
    raise AssertionError("timed out")


def test_scheduled_task_runs_at_its_time_and_missed_is_listed(hub):  # M8 check 1
    port, store, history, tmp = hub
    st, data = call(port, "POST", "/api/schedules", {
        "name": "Haiku file", "kind": "role", "target": "writer", "task": "Write a three-word line into line.md.",
        "spec": {"type": "daily", "time": "08:00"}})
    assert st == 200
    sid = data["schedule"]["id"]
    store.set_next(sid, time.time() + 2)  # its time is in two seconds
    run = wait_for(lambda: (r := store.last_run(sid)) and r["status"] in ("done", "failed") and r)
    assert run["status"] == "done", run
    assert (tmp / "projects" / "role-writer" / "line.md").is_file()
    assert history.get_chat(int(run["ref"][5:]))["title"] == "Scheduled: Haiku file"
    store.set_next(sid, time.time() - 7200)  # as if the app was off two hours ago
    missed = wait_for(lambda: call(port, "GET", "/api/schedules")[1]["missed"], 20)
    assert missed[0]["schedule_id"] == sid


def test_sample_pack_installs_runs_and_removes(hub):  # M8 check 2
    port, store, history, tmp = hub
    assert call(port, "POST", "/api/packs/sample/install")[0] == 200
    sch = next(s for s in call(port, "GET", "/api/schedules")[1]["schedules"] if s["pack"] == "sample")
    assert not sch["enabled"]  # pack schedules are installed switched off
    assert call(port, "POST", f"/api/schedules/{sch['id']}/run")[0] == 200
    run = wait_for(lambda: (r := store.last_run(sch["id"])) and r["status"] in ("done", "failed") and r)
    assert run["status"] == "done", run
    held = call(port, "GET", "/api/approvals")[1]
    items = held.get("items") or held.get("approvals") or []
    assert any(i["pack"] == "sample" and i["action_id"] == "sample_notes.add_note" for i in items)  # Ask: held
    assert call(port, "POST", "/api/packs/sample/remove")[1]["rejected"] >= 1
    assert "sample.notetaker" not in [r["id"] for r in call(port, "GET", "/api/roles")[1]["roles"]]
    assert not (tmp / "outbox" / "sample-notes.md").exists()  # nothing ran without my approval
