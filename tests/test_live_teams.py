"""Live Milestone 7 checks against real Claude (Haiku). Skipped unless HUB_LIVE=1.

- M7 check 1: a lead, worker and reviewer team finishes a small task; the board shows each status
- M7 check 2: I can pause, stop and redirect the team mid-task
"""

import json
import os
import time

import pytest

from helpers.hubhttp import free_port, own, request, start_server
from hub import config
from hub.chat import claude_session
from hub.chat.claude_session import delete_transcript
from hub.chat.history import HistoryStore
from hub.engine import roles
from hub.engine.roles import RoleStore
from hub.engine.teams import TeamStore
from hub.gate.store import GateStore

pytestmark = pytest.mark.skipif(os.environ.get("HUB_LIVE") != "1", reason="set HUB_LIVE=1 to run live tests")


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    rs = RoleStore(tmp_path / "roles")
    rs.save(None, "Manager", "haiku", [], "You lead small writing tasks. Keep steps short.")
    rs.save(None, "Writer", "haiku", ["Write"], "You write short texts into files in the shared folder.")
    rs.save(None, "Reviewer", "haiku", ["Read"], "You read the files and say briefly whether they meet the task.")
    ts = TeamStore(tmp_path / "teams", roles_get=rs.get)
    ts.save(None, "Writers", "manager", "reviewer", ["writer"], 2, 60, "")
    port = free_port()
    srv, t = start_server(port, history=HistoryStore(tmp_path / "c.db"), roles=rs,
                          gate_store=GateStore(tmp_path / "hub.db"), team_store=ts)
    yield port, tmp_path
    srv.should_exit = True
    t.join(30)
    for d in claude_session.transcript_dirs()[1:]:  # leave no test transcripts behind
        for f in d.glob("*.jsonl"):
            delete_transcript(f.stem)


def call(port, method, path, body=None):
    resp, data = request(port, method, path, origin=None if method == "GET" else own(port), body=body, timeout=30)
    return resp.status, json.loads(data or b"{}")


def statuses(port, rid):
    data = call(port, "GET", f"/api/runs/{rid}")[1]
    return data, {m["key"]: m["status"] for m in data["board"]["members"]}


def test_team_finishes_a_small_task(hub):  # M7 check 1
    port, tmp = hub
    st, data = call(port, "POST", "/api/teams/writers/runs",
                    {"task": "Write a four-line poem about tea into poem.md, have it reviewed, then report."})
    assert st == 200
    rid, seen = data["run_id"], {}
    deadline = time.time() + 400
    while time.time() < deadline:
        data, now = statuses(port, rid)
        for key, s in now.items():
            seen.setdefault(key, set()).add(s)
        if data["run"]["status"] in ("done", "stopped", "failed"):
            break
        time.sleep(1)
    assert data["run"]["status"] == "done", (data["run"], data["events"][-5:])
    assert "working" in seen["writer"] and "working" in seen["reviewer"]  # the board showed them at work
    assert set(now.values()) == {"done"}
    folder = tmp / "projects" / f"team-{rid}"
    assert (folder / "poem.md").is_file() and (folder / "REPORT.md").is_file()
    done = [s["member"] for s in data["steps"] if s["status"] == "done"]
    assert "writer" in done and done[-1] == "reviewer"  # reviewed last


def test_pause_redirect_and_stop_mid_task(hub):  # M7 check 2
    port, tmp = hub
    st, data = call(port, "POST", "/api/teams/writers/runs",
                    {"task": "Write five short poems, one per step and one file each (poem1.md to poem5.md). "
                             "Assign them one at a time."})
    rid = data["run_id"]
    deadline = time.time() + 300
    while time.time() < deadline and statuses(port, rid)[1].get("writer") != "working":
        time.sleep(0.5)
    assert call(port, "POST", f"/api/runs/{rid}/pause")[1]["status"] == "paused"
    time.sleep(3)
    data, now = statuses(port, rid)
    assert data["run"]["status"] == "paused" and now["writer"] != "working"  # the writer stopped
    assert call(port, "POST", f"/api/runs/{rid}/message", {"text": "Give every poem a title line."})[0] == 200
    assert call(port, "POST", f"/api/runs/{rid}/resume")[1]["status"] == "running"
    while time.time() < deadline and statuses(port, rid)[1].get("writer") != "working":
        time.sleep(0.5)
    assert call(port, "POST", f"/api/runs/{rid}/stop")[1]["status"] == "stopped"
    while time.time() < deadline and call(port, "GET", f"/api/runs/{rid}")[1]["board"]["members"][0]["status"] != "stopped":
        time.sleep(0.5)
    data, now = statuses(port, rid)
    assert data["run"]["status"] == "stopped" and set(now.values()) == {"stopped"}
    events = " ".join(e["text"] for e in data["events"])
    assert "paused the team" in events and "Give every poem a title line" in events and "run stopped" in events
    assert any(s["status"] == "interrupted" for s in data["steps"])
