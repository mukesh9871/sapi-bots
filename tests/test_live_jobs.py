"""Live Milestone 11 checks against real Claude (Haiku). Skipped unless HUB_LIVE=1.

- P-JOB-3: the fact checker catches an invented claim (a degree the profile does not have)
- P-JOB-4/6, M11 check 2: the filler prepares a Prepare only job, and hands over at a CAPTCHA
(The ten real reviews before any Auto, M11 check 1, are yours: see the hand-over.)
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
    shutil.copytree(PACKS_DIR / "jobs", tmp_path / "packs" / "jobs")
    for f in (tmp_path / "packs" / "jobs" / "roles").glob("*.md"):  # haiku everywhere, to keep it light
        f.write_text(f.read_text(encoding="utf-8").replace("model: sonnet", "model: haiku"), encoding="utf-8")
    ps = PackStore(tmp_path / "packs", tmp_path / "hub.db")
    store = ScheduleStore(tmp_path / "hub.db")
    port = free_port()
    srv, t = start_server(port, history=HistoryStore(tmp_path / "chats.db"), roles=RoleStore(tmp_path / "roles"),
                          gate_store=GateStore(tmp_path / "hub.db"), pack_store=ps, schedule_store=store,
                          scheduler_tick=0.5)
    resume = tmp_path / "resume.md"
    resume.write_text("# Sam Doe\nTest engineer, 6 years at Volt GmbH. Python, CAN bus, data logging.\n"
                      "Education: BSc Electrical Engineering, TU Munich.\n", encoding="utf-8")
    assert call(port, "POST", "/api/packs/jobs/install")[0] == 200
    assert call(port, "POST", "/api/packs/jobs/settings", {"values": {"resume_file": str(resume),
                "skills": "Python, CAN bus, data logging", "titles": "Test engineer"}})[0] == 200
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


def run_role(port, store, target, task):
    body = {"name": f"Live {target}", "kind": "role", "target": target, "task": task,
            "spec": {"type": "daily", "time": "03:00"}}
    sid = call(port, "POST", "/api/schedules", body)[1]["schedule"]["id"]
    assert call(port, "POST", f"/api/schedules/{sid}/run")[0] == 200
    end = time.time() + 300
    while time.time() < end:
        r = store.last_run(sid)
        if r and r["status"] != "running":
            return r
        time.sleep(1)
    raise AssertionError("timed out")


def tracker(port):
    return call(port, "POST", "/api/packs/jobs/view", {"action": "jobs_tools.view_tracker"})[1]["rows"]


def test_fact_checker_catches_an_invented_degree(hub):  # P-JOB-3
    port, store, tmp = hub
    track = connectors.get("jobs_tools.track_job")
    track.run({"company": "Acme", "title": "Test engineer", "url": "https://jobs.acme.example/1"})
    connectors.get("jobs_tools.save_documents").run({"job": "1",
        "resume": "Sam Doe. Test engineer, 6 years at Volt GmbH. Python, CAN bus. PhD in Physics, MIT.",
        "cover_letter": "Dear Acme, with my PhD from MIT and six years of CAN bus testing I would like to join you."})
    r = run_role(port, store, "jobs.fact-checker", "Check the documents of job 1 against the profile and record the result.")
    assert r["status"] == "done", r
    row = tracker(port)[0]
    assert row[4].startswith("needs fixing"), (row, r["result"][:1500])  # the invented PhD was caught


def test_filler_prepares_and_hands_over_at_a_captcha(hub):  # P-JOB-4, P-JOB-6, M11 check 2
    port, store, tmp = hub
    track = connectors.get("jobs_tools.track_job")
    for n, company in enumerate(("Beta", "Gamma"), 1):
        track.run({"company": company, "title": "Test engineer", "url": f"https://jobs.{company.lower()}.example/{n}"})
        connectors.get("jobs_tools.save_documents").run({"job": str(n), "resume": "Sam Doe, Python, CAN bus.",
                                                          "cover_letter": f"Dear {company}, I test CAN bus systems."})
        fp = json.loads(connectors.get("jobs_tools.job").run({"job": str(n)}).split("\n", 1)[1])["fingerprint"]
        connectors.get("jobs_tools.fact_check").run({"job": str(n), "result": "pass", "fingerprint": fp})
    r = run_role(port, store, "jobs.application-filler", "Finish job 1.")
    assert r["status"] == "done", r
    r = run_role(port, store, "jobs.application-filler",
                 "Finish job 2. The employer's application page shows a CAPTCHA before the form.")
    assert r["status"] == "done", r
    rows = {row[1]: row[4] for row in tracker(port)}
    assert rows["Beta"].startswith("ready for you"), rows
    assert rows["Gamma"].startswith("needs you") and "captcha" in rows["Gamma"].lower(), rows
