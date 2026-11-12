"""Milestone 2 server checks: launch token, Host/Origin checks, headers (N-SEC-1, N-SEC-4).

Runs a real Uvicorn server on a free localhost port. No Claude calls.
"""

import json

import pytest

from helpers.hubhttp import TOKEN, free_port, own, request, start_server
from hub import config
from hub.auth_check import LoginState
from hub.server import COOKIE, create_app, make_server


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    old = config.CONFIG_PATH
    config.CONFIG_PATH = tmp_path_factory.mktemp("cfg") / "config.json"  # never touch the real file
    port = free_port()
    srv, t = start_server(port, login=LoginState(True, "claude.ai", "pro"))
    yield port
    srv.should_exit = True
    t.join(5)
    config.CONFIG_PATH = old


# ---- M2 check 4: a request without the launch token is rejected -------------------

@pytest.mark.parametrize("path", ["/", "/api/status", "/static/app.js"])
def test_no_token_rejected(server, path):
    resp, _ = request(server, "GET", path, token=None)
    assert resp.status == 403


def test_no_token_chat_rejected(server):
    resp, _ = request(server, "POST", "/api/chat", token=None, origin=own(server), body={"text": "hi"})
    assert resp.status == 403


def test_wrong_token_rejected(server):
    resp, _ = request(server, "GET", "/api/status", token="guess")
    assert resp.status == 403


def test_launch_sets_strict_httponly_cookie(server):
    resp, _ = request(server, "GET", f"/launch?token={TOKEN}", token=None)
    assert resp.status == 303
    cookie = resp.getheader("set-cookie").lower()
    assert f"{COOKIE}={TOKEN}".lower() in cookie and "httponly" in cookie and "samesite=strict" in cookie


def test_launch_with_bad_token_rejected(server):
    resp, _ = request(server, "GET", "/launch?token=nope", token=None)
    assert resp.status == 403 and resp.getheader("set-cookie") is None


def test_with_token_ok_and_secure_headers(server):
    resp, data = request(server, "GET", "/api/status")
    assert resp.status == 200
    body = json.loads(data)
    assert body["status"]["text"].startswith("Signed in with Claude subscription")  # M2 check 3
    assert "default-src 'self'" in resp.getheader("content-security-policy")


# ---- Host and Origin checks (a website open in the browser cannot talk to the bot) -

def test_bad_host_rejected_even_with_token(server):  # DNS rebinding
    resp, _ = request(server, "GET", "/api/status", host="evil.example:80")
    assert resp.status == 403


def test_foreign_origin_rejected(server):
    resp, _ = request(server, "POST", "/api/settings", origin="https://evil.example", body={"model": "haiku"})
    assert resp.status == 403


def test_post_without_origin_rejected(server):
    resp, _ = request(server, "POST", "/api/settings", body={"model": "haiku"})
    assert resp.status == 403


def test_cross_site_fetch_rejected(server):
    resp, _ = request(server, "GET", "/api/status", extra={"Sec-Fetch-Site": "cross-site"})
    assert resp.status == 403


def test_settings_saved_with_own_origin(server):  # F-PERS-2, F-PERS-3
    resp, data = request(server, "POST", "/api/settings", origin=own(server),
                         body={"personality": "custom", "custom_prompt": "Talk like a pirate.", "port": 1})
    assert resp.status == 200
    saved = json.loads(config.CONFIG_PATH.read_text())
    assert saved["personality"] == "custom" and saved["custom_prompt"] == "Talk like a pirate."
    assert saved["port"] != 1  # not editable from the window


def test_unknown_personality_rejected(server):
    resp, _ = request(server, "POST", "/api/settings", origin=own(server), body={"personality": "evil"})
    assert resp.status == 400


def test_binds_localhost_only():  # N-SEC-1
    assert make_server(create_app("t", 1, check_login_on_start=False), 1).config.host == "127.0.0.1"


@pytest.mark.parametrize("body", [
    {"custom_prompt": 5}, {"web_enabled": "false"}, {"model": "gpt-4"}, {"personality": None, "model": 3},
])
def test_bad_settings_values_rejected(server, body):  # review finding 6
    resp, _ = request(server, "POST", "/api/settings", origin=own(server), body=body)
    assert resp.status == 400


def test_non_object_settings_rejected(server):
    resp, _ = request(server, "POST", "/api/settings", origin=own(server), body=["x"])
    assert resp.status == 400


def test_status_reports_busy_flag(server):
    _, data = request(server, "GET", "/api/status")
    assert json.loads(data)["busy"] is False


def test_custom_personality_needs_prompt(server):  # review 2 finding 6
    request(server, "POST", "/api/settings", origin=own(server), body={"personality": "straight", "custom_prompt": ""})
    resp, _ = request(server, "POST", "/api/settings", origin=own(server), body={"personality": "custom"})
    assert resp.status == 400


@pytest.mark.parametrize("path", ["/api/settings", "/api/chat"])
def test_invalid_json_body_is_400(server, path):
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", server, timeout=10)
    conn.request("POST", path, body="{oops", headers={
        "Host": f"127.0.0.1:{server}", "Origin": own(server), "Cookie": f"{COOKIE}={TOKEN}",
        "Content-Type": "application/json"})
    assert conn.getresponse().status == 400
