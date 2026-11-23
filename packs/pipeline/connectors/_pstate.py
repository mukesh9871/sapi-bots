"""Shared state of the pipeline pack (Milestone 14, P-PIPE-1..7): the channels I set up, the queue,
and starting, watching and stopping runs. Files starting with "_" are never loaded as connectors.

- Channels (P-PIPE-1) come only from my pack setting "channels" (hub.db, edited on the Packs page).
  An agent can never write a folder or a command.
- A run (P-PIPE-3) starts with no shell: the program is resolved to a full path, the arguments are a
  list, and the only thing that changes per item is {input} (safe characters only, never starting
  with "-") or {input_file} (the item's text in a file).
- A run is started through _wrap.py, detached, so it survives a restart of the hub (P-PIPE-6). The
  wrapper writes the exit code to result.json; the hub reads it when it next looks.
"""

from __future__ import annotations

import ctypes
import glob
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from hub.connectors.base import ActionError
from hub.engine import packs

PACK = "pipeline"
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
# {input} goes into a command line: letters, digits and a few signs, never a leading "-" (an option),
# and none of the characters cmd.exe treats specially or splits on (= , ; ^ & | % ! " < >), because a
# .bat or .cmd program goes through cmd.exe (review M14). No ":" (a drive or a file stream) and no ".."
# folder step, so an item cannot point the run outside the repo.
SAFE_INPUT = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.@+/\\-]{0,199}")
PROGRAM_TYPES = (".exe", ".com", ".bat", ".cmd")  # what Windows can start directly
MAX_INPUT = 500
PLACEHOLDERS = ("{input}", "{input_file}", "{item}")
KEYS = ("folder", "command", "retry_command", "output", "gpu", "parallel", "timeout", "quiet", "retries")
DEFAULT_QUIET = 2 * 3600
MAX_DURATION = 7 * 24 * 3600
ACTIVE = ("approval", "launching", "running")  # these hold a slot
LOCK = threading.RLock()  # one change to the queue at a time (the service, my buttons, the agents)

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL, input TEXT NOT NULL, position REAL NOT NULL,
    state TEXT NOT NULL DEFAULT 'waiting',   -- waiting approval launching running done failed skipped stopped
    tries INTEGER NOT NULL DEFAULT 0,        -- attempts ever started
    asks INTEGER NOT NULL DEFAULT 0,         -- times a start was asked for: each ask is its own approval
    fails INTEGER NOT NULL DEFAULT 0,        -- failures since it was added or retried by hand
    approval INTEGER, fingerprint TEXT NOT NULL DEFAULT '',
    run_dir TEXT NOT NULL DEFAULT '', pid INTEGER, created INTEGER,
    started_at REAL, ended_at REAL, code INTEGER, output TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '', warned INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL, added_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS paused (channel TEXT PRIMARY KEY);
