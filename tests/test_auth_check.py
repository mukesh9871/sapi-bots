"""Unit tests for startup checks (R-AUTH-3, R-AUTH-4, R-AUTH-5, R-AUTH-8). No network."""

from hub.auth_check import (
    BILLING_SWITCH_VARS,
    LoginState,
    parse_login_status,
    scrub_billing_env,
    status_line,
)


def test_scrub_removes_api_key_and_shows_notice():  # M1 check 4, R-AUTH-3
    env = {"ANTHROPIC_API_KEY": "sk-fake", "PATH": "x"}
    notices = scrub_billing_env(env)
    assert "ANTHROPIC_API_KEY" not in env
    assert env["PATH"] == "x"
    assert len(notices) == 1 and "ANTHROPIC_API_KEY" in notices[0]
    assert "sk-fake" not in notices[0]  # never echo the key


def test_scrub_covers_bedrock_and_vertex():  # R-AUTH-3
    env = {name: "1" for name in BILLING_SWITCH_VARS}
    notices = scrub_billing_env(env)
    assert env == {}
    assert len(notices) == len(BILLING_SWITCH_VARS)
    assert {"CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"} <= set(BILLING_SWITCH_VARS)


def test_scrub_silent_when_clean():
    assert scrub_billing_env({"PATH": "x"}) == []


def test_parse_login_status_drops_personal_fields():  # R-AUTH-5
    state = parse_login_status(
        {"loggedIn": True, "authMethod": "claude.ai", "email": "me@example.com",
         "orgId": "abc", "subscriptionType": "max"}
    )
    assert state == LoginState(True, "claude.ai", "max")
    assert "example.com" not in repr(state)


def test_status_line_subscription_ok():  # R-AUTH-8
    ok, text = status_line("none", LoginState(True, "claude.ai", "pro"))
    assert ok and text == "Signed in with Claude subscription (pro)"


def test_status_line_warns_on_api_key():  # R-AUTH-8
    ok, text = status_line("ANTHROPIC_API_KEY", LoginState(True, "claude.ai", "pro"))
    assert not ok and "API" in text


def test_status_line_not_logged_in():  # R-AUTH-4
    ok, _ = status_line(None, LoginState(False))
    assert not ok
