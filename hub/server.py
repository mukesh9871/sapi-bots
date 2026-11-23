"""Local hub server: serves the chat window and streams replies (Milestones 2 to 7).

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
import errno
import json
import secrets
import socket
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
from hub.chat.claude_session import ChatSession, all_transcript_ids, delete_transcript, transcript_exists
from hub.chat.history import HistoryStore
from hub.chat.personalities import PERSONALITIES
from hub.engine.roles import MODELS, Role, RoleError, RoleStore, catalog
from hub.connectors.base import ActionError
from hub.gate.gate import Gate, RepeatRun
from hub.gate.store import GateStore
from hub.engine import roles as roles_mod
from hub.engine.orchestrator import TeamRun, new_run
from hub.engine.runs import RunStore
from hub.engine.teams import TeamError, TeamStore

WEB_DIR = Path(__file__).parent / "web"
COOKIE = "hub_token"
MAX_PROMPT = 20_000  # characters, for any personality prompt
THEMES = ("system", "light", "dark")

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
    history: HistoryStore | None = None
    chat_id: int | None = None  # the chat the live session belongs to (None = none yet)
    turn_chat_id: int | None = None  # the chat the running reply is for (while busy)
    closing_chat_id: int | None = None  # a chat whose Claude process is still shutting down
    stop_pending: bool = False  # Stop pressed during this turn, maybe before its session existed
    roles: RoleStore | None = None  # Milestone 5
    gate: Gate | None = None  # Milestone 6
    turn_is_role: bool = False  # the running turn is a role task (pause stops it, G-BUDGET-2)
    # G-BUDGET-2: held in memory and written through to config.json. Never re-read from the file
    # per call: a failed read (file busy while it is saved) would fall back to "not paused" (review M6).
    paused: bool = False
    runs: dict = field(default_factory=dict)  # Milestone 7: team runs in progress, by run id


class RoleUnavailable(Exception):
    """A role chat whose role was deleted, or whose file is broken (hand edit)."""


def _forbidden(reason: str) -> Response:
    return JSONResponse({"error": reason}, status_code=403, headers=SECURITY_HEADERS)


def _bad(reason: str, code: int = 400) -> Response:
    return JSONResponse({"error": reason}, status_code=code)


def validate_settings(body: dict[str, Any], current: dict[str, Any]) -> str | None:
    """Returns the problem with a settings change, or None. A bad value must never break later chats."""
    if any(k in body and not isinstance(body[k], config.TYPES[k]) for k in config.EDITABLE):
        return "bad settings value"
    if body.get("personality") not in (None, *PERSONALITIES, "custom"):
        return "unknown personality"
    if body.get("model") not in (None, "", "haiku", "sonnet", "opus"):
        return "unknown model"
    if body.get("theme") not in (None, *THEMES):
        return "unknown theme"
    if "bot_name" in body and not 0 < len(body["bot_name"].strip()) <= 40:
        return "bot name must be 1 to 40 characters"
    if "avatar" in body and not 0 < len(body["avatar"].strip()) <= 8:
        return "avatar must be an emoji or up to 4 letters"
    prompts = body.get("prompts", {})
    if any(k not in PERSONALITIES or not isinstance(v, str) or len(v) > MAX_PROMPT for k, v in prompts.items()):
        return "bad personality prompt"
    if len(body.get("custom_prompt", "")) > MAX_PROMPT:
        return "custom prompt is too long"
    merged = {**current, **body}
    if merged["personality"] == "custom" and not merged["custom_prompt"].strip():
        return "custom personality needs a prompt"
    return None


def create_app(token: str, port: int, *, login: LoginState | None = None,
               notices: list[str] | None = None, check_login_on_start: bool = True,
               history: HistoryStore | None = None, roles: RoleStore | None = None,
               gate_store: GateStore | None = None, team_store: TeamStore | None = None,
               run_store: RunStore | None = None) -> FastAPI:
    state = HubState(token=token, port=port, login=login or LoginState(None), notices=notices or [],
                     history=history or HistoryStore(), roles=roles or RoleStore())
    # G-BUDGET-2: the pause switch is read on every acting call, so it applies at once.
    state.paused, note = config.start_paused()  # fails closed on a busy or broken file (review M6)
    if note:
        state.notices.append(note)
    state.gate = Gate(gate_store or GateStore(), paused=lambda: state.paused)
    gate = state.gate
    unknown = gate.recover()  # N-REL-3: calls a crash interrupted are marked, never re-run
    if unknown:
        state.notices.append(f"{unknown} action(s) were interrupted when the app last stopped. "
                             "Check them in the Activity log: they were not run again.")
    gate.ensure_rules()  # G-APPR-3: every installed action has a rule, Ask by default
    store = state.history
    role_store = state.roles
    role_store.in_use = lambda: {c["role"] for c in store.list_chats() if c["role"]}  # review M5
    # Milestone 7: team templates read roles from the same store; runs share hub.db with the gate.
    teams = team_store or TeamStore()
    teams.roles_get = role_store.get
    run_store = run_store or RunStore(gate.store.path)
    left = run_store.stop_unfinished()  # a restart never resumes a team by itself
    if left:
        state.notices.append(f"{left} team run(s) were stopped by the restart. Their work so far is on the Teams board.")
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
        live = list(state.runs.values())
        for run in live:  # the app is closing: stop team runs cleanly
            await run.stop("the app was closed")
        tasks = [r.run_task for r in live if r.run_task]
        if tasks:  # let each run close its member sessions, so no claude.exe is left behind
            await asyncio.wait(tasks, timeout=15)
        await detach_session()

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
            "builtin_prompts": PERSONALITIES,  # for "reset to default" on the settings page
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
        if body is None:
            return _bad("bad settings value")
        problem = validate_settings(body, config.load())
        if problem:
            return _bad(problem)
        if "prompts" in body:
            # An empty prompt means "use the built-in text again".
            body["prompts"] = {k: v for k, v in body["prompts"].items() if v.strip()}
        for key in ("bot_name", "avatar"):
            if key in body:
                body[key] = body[key].strip()
        if "automation_paused" in body:
            state.paused = body["automation_paused"]  # takes effect at once, even if the save below fails
            if state.paused:
                await stop_role_turn()  # G-BUDGET-2: pausing also stops a running role task
            for run in list(state.runs.values()):  # and pauses or resumes every team run
                await (run.pause("global") if state.paused else run.resume("global"))
        try:
            # The file always gets the pause state the app is really in. Otherwise saving the theme
            # after a start-up from a broken file would write "not paused" (review M6).
            cfg = config.save({**body, "automation_paused": state.paused})  # M3 check 3, F-PERS-3
        except OSError:
            # Another program holds config.json (antivirus, sync). Say so plainly (review M6).
            note = ""
            if "automation_paused" in body:  # the switch already changed in memory (review M6)
                now, later = ("paused", "resumes") if state.paused else ("running", "pauses again")
                note = f" Automation is {now} for now, but this could not be saved, so it {later} after a restart."
            return _bad("the settings file is busy (another program has it open), so nothing was saved: "
                        "try again." + note, 503)
        return {"settings": {k: cfg[k] for k in config.EDITABLE}}

    async def stop_role_turn() -> None:
        if state.busy and state.turn_is_role:
            state.stop_pending = True  # also covers a session that is still starting, like Stop
            if state.session:
                await state.session.stop()

    def usable_role(role_id: str) -> Role:
        role = role_store.get(role_id)
        if role is None:
            raise RoleUnavailable("role_missing")
        if role.problem:
            raise RoleUnavailable("role_broken")
        if state.paused:
            raise RoleUnavailable("automation_paused")  # G-BUDGET-2: plain chat stays available
        return role

    def session_kwargs(chat: dict[str, Any] | None = None) -> dict[str, Any]:
        cfg = config.load()
        if chat and chat.get("role"):
            # G-AGENT-3: a role chat runs as the role (its prompt, tools, folder and model).
            # Personality and the web toggle do not apply. A blank role model uses Settings.
            return {"role": usable_role(chat["role"]), "model": cfg["model"] or None, "gate": gate}
        personality = cfg["personality"] if cfg["personality"] in PERSONALITIES else "straight"
        if cfg["personality"] == "custom" and cfg["custom_prompt"].strip():
            prompt = cfg["custom_prompt"]
        else:
            prompt = cfg["prompts"].get(personality)  # edited built-in prompt, or None = built-in text
        return {
            "personality": personality,
            "custom_prompt": prompt or None,
            "model": cfg["model"] or None,
            "web_enabled": bool(cfg["web_enabled"]),
        }

    async def detach_session() -> None:
        """Close the live Claude process (N-PERF-3). Detach first, so nothing reuses it while it closes."""
        if state.session:
            # Clear both before the await: a message arriving during close() must start fresh,
            # never reuse this chat id with a new process (review M3 finding 1).
            old, closing = state.session, state.chat_id
            state.session, state.chat_id, state.closing_chat_id = None, None, closing
            try:
                await old.close()
            except Exception:
                pass  # already gone
            finally:
                if state.closing_chat_id == closing:
                    state.closing_chat_id = None

    def open_session(chat_id: int, kwargs: dict[str, Any]) -> tuple[ChatSession, str]:
        """F-HIST-4: resume the chat's stored session, or replay its saved messages if that is gone.

        Returns the session (not yet connected) and a note for the window ("" = nothing to say).
        """
        chat = store.get_chat(chat_id)
        sid = chat["session_id"]
        role = kwargs.get("role")
        if transcript_exists(sid, role.folder if role else None):  # a role's sessions live in its folder
            session = ChatSession(**kwargs, resume=sid, ctrl_c_shield=False)
            # A resumed session keeps the prompt it last followed. Only when the personality
            # changed since then, the new one travels with the next message (review M3).
            if chat["system_prompt"] != session.options.system_prompt:
                session.pending_personality = session.options.system_prompt
            return session, ""
        session = ChatSession(**kwargs, ctrl_c_shield=False)  # Ctrl+C in the server window means quit
        replay, left_out = store.replay_text(chat_id)
        if not replay:
            return session, ""
        session.pending_replay = replay
        note = "Restored this chat from saved history."
        if left_out:
            note += f" The {left_out} oldest messages were left out to save usage."
        return session, note

    @app.post("/api/chat")
    async def chat(request: Request):
        body = await read_json(request)
        if body is None:
            return _bad("bad request")
        text = str(body.get("text", "")).strip()
        if not text:
            return _bad("empty message")
        chat_id = body.get("chat_id")  # None = start a new chat
        if chat_id is not None and (not isinstance(chat_id, int) or isinstance(chat_id, bool)):
            return _bad("bad chat id")
        if chat_id is not None and store.get_chat(chat_id) is None:
            return _bad("this chat no longer exists", 404)
        role_id = body.get("role") if chat_id is None else None  # an existing chat keeps its role
        if role_id is not None:
            role = role_store.get(role_id) if isinstance(role_id, str) else None
            if role is None:
                return _bad("this role no longer exists", 404)
            if role.problem:
                return _bad(f"this role cannot run: {role.problem}")
            if state.paused:
                return _bad("automation is paused: resume it to give a role a task", 409)
        if state.busy:
            return _bad("a reply is still streaming", 409)
        state.busy, state.turn_chat_id, state.stop_pending = True, chat_id, False
        state.turn_is_role = bool(role_id or (chat_id is not None and store.get_chat(chat_id)["role"]))

        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        async def run_turn() -> None:
            # Runs on its own task, so a closed tab never leaves half-read SDK messages behind
            # and the reply is still saved.
            nonlocal chat_id
            reply: list[str] = []
            sources: list[dict] = []  # F-WEB-2: the reply's web sources, saved with it
            stopped = failed = budget = False
            user_msg = None
            created = False  # this turn created the chat
            try:
                if chat_id != state.chat_id:  # another chat (or a new one) gets its own session
                    await detach_session()
                if chat_id is None:
                    new = store.create_chat(text, role_id)  # F-HIST-2: title from the first words
                    chat_id, created = new["id"], True
                    queue.put_nowait({"kind": "chat", "id": chat_id, "title": new["title"], "role": role_id})
                kwargs = session_kwargs(store.get_chat(chat_id))
                fresh, note = None, ""
                if state.session is None:
                    # Decide resume or replay before saving this message, so it is not replayed too.
                    fresh, note = open_session(chat_id, kwargs)
                user_msg = store.add_message(chat_id, "user", text)  # N-REL-2: saved before sending
                if fresh is not None:
                    state.session, state.chat_id = fresh, chat_id  # set first, so Stop can reach it
                    fresh.stop_requested = state.stop_pending  # Stop pressed while the old chat closed
                    try:
                        await fresh.connect()
                    except Exception:
                        state.session, state.chat_id = None, None  # never keep a half-started process
                        raise
                    if note:
                        queue.put_nowait({"kind": "note", "text": note})
                else:
                    # A stale Stop from an earlier turn must not hit this one, but this turn's must.
                    state.session.stop_requested = state.stop_pending
                    await state.session.reconfigure(**kwargs)
                session = state.session
                async for ev in session.send(text):
                    if ev.kind == "init":
                        if session.session_id:
                            # Known early, for D-2. A session started without resume got the current
                            # system prompt at launch, so record it now: a failed turn must not leave
                            # the old chat's prompt on record for the new session (review M3 round 4).
                            launched = None if getattr(session.options, "resume", None) else session.options.system_prompt
                            store.set_session(chat_id, session.session_id, launched)
                        queue.put_nowait({"kind": "status", **current_status()})
                    elif ev.kind == "done":
                        d = ev.data
                        if session.session_id:
                            # The prompt counts as delivered only when the turn went through.
                            prompt = None if d["is_error"] else session.options.system_prompt
                            store.set_session(chat_id, session.session_id, prompt)
                        stopped = str(d["terminal_reason"] or "").startswith("aborted")
                        budget = d["subtype"] == "error_max_turns"  # G-BUDGET-1, saved with the reply
                        queue.put_nowait({"kind": "done", "subtype": d["subtype"], "num_turns": d.get("num_turns"),
                                          "terminal_reason": d["terminal_reason"], "usage": d["usage"]})
                    elif ev.kind == "sources":
                        sources = ev.data["sources"]
                        queue.put_nowait({"kind": "sources", "sources": sources})
                    elif ev.kind == "error":
                        failed = True
                        # detail: the CLI's own error text, used only to show a usage-limit
                        # reset time when no rate-limit event gave one (R-AUTH-6).
                        queue.put_nowait({"kind": "error", "code": ev.text,
                                          "rate_limit": session.last_rate_limit,
                                          "detail": ev.data.get("detail", "")})
                    else:
                        if ev.kind == "text":
                            reply.append(ev.text)
                        queue.put_nowait({"kind": ev.kind, "text": ev.text, "data": ev.data})
            except RoleUnavailable as exc:
                failed = True
                queue.put_nowait({"kind": "error", "code": str(exc), "detail": ""})
            except Exception as exc:  # N-REL-1: plain message instead of a crash
                failed = True
                queue.put_nowait({"kind": "error", "code": "exception", "detail": type(exc).__name__})
            finally:
                try:
                    if reply:  # F-HIST-1: the reply is saved, a stopped one too
                        status = "stopped" if stopped else "budget" if budget else ""
                        store.add_message(chat_id, "bot", "".join(reply), status, sources)
                    elif budget:
                        # G-BUDGET-1: stopped at its budget before writing any text; keep the note.
                        store.add_message(chat_id, "bot", "", "budget", sources)
                    elif failed and user_msg is not None:
                        # No reply: the window puts the message back in the input box for Retry,
                        # so drop it here, or a retry would save it twice.
                        store.delete_message(user_msg)
                    gone = store.delete_if_empty(chat_id) if created and failed and not reply else None
                    if gone is not None:
                        # Its first message failed: no empty titled chat is left in the sidebar.
                        # Close its process first, then remove its transcripts (D-2).
                        if state.chat_id == chat_id:
                            await detach_session()
                        await forget_sessions(gone)
                        queue.put_nowait({"kind": "chat_removed", "id": chat_id})
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
                if not finished and not turn.done():
                    # Tab closed mid-reply. This scope is already cancelled, so shield the stop,
                    # or it is cancelled before it reaches Claude (found in review, tested).
                    state.stop_pending = True  # also covers a session that does not exist yet
                    if state.session:
                        with anyio.CancelScope(shield=True):
                            await state.session.stop()

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.post("/api/stop")
    async def stop() -> dict[str, bool]:  # F-CHAT-4
        if not state.busy:
            return {"stopped": False}
        state.stop_pending = True  # if the session is not started yet, run_turn passes this on
        if state.session:
            await state.session.stop()
        return {"stopped": True}

    @asynccontextmanager
    async def exclusive():
        """Hold the busy flag while a process closes, so no message can slip in (review M3 finding 1)."""
        state.busy = True
        try:
            yield
        finally:
            state.busy = False

    @app.post("/api/new")
    async def new_chat() -> dict[str, bool]:
        if state.busy:
            return _bad("a reply is still streaming", 409)
        async with exclusive():
            await detach_session()  # N-PERF-3: no idle Claude process kept around
        return {"ok": True}

    # ---- history (Milestone 3) -------------------------------------------------------
    @app.get("/api/chats")
    async def list_chats(q: str = "") -> dict[str, Any]:  # F-HIST-2, F-HIST-3
        return {"chats": store.list_chats(q)}

    @app.get("/api/chats/{chat_id}")
    async def get_chat(chat_id: int):  # F-HIST-4: reopen
        chat = store.get_chat(chat_id)
        if chat is None:
            return _bad("chat not found", 404)
        chat.pop("session_id", None)  # the window has no use for it
        return {"chat": chat, "messages": store.messages(chat_id)}

    @app.patch("/api/chats/{chat_id}")
    async def rename_chat(chat_id: int, request: Request):  # F-HIST-3
        body = await read_json(request)
        title = body.get("title") if body else None
        if not isinstance(title, str) or not 0 < len(title.strip()) <= 200:
            return _bad("title must be 1 to 200 characters")
        if not store.rename(chat_id, " ".join(title.split())):
            return _bad("chat not found", 404)
        return {"ok": True}

    async def forget_sessions(session_ids: list[str]) -> dict[str, int]:
        """D-2: delete SDK transcripts. Counts removed files, and files that could not be removed
        (for example held open by another program), so the window can say so."""
        def run() -> dict[str, int]:
            counts = {"transcripts_removed": 0, "transcripts_failed": 0}
            for sid in set(session_ids):
                try:
                    counts["transcripts_removed"] += delete_transcript(sid)
                except OSError:
                    counts["transcripts_failed"] += 1
            return counts
        return await asyncio.to_thread(run)

    @app.delete("/api/chats/{chat_id}")
    async def delete_chat(chat_id: int):  # F-HIST-3, D-2
        if state.busy and chat_id in (state.chat_id, state.turn_chat_id):
            return _bad("a reply is still streaming in this chat", 409)
        if chat_id == state.closing_chat_id:  # its process could still write the transcript
            return _bad("this chat is still closing, try again in a moment", 409)
        if chat_id == state.chat_id:
            async with exclusive():
                await detach_session()  # the process must not write the transcript again after we delete it
        session_ids = store.delete_chat(chat_id)
        if session_ids is None:
            return _bad("chat not found", 404)
        return {"ok": True, **await forget_sessions(session_ids)}

    @app.post("/api/chats/{chat_id}/export")
    async def export_chat(chat_id: int):  # F-HIST-5
        chat = store.get_chat(chat_id)
        role = role_store.get(chat["role"]) if chat and chat["role"] else None
        name = role.name if role else (chat["role"] if chat and chat["role"] else config.load()["bot_name"])
        path = store.export_markdown(chat_id, bot_name=name)
        if path is None:
            return _bad("chat not found", 404)
        return {"ok": True, "path": str(path)}

    @app.post("/api/history/delete-all")
    async def delete_all(request: Request):  # D-3
        body = await read_json(request)
        if not body or body.get("confirm") != "DELETE":  # the window's confirmation step
            return _bad("confirmation missing")
        if state.busy:
            return _bad("a reply is still streaming", 409)
        if state.runs:  # a running team still needs its transcripts
            closing = all(r.stopped or r.finished for r in state.runs.values())
            return _bad("a team run is still closing: try again in a moment" if closing
                        else "a team is still running: stop it first", 409)
        async with exclusive():
            await detach_session()
            if state.runs:  # a team started while the chat process was closing (review M7-5 L2)
                return _bad("a team is still running: stop it first", 409)
            # Team runs go too: their steps and outputs are history (their project folders stay).
            ids =store.delete_all() + run_store.delete_all() + all_transcript_ids()
        return {"ok": True, **await forget_sessions(ids)}

    # ---- roles (Milestone 5, G-AGENT-1, G-AGENT-2) --------------------------------------
    def role_fields(body: dict[str, Any] | None) -> tuple | None:
        if body is None:
            return None
        return (body.get("name", ""), body.get("model", ""), body.get("tools", []), body.get("instructions", ""),
                body.get("max_turns", 30))

    @app.get("/api/roles")
    async def list_roles() -> dict[str, Any]:
        return {"roles": [r.to_dict() for r in role_store.list()], "tools": catalog(), "models": list(MODELS)}

    @app.post("/api/roles")
    async def create_role(request: Request):
        fields = role_fields(await read_json(request))
        if fields is None:
            return _bad("bad request")
        try:
            return {"role": role_store.save(None, *fields).to_dict()}
        except RoleError as exc:
            return _bad(str(exc))

    @app.put("/api/roles/{role_id}")
    async def update_role(role_id: str, request: Request):
        # Takes effect from the role's next message in each chat (the session restarts).
        fields = role_fields(await read_json(request))
        if fields is None:
            return _bad("bad request")
        try:
            return {"role": role_store.save(role_id, *fields).to_dict()}
        except RoleError as exc:
            return _bad(str(exc), 404 if "no longer exists" in str(exc) else 400)

    @app.post("/api/roles/{role_id}/copy")
    async def copy_role(role_id: str):
        try:
            return {"role": role_store.copy(role_id).to_dict()}
        except RoleError as exc:
            return _bad(str(exc), 404 if "no longer exists" in str(exc) else 400)

    @app.delete("/api/roles/{role_id}")
    async def delete_role(role_id: str):
        # Keeps the role's chats (readable, not continuable) and its folder (its work).
        if not role_store.delete(role_id):
            return _bad("role not found", 404)
        return {"ok": True}

    # ---- approvals, activity log, rules, pause (Milestone 6) ---------------------------
    @app.get("/api/gate/summary")
    async def gate_summary() -> dict[str, Any]:  # UI 8 badge, UI 13 usage bar
        return {"pending": gate.store.pending_count(), "paused": state.paused}

    @app.get("/api/approvals")
    async def list_approvals() -> dict[str, Any]:  # G-APPR-2
        from hub import connectors
        items = []
        for item in gate.store.approvals("pending"):
            action = connectors.get(item["action_id"])
            items.append({**item, "preview": action.preview(item["payload"]) if action else "",
                          "fields": action.fields if action else {}})
        return {"items": items}

    @app.post("/api/approvals/{item_id}/approve")
    async def approve(item_id: int, request: Request):  # G-APPR-2: Approve, or Edit then Approve
        body = await read_json(request) or {}
        try:
            return await asyncio.to_thread(gate.approve, item_id, body.get("payload"), body.get("confirm_repeat") is True)
        except KeyError:
            return _bad("item not found", 404)
        except RepeatRun as exc:  # the window asks me, then sends confirm_repeat
            return JSONResponse({"error": str(exc), "repeat": True}, status_code=409)
        except (ActionError, ValueError) as exc:
            return _bad(str(exc), 409 if "already decided" in str(exc) else 400)

    @app.post("/api/approvals/{item_id}/reject")
    async def reject(item_id: int):
        try:
            gate.reject(item_id)
        except KeyError:
            return _bad("item not found", 404)
        except ValueError as exc:
            return _bad(str(exc), 409)
        return {"ok": True}

    @app.get("/api/activity")
    async def activity(q: str = "", pack: str = "", platform: str = "", day: str = ""):  # G-LOG-1, UI 12
        try:
            return {"entries": gate.store.activity(q, pack, platform, day)}
        except (ValueError, OSError, OverflowError):  # Windows cannot handle dates before 1970
            return _bad("date must be YYYY-MM-DD")

    @app.get("/api/rules")
    async def list_rules() -> dict[str, Any]:
        return {"rules": gate.store.rules()}

    @app.put("/api/rules")
    async def set_rule(request: Request):  # G-APPR-1, G-APPR-3: only I change rules, from Settings
        body = await read_json(request) or {}
        key = (body.get("pack"), body.get("platform"), body.get("action"))
        if not all(isinstance(k, str) for k in key) or key not in {(r["pack"], r["platform"], r["action"])
                                                                   for r in gate.store.rules()}:
            return _bad("unknown rule", 404)
        try:
            return {"rule": gate.store.set_rule(*key, body.get("mode"), body.get("daily_cap"))}
        except ValueError as exc:
            return _bad(str(exc))

    # ---- teams (Milestone 7, G-TEAM-1..6) ---------------------------------------------------
    def team_fields(body: dict[str, Any]) -> tuple:
        return (body.get("name", ""), body.get("lead", ""), body.get("reviewer", ""), body.get("members", []),
                body.get("max_agents", 2), body.get("max_turns", 100), body.get("notes", ""))

    @app.get("/api/teams")
    async def list_teams() -> dict[str, Any]:
        return {"teams": [t.to_dict() for t in teams.list()]}

    @app.post("/api/teams")
    async def create_team(request: Request):  # G-TEAM-2: saved as a template
        body = await read_json(request)
        if body is None:
            return _bad("bad request")
        try:
            return {"team": teams.save(None, *team_fields(body)).to_dict()}
        except TeamError as exc:
            return _bad(str(exc))

    @app.put("/api/teams/{team_id}")
    async def update_team(team_id: str, request: Request):
        body = await read_json(request)
        if body is None:
            return _bad("bad request")
        try:
            return {"team": teams.save(team_id, *team_fields(body)).to_dict()}
        except TeamError as exc:
            return _bad(str(exc), 404 if "no longer exists" in str(exc) else 400)

    @app.delete("/api/teams/{team_id}")
    async def delete_team(team_id: str):
        return {"ok": True} if teams.delete(team_id) else _bad("team not found", 404)

    @app.post("/api/teams/{team_id}/runs")
    async def start_run(team_id: str, request: Request):  # give the team a task
        body = await read_json(request) or {}
        task = body.get("task")
        if not isinstance(task, str) or not 0 < len(task.strip()) <= 20_000:
            return _bad("write a task (up to 20000 characters)")
        team = teams.get(team_id)
        if team is None:
            return _bad("team not found", 404)
        if team.problem:
            return _bad(f"this team cannot start: {team.problem}")
        if state.paused:
            return _bad("automation is paused: resume it to start a team", 409)
        roles = {rid: role_store.get(rid) for rid in {team.lead, team.reviewer, *team.members} if rid}
        run = new_run(team, roles, task.strip(), run_store, gate,
                      # A blank role model uses the Settings model, as in role chats (R-AUTH-10).
                      lambda **kw: ChatSession(ctrl_c_shield=False, model=config.load()["model"] or None, **kw),
                      roles_mod.ROLE_WORK_ROOT)
        state.runs[run.run_id] = run
        handle = run.run_task = asyncio.create_task(run.run())  # kept: a strong reference, awaited at shutdown
        handle.add_done_callback(lambda _t, rid=run.run_id: state.runs.pop(rid, None))
        return {"run_id": run.run_id}

    @app.get("/api/runs")
    async def list_runs() -> dict[str, Any]:
        return {"runs": run_store.runs()}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: int):  # G-TEAM-4: the board
        rec = run_store.run(run_id)
        if rec is None:
            return _bad("run not found", 404)
        steps = run_store.steps(run_id)
        live = state.runs.get(run_id)
        if live:
            board = live.board()
        else:  # finished or stopped: rebuild the columns from the saved steps
            final = "done" if rec["status"] == "done" else "stopped"
            board = {"status": rec["status"], "members": []}
            for m in rec["members"]:
                mine = [s for s in steps if s["member"] == m["key"]]
                if m["key"] == "lead":  # the lead has no steps: show its report
                    step, output = "leading the team", rec["report"] or ""
                else:
                    step = mine[-1]["instructions"][:1000] if mine else ""
                    output = mine[-1]["output"][-4000:] if mine else ""
                board["members"].append({"key": m["key"], "role": m["role_id"], "name": m["name"], "status": final,
                                         "step": step, "output": output[-4000:]})
        rec.pop("session_ids", None)
        return {"run": rec, "board": board, "steps": steps, "events": run_store.events(run_id),
                "questions": run_store.questions(run_id, open_only=True),
                "closing": bool(live and (live.stopped or live.finished))}  # ended, sessions still closing

    def live_run(run_id: int) -> TeamRun | None:
        return state.runs.get(run_id)

    @app.post("/api/runs/{run_id}/{action}")
    async def control_run(run_id: int, action: str, request: Request):  # G-TEAM-5
        run = live_run(run_id)
        if run is None:
            return _bad("this run is not running", 409 if run_store.run(run_id) else 404)
        body = await read_json(request) or {}
        if action == "pause":
            await run.pause("team")
        elif action == "resume":
            if state.paused:
                return _bad("all automation is paused: resume it at the bottom of the window first", 409)
            await run.resume("team")
        elif action == "stop":
            await run.stop("stopped by you")
        elif action == "message":
            text = body.get("text")
            if not isinstance(text, str) or not 0 < len(text.strip()) <= 4000:
                return _bad("write a message (up to 4000 characters)")
            run.message(text.strip())
        elif action == "answer":
            qid, text = body.get("question_id"), body.get("text")
            if not isinstance(qid, int) or not isinstance(text, str) or not text.strip():
                return _bad("write an answer")
            if not run.answer(qid, text.strip()[:4000]):
                return _bad("this question was already answered", 409)
        else:
            return _bad("unknown action", 404)
        return {"ok": True, "status": run.status()}

    @app.delete("/api/runs/{run_id}")
    async def delete_run(run_id: int):  # keeps the folder (its work), removes records and transcripts
        run = live_run(run_id)
        if run:  # an ended run closes its sessions for a few seconds first (review M7-7 L2)
            ended = run.stopped or run.finished
            return _bad("the run is still closing: try again in a moment" if ended else "stop the run first", 409)
        ids = run_store.delete_run(run_id)
        if ids is None:
            return _bad("run not found", 404)
        return {"ok": True, **await forget_sessions(ids)}

    return app


def make_server(app: FastAPI, port: int) -> uvicorn.Server:
    # N-SEC-1: localhost only, never 0.0.0.0.
    # timeout_graceful_shutdown: never wait long for an open reply stream on Ctrl+C.
    return uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                         timeout_graceful_shutdown=3))


EXIT_ALREADY_RUNNING = 2  # start.bat shows a message box for this (the console may be minimized)


def port_in_use(port: int) -> bool:
    """True if something already listens on 127.0.0.1:port (usually the hub started earlier).
    Only "address in use" counts: another bind error (a port range Windows reserved for Hyper-V,
    WSL or Docker) is left to uvicorn, which prints the real reason (review M4)."""
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError as exc:
            return exc.errno == errno.EADDRINUSE
    return False


def main() -> int:
    notices = scrub_billing_env()  # R-AUTH-3, before anything starts the SDK
    for n in notices:
        print(n)
    port = int(config.load()["port"])
    if port_in_use(port):
        # A second click on the shortcut: say so instead of failing silently in a minimized console.
        print(f"Port {port} is already in use: the hub is probably running already.")
        return EXIT_ALREADY_RUNNING
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
