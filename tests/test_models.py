"""Models from Claude Code's own list, and effort (R-MODEL-1..3). Offline: the list is a sample of
what Claude Code returns (the real shape, from its start-up info)."""

import json

import pytest

from helpers.hubhttp import free_port, own, request, start_server
from hub import config
from hub.chat import models
from hub.chat.claude_session import build_options
from hub.engine import roles
from hub.engine.roles import RoleError, RoleStore

SAMPLE = [
    {"value": "default", "resolvedModel": "claude-opus-5-5", "displayName": "Default (recommended)",
     "description": "Opus 5.5 · Best for everyday, complex tasks", "supportsEffort": True,
     "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
    {"value": "opus", "resolvedModel": "claude-opus-5-5", "displayName": "Opus 5.5", "description": "For complex work",
     "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
    {"value": "fable", "resolvedModel": "claude-fable-5-2", "displayName": "Fable 5.2",
     "description": "For your toughest challenges", "supportsEffort": True,
     "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
    {"value": "claude-haiku-4-5-20251001", "resolvedModel": "claude-haiku-4-5-20251001", "displayName": "Haiku 4.5",
     "description": "Fastest for quick answers", "supportsThinkingOff": True},
    {"value": "claude-fable-5-2[1m]", "displayName": "Fable 5.2 (1M context)", "description": "Long context",
     "supportsEffort": True, "supportedEffortLevels": ["low", "high"]},
    {"value": "--dangerous", "displayName": "Bad"},  # never a choice: it would read as an option
    {"value": 5}, "junk",
]


def test_the_list_is_cleaned_saved_and_read_back(model_list):  # R-MODEL-1
    assert models.current()["source"] == "builtin"
    assert [m["value"] for m in models.current()["models"]] == ["haiku", "sonnet", "opus"]
    assert models.update_from(SAMPLE)
    state = models.current()
    assert state["source"] == "live" and state["default"]["resolved"] == "claude-opus-5-5"
    assert [m["value"] for m in state["models"]] == ["opus", "fable", "claude-haiku-4-5-20251001",
                                                     "claude-fable-5-2[1m]"]
    assert models.known("claude-haiku-4-5-20251001")["efforts"] == []  # Haiku 4.5 has no effort setting
    saved = json.loads(models.CACHE.read_text(encoding="utf-8"))
    assert len(saved["models"]) == 4
    models._state.update(models=None)  # as after a restart: the saved copy is used at once
    assert models.current()["source"] == "saved" and models.known("fable")
    assert not models.update_from([]) and not models.update_from(None)  # an empty answer changes nothing


def test_only_listed_models_and_known_efforts_are_accepted(model_list):  # R-MODEL-1, R-MODEL-2
    assert models.problem("") == "" and models.problem("opus") == ""
    assert "not offered" in models.problem("fable")  # no list read yet: only the built-in short names
    models.update_from(SAMPLE)
    assert models.problem("fable", "max") == "" and models.problem("claude-haiku-4-5-20251001") == ""
    assert "not offered" in models.problem("claude-opus-4-1")  # not in this plan's list
    assert models.problem("sonnet") == ""  # the short names always work (pack roles use them)
    assert models.problem("-x") and models.problem("Opus 5") and models.problem(3)
    assert models.problem("opus", "huge") == "unknown effort level"


def test_effort_is_sent_only_where_the_model_supports_it(model_list):  # R-MODEL-2
    models.update_from(SAMPLE)
    assert models.effort_for("fable", "max") == "max"
    assert models.effort_for("claude-haiku-4-5-20251001", "high") is None
    assert models.effort_for(None, "low") == "low"  # the plan default (Opus 5.5) supports it
    assert models.effort_for("opus", "") is None and models.effort_for("opus", "huge") is None


def test_options_carry_model_and_effort_and_a_role_overrides_them(model_list):  # R-MODEL-2, R-MODEL-3
    models.update_from(SAMPLE)
    plain = build_options(model="fable", effort="high")
    assert plain.model == "fable" and plain.effort == "high"
    role = roles.Role("r", "R", "claude-haiku-4-5-20251001", ("WebSearch",), "Look things up.", effort="max")
    opts = build_options(role=role, model="fable", effort="low")
    assert opts.model == "claude-haiku-4-5-20251001" and opts.effort is None  # its model has no effort
    role2 = roles.Role("r", "R", "", ("WebSearch",), "Look things up.", effort="xhigh")
    opts2 = build_options(role=role2, model="fable", effort="low")
    assert opts2.model == "fable" and opts2.effort == "xhigh"  # blank model: Settings; own effort wins


def test_role_files_keep_model_and_effort(tmp_path, monkeypatch, model_list):  # R-MODEL-3
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")
    store = RoleStore(tmp_path / "roles")
    r = store.save(None, "Writer", "claude-fable-5-1", ["WebSearch"], "Write things.", 10, "high")
    text = (tmp_path / "roles" / f"{r.id}.md").read_text(encoding="utf-8")
    assert "model: claude-fable-5-1\neffort: high\n" in text
    back = store.get(r.id)
    assert back.model == "claude-fable-5-1" and back.effort == "high" and not back.problem
    plain = store.save(None, "Plain", "", ["WebSearch"], "Look.")
    assert "effort:" not in (tmp_path / "roles" / f"{plain.id}.md").read_text(encoding="utf-8")  # older files unchanged
    (tmp_path / "roles" / "odd.md").write_text("---\nname: Odd\nmodel: opus\neffort: huge\ntools: WebSearch\n---\nX",
                                               encoding="utf-8")
    assert "effort" in store.get("odd").problem  # a hand edit with a bad level never runs
    with pytest.raises(RoleError, match="effort"):
        store.save(None, "Bad", "", ["WebSearch"], "X", 10, "huge")


@pytest.fixture
def hub(tmp_path, monkeypatch, model_list):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")
    port = free_port()
    srv, t = start_server(port, roles=RoleStore(tmp_path / "roles"))
    yield port
    srv.should_exit = True
    t.join(10)


def call(port, method, path, body=None):
    resp, data = request(port, method, path, origin=None if method == "GET" else own(port), body=body)
    return resp.status, json.loads(data) if data else {}


def test_window_gets_the_list_and_settings_follow_it(hub):  # R-MODEL-1, R-MODEL-2
    st, data = call(hub, "GET", "/api/models")
    assert st == 200 and data["source"] == "builtin"
    assert call(hub, "POST", "/api/settings", {"model": "fable"})[0] == 400
    models.update_from(SAMPLE)
    st, data = call(hub, "GET", "/api/models")
    assert data["source"] == "live" and any(m["name"] == "Fable 5.2" for m in data["models"])
    st, data = call(hub, "POST", "/api/settings", {"model": "fable", "effort": "max"})
    assert st == 200 and data["settings"]["model"] == "fable" and data["settings"]["effort"] == "max"
    assert config.load()["effort"] == "max"
    assert call(hub, "POST", "/api/settings", {"model": "gpt-4"})[0] == 400
    assert call(hub, "POST", "/api/settings", {"effort": "turbo"})[0] == 400
    body = {"name": "Scout", "model": "fable", "tools": ["WebSearch"], "instructions": "Find.", "max_turns": 5,
            "effort": "low"}
    st, data = call(hub, "POST", "/api/roles", body)
    assert st == 200 and data["role"]["model"] == "fable" and data["role"]["effort"] == "low"
    assert call(hub, "POST", "/api/roles", {**body, "model": "claude-opus-4-1"})[0] == 400  # not in the list
    models.update_from([{"value": "opus", "displayName": "Opus"}])  # the list changes under a saved choice
    assert call(hub, "POST", "/api/settings", {"model": "fable", "theme": "dark"})[0] == 200  # unchanged: saved


def test_reading_the_list_never_breaks_start_up(monkeypatch, model_list):  # R-MODEL-1
    import asyncio
    import claude_agent_sdk

    class Broken:
        def __init__(self, *a, **k):
            raise RuntimeError("not signed in")

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", Broken)
    assert asyncio.run(models.refresh()) is False
    assert models.current()["source"] == "builtin"
