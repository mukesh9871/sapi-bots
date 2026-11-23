"""Live Milestone 10 checks against real Claude (Haiku). Skipped unless HUB_LIVE=1.

- M10 check 1: the daily trend report runs and has links (P-SOC-2)
- M10 check 2: the publisher posts under Ask (held, then approved), then a routine announcement
  under Auto, on the demo channel. (A real LinkedIn post needs your account: see the hand-over.)
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
    monkeypatch.setenv("HUB_OUTBOX", str(tmp_path / "outbox"))
    config.save({"model": "haiku", "web_enabled": False, "personality": "straight"})
    shutil.copytree(PACKS_DIR / "social", tmp_path / "packs" / "social")
    for f in (tmp_path / "packs" / "social" / "roles").glob("*.md"):  # haiku everywhere, to keep it light
        f.write_text(f.read_text(encoding="utf-8").replace("model: sonnet", "model: haiku"), encoding="utf-8")
    ps = PackStore(tmp_path / "packs", tmp_path / "hub.db")
    store = ScheduleStore(tmp_path / "hub.db")
    port = free_port()
    srv, t = start_server(port, history=HistoryStore(tmp_path / "chats.db"), roles=RoleStore(tmp_path / "roles"),
                          gate_store=GateStore(tmp_path / "hub.db"), pack_store=ps, schedule_store=store,
                          scheduler_tick=0.5)
    assert call(port, "POST", "/api/packs/social/install")[0] == 200
    assert call(port, "POST", "/api/packs/social/settings", {"values": {
        "demo_niche": "Practical tips for home baking", "demo_voice": "Warm and short",
        "demo_audience": "Beginner bakers", "demo_avoid": "politics", "demo_announce_domains": "example.com"}})[0] == 200
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


def run_task(port, store, name, target, task):
    body = {"name": name, "kind": "role", "target": target, "task": task, "spec": {"type": "daily", "time": "03:00"}}
    sid = call(port, "POST", "/api/schedules", body)[1]["schedule"]["id"]
    assert call(port, "POST", f"/api/schedules/{sid}/run")[0] == 200
    end = time.time() + 300
    while time.time() < end:
        r = store.last_run(sid)
        if r and r["status"] != "running":
            return r
        time.sleep(1)
    raise AssertionError("timed out")


def test_daily_trend_report_has_links(hub):  # M10 check 1
    port, store, tmp = hub
    sch = next(s for s in call(port, "GET", "/api/schedules")[1]["schedules"] if s["name"] == "Daily trend report")
    assert call(port, "POST", f"/api/schedules/{sch['id']}/run")[0] == 200
    end = time.time() + 300
    while (r := store.last_run(sch["id"])) is None or r["status"] == "running":
        assert time.time() < end, "timed out"
        time.sleep(1)
    assert r["status"] == "done", r
    assert "http" in r["result"] and "bak" in r["result"].lower()


def test_post_under_ask_then_announcement_under_auto(hub):  # M10 check 2
    port, store, tmp = hub
    outbox = tmp / "outbox" / "social-demo.md"
    r = run_task(port, store, "Publish", "social.publisher",
                 "Post this on the demo channel exactly: \"Tip: let your dough rest 30 minutes before shaping.\"")
    assert r["status"] == "done", r
    items = call(port, "GET", "/api/approvals")[1]["items"]
    assert any(i["action_id"] == "social_demo.post" for i in items) and not outbox.exists()  # Ask: held
    for i in items:
        assert call(port, "POST", f"/api/approvals/{i['id']}/approve", {})[0] == 200
    assert "let your dough rest" in outbox.read_text(encoding="utf-8")  # posted once I approved
    assert call(port, "PUT", "/api/rules", {"pack": "social", "platform": "demo_social", "action": "post_announcement",
                                            "mode": "auto", "daily_cap": 3})[0] == 200
    r = run_task(port, store, "Announce", "social.publisher",
                 "Announce on the demo channel, as a routine announcement: title \"Sourdough basics video\", "
                 "line \"Twelve minutes, start to loaf.\", link https://example.com/sourdough")
    assert r["status"] == "done", r
    text = outbox.read_text(encoding="utf-8")
    assert "New: Sourdough basics video." in text and "https://example.com/sourdough" in text  # Auto: posted itself
