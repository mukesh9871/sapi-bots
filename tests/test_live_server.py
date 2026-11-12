"""Live Milestone 2 check: a reply streams through the local server, and a personality
switch applies from the next message with context kept. Skipped unless HUB_LIVE=1."""

import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from helpers.hubhttp import TOKEN, free_port, own, request, start_server
from hub import config

pytestmark = pytest.mark.skipif(os.environ.get("HUB_LIVE") != "1", reason="set HUB_LIVE=1 to run live tests")


@pytest.fixture
def server(tmp_path):
    old = config.CONFIG_PATH
    config.CONFIG_PATH = tmp_path / "config.json"
    config.save({"model": "haiku", "web_enabled": False, "personality": "straight"})
    port = free_port()
    srv, t = start_server(port)
    yield port
    srv.should_exit = True
    t.join(10)
    config.CONFIG_PATH = old


def chat(port, text):
    resp, data = request(port, "POST", "/api/chat", origin=own(port), body={"text": text}, timeout=120)
    assert resp.status == 200
    return [json.loads(line) for line in data.decode().splitlines() if line]


def test_stream_and_switch_personality(server):
    events = chat(server, "Remember the code word HERON-9. Reply with only OK.")
    kinds = [e["kind"] for e in events]
    assert "status" in kinds and kinds[-1] == "done"
    status = next(e for e in events if e["kind"] == "status")
    assert status["ok"] and status["confirmed"]  # R-AUTH-8 confirmed by the SDK
    assert kinds.count("text") >= 1

    # F-PERS-3: custom personality applies from the next message, context kept.
    resp, _ = request(server, "POST", "/api/settings", origin=own(server),
                      body={"personality": "custom", "custom_prompt": "Always answer in UPPERCASE letters only."})
    assert resp.status == 200
    events = chat(server, "What was the code word, and what colour is the sky on a clear day? One sentence.")
    reply = "".join(e["text"] for e in events if e["kind"] == "text")
    assert "HERON-9" in reply and reply == reply.upper()


def _pid_alive(pid: int) -> bool:
    res = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
    return str(pid) in res.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console Ctrl+C behaviour")
@pytest.mark.parametrize("mode", ["idle", "busy"])
def test_server_ctrl_c_exits_cleanly(tmp_path, mode):  # Ctrl+C: quick, no traceback, no orphan claude.exe
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"model": "haiku", "web_enabled": False, "port": free_port()}))
    log = tmp_path / "log.txt"
    helper = Path(__file__).parent / "helpers" / "server_ctrlc_child.py"
    subprocess.run([sys.executable, str(helper), str(log), str(cfg), mode],
                   creationflags=subprocess.CREATE_NEW_CONSOLE, timeout=300)
    lines = log.read_text().splitlines()
    pids = [int(p) for p in lines[0].split()[1:]]
    assert pids, lines  # a claude.exe child existed
    assert lines[1] == "exit 0", lines  # clean exit, no KeyboardInterrupt traceback
    assert float(lines[2].split()[1]) < 8, lines  # did not wait for a long reply to finish
    time.sleep(2)
    assert not [p for p in pids if _pid_alive(p)], "claude.exe left running after Ctrl+C"


def test_tab_close_mid_reply_frees_server(server):  # review finding 1, F-CHAT-4
    conn = http.client.HTTPConnection("127.0.0.1", server, timeout=60)
    conn.request("POST", "/api/chat", body=json.dumps({"text": "Write a 600-word story about a lighthouse."}),
                 headers={"Host": f"127.0.0.1:{server}", "Origin": own(server),
                          "Cookie": f"hub_token={TOKEN}", "Content-Type": "application/json"})
    resp = conn.getresponse()
    resp.fp.readline()  # first event arrived: the reply is streaming
    conn.sock.shutdown(socket.SHUT_RDWR)  # the tab closes
    conn.close()
    deadline = time.time() + 8  # a 600-word reply takes far longer, so free = stop worked
    while time.time() < deadline:
        _, data = request(server, "GET", "/api/status")
        if not json.loads(data)["busy"]:
            return
        time.sleep(0.5)
    pytest.fail("server still busy after the tab closed: the stop never reached Claude")


def test_stop_while_claude_is_starting(server):  # review 2 finding 2, F-CHAT-4
    # A model change forces a new CLI process, so the Stop lands while it is starting.
    request(server, "POST", "/api/settings", origin=own(server), body={"model": "sonnet"})
    result = {}

    def run_chat():
        result["events"] = chat(server, "Write a 600-word story about a lighthouse.")

    t = threading.Thread(target=run_chat)
    t.start()
    time.sleep(0.3)  # the chat request is in, the CLI is still starting
    request(server, "POST", "/api/stop", origin=own(server))
    t.join(30)
    assert not t.is_alive(), "reply was not stopped: it ran to the end"
    done = [e for e in result["events"] if e["kind"] == "done"]
    assert done and str(done[0]["terminal_reason"]).startswith("aborted"), result["events"][-1]
