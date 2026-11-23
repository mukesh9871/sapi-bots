"""Shared state of the video pack (Milestone 13): which script I approved (by its exact text) and
which final cut, per project folder. Kept in data/pack-data/video/state.db. Files starting with "_"
are never loaded as connectors.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from hub.connectors.base import ActionError
from hub.engine import packs

PACK = "video"
MAX_SCRIPT = 20_000
VIDEO_TYPES = (".mp4", ".mov", ".webm", ".mkv", ".gif")
SCHEMA = """
CREATE TABLE IF NOT EXISTS scripts (folder TEXT PRIMARY KEY, hash TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS finals (folder TEXT PRIMARY KEY, file TEXT NOT NULL, hash TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS renders (id INTEGER PRIMARY KEY AUTOINCREMENT, folder TEXT NOT NULL, shot TEXT NOT NULL,
    status TEXT NOT NULL, detail TEXT NOT NULL, at REAL NOT NULL);
"""


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(packs.data_dir(PACK) / "state.db", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def folder_of(payload: dict[str, str]) -> Path:
    try:
        return packs.project_folder(payload.get("folder", ""))
    except ValueError as exc:
        raise ActionError(str(exc)) from None


def script(folder: Path) -> str:
    path = folder / "SCRIPT.md"
    if not path.is_file():
        raise ActionError("there is no SCRIPT.md in the project folder yet")
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip() or len(text) > MAX_SCRIPT:
        raise ActionError(f"SCRIPT.md must have text and at most {MAX_SCRIPT} characters")
    return text


def digest(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


_FILE_DIGESTS: dict[tuple, str] = {}


def file_digest(path: Path) -> str:
    """A file's fingerprint, read in pieces and remembered by size and change time (review M13: the
    Approvals page asks for it each time it opens; a long video is never read in one go)."""
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _FILE_DIGESTS:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if len(_FILE_DIGESTS) > 200:
            _FILE_DIGESTS.clear()
        _FILE_DIGESTS[key] = h.hexdigest()
    return _FILE_DIGESTS[key]


def approve_script(folder: Path, text: str) -> None:
    with closing(db()) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO scripts VALUES (?, ?, ?)", (str(folder), digest(text), time.time()))


def script_approved(folder: Path) -> bool:
    """P-VID-1: I approved exactly the SCRIPT.md that is there now."""
    try:
        text = script(folder)
    except ActionError:
        return False
    with closing(db()) as conn:
        row = conn.execute("SELECT hash FROM scripts WHERE folder = ?", (str(folder),)).fetchone()
    return bool(row) and row[0] == digest(text)


def inside(folder: Path, rel: str, what: str) -> Path:
    """A file the agent names, inside the project folder only (no .., no links, no other drive)."""
    rel = rel.strip()
    if not rel or rel.startswith(("/", "\\")) or ":" in rel or ".." in rel.replace("\\", "/").split("/"):
        raise ActionError(f"{what} must be a path inside the project folder, like renders/shot1/clip.mp4")
    path = (folder / rel)
    try:
        resolved = path.resolve()
    except OSError:
        raise ActionError(f"{what} cannot be read") from None
    if folder not in resolved.parents or any(p.is_symlink() for p in [path, *path.parents] if folder in p.parents):
        raise ActionError(f"{what} must stay inside the project folder")
    return resolved


def log_render(folder: Path, shot: str, status: str, detail: str) -> None:
    with closing(db()) as conn, conn:
        conn.execute("INSERT INTO renders (folder, shot, status, detail, at) VALUES (?, ?, ?, ?, ?)",
                     (str(folder), shot, status, detail[:1000], time.time()))


def renders(folder: Path) -> list[dict]:
    with closing(db()) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM renders WHERE folder = ? ORDER BY id", (str(folder),))]


def approve_final(folder: Path, file: Path) -> None:
    with closing(db()) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO finals VALUES (?, ?, ?, ?)",
                     (str(folder), str(file), file_digest(file), time.time()))


def final(folder: Path) -> dict | None:
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM finals WHERE folder = ?", (str(folder),)).fetchone()
    return dict(row) if row else None
