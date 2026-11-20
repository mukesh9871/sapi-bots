"""Approval rules, approvals inbox and activity log in <app folder>/data/hub.db (Milestone 6).

- G-APPR-1/3: one rule per (pack, platform, action). A missing rule is created as Ask.
- G-APPR-2: held items wait in the approvals table until approved or rejected.
- G-LOG-1: every acting call is written to the activity table. It is append-only: SQLite
  triggers refuse UPDATE and DELETE, so entries are never edited.
- N-REL-3: an action gets a "started" row before it runs and a "done"/"failed" row after.
  A call with only "started" (crash) is marked "unknown" and never re-run automatically.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "hub.db"

MODES = ("auto", "ask", "never")
DEFAULT_CAP = 5  # G-APPR-4: Auto runs per rule per day
MAX_CAP = 1000

SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    pack TEXT NOT NULL, platform TEXT NOT NULL, action TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'ask' CHECK (mode IN ('auto', 'ask', 'never')),
    daily_cap INTEGER NOT NULL DEFAULT 5 CHECK (daily_cap BETWEEN 1 AND 1000),
    PRIMARY KEY (pack, platform, action)
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    agent TEXT NOT NULL, pack TEXT NOT NULL, platform TEXT NOT NULL,
    action_id TEXT NOT NULL,         -- connector action, e.g. demo.post_note
    payload TEXT NOT NULL,           -- JSON, exactly what will be done (my edit replaces it)
    proposed TEXT NOT NULL DEFAULT '',  -- JSON, what the agent asked for; never changed (N-REL-3)
    reason TEXT NOT NULL DEFAULT '', -- why it was held: '' (Ask), daily cap, paused
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | failed | rejected
    result TEXT NOT NULL DEFAULT '',
    decided_at REAL
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    call_id TEXT NOT NULL,           -- one action run; links its started and done rows
    agent TEXT NOT NULL, pack TEXT NOT NULL, platform TEXT NOT NULL, action_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    decision TEXT NOT NULL,          -- auto | approved | held | blocked | rejected
    outcome TEXT NOT NULL DEFAULT '',  -- started | done | failed | unknown | '' (not run)
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS activity_call ON activity(call_id);
CREATE TRIGGER IF NOT EXISTS activity_no_update BEFORE UPDATE ON activity
BEGIN SELECT RAISE(ABORT, 'the activity log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS activity_no_delete BEFORE DELETE ON activity
BEGIN SELECT RAISE(ABORT, 'the activity log is append-only'); END;
"""


