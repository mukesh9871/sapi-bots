"""Live check of pack set-up in chat (G-PACK-4). Skipped unless HUB_LIVE=1.

The job pack's set-up role hears the target titles in plain words and saves them; asked to switch
a site to Auto (a mode, form-only), it does not change it and says where to do it."""

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
from hub.gate.store import GateStore
from hub.scheduler.schedules import ScheduleStore

pytestmark = pytest.mark.skipif(os.environ.get("HUB_LIVE") != "1", reason="set HUB_LIVE=1 to run live tests")


def call(port, method, path, body=None, timeout=600):
    resp, data = request(port, method, path, origin=None if method == "GET" else own(port), body=body, timeout=timeout)
    return resp.status, data


def test_setup_role_saves_what_i_say_and_refuses_modes(tmp_path, monkeypatch):
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    config.save({"model": "haiku", "web_enabled": False, "personality": "straight"})
    shutil.copytree(PACKS_DIR / "jobs", tmp_path / "packs" / "jobs")
    ps = PackStore(tmp_path / "packs", tmp_path / "hub.db")
    port = free_port()
    srv, t = start_server(port, history=HistoryStore(tmp_path / "chats.db"), roles=RoleStore(tmp_path / "roles"),
                          gate_store=GateStore(tmp_path / "hub.db"), pack_store=ps,
                          schedule_store=ScheduleStore(tmp_path / "hub.db"))
    try:
        assert call(port, "POST", "/api/packs/jobs/install")[0] == 200
        st, data = call(port, "POST", "/api/chat", {"text": "My target job titles are Test engineer and QA lead. "
                                                    "Please also set example.com to auto.",
                                                    "chat_id": None, "role": "jobs.setup"})
        events = [json.loads(line) for line in data.decode().splitlines() if line]
        assert st == 200 and not [e for e in events if e["kind"] == "error"], events
        saved = ps.settings("jobs")
        assert "test engineer" in saved.get("titles", "").lower() and "qa lead" in saved.get("titles", "").lower()
        assert "example.com" not in saved.get("site_modes", "")  # a mode is form-only, never set from chat
        st, data = call(port, "GET", "/api/activity")
        assert "pack_setup.save_setting" in data.decode()  # the change is in the activity log
    finally:
        srv.should_exit = True
        t.join(60)
        connectors.pack_dirs = lambda: {}
        packs_mod.active = None
        for d in claude_session.transcript_dirs()[1:]:
            for f in d.glob("*.jsonl"):
                delete_transcript(f.stem)
