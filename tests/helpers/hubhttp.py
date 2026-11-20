"""Shared helpers: run a real hub server on a free port and talk to it with http.client."""

import asyncio
import http.client
import json
import socket
import tempfile
import threading
import time
from pathlib import Path

from hub.chat.history import HistoryStore
from hub.gate.store import GateStore
from hub.server import COOKIE, create_app, make_server

TOKEN = "test-token-123"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port, **app_kwargs):
    # Tests never touch the real data/chats.db: default to a fresh temporary one.
    app_kwargs.setdefault("history", HistoryStore(Path(tempfile.mkdtemp()) / "chats.db"))
    app_kwargs.setdefault("gate_store", GateStore(Path(tempfile.mkdtemp()) / "hub.db"))  # never data/hub.db
    app = create_app(TOKEN, port, check_login_on_start=False, **app_kwargs)
    srv = make_server(app, port)
    t = threading.Thread(target=lambda: asyncio.run(srv.serve()), daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.05)
    return srv, t


def request(port, method, path, *, host=None, origin=None, token=TOKEN, body=None, extra=None, timeout=10):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if origin:
        headers["Origin"] = origin
    if token:
        headers["Cookie"] = f"{COOKIE}={token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(body)
    headers.update(extra or {})
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp, data


def own(port):
    return f"http://127.0.0.1:{port}"
