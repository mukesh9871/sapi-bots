"""Live Milestone 12 check against real Claude (Haiku). Skipped unless HUB_LIVE=1. Takes several minutes.

- M12 check: the full team delivers a small tested program in a git project folder, within budget.
  (Running the tests is Auto here, as if I had switched it on; pushing stays Ask and is not used.)
"""

import json
import os
import shutil
import subprocess
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
from hub.engine.teams import TeamStore
from hub.gate.store import GateStore
from hub.scheduler.schedules import ScheduleStore

pytestmark = pytest.mark.skipif(os.environ.get("HUB_LIVE") != "1", reason="set HUB_LIVE=1 to run live tests")


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    config.save({"model": "haiku", "web_enabled": False, "personality": "straight"})
    shutil.copytree(PACKS_DIR / "devteam", tmp_path / "packs" / "devteam")
    ps = PackStore(tmp_path / "packs", tmp_path / "hub.db")
    rs = RoleStore(tmp_path / "roles")
    port = free_port()
    srv, t = start_server(port, history=HistoryStore(tmp_path / "chats.db"), roles=rs,
                          gate_store=GateStore(tmp_path / "hub.db"), pack_store=ps,
                          team_store=TeamStore(tmp_path / "teams", roles_get=rs.get),
                          schedule_store=ScheduleStore(tmp_path / "hub.db"))
    assert call(port, "POST", "/api/packs/devteam/install")[0] == 200
    assert call(port, "PUT", "/api/rules", {"pack": "devteam", "platform": "devteam", "action": "run_tests",
                                            "mode": "auto", "daily_cap": 50})[0] == 200
    yield port, tmp_path
    srv.should_exit = True
    t.join(60)
    connectors.pack_dirs = lambda: {}
    packs_mod.active = None
    for d in claude_session.transcript_dirs()[1:]:
        for f in d.glob("*.jsonl"):
            delete_transcript(f.stem)


def call(port, method, path, body=None):
    resp, data = request(port, method, path, origin=None if method == "GET" else own(port), body=body, timeout=120)
    return resp.status, json.loads(data or b"{}")


def test_software_team_delivers_a_tested_program(hub):  # M12 check
    port, tmp = hub
    st, data = call(port, "POST", "/api/teams/devteam.software-team/runs", {"task":
        "Build a tiny Python module wordcount.py with one function count_words(text) that returns the number "
        "of words in the text (words are separated by whitespace). One branch is enough. It must have tests."})
    assert st == 200, data
    rid = data["run_id"]
    end = time.time() + 1500
    while time.time() < end:
        run = call(port, "GET", f"/api/runs/{rid}")[1]["run"]
        if run["status"] in ("done", "stopped", "failed"):
            break
        time.sleep(5)
    assert run["status"] == "done", run
    assert run["turns_used"] <= run["max_turns"]  # within budget
    folder = tmp / "projects" / f"team-{rid}"
    git = lambda *a: subprocess.run(["git", "-C", str(folder), *a], capture_output=True, text=True).stdout
    assert git("branch", "--show-current").strip() == "main"
    assert "Merge " in git("log", "--format=%s", "main")  # a branch was merged after tests and review
    assert (folder / "wordcount.py").is_file() and list(folder.glob("check_*.py"))
    from importlib import util
    spec = util.spec_from_file_location("wordcount_live", folder / "wordcount.py")
    mod = util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.count_words("one two  three\nfour") == 4  # the program really works
