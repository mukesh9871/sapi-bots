"""Startup checks that keep the app on the Claude subscription.

- R-AUTH-3: strip env vars that would move billing to the API or a cloud provider.
- R-AUTH-4: detect "not logged in" and explain how to log in.
- R-AUTH-5: we only ask the CLI *whether* we are logged in. We never read,
  copy or print the login token (or the account email).
- R-AUTH-8: turn the SDK's reported credential source into a status line.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import MutableMapping

# Env vars that switch Claude Code away from the subscription login.
# ANTHROPIC_AUTH_TOKEN is a bearer token for the API, so it bills the API too.
BILLING_SWITCH_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)

LOGIN_STEPS = (
    "You are not signed in to Claude.\n"
    "To sign in:\n"
    "  1. Open a terminal and run:  claude\n"
    "  2. Type /login and choose \"Claude account with subscription\".\n"
    "  3. Finish the sign-in in your browser, then start this app again."
)


def scrub_billing_env(env: MutableMapping[str, str] | None = None) -> list[str]:
    """R-AUTH-3: remove billing-switch vars from this process. Returns one notice per var removed."""
    env = os.environ if env is None else env
    notices = []
    for name in BILLING_SWITCH_VARS:
        if name in env:
            del env[name]  # the SDK child process inherits os.environ, so this covers it too
            notices.append(
                f"Notice: {name} was set and has been removed for this app, "
                "so usage stays on your Claude subscription."
            )
    return notices


@dataclass
class LoginState:
    logged_in: bool | None  # None = could not tell (CLI missing or unexpected output)
    auth_method: str | None = None  # e.g. "claude.ai"
    subscription: str | None = None  # e.g. "pro", "max", "team"
    detail: str = ""


def find_cli() -> str | None:
    """Prefer the CLI bundled with the SDK (the one the SDK actually runs), else `claude` on PATH."""
    try:
        import claude_agent_sdk

        name = "claude.exe" if os.name == "nt" else "claude"
        bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / name
        if bundled.exists():
            return str(bundled)
    except ImportError:
        pass
    return shutil.which("claude")


def check_login(cli: str | None = None, timeout: float = 20) -> LoginState:
    """R-AUTH-4: ask the CLI for the login state. Only whitelisted, non-secret fields are kept."""
    cli = cli or find_cli()
    if not cli:
        return LoginState(None, detail="Claude Code CLI not found.")
    try:
        out = subprocess.run(
            [cli, "auth", "status"], capture_output=True, text=True, timeout=timeout
        )
        data = json.loads(out.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return LoginState(None, detail=f"Could not read login state ({type(exc).__name__}).")
    return parse_login_status(data)


def parse_login_status(data: dict) -> LoginState:
    # Deliberately ignore email, org id and anything else personal.
    return LoginState(
        logged_in=bool(data.get("loggedIn")),
        auth_method=data.get("authMethod"),
        subscription=data.get("subscriptionType"),
    )


def status_line(api_key_source: str | None, login: LoginState | None = None) -> tuple[bool, str]:
    """R-AUTH-8: (ok, text) for the status line.

    `api_key_source` comes from the SDK init message: "none" means no API key
    is in use, i.e. the claude.ai subscription login.
    """
    if api_key_source and api_key_source != "none":
        return False, f"WARNING: an API credential is in use ({api_key_source}). Usage may be billed to the API."
    if login is not None and login.logged_in is False:
        return False, "Not signed in to Claude."
    if login is not None and login.auth_method not in (None, "claude.ai"):
        return False, f"WARNING: signed in with '{login.auth_method}', not a Claude subscription."
    plan = f" ({login.subscription})" if login and login.subscription else ""
    return True, f"Signed in with Claude subscription{plan}"
