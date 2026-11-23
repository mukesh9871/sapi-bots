"""Team templates (Milestone 7): a set of roles plus how they work together (G-TEAM-1, G-TEAM-2).

One Markdown file per team in packs/local/teams/<id>.md:

    ---
    name: Writing team
    lead: manager
    reviewer: reviewer
    members: writer, writer
    max_agents: 2
    max_turns: 100
    ---
    Optional notes for the lead: how this team should work.

- lead: breaks the task into steps, assigns them and reports to me (G-TEAM-3).
- reviewer: optional. If set, the lead can only finish after it has checked the latest work.
- members: the workers. A role may appear more than once (developer 1 and 2).
- max_agents and max_turns are the team's budget (G-BUDGET-1).
Roles are referenced by id; a team whose role was deleted or is broken cannot start.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from hub.engine.roles import ID_RE, MAX_NAME, Role, make_id

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEAMS_DIR = PROJECT_ROOT / "packs" / "local" / "teams"

DEFAULT_AGENTS, MAX_AGENTS = 2, 5
DEFAULT_TEAM_TURNS, MIN_TEAM_TURNS, MAX_TEAM_TURNS = 100, 10, 1000
MAX_MEMBERS = 8
MAX_NOTES = 10_000


class TeamError(ValueError):
    """A team that cannot be saved. The message is shown to the user."""


@dataclass(frozen=True)
class Team:
    id: str
    name: str
    lead: str
    reviewer: str  # "" = no reviewer
    members: tuple[str, ...]
    max_agents: int = DEFAULT_AGENTS
    max_turns: int = DEFAULT_TEAM_TURNS
    notes: str = ""
    problem: str = ""  # a hand edit made the file invalid; such a team never starts

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "lead": self.lead, "reviewer": self.reviewer,
                "members": list(self.members), "max_agents": self.max_agents, "max_turns": self.max_turns,
                "notes": self.notes, "problem": self.problem}


def _int(value: object, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def check(name: str, lead: str, reviewer: str, members: list[str] | tuple[str, ...], max_agents: int,
          max_turns: int, notes: str, role_ok: Callable[[str], str | None]) -> str | None:
    """What is wrong with a team, or None. role_ok(id) returns a problem with that role, or None."""
    if not isinstance(name, str) or not 0 < len(name.strip()) <= MAX_NAME or any(ord(c) < 32 for c in name):
        return f"name must be 1 to {MAX_NAME} characters on one line"
    if not isinstance(lead, str) or not lead:
        return "pick a lead"
    if not isinstance(reviewer, str):
        return "bad reviewer"
    if not isinstance(members, (list, tuple)) or not all(isinstance(m, str) and m for m in members):
        return "bad member list"
    if not members:
        return "add at least one member"
    if len(members) > MAX_MEMBERS:
        return f"at most {MAX_MEMBERS} members"
    if lead in members or (reviewer and (reviewer == lead or reviewer in members)):
        return "the lead and the reviewer cannot also be members"  # keeps each job clear
    for rid in [lead, *([reviewer] if reviewer else []), *members]:
        problem = role_ok(rid)
        if problem:
            return f"role {rid}: {problem}"
    if not _int(max_agents, 1, MAX_AGENTS):
        return f"max agents at once must be 1 to {MAX_AGENTS}"
    if not _int(max_turns, MIN_TEAM_TURNS, MAX_TEAM_TURNS):
        return f"max turns must be {MIN_TEAM_TURNS} to {MAX_TEAM_TURNS}"
    if not isinstance(notes, str) or len(notes) > MAX_NOTES:
        return "the notes are too long"
    return None


def parse(team_id: str, text: str, role_ok: Callable[[str], str | None]) -> Team:
    """Reads a team file. Never raises: a bad file gives a Team with `problem` set."""
    header: dict[str, str] = {}
    lines = text.replace("\r\n", "\n").split("\n")
    closed = bool(lines) and lines[0].strip() == "---" and "---" in (l.strip() for l in lines[1:])
    body = text
    if closed:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        for line in lines[1:end]:
            if ":" in line:
                key, value = line.split(":", 1)
                header[key.strip().lower()] = value.strip()
        body = "\n".join(lines[end + 1:])

    def number(key: str, default: int) -> int:
        raw = header.get(key, "") or str(default)
        return int(raw) if raw.isascii() and raw.isdigit() and len(raw) < 6 else -1

    name = header.get("name", "") or team_id
    lead, reviewer = header.get("lead", ""), header.get("reviewer", "")
    members = tuple(m.strip() for m in header.get("members", "").split(",") if m.strip())
    agents, turns = number("max_agents", DEFAULT_AGENTS), number("max_turns", DEFAULT_TEAM_TURNS)
    notes = body.strip()
    problem = "" if closed else "the file has no header (--- lines above and below it)"
    problem = problem or check(name, lead, reviewer, members, agents, turns, notes, role_ok) or ""
    return Team(team_id, name, lead, reviewer, members, max(agents, 1), max(turns, MIN_TEAM_TURNS), notes, problem)


def dump(team: Team) -> str:
    return (f"---\nname: {team.name.strip()}\nlead: {team.lead}\nreviewer: {team.reviewer}\n"
            f"members: {', '.join(team.members)}\nmax_agents: {team.max_agents}\nmax_turns: {team.max_turns}\n"
            f"---\n{team.notes.strip()}\n")


class TeamStore:
    def __init__(self, folder: Path | None = None, roles_get: Callable[[str], Role | None] = lambda _id: None):
        self.folder = folder or TEAMS_DIR
        self.roles_get = roles_get  # the server passes RoleStore.get

    def role_ok(self, role_id: str) -> str | None:
        if not isinstance(role_id, str) or not ID_RE.fullmatch(role_id):
            return "not a role id"
        role = self.roles_get(role_id)
        if role is None:
            return "no such role"
        return f"needs fixing: {role.problem}" if role.problem else None

    def _path(self, team_id: str) -> Path | None:
        return self.folder / f"{team_id}.md" if isinstance(team_id, str) and ID_RE.fullmatch(team_id) else None

    def list(self) -> list[Team]:
        if not self.folder.is_dir():
            return []
        teams = [self.get(p.stem) for p in sorted(self.folder.glob("*.md")) if ID_RE.fullmatch(p.stem)]
        return sorted((t for t in teams if t), key=lambda t: t.name.lower())

    def get(self, team_id: str) -> Team | None:
        path = self._path(team_id)
        if path is None or not path.is_file():
            return None
        try:
            return parse(team_id, path.read_text(encoding="utf-8-sig"), self.role_ok)
        except (OSError, UnicodeDecodeError) as exc:
            return Team(team_id, team_id, "", "", (), problem=f"cannot read the file ({type(exc).__name__})")

    def save(self, team_id: str | None, name: str, lead: str, reviewer: str, members: list[str],
             max_agents: int = DEFAULT_AGENTS, max_turns: int = DEFAULT_TEAM_TURNS, notes: str = "") -> Team:
        """Creates a team (team_id None) or replaces one (G-TEAM-2: saved as a template)."""
        problem = check(name, lead, reviewer, members, max_agents, max_turns, notes, self.role_ok)
        if problem:
            raise TeamError(problem)
        if team_id is None:
            taken = {p.stem.lower() for p in self.folder.glob("*.md")} if self.folder.is_dir() else set()
            team_id = make_id(name, taken)
        elif self.get(team_id) is None:
            raise TeamError("this team no longer exists")
        team = Team(team_id, name.strip(), lead, reviewer, tuple(members), max_agents, max_turns, notes.strip())
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(team_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(dump(team), encoding="utf-8")
        os.replace(tmp, path)
        return team

    def delete(self, team_id: str) -> bool:
        path = self._path(team_id)
        if path is None or not path.is_file():
            return False
        path.unlink()
        return True


def member_keys(team: Team) -> list[tuple[str, str]]:
    """(member key, role id) for everyone, lead first. A repeated role gets "#2", "#3"."""
    out = [("lead", team.lead)]
    if team.reviewer:
        out.append(("reviewer", team.reviewer))
    used = {k for k, _ in out}
    for rid in team.members:
        base = rid if rid not in ("lead", "reviewer") else f"member-{rid}"  # never clash with the lead's key
        key, n = base, 1
        while key in used:  # also covers a real role id "member-lead" next to a worker "lead"
            n += 1
            key = f"{base}#{n}"  # "#" is never in a role id
        used.add(key)
        out.append((key, rid))
    return out

