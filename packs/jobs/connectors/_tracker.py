"""Job tracker shared by the jobs pack's connectors (Milestone 11, P-JOB-5, P-JOB-7).

Kept in data/pack-data/jobs/tracker.db (never in the pack folder). One row per job, never two
for the same job: the same address (without ? and #) or the same company and title is the same
job. Statuses:
    shortlisted -> drafted -> checked (or "needs fixing") -> ready for you (Prepare only)
                                                           -> applied (Auto, by email)
    needs you (handed over: a CAPTCHA, a login, a question the profile cannot answer, P-JOB-6)
Files starting with "_" are never loaded as connectors (hub/connectors/__init__.py).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from hub.connectors.base import ActionError
from hub.engine import packs

PACK = "jobs"
MODES = ("auto", "prepare", "skip")
FORCED_PREPARE = ("linkedin.com",)  # P-JOB-4: LinkedIn bans bots: always Prepare only, whatever the list says
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL, url_key TEXT NOT NULL UNIQUE, title_key TEXT NOT NULL UNIQUE,
    company TEXT NOT NULL, title TEXT NOT NULL, location TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '', score INTEGER NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '',
    apply_method TEXT NOT NULL DEFAULT 'site', apply_email TEXT NOT NULL DEFAULT '',
    site_mode TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'shortlisted', reason TEXT NOT NULL DEFAULT '',
    resume TEXT NOT NULL DEFAULT '', cover_letter TEXT NOT NULL DEFAULT '', check_notes TEXT NOT NULL DEFAULT '',
    documents TEXT NOT NULL DEFAULT '', found_at REAL NOT NULL, applied_at REAL, follow_up REAL
);
"""
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")
TRACKING = ("utm_", "ref", "source", "src", "trk", "tracking", "campaign", "fbclid", "gclid", "from")


def fingerprint(job: dict[str, Any]) -> str:
    """A short fingerprint of exactly what would be checked and sent: the documents and the address.
    An approval carries it, so a job whose documents changed after it was held is never sent (review M11)."""
    text = "\n\x00".join((job["resume"], job["cover_letter"], job["apply_email"], job["title"]))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(packs.data_dir(PACK) / "tracker.db", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def url_key(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ActionError("the job address must be a web address")
    # The query stays (Indeed and Greenhouse keep the job id there), without tracking parts (review M11).
    query = sorted((k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(TRACKING))
    key = f"{parts.hostname.lower().removeprefix('www.')}{parts.path.rstrip('/').lower()}"
    return key + (f"?{urlencode(query)}" if query else "")


def title_key(company: str, title: str) -> str:
    return " ".join(f"{company} | {title}".lower().split())


def site_mode(url: str) -> str:
    """P-JOB-5: the mode for this job's site from my list; unknown sites start as Prepare only."""
    host = (urlsplit(url).hostname or "").lower()
    best, mode = "", "prepare"  # the most specific matching line wins (careers.example.com over example.com)
    for line in packs.setting(PACK, "site_modes").splitlines():
        site, _, m = (x.strip().lower() for x in line.partition(":"))
        site = site.removeprefix("www.")
        if site and m in MODES and (host == site or host.endswith("." + site)):
            if len(site) > len(best) or (len(site) == len(best) and mode != "skip"):  # same line twice: Skip wins
                best, mode = site, m
    if mode == "auto" and any(host == d or host.endswith("." + d) for d in FORCED_PREPARE):
        mode = "prepare"  # LinkedIn is never Auto; Skip still wins (review M11)
    return mode


def get(job_id: Any) -> dict[str, Any]:
    try:
        jid = int(str(job_id).strip())
    except ValueError:
        raise ActionError("job must be a job number from the tracker") from None
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
    if row is None:
        raise ActionError(f"there is no job {jid} in the tracker")
    return dict(row)


def update(job_id: int, **fields: Any) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    with closing(db()) as conn, conn:
        conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*fields.values(), job_id))


def day(t: float | None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t)) if t else ""


def folder_for(job: dict[str, Any]) -> Path:
    safe = re.sub(r"[^A-Za-z0-9]+", "-", f"{job['id']}-{job['company']}")[:60].strip("-")
    folder = packs.data_dir(PACK) / "documents" / safe
    folder.mkdir(parents=True, exist_ok=True)
    return folder
