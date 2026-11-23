"""Milestone 10 offline checks: the social media manager pack (P-SOC-1..8). No Claude calls.

- M10 check 1: the daily trend report and the weekly calendar arrive on schedule (simulated
  with a fake clock; the schedules come from the pack).
- M10 check 2: one platform posts through its official API under Ask, then one routine post type
  under Auto. LinkedIn's Posts API is checked against a local stand-in server (headers, body,
  token, errors); the demo channel runs the whole flow. A real LinkedIn post needs your account.
"""

import asyncio
import json
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from hub import connectors
from hub.chat import claude_session
from hub.engine import packs as packs_mod
from hub.engine import roles
from hub.engine.packs import PACKS_DIR, PackError, PackStore, install, read_pack, remove
from hub.engine.roles import RoleStore
from hub.gate.gate import Gate
from hub.gate.store import GateStore
from hub.scheduler.runner import Scheduler
from hub.scheduler.schedules import ScheduleStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")
    monkeypatch.setenv("HUB_OUTBOX", str(tmp_path / "outbox"))
    root = tmp_path / "packs"
    shutil.copytree(PACKS_DIR / "social", root / "social")
    ps = PackStore(root, tmp_path / "hub.db")
    monkeypatch.setattr(connectors, "pack_dirs", ps.enabled_dirs)
    monkeypatch.setattr(packs_mod, "active", ps)
    gate = Gate(GateStore(tmp_path / "hub.db"), paused=lambda: False)
    ss = ScheduleStore(tmp_path / "hub.db")
    rs = RoleStore(tmp_path / "roles")
    rs.pack_dirs = ps.enabled_dirs
    install(ps, gate, ss, "social")
    return tmp_path, ps, gate, ss, rs


def test_social_pack_is_complete():  # P-SOC roles, schedules, suggested rules
    pack = read_pack("social", PACKS_DIR / "social")
    assert pack.problem == ""
    assert pack.roles == ["analytics-reporter", "content-writer", "publisher", "social-lead", "trend-analyst"]
    assert {s["name"] for s in pack.schedules} == {"Daily trend report", "Weekly content calendar",
                                                  "Weekly analytics summary"}
    rules = {r["action"]: r["mode"] for r in pack.rules}
    assert rules["social_linkedin.post"] == "ask" and rules["social_linkedin.post_announcement"] == "auto"


def test_rules_install_as_ask_and_roles_work(env):  # P-SOC-5, G-APPR-3
    tmp, ps, gate, ss, rs = env
    for action in ("post", "post_announcement"):
        assert gate.store.rule("social", "linkedin", action)["mode"] == "ask"
        assert gate.store.rule("social", "demo_social", action)["mode"] == "ask"
    assert all(rs.get(f"social.{r}").problem == "" for r in read_pack("social", PACKS_DIR / "social").roles)
    assert "social_linkedin.post" not in rs.get("social.content-writer").tools  # the writer only drafts (P-SOC-4)


def test_brand_profile_per_channel(env):  # P-SOC-1
    tmp, ps, gate, ss, rs = env
    brand = connectors.get("social_tools.brand")
    assert "no brand profile" in brand.run({"channel": "linkedin"})
    ps.set_settings("social", {"linkedin_niche": "Industrial data logging", "linkedin_voice": "Calm, expert",
                               "linkedin_audience": "Test engineers\nin automotive", "linkedin_avoid": "politics, AI"})
    text = brand.run({"channel": "linkedin"})
    assert "Industrial data logging" in text and "politics, AI" in text and "information" in text
    with pytest.raises(Exception):
        brand.run({"channel": "tiktok"})


def test_never_topics_are_refused_by_whole_word(env):  # P-SOC-1
    tmp, ps, gate, ss, rs = env
    ps.set_settings("social", {"demo_avoid": "politics, AI"})
    post = connectors.get("social_demo.post")
    with pytest.raises(Exception, match="never posts about"):
        post.run({"text": "My view on Politics today"})
    with pytest.raises(Exception, match="never posts about"):
        post.run({"text": "How AI changes testing"})
    assert post.run({"text": "She said the test rig was ready."}).startswith("Posted")  # "said" is not "AI"