"""


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(packs.data_dir(PACK) / "queue.db", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ---- channels (P-PIPE-1) -------------------------------------------------------------------------

@dataclass
class Channel:
    name: str
    folder: str = ""
    command: str = ""
    retry_command: str = ""
    output: str = ""
    gpu: bool = True  # P-PIPE-4: unknown is treated as GPU work (safe default)
    parallel: int = 1
    timeout: int | None = None  # seconds; None = no limit (a render may take 12 hours or more)
    quiet: int = DEFAULT_QUIET
    retries: int = 1
    problems: list[str] = field(default_factory=list)

    def fingerprint(self) -> str:
        data = [self.folder, self.command, self.retry_command, self.output, self.gpu, self.timeout]
        return hashlib.sha256(json.dumps(data).encode()).hexdigest()

    def uses(self, placeholder: str) -> bool:
        return placeholder in self.command or placeholder in self.retry_command


def duration(text: str) -> int | None:
    """ "14h", "90m", "2h30m", "1d" -> seconds; "", "none", "0" -> None (no limit). Raises ValueError."""
    t = text.strip().lower().replace(" ", "")
    if t in ("", "none", "no", "0", "off"):
        return None
    m = re.fullmatch(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?", t)
    if not m or not any(m.groups()):
        raise ValueError(f"'{text}' is not a time like 14h, 90m or 2h30m")
    secs = int(m[1] or 0) * 86400 + int(m[2] or 0) * 3600 + int(m[3] or 0) * 60
    if not 60 <= secs <= MAX_DURATION:
        raise ValueError("a time must be 1 minute to 7 days")
    return secs


def parse_channels(text: str) -> list[Channel]:
    """My setting: one block per channel. A channel with problems is listed but never started.

        [reelwright]
        folder: D:\\repos\\video-gen-ai-agent
        command: .venv\\Scripts\\python.exe main.py --input {input}
        output: output\\**\\*.mp4
        gpu: yes
    """
    out: list[Channel] = []
    cur: Channel | None = None
    for n, raw in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip().lower()
            cur = Channel(name)
            if not NAME_RE.fullmatch(name):
                cur.problems.append("the name must be lowercase letters, digits and dashes")
            elif any(c.name == name for c in out):
                cur.problems.append("this name is used twice")
            out.append(cur)
            continue
        key, sep, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if cur is None:  # text before the first [name]: shown as a problem, never ignored quietly
            cur = Channel("?", problems=[f"line {n}: start each channel with its [name]"])
            out.append(cur)
            continue
        if not sep or key not in KEYS:
            cur.problems.append(f"line {n} is not 'key: value' with one of {', '.join(KEYS)}")
            continue
        try:
            if key in ("folder", "command", "retry_command", "output"):
                setattr(cur, key, value)
            elif key == "gpu":
                if value.lower() not in ("yes", "no"):
                    raise ValueError("gpu must be yes or no")
                cur.gpu = value.lower() == "yes"
            elif key in ("parallel", "retries"):
                if not (value.isascii() and value.isdigit()):
                    raise ValueError(f"{key} must be a number")
                cur.parallel = max(1, min(int(value), 8)) if key == "parallel" else cur.parallel
                cur.retries = min(int(value), 3) if key == "retries" else cur.retries
            elif key == "timeout":
                cur.timeout = duration(value)
            elif key == "quiet":
                cur.quiet = duration(value) or 0
        except ValueError as exc:
            cur.problems.append(f"line {n}: {exc}")
    for c in out:
        if c.name != "?":
            c.problems += _check(c)
    # Review M14: channels whose outputs land in the same folder (or one inside the other) share its
    # files, so each needs its own output name. Compared as real folders, however they are written.
    bases = {id(c): _output_base(c) for c in out if not c.problems and c.name != "?"}
    for c in out:
        if id(c) not in bases or own_name(c.output, need_item=True):
            continue
        mine = bases[id(c)]
        others = [o.name for o in out if o is not c and id(o) in bases
                  and (bases[id(o)] == mine or mine in bases[id(o)].parents or bases[id(o)] in mine.parents)]
        if others:  # the same item text can be queued in both, so only the queue number is unique
            c.problems.append(f"{', '.join(others)} writes to the same folder: output must use {{item}} (the queue "
                              "number), so each run has its own file")
    return out


def _local_folder(value: str) -> Path:
    """The same rules as a folder setting: an existing folder on this PC, no share, no link."""
    folder = Path(value)
    if not value or not folder.is_absolute() or value.startswith(("\\\\", "//")) or packs._remote_drive(folder) \
            or any(os.path.islink(p) for p in (folder, *folder.parents)) or not folder.is_dir():
        raise ValueError("folder must be the full path of a folder that exists on this PC")
    return folder.resolve()


def _check(c: Channel) -> list[str]:
    problems = []
    try:
        _local_folder(c.folder)
    except ValueError as exc:
        problems.append(str(exc))
    for key in ("command", "retry_command"):
        value = getattr(c, key)
        if not value and key == "retry_command":
            continue
        try:
            tokens = split(value)
            if not tokens:
                raise ValueError("it is empty")
            if "{" in tokens[0]:
                raise ValueError("the program itself cannot be a placeholder")
            for t in tokens:
                for ph in re.findall(r"\{[^}]*\}", t):
                    if ph not in PLACEHOLDERS:
                        raise ValueError(f"unknown placeholder {ph} (use {', '.join(PLACEHOLDERS)})")
            if not problems:  # review M14: a missing program (a venv being rebuilt) is "needs fixing",
                _which(tokens[0], _local_folder(c.folder))  # never a run of failed starts
        except (ValueError, ActionError) as exc:
            problems.append(f"{key}: {exc}")
    if not c.output:
        problems.append("output is missing: the file a finished run leaves (it may use * and {input})")
    elif c.parallel > 1 and not own_name(c.output):  # with {input}: no wildcard after it
        # Review M14: two runs at once must never both claim the same new file.
        problems.append("with parallel above 1, output must use {input} or {item}, so each run has its own file")
    elif "{input}" in c.output and c.uses("{input_file}") and not c.uses("{input}"):
        problems.append("output can use {input} only when the command does")
    return problems


def own_name(output: str, need_item: bool = False) -> bool:
    """Each run's output has a name no other item's file can match (review M14):
    - {input} only in a pattern with no wildcard at all: an item may hold "_" or "/", so
      out\\*_{input}.mp4 or out\\{input}_*.mp4 would let "intro" match the file of "long_intro";
    - {item} (the queue number, unique across channels) with no wildcard before it and no digit or
      wildcard straight after it, so item 1 never matches the file of item 12 (out\\{item}_*.mp4 is fine).
    need_item: only {item} counts (channels sharing a folder can have the same item text)."""
    wild = lambda text: any(ch in text for ch in "*?[")
    for m in re.finditer(r"\{(input|item)\}", output):
        before, rest = output[:m.start()], output[m.end():]
        if m[1] == "item" and not wild(before) and not (rest[:1].isdigit() or rest[:1] in ("*", "?", "[")):
            return True
        if m[1] == "input" and not need_item and not wild(output):
            return True
    return False


def _output_base(c: Channel) -> Path:
    """The real folder a channel's output pattern starts in (the part before any wildcard or {...})."""
    pattern = c.output if Path(c.output).is_absolute() else str(Path(c.folder) / c.output)
    cut = min([i for i in (pattern.find(ch) for ch in "*?{[") if i >= 0] or [len(pattern)])
    head = pattern[:cut]
    base = Path(head) if head.endswith(("\\", "/")) else Path(head).parent
    try:
        return base.resolve()
    except OSError:
        return base


