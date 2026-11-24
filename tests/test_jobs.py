"""Milestone 11 offline checks: the job applications pack (P-JOB-1..7). No Claude calls.

- M11 check 1: ten prepared applications reviewed by me with no invented facts, before any site is
  set to Auto. Offline: the fact check gate (nothing moves on without a pass), Prepare only by
  default, and sending only through Approvals. The ten real reviews are mine to do.
- M11 check 2: the tracker shows every application, and the bot hands over at a CAPTCHA.
"""

import asyncio
import json
import shutil

import pytest

from hub import connectors
from hub.chat import claude_session
from hub.engine import packs as packs_mod
from hub.engine import roles
from hub.engine.packs import PACKS_DIR, PackError, PackStore, install, read_pack
from hub.engine.roles import RoleStore
from hub.gate.gate import Gate
from hub.gate.store import GateStore
from hub.scheduler.schedules import ScheduleStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")
    root = tmp_path / "packs"
    shutil.copytree(PACKS_DIR / "jobs", root / "jobs")
    ps = PackStore(root, tmp_path / "hub.db")
    monkeypatch.setattr(connectors, "pack_dirs", ps.enabled_dirs)
    monkeypatch.setattr(packs_mod, "active", ps)
    gate = Gate(GateStore(tmp_path / "hub.db"), paused=lambda: False)
    ss = ScheduleStore(tmp_path / "hub.db")
    rs = RoleStore(tmp_path / "roles")
    rs.pack_dirs = ps.enabled_dirs
    install(ps, gate, ss, "jobs")
    resume = tmp_path / "resume.md"
    resume.write_text("# Sam Doe\nTest engineer, 6 years. Python, CAN bus, data logging.\n", encoding="utf-8")
    ps.set_settings("jobs", {"resume_file": str(resume), "skills": "Python, CAN bus", "titles": "Test engineer",
                             "exclude": "BadCorp", "site_modes": "jobs.example.com: auto\nspam.example: skip\nlinkedin.com: auto",
                             "email_from": "sam@example.com", "smtp_host": "smtp.example.com", "smtp_port": "587",
                             "smtp_user": "sam@example.com", "smtp_password": "app-pw"})
    return tmp_path, ps, gate, ss, rs


def tool(name):
    return connectors.get(name)


def fp(jid):
    """The fingerprint the job tool shows, as the checker and the filler pass it on."""
    return json.loads(tool("jobs_tools.job").run({"job": str(jid)}).split("\n", 1)[1])["fingerprint"]


def passed(jid):
    return tool("jobs_tools.fact_check").run({"job": str(jid), "result": "pass", "fingerprint": fp(jid)})


def track(company="Acme", title="Test engineer", url="https://jobs.example.com/1", **kw):
    return tool("jobs_tools.track_job").run({"company": company, "title": title, "url": url, **kw})


def test_jobs_pack_is_complete():
    pack = read_pack("jobs", PACKS_DIR / "jobs")
    assert pack.problem == "" and pack.views == [{"action": "jobs_tools.view_tracker", "label": "Tracker"}]
    assert pack.roles == ["application-filler", "fact-checker", "job-lead", "job-scout", "resume-writer", "setup"]


def test_install_roles_and_rules(env):
    tmp, ps, gate, ss, rs = env
    assert all(rs.get(f"jobs.{r}").problem == "" for r in read_pack("jobs", PACKS_DIR / "jobs").roles)
    assert gate.store.rule("jobs", "email", "send_application")["mode"] == "ask"  # G-APPR-3
    assert "jobs_email.send_application" not in rs.get("jobs.resume-writer").tools  # the writer cannot send


def test_profile_and_file_setting(env):  # P-JOB-1
    tmp, ps, gate, ss, rs = env
    text = tool("jobs_tools.profile").run({})
    assert "CAN bus, data logging" in text and "never add to them" in text and "BadCorp" in text
    for bad in ("relative.md", str(tmp / "missing.md"), str(tmp / "x.exe")):
        (tmp / "x.exe").write_text("x")
        with pytest.raises(PackError):
            ps.set_settings("jobs", {"resume_file": bad})


def test_track_job_never_twice_and_respects_lists(env):  # P-JOB-2, P-JOB-5, P-JOB-7
    assert "Tracked as job 1" in track(apply_method="email", apply_email="hr@acme.example")
    assert "Already in the tracker" in track(url="https://www.jobs.example.com/1/?ref=x")  # same address
    assert "Already in the tracker" in track(url="https://other.example/9")  # same company and title
    with pytest.raises(Exception, match="exclude list"):
        track(company="BadCorp", title="x", url="https://jobs.example.com/2")
    with pytest.raises(Exception, match="skips this site"):
        track(company="Spam", title="x", url="https://spam.example/3")
    assert "Prepare only" in track(company="New", title="x", url="https://unknown.example/4")  # unknown: Prepare
    assert "Prepare only" in track(company="LI", title="x", url="https://www.linkedin.com/jobs/5")  # always Prepare


