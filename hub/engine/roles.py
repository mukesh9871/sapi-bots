"""Roles (Milestone 5): an agent is a role definition (G-AGENT-1), kept as a readable file (G-AGENT-2).

One Markdown file per role in packs/local/roles/<id>.md:

    ---
    name: Researcher
    model: sonnet
    tools: WebSearch, WebFetch, Write
    ---
    You research a topic and write report.md in your folder.

- The file name (without .md) is the role's id. It never changes, so chats stay linked
  when the role is renamed.
- The header is parsed here (no YAML library, no new dependency).
- G-TOOL-3: a role lists only tools from TOOL_CATALOG. A hand-edited file that lists
  anything else (for example Bash) is shown with a problem and never runs.
- Each role works in its own folder projects/role-<id>/ (G-TEAM-6, N-SEC-5).
- Milestone 8 (G-PACK-1): an installed pack adds read-only roles from packs/<pack>/roles/. Their
  id is "<pack>.<role>", and their acting tools use the pack's approval rules.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROLES_DIR = PROJECT_ROOT / "packs" / "local" / "roles"
ROLE_WORK_ROOT = PROJECT_ROOT / "projects"

# G-TOOL-1, G-TOOL-2: the built-in tools a role may list. Connector actions (acting, through
# the approval gate) are added by catalog(). "own-folder" tools are limited to the role's folder
# (see hub/chat/claude_session.py, outside_folder).
TOOL_CATALOG: dict[str, dict[str, str]] = {
    "WebSearch": {"kind": "read-only", "label": "Search the web"},
    "WebFetch": {"kind": "read-only", "label": "Read a web page"},
    "Read": {"kind": "read-only", "label": "Read files in its folder"},
    "Glob": {"kind": "read-only", "label": "Find files in its folder"},
    "Grep": {"kind": "read-only", "label": "Search text in its folder"},
    "Write": {"kind": "own-folder", "label": "Create files in its folder"},
    "Edit": {"kind": "own-folder", "label": "Change files in its folder"},
}
FILE_TOOLS = ("Read", "Glob", "Grep", "Write", "Edit")
MODELS = ("", "haiku", "sonnet", "opus")  # always accepted; the full list comes from Claude Code (R-MODEL-1)

MAX_NAME = 40
DEFAULT_TURNS = 30  # G-BUDGET-1: a task's budget in turns, per message
MAX_TURNS = 200
MAX_INSTRUCTIONS = 20_000
ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")  # a role id is also part of a folder name
FULL_ID_RE = re.compile(r"(?:[a-z0-9]+(?:-[a-z0-9]+)*\.)?[a-z0-9]+(?:-[a-z0-9]+)*")  # "writer" or "pack.writer"
LOCAL = "local"  # the pack name of my own roles, teams and rules


def split_id(full_id: object) -> tuple[str, str] | None:
    """("local", "writer") or ("sample", "writer") for "sample.writer"; None for anything else."""
    if not isinstance(full_id, str) or not FULL_ID_RE.fullmatch(full_id):
        return None
    pack, _, item = full_id.rpartition(".")
    return (pack or LOCAL, item)


def catalog() -> dict[str, dict[str, str]]:
    """Everything a role may list: built-in tools, then connector actions such as
    "demo.post_note" (kind "acting": every call goes through the approval gate)."""
    from hub import connectors  # here, so the role files can be read without loading connectors
    acts = {a.id: {"kind": "acting" if a.acting else "read-only", "label": a.description}
            for a in connectors.actions().values() if not a.ui_only}  # buttons for me are never a role's tool
    # G-CONN-2: tools of the claude.ai connectors I switched on (read-only ones run freely, the rest are gated)
    from hub.chat import connectors_claude
    claude = {tid: {"kind": "read-only" if c["read_only"] else "acting", "label": f"{c['label']}: {c['tool']}"}
              for tid, c in connectors_claude.tools().items()}
    return {**TOOL_CATALOG, **acts, **claude}


class RoleError(ValueError):
    """A role that cannot be saved. The message is shown to the user."""


@dataclass(frozen=True)  # frozen: compared by value, so an edited role restarts its session
class Role:
    id: str
    name: str
    model: str
    tools: tuple[str, ...]
    instructions: str
    problem: str = ""  # set when a hand-edited file is not valid; such a role never runs
    max_turns: int = DEFAULT_TURNS  # G-BUDGET-1
    pack: str = LOCAL  # G-PACK-1: which pack it comes from; its acting tools use that pack's rules
    effort: str = ""  # R-MODEL-2: "" = the Settings effort

    @property
    def folder(self) -> Path:
        return ROLE_WORK_ROOT / f"role-{self.id}"

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "model": self.model, "tools": list(self.tools),
                "instructions": self.instructions, "problem": self.problem, "folder": str(self.folder),
                "max_turns": self.max_turns, "pack": self.pack, "effort": self.effort}


def check(name: str, model: str, tools: list[str] | tuple[str, ...], instructions: str,
          max_turns: int = DEFAULT_TURNS, effort: str = "") -> str | None:
    """Returns what is wrong with a role, or None."""
    # No control characters: a lone carriage return would end the header line on reload (review M5).
    if not isinstance(name, str) or not 0 < len(name.strip()) <= MAX_NAME or any(ord(c) < 32 or ord(c) == 127 for c in name):
        return f"name must be 1 to {MAX_NAME} characters on one line"
    # R-MODEL-1: any name from Claude Code's list (checked against the list where I choose it). Here
    # only its form, so a role file never fails because the list was not read yet. It is one CLI
    # argument: a name, never an option.
    from hub.chat.models import EFFORTS, NAME_RE
    if not isinstance(model, str) or (model and not NAME_RE.fullmatch(model)):
        return "unknown model"
    if not isinstance(effort, str) or (effort and effort not in EFFORTS):
        return "unknown effort level"
    if not isinstance(tools, (list, tuple)) or not all(isinstance(t, str) for t in tools):
        return "bad tool list"
    unknown = [t for t in tools if t not in catalog()]
    if unknown:
        return f"tools not allowed for a role: {', '.join(unknown)}"  # G-TOOL-3
    # Review M15: what a connector reads (my mail, my files) must never leave through a web request
    # made without my approval, so a role has connector tools or web tools, never both.
    if any(t.startswith("mcp__claude_ai_") for t in tools) and {"WebSearch", "WebFetch"} & set(tools):
        return "a role with connector tools cannot also have web search or web fetch"
    if not isinstance(instructions, str) or not instructions.strip():
        return "the job description is empty"
    if len(instructions) > MAX_INSTRUCTIONS:
        return "the job description is too long"
    if not isinstance(max_turns, int) or isinstance(max_turns, bool) or not 1 <= max_turns <= MAX_TURNS:
        return f"max turns must be 1 to {MAX_TURNS}"
    return None


def parse(role_id: str, text: str, pack: str = LOCAL) -> Role:
    """Reads a role file. Never raises: a bad file gives a Role with `problem` set."""
    header: dict[str, str] = {}
    body = text
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[0].strip() == "---" and "---" in (l.strip() for l in lines[1:]):
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        for line in lines[1:end]:
            if ":" in line:
                key, value = line.split(":", 1)
                header[key.strip().lower()] = value.strip()
        body = "\n".join(lines[end + 1:])
    name = header.get("name", "") or role_id
    model = header.get("model", "")
    tools = tuple(t.strip() for t in header.get("tools", "").split(",") if t.strip())
    if {"Write", "Edit"} & set(tools) and "Read" not in tools:
        tools = ("Read", *tools)  # same rule as save(): the CLI changes only files it has read
    instructions = body.strip()
    raw_turns = header.get("max_turns", "") or str(DEFAULT_TURNS)
    # isascii: "²".isdigit() is True but int("²") fails, and parse() must never raise (review M6)
    max_turns = int(raw_turns) if raw_turns.isascii() and raw_turns.isdigit() and len(raw_turns) < 6 else -1
    closed = lines[0].strip() == "---" and "---" in (l.strip() for l in lines[1:])
    problem = "" if closed else "the file has no header (--- lines above and below it)"
    effort = header.get("effort", "")
    problem = problem or check(name, model, tools, instructions, max_turns, effort) or ""
    return Role(role_id, name, model, tools, instructions, problem, max_turns if max_turns > 0 else DEFAULT_TURNS, pack,
                effort)


def dump(role: Role) -> str:
    effort = f"effort: {role.effort}\n" if role.effort else ""  # only when set: older files read the same
    return (f"---\nname: {role.name.strip()}\nmodel: {role.model}\n{effort}"
            f"tools: {', '.join(role.tools)}\nmax_turns: {role.max_turns}\n---\n{role.instructions.strip()}\n")


def make_id(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30].strip("-") or "role"
    rid, n = base, 2
    while rid in taken:
        rid, n = f"{base}-{n}", n + 1
    return rid


class RoleStore:
    def __init__(self, folder: Path | None = None):
        self.folder = folder or ROLES_DIR
        # Ids still used elsewhere (the server adds the role ids of saved chats). A new role never
        # takes one, so a deleted role's chats and folder never pass to a new role (review M5).
        self.in_use: Callable[[], set[str]] = lambda: set()
        # G-PACK-1: installed and switched-on packs, {pack id: pack folder}. Set by the server.
        self.pack_dirs: Callable[[], dict[str, Path]] = lambda: {}

    def _taken(self) -> set[str]:
        files = {p.stem for p in self.folder.glob("*.md")} if self.folder.is_dir() else set()
        root = ROLE_WORK_ROOT
        folders = {d.name[5:] for d in root.glob("role-*")} if root.is_dir() else set()
        # Lowercase: on Windows "Writer.md" and "writer.md" are the same file (review M5 round 2).
        return {x.lower() for x in files | folders | self.in_use()}

    def _path(self, role_id: str) -> Path | None:
        # Never build a path from anything but a plain id (no "..", slashes or drive letters).
        return self.folder / f"{role_id}.md" if isinstance(role_id, str) and ID_RE.fullmatch(role_id) else None

    def list(self) -> list[Role]:
        ids = [p.stem for p in sorted(self.folder.glob("*.md"))] if self.folder.is_dir() else []
        for pack, root in sorted(self.pack_dirs().items()):  # pack roles after mine (G-PACK-1)
            folder = root / "roles"
            ids += [f"{pack}.{p.stem}" for p in sorted(folder.glob("*.md"))] if folder.is_dir() else []
        roles = [self.get(i) for i in ids if FULL_ID_RE.fullmatch(i)]
        return sorted((r for r in roles if r), key=lambda r: (r.pack != LOCAL, r.pack, r.name.lower()))

    def _file(self, role_id: str) -> tuple[Path, str] | None:
        """The file of a role and its pack. A pack role is found only while its pack is on."""
        parts = split_id(role_id)
        if parts is None:
            return None
        pack, item = parts
        if pack == LOCAL:
            return (self.folder / f"{item}.md", pack) if "." not in role_id else None  # "local.x" is not an id
        root = self.pack_dirs().get(pack)
        return (root / "roles" / f"{item}.md", pack) if root else None

    def get(self, role_id: str) -> Role | None:
        found = self._file(role_id)
        if found is None or not found[0].is_file():
            return None
        path, pack = found
        try:
            return parse(role_id, path.read_text(encoding="utf-8-sig"), pack)  # a BOM from Notepad is fine
        except (OSError, UnicodeDecodeError) as exc:
            return Role(role_id, role_id, "", (), "", f"cannot read the file ({type(exc).__name__})", pack=pack)

    def _write(self, role: Role) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(role.id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(dump(role), encoding="utf-8")
        os.replace(tmp, path)  # atomic, so a crash never leaves half a role file

    def save(self, role_id: str | None, name: str, model: str, tools: list[str], instructions: str,
             max_turns: int = DEFAULT_TURNS, effort: str = "") -> Role:
        """Creates a role (role_id None) or replaces an existing one. Raises RoleError."""
        problem = check(name, model, tools, instructions, max_turns, effort)
        if problem:
            raise RoleError(problem)
        if role_id is None:
            role_id = make_id(name, self._taken())
        elif self._path(role_id) is None:  # G-PACK-2: a pack's files are never changed by the app
            raise RoleError("this role comes from a pack and cannot be changed: copy it first")
        elif self.get(role_id) is None:
            raise RoleError("this role no longer exists")
        # The CLI refuses to change a file the role has not read first, so Write and Edit bring Read.
        if {"Write", "Edit"} & set(tools):
            tools = [*tools, "Read"]
        # Keep the catalog order, drop repeats, so the file reads the same every time.
        role = Role(role_id, name.strip(), model, tuple(t for t in catalog() if t in tools), instructions.strip(),
                    max_turns=max_turns, effort=effort)
        self._write(role)
        return role

    def copy(self, role_id: str) -> Role:
        role = self.get(role_id)
        if role is None:
            raise RoleError("this role no longer exists")
        if role.problem:
            raise RoleError(f"fix this role first: {role.problem}")
        return self.save(None, f"{role.name} (copy)"[:MAX_NAME], role.model, list(role.tools), role.instructions,
                         role.max_turns, role.effort)

    def delete(self, role_id: str) -> bool:
        """Removes the role file only. Its folder (the role's work) and its chats are kept."""
        path = self._path(role_id)
        if path is None or not path.is_file():
            return False
        path.unlink()
        return True