def channels() -> list[Channel]:
    return parse_channels(packs.setting(PACK, "channels"))


def channel(name: str) -> Channel:
    for c in channels():
        if c.name == name.strip().lower():
            return c
    raise ActionError(f"there is no channel called '{name}' (set channels up on the Packs page)")


def max_parallel() -> int:
    value = packs.setting(PACK, "max_parallel").strip()
    return max(1, min(int(value), 16)) if value.isascii() and value.isdigit() else 3


def _batch(c: Channel) -> bool:
    """The channel's program, or its retry program, goes through cmd.exe (.bat or .cmd), which also
    splits on spaces."""
    try:
        folder = _local_folder(c.folder)
        return any(_which(split(cmd)[0], folder).suffix.lower() in (".bat", ".cmd")
                   for cmd in (c.command, c.retry_command) if cmd)
    except (ValueError, ActionError, IndexError):
        return True  # unknown: the stricter rule


def check_input(c: Channel, text: str) -> str:
    """P-PIPE-3: what an item may be, by how the channel's command uses it."""
    text = text.strip()
    if not text or len(text) > MAX_INPUT or any(ord(ch) < 32 for ch in text):
        raise ActionError(f"an item must be one line of 1 to {MAX_INPUT} characters")
    if c.uses("{input}") or "{input}" in c.output:
        steps = text.replace("\\", "/").split("/")
        if not SAFE_INPUT.fullmatch(text) or ".." in steps:
            raise ActionError(f"'{text[:60]}' cannot go into {c.name}'s command: use letters, digits, spaces and "
                              "_ . @ + / \\ - only, starting with a letter or digit, with no '..' step (for free "
                              "text, the channel's command can take {input_file} instead)")
        if " " in text and _batch(c):
            raise ActionError(f"'{text[:60]}' has a space, and {c.name}'s program is a .bat or .cmd file, which "
                              "would split it: leave the spaces out or use {input_file}")
    return text


# ---- building the command (P-PIPE-3) --------------------------------------------------------------

def split(command: str) -> list[str]:
    """A command line into words, Windows style: double quotes group words (also inside a word, as in
    --out="a b") and are dropped; backslashes stay as they are (review M14)."""
    words, cur, quoted, started = [], [], False, False
    for ch in command:
        if ch == '"':
            quoted, started = not quoted, True
        elif ch in " \t" and not quoted:
            if started:
                words.append("".join(cur))
            cur, started = [], False
        else:
            cur.append(ch)
            started = True
    if quoted:
        raise ValueError("a quote is not closed")
    if started:
        words.append("".join(cur))
    return words


