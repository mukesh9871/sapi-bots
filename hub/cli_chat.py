"""Milestone 1: chat with Claude in the terminal.

Run:  .venv\\Scripts\\python -m hub.cli_chat [--personality fun|straight|explainer] [--model sonnet] [--no-web]

Commands while chatting:  /usage  /retry  /quit
Ctrl+C while a reply is streaming stops it (F-CHAT-4). Ctrl+C at the prompt quits.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
import threading
from datetime import datetime

from hub.auth_check import LOGIN_STEPS, check_login, scrub_billing_env, status_line
from hub.chat.claude_session import ChatSession
from hub.chat.personalities import PERSONALITIES

FRIENDLY_ERRORS = {
    # N-REL-1 / R-AUTH-4 / R-AUTH-6: plain words instead of raw errors.
    "authentication_failed": LOGIN_STEPS,
    "rate_limit": "You have reached your Claude subscription usage limit.",
    "billing_error": "Claude reported a billing problem with this account.",
    "server_error": "Claude's servers had a problem. Try /retry in a moment.",
}


def format_reset(rate_limit: dict | None) -> str:
    ts = (rate_limit or {}).get("resets_at")
    return f" It resets at {datetime.fromtimestamp(ts):%H:%M on %d %b}." if ts else ""


def format_usage(done: dict) -> str:
    u = done.get("usage", {})
    fresh_in = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
    return (
        f"[in {fresh_in} + cached {u.get('cache_read_input_tokens', 0)}, "
        f"out {u.get('output_tokens', 0)} tokens, {done.get('duration_ms', 0) / 1000:.1f}s]"
    )


async def ainput(prompt: str) -> str:
    """input() on a daemon thread, so a blocked prompt never stops the app from exiting."""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[str] = loop.create_future()

    def worker() -> None:
        try:
            result: str | BaseException = input(prompt)
        except BaseException as exc:  # EOFError etc.
            result = exc
        def deliver() -> None:
            if fut.done():
                return
            if isinstance(result, BaseException):
                fut.set_exception(result)
            else:
                fut.set_result(result)
        loop.call_soon_threadsafe(deliver)

    threading.Thread(target=worker, daemon=True).start()
    return await fut


async def stream_reply(chat: ChatSession, text: str) -> tuple[dict | None, str | None]:
    """Print one streamed reply. Returns (done data, error code)."""
    done, error = None, None
    async for ev in chat.send(text):
        if ev.kind == "text":
            print(ev.text, end="", flush=True)
        elif ev.kind == "tool":
            label = "searching the web" if ev.text == "WebSearch" else f"using {ev.text}"
            print(f"\n  ({label}...)\n", flush=True)
        elif ev.kind == "error":
            error = ev.text
        elif ev.kind == "done":
            done = ev.data
    print()
    return done, error


async def main() -> int:
    ap = argparse.ArgumentParser(description="Terminal chat (Milestone 1)")
    ap.add_argument("--personality", choices=sorted(PERSONALITIES), default="straight")
    ap.add_argument("--model", default=None, help="model alias, e.g. sonnet, haiku, opus")
    ap.add_argument("--no-web", action="store_true", help="turn web search off (F-WEB-3)")
    args = ap.parse_args()

    for notice in scrub_billing_env():  # R-AUTH-3, before the SDK starts
        print(notice)

    login = check_login()
    if login.logged_in is False:
        print(LOGIN_STEPS)
        return 1

    loop = asyncio.get_running_loop()
    state = {"chat": None, "streaming": False}

    def on_sigint(*_: object) -> None:
        # Streaming: stop the reply. At the prompt: quit straight away.
        if state["streaming"] and state["chat"]:
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(state["chat"].stop()))
            print("\n  (stopping...)", flush=True)
        else:
            print()
            os._exit(0)

    signal.signal(signal.SIGINT, on_sigint)

    async with ChatSession(personality=args.personality, model=args.model, web_enabled=not args.no_web) as chat:
        state["chat"] = chat
        print(f"Chat ready ({args.personality}). /usage, /retry, /quit. Ctrl+C stops a reply.\n")
        last_failed, cumulative, shown_status = None, None, False
        while True:
            try:
                line = (await ainput("you> ")).strip()
            except EOFError:
                break
            if not line:
                continue
            if line == "/quit":
                break
            if line == "/usage":
                print(f"  cumulative: {cumulative or 'nothing yet'}\n  rate limit: {chat.last_rate_limit or 'no event yet'}")
                continue
            if line == "/retry":
                if not last_failed:
                    print("  Nothing to retry.")
                    continue
                line = last_failed

            print("bot> ", end="", flush=True)
            state["streaming"] = True
            try:
                done, error = await stream_reply(chat, line)
            finally:
                state["streaming"] = False

            if not shown_status and chat.api_key_source is not None:  # R-AUTH-8
                ok, text = status_line(chat.api_key_source, login)
                print(f"  status: {'OK' if ok else '!!'} {text}")
                shown_status = True

            if error:
                msg = FRIENDLY_ERRORS.get(error, f"Something went wrong ({error}). Try /retry.")
                if error == "rate_limit":
                    msg += format_reset(chat.last_rate_limit)
                print(f"\n  {msg}\n  Your message was kept. Type /retry to send it again.")  # R-AUTH-6
                last_failed = line
            elif done and str(done.get("terminal_reason", "")).startswith("aborted"):
                print("  (stopped)")
            elif done:
                cumulative = done.get("model_usage")
                print(f"  {format_usage(done)}")
                last_failed = None
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
