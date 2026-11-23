"""Schedules (Milestone 8): when a role or team task runs, and the record of every run.

G-SCHED-1: a task runs on a schedule or on a trigger:
    daily 08:00                     every day at a time
    weekly mon,fri 09:30            on chosen days at a time ("custom")
    every 90 minutes                at a fixed interval (5 minutes to 7 days)
    file C:\\path\\to\\folder         when a new file appears in a folder
    after <schedule id>             when another schedule's run finishes well
G-SCHED-2: runs happen only while the app is on. A run that came due while the app was off
(or paused) is listed as missed; I choose Run late or Dismiss. Times are local PC time.
Stored in data/hub.db: tables schedules and schedule_runs.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from hub.gate import store as gate_store

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MIN_MINUTES, MAX_MINUTES = 5, 7 * 24 * 60
MAX_NAME, MAX_TASK = 60, 20_000
MAX_RESULT = 20_000
KINDS = ("role", "team")
TIME_RE = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")

SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    pack TEXT NOT NULL DEFAULT 'local',  -- G-PACK-1: a pack's schedules go when the pack is removed
    pack_key TEXT,                       -- the schedule's file name inside its pack
    kind TEXT NOT NULL,                  -- role | team
    target TEXT NOT NULL,                -- role id or team id
    task TEXT NOT NULL,
    spec TEXT NOT NULL,                  -- JSON, see parse_when
    enabled INTEGER NOT NULL DEFAULT 1,
    next_run REAL,                       -- unix time; NULL for triggers
    state TEXT NOT NULL DEFAULT '{}',    -- file trigger: the files already seen
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS schedule_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id INTEGER NOT NULL,
    due_at REAL NOT NULL,
    started_at REAL,
    ended_at REAL,
    status TEXT NOT NULL,   -- running | done | failed | stopped | skipped | missed | ran-late | dismissed
    reason TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '',
    ref TEXT NOT NULL DEFAULT '',        -- "chat:12" or "team:5": where to read the full result
    missed_count INTEGER NOT NULL DEFAULT 0,
    late_of INTEGER,                     -- a late run: the missed run it makes up for
    extra TEXT NOT NULL DEFAULT ''       -- a missed file trigger: the files it was about
);
CREATE INDEX IF NOT EXISTS schedule_runs_by_schedule ON schedule_runs (schedule_id, id);
-- Milestone 9: what a "tell me" schedule finished, in the order runs finished. AUTOINCREMENT: an id is
-- never used twice, even after a schedule or a pack is deleted, so the window's cursor never skips one.
CREATE TABLE IF NOT EXISTS notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    schedule_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    result TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    ended_at REAL NOT NULL
);
"""


class ScheduleError(ValueError):
    """A schedule that cannot be saved. The message is shown to the user."""


# ---- when: parse, describe, next run ------------------------------------------------------

def parse_when(text: str) -> dict[str, Any]:
    """Text form (pack files, see the module doc) to a spec dict. Raises ScheduleError."""
    if not isinstance(text, str):
        raise ScheduleError("say when it runs")
    words = text.strip().split(None, 1)
    kind, rest = (words[0].lower(), words[1].strip() if len(words) > 1 else "") if words else ("", "")
    if kind == "daily":
        return check_spec({"type": "daily", "time": rest})
    if kind == "weekly":
        days, _, at = rest.rpartition(" ")
        return check_spec({"type": "weekly", "days": [d.strip().lower() for d in days.split(",") if d.strip()],
                           "time": at})
    if kind == "every":
        m = re.fullmatch(r"([0-9]{1,5})\s*(minutes?|hours?)", rest.lower())
        if not m:
            raise ScheduleError("write it as 'every 90 minutes' or 'every 2 hours'")
        n = int(m.group(1)) * (60 if m.group(2).startswith("hour") else 1)
        return check_spec({"type": "interval", "minutes": n})
    if kind == "file":
        return check_spec({"type": "file", "folder": rest})
    if kind == "after":
        return check_spec({"type": "after", "schedule": int(rest) if rest.isascii() and rest.isdigit() else rest})
    raise ScheduleError("start with daily, weekly, every, file or after")


