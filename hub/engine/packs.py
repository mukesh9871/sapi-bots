"""Instruction packs (Milestone 8, G-PACK-1, G-PACK-2).

A pack is a folder in packs/<id>/ that bundles one type of work:

    packs/<id>/pack.md          header: name, version, description, views and row_actions
                                ("<action id> | <label>, ...": read-only tables on the Packs page, and my
                                buttons per table row; a row action is never offered to a role)
    packs/<id>/roles/*.md       roles (same format as my own roles)
    packs/<id>/teams/*.md       team templates (a plain role id means this pack's role)
    packs/<id>/connectors/*.py  connectors (modules that list ACTIONS, as in hub/connectors/)
    packs/<id>/rules.md         suggested approval rules, one per line: "<action id>: ask|never|auto [cap N]"
    packs/<id>/schedules/*.md   default schedules (header: name, run, when, notify; the body is the task)
    packs/<id>/settings.md      settings I fill in on the Packs page, one per line:
                                "<key> | folder|file|text|longtext|secret | <label>"
                                (file: a local .md or .txt file, for example a master resume)
                                A secret (a platform login) is kept in Windows Credential Manager through
                                keyring: never in hub.db, a file, a log or an agent's context (hard rule).

Installing records the pack in data/hub.db. While it is on, its roles, teams and connectors
appear (ids "<pack>.<role>"), read-only. Its rules are created as Ask or Never: a suggested
Auto is created as Ask and listed, because only I switch a rule to Auto (G-APPR-3). Its
schedules are added switched off. Switching a pack off hides it and keeps its rules and
schedules. Removing it deletes its rules, schedules and pending approvals; the activity log
keeps every entry. None of this changes the engine or another pack (G-PACK-2).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hub.engine.roles import ID_RE, LOCAL, PROJECT_ROOT
from hub.gate import store as gate_store
from hub.scheduler.schedules import ScheduleError, ScheduleStore, parse_when

PACKS_DIR = PROJECT_ROOT / "packs"
RESERVED = {LOCAL}  # packs/local holds my own roles and teams, it is not a pack
SETTING_RE = re.compile(r"[a-z][a-z0-9_]{0,39}")
SETTING_TYPES = ("folder", "file", "text", "longtext", "secret")
FILE_TYPES = (".md", ".txt")  # a "file" setting: text a connector may read (Milestone 11)
MAX_SETTING, MAX_LONG_SETTING = 400, 4000
MAX_SECRET = 1200  # a LinkedIn token is about 500 characters; Windows Vault holds about 1280 (review M10)
SECRET_NAMES = "__secret_names"  # in hub.db: only the NAMES of secrets that were set, never their values
KEYRING_SERVICE = "sapi-ai-hub"  # Windows Credential Manager: "sapi-ai-hub" / "<pack>/<key>"
active: "PackStore | None" = None  # the app's store, so a pack connector can read its settings (setting())


def _remote_drive(folder: Path) -> bool:
    """True for a mapped network drive (Windows DRIVE_REMOTE). Asks Windows about the letter only."""
    if os.name != "nt" or not folder.drive or not folder.drive.endswith(":"):
        return False
    import ctypes
    return ctypes.windll.kernel32.GetDriveTypeW(folder.drive + "\\") == 4  # DRIVE_REMOTE


def setting(pack_id: str, key: str) -> str:
    """A pack setting I filled in on the Packs page ("" if not set). Pack connectors call this."""
    return (active.settings(pack_id).get(key, "") if active else "") or ""


def project_folder(value: str) -> Path:
    """Milestone 12: a working folder given by the hub (a hub-filled field), checked again: it must be an
    existing folder inside projects/ (a role's or a team's folder), never a link out of it."""
    from hub.engine import roles
    if not value:
        raise ValueError("no working folder")
    folder = Path(value)
    root = roles.ROLE_WORK_ROOT.resolve()
    if not folder.is_absolute() or os.path.islink(folder) or not folder.is_dir():
        raise ValueError("the working folder is not a folder")
    resolved = folder.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError("the working folder must be inside the projects folder")
    return resolved


def data_dir(pack_id: str) -> Path:
    """A pack's own data folder, next to hub.db (data/pack-data/<pack>/): for example a tracker database.
    Never inside the pack's folder, which the app never changes (G-PACK-2)."""
    base = active.db_path.parent if active else PROJECT_ROOT / "data"
    folder = base / "pack-data" / pack_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def secret(pack_id: str, key: str) -> str:
    """A pack's secret setting (a login), read from Windows Credential Manager at the moment a
    connector needs it. "" if not set. Never pass it to an agent or write it anywhere."""
    import keyring
    try:
        return keyring.get_password(KEYRING_SERVICE, f"{pack_id}/{key}") or ""
    except Exception:  # no credential store: treated as not set, the connector says so plainly
        return ""