def test_fact_check_gate_and_prepare_only_flow(env):  # P-JOB-3, P-JOB-4
    track(company="Beta", title="QA", url="https://unknown.example/7")
    with pytest.raises(Exception, match="fact check"):
        tool("jobs_tools.mark_prepared").run({"job": "1"})  # nothing moves on before the check
    tool("jobs_tools.save_documents").run({"job": "1", "resume": "Sam, Python", "cover_letter": "Dear Beta"})
    with pytest.raises(Exception, match="say which claims"):
        tool("jobs_tools.fact_check").run({"job": "1", "result": "fail", "fingerprint": fp(1)})
    tool("jobs_tools.fact_check").run({"job": "1", "result": "fail", "notes": "PhD is not in the profile", "fingerprint": fp(1)})
    with pytest.raises(Exception, match="fact check"):
        tool("jobs_tools.mark_prepared").run({"job": "1"})
    tool("jobs_tools.save_documents").run({"job": "1", "resume": "Sam, Python", "cover_letter": "Dear Beta"})
    passed("1")
    assert "ready for the user" in tool("jobs_tools.mark_prepared").run({"job": "1"})
    folder = json.loads(tool("jobs_tools.view_tracker").run({}))["rows"][0][8]
    assert "Dear Beta" in (__import__("pathlib").Path(folder) / "cover-letter.md").read_text(encoding="utf-8")


def test_hand_over_at_a_captcha_shows_in_the_tracker(env):  # M11 check 2, P-JOB-6
    track(company="Gamma", title="Engineer", url="https://unknown.example/8")
    assert "Stop working on it" in tool("jobs_tools.hand_over").run({"job": "1", "reason": "The site shows a CAPTCHA"})
    table = json.loads(tool("jobs_tools.view_tracker").run({}))
    assert table["columns"][:5] == ["#", "Company", "Title", "Site", "Status"]
    assert table["rows"][0][4] == "needs you (The site shows a CAPTCHA)"


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.tls = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, password):
        assert self.tls, "never log in before encryption"
        self.login_as = (user, password)

    def send_message(self, msg):
        FakeSMTP.sent.append((self.host, self.login_as, msg))


@pytest.fixture
def smtp(env, monkeypatch):
    import smtplib
    FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return env


def checked_email_job(company="Acme", url="https://jobs.example.com/1"):
    out = track(company=company, url=url, apply_method="email", apply_email=f"hr@{company.lower()}.example")
    jid = out.split("job ")[1].split(" ")[0]
    tool("jobs_tools.save_documents").run({"job": jid, "resume": "Sam: Python, CAN bus", "cover_letter": f"Dear {company}"})
    passed(jid)
    return jid


def test_email_application_through_approvals_once(smtp):  # P-JOB-4, P-JOB-7, G-APPR-2
    tmp, ps, gate, ss, rs = smtp
    jid = checked_email_job()
    send = claude_session._gated_tool(tool("jobs_email.send_application"), gate, "Application filler", "jobs")
    asyncio.run(send.handler({"job": jid, "fingerprint": fp(jid)}))  # Ask: held, nothing sent
    assert FakeSMTP.sent == []
    item = gate.store.approvals()[0]
    preview = gate.lookup(item["action_id"]).preview(item["payload"])
    assert "Email to hr@acme.example from sam@example.com" in preview and "Dear Acme" in preview
    gate.approve(item["id"])
    host, login, msg = FakeSMTP.sent[0]
    assert host == "smtp.example.com" and login == ("sam@example.com", "app-pw") and msg["To"] == "hr@acme.example"
    assert msg.get_body().get_content().strip() == "Dear Acme"
    assert [a.get_filename() for a in msg.iter_attachments()] == ["resume.md"]
    row = json.loads(tool("jobs_tools.view_tracker").run({}))["rows"][0]
    assert row[4].startswith("applied") and row[6] and row[7]  # applied date and follow-up date
    out = gate.call("jobs_email.send_application", {"job": jid, "fingerprint": fp(jid)}, "Application filler", "jobs")
    assert out.is_error and "never twice" in out.text and len(FakeSMTP.sent) == 1