def check_spec(spec: Any) -> dict[str, Any]:
    """A clean spec dict, or ScheduleError. Also used for what the window sends."""
    if not isinstance(spec, dict):
        raise ScheduleError("bad schedule")
    kind = spec.get("type")
    if kind in ("daily", "weekly"):
        at = spec.get("time")
        if not isinstance(at, str) or not TIME_RE.fullmatch(at.strip()):
            raise ScheduleError("time must be HH:MM (24 hours)")
        h, m = TIME_RE.fullmatch(at.strip()).groups()
        out = {"type": kind, "time": f"{int(h):02d}:{m}"}
        if kind == "weekly":
            days = spec.get("days")
            if not isinstance(days, list) or not days or not all(d in DAYS for d in days):
                raise ScheduleError("pick at least one day (mon to sun)")
            out["days"] = [d for d in DAYS if d in days]
        return out
    if kind == "interval":
        n = spec.get("minutes")
        if not isinstance(n, int) or isinstance(n, bool) or not MIN_MINUTES <= n <= MAX_MINUTES:
            raise ScheduleError(f"the interval must be {MIN_MINUTES} minutes to 7 days")
        return {"type": "interval", "minutes": n}
    if kind == "file":
        folder = spec.get("folder")
        if not isinstance(folder, str) or not folder.strip() or len(folder) > 400 or any(ord(c) < 32 for c in folder):
            raise ScheduleError("name the folder to watch")
        path = Path(folder.strip())
        if not path.is_absolute() or str(path).startswith("\\\\"):  # no network shares
            raise ScheduleError("the folder must be a full local path, for example C:\\Users\\me\\Inbox")
        return {"type": "file", "folder": str(path)}
    if kind == "after":
        sid = spec.get("schedule")
        if isinstance(sid, bool) or not isinstance(sid, (int, str)) or not str(sid).strip():
            raise ScheduleError("pick the schedule it follows")
        return {"type": "after", "schedule": sid}
    raise ScheduleError("unknown schedule type")


def describe(spec: dict[str, Any], names: dict[int, str] | None = None) -> str:
    kind = spec.get("type")
    if kind == "daily":
        return f"every day at {spec['time']}"
    if kind == "weekly":
        return f"{', '.join(d.capitalize() for d in spec['days'])} at {spec['time']}"
    if kind == "interval":
        n = spec["minutes"]
        return f"every {n // 60} hours" if n % 60 == 0 and n >= 120 else f"every {n} minutes"
    if kind == "file":
        return f"when a new file appears in {spec['folder']}"
    if kind == "after":
        sid = spec["schedule"]
        return f"after \"{(names or {}).get(sid, sid)}\" finishes"
    return "?"


def next_run(spec: dict[str, Any], after: float) -> float | None:
    """The first run time strictly after `after` (unix time), or None for a trigger."""
    kind = spec.get("type")
    if kind == "interval":
        return after + spec["minutes"] * 60
    if kind not in ("daily", "weekly"):
        return None
    h, m = (int(x) for x in spec["time"].split(":"))
    base = dt.datetime.fromtimestamp(after)
    for add in range(0, 8):
        day = base.date() + dt.timedelta(days=add)
        when = dt.datetime.combine(day, dt.time(h, m))
        if when.timestamp() <= after:
            continue
        if kind == "weekly" and DAYS[day.weekday()] not in spec["days"]:
            continue
        return when.timestamp()
    return None  # not reached for a valid spec


# ---- the store ---------------------------------------------------------------------------------