def test_secret_is_kept_in_credential_manager_only(env, memory_keyring):  # hard rule: logins in keyring
    tmp, ps, gate, ss, rs = env
    view = ps.set_settings("social", {"linkedin_token": "tok-SECRET-123"})
    assert view["linkedin_token"] == "(set)"
    assert memory_keyring.store[("sapi-ai-hub", "social/linkedin_token")] == "tok-SECRET-123"
    assert "tok-SECRET" not in json.dumps(ps.settings("social"))  # never in hub.db
    assert "tok-SECRET" not in (tmp / "hub.db").read_bytes().decode("latin-1")
    assert packs_mod.secret("social", "linkedin_token") == "tok-SECRET-123"
    ps.set_settings("social", {"linkedin_token": ""})  # Forget
    assert packs_mod.secret("social", "linkedin_token") == ""
    ps.set_settings("social", {"linkedin_token": "again"})
    remove(ps, gate, ss, "social")
    assert ("sapi-ai-hub", "social/linkedin_token") not in memory_keyring.store  # removing the pack forgets it


# ---- LinkedIn: the official Posts API against a local stand-in --------------------------------------

class FakeLinkedIn(BaseHTTPRequestHandler):
    seen: list = []
    answer = 201

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeLinkedIn.seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
        self.send_response(FakeLinkedIn.answer)
        if FakeLinkedIn.answer == 201:
            self.send_header("x-restli-id", "urn:li:share:7000000000000000001")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def linkedin(env, monkeypatch):
    FakeLinkedIn.seen, FakeLinkedIn.answer = [], 201
    srv = HTTPServer(("127.0.0.1", 0), FakeLinkedIn)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("HUB_LINKEDIN_API", f"http://127.0.0.1:{srv.server_port}")
    yield env
    srv.shutdown()