def _which(program: str, folder: Path) -> Path:
    """The program's full path. A path with a folder in it is taken from the channel's folder (for
    example .venv\\Scripts\\python.exe); a bare name is looked up on PATH only, never in the current
    folder (Windows would run a python.exe that happens to sit in the repo first)."""
    p = Path(program)
    win = os.name == "nt"
    if p.is_absolute() or any(s in program for s in ("/", "\\")):
        full = p if p.is_absolute() else folder / p
        if full.is_file() and (not win or full.suffix.lower() in PROGRAM_TYPES):
            return full.resolve()
        raise ActionError(f"the program {program} does not exist or is not a .exe, .com, .bat or .cmd file")
    # Review M14: only names Windows really starts (never an extensionless "npm" next to npm.cmd).
    if win:
        names = ([program] if p.suffix.lower() in PROGRAM_TYPES else []) + [program + e for e in PROGRAM_TYPES]
    else:
        names = [program]
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d or not Path(d).is_absolute():
            continue  # "." or an empty entry would mean the current folder
        for name in names:
            cand = Path(d) / name
            if cand.is_file() and (win or os.access(cand, os.X_OK)):
                return cand.resolve()
    raise ActionError(f"the program {program} is not on PATH (give its full path in the command)")


@dataclass
class Plan:
    argv: list[str]
    folder: Path
    output: str  # the pattern with {input} and {item} filled in
    input_file: Path | None


def plan(c: Channel, item: dict, attempt: int, run_dir: Path) -> Plan:
    if c.problems:
        raise ActionError(f"channel {c.name} needs fixing on the Packs page: {'; '.join(c.problems)}")
    folder = _local_folder(c.folder)
    text = check_input(c, item["input"])
    command = c.retry_command if item["tries"] > 0 and c.retry_command else c.command  # a start before failed
    words = split(command)
    input_file = run_dir / "input.txt"

    def fill(word: str) -> str:
        return word.replace("{input_file}", str(input_file)).replace("{item}", str(item["id"])) \
            .replace("{input}", text)

    argv = [str(_which(words[0], folder))] + [fill(w) for w in words[1:]]
    output = c.output.replace("{item}", str(item["id"])).replace("{input}", text)
    return Plan(argv, folder, output, input_file if c.uses("{input_file}") else None)


def fingerprint(c: Channel, item: dict) -> str:
    """P-PIPE-5: what an approval is pinned to: the channel's set-up, the item and its text."""
    return hashlib.sha256(f"{c.fingerprint()}|{item['id']}|{item['input']}".encode()).hexdigest()[:16]


def command_line(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv)


# ---- processes (P-PIPE-6) -------------------------------------------------------------------------

DETACHED, NEW_GROUP, BREAKAWAY, NO_WINDOW = 0x8, 0x200, 0x01000000, 0x08000000
WRAPPER = Path(__file__).with_name("_wrap.py")


def _created(pid: int, handle: int | None = None) -> int | None:
    """When a process started (Windows FILETIME), to tell it from a later process with the same id."""
    if os.name != "nt":
        return 0 if _alive_posix(pid) else None
    k32 = ctypes.windll.kernel32
    own = handle is None
    h = k32.OpenProcess(0x1000, False, pid) if own else handle  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        c, e, k, u = (ctypes.c_ulonglong() for _ in range(4))
        if not k32.GetProcessTimes(ctypes.c_void_p(h), ctypes.byref(c), ctypes.byref(e), ctypes.byref(k),
                                   ctypes.byref(u)):
            return None
        code = ctypes.c_ulong()
        if own and k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code)) and code.value != 259:
            return None  # ended (259 = STILL_ACTIVE)
        return c.value
    finally:
        if own:
            k32.CloseHandle(ctypes.c_void_p(h))


