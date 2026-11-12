"""Local hub server: serves the chat window and streams replies (Milestone 2).

Run:  .venv\\Scripts\\python -m hub.server        (opens your browser)

Security (N-SEC-1, N-SEC-4):
  - binds to 127.0.0.1 only
  - a random token per launch; the browser gets it once via /launch?token=... and keeps
    it as an HttpOnly, SameSite=Strict cookie, so other websites cannot send it
  - Host header must be our own address (blocks DNS-rebinding)
  - Origin must be our own origin on every state-changing request
  - strict Content-Security-Policy: the page loads only its own files
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
import threading
import webbrowser
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from hub import config
from hub.auth_check import LOGIN_STEPS, LoginState, check_login, scrub_billing_env, status_line
from hub.chat.claude_session import ChatSession
from hub.chat.personalities import PERSONALITIES

WEB_DIR = Path(__file__).parent / "web"
COOKIE = "hub_token"

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


@dataclass
class HubState:
    token: str
    port: int
    login: LoginState = field(default_factory=lambda: LoginState(None))
    notices: list[str] = field(default_factory=list)
    login_checked: bool = False
    session: ChatSession | None = None
    busy: bool = False  # a reply is streaming


def _forbidden(reason: str) -> Response:
    return JSONResponse({"error": reason}, status_code=403, headers=SECURITY_HEADERS)


def create_app(token: str, port: int, *, login: LoginState | None = None,
               notices: list[str] | None = None, check_login_on_start: bool = True) -> FastAPI:
    state = HubState(token=token, port=port, login=login or LoginState(None), notices=notices or [])
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{h}" for h in allowed_hosts}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async def refresh_login() -> None:
            try:
                state.login = await asyncio.to_thread(check_login)  # R-AUTH-4
            finally:
                state.login_checked = True  # never leave the window stuck on "checking"

        # In the background, so a slow CLI never delays the window opening (N-PERF-1).
        login_task = asyncio.create_task(refresh_login()) if check_login_on_start else None
        if not check_login_on_start:
            state.login_checked = True
        yield
        if login_task:
            login_task.cancel()
        if state.session:
            await state.session.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.hub = state

    # ---- N-SEC-4: every request passes these checks --------------------------------
    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host") not in allowed_hosts:
            return _forbidden("bad host")
        origin = request.headers.get("origin")
        if origin is not None and origin not in allowed_origins:
            return _forbidden("bad origin")
        if request.method not in ("GET", "HEAD") and origin is None:
            return _forbidden("missing origin")
        if request.headers.get("sec-fetch-site") == "cross-site":
            return _forbidden("cross-site request")

        if request.url.path == "/launch":
            # One-time hand-over of the launch token to the browser as a cookie.
            if not secrets.compare_digest(request.query_params.get("token", ""), state.token):
                return _forbidden("bad token")
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie(COOKIE, state.token, httponly=True, samesite="strict", path="/")
            return resp

        sent = request.cookies.get(COOKIE) or request.headers.get("x-hub-token") or ""
        # Note: browsers do not separate cookies by port, so another local server on 127.0.0.1
        # that this browser visits could see the cookie. Accepted for a single-user PC.
        if not secrets.compare_digest(sent, state.token):
            return _forbidden("missing or bad launch token")

        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    # ---- pages -----------------------------------------------------------------------
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    # ---- API -------------------------------------------------------------------------
    def current_status() -> dict[str, Any]:
        source = state.session.api_key_source if state.session else None
        if not state.login_checked and source is None:
            return {"ok": True, "text": "Checking sign-in…", "confirmed": False, "pending": True}
        ok, text = status_line(source, state.login)
        if state.login.logged_in is False:
            text = LOGIN_STEPS
        return {"ok": ok, "text": text, "confirmed": source is not None}

    @app.get("/api/status")
    async def get_status() -> dict[str, Any]:
        cfg = config.load()
        return {
            "status": current_status(),
            "busy": state.busy,
            "notices": state.notices,
            "personalities": sorted(PERSONALITIES) + ["custom"],
            "settings": {k: cfg[k] for k in config.EDITABLE},
        }

    async def read_json(request: Request) -> dict[str, Any] | None:
        try:
            body = await request.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    @app.post("/api/settings")
    async def post_settings(request: Request) -> dict[str, Any]:
        body = await read_json(request)
        # Check types, so a bad value can never break every later chat.
        if body is None or any(
            k in body and not isinstance(body[k], config.TYPES[k]) for k in config.EDITABLE
        ):
            return JSONResponse({"error": "bad settings value"}, status_code=400)
        if body.get("personality") not in (None, *PERSONALITIES, "custom"):
            return JSONResponse({"error": "unknown personality"}, status_code=400)
        if body.get("model") not in (None, "", "haiku", "sonnet", "opus"):
            return JSONResponse({"error": "unknown model"}, status_code=400)
        merged = {**config.load(), **body}
        if merged["personality"] == "custom" and not merged["custom_prompt"].strip():
            return JSONResponse({"error": "custom personality needs a prompt"}, status_code=400)
        cfg = config.save(body)  # applies from the next message (F-PERS-3)
        return {"settings": {k: cfg[k] for k in config.EDITABLE}}

    def session_kwargs() -> dict[str, Any]:
        cfg = config.load()
        custom = cfg["personality"] == "custom" and cfg["custom_prompt"].strip()
        return {
            "personality": cfg["personality"] if cfg["personality"] in PERSONALITIES else "straight",
            "custom_prompt": cfg["custom_prompt"] if custom else None,
            "model": cfg["model"] or None,
            "web_enabled": bool(cfg["web_enabled"]),
        }

    @app.post("/api/chat")
    async def chat(request: Request):
        body = await read_json(request)
        if body is None:
            return JSONResponse({"error": "bad request"}, status_code=400)
        text = str(body.get("text", "")).strip()
        if not text:
            return JSONResponse({"error": "empty message"}, status_code=400)
        if state.busy:
            return JSONResponse({"error": "a reply is still streaming"}, status_code=409)
        state.busy = True

        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        async def run_turn() -> None:
            # Runs on its own task, so a closed tab never leaves half-read SDK messages behind.
            try:
                kwargs = session_kwargs()
                if state.session is None:
                    state.session = ChatSession(**kwargs)
                    await state.session.connect()
                else:
                    state.session.stop_requested = False  # a stale Stop must not hit this turn
                    await state.session.reconfigure(**kwargs)
                async for ev in state.session.send(text):
                    if ev.kind == "init":
                        queue.put_nowait({"kind": "status", **current_status()})
                    elif ev.kind == "done":
                        d = ev.data
                        queue.put_nowait({"kind": "done", "subtype": d["subtype"],
                                          "terminal_reason": d["terminal_reason"], "usage": d["usage"]})
                    elif ev.kind == "error":
                        queue.put_nowait({"kind": "error", "code": ev.text,
                                          "rate_limit": state.session.last_rate_limit})
                    else:
                        queue.put_nowait({"kind": ev.kind, "text": ev.text, "data": ev.data})
            except Exception as exc:  # N-REL-1: plain message instead of a crash
                queue.put_nowait({"kind": "error", "code": "exception", "detail": type(exc).__name__})
            finally:
                state.busy = False
                queue.put_nowait(None)

        turn = asyncio.create_task(run_turn())

        async def stream():
            finished = False
            try:
                while (item := await queue.get()) is not None:
                    yield json.dumps(item, default=str) + "\n"
                finished = True
            finally:
                if not finished and state.session and not turn.done():
                    # Tab closed mid-reply. This scope is already cancelled, so shield the stop,
                    # or it is cancelled before it reaches Claude (found in review, tested).
                    with anyio.CancelScope(shield=True):
                        await state.session.stop()

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.post("/api/stop")
    async def stop() -> dict[str, bool]:  # F-CHAT-4
        if state.busy and state.session:
            await state.session.stop()
            return {"stopped": True}
        return {"stopped": False}

    @app.post("/api/new")
    async def new_chat() -> dict[str, bool]:
        if state.busy:
            return JSONResponse({"error": "a reply is still streaming"}, status_code=409)
        if state.session:
            # Detach first, so a message sent while this closes starts a fresh session
            # instead of reusing the closing one (found in review).
            old, state.session = state.session, None
            await old.close()  # N-PERF-3: no idle Claude process kept around
        return {"ok": True}

    return app


def make_server(app: FastAPI, port: int) -> uvicorn.Server:
    # N-SEC-1: localhost only, never 0.0.0.0.
    # timeout_graceful_shutdown: never wait long for an open reply stream on Ctrl+C.
    return uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                         timeout_graceful_shutdown=3))


def main() -> int:
    notices = scrub_billing_env()  # R-AUTH-3, before anything starts the SDK
    for n in notices:
        print(n)
    port = int(config.load()["port"])
    token = secrets.token_urlsafe(32)
    app = create_app(token, port, notices=notices)

    url = f"http://127.0.0.1:{port}/launch?token={token}"
    server = make_server(app, port)

    def open_browser() -> None:
        # Wait until the server is listening, then open the window.
        while not server.started:
            threading.Event().wait(0.1)
        print(f"Hub running at http://127.0.0.1:{port}  (Ctrl+C to quit)")
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()
    try:
        asyncio.run(server.serve())  # our own loop: the Proactor loop on Windows, which the SDK needs
    except KeyboardInterrupt:
        # Uvicorn shuts down cleanly (closing Claude), then re-raises Ctrl+C. Exit quietly.
        pass
    print("Hub stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
