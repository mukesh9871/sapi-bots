"""claude.ai connectors reused by the hub (G-CONN-1..4) and pack set-up in chat (G-PACK-4). Offline:
the connector list is a sample in the shape Claude Code returns from get_mcp_status."""

import asyncio
import json
import shutil
import threading
import time

import pytest

from hub import config, connectors
from hub.chat import connectors_claude as cc
from hub.chat.claude_session import build_options
from hub.engine import packs as packs_mod
from hub.engine import roles
from hub.engine.packs import PACKS_DIR, PackStore, install, read_pack
from hub.engine.roles import Role, RoleStore
from hub.gate.gate import Gate
from hub.gate.store import GateStore
from hub.scheduler.schedules import ScheduleStore

STATUS = [
    {"name": "claude.ai Gmail", "status": "connected", "scope": "claudeai",
     "config": {"type": "claudeai-proxy", "url": "https://api.anthropic.com/x"},
     "tools": [{"name": "search_threads", "annotations": {"readOnly": True}},
               {"name": "create_draft", "annotations": {}},
               {"name": "bad name!", "annotations": {"readOnly": True}}]},
    {"name": "claude.ai Meetings", "status": "connected", "scope": "claudeai", "config": {"type": "claudeai-proxy"},
     "tools": [{"name": "list_meetings", "annotations": {"readOnly": True}}]},
    {"name": "polarion", "status": "pending", "scope": "user", "config": {"type": "stdio"}, "tools": []},  # never
]
SEARCH = "mcp__claude_ai_Gmail__search_threads"
DRAFT = "mcp__claude_ai_Gmail__create_draft"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "CACHE", tmp_path / "connectors.json")
    monkeypatch.setitem(cc._state, "servers", None)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    cc.update_from(STATUS)
    yield tmp_path


def test_only_claude_ai_connectors_are_listed_and_saved(setup):  # G-CONN-1
    names = [s["name"] for s in cc.current()["servers"]]
    assert names == ["claude.ai Gmail", "claude.ai Meetings"]  # never my local MCP servers
    gmail = cc.current()["servers"][0]
    assert gmail["label"] == "Gmail" and [t["name"] for t in gmail["tools"]] == ["search_threads", "create_draft"]
    saved = json.loads((setup / "connectors.json").read_text(encoding="utf-8"))
    assert [s["name"] for s in saved["servers"]] == names
    cc._state.update(servers=None)
    assert cc.current()["source"] == "saved"
    assert cc.tool_id("claude.ai Gmail", "x") == "mcp__claude_ai_Gmail__x"


def test_a_connector_is_usable_only_when_switched_on(setup):  # G-CONN-1, G-CONN-2
    assert cc.tools() == {} and SEARCH not in roles.catalog()
    config.save({**config.load(), "connectors": ["claude.ai Gmail"]})
    cat = roles.catalog()
    assert cat[SEARCH]["kind"] == "read-only" and cat[DRAFT]["kind"] == "acting"
    assert "mcp__claude_ai_Meetings__list_meetings" not in cat


def test_plain_chat_and_other_roles_stay_closed(setup):  # G-CONN-1 (N-SEC-5 kept where unused)
    config.save({**config.load(), "connectors": ["claude.ai Gmail"]})
    chat = build_options()
    assert chat.strict_mcp_config and "mcp__claude_ai_Gmail" in chat.disallowed_tools
    role = Role("w", "W", "", ("WebSearch",), "Look.")
    assert build_options(role=role).strict_mcp_config


def test_a_role_gets_only_its_listed_connector_tools(setup, tmp_path):  # G-CONN-2
    config.save({**config.load(), "connectors": ["claude.ai Gmail", "claude.ai Meetings"]})
    gate = Gate(GateStore(tmp_path / "hub.db"), paused=lambda: False)
    role = Role("w", "W", "", (SEARCH, DRAFT), "Mail things.")
    opts = build_options(role=role, gate=gate)
    assert not opts.strict_mcp_config
    assert SEARCH in opts.allowed_tools and DRAFT not in opts.allowed_tools  # read-only runs, acting is gated
    assert "mcp__claude_ai_Meetings" in opts.disallowed_tools and "mcp__claude_ai_Gmail" not in opts.disallowed_tools
    with pytest.raises(ValueError, match="gate"):
        build_options(role=role)  # acting connector tools never run without the gate
    config.save({**config.load(), "connectors": []})
    with pytest.raises(ValueError, match="not switched on"):
        build_options(role=role, gate=gate)


def _permission(opts, tool, args, use_id="t1"):
    from claude_agent_sdk import ToolPermissionContext
    return opts.can_use_tool(tool, args, ToolPermissionContext(tool_use_id=use_id))