class ScheduleStore:
    def __init__(self, path: Path | None = None):
        self.path = path or gate_store.DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._conn()) as db, db:
            db.executescript(SCHEMA)
            try:  # Milestone 9: tell me in the window when a run is done (reminders)
                db.execute("ALTER TABLE schedules ADD COLUMN notify INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # already there

    def _conn(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _row(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["spec"] = json.loads(d["spec"])
        d["state"] = json.loads(d["state"])
        d["enabled"] = bool(d["enabled"])
        d["notify"] = bool(d.get("notify"))
        return d

    # schedules
    def add(self, name: str, kind: str, target: str, task: str, spec: dict[str, Any], enabled: bool = True,
            pack: str = "local", pack_key: str | None = None, now: float | None = None,
            notify: bool = False) -> dict[str, Any]:
        spec = check_spec(spec)
        self._check(name, kind, target, task)
        now = time.time() if now is None else now
        nxt = next_run(spec, now) if enabled else None
        with closing(self._conn()) as db, db:
            cur = db.execute("INSERT INTO schedules (name, pack, pack_key, kind, target, task, spec, enabled, next_run, "
                             "created_at, notify) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (name.strip(), pack, pack_key, kind, target, task.strip(), json.dumps(spec), int(enabled),
                              nxt, now, int(bool(notify))))
            return self.get(cur.lastrowid, db)

    def update(self, sid: int, name: str, kind: str, target: str, task: str, spec: dict[str, Any],
               now: float | None = None, notify: bool | None = None) -> dict[str, Any]:
        spec = check_spec(spec)
        self._check(name, kind, target, task)
        old = self.get(sid)
        if old is None:
            raise ScheduleError("this schedule no longer exists")
        now = time.time() if now is None else now
        nxt = next_run(spec, now) if old["enabled"] else None
        state = old["state"] if old["spec"] == spec else {}  # a new folder starts with a new baseline
        with closing(self._conn()) as db, db:
            db.execute("UPDATE schedules SET name = ?, kind = ?, target = ?, task = ?, spec = ?, next_run = ?, "
                       "state = ?, notify = ? WHERE id = ?",
                       (name.strip(), kind, target, task.strip(), json.dumps(spec), nxt, json.dumps(state),
                        int(old["notify"] if notify is None else bool(notify)), sid))
        return self.get(sid)

    @staticmethod
    def _check(name: str, kind: str, target: str, task: str) -> None:
        if not isinstance(name, str) or not 0 < len(name.strip()) <= MAX_NAME or any(ord(c) < 32 for c in name):
            raise ScheduleError(f"name must be 1 to {MAX_NAME} characters on one line")
        if kind not in KINDS:
            raise ScheduleError("run a role or a team")
        if not isinstance(target, str) or not target:
            raise ScheduleError("pick the role or team")
        if not isinstance(task, str) or not 0 < len(task.strip()) <= MAX_TASK:
            raise ScheduleError(f"write the task (up to {MAX_TASK} characters)")

    def set_enabled(self, sid: int, enabled: bool, now: float | None = None) -> dict[str, Any] | None:
        s = self.get(sid)
        if s is None:
            return None
        now = time.time() if now is None else now
        nxt = next_run(s["spec"], now) if enabled else None  # switching on never fires a past time
        # A file trigger switched on starts a new baseline: files from while it was off do not count.
        state = "{}" if enabled and not s["enabled"] else json.dumps(s["state"])
        with closing(self._conn()) as db, db:
            db.execute("UPDATE schedules SET enabled = ?, next_run = ?, state = ? WHERE id = ?",
                       (int(enabled), nxt, state, sid))
        return self.get(sid)

    def set_next(self, sid: int, nxt: float | None) -> None:
        with closing(self._conn()) as db, db:
            db.execute("UPDATE schedules SET next_run = ? WHERE id = ?", (nxt, sid))

    def set_state(self, sid: int, state: dict[str, Any]) -> None:
        with closing(self._conn()) as db, db:
            db.execute("UPDATE schedules SET state = ? WHERE id = ?", (json.dumps(state), sid))

    def get(self, sid: int, db: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        if db is None:
            with closing(self._conn()) as own:
                return self.get(sid, own)
        r = db.execute("SELECT * FROM schedules WHERE id = ?", (sid,)).fetchone()
        return self._row(r) if r else None

    def list(self) -> list[dict[str, Any]]:
        with closing(self._conn()) as db:
            return [self._row(r) for r in db.execute("SELECT * FROM schedules ORDER BY pack != 'local', pack, name")]

    def would_loop(self, sid: int | None, spec: dict[str, Any]) -> bool:
        """True if following "after" links from spec leads back to sid (A after B after A)."""
        if sid is None or spec.get("type") != "after":
            return False
        seen, cur = set(), spec.get("schedule")
        while isinstance(cur, int) and cur not in seen:
            if cur == sid:
                return True
            seen.add(cur)
            other = self.get(cur)
            cur = other["spec"].get("schedule") if other and other["spec"].get("type") == "after" else None
        return False

    def delete(self, sid: int) -> bool:
        with closing(self._conn()) as db, db:
            n = db.execute("DELETE FROM schedules WHERE id = ?", (sid,)).rowcount
            db.execute("DELETE FROM schedule_runs WHERE schedule_id = ?", (sid,))
        return bool(n)

    def delete_pack(self, pack: str) -> int:
        """G-PACK-2: removing a pack removes its schedules and their run records."""
        with closing(self._conn()) as db, db:
            ids = [r[0] for r in db.execute("SELECT id FROM schedules WHERE pack = ?", (pack,))]
            for sid in ids:
                db.execute("DELETE FROM schedule_runs WHERE schedule_id = ?", (sid,))
            db.execute("DELETE FROM schedules WHERE pack = ?", (pack,))
        return len(ids)

    # runs
    def start_run(self, sid: int, due_at: float, late_of: int | None = None) -> int:
        with closing(self._conn()) as db, db:
            return db.execute("INSERT INTO schedule_runs (schedule_id, due_at, started_at, status, late_of) "
                              "VALUES (?, ?, ?, 'running', ?)", (sid, due_at, time.time(), late_of)).lastrowid

    def end_run(self, run_id: int, status: str, result: str = "", reason: str = "", ref: str = "") -> None:
        with closing(self._conn()) as db, db:
            now = time.time()
            ended = db.execute("UPDATE schedule_runs SET status = ?, result = ?, reason = ?, ref = ?, ended_at = ? "
                               "WHERE id = ? AND status = 'running'",
                               (status, result[-MAX_RESULT:], reason[:500], ref, now, run_id)).rowcount
            if ended and status in ("done", "failed", "budget"):
                # A notice only for a run that ends while "tell me" is on (review M9: ticking it later
                # must not bring back old results).
                db.execute("INSERT INTO notices (run_id, schedule_id, name, status, result, reason, ended_at) "
                           "SELECT r.id, s.id, s.name, ?, ?, ?, ? FROM schedule_runs r JOIN schedules s "
                           "ON s.id = r.schedule_id WHERE r.id = ? AND s.notify = 1",
                           (status, result[-2000:], reason[:500], now, run_id))

    def note_run(self, sid: int, due_at: float, status: str, reason: str, extra: str = "") -> int:
        """A run that did not happen: missed (G-SCHED-2) or skipped. Repeated misses of one schedule
        are counted on its open missed row, so the list shows each schedule once."""
        with closing(self._conn()) as db, db:
            if status == "missed":
                row = db.execute("SELECT id, extra FROM schedule_runs WHERE schedule_id = ? AND status = 'missed'",
                                 (sid,)).fetchone()
                if row:
                    both = (row[1] + extra)[-8000:] if extra else row[1]  # file names of every miss
                    db.execute("UPDATE schedule_runs SET missed_count = missed_count + 1, reason = ?, extra = ? "
                               "WHERE id = ?", (reason, both, row[0]))
                    return row[0]
            return db.execute("INSERT INTO schedule_runs (schedule_id, due_at, ended_at, status, reason, missed_count, "
                              "extra) VALUES (?, ?, ?, ?, ?, ?, ?)", (sid, due_at, time.time(), status, reason,
                                                                     1 if status == "missed" else 0, extra[-8000:])).lastrowid

    def run(self, run_id: int) -> dict[str, Any] | None:
        with closing(self._conn()) as db:
            r = db.execute("SELECT * FROM schedule_runs WHERE id = ?", (run_id,)).fetchone()
            return dict(r) if r else None

    def runs(self, sid: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self._conn()) as db:
            if sid is None:
                rows = db.execute("SELECT * FROM schedule_runs ORDER BY id DESC LIMIT ?", (limit,))
            else:
                rows = db.execute("SELECT * FROM schedule_runs WHERE schedule_id = ? ORDER BY id DESC LIMIT ?",
                                  (sid, limit))
            return [dict(r) for r in rows]

    def missed(self) -> list[dict[str, Any]]:
        with closing(self._conn()) as db:
            return [dict(r) for r in db.execute(
                "SELECT r.*, s.name FROM schedule_runs r JOIN schedules s ON s.id = r.schedule_id "
                "WHERE r.status = 'missed' ORDER BY r.due_at")]

    def close_missed(self, run_id: int, status: str) -> bool:
        """ran-late or dismissed. False if it was not an open missed run."""
        with closing(self._conn()) as db, db:
            return bool(db.execute("UPDATE schedule_runs SET status = ? WHERE id = ? AND status = 'missed'",
                                   (status, run_id)).rowcount)

    def notices(self, after: int = 0, limit: int = 20) -> list[dict[str, Any]]:
        """Finished runs of schedules that tell me (notify), newer than run id `after`."""
        with closing(self._conn()) as db:
            return [dict(r) for r in db.execute("SELECT * FROM notices WHERE id > ? ORDER BY id LIMIT ?",
                                                (after, limit))]

    def last_notice(self) -> int:
        with closing(self._conn()) as db:
            row = db.execute("SELECT MAX(id) FROM notices").fetchone()
        return row[0] or 0

    def last_run(self, sid: int) -> dict[str, Any] | None:
        with closing(self._conn()) as db:
            r = db.execute("SELECT * FROM schedule_runs WHERE schedule_id = ? AND status NOT IN ('missed', "
                           "'dismissed') ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
            return dict(r) if r else None

    def stop_unfinished(self) -> int:
        """At start-up: runs left 'running' by a crash or restart are marked stopped, never re-run."""
        with closing(self._conn()) as db, db:
            return db.execute("UPDATE schedule_runs SET status = 'stopped', reason = 'the app stopped while this ran', "
                              "ended_at = ? WHERE status = 'running'", (time.time(),)).rowcount
