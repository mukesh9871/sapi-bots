"""Team runs in <app folder>/data/hub.db (Milestone 7): runs, steps, events and questions.

The board (G-TEAM-4) reads these, so it survives a page reload. After a restart, a run that
was still running or paused is marked "stopped" with its work kept: nothing re-runs by itself.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from hub.gate import store as gate_store  # same database file as the approval gate

SCHEMA = """
CREATE TABLE IF NOT EXISTS team_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id TEXT NOT NULL, team_name TEXT NOT NULL, task TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',  -- running | paused | done | stopped | failed
    reason TEXT NOT NULL DEFAULT '',         -- why it stopped or failed
    folder TEXT NOT NULL,
    report TEXT NOT NULL DEFAULT '',
    members TEXT NOT NULL DEFAULT '[]',      -- JSON [{key, role_id, name}]
    max_agents INTEGER NOT NULL, max_turns INTEGER NOT NULL,
    turns_used INTEGER NOT NULL DEFAULT 0,
    session_ids TEXT NOT NULL DEFAULT '[]',  -- for deleting transcripts with the run
    created_at REAL NOT NULL, ended_at REAL
);
CREATE TABLE IF NOT EXISTS team_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL, member TEXT NOT NULL,
    instructions TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',   -- queued | working | done | interrupted | failed
    output TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL, started_at REAL, ended_at REAL
);
CREATE TABLE IF NOT EXISTS team_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL, at REAL NOT NULL, who TEXT NOT NULL, text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS team_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL, member TEXT NOT NULL, question TEXT NOT NULL,
    answer TEXT, asked_at REAL NOT NULL, answered_at REAL
);
CREATE INDEX IF NOT EXISTS team_steps_run ON team_steps(run_id, id);
CREATE INDEX IF NOT EXISTS team_events_run ON team_events(run_id, id);
"""

MAX_TEXT = 20_000  # stored output per step (the board shows the end of it)


class RunStore:
    def __init__(self, path: Path | None = None):
        self.path = path or gate_store.DB_PATH  # looked up now, so tests can redirect it
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._conn()) as db, db:
            db.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def _exec(self, sql: str, args: tuple = ()) -> int:
        with closing(self._conn()) as db, db:
            return db.execute(sql, args).lastrowid

    # ---- runs ---------------------------------------------------------------------------
    def create_run(self, team: Any, task: str, folder: str, members: list[dict]) -> int:
        return self._exec("INSERT INTO team_runs (team_id, team_name, task, folder, members, max_agents, max_turns, "
                          "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (team.id, team.name, task, folder, json.dumps(members), team.max_agents, team.max_turns,
                           time.time()))

    def set_folder(self, run_id: int, folder: str) -> None:
        self._exec("UPDATE team_runs SET folder = ? WHERE id = ?", (folder, run_id))

    def set_status(self, run_id: int, status: str, reason: str = "") -> None:
        ended = time.time() if status in ("done", "stopped", "failed") else None
        self._exec("UPDATE team_runs SET status = ?, reason = ?, ended_at = COALESCE(?, ended_at) WHERE id = ?",
                   (status, reason, ended, run_id))

    def set_report(self, run_id: int, report: str) -> None:
        self._exec("UPDATE team_runs SET report = ? WHERE id = ?", (report[:MAX_TEXT], run_id))

    def add_turns(self, run_id: int, n: int = 1) -> int:
        with closing(self._conn()) as db, db:
            db.execute("UPDATE team_runs SET turns_used = turns_used + ? WHERE id = ?", (n, run_id))
            return db.execute("SELECT turns_used FROM team_runs WHERE id = ?", (run_id,)).fetchone()[0]

    def add_session(self, run_id: int, session_id: str) -> None:
        with closing(self._conn()) as db, db:
            ids = json.loads(db.execute("SELECT session_ids FROM team_runs WHERE id = ?", (run_id,)).fetchone()[0])
            if session_id not in ids:
                db.execute("UPDATE team_runs SET session_ids = ? WHERE id = ?", (json.dumps(ids + [session_id]), run_id))

    def run(self, run_id: int) -> dict[str, Any] | None:
        with closing(self._conn()) as db:
            row = db.execute("SELECT * FROM team_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return {**dict(row), "members": json.loads(row["members"]), "session_ids": json.loads(row["session_ids"])}

    def runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self._conn()) as db:
            rows = db.execute("SELECT id, team_id, team_name, task, status, reason, created_at, ended_at, turns_used, "
                              "max_turns FROM team_runs ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(r) for r in rows]

    def stop_unfinished(self) -> int:
        """At start-up: runs left running or paused by a crash or restart are marked stopped."""
        with closing(self._conn()) as db, db:
            n = db.execute("UPDATE team_runs SET status = 'stopped', reason = 'stopped by a restart of the app', "
                           "ended_at = ? WHERE status IN ('running', 'paused')", (time.time(),)).rowcount
            db.execute("UPDATE team_steps SET status = 'interrupted', ended_at = ? WHERE status IN ('queued', 'working')",
                       (time.time(),))
            db.execute("UPDATE team_questions SET answer = '(the app restarted)', answered_at = ? WHERE answer IS NULL",
                       (time.time(),))
            return n

    def delete_run(self, run_id: int) -> list[str] | None:
        """Deletes the run's records (not its folder: that is its work). Returns its session ids."""
        run = self.run(run_id)
        if run is None:
            return None
        with closing(self._conn()) as db, db:
            for table in ("team_steps", "team_events", "team_questions"):
                db.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
            db.execute("DELETE FROM team_runs WHERE id = ?", (run_id,))
        return run["session_ids"]

    def delete_all(self) -> list[str]:
        """Deletes every run's records (Delete all history). Returns all their session ids."""
        with closing(self._conn()) as db, db:
            ids = [sid for (raw,) in db.execute("SELECT session_ids FROM team_runs") for sid in json.loads(raw)]
            for table in ("team_steps", "team_events", "team_questions", "team_runs"):
                db.execute(f"DELETE FROM {table}")
        return ids

    # ---- steps, events, questions -------------------------------------------------------
    def add_step(self, run_id: int, member: str, instructions: str) -> int:
        return self._exec("INSERT INTO team_steps (run_id, member, instructions, created_at) VALUES (?, ?, ?, ?)",
                          (run_id, member, instructions, time.time()))

    def step_started(self, step_id: int) -> None:
        self._exec("UPDATE team_steps SET status = 'working', started_at = ? WHERE id = ?", (time.time(), step_id))

    def step_output(self, step_id: int, output: str) -> None:
        self._exec("UPDATE team_steps SET output = ? WHERE id = ?", (output[-MAX_TEXT:], step_id))

    def step_ended(self, step_id: int, status: str, output: str) -> None:
        self._exec("UPDATE team_steps SET status = ?, output = ?, ended_at = ? WHERE id = ?",
                   (status, output[-MAX_TEXT:], time.time(), step_id))

    def steps(self, run_id: int) -> list[dict[str, Any]]:
        with closing(self._conn()) as db:
            return [dict(r) for r in db.execute("SELECT * FROM team_steps WHERE run_id = ? ORDER BY id", (run_id,))]

    def event(self, run_id: int, who: str, text: str) -> None:
        self._exec("INSERT INTO team_events (run_id, at, who, text) VALUES (?, ?, ?, ?)",
                   (run_id, time.time(), who, text[:2000]))

    def events(self, run_id: int, limit: int = 200) -> list[dict[str, Any]]:
        with closing(self._conn()) as db:
            rows = db.execute("SELECT * FROM team_events WHERE run_id = ? ORDER BY id DESC LIMIT ?", (run_id, limit))
            return list(reversed([dict(r) for r in rows]))

    def ask(self, run_id: int, member: str, question: str) -> int:
        return self._exec("INSERT INTO team_questions (run_id, member, question, asked_at) VALUES (?, ?, ?, ?)",
                          (run_id, member, question[:4000], time.time()))

    def answer(self, question_id: int, answer: str) -> bool:
        with closing(self._conn()) as db, db:
            return db.execute("UPDATE team_questions SET answer = ?, answered_at = ? WHERE id = ? AND answer IS NULL",
                              (answer[:4000], time.time(), question_id)).rowcount == 1

    def questions(self, run_id: int, open_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM team_questions WHERE run_id = ?" + (" AND answer IS NULL" if open_only else "")
        with closing(self._conn()) as db:
            return [dict(r) for r in db.execute(sql + " ORDER BY id", (run_id,))]