def test_acting_connector_call_waits_for_my_decision(setup, tmp_path):  # G-CONN-3, G-APPR-2, G-LOG-1
    config.save({**config.load(), "connectors": ["claude.ai Gmail"]})
    gate = Gate(GateStore(tmp_path / "hub.db"), paused=lambda: False)
    opts = build_options(role=Role("w", "Writer", "", (SEARCH, DRAFT), "Mail."), gate=gate)
    args = {"to": "hr@example.com", "body": "Hello"}

    def approve_when_held():
        for _ in range(100):
            items = gate.store.approvals("pending")
            if items:
                assert "hr@example.com" in gate.find(items[0]["action_id"]).show(items[0]["payload"])
                gate.approve(items[0]["id"])
                return
            time.sleep(0.05)

    threading.Thread(target=approve_when_held).start()
    result = asyncio.run(_permission(opts, DRAFT, args))
    assert result.behavior == "allow" and result.updated_input == args
    hook = opts.hooks["PostToolUse"][0].hooks[0]
    asyncio.run(hook({"hook_event_name": "PostToolUse", "tool_name": DRAFT, "tool_input": args,
                      "tool_response": "draft 42 created"}, "t1", None))
    log = gate.store.activity()
    assert [r["outcome"] for r in log if r["action_id"].endswith(".create_draft")][:2] in (["done", "started"],
                                                                                         ["started", "done"])
    assert gate.store.approvals("done")  # the inbox item is closed with the outcome


def test_rejected_or_never_connector_calls_are_refused(setup, tmp_path):  # G-CONN-3
    config.save({**config.load(), "connectors": ["claude.ai Gmail"]})
    gate = Gate(GateStore(tmp_path / "hub.db"), paused=lambda: False)
    opts = build_options(role=Role("w", "Writer", "", (DRAFT,), "Mail."), gate=gate)

    def reject_when_held():
        for _ in range(100):
            items = gate.store.approvals("pending")
            if items:
                gate.reject(items[0]["id"])
                return
            time.sleep(0.05)

    threading.Thread(target=reject_when_held).start()
    assert asyncio.run(_permission(opts, DRAFT, {"body": "x"})).behavior == "deny"
    action = gate.external_action("Gmail", "claude_ai_Gmail", "create_draft")
    gate.store.set_rule("local", action.platform, "create_draft", "never", 5)
    assert asyncio.run(_permission(opts, DRAFT, {"body": "y"}, "t2")).behavior == "deny"
    gate.store.set_rule("local", action.platform, "create_draft", "auto", 5)
    assert asyncio.run(_permission(opts, DRAFT, {"body": "z"}, "t3")).behavior == "allow"  # Auto: logged, no wait


def test_approving_after_the_agent_stopped_waiting_runs_nothing(setup, tmp_path):  # G-CONN-3
    gate = Gate(GateStore(tmp_path / "hub.db"), paused=lambda: False)
    action = gate.external_action("Gmail", "claude_ai_Gmail", "create_draft")
    held = gate.decide_external(action, {"body": "x"}, "Writer")
    assert held.decision == "held"
    with pytest.raises(ValueError, match="stopped waiting"):
        gate.approve(held.item_id)
    gate2 = Gate(GateStore(tmp_path / "hub.db"), paused=lambda: False)
    gate2.recover()  # after a restart nobody waits: the item is withdrawn, never left pending
    assert gate2.store.approval(held.item_id)["status"] == "withdrawn"


def test_connector_tools_never_share_a_role_with_web_tools(setup, tmp_path):  # review M15 (security)
    config.save({**config.load(), "connectors": ["claude.ai Gmail"]})
    assert "web" in roles.check("R", "", [SEARCH, "WebFetch"], "Read mail.")
    gate = Gate(GateStore(tmp_path / "hub.db"), paused=lambda: False)
    with pytest.raises(ValueError, match="web tools"):
        build_options(role=Role("w", "W", "", (SEARCH, "WebSearch"), "x"), gate=gate)


def test_an_approval_in_the_last_moment_still_goes_ahead(setup, tmp_path, monkeypatch):  # review M15
    from hub.chat import claude_session
    monkeypatch.setattr(claude_session, "CONNECTOR_WAIT", 1)
    config.save({**config.load(), "connectors": ["claude.ai Gmail"]})
    gate = Gate(GateStore(tmp_path / "hub.db"), paused=lambda: False)
    opts = build_options(role=Role("w", "Writer", "", (DRAFT,), "Mail."), gate=gate)
    real_withdraw = gate.withdraw

    def approve_then_withdraw(item_id, reason, pack):  # I approve just before the time is up
        if gate.store.approval(item_id)["status"] == "pending":
            gate.waiting.add(item_id)
            gate.approve(item_id)
        return real_withdraw(item_id, reason, pack)

    monkeypatch.setattr(gate, "withdraw", approve_then_withdraw)
    result = asyncio.run(_permission(opts, DRAFT, {"body": "late"}))
    assert result.behavior == "allow"