def test_linkedin_posts_through_the_official_api(linkedin):  # P-SOC-6
    tmp, ps, gate, ss, rs = linkedin
    post = connectors.get("social_linkedin.post")
    with pytest.raises(Exception, match="member URN"):
        post.run({"text": "Hello"})
    ps.set_settings("social", {"linkedin_author": "urn:li:person:abc123"})
    with pytest.raises(Exception, match="not connected"):
        post.run({"text": "Hello"})
    ps.set_settings("social", {"linkedin_token": "tok-XYZ"})
    assert post.run({"text": "Hello LinkedIn"}) == "Posted on LinkedIn (urn:li:share:7000000000000000001)"
    req = FakeLinkedIn.seen[-1]
    h = {k.lower(): v for k, v in req["headers"].items()}
    assert req["path"] == "/rest/posts" and h["authorization"] == "Bearer tok-XYZ"
    assert h["linkedin-version"].isdigit() and len(h["linkedin-version"]) == 6 and h["x-restli-protocol-version"] == "2.0.0"
    assert req["body"] == {"author": "urn:li:person:abc123", "commentary": "Hello LinkedIn", "visibility": "PUBLIC",
                           "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                                            "thirdPartyDistributionChannels": []},
                           "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}


def test_linkedin_errors_are_plain_and_never_show_the_token(linkedin):
    tmp, ps, gate, ss, rs = linkedin
    ps.set_settings("social", {"linkedin_author": "urn:li:person:abc123", "linkedin_token": "tok-NEVER-SHOWN"})
    FakeLinkedIn.answer = 401
    with pytest.raises(Exception) as exc:
        connectors.get("social_linkedin.post").run({"text": "Hello"})
    assert "expired" in str(exc.value) and "tok-NEVER" not in str(exc.value)


def test_ask_then_auto_on_one_platform(linkedin):  # M10 check 2: Ask first, then one routine type on Auto
    tmp, ps, gate, ss, rs = linkedin
    ps.set_settings("social", {"linkedin_author": "urn:li:person:abc123", "linkedin_token": "tok",
                               "linkedin_announce_domains": "example.com"})
    general = claude_session._gated_tool(connectors.get("social_linkedin.post"), gate, "Publisher", "social")
    ann = claude_session._gated_tool(connectors.get("social_linkedin.post_announcement"), gate, "Publisher", "social")
    # Ask: held, nothing reaches LinkedIn; the approval shows exactly what will be posted
    asyncio.run(ann.handler({"title": "My new test-rig video", "line": "Ten minutes, start to finish.",
                             "link": "https://example.com/v/1"}))
    item = gate.store.approvals()[0]
    preview = gate.lookup(item["action_id"]).preview(item["payload"])
    assert "Will publish exactly:\nNew: My new test-rig video. Ten minutes, start to finish. https://example.com/v/1" in preview
    assert FakeLinkedIn.seen == []
    gate.approve(item["id"])  # I approve it: now it posts
    assert FakeLinkedIn.seen[-1]["body"]["commentary"].startswith("New: My new test-rig video.")
    # I switch the routine post type to Auto (my choice, in Settings): the next announcement posts by itself
    gate.store.set_rule("social", "linkedin", "post_announcement", "auto", 1)
    out = asyncio.run(ann.handler({"title": "Part two", "line": "", "link": "https://example.com/v/2"}))
    assert not out["is_error"] and FakeLinkedIn.seen[-1]["body"]["commentary"] == "New: Part two. https://example.com/v/2"
    # a general post still asks (P-SOC-5)
    asyncio.run(general.handler({"text": "My opinion on test standards"}))
    assert len(FakeLinkedIn.seen) == 2 and gate.store.pending_count() == 1
    # the daily cap of 1 stops a runaway loop of announcements (G-APPR-4)
    asyncio.run(ann.handler({"title": "Part three", "line": "", "link": "https://example.com/v/3"}))
    assert len(FakeLinkedIn.seen) == 2


def test_announcement_link_and_fields_are_checked(env):
    tmp, ps, gate, ss, rs = env
    with pytest.raises(Exception, match="https"):
        connectors.get("social_linkedin.post_announcement").run({"title": "x", "line": "", "link": "javascript:alert(1)"})
    preview = connectors.get("social_demo.post_announcement").preview({"title": "x", "line": "", "link": "nope"})
    assert "cannot be published" in preview


def test_announcement_cannot_smuggle_free_text(env):  # review M10-1 finding 3 (P-SOC-5)
    tmp, ps, gate, ss, rs = env
    ann = connectors.get("social_linkedin.post_announcement")
    with pytest.raises(Exception, match="sites set on the Packs page"):
        ann.show({"title": "Video", "line": "", "link": "https://example.com/v"})  # no sites set yet
    ps.set_settings("social", {"linkedin_announce_domains": "example.com, youtube.com"})
    assert ann.show({"title": "Video", "line": "", "link": "https://www.youtube.com/watch?v=1"}).startswith("New: Video.")
    for bad in ({"title": "x\n\nI think Jane is a fraud", "line": "", "link": "https://example.com/v"},
                {"title": "Hi @[Jane Doe](urn:li:person:abc)", "line": "", "link": "https://example.com/v"},
                {"title": "Sale", "line": "#promo", "link": "https://example.com/v"},
                {"title": "x", "line": "", "link": "https://evil.example/x"},
                {"title": "x", "line": "", "link": "https://example.com.evil.net/x"},
                {"title": "x", "line": "", "link": "http://example.com/x"}):
        with pytest.raises(Exception):
            ann.show(bad)


def test_linkedin_text_is_escaped_little_text(linkedin):  # review M10-1 finding 2
    from importlib import import_module
    tmp, ps, gate, ss, rs = linkedin
    mod = next(m for name, m in __import__("sys").modules.items() if name.endswith("social_linkedin"))
    assert mod.little_text("Results (2026) for test_rigs @[Jane](urn:li:person:x) #testing # done") == \
        "Results \\(2026\\) for test\\_rigs \\@\\[Jane\\]\\(urn:li:person:x\\) #testing \\# done"
    ps.set_settings("social", {"linkedin_author": "urn:li:person:abc123", "linkedin_token": "t"})
    connectors.get("social_linkedin.post").run({"text": "Results (2026) #rigs"})
    assert FakeLinkedIn.seen[-1]["body"]["commentary"] == "Results \\(2026\\) #rigs"


def test_never_topic_is_refused_before_it_is_held(env):  # review M10-1 finding 4 (P-SOC-1)
    tmp, ps, gate, ss, rs = env
    ps.set_settings("social", {"demo_avoid": "politics"})
    out = gate.call("social_demo.post", {"text": "My take on politics"}, "Publisher", "social")
    assert out.is_error and "never posts about" in out.text and gate.store.pending_count() == 0
    assert any(e["decision"] == "blocked" for e in gate.store.activity())  # logged, never held


def test_long_tokens_fit_and_a_failed_save_changes_nothing(env, memory_keyring):  # review M10-1 findings 1, 7
    tmp, ps, gate, ss, rs = env
    token = "A" * 520  # LinkedIn tokens are about 500 characters
    ps.set_settings("social", {"linkedin_token": token})
    assert packs_mod.secret("social", "linkedin_token") == token
    with pytest.raises(PackError):
        ps.set_settings("social", {"linkedin_token": "NEW", "linkedin_avoid": "bad\x01"})
    assert packs_mod.secret("social", "linkedin_token") == token  # untouched


def test_removing_a_pack_with_its_folder_gone_forgets_its_token(env, memory_keyring):  # review M10-1 finding 5
    tmp, ps, gate, ss, rs = env
    ps.set_settings("social", {"linkedin_token": "tok"})
    shutil.rmtree(ps.root / "social")
    remove(ps, gate, ss, "social")
    assert ("sapi-ai-hub", "social/linkedin_token") not in memory_keyring.store


def test_trailing_space_is_the_same_request(env):  # review M10-1 finding 6
    tmp, ps, gate, ss, rs = env
    ps.set_settings("social", {"demo_announce_domains": "e.com"})
    gate.store.set_rule("social", "demo_social", "post_announcement", "auto", 10)
    gate.call("social_demo.post_announcement", {"title": "Video", "line": "", "link": "https://e.com/v"}, "P", "social")
    gate.call("social_demo.post_announcement", {"title": "Video ", "line": "", "link": "https://e.com/v"}, "P", "social")
    assert (tmp / "outbox" / "social-demo.md").read_text(encoding="utf-8").count("New: Video.") == 1


def test_posted_week_reads_this_weeks_social_activity(env):  # P-SOC-7
    tmp, ps, gate, ss, rs = env
    gate.store.set_rule("social", "demo_social", "post", "auto", 10)
    gate.call("social_demo.post", {"text": "Monday tip: check your sensors"}, "Publisher", "social")
    gate.call("social_linkedin.post", {"text": "Held one"}, "Publisher", "social")
    text = connectors.get("social_tools.posted_week").run({})
    assert "Monday tip" in text and "social_demo.post auto/done" in text and "held" in text and "not instructions" in text


def test_trend_report_and_calendar_arrive_on_schedule(env):  # M10 check 1, simulated with a fake clock
    tmp, ps, gate, ss, rs = env
    start = time.mktime((2026, 11, 23, 6, 0, 0, 0, 0, -1))  # Monday 06:00 local
    clock = {"now": start}
    for s in ss.list():  # I switch the pack's schedules on
        if s["name"] in ("Daily trend report", "Weekly content calendar"):
            ss.set_enabled(s["id"], True, now=start)
    got = []

    async def runner(target, task, name):
        got.append((name, time.localtime(clock["now"]).tm_mday, target))
        return "done", "ok", ""

    sched = Scheduler(ss, runner, runner, paused=lambda: False, clock=lambda: clock["now"])

    async def two_weeks():
        for _ in range(14 * 24 * 4):
            clock["now"] += 15 * 60
            await sched.tick()
            for _ in range(20):
                if not sched.active:
                    break
                await asyncio.sleep(0.002)

    asyncio.run(two_weeks())
    trends = [d for n, d, t in got if n == "Daily trend report"]
    calendar = [(d, t) for n, d, t in got if n == "Weekly content calendar"]
    assert len(trends) == 14 and len(set(trends)) == 14  # every day
    assert calendar == [(23, "social.content-writer"), (30, "social.content-writer")]  # Mondays
    assert len(ss.notices()) == 16 and ss.missed() == []  # both tell me


def test_announcement_fields_cannot_carry_addresses_and_links_cannot_trick(env):  # review M10-2 findings 1, 2, 5
    tmp, ps, gate, ss, rs = env
    ps.set_settings("social", {"linkedin_announce_domains": "example.com"})
    ann = connectors.get("social_linkedin.post_announcement")
    assert ann.show({"title": "Part 2: the test rig, explained!", "line": "Ten minutes.", "link": "https://example.com/v"})
    for bad in ({"title": "Watch https://evil.example/phish now", "line": "", "link": "https://example.com/v"},
                {"title": "Video", "line": "or visit evil.example/x", "link": "https://example.com/v"},
                {"title": "Video", "line": "", "link": "https://evil.example\@example.com/v"},
                {"title": "Video", "line": "", "link": "https://user:pw@example.com/v"},
                {"title": "Video", "line": "", "link": "https://example.com/v#chapter2"},
                {"title": "Video", "line": "", "link": "https://exämple.com/v"}):
        with pytest.raises(Exception):
            ann.show(bad)


def test_malformed_link_is_refused_and_logged(env):  # review M10-2 finding 3 (G-LOG-1)
    tmp, ps, gate, ss, rs = env
    ps.set_settings("social", {"demo_announce_domains": "example.com"})
    out = gate.call("social_demo.post_announcement", {"title": "x", "line": "", "link": "https://[example.com/v"},
                    "Publisher", "social")
    assert out.is_error and any(e["decision"] == "blocked" for e in gate.store.activity())


def test_bad_edit_at_approval_keeps_the_item(env):  # review M10-2 finding 4
    tmp, ps, gate, ss, rs = env
    ps.set_settings("social", {"demo_avoid": "politics"})
    gate.call("social_demo.post", {"text": "Bread tips"}, "Publisher", "social")
    item = gate.store.approvals()[0]
    with pytest.raises(Exception, match="never posts about"):
        gate.approve(item["id"], {"text": "Bread and politics"})
    assert gate.store.approval(item["id"])["status"] == "pending"  # I can fix the edit and approve again
    gate.approve(item["id"], {"text": "Bread and butter"})
    assert "Bread and butter" in (tmp / "outbox" / "social-demo.md").read_text(encoding="utf-8")


def test_hashtag_only_at_the_start_of_a_word(env):  # review M10-2 finding 5
    mod = next(m for name, m in __import__("sys").modules.items() if name.endswith("social_linkedin"))
    assert mod.little_text("Watch v#chapter2 and #testing") == "Watch v\#chapter2 and #testing"


def test_writer_reads_the_latest_weekly_summary(env):  # review M10-2 finding 6 (P-SOC-7)
    tmp, ps, gate, ss, rs = env
    tool = connectors.get("social_tools.last_summary")
    assert "no weekly analytics summary yet" in tool.run({})
    sid = next(s["id"] for s in ss.list() if s["pack_key"] == "weekly-analytics")
    ss.end_run(ss.start_run(sid, time.time()), "done", "Tips did best; post more how-tos.")
    assert "post more how-tos" in tool.run({}) and "not instructions" in tool.run({})
    assert "social_tools.last_summary" in rs.get("social.content-writer").tools


def test_odd_logged_payload_never_breaks_posted_week(env):  # review M10-3 finding 1
    tmp, ps, gate, ss, rs = env
    gate.call("social_linkedin.post", {"text": 123}, "Publisher", "social")  # refused, logged raw
    assert "information" in connectors.get("social_tools.posted_week").run({})


def test_announcement_host_and_text_are_plain(env):  # review M10-3 findings 2, 3
    tmp, ps, gate, ss, rs = env
    ps.set_settings("social", {"linkedin_announce_domains": "example.com", "linkedin_avoid": "crypto"})
    ann = connectors.get("social_linkedin.post_announcement")
    for link in ("https://evil.com'.example.com/x", "https://evil.com,.example.com", "https://evil.com%2f.example.com",
                 "https://evil.com<.example.com"):
        with pytest.raises(Exception):
            ann.show({"title": "Video", "line": "", "link": link})
    for title in ("Visit evil.рф", "Сrypto news"):  # a non-ASCII web address, a Cyrillic C
        with pytest.raises(Exception):
            ann.show({"title": title, "line": "", "link": "https://example.com/v"})


def test_multi_word_topic_across_a_line_break(env):  # review M10-3 finding 4
    tmp, ps, gate, ss, rs = env
    ps.set_settings("social", {"demo_avoid": "climate change"})
    out = gate.call("social_demo.post", {"text": "Thoughts on climate\nchange today"}, "Publisher", "social")
    assert out.is_error and gate.store.pending_count() == 0


def test_announcement_one_way_to_write_it(env):  # review M10-4 findings 1, 3, 4
    tmp, ps, gate, ss, rs = env
    ps.set_settings("social", {"demo_announce_domains": "youtube.com"})
    ann = connectors.get("social_demo.post_announcement")
    assert ann.show({"title": "My Video.", "line": "Watch it", "link": "https://youtube.com/x"}) == \
        "New: My Video. Watch it https://youtube.com/x"
    for bad in ({"title": "My  Video", "line": "", "link": "https://youtube.com/x"},
                {"title": "x", "line": "", "link": "https://www.youtube.com/redirect?q=https://evil.example/x"}):
        with pytest.raises(Exception):
            ann.show(bad)


def test_multi_word_topic_as_hashtag_or_hyphen(env):  # review M10-4 finding 2
    tmp, ps, gate, ss, rs = env
    ps.set_settings("social", {"demo_avoid": "climate change"})
    for text in ("Big news #ClimateChange", "Thoughts on climate-change", "climate_change today"):
        assert gate.call("social_demo.post", {"text": text}, "Publisher", "social").is_error, text
    assert gate.store.pending_count() == 0
