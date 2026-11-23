"""Shared state of the software team pack (Milestone 12): test results and reviews per commit.

Kept in data/pack-data/devteam/state.db, keyed by the project folder and the exact commit, so a
merge can require that THIS commit passed its tests and was accepted by someone who did not write
it (P-DEV-4). Files starting with "_" are never loaded as connectors.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import types
from contextlib import contextmanager
from contextlib import closing
from pathlib import Path

from hub.connectors.base import ActionError
from hub.engine import packs

PACK = "devteam"
BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,60}")  # never starts with "-": no git option injection
SCHEMA = """
CREATE TABLE IF NOT EXISTS test_results (folder TEXT NOT NULL, commit_id TEXT NOT NULL, passed INTEGER NOT NULL,
    summary TEXT NOT NULL, at REAL NOT NULL, sig TEXT NOT NULL, PRIMARY KEY (folder, commit_id));
CREATE TABLE IF NOT EXISTS review_results (folder TEXT NOT NULL, commit_id TEXT NOT NULL, accepted INTEGER NOT NULL,
    reviewer TEXT NOT NULL, notes TEXT NOT NULL, at REAL NOT NULL, sig TEXT NOT NULL, PRIMARY KEY (folder, commit_id));
"""
# Review M12-3: test code runs with my rights and could write rows into state.db itself. Each row is
# signed with a key that exists only in this hub process (never on disk), so a planted row does not
# count. One key for every connector module that loads this file. After a restart, old rows no
# longer count either: the tests are run again and the branch is reviewed again.
_KEYS = sys.modules.setdefault("hub_pack_devteam__keys", types.ModuleType("hub_pack_devteam__keys"))
if not hasattr(_KEYS, "key"):
    _KEYS.key = secrets.token_bytes(32)
if not hasattr(_KEYS, "locks"):
    _KEYS.locks, _KEYS.guard = {}, threading.Lock()


@contextmanager
def busy(folder: Path, what: str):
    """Review M12-5: while the tests run, nothing else moves main in that folder (so a change to main
    during the run can only come from the tests, and is put back). Never waits: says to try later."""
    with _KEYS.guard:
        lock = _KEYS.locks.setdefault(str(folder).lower(), threading.Lock())
    if not lock.acquire(blocking=False):
        raise ActionError(f"a test run or another change to main is in progress in this folder: {what} again "
                          "in a few minutes")
    try:
        yield
    finally:
        lock.release()


def _sig(*parts: object) -> str:
    return hmac.new(_KEYS.key, "\x1f".join(str(p) for p in parts).encode("utf-8"), hashlib.sha256).hexdigest()
# Signing off, no pager or prompt, no file protocol: git runs only what this connector asks for.
GIT_SAFE = ["-c", "commit.gpgsign=false", "-c", "core.pager=cat", "-c", "credential.helper=",
            "-c", "protocol.file.allow=never", "-c", "core.fsmonitor=false"]


def _no_hooks() -> str:
    """An empty folder outside every project (agents cannot write there): git's hooks come only from
    here, so a planted hook in the project never runs (review M12, a relative path would be inside it)."""
    folder = packs.data_dir(PACK) / "no-hooks"
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(packs.data_dir(PACK) / "state.db", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def folder_of(payload: dict[str, str]) -> Path:
    """The agent's working folder, filled in by the hub (never by the agent) and checked again."""
    try:
        return packs.project_folder(payload.get("folder", ""))
    except ValueError as exc:
        raise ActionError(str(exc)) from None


def git(folder: Path, *args: str, check: bool = True, timeout: int = 60, pre: tuple = ()) -> str:
    import os
    from hub.engine import roles
    # GIT_CEILING_DIRECTORIES: before init, git must not find the hub's own repository above projects/.
    env = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": "",
           "GIT_CEILING_DIRECTORIES": str(roles.ROLE_WORK_ROOT.resolve())}
    full_env = {**{k: v for k, v in os.environ.items() if not k.upper().startswith(("GIT_", "ANTHROPIC"))}, **env}
    try:
        out = subprocess.run(["git", *GIT_SAFE, "-c", f"core.hooksPath={_no_hooks()}", *pre, "-C", str(folder), *args],
                             capture_output=True, text=True,
                             timeout=timeout, env=full_env, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise ActionError("git is not installed on this PC") from None
    except subprocess.TimeoutExpired:
        raise ActionError(f"git {args[0]} took too long") from None
    if check and out.returncode != 0:
        raise ActionError(f"git {args[0]} failed: {(out.stderr or out.stdout).strip()[-800:]}")
    return out.stdout


def is_repo(folder: Path) -> bool:
    return (folder / ".git").is_dir()


def head(folder: Path, ref: str = "HEAD") -> str:
    return git(folder, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def author(folder: Path, ref: str) -> str:
    return git(folder, "log", "-1", "--format=%an", ref).strip()


def branch_name(name: str) -> str:
    name = name.strip()
    if not BRANCH_RE.fullmatch(name) or ".." in name or name.endswith((".", "/", ".lock")) or "//" in name:
        raise ActionError("a branch name is letters, numbers and . _ / - (for example feature/login)")
    return name


def on_main(folder: Path) -> bool:
    """True if HEAD is main, whatever letter case it was switched with (NTFS: "Main" is main, review M12)."""
    ref = git(folder, "symbolic-ref", "-q", "HEAD", check=False).strip()
    if not ref:
        return True  # detached HEAD: never a place to commit
    return ref.lower() == "refs/heads/main"


def same_branch_other_case(folder: Path, name: str) -> bool:
    """A branch that differs from an existing one only in letter case is the same file on Windows."""
    existing = git(folder, "branch", "--format=%(refname:short)").split()
    return any(b.lower() == name.lower() and b != name for b in existing) or (name.lower() == "main" and name != "main")


def record_result(folder: Path, commit: str, passed: bool, summary: str) -> None:
    with closing(db()) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO test_results VALUES (?, ?, ?, ?, ?, ?)",
                     (str(folder), commit, int(passed), summary[-2000:], time.time(),
                      _sig("result", folder, commit, int(passed))))


def result(folder: Path, commit: str) -> dict | None:
    """The signed test result for this commit, or None (a row the hub did not write does not count)."""
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM test_results WHERE folder = ? AND commit_id = ?",
                           (str(folder), commit)).fetchone()
    if not row or not hmac.compare_digest(row["sig"], _sig("result", folder, commit, row["passed"])):
        return None
    return dict(row)


def record_review(folder: Path, commit: str, accepted: bool, reviewer: str, notes: str) -> None:
    with closing(db()) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO review_results VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (str(folder), commit, int(accepted), reviewer, notes[-2000:], time.time(),
                      _sig("review", folder, commit, int(accepted), reviewer)))


def review(folder: Path, commit: str) -> dict | None:
    """The signed review for this commit, or None."""
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM review_results WHERE folder = ? AND commit_id = ?",
                           (str(folder), commit)).fetchone()
    if not row or not hmac.compare_digest(row["sig"], _sig("review", folder, commit, row["accepted"], row["reviewer"])):
        return None
    return dict(row)


def author_name(agent: str) -> str:
    """How a role signs its commits: "<role name> (AI)"."""
    clean = re.sub(r"[^A-Za-z0-9 ._-]", "", agent).strip() or "Agent"
    return f"{clean[:40]} (AI)"
