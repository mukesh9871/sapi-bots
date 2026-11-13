"""Helper for test_server_ctrl_c_exits_cleanly. Runs `hub.server.main()` in its own console.

Starts the real server, has one chat turn (so a claude.exe child exists), then presses
Ctrl+C on the console. Logs the claude.exe child PIDs and how main() ended.
argv: <log file> <config file> [idle|busy|starting]
  idle     = after a full reply
  busy     = while a long reply streams
  starting = while Claude is still starting (M3: this press used to be lost)
"""

import ctypes
import http.client
import json
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hub import config  # noqa: E402

config.CONFIG_PATH = Path(sys.argv[2])
from hub.chat import history  # noqa: E402

history.DB_PATH = Path(sys.argv[2]).with_name("chats.db")  # never the real data/chats.db
import hub.server as hub_server  # noqa: E402

k32 = ctypes.windll.kernel32
out = open(sys.argv[1], "w")
captured: dict[str, str] = {}
webbrowser.open = lambda url: captured.setdefault("url", url)  # do not open a real browser


def log(*a):
    print(*a, file=out, flush=True)


def child_claude_pids() -> list[int]:
    ps = (f"Get-CimInstance Win32_Process -Filter \"ParentProcessId={__import__('os').getpid()}\" | "
          "Where-Object Name -like 'claude*' | ForEach-Object ProcessId")
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    return [int(x) for x in res.stdout.split()]


def driver():
    while "url" not in captured:
        time.sleep(0.1)
    u = urlparse(captured["url"])
    token, port = parse_qs(u.query)["token"][0], u.port
    mode = sys.argv[3] if len(sys.argv) > 3 else "idle"
    text = "Reply with only OK." if mode == "idle" else "Write a 1500-word story about a lighthouse."
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=300)
    conn.request("POST", "/api/chat", body=json.dumps({"text": text}),
                 headers={"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}",
                          "Cookie": f"hub_token={token}", "Content-Type": "application/json"})
    resp = conn.getresponse()
    if mode == "idle":
        resp.read()
    else:
        # Read events until the trigger: the new-chat event (sent before Claude starts) or the first text.
        want = "chat" if mode == "starting" else "text"
        while json.loads(resp.readline())["kind"] != want:
            pass
    log("pids", *child_claude_pids())
    captured["t0"] = time.time()
    k32.GenerateConsoleCtrlEvent(0, 0)  # Ctrl+C, as if pressed in the terminal


k32.SetConsoleCtrlHandler(None, False)  # behave like a normal terminal
threading.Thread(target=driver, daemon=True).start()
try:
    log("exit", hub_server.main())
except BaseException as exc:  # noqa: BLE001 - we want to see exactly how it ended
    log("exception", type(exc).__name__)
log("seconds", round(time.time() - captured.get("t0", time.time()), 1))
