"""Unit tests for the chat tool lock-down (N-SEC-3, N-SEC-5). No network."""

import asyncio

from hub.chat.claude_session import DENIED_TOOLS, WEB_TOOLS, build_options


def test_only_web_tools_available(tmp_path):  # M1 check 5, N-SEC-3
    o = build_options(cwd=tmp_path)
    assert sorted(o.tools) == sorted(WEB_TOOLS)
    assert sorted(o.allowed_tools) == sorted(WEB_TOOLS)
    for tool in ("Bash", "Write", "Edit", "PowerShell", "Read"):
        assert tool in o.disallowed_tools  # removed AND denied, not just un-allowed


def test_never_bypass_permissions(tmp_path):  # N-SEC-3
    assert build_options(cwd=tmp_path).permission_mode != "bypassPermissions"


def test_isolated_from_user_setup(tmp_path):  # N-SEC-5
    o = build_options(cwd=tmp_path)
    assert o.setting_sources == []
    assert o.skills == []
    assert o.mcp_servers == {} and o.strict_mcp_config
    assert o.cwd == str(tmp_path)


def test_no_telemetry_env(tmp_path):  # N-SEC-2
    assert build_options(cwd=tmp_path).env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


def test_web_off_removes_web_tools(tmp_path):  # F-WEB-3
    o = build_options(cwd=tmp_path, web_enabled=False)
    assert o.tools == [] and o.allowed_tools == []
    for tool in WEB_TOOLS:
        assert tool in o.disallowed_tools


def test_permission_callback_denies_everything_else(tmp_path):  # N-SEC-3 layer 3
    gate = build_options(cwd=tmp_path).can_use_tool

    def verdict(tool):
        return type(asyncio.run(gate(tool, {}, None))).__name__

    assert verdict("WebSearch") == "PermissionResultAllow"
    for tool in DENIED_TOOLS:
        assert verdict(tool) == "PermissionResultDeny"
    assert verdict("SomeFutureTool") == "PermissionResultDeny"