class GateStore:
    def __init__(self, path: Path | None = None):
        self.path = path or DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._conn()) as db, db:
            db.executescript(SCHEMA)
            if "proposed" not in {r[1] for r in db.execute("PRAGMA table_info(approvals)")}:
                db.execute("ALTER TABLE approvals ADD COLUMN proposed TEXT NOT NULL DEFAULT ''")

    def _conn(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    # ---- rules (G-APPR-1, G-APPR-3) -----------------------------------------------------
    def rule(self, pack: str, platform: str, action: str) -> dict[str, Any]:
        """The rule for this action. A new one is created as Ask (G-APPR-3)."""
        with closing(self._conn()) as db, db:
            db.execute("INSERT OR IGNORE INTO rules (pack, platform, action) VALUES (?, ?, ?)",
                       (pack, platform, action))
            row = db.execute("SELECT * FROM rules WHERE pack = ? AND platform = ? AND action = ?",
                             (pack, platform, action)).fetchone()
            return dict(row)

    def rules(self) -> list[dict[str, Any]]:
        with closing(self._conn()) as db:
            return [dict(r) for r in db.execute("SELECT * FROM rules ORDER BY pack, platform, action")]

    def set_rule(self, pack: str, platform: str, action: str, mode: str, daily_cap: int) -> dict[str, Any]:
        """Only the user changes a rule (from Settings). No agent tool can call this (G-APPR-3)."""
        if mode not in MODES:
            raise ValueError("mode must be auto, ask or never")
        if not isinstance(daily_cap, int) or isinstance(daily_cap, bool) or not 1 <= daily_cap <= MAX_CAP:
            raise ValueError(f"daily cap must be 1 to {MAX_CAP}")
        with closing(self._conn()) as db, db:
            db.execute("INSERT INTO rules VALUES (?, ?, ?, ?, ?) ON CONFLICT (pack, platform, action) "
                       "DO UPDATE SET mode = excluded.mode, daily_cap = excluded.daily_cap",
                       (pack, platform, action, mode, daily_cap))
        return self.rule(pack, platform, action)

    # ---- activity log (G-LOG-1, N-REL-3) ------------------------------------------------
    def log(self, call_id: str, agent: str, pack: str, platform: str, action_id: str,
            payload: dict, decision: str, outcome: str = "", detail: str = "") -> None:
        with closing(self._conn()) as db, db:
            db.execute("INSERT INTO activity (at, call_id, agent, pack, platform, action_id, payload, decision, "
                       "outcome, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (time.time(), call_id, agent, pack, platform, action_id,
                        json.dumps(payload, ensure_ascii=False), decision, outcome, detail[:1000]))

    @staticmethod
    def _auto_runs_today(db: sqlite3.Connection, pack: str, platform: str, action: str) -> int:
        """G-APPR-4: Auto runs started since local midnight under this rule. Counted by action
        name, like the rule, so two connectors with the same action share one cap (review M6)."""
        midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        return db.execute("SELECT COUNT(*) FROM activity WHERE decision = 'auto' AND outcome = 'started' "
                          "AND pack = ? AND platform = ? AND substr(action_id, instr(action_id, '.') + 1) = ? "
                          "AND at >= ?", (pack, platform, action, midnight)).fetchone()[0]

    def auto_runs_today(self, pack: str, platform: str, action: str) -> int:
        with closing(self._conn()) as db:
            return self._auto_runs_today(db, pack, platform, action)

    def start_auto(self, call_id: str, agent: str, pack: str, platform: str, action_id: str,
                   payload: dict, cap: int) -> bool:
        """G-APPR-4 / N-REL-3: under the cap, log "started" and return True, in one locked step,
        so two calls at once can never both take the last slot (review M6)."""
        action = action_id.split(".", 1)[-1]
        db = self._conn()
        db.isolation_level = None  # manual transaction
        try:
            db.execute("BEGIN IMMEDIATE")  # write lock first: the count cannot change under us
            if self._auto_runs_today(db, pack, platform, action) >= cap:
                db.execute("ROLLBACK")
                return False
            db.execute("INSERT INTO activity (at, call_id, agent, pack, platform, action_id, payload, decision, "
                       "outcome, detail) VALUES (?, ?, ?, ?, ?, ?, ?, 'auto', 'started', '')",
                       (time.time(), call_id, agent, pack, platform, action_id,
                        json.dumps(payload, ensure_ascii=False)))
            db.execute("COMMIT")
            return True
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def earlier_same(self, action_id: str, payload: dict, since: float) -> dict[str, Any] | None:
        """N-REL-3 (review M6): the latest earlier call of this action with exactly this payload,
        as a log row or an inbox item still waiting or running, so an agent's retry never
        runs or queues it a second time."""
        body = json.dumps(payload, ensure_ascii=False)
        with closing(self._conn()) as db:
            item = db.execute("SELECT id, status FROM approvals WHERE action_id = ? AND payload = ? "
                              "AND status IN ('pending', 'running') ORDER BY id DESC LIMIT 1",
                              (action_id, body)).fetchone()
            if item:
                return {"kind": "item", "id": item["id"], "status": item["status"]}
            # A reject means "not this content": the same content again is held, never auto-run.
            rejected = db.execute("SELECT id FROM approvals WHERE action_id = ? AND payload = ? AND status = "
                                  "'rejected' AND decided_at >= ? ORDER BY id DESC LIMIT 1",
                                  (action_id, body, since)).fetchone()
            if rejected:
                return {"kind": "rejected", "id": rejected["id"]}
            # The agent's original text of an item I edited before approving: never posted as well.
            edited = db.execute("SELECT id FROM approvals WHERE action_id = ? AND proposed = ? AND payload != ? "
                                "AND status IN ('running', 'done', 'failed') AND decided_at >= ? "
                                "ORDER BY id DESC LIMIT 1", (action_id, body, body, since)).fetchone()
            if edited:
                return {"kind": "edited", "id": edited["id"]}
            row = db.execute("SELECT outcome FROM activity WHERE action_id = ? AND payload = ? AND at >= ? "
                             "AND outcome IN ('started', 'done', 'failed', 'unknown') ORDER BY id DESC LIMIT 1",
                             (action_id, body, since)).fetchone()
            return {"kind": "run", "outcome": row["outcome"]} if row else None

    def recent_run(self, action_id: str, payload: dict, since: float) -> str:
        """The outcome of the latest run of exactly this content since `since`, or ''."""
        with closing(self._conn()) as db:
            row = db.execute("SELECT outcome FROM activity WHERE action_id = ? AND payload = ? AND at >= ? "
                             "AND outcome IN ('started', 'done', 'failed', 'unknown') ORDER BY id DESC LIMIT 1",
                             (action_id, json.dumps(payload, ensure_ascii=False), since)).fetchone()
            return row["outcome"] if row else ""

    def unfinished_calls(self) -> list[dict[str, Any]]:
        """N-REL-3: calls that started but have no done, failed or unknown row (a crash mid-run)."""
        with closing(self._conn()) as db:
            rows = db.execute("SELECT * FROM activity a WHERE outcome = 'started' AND NOT EXISTS "
                              "(SELECT 1 FROM activity b WHERE b.call_id = a.call_id AND b.id > a.id "
                              "AND b.outcome IN ('done', 'failed', 'unknown'))")
            return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def activity(self, q: str = "", pack: str = "", platform: str = "", day: str = "",
                 limit: int = 500) -> list[dict[str, Any]]:
        """G-LOG-1 / UI 12: newest first, searchable, filterable by pack, platform and date (YYYY-MM-DD)."""
        sql, args = "SELECT * FROM activity WHERE 1 = 1", []
        if pack:
            sql += " AND pack = ?"; args.append(pack)
        if platform:
            sql += " AND platform = ?"; args.append(platform)
        if day:
            first = datetime.strptime(day, "%Y-%m-%d")  # raises ValueError on a bad date
            # Local midnight to local midnight: 23 or 25 hours on daylight-saving days (review M6).
            sql += " AND at >= ? AND at < ?"; args += [first.timestamp(), (first + timedelta(days=1)).timestamp()]
        if q.strip():
            like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            sql += (" AND (agent LIKE ? ESCAPE '\\' OR action_id LIKE ? ESCAPE '\\' OR payload LIKE ? ESCAPE '\\'"
                    " OR detail LIKE ? ESCAPE '\\')")
            args += [like] * 4
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with closing(self._conn()) as db:
            return [{**dict(r), "payload": json.loads(r["payload"])} for r in db.execute(sql, args)]

    # ---- approvals inbox (G-APPR-2) -----------------------------------------------------
    def hold(self, agent: str, pack: str, platform: str, action_id: str, payload: dict, reason: str) -> int:
        with closing(self._conn()) as db, db:
            body = json.dumps(payload, ensure_ascii=False)
            cur = db.execute("INSERT INTO approvals (created_at, agent, pack, platform, action_id, payload, proposed, "
                             "reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             (time.time(), agent, pack, platform, action_id, body, body, reason))
            return cur.lastrowid

    def approval(self, item_id: int) -> dict[str, Any] | None:
        with closing(self._conn()) as db:
            row = db.execute("SELECT * FROM approvals WHERE id = ?", (item_id,)).fetchone()
            return {**dict(row), "payload": json.loads(row["payload"])} if row else None

    def approvals(self, status: str = "pending") -> list[dict[str, Any]]:
        with closing(self._conn()) as db:
            rows = db.execute("SELECT * FROM approvals WHERE status = ? ORDER BY id", (status,))
            return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def pending_count(self) -> int:
        with closing(self._conn()) as db:
            return db.execute("SELECT COUNT(*) FROM approvals WHERE status = 'pending'").fetchone()[0]

    def claim(self, item_id: int, new_status: str, payload: dict | None = None) -> bool:
        """Move a pending item on, exactly once: a double click never runs it twice (N-REL-3).
        payload: my edited version, stored in the same step, before the action runs, so a crash
        mid-run still knows what I approved (review M6)."""
        with closing(self._conn()) as db, db:
            if payload is None:
                return db.execute("UPDATE approvals SET status = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
                                  (new_status, time.time(), item_id)).rowcount == 1
            return db.execute("UPDATE approvals SET status = ?, decided_at = ?, payload = ? WHERE id = ? "
                              "AND status = 'pending'", (new_status, time.time(),
                                                         json.dumps(payload, ensure_ascii=False), item_id)).rowcount == 1

    def item_outcome(self, item_id: int) -> str:
        """The logged outcome of an approved item's run ('' if none): done, failed or started."""
        with closing(self._conn()) as db:
            row = db.execute("SELECT b.outcome FROM activity a JOIN activity b ON b.call_id = a.call_id "
                             "WHERE a.decision = 'approved' AND a.outcome = 'started' AND (a.detail = ? OR a.detail "
                             "LIKE ?) ORDER BY b.id DESC LIMIT 1",
                             (f"inbox item {item_id}", f"inbox item {item_id},%")).fetchone()
            return row["outcome"] if row else ""

    def finish(self, item_id: int, status: str, result: str) -> None:
        with closing(self._conn()) as db, db:
            db.execute("UPDATE approvals SET status = ?, result = ? WHERE id = ?", (status, result[:1000], item_id))