def _alive_posix(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def alive(pid: int | None, created: int | None) -> bool:
    return bool(pid) and created is not None and _created(pid) == created


def launch(p: Plan, item: dict, run_dir: Path, started: float) -> tuple[int, int | None]:
    """Starts the wrapper, detached (no window, outside the hub's console). Returns (pid, created).
    spec.json is complete before the wrapper starts: never rewritten while it may be read (review M14)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    if p.input_file is not None:
        p.input_file.write_text(item["input"] + "\n", encoding="utf-8")
    env = {"PYTHONUNBUFFERED": "1", "HUB_PIPELINE_ITEM": str(item["id"]), "HUB_PIPELINE_CHANNEL": item["channel"]}
    (run_dir / "spec.json").write_text(json.dumps({"argv": p.argv, "cwd": str(p.folder), "env": env,
                                                   "output": p.output, "started": started}), encoding="utf-8")
    cmd = [sys.executable, "-I", str(WRAPPER), str(run_dir)]
    flags = DETACHED | NEW_GROUP | BREAKAWAY if os.name == "nt" else 0
    kwargs = dict(cwd=str(run_dir), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  close_fds=True)
    try:
        proc = subprocess.Popen(cmd, creationflags=flags, **kwargs) if os.name == "nt" else \
            subprocess.Popen(cmd, start_new_session=True, **kwargs)
    except OSError:
        if os.name != "nt":
            raise
        # The hub may run inside a job that does not allow breaking away: start it inside then.
        proc = subprocess.Popen(cmd, creationflags=DETACHED | NEW_GROUP, **kwargs)
    if os.name != "nt":
        return proc.pid, 0
    # The wrapper says its own id first thing. With a venv python.exe (a launcher that starts the real
    # Python as its child) that is not proc.pid: watching or stopping the launcher would miss the run.
    end = time.time() + 15
    while time.time() < end:
        info = wrapper_info(str(run_dir))
        if isinstance(info.get("wrapper"), int):
            pid, created = wrapper_pid(str(run_dir))
            if pid is not None:
                return pid, created
            break  # it already ended: the result file says how
        if proc.poll() is not None and proc.returncode != 0:
            break  # the launcher failed: watch it, the tick sees there is no result
        time.sleep(0.05)
    return proc.pid, _created(proc.pid, int(proc._handle))


def wrapper_pid(run_dir: str) -> tuple[int | None, int | None]:
    """The wrapper's (pid, created) from pid.json, only if that process is still exactly the one that
    wrote it: the wrapper records its own start time (review M14: after a reboot the number can belong
    to anything)."""
    info = wrapper_info(run_dir)
    pid, created = info.get("wrapper"), info.get("created")
    if isinstance(pid, int) and isinstance(created, int) and _created(pid) == created:
        return pid, created
    return None, None


def unique_run_dir(base: Path) -> Path:
    """A run folder never used before (review M14: queue numbers start again if queue.db is reset)."""
    if not base.exists():
        return base
    return base.with_name(f"{base.name}-{int(time.time() * 1000)}")


def wrapper_info(run_dir: str) -> dict:
    """pid.json, written by the wrapper as it starts: its own pid, the program's pid, and the job."""
    try:
        data = json.loads((Path(run_dir) / "pid.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def kill(pid: int | None, created: int | None, run_dir: str = "") -> bool:
    """Stops a run and everything it started, only if pid is still the same process (review M14).

    Two nets, because neither catches everything alone:
    - taskkill /T ends the wrapper and every process still linked to it by parent;
    - the wrapper's Windows job then closes and ends what is left in it, also a process whose parent
      already exited. (A Microsoft Store Python, and a venv or py launcher, moves its children out of
      the job: below those, only the first net reaches a process.)
    The handle stays open throughout, so the process number cannot pass to another process meanwhile."""
    if not pid or created is None:
        return False
    if os.name != "nt":
        if not alive(pid, created):
            return False
        import signal
        os.killpg(pid, signal.SIGKILL)
        return True
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1001, False, pid)  # PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        if _created(pid, h) != created:
            return False  # a different process has this number now
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        subprocess.run([str(taskkill), "/T", "/F", "/PID", str(pid)], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW, timeout=60)
        k32.TerminateProcess(ctypes.c_void_p(h), 1)  # if taskkill could not: its job closes with it
        return True
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


def result(run_dir: str) -> dict | None:
    try:
        data = json.loads((Path(run_dir) / "result.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("code"), int) else None
    except (OSError, ValueError):
        return None


def output_found(pattern: str, folder: Path, since: float, taken: frozenset = frozenset()) -> str:
    """P-PIPE-2: a file matching the pattern, not empty, written during this run, and not already the
    output of another item (review M14: two runs at once never claim one file). "" if none."""
    base = pattern if Path(pattern).is_absolute() else str(folder / pattern)
    best = ""
    best_t = 0.0
    for name in glob.iglob(base, recursive=True):
        try:
            st = os.stat(name)
        except OSError:
            continue
        if os.path.normcase(os.path.abspath(name)) in taken:
            continue
        if os.path.isfile(name) and st.st_size > 0 and st.st_mtime >= since - 2 and st.st_mtime >= best_t:
            best, best_t = name, st.st_mtime
    return best


def log_age(run_dir: str, started: float) -> float:
    """Seconds since the run last wrote to its log (or since it started, if it wrote nothing)."""
    try:
        last = os.stat(Path(run_dir) / "output.log").st_mtime
    except OSError:
        last = started
    return time.time() - max(last, started)


def hours(secs: float) -> str:
    secs = int(secs)
    return f"{secs // 3600}h {secs % 3600 // 60:02d}m" if secs >= 3600 else f"{secs // 60}m"