SCHEMA = """
CREATE TABLE IF NOT EXISTS packs (
    id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    installed_at REAL NOT NULL,
    action_keys TEXT NOT NULL DEFAULT '[]'  -- what its connectors claim (see connectors.keys), for clash checks
);
"""


class PackError(ValueError):
    """A pack action that cannot be done. The message is shown to the user."""


def _header(text: str) -> tuple[dict[str, str], str]:
    lines = text.replace("\r\n", "\n").split("\n")
    if not (lines and lines[0].strip() == "---" and "---" in (l.strip() for l in lines[1:])):
        return {}, text
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    head = {}
    for line in lines[1:end]:
        if ":" in line:
            k, v = line.split(":", 1)
            head[k.strip().lower()] = v.strip()
    return head, "\n".join(lines[end + 1:]).strip()


@dataclass
class Pack:
    id: str
    path: Path
    name: str = ""
    version: str = ""
    description: str = ""
    readme: str = ""
    roles: list[str] = field(default_factory=list)
    teams: list[str] = field(default_factory=list)
    connectors: list[str] = field(default_factory=list)
    rules: list[dict[str, Any]] = field(default_factory=list)  # {"action", "mode", "cap"}
    schedules: list[dict[str, Any]] = field(default_factory=list)  # {"key", "name", "kind", "target", "spec", "task"}
    settings: list[dict[str, str]] = field(default_factory=list)  # {"key", "type", "label"} (Milestone 9)
    views: list[dict[str, str]] = field(default_factory=list)  # {"action", "label"}: tables on the Packs page (M11)
    row_actions: list[dict[str, str]] = field(default_factory=list)  # {"action", "label"}: my buttons per table row
    problem: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "version": self.version, "description": self.description,
                "readme": self.readme, "roles": self.roles, "teams": self.teams, "connectors": self.connectors,
                "rules": self.rules, "schedules": [{k: v for k, v in s.items() if k != "task"} for s in self.schedules],
                "settings": self.settings, "views": self.views, "row_actions": self.row_actions,
                "problem": self.problem}