def test_an_identical_call_while_one_waits_is_refused_not_shared(setup, tmp_path):  # review M15 round 2
    gate = Gate(GateStore(tmp_path / "hub.db"), paused=lambda: False)
    action = gate.external_action("Gmail", "claude_ai_Gmail", "create_draft")
    first = gate.decide_external(action, {"body": "x"}, "Writer")
    gate.waiting.add(first.item_id)
    second = gate.decide_external(action, {"body": "x"}, "Writer (scheduled)")
    assert second.decision == "blocked" and "already waiting" in second.text
    assert gate.store.approval(first.item_id)["status"] == "pending"  # the first one is untouched


def test_a_stopped_wait_withdraws_its_item(setup, tmp_path):  # review M15
    config.save({**config.load(), "connectors": ["claude.ai Gmail"]})
    gate = Gate(GateStore(tmp_path / "hub.db"), paused=lambda: False)
    opts = build_options(role=Role("w", "Writer", "", (DRAFT,), "Mail."), gate=gate)

    async def stop_soon():
        task = asyncio.ensure_future(_permission(opts, DRAFT, {"body": "x"}))
        await asyncio.sleep(0.5)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(stop_soon())
    assert gate.store.approvals("pending") == [] and gate.store.approvals("withdrawn")


# ---- G-PACK-4: minimal set-up, the rest in chat ---------------------------------------------------

@pytest.fixture
def jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")
    root = tmp_path / "packs"
    shutil.copytree(PACKS_DIR / "jobs", root / "jobs")
    ps = PackStore(root, tmp_path / "hub.db")
    monkeypatch.setattr(connectors, "pack_dirs", ps.enabled_dirs)
    monkeypatch.setattr(packs_mod, "active", ps)
    logged = []
    monkeypatch.setattr(packs_mod, "on_setting_change", lambda *a: logged.append(a))
    gate = Gate(GateStore(tmp_path / "hub.db"), paused=lambda: False)
    install(ps, gate, ScheduleStore(tmp_path / "hub.db"), "jobs")
    yield ps, logged


def run(action, **fields):
    act = connectors.get(action)
    return act.run(act.clean(act.fill(fields, {"pack": "jobs", "agent": "Job set-up", "folder": ""})))


def test_settings_say_what_is_required_and_what_chat_may_set(jobs):  # G-PACK-4
    pk = read_pack("jobs", PACKS_DIR / "jobs")
    spec = {s["key"]: s for s in pk.settings}
    assert spec["resume_file"]["required"] and not spec["resume_file"]["chat"]
    assert spec["titles"]["chat"] and spec["skills"]["chat"]
    assert not spec["site_modes"]["chat"] and not spec["smtp_password"]["chat"]  # modes and logins: form only
    for name in ("jobs", "social", "pipeline", "video"):
        assert not read_pack(name, PACKS_DIR / name).problem and "setup" in read_pack(name, PACKS_DIR / name).roles


def test_only_text_settings_can_be_marked_chat(tmp_path):  # G-PACK-4
    pack = tmp_path / "p"
    (pack / "roles").mkdir(parents=True)
    (pack / "pack.md").write_text("---\nname: P\n---\n", encoding="utf-8")
    (pack / "settings.md").write_text("token | secret | A token | chat\nwhere | folder | A folder | chat\n"
                                      "x | text | X | sometimes\n", encoding="utf-8")
    problem = read_pack("p", pack).problem
    assert "secret" in problem and "folder" in problem and "line 3" in problem


def test_setup_role_saves_chat_settings_only_and_logs_them(jobs):  # G-PACK-4
    ps, logged = jobs
    assert "titles" in run("pack_setup.show_settings")
    assert run("pack_setup.save_setting", key="titles", value="Test engineer, QA lead") == "Saved Target job titles (comma-separated)."
    assert ps.settings("jobs")["titles"] == "Test engineer, QA lead"
    assert logged == [("jobs", "titles", "Test engineer, QA lead", "Job set-up")]
    with pytest.raises(Exception, match="Packs page"):
        run("pack_setup.save_setting", key="site_modes", value="example.com: auto")  # never a mode from chat
    with pytest.raises(Exception, match="Packs page"):
        run("pack_setup.save_setting", key="smtp_password", value="hunter2")  # never a login
    act = connectors.get("pack_setup.save_setting")
    filled = act.fill({"pack": "social", "key": "titles", "value": "x"}, {"pack": "jobs", "agent": "A"})
    assert filled["pack"] == "jobs"  # the agent can never point it at another pack