def test_email_refused_for_prepare_sites_and_unchecked_jobs(smtp):  # P-JOB-3, P-JOB-5
    tmp, ps, gate, ss, rs = smtp
    track(company="Delta", title="x", url="https://unknown.example/1", apply_method="email", apply_email="a@d.example")
    out = gate.call("jobs_email.send_application", {"job": "1", "fingerprint": fp(1)}, "Application filler", "jobs")
    assert out.is_error and "fact check" in out.text
    tool("jobs_tools.save_documents").run({"job": "1", "resume": "r", "cover_letter": "c"})
    passed("1")
    out = gate.call("jobs_email.send_application", {"job": "1", "fingerprint": fp(1)}, "Application filler", "jobs")
    assert out.is_error and "Prepare only" in out.text  # unknown sites are Prepare only
    li = track(company="LI", title="x", url="https://www.linkedin.com/jobs/2", apply_method="email", apply_email="a@li.example")
    jid = li.split("job ")[1].split(" ")[0]
    tool("jobs_tools.save_documents").run({"job": jid, "resume": "r", "cover_letter": "c"})
    passed(jid)
    out = gate.call("jobs_email.send_application", {"job": jid, "fingerprint": fp(jid)}, "Application filler", "jobs")
    assert out.is_error and "Prepare only" in out.text and FakeSMTP.sent == []  # LinkedIn: never automated
    assert gate.store.pending_count() == 0  # refused, never held


def test_password_is_never_in_the_tracker_or_db(smtp, memory_keyring):
    tmp, ps, gate, ss, rs = smtp
    assert "app-pw" not in (tmp / "hub.db").read_bytes().decode("latin-1")
    tracker = next((tmp / "pack-data" / "jobs").glob("tracker.db"), None)
    if tracker:
        assert "app-pw" not in tracker.read_bytes().decode("latin-1")
    assert memory_keyring.store[("sapi-ai-hub", "jobs/smtp_password")] == "app-pw"


def test_ten_prepared_before_auto(smtp):  # M11 check 1, the offline part
    tmp, ps, gate, ss, rs = smtp
    ps.set_settings("jobs", {"site_modes": ""})  # no site on Auto yet: everything is Prepare only
    for n in range(10):
        track(company=f"Co{n}", title="Engineer", url=f"https://jobs.example.com/{n}")
        tool("jobs_tools.save_documents").run({"job": str(n + 1), "resume": "r", "cover_letter": "c"})
        passed(str(n + 1))
        tool("jobs_tools.mark_prepared").run({"job": str(n + 1)})
    rows = json.loads(tool("jobs_tools.view_tracker").run({}))["rows"]
    assert len(rows) == 10 and all(r[3] == "Prepare only" and r[4].startswith("ready for you") for r in rows)
    assert FakeSMTP.sent == []  # nothing left the PC


def test_api_pack_view_shows_the_tracker(env, monkeypatch):  # M11 check 2 through the window's API
    from helpers.hubhttp import free_port, own, request, start_server
    from hub import config
    from hub.chat.history import HistoryStore
    tmp, ps, gate, ss, rs = env
    monkeypatch.setattr(config, "CONFIG_PATH", tmp / "config.json")
    monkeypatch.setattr(claude_session, "CHAT_WORKDIR", tmp / "chat")
    track(company="Omega", title="Engineer", url="https://unknown.example/1")
    port = free_port()
    srv, t = start_server(port, history=HistoryStore(tmp / "chats.db"), roles=rs, gate_store=gate.store,
                          pack_store=ps, schedule_store=ss)
    try:
        def call(body):
            resp, data = request(port, "POST", "/api/packs/jobs/view", origin=own(port), body=body)
            return resp.status, json.loads(data or b"{}")
        st, table = call({"action": "jobs_tools.view_tracker"})
        assert st == 200 and table["rows"][0][1] == "Omega" and table["columns"][0] == "#"
        assert call({"action": "jobs_tools.profile"})[0] == 404  # only declared views
        assert call({"action": "jobs_email.send_application"})[0] == 404  # never an acting action
        def row(action):
            resp, data = request(port, "POST", "/api/packs/jobs/row", origin=own(port), body={"action": action, "row": "1"})
            return resp.status, json.loads(data or b"{}")
        assert row("jobs_tools.hand_over")[0] == 404  # only declared buttons
        assert row("jobs_email.send_application")[0] == 404  # never an outside action
        st, out = row("jobs_tools.mark_submitted")
        assert st == 200 and "marked as submitted" in out["result"]
        assert call({"action": "jobs_tools.view_tracker"})[1]["rows"][0][4].startswith("applied")
        packs_list = json.loads(request(port, "GET", "/api/packs")[1])["packs"]
        assert next(p for p in packs_list if p["id"] == "jobs")["views"][0]["label"] == "Tracker"
    finally:
        srv.should_exit = True
        t.join(5)