def read_pack(pack_id: str, path: Path) -> Pack:
    """Reads a pack folder. Never raises: a bad pack gets `problem` set and cannot be installed."""
    pack = Pack(pack_id, path)
    try:
        head, body = _header((path / "pack.md").read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError) as exc:
        pack.problem = f"cannot read pack.md ({type(exc).__name__})"
        return pack
    pack.name, pack.version = head.get("name", "") or pack_id, head.get("version", "")
    pack.description, pack.readme = head.get("description", ""), body[:20_000]
    # Milestone 11: "views: <action id> | <label>, ..." are read-only tables shown on the Packs page.
    for item in (v.strip() for v in head.get("views", "").split(",") if v.strip()):
        action, _, label = (x.strip() for x in item.partition("|"))
        pack.views.append({"action": action, "label": label or action})
    for item in (v.strip() for v in head.get("row_actions", "").split(",") if v.strip()):
        action, _, label = (x.strip() for x in item.partition("|"))
        pack.row_actions.append({"action": action, "label": label or action})
    stems = lambda sub, ext: sorted(p.stem for p in (path / sub).glob(f"*{ext}") if ID_RE.fullmatch(p.stem)) \
        if (path / sub).is_dir() else []
    pack.roles, pack.teams = stems("roles", ".md"), stems("teams", ".md")
    pack.connectors = sorted(p.stem for p in (path / "connectors").glob("*.py") if not p.stem.startswith("_")) \
        if (path / "connectors").is_dir() else []
    problems = []

    def read(f: Path) -> str | None:  # a file that cannot be read is a problem, never a crash (review M8)
        try:
            return f.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{f.relative_to(path).as_posix()}: cannot read it ({type(exc).__name__})")
            return None

    for sub in ("roles", "teams"):
        for f in sorted((path / sub).glob("*.md")) if (path / sub).is_dir() else []:
            read(f)
    rules_file = path / "rules.md"
    rules_text = read(rules_file) if rules_file.is_file() else None
    if rules_text is not None:
        for n, line in enumerate(rules_text.splitlines(), 1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            action, _, rest = line.partition(":")
            words = rest.split()
            mode = words[0].lower() if words else ""
            ok_cap = len(words) == 3 and words[1].lower() == "cap" and words[2].isascii() and words[2].isdigit()
            cap = int(words[2]) if ok_cap else 5  # isascii: "²".isdigit() is True but int("²") fails
            if "." not in action or mode not in ("ask", "never", "auto") or len(words) not in (1, 3):
                problems.append(f"rules.md line {n} is not '<action id>: ask|never|auto [cap N]'")
                continue
            pack.rules.append({"action": action.strip(), "mode": mode, "cap": max(1, min(cap, 1000))})
    if (path / "schedules").is_dir():
        for f in sorted((path / "schedules").glob("*.md")):
            if not ID_RE.fullmatch(f.stem):
                continue
            text = read(f)
            if text is None:
                continue
            head, task = _header(text)
            kind, _, target = head.get("run", "").partition(" ")
            try:
                spec = parse_when(head.get("when", ""))
            except ScheduleError as exc:
                problems.append(f"schedules/{f.name}: {exc}")
                continue
            if kind not in ("role", "team") or not target.strip() or not task:
                problems.append(f"schedules/{f.name}: needs 'run: role <id>' or 'run: team <id>' and a task")
                continue
            target = target.strip()
            target = target if "." in target else f"{pack_id}.{target}"  # a plain id means this pack's
            name = head.get("name", "") or f.stem
            try:  # the same checks as a schedule made in the window, so install cannot fail halfway
                ScheduleStore._check(name, kind, target, task)
            except ScheduleError as exc:
                problems.append(f"schedules/{f.name}: {exc}")
                continue
            pack.schedules.append({"key": f.stem, "name": name, "kind": kind, "target": target, "spec": spec,
                                   "task": task, "notify": head.get("notify", "").lower() in ("yes", "true")})
    settings_file = path / "settings.md"
    settings_text = read(settings_file) if settings_file.is_file() else None
    for n, line in enumerate((settings_text or "").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [x.strip() for x in line.split("|")]
        if len(parts) != 3 or not SETTING_RE.fullmatch(parts[0]) or parts[1] not in SETTING_TYPES or not parts[2]:
            problems.append(f"settings.md line {n} is not '<key> | folder|text | <label>'")
            continue
        pack.settings.append({"key": parts[0], "type": parts[1], "label": parts[2][:200]})
    keys = {x["key"] for x in pack.schedules}
    follows = {x["key"]: str(x["spec"]["schedule"]) for x in pack.schedules if x["spec"]["type"] == "after"}
    for x in pack.schedules:  # "after <key>" must name another schedule of this pack, with no loop
        if x["key"] not in follows:
            continue
        if follows[x["key"]] not in keys - {x["key"]}:
            problems.append(f"schedules/{x['key']}.md: 'after' must name another schedule file of this pack")
            continue
        cur, seen = follows[x["key"]], {x["key"]}
        while cur in follows and cur not in seen:
            seen.add(cur)
            cur = follows[cur]
        if cur == x["key"]:
            problems.append(f"schedules/{x['key']}.md: its 'after' chain loops back to itself")
    pack.problem = "; ".join(problems)
    return pack


class PackStore:
    def __init__(self, root: Path | None = None, db_path: Path | None = None):
        self.root = root or PACKS_DIR
        self.db_path = db_path or gate_store.DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._conn()) as db, db:
            db.executescript(SCHEMA)
            try:  # Milestone 9: settings per pack (added to a table Milestone 8 made)
                db.execute("ALTER TABLE packs ADD COLUMN settings TEXT NOT NULL DEFAULT '{}'")
            except sqlite3.OperationalError:
                pass  # already there

    def _conn(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def _folder(self, pack_id: str) -> Path | None:
        if not isinstance(pack_id, str) or not ID_RE.fullmatch(pack_id) or pack_id in RESERVED:
            return None
        path = self.root / pack_id
        return path if (path / "pack.md").is_file() else None

    def available(self) -> list[Pack]:
        """Every pack folder (installed or not)."""
        if not self.root.is_dir():
            return []
        return [read_pack(p.name, p) for p in sorted(self.root.iterdir())
                if p.is_dir() and ID_RE.fullmatch(p.name) and p.name not in RESERVED and (p / "pack.md").is_file()]

    def get(self, pack_id: str) -> Pack | None:
        folder = self._folder(pack_id)
        return read_pack(pack_id, folder) if folder else None

    def states(self) -> dict[str, dict[str, Any]]:
        with closing(self._conn()) as db:
            return {r["id"]: {"enabled": bool(r["enabled"]), "installed_at": r["installed_at"]}
                    for r in db.execute("SELECT * FROM packs ORDER BY installed_at, id")}  # install order

    def enabled_dirs(self) -> dict[str, Path]:
        """{pack id: folder} of packs that are installed, switched on and still on disk."""
        out = {}
        for pid, st in self.states().items():
            folder = self._folder(pid)
            if st["enabled"] and folder:
                out[pid] = folder
        return out

    def mark_installed(self, pack_id: str) -> None:
        with closing(self._conn()) as db, db:
            db.execute("INSERT INTO packs (id, enabled, installed_at) VALUES (?, 1, ?)", (pack_id, time.time()))

    def set_enabled(self, pack_id: str, enabled: bool) -> bool:
        with closing(self._conn()) as db, db:
            return bool(db.execute("UPDATE packs SET enabled = ? WHERE id = ?", (int(enabled), pack_id)).rowcount)

    def settings(self, pack_id: str) -> dict[str, str]:
        with closing(self._conn()) as db:
            row = db.execute("SELECT settings FROM packs WHERE id = ?", (pack_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def view(self, pack_id: str) -> dict[str, str]:
        """Settings as the Packs page shows them: a secret only as "(set)" or "", never its value."""
        out = {k: v for k, v in self.settings(pack_id).items() if k != SECRET_NAMES}
        pack = self.get(pack_id)
        for spec in (pack.settings if pack else []):
            if spec["type"] == "secret":
                out[spec["key"]] = "(set)" if secret(pack_id, spec["key"]) else ""
        return out

    def forget_secrets(self, pack_id: str) -> None:
        """Removing a pack removes its logins from Windows Credential Manager too."""
        import keyring
        pack = self.get(pack_id)
        names = {s["key"] for s in (pack.settings if pack else []) if s["type"] == "secret"}
        names |= set(self.settings(pack_id).get(SECRET_NAMES, []))  # also with its folder gone (review M10)
        for name in names:
            try:
                keyring.delete_password(KEYRING_SERVICE, f"{pack_id}/{name}")
            except Exception:
                pass  # not set

    def set_settings(self, pack_id: str, values: Any) -> dict[str, str]:
        """Only I fill in settings (Packs page). Raises PackError."""
        pack = self.get(pack_id)
        if pack is None or pack_id not in self.states():
            raise PackError("this pack is not installed")
        if not isinstance(values, dict):
            raise PackError("bad settings")
        known = {s["key"]: s for s in pack.settings}
        out = self.settings(pack_id)
        secrets_to_write: dict[str, str] = {}  # written only after every value passed (review M10)
        for key, value in values.items():
            spec = known.get(key)
            if spec is None:
                raise PackError(f"unknown setting {key}")
            limit = {"longtext": MAX_LONG_SETTING, "secret": MAX_SECRET}.get(spec["type"], MAX_SETTING)
            line_ok = (lambda c: c in "\n\r\t" or ord(c) >= 32) if spec["type"] == "longtext" else (lambda c: ord(c) >= 32)
            if not isinstance(value, str) or len(value) > limit or not all(line_ok(c) for c in value):
                raise PackError(f"{spec['label']}: bad value")
            value = value.strip()
            if spec["type"] == "secret":  # goes to Credential Manager below; "" forgets it
                secrets_to_write[key] = value
                out.pop(key, None)  # never stored in hub.db
                continue
            if spec["type"] == "file" and value:
                file = Path(value)
                # The same local-only rules as a folder (no shares, mapped drives or links), then a text file.
                if not file.is_absolute() or str(file).startswith("\\\\") or _remote_drive(file) \
                        or any(os.path.islink(p) for p in (file, *file.parents)) or not file.is_file() \
                        or file.suffix.lower() not in FILE_TYPES:
                    raise PackError(f"{spec['label']}: give the full path of a .md or .txt file on this PC")
                value = str(file.resolve())
            if spec["type"] == "folder" and value:
                folder = Path(value)
                # Checked before is_dir(): never touch a network share, also not written as //host/share
                # or reached through a mapped drive letter or a symbolic link (review M9). islink() never follows.
                if not folder.is_absolute() or str(folder).startswith("\\\\") or _remote_drive(folder) \
                        or any(os.path.islink(p) for p in (folder, *folder.parents)) or not folder.is_dir():
                    raise PackError(f"{spec['label']}: give the full path of a folder that exists on this PC")
                resolved = folder.resolve()
                if str(resolved).startswith("\\\\") or _remote_drive(resolved):
                    raise PackError(f"{spec['label']}: give the full path of a folder that exists on this PC")
                value = str(resolved)
            out[key] = value
        if secrets_to_write:
            import keyring
            names = set(out.get(SECRET_NAMES, []))
            for key, value in secrets_to_write.items():
                try:
                    if value:
                        keyring.set_password(KEYRING_SERVICE, f"{pack_id}/{key}", value)
                        names.add(key)
                    else:
                        try:
                            keyring.delete_password(KEYRING_SERVICE, f"{pack_id}/{key}")
                        except Exception:
                            pass  # was not set
                        names.discard(key)
                except Exception as exc:
                    raise PackError(f"{known[key]['label']}: Windows Credential Manager refused it "
                                    f"({type(exc).__name__})") from None
            out[SECRET_NAMES] = sorted(names)
        with closing(self._conn()) as db, db:
            db.execute("UPDATE packs SET settings = ? WHERE id = ?", (json.dumps(out), pack_id))
        return self.view(pack_id)

    def set_keys(self, pack_id: str, action_keys: list[str]) -> None:
        with closing(self._conn()) as db, db:
            db.execute("UPDATE packs SET action_keys = ? WHERE id = ?", (json.dumps(sorted(action_keys)), pack_id))

    def clash(self, pack_id: str, action_keys: list[str] | None = None) -> list[str]:
        """Keys this pack shares with the hub's own connectors or with any other installed pack,
        switched on or off (review M8: a pack switched off and on again must not take over)."""
        from hub import connectors
        with closing(self._conn()) as db:
            rows = {r["id"]: json.loads(r["action_keys"]) for r in db.execute("SELECT id, action_keys FROM packs")}
        mine = set(action_keys if action_keys is not None else rows.get(pack_id, []))
        taken = connectors.builtin_keys() | {k for pid, ks in rows.items() if pid != pack_id for k in ks}
        return sorted(mine & taken)

    def mark_removed(self, pack_id: str) -> bool:
        with closing(self._conn()) as db, db:
            return bool(db.execute("DELETE FROM packs WHERE id = ?", (pack_id,)).rowcount)


# ---- install and remove (the server calls these; tests call them directly) -----------------------

def install(packs: PackStore, gate: Any, schedules: Any, pack_id: str) -> dict[str, Any]:
    """Installs a pack. Returns what was added and which suggested Auto rules were set to Ask."""
    pack = packs.get(pack_id)
    if pack is None:
        raise PackError("no such pack")
    if pack.problem:
        raise PackError(f"this pack needs fixing: {pack.problem}")
    if pack_id in packs.states():
        raise PackError("this pack is already installed")
    packs.mark_installed(pack_id)
    try:
        return _install_parts(packs, gate, schedules, pack)
    except Exception as exc:  # all or nothing: never leave a half-installed pack (review M8)
        remove(packs, gate, schedules, pack_id)
        raise exc if isinstance(exc, PackError) else PackError(f"install failed: {type(exc).__name__}: {exc}")


def _install_parts(packs: PackStore, gate: Any, schedules: Any, pack: Pack) -> dict[str, Any]:
    pack_id = pack.id
    from hub import connectors  # the pack's connectors load now that it is on
    try:
        own = connectors.pack_actions(pack_id, pack.path)
    except Exception as exc:
        raise PackError(f"a connector of this pack cannot load: {type(exc).__name__}: {exc}")
    own_keys = [k for a in own for k in connectors.keys(a)]
    if len(own_keys) != len(set(own_keys)):
        raise PackError("a connector of this pack cannot load: two of its actions clash with each other")
    clash = packs.clash(pack_id, own_keys)
    if clash:
        raise PackError(f"a connector of this pack cannot load: it clashes with an action that already exists "
                        f"({', '.join(clash)})")
    packs.set_keys(pack_id, own_keys)
    acts = connectors.actions()
    if pack_id in connectors.problems:
        raise PackError(f"a connector of this pack cannot load: {connectors.problems[pack_id]}")
    suggested_auto = []
    for rule in pack.rules:
        action = acts.get(rule["action"])
        if action is None or not action.acting:
            continue  # a rule for a missing or read-only action does nothing
        mode = rule["mode"]
        if mode == "auto":  # G-APPR-3: only I switch a rule to Auto, in Settings
            suggested_auto.append(rule["action"])
            mode = "ask"
        gate.store.set_rule(pack_id, action.platform, action.name, mode, rule["cap"])
    from hub.engine.roles import parse as parse_role
    for rid in pack.roles:  # every acting tool a pack role lists has a rule, Ask by default (G-APPR-3)
        role = parse_role(f"{pack_id}.{rid}", (pack.path / "roles" / f"{rid}.md").read_text(encoding="utf-8-sig"),
                          pack_id)
        for tool_id in role.tools:
            action = acts.get(tool_id)
            if action is not None and action.acting:
                gate.store.rule(pack_id, action.platform, action.name)
    ids: dict[str, int] = {}
    for s in pack.schedules:  # added switched off: I turn on what I want
        spec = {"type": "interval", "minutes": 60} if s["spec"]["type"] == "after" else s["spec"]  # set below
        ids[s["key"]] = schedules.add(s["name"], s["kind"], s["target"], s["task"], spec, enabled=False,
                                      pack=pack_id, pack_key=s["key"], notify=s["notify"])["id"]
    for s in pack.schedules:  # second pass: "after <key>" becomes the id of that schedule
        if s["spec"]["type"] == "after":
            schedules.update(ids[s["key"]], s["name"], s["kind"], s["target"], s["task"],
                             {"type": "after", "schedule": ids[str(s["spec"]["schedule"])]}, notify=s["notify"])
    return {"pack": pack_id, "roles": len(pack.roles), "teams": len(pack.teams),
            "connectors": len(pack.connectors), "schedules": len(pack.schedules), "suggested_auto": suggested_auto}


def remove(packs: PackStore, gate: Any, schedules: Any, pack_id: str) -> dict[str, Any]:
    """Removes a pack's records. Its folder stays, so it can be installed again."""
    if pack_id not in packs.states():
        raise PackError("this pack is not installed")
    rejected = 0
    for item in gate.store.approvals("pending"):
        if item["pack"] == pack_id:
            try:
                gate.reject(item["id"])  # logged as rejected in the activity log
                rejected += 1
            except (KeyError, ValueError):
                pass  # decided meanwhile
    packs.forget_secrets(pack_id)  # its logins leave Windows Credential Manager too
    removed_rules = gate.store.delete_rules(pack_id)
    removed_schedules = schedules.delete_pack(pack_id)
    packs.mark_removed(pack_id)
    return {"pack": pack_id, "rules": removed_rules, "schedules": removed_schedules, "rejected": rejected}
