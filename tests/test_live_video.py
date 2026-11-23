"""Live Milestone 13 check against real Claude. Skipped unless HUB_LIVE=1. No ComfyUI needed.

- P-VID-1: the script writer turns a one-line idea into SCRIPT.md and asks me to approve it. The
  approval shows the whole script and its fingerprint matches the file, so approving it unlocks renders.
- The rest of the M13 check (real renders and the final cut) needs ComfyUI and FFmpeg: yours to do.
"""

import json
import os
import shutil

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
    shutil.copytree(PACKS_DIR / "video", tmp_path / "packs" / "video")
    ps = PackStore(tmp_path / "packs", tmp_path / "hub.db")
    rs = RoleStore(tmp_path / "roles")
    port = free_port()
    srv, t = start_server(port, history=HistoryStore(tmp_path / "chats.db"), roles=rs,
                          gate_store=GateStore(tmp_path / "hub.db"), pack_store=ps,
                          team_store=TeamStore(tmp_path / "teams", roles_get=rs.get),
                          schedule_store=ScheduleStore(tmp_path / "hub.db"))
    assert call(port, "POST", "/api/packs/video/install")[0] == 200
    yield port, tmp_path, rs
    srv.should_exit = True
    t.join(60)
    connectors.pack_dirs = lambda: {}
    packs_mod.active = None
    for d in claude_session.transcript_dirs()[1:]:
        for f in d.glob("*.jsonl"):
            delete_transcript(f.stem)


def call(port, method, path, body=None, timeout=120):
    resp, data = request(port, method, path, origin=None if method == "GET" else own(port), body=body, timeout=timeout)
    return resp.status, data


def test_script_writer_asks_for_approval_of_the_exact_script(hub):  # P-VID-1
    port, tmp, rs = hub
    st, data = call(port, "POST", "/api/chat", {"text": "Idea: a lighthouse keeper's quiet morning, 20 seconds.",
                                                "chat_id": None, "role": "video.script-writer"}, timeout=600)
    assert st == 200, data
    events = [json.loads(line) for line in data.decode().splitlines() if line]
    assert not [e for e in events if e["kind"] == "error"], events
    folder = rs.get("video.script-writer").folder
    script = (folder / "SCRIPT.md").read_text(encoding="utf-8-sig")
    assert script.lstrip().startswith("#") and "shot01" in script.lower().replace(" ", "")
    st, data = call(port, "GET", "/api/approvals")
    items = [i for i in json.loads(data)["items"] if i["action_id"] == "video_tools.submit_script"]
    assert len(items) == 1, items
    assert script.strip() in items[0]["exact"]  # I see the whole script I approve
    st, data = call(port, "POST", f"/api/approvals/{items[0]['id']}/approve", {})
    assert st == 200, data
    status = connectors.get("video_tools.status").run({"folder": str(folder)})
    assert "Script approved: yes" in status, status