def test_approval_pins_the_documents(smtp):  # review M11-1 finding 1 (G-APPR-2)
    tmp, ps, gate, ss, rs = smtp
    jid = checked_email_job()
    gate.call("jobs_email.send_application", {"job": jid, "fingerprint": fp(jid)}, "Application filler", "jobs")
    item = gate.store.approvals()[0]
    preview = gate.lookup(item["action_id"]).preview(item["payload"])
    assert "Sam: Python, CAN bus" in preview  # the whole resume is shown, not only its length
    tool("jobs_tools.save_documents").run({"job": jid, "resume": "new", "cover_letter": "Totally different letter"})
    passed(jid)
    with pytest.raises(Exception, match="documents changed"):
        gate.approve(item["id"])
    assert FakeSMTP.sent == []


def test_checker_must_have_read_the_current_documents(env):  # review M11-1 finding 1
    track(company="Beta", title="QA", url="https://unknown.example/7")
    tool("jobs_tools.save_documents").run({"job": "1", "resume": "r", "cover_letter": "c"})
    old = fp(1)
    tool("jobs_tools.save_documents").run({"job": "1", "resume": "r2", "cover_letter": "c2"})
    with pytest.raises(Exception, match="changed since you read"):
        tool("jobs_tools.fact_check").run({"job": "1", "result": "pass", "fingerprint": old})


