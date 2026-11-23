"""Live Milestone 14 check against real Claude. Skipped unless HUB_LIVE=1.

- P-PIPE-7: I tell the Pipeline manager role in plain words what to queue; it adds exactly those
  items to the right channel, and the queue service asks for the first start in Approvals with the
  exact command (P-PIPE-5). Then I ask what is waiting, and it answers from the queue.
- The rest of the M14 check (my real repos, a run of 12 hours or more) is yours to do.
"""

import json
import os
import shutil
import sys
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
    shutil.copytree(PACKS_DIR / "pipeline", tmp_path / "packs" / "pipeline")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("print('rendering')\n", encoding="utf-8")
    ps = PackStore(tmp_path / "packs", tmp_path / "hub.db")
    rs = RoleStore(tmp_path / "roles")
    port = free_port()
    srv, t = start_server(port, history=HistoryStore(tmp_path / "chats.db"), roles=rs,
                          gate_store=GateStore(tmp_path / "hub.db"), pack_store=ps,
                          schedule_store=ScheduleStore(tmp_path / "hub.db"), scheduler_tick=1)
    assert call(port, "POST", "/api/packs/pipeline/install")[0] == 200
    chan = (f"[reelwright]\nfolder: {repo}\ncommand: \"{sys.executable}\" main.py --input {{input}}\n"
            f"output: output/**/*.mp4\ngpu: yes\n")
    assert call(port, "POST", "/api/packs/pipeline/settings", {"values": {"channels": chan}})[0] == 200
    yield port, tmp_path
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


def chat(port, text, chat_id=None):
    st, data = call(port, "POST", "/api/chat", {"text": text, "chat_id": chat_id, "role": "pipeline.pipeline-manager"},
                    timeout=600)
    assert st == 200, data
    events = [json.loads(line) for line in data.decode().splitlines() if line]
    assert not [e for e in events if e["kind"] == "error"], events
    return events


def test_manager_queues_from_plain_words_and_reports(hub):  # P-PIPE-7, P-PIPE-5
    port, tmp = hub
    events = chat(port, "Please queue stories/abc_song.yaml and stories/ancient_night.yaml for the reelwright channel.")
    queue = json.loads(call(port, "POST", "/api/packs/pipeline/view", {"action": "pipeline_tools.view_queue"})[1])
    items = [r[2] for r in queue["rows"] if r[0] != ""]
    assert items == ["stories/abc_song.yaml", "stories/ancient_night.yaml"], queue["rows"]
    held = []
    for _ in range(20):  # the service (not the agent) asks for the first start
        held = [i for i in json.loads(call(port, "GET", "/api/approvals")[1])["items"]
                if i["action_id"] == "pipeline_tools.start_run"]
        if held:
            break
        time.sleep(0.5)
    assert len(held) == 1 and "main.py --input stories/abc_song.yaml" in held[0]["preview"]
    assert held[0]["agent"] == "pipeline service"
    chat_id = next(e["id"] for e in events if e["kind"] == "chat")
    events = chat(port, "What is waiting right now?", chat_id)
    reply = "".join(e.get("text", "") for e in events if e["kind"] == "text").lower()
    assert "ancient_night" in reply, reply
