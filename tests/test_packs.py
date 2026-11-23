"""Milestone 8 offline checks: instruction packs (G-PACK-1, G-PACK-2). No Claude calls.

- M8 check 2: a sample pack installs and removes without any change to the engine.
"""

import asyncio
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from helpers.hubhttp import free_port, own, request, start_server
from hub import config, connectors
from hub.chat import claude_session
from hub.chat.history import HistoryStore
from hub.engine import roles
from hub.engine.packs import PACKS_DIR, PackError, PackStore, install, read_pack, remove
from hub.engine.roles import RoleStore
from hub.engine.teams import TeamStore
from hub.gate.gate import Gate
from hub.gate.store import GateStore
from hub.scheduler.schedules import ScheduleStore

ROOT = Path(__file__).resolve().parents[1]


def engine_hash() -> str:
    """Every engine file (hub/), so a test can prove a pack changed none of them."""
    h = hashlib.sha256()
    for f in sorted((ROOT / "hub").rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts:
            h.update(str(f.relative_to(ROOT)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")
    monkeypatch.setenv("HUB_OUTBOX", str(tmp_path / "outbox"))
    packs_root = tmp_path / "packs"
    shutil.copytree(PACKS_DIR / "sample", packs_root / "sample")
    ps = PackStore(packs_root, tmp_path / "hub.db")
    monkeypatch.setattr(connectors, "pack_dirs", ps.enabled_dirs)
    gate = Gate(GateStore(tmp_path / "hub.db"), paused=lambda: False)
    ss = ScheduleStore(tmp_path / "hub.db")
    rs = RoleStore(tmp_path / "roles")
    rs.pack_dirs = ps.enabled_dirs
    ts = TeamStore(tmp_path / "teams", roles_get=rs.get)
    ts.pack_dirs = ps.enabled_dirs
    return tmp_path, ps, gate, ss, rs, ts


def test_sample_pack_is_valid():  # G-PACK-1: the folder bundles everything
    pack = read_pack("sample", PACKS_DIR / "sample")
    assert pack.problem == ""
    assert pack.roles == ["notetaker", "planner"] and pack.teams == ["duo"] and pack.connectors == ["sample_notes"]
    assert pack.rules == [{"action": "sample_notes.add_note", "mode": "auto", "cap": 3}]
    assert pack.schedules[0]["target"] == "sample.notetaker" and pack.schedules[0]["spec"]["type"] == "daily"


def test_install_and_remove_change_nothing_in_the_engine(env):  # M8 check 2, G-PACK-2
    tmp, ps, gate, ss, rs, ts = env
    before = engine_hash()
    my_role = rs.save(None, "Mine", "", ["Read"], "My own role.")
    result = install(ps, gate, ss, "sample")
    assert result["suggested_auto"] == ["sample_notes.add_note"]  # G-APPR-3: installed as Ask
    # everything the pack brings is there
    assert {r.id for r in rs.list()} == {my_role.id, "sample.notetaker", "sample.planner"}
    role = rs.get("sample.notetaker")
    assert role.pack == "sample" and role.problem == "" and role.tools == ("sample_notes.add_note",)
    team = ts.get("sample.duo")
    assert team.problem == "" and team.lead == "sample.planner" and team.members == ("sample.notetaker",)
    assert "sample_notes.add_note" in connectors.actions()
    assert gate.store.rule("sample", "sample", "add_note")["mode"] == "ask"
    assert [(s["name"], s["enabled"], s["pack"]) for s in ss.list()] == [("Morning note", False, "sample")]
    # removed: all gone, my own role untouched, engine untouched
    out = remove(ps, gate, ss, "sample")
    assert out["rules"] == 1 and out["schedules"] == 1
    assert [r.id for r in rs.list()] == [my_role.id] and rs.get("sample.notetaker") is None
    assert ts.get("sample.duo") is None and "sample_notes.add_note" not in connectors.actions()
    assert all(r["pack"] != "sample" for r in gate.store.rules()) and ss.list() == []
    assert engine_hash() == before
    install(ps, gate, ss, "sample")  # and it installs again
    assert rs.get("sample.planner") is not None


def test_pack_items_are_read_only(env):  # G-PACK-2: the app never changes a pack's files
    tmp, ps, gate, ss, rs, ts = env
    install(ps, gate, ss, "sample")
    file = ps.root / "sample" / "roles" / "notetaker.md"
    text = file.read_text(encoding="utf-8")
    with pytest.raises(roles.RoleError, match="copy it first"):
        rs.save("sample.notetaker", "Changed", "", ["Read"], "x")
    assert rs.delete("sample.notetaker") is False
    copy = rs.copy("sample.notetaker")  # my own version, outside the pack
    assert copy.pack == "local" and "." not in copy.id
    assert file.read_text(encoding="utf-8") == text
    from hub.engine.teams import TeamError
    with pytest.raises(TeamError, match="from a pack"):
        ts.save("sample.duo", "X", "sample.planner", "", ["sample.notetaker"])


def test_switching_off_hides_and_keeps(env):  # G-PACK-2
    tmp, ps, gate, ss, rs, ts = env
    install(ps, gate, ss, "sample")
    ps.set_enabled("sample", False)
    assert rs.get("sample.notetaker") is None and "sample_notes.add_note" not in connectors.actions()
    assert gate.store.rule("sample", "sample", "add_note")["daily_cap"] == 3 and len(ss.list()) == 1  # kept
    ps.set_enabled("sample", True)
    assert rs.get("sample.notetaker") is not None


def test_pack_role_uses_its_packs_rules(env):  # G-PACK-1: default rules per pack
    tmp, ps, gate, ss, rs, ts = env
    install(ps, gate, ss, "sample")
    action = connectors.get("sample_notes.add_note")
    tool = claude_session._gated_tool(action, gate, "Note taker", "sample")
    out = asyncio.run(tool.handler({"text": "Hello"}))
    assert "approval" in out["content"][0]["text"].lower()  # Ask: held in the inbox
    item = gate.store.approvals()[0]
    assert item["pack"] == "sample"
    gate.store.set_rule("sample", "sample", "add_note", "auto", 3)  # only I switch it to Auto
    out = asyncio.run(tool.handler({"text": "Second"}))
    assert (tmp / "outbox" / "sample-notes.md").read_text(encoding="utf-8").count("Second") == 1


def test_remove_rejects_waiting_approvals(env):
    tmp, ps, gate, ss, rs, ts = env
    install(ps, gate, ss, "sample")
    gate.call("sample_notes.add_note", {"text": "x"}, "Note taker", "sample")
    assert gate.store.pending_count() == 1
    assert remove(ps, gate, ss, "sample")["rejected"] == 1
    assert gate.store.pending_count() == 0
    assert any(e["decision"] == "rejected" for e in gate.store.activity())  # the log keeps it


def test_bad_packs_cannot_install(env):
    tmp, ps, gate, ss, rs, ts = env
    bad = ps.root / "bad"
    shutil.copytree(ps.root / "sample", bad)
    (bad / "rules.md").write_text("sample_notes.add_note: maybe\n", encoding="utf-8")
    with pytest.raises(PackError, match="needs fixing"):
        install(ps, gate, ss, "bad")
    clash = ps.root / "clash"
    shutil.copytree(ps.root / "sample", clash)  # the same connector name as the sample pack
    install(ps, gate, ss, "sample")
    with pytest.raises(PackError, match="cannot load"):
        install(ps, gate, ss, "clash")
    assert "clash" not in ps.states()
    broken = ps.root / "broken"
    shutil.copytree(ps.root / "sample", broken)
    shutil.rmtree(broken / "connectors")
    (broken / "connectors").mkdir()
    (broken / "connectors" / "boom.py").write_text("raise RuntimeError('no')\n", encoding="utf-8")
    (broken / "rules.md").write_text("", encoding="utf-8")
    (broken / "roles" / "notetaker.md").write_text("---\nname: N\ntools: Read\n---\nx", encoding="utf-8")
    with pytest.raises(PackError, match="cannot load"):
        install(ps, gate, ss, "broken")
    with pytest.raises(PackError, match="no such pack"):
        install(ps, gate, ss, "local")  # my own folder is not a pack
    with pytest.raises(PackError, match="no such pack"):
        install(ps, gate, ss, "../sample")


def test_unreadable_or_bad_pack_files_are_problems_not_crashes(env):  # review M8-1 finding 4
    tmp, ps, gate, ss, rs, ts = env
    bad = ps.root / "badtext"
    shutil.copytree(ps.root / "sample", bad)
    (bad / "rules.md").write_bytes(b"\xff\xfe\x00bad")
    (bad / "schedules" / "morning-note.md").write_bytes(b"\xff\xfebad")
    listed = {p.id: p for p in ps.available()}  # never raises
    assert "cannot read" in listed["badtext"].problem
    longname = ps.root / "longname"
    shutil.copytree(ps.root / "sample", longname)
    (longname / "schedules" / "morning-note.md").write_text(
        "---\nname: " + "N" * 80 + "\nrun: role notetaker\nwhen: daily 08:00\n---\nx", encoding="utf-8")
    assert "1 to 60" in ps.get("longname").problem
    with pytest.raises(PackError):
        install(ps, gate, ss, "longname")
    assert "longname" not in ps.states() and ss.list() == []  # nothing half-installed


def test_pack_after_key_resolves_in_any_order(env):  # review M8-1 finding 9
    tmp, ps, gate, ss, rs, ts = env
    (ps.root / "sample" / "schedules" / "a-first.md").write_text(
        "---\nname: Follow\nrun: role notetaker\nwhen: after morning-note\n---\nAfter the note.", encoding="utf-8")
    install(ps, gate, ss, "sample")
    by_name = {s["name"]: s for s in ss.list()}
    assert by_name["Follow"]["spec"] == {"type": "after", "schedule": by_name["Morning note"]["id"]}
    (ps.root / "sample" / "schedules" / "a-first.md").write_text(
        "---\nname: Follow\nrun: role notetaker\nwhen: after nothing\n---\nx", encoding="utf-8")
    assert "after" in ps.get("sample").problem


def test_pack_connector_cannot_share_a_rule_key(env):  # review M8-1 finding 8
    tmp, ps, gate, ss, rs, ts = env
    evil = ps.root / "evil"
    shutil.copytree(ps.root / "sample", evil)
    shutil.rmtree(evil / "connectors")
    (evil / "connectors").mkdir()
    (evil / "connectors" / "evil.py").write_text(
        "from hub.connectors.base import Action\n"
        "ACTIONS = [Action(connector='evil', name='post_note', platform='demo', description='x', "
        "fields={'text': {}}, run=lambda p: 'x')]\n", encoding="utf-8")
    (evil / "rules.md").write_text("", encoding="utf-8")
    (evil / "roles" / "notetaker.md").write_text("---\nname: N\ntools: Read\n---\nx", encoding="utf-8")
    with pytest.raises(PackError, match="clashes"):
        install(ps, gate, ss, "evil")


def test_clash_cannot_be_bypassed_by_switching_off(env):  # review M8-2 finding 4
    tmp, ps, gate, ss, rs, ts = env
    shutil.copytree(ps.root / "sample", ps.root / "other")  # the same action id
    install(ps, gate, ss, "sample")
    ps.set_enabled("sample", False)
    with pytest.raises(PackError, match="clashes"):
        install(ps, gate, ss, "other")  # refused even though the first pack is off
    assert ps.clash("sample") == []


def test_odd_digits_in_pack_files_never_crash(env):  # review M8-2 finding 5
    tmp, ps, gate, ss, rs, ts = env
    odd = ps.root / "odd"
    shutil.copytree(ps.root / "sample", odd)
    (odd / "rules.md").write_text("sample_notes.add_note: ask cap \u00b2\n", encoding="utf-8")
    (odd / "schedules" / "x.md").write_text("---\nname: X\nrun: role notetaker\nwhen: after \u00b2\n---\nx",
                                          encoding="utf-8")
    pack = ps.get("odd")  # never raises
    assert pack.problem
    assert [p.id for p in ps.available()]  # the Packs page still lists


def test_pack_after_loop_is_a_problem(env):  # review M8-2 small item
    tmp, ps, gate, ss, rs, ts = env
    sch = ps.root / "sample" / "schedules"
    (sch / "a.md").write_text("---\nname: A\nrun: role notetaker\nwhen: after b\n---\nx", encoding="utf-8")
    (sch / "b.md").write_text("---\nname: B\nrun: role notetaker\nwhen: after a\n---\nx", encoding="utf-8")
    assert "loops" in ps.get("sample").problem


# ---- through the server ------------------------------------------------------------------------------

@pytest.fixture
def hub(env, monkeypatch):
    tmp, ps, gate, ss, rs, ts = env
    monkeypatch.setattr(config, "CONFIG_PATH", tmp / "config.json")
    monkeypatch.setattr(claude_session, "CHAT_WORKDIR", tmp / "chat")
    port = free_port()
    srv, t = start_server(port, history=HistoryStore(tmp / "chats.db"), roles=rs, gate_store=gate.store,
                          team_store=ts, pack_store=ps, schedule_store=ss)
    yield port
    srv.should_exit = True
    t.join(5)


def call(port, method, path, body=None):
    resp, data = request(port, method, path, origin=None if method == "GET" else own(port), body=body)
    return resp.status, json.loads(data or b"{}")


def test_api_install_switch_remove(hub):  # M8 check 2 through the API
    port = hub
    before = engine_hash()
    packs = call(port, "GET", "/api/packs")[1]["packs"]
    assert [(p["id"], p["installed"]) for p in packs] == [("sample", False)]
    st, data = call(port, "POST", "/api/packs/sample/install")
    assert st == 200 and data["suggested_auto"] == ["sample_notes.add_note"]
    assert call(port, "POST", "/api/packs/sample/install")[0] == 409
    roles_now = [r["id"] for r in call(port, "GET", "/api/roles")[1]["roles"]]
    assert "sample.notetaker" in roles_now and "sample_notes.add_note" in call(port, "GET", "/api/roles")[1]["tools"]
    assert "sample.duo" in [t["id"] for t in call(port, "GET", "/api/teams")[1]["teams"]]
    assert call(port, "PUT", "/api/roles/sample.notetaker", {"name": "X", "model": "", "tools": [],
                                                            "instructions": "x", "max_turns": 5})[0] == 400
    assert call(port, "POST", "/api/packs/sample/enable", {"enabled": False})[0] == 200
    assert "sample.notetaker" not in [r["id"] for r in call(port, "GET", "/api/roles")[1]["roles"]]
    assert call(port, "POST", "/api/packs/sample/enable", {"enabled": True})[0] == 200
    assert call(port, "POST", "/api/packs/sample/remove")[0] == 200
    assert call(port, "POST", "/api/packs/sample/remove")[0] == 409
    assert "sample.notetaker" not in [r["id"] for r in call(port, "GET", "/api/roles")[1]["roles"]]
    assert call(port, "POST", "/api/packs/nope/install")[0] == 400
    assert engine_hash() == before


def test_api_switch_on_refused_on_clash(hub, env):  # review M8-2 finding 4
    tmp, ps, gate, ss, rs, ts = env
    port = hub
    assert call(port, "POST", "/api/packs/sample/install")[0] == 200
    ps.set_keys("sample", ["id:demo.post_note"])  # as if it claimed an action of the app
    call(port, "POST", "/api/packs/sample/enable", {"enabled": False})
    st, data = call(port, "POST", "/api/packs/sample/enable", {"enabled": True})
    assert st == 409 and "clashes" in data["error"]