def test_never_twice_even_after_a_hand_over_or_two_approvals(smtp):  # review M11-1 finding 2
    import threading
    import time as _time
    tmp, ps, gate, ss, rs = smtp
    jid = checked_email_job()
    gate.store.set_rule("jobs", "email", "send_application", "auto", 10)
    gate.call("jobs_email.send_application", {"job": jid, "fingerprint": fp(jid)}, "Application filler", "jobs")
    assert len(FakeSMTP.sent) == 1
    with pytest.raises(Exception, match="already applied"):
        tool("jobs_tools.hand_over").run({"job": jid, "reason": "x"})
    with pytest.raises(Exception, match="already prepared or applied"):
        tool("jobs_tools.save_documents").run({"job": jid, "resume": "r", "cover_letter": "c"})
    # two calls for one job at the same moment ("2" and "02" are the same job)
    jid2 = checked_email_job(company="Zeta", url="https://jobs.example.com/z")
    slow = FakeSMTP.send_message

    def slow_send(self, msg):
        _time.sleep(0.5)
        slow(self, msg)

    FakeSMTP.send_message = slow_send
    try:
        f = fp(jid2)
        threads = [threading.Thread(target=gate.call, args=("jobs_email.send_application", {"job": j, "fingerprint": f},
                                                             "Application filler", "jobs")) for j in (jid2, "0" + jid2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
    finally:
        FakeSMTP.send_message = slow
    assert len(FakeSMTP.sent) == 2  # the first job once, Zeta once


def test_job_ids_in_the_query_are_different_jobs(env):  # review M11-1 finding 4
    assert "Tracked" in track(company="A", title="x", url="https://www.indeed.com/viewjob?jk=111&utm_source=feed")
    assert "Tracked" in track(company="B", title="y", url="https://www.indeed.com/viewjob?jk=222")
    assert "Already" in track(company="C", title="z", url="https://indeed.com/viewjob?utm_medium=x&jk=111")


def test_site_list_rules(env):  # review M11-1 findings 6, 7
    tmp, ps, gate, ss, rs = env
    ps.set_settings("jobs", {"site_modes": "example.com: auto\ncareers.example.com: prepare\nlinkedin.com: skip"})
    with pytest.raises(Exception, match="skips this site"):
        track(company="L", title="x", url="https://www.linkedin.com/jobs/1")  # Skip wins over forced Prepare
    assert "Prepare only" in track(company="C", title="x", url="https://careers.example.com/1")  # most specific
    assert "Auto" in track(company="E", title="x", url="https://jobs.example.com/2")


def test_tracker_buttons_for_me(env):  # review M11-1 finding 5 (P-JOB-7)
    track(company="Self", title="x", url="https://unknown.example/s")
    assert "marked as submitted" in tool("jobs_tools.mark_submitted").run({"job": "1"})
    row = json.loads(tool("jobs_tools.view_tracker").run({}))["rows"][0]
    assert row[4].startswith("applied") and row[6] and row[7]
    track(company="Hand", title="y", url="https://unknown.example/h")
    tool("jobs_tools.hand_over").run({"job": "2", "reason": "CAPTCHA"})
    assert "back on the shortlist" in tool("jobs_tools.reopen").run({"job": "2"})
    pack = read_pack("jobs", PACKS_DIR / "jobs")
    assert [a["action"] for a in pack.row_actions] == ["jobs_tools.mark_submitted", "jobs_tools.reopen"]
    assert not any("mark_submitted" in t for r in pack.roles
                   for t in (PACKS_DIR / "jobs" / "roles" / f"{r}.md").read_text(encoding="utf-8").split())


def test_failures_before_sending_reset_and_never_quote_the_password(smtp, monkeypatch):  # review M11-2 M1, M2
    import smtplib
    tmp, ps, gate, ss, rs = smtp
    gate.store.set_rule("jobs", "email", "send_application", "auto", 10)
    jid = checked_email_job()

    def bad_login(self, user, password):
        raise UnicodeEncodeError("ascii", password, 3, 4, "ordinal not in range")

    monkeypatch.setattr(FakeSMTP, "login", bad_login)
    out = gate.call("jobs_email.send_application", {"job": jid, "fingerprint": fp(jid)}, "Application filler", "jobs")
    assert out.is_error and "characters the mail login cannot send" in out.text and "app-pw" not in out.text
    assert "position" not in json.dumps(gate.store.activity())  # no part of the password in the log
    assert json.loads(tool("jobs_tools.job").run({"job": jid}).split("\n", 1)[1])["status"] == "checked"  # retry ok

    def refused(self, host, port, timeout=None):
        raise ConnectionRefusedError("offline")

    monkeypatch.setattr(FakeSMTP, "__init__", refused)
    with pytest.raises(Exception, match="could not be reached"):  # the connector itself (the gate holds a repeat)
        tool("jobs_email.send_application").run({"job": jid, "fingerprint": fp(jid)})
    assert json.loads(tool("jobs_tools.job").run({"job": jid}).split("\n", 1)[1])["status"] == "checked"


def test_interrupted_send_stays_until_i_reopen_it(smtp, monkeypatch):  # review M11-2 M2
    tmp, ps, gate, ss, rs = smtp
    gate.store.set_rule("jobs", "email", "send_application", "auto", 10)
    jid = checked_email_job()

    def cut(self, msg):
        raise OSError("connection lost")

    monkeypatch.setattr(FakeSMTP, "send_message", cut)
    out = gate.call("jobs_email.send_application", {"job": jid, "fingerprint": fp(jid)}, "Application filler", "jobs")
    assert out.is_error and "may have gone out" in out.text
    assert json.loads(tool("jobs_tools.job").run({"job": jid}).split("\n", 1)[1])["status"] == "sending"
    assert "can be sent again" in tool("jobs_tools.reopen").run({"job": jid})  # I checked: it did not go out


def test_my_buttons_are_never_a_roles_tool(env):  # review M11-2 L2
    from hub.engine.roles import catalog, check
    assert "jobs_tools.mark_submitted" not in catalog() and "jobs_tools.reopen" not in catalog()
    assert "not allowed" in check("R", "", ["jobs_tools.mark_submitted"], "x")


def test_same_site_twice_skip_wins(env):  # review M11-2 L1
    tmp, ps, gate, ss, rs = env
    ps.set_settings("jobs", {"site_modes": "example.com: auto\nwww.example.com: skip"})
    with pytest.raises(Exception, match="skips this site"):
        track(company="S", title="x", url="https://example.com/1")


def test_huge_job_number_is_refused_and_logged(env):  # review M11-3 L1 (G-LOG-1)
    tmp, ps, gate, ss, rs = env
    out = gate.call("jobs_email.send_application", {"job": "9" * 5000, "fingerprint": "x"}, "Filler", "jobs")
    assert out.is_error and gate.store.activity()[0]["decision"] == "blocked"


def test_site_mode_is_read_now(env):  # review M11-3 L3
    tmp, ps, gate, ss, rs = env
    ps.set_settings("jobs", {"site_modes": ""})
    track(company="Late", title="x", url="https://jobs.example.com/late")
    ps.set_settings("jobs", {"site_modes": "jobs.example.com: auto"})  # I set the site to Auto afterwards
    assert json.loads(tool("jobs_tools.job").run({"job": "1"}).split("\n", 1)[1])["site_mode"] == "auto"
    assert json.loads(tool("jobs_tools.view_tracker").run({}))["rows"][0][3] == "Auto"
