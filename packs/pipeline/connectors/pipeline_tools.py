"""Pipeline manager (Milestone 14, P-PIPE-1..7): a queue of items per channel repo on this PC, started
one after another (or a few at once), watched, and the next one started when one finishes.

- start_run (acting, through Approvals): runs the channel's command for one item. The approval shows
  the exact command and folder and is pinned to them (P-PIPE-5). Only the queue service asks for it.
- TICK (plain code every few seconds, no Claude): watches running items, finishes them, retries a
  failure once, then flags it; starts the next items while slots are free (P-PIPE-2, P-PIPE-4).
- Tools for the Pipeline manager role (no outside action): add items, read the queue, skip, move,
  retry, pause and resume a channel. Buttons on the Queue for me: the same, plus Stop.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from contextlib import closing
from pathlib import Path

from hub.connectors.base import Action, ActionError
from hub.engine import packs

_spec = importlib.util.spec_from_file_location("hub_pack_pipeline__pstate", Path(__file__).with_name("_pstate.py"))
P = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = P  # its dataclasses look their module up there
_spec.loader.exec_module(P)

START = "pipeline_tools.start_run"
MAX_ITEMS = 50  # per add
SHOW_DONE = 20  # finished items per channel on the Queue
RANK = {"running": 0, "launching": 0, "approval": 1, "waiting": 2, "failed": 3, "stopped": 4, "skipped": 4, "done": 5}


# ---- the queue -----------------------------------------------------------------------------------

def _get(item_id) -> dict:
    try:
        n = int(str(item_id).strip())
    except ValueError:
        raise ActionError("item must be a queue number") from None
    with closing(P.db()) as conn:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (n,)).fetchone()
    if row is None:
        raise ActionError(f"there is no item {n} in the queue")
    return dict(row)


def _set(item_id: int, **fields) -> None:
    with closing(P.db()) as conn, conn:
        conn.execute(f"UPDATE items SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                     (*fields.values(), item_id))


def _rows(where: str = "1", args: tuple = ()) -> list[dict]:
    with closing(P.db()) as conn:
        return [dict(r) for r in conn.execute(f"SELECT * FROM items WHERE {where} ORDER BY position, id", args)]


def _paused() -> set[str]:
    with closing(P.db()) as conn:
        return {r[0] for r in conn.execute("SELECT channel FROM paused")}


def _front(channel: str) -> float:
    rows = _rows("channel = ? AND state = 'waiting'", (channel,))
    return (rows[0]["position"] - 1) if rows else time.time()


def _same(text: str) -> str:
    return os.path.normcase(os.path.normpath(text))


def add_items(payload: dict[str, str]) -> str:
    c = P.channel(payload["channel"])
    lines = [l.strip() for l in payload["items"].replace("\r\n", "\n").split("\n") if l.strip()]
    if not lines or len(lines) > MAX_ITEMS:
        raise ActionError(f"give 1 to {MAX_ITEMS} items, one per line")
    texts = [P.check_input(c, l) for l in lines]  # all or nothing: one bad line adds none
    added, dup = [], []
    with P.LOCK, closing(P.db()) as conn, conn:
        # Compared the way Windows compares paths: "Story1" and "story1", "a/b" and "a\\b" name the same
        # file, so they are the same item (review M14).
        queued = {_same(r[0]) for r in conn.execute("SELECT input FROM items WHERE channel = ? AND state IN "
                                                    "('waiting', 'approval', 'launching', 'running')", (c.name,))}
        last = conn.execute("SELECT MAX(position) FROM items").fetchone()[0] or 0
        for t in texts:
            if _same(t) in queued:
                dup.append(t)
                continue
            queued.add(_same(t))
            last = max(last + 1, time.time())
            added.append(conn.execute("INSERT INTO items (channel, input, position, added_at, added_by) VALUES "
                                      "(?, ?, ?, ?, ?)", (c.name, t, last, time.time(), "agent")).lastrowid)
    out = f"Added {len(added)} item(s) to {c.name}" + (f": numbers {', '.join(map(str, added))}." if added else ".")
    if dup:
        out += f" Already in the queue, not added again: {', '.join(dup)[:400]}."
    if c.problems:
        out += f" Note: channel {c.name} needs fixing on the Packs page before anything runs: {'; '.join(c.problems)}"
    return out


def skip(payload: dict[str, str]) -> str:
    with P.LOCK:
        item = _get(payload["item"])
        if item["state"] == "approval":
            raise ActionError(f"item {item['id']} waits for the user's approval: reject it in Approvals instead")
        if item["state"] != "waiting":
            raise ActionError(f"item {item['id']} is {item['state']}: only a waiting item can be skipped")
        _set(item["id"], state="skipped", note="skipped", ended_at=time.time())
    return f"Item {item['id']} ({item['input']}) is skipped."


def retry(payload: dict[str, str]) -> str:
    with P.LOCK:
        item = _get(payload["item"])
        if item["state"] not in ("failed", "stopped", "skipped"):
            raise ActionError(f"item {item['id']} is {item['state']}: only a failed, stopped or skipped item can "
                              "be tried again")
        _set(item["id"], state="waiting", fails=0, note="back in the queue (first in line)", warned=0,
             position=_front(item["channel"]), approval=None)
    return f"Item {item['id']} ({item['input']}) is back in the queue, first in line for {item['channel']}."


def move(payload: dict[str, str]) -> str:
    where = payload.get("where", "").strip().lower() or "top"
    if where not in ("top", "bottom", "up", "down"):
        raise ActionError("where must be top, bottom, up or down")
    with P.LOCK:
        item = _get(payload["item"])
        if item["state"] != "waiting":
            raise ActionError(f"item {item['id']} is {item['state']}: only a waiting item can be moved")
        rows = _rows("channel = ? AND state = 'waiting'", (item["channel"],))
        i = next(n for n, r in enumerate(rows) if r["id"] == item["id"])
        if where == "top":
            _set(item["id"], position=rows[0]["position"] - 1)
        elif where == "bottom":
            _set(item["id"], position=rows[-1]["position"] + 1)
        else:
            j = i - 1 if where == "up" else i + 1
            if 0 <= j < len(rows):  # swap with its neighbour
                _set(item["id"], position=rows[j]["position"])
                _set(rows[j]["id"], position=item["position"])
    return f"Item {item['id']} moved {where} in {item['channel']}."


def pause_channel(payload: dict[str, str]) -> str:
    c = P.channel(payload["channel"])
    with closing(P.db()) as conn, conn:
        conn.execute("INSERT OR IGNORE INTO paused VALUES (?)", (c.name,))
    return f"Channel {c.name} is paused: nothing new starts there (a run already going goes on)."


def resume_channel(payload: dict[str, str]) -> str:
    c = P.channel(payload["channel"])
    with closing(P.db()) as conn, conn:
        conn.execute("DELETE FROM paused WHERE channel = ?", (c.name,))
    return f"Channel {c.name} goes on: its next item starts when a slot is free."


def _row_channel(fn):
    """A Queue button on an item row that acts on the item's channel."""
    return lambda payload: fn({"channel": _get(payload["item"])["channel"]})


def stop(payload: dict[str, str]) -> str:
    """Only a button for me: ends a run and everything it started. Never retried by itself."""
    with P.LOCK:
        item = _get(payload["item"])
        if item["state"] not in ("running", "launching"):
            raise ActionError(f"item {item['id']} is {item['state']}: only a running item can be stopped")
        pid, created = item["pid"], item["created"]
        if not pid and item["run_dir"]:  # a start the app was cut off from: the wrapper may run (review M14)
            pid, created = P.wrapper_pid(item["run_dir"])  # exactly the wrapper, never its old number
        P.kill(pid, created, item["run_dir"])
        late = P.result(item["run_dir"]) if item["run_dir"] else None
        if late is not None:  # it had just finished by itself (review M14): judge that, never "stopped"
            _finish(item, late, P_channel(item["channel"]))
            return f"Item {item['id']} had already finished: {_get(item['id'])['state']}."
        _set(item["id"], state="stopped", note="stopped by you", ended_at=time.time())
    return f"Item {item['id']} is stopped."


def P_channel(name: str):
    try:
        return P.channel(name)
    except ActionError:
        return None  # no longer set up: default retries


# ---- reading it ----------------------------------------------------------------------------------

def _when(t) -> str:
    return time.strftime("%d %b %H:%M", time.localtime(t)) if t else ""


def _ordered() -> list[tuple[str, list[dict]]]:
    """Channels in my set-up order (then any channel no longer set up), each with its items in order."""
    rows = _rows()
    names = [c.name for c in P.channels() if c.name != "?"]
    names += sorted({r["channel"] for r in rows} - set(names))
    out = []
    for name in names:
        mine = [r for r in rows if r["channel"] == name]
        live = sorted((r for r in mine if RANK[r["state"]] <= 2), key=lambda r: (RANK[r["state"]], r["position"], r["id"]))
        ended = sorted((r for r in mine if RANK[r["state"]] > 2), key=lambda r: -(r["ended_at"] or r["id"]))[:SHOW_DONE]
        out.append((name, live + ended))
    return out


def _time(r: dict) -> str:
    if not r["started_at"]:
        return ""
    return P.hours((r["ended_at"] or time.time()) - r["started_at"]) if r["state"] != "waiting" else ""


def view_queue(payload: dict[str, str]) -> str:
    chans = {c.name: c for c in P.channels()}
    paused = _paused()
    out = []
    for name, items in _ordered():
        c = chans.get(name)
        if c is None:
            out.append(["", name, "", "not set up", "", "", "", "", "this channel is no longer in the settings"])
        elif c.problems:
            out.append(["", name, "", "needs fixing", "", "", "", "", "; ".join(c.problems)[:400]])
        elif name in paused:
            out.append(["", name, "", "paused", "", "", "", "", "nothing new starts here until you resume it"])
        for r in items:
            log = str(Path(r["run_dir"]) / "output.log") if r["run_dir"] else ""
            note = r["note"] or (r["output"] if r["state"] == "done" else "")
            out.append([r["id"], name, r["input"], r["state"], r["tries"], _when(r["started_at"]), _time(r), log, note])
    return json.dumps({"columns": ["#", "Channel", "Item", "State", "Tries", "Started", "Time", "Log", "Note"],
                       "rows": out})


def queue(payload: dict[str, str]) -> str:
    want = payload.get("channel", "").strip().lower()
    lines = []
    for row in json.loads(view_queue({}))["rows"]:
        if want and row[1] != want:
            continue
        num = f"#{row[0]} " if row[0] != "" else ""
        lines.append(f"{num}{row[1]}: {row[2]} [{row[3]}]" + (f" tries {row[4]}" if row[4] else "")
                     + (f", {row[6]}" if row[6] else "") + (f" ({row[8]})" if row[8] else ""))
    return "\n".join(lines) or "The queue is empty."


def list_channels(payload: dict[str, str]) -> str:
    paused = _paused()
    out = []
    for c in P.channels():
        state = "needs fixing: " + "; ".join(c.problems) if c.problems else ("paused" if c.name in paused else "on")
        limit = P.hours(c.timeout) if c.timeout else "none"
        # The command itself stays out of the agent's context (it is the user's; review M14).
        out.append(f"{c.name} ({state})\n  folder: {c.folder}\n  output: {c.output}\n"
                   f"  gpu: {'yes' if c.gpu else 'no'}, parallel: {c.parallel}, time limit: {limit}, "
                   f"retries: {c.retries}")
    return "\n".join(out) + f"\nAt most {P.max_parallel()} runs at once, one GPU run at a time." if out else \
        "No channels are set up yet: the user adds them on the Packs page (Pipeline manager, Settings)."


# ---- starting a run (acting, P-PIPE-3, P-PIPE-5) -------------------------------------------------

def _ready(payload: dict[str, str]) -> tuple[dict, object]:
    item = _get(payload["item"])
    if item["state"] != "approval" or not item["run_dir"]:
        raise ActionError(f"item {item['id']} is {item['state']} now, not waiting to start")
    if payload.get("attempt", "") != str(item["asks"]):  # each ask is its own approval (review M14)
        raise ActionError(f"this is not the current request for item {item['id']}")
    c = P.channel(item["channel"])
    if payload.get("fingerprint", "") != P.fingerprint(c, item):  # the channel's set-up changed since
        raise ActionError(f"channel {c.name}'s set-up changed since this was asked: it will be asked again")
    if c.problems:
        raise ActionError(f"channel {c.name} needs fixing on the Packs page: {'; '.join(c.problems)}")
    return item, c


def check_start(payload: dict[str, str]) -> None:
    with P.LOCK:
        _ready(payload)


def show_start(payload: dict[str, str]) -> str:
    item, c = _ready(payload)
    attempt = item["tries"] + 1
    p = P.plan(c, item, attempt, Path(item["run_dir"]))
    limit = f"stopped after {P.hours(c.timeout)}" if c.timeout else "no time limit"
    quiet = f", you get a notice if it prints nothing for {P.hours(c.quiet)}" if c.quiet else ""
    return (f"Run item {item['id']} of {c.name} ({item['input']}), attempt {attempt}.\n\nFolder:\n{p.folder}\n\n"
            f"Command (run directly, no shell):\n{P.command_line(p.argv)}\n\n"
            f"Done when it exits with 0 and leaves a new file matching:\n{p.output}\n\n"
            f"GPU: {'yes (one GPU run at a time)' if c.gpu else 'no'}. {limit.capitalize()}{quiet}. "
            f"It runs with your Windows rights.")


def start_run(payload: dict[str, str]) -> str:
    with P.LOCK:
        item, c = _ready(payload)
        attempt = item["tries"] + 1
        run_dir = Path(item["run_dir"])  # chosen when it was asked for, so the card showed this one
        now = time.time()
        try:
            p = P.plan(c, item, attempt, run_dir)  # uses tries before this attempt (retry_command)
        except Exception as exc:
            _failed(dict(item, tries=attempt), f"could not start: {exc}", c)
            raise ActionError(f"could not start item {item['id']}: {exc}") from None
        _set(item["id"], state="launching", tries=attempt, run_dir=str(run_dir), started_at=now, ended_at=None,
             pid=None, created=None, code=None, output="", warned=0, note="starting")
        try:
            pid, created = P.launch(p, item, run_dir, now)
        except Exception as exc:
            _failed(dict(item, tries=attempt), f"could not start: {exc}", c)
            raise ActionError(f"could not start item {item['id']}: {exc}") from None
        _set(item["id"], state="running", pid=pid, created=created, note="")
    return f"Started item {item['id']} of {c.name} ({item['input']}), attempt {attempt}. Log: {run_dir / 'output.log'}"


# ---- the service (P-PIPE-2, P-PIPE-4, P-PIPE-6) ---------------------------------------------------

_notify = None  # the context's notify, kept for helpers called from a tick


def _say(title: str, text: str, failed: bool) -> None:
    if _notify:
        try:
            _notify(title, text, failed)
        except Exception:
            pass  # a notice that cannot be shown never stops the queue


def _failed(item: dict, why: str, chan) -> None:
    """A failed attempt: back in line to try again (P-PIPE-2: once by default), or flagged to me."""
    retries = chan.retries if chan is not None else 1
    fails = item["fails"] + 1
    name = f"{item['channel']} #{item['id']} ({item['input']})"
    if fails <= retries:
        _set(item["id"], state="waiting", fails=fails, ended_at=time.time(), approval=None,
             note=f"attempt {item['tries']} failed: {why}; trying again")
        _say(f"Pipeline: {name}", f"attempt {item['tries']} failed ({why}); it will be tried again", False)
    else:
        _set(item["id"], state="failed", fails=fails, ended_at=time.time(), note=why)
        _say(f"Pipeline: {name}", f"failed: {why}. It needs you (Queue on the Packs page)", True)


def _finish(item: dict, res: dict, chan) -> None:
    try:
        spec = json.loads((Path(item["run_dir"]) / "spec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        spec = {}
    if res["code"] != 0:
        return _failed(item, f"exit code {res['code']} (see the log)", chan)
    # A file another item already finished with counts only if it was written again after that item
    # ended (review M14): a run that left nothing never takes the last run's file, and a re-run of a
    # finished item may still write the same path again.
    with closing(P.db()) as conn:
        rows = conn.execute("SELECT output, ended_at FROM items WHERE state = 'done' AND output != '' AND id != ?",
                            (item["id"],)).fetchall()
    taken = set()
    for out, ended in rows:
        try:
            if os.stat(out).st_mtime <= (ended or 0):  # written before that item ended: still its file
                taken.add(os.path.normcase(os.path.abspath(out)))
        except OSError:
            pass  # gone: it cannot be found either
    taken = frozenset(taken)
    found = P.output_found(spec.get("output", ""), Path(spec.get("cwd", ".")), item["started_at"] or 0, taken) \
        if spec.get("output") else ""
    if not found:
        return _failed(item, f"it exited with 0 but left no new file matching {spec.get('output', '?')}", chan)
    _set(item["id"], state="done", code=0, ended_at=res.get("ended") or time.time(), output=found, note="")
    _say(f"Pipeline: {item['channel']} #{item['id']} done", f"{item['input']}: {found}", False)


def _watch(ctx, chans: dict) -> None:
    now = time.time()
    for item in _rows("state IN ('approval', 'launching', 'running')"):
        chan = chans.get(item["channel"])
        if item["state"] == "approval":
            box = ctx.approval(item["approval"]) if item["approval"] else None
            if box is None:
                if item["approval"] or now - (item["ended_at"] or 0) > 60:
                    _set(item["id"], state="waiting", approval=None, note="")  # the question went away: ask again
            elif box["status"] == "pending" and (chan is None or chan.problems
                                                 or item["fingerprint"] != P.fingerprint(chan, item)):
                # Review M14: the set-up changed (or went) while this waited: that card can never be
                # approved, so it is withdrawn and the item asked again, never left blocking its slot.
                ctx.withdraw(item["approval"], "the channel's set-up changed: asked again")
                _set(item["id"], state="waiting", approval=None, note="the set-up changed: asked again")
            elif box["status"] == "rejected":
                _set(item["id"], state="skipped", ended_at=now, note="you rejected it in Approvals")
            elif box["status"] == "failed":
                # Refused at approval (the set-up changed after the card was shown): nothing ran, so it is
                # asked again rather than counted as a failed attempt (review M14). A start that ran and
                # failed has already left this state.
                _set(item["id"], state="waiting", approval=None,
                     note=f"not started ({box.get('result', '')[:200]}): asked again")
            continue
        alive = P.alive(item["pid"], item["created"])
        res = P.result(item["run_dir"]) if item["run_dir"] else None  # read after alive: never miss a result
        if res is not None:
            _finish(item, res, chan)
        elif item["state"] == "launching":
            wpid, created = P.wrapper_pid(item["run_dir"]) if item["run_dir"] else (None, None)
            if wpid is not None:  # review M14: the app stopped mid-start, but the run did start: adopt it
                _set(item["id"], state="running", pid=wpid, created=created, note="")
            elif now - (item["started_at"] or 0) > 120:
                _failed(item, "the start was interrupted (the app stopped while it started)", chan)
        elif not alive:
            _failed(item, "it ended without a result (the PC restarted, or it was closed from outside)", chan)
        elif chan and chan.timeout and now - item["started_at"] > chan.timeout:
            P.kill(item["pid"], item["created"], item["run_dir"])
            late = P.result(item["run_dir"])  # it ended by itself just before (review M14): judge that
            if late is not None:
                _finish(item, late, chan)
                continue
            _failed(item, f"stopped after the time limit of {P.hours(chan.timeout)}", chan)
        else:
            quiet = chan.quiet if chan else P.DEFAULT_QUIET
            age = P.log_age(item["run_dir"], item["started_at"])
            if quiet and age > quiet and not item["warned"]:
                _set(item["id"], warned=1, note=f"no output in its log for {P.hours(age)} (still running)")
                _say(f"Pipeline: {item['channel']} #{item['id']}", f"still running, but no output for {P.hours(age)}: "
                     "check its log", False)
            elif item["warned"] and age < quiet:
                _set(item["id"], warned=0, note="")


def _start_next(ctx, chans: dict) -> None:
    """P-PIPE-4: start what the slots allow: per channel, one GPU run overall, a total cap."""
    if ctx.paused():  # G-BUDGET-2: nothing new starts while automation is paused
        return
    paused = _paused()
    usable = [c for c in chans.values() if not c.problems and c.name not in paused]
    while True:
        usable = [c for c in usable if c.name not in _blocked]
        busy = _rows("state IN ('approval', 'launching', 'running')")
        gpu_busy = any(chans.get(r["channel"]) is None or chans[r["channel"]].gpu for r in busy)
        started = False
        for c in usable:
            if len(busy) >= P.max_parallel():
                return
            if sum(r["channel"] == c.name for r in busy) >= c.parallel or (c.gpu and gpu_busy):
                continue
            nxt = _rows("channel = ? AND state = 'waiting'", (c.name,))
            if not nxt:
                continue
            if not _ask(ctx, c, nxt[0]):
                _blocked.add(c.name)  # review M14: one failed start stops this channel for this tick
            started = True
            break  # count the slots again
        if not started:
            return


_blocked: set[str] = set()  # channels whose start failed in this tick


def _ask(ctx, c, item: dict) -> bool:
    """Asks for (or, under Auto, makes) the start. False if it could not start."""
    fp = P.fingerprint(c, item)
    asks = item["asks"] + 1  # a new request each time, so a retry after a reject is never a "repeat"
    run_dir = P.unique_run_dir(packs.data_dir(P.PACK) / "runs" / f"{item['id']}-{asks}")
    _set(item["id"], state="approval", fingerprint=fp, approval=None, asks=asks, ended_at=time.time(), note="asking",
         run_dir=str(run_dir))
    result = ctx.call(START, {"item": str(item["id"]), "attempt": str(asks), "fingerprint": fp})
    now = _get(item["id"])
    if result.decision == "held" and now["state"] == "approval":
        _set(item["id"], approval=result.item_id, note=f"waiting for your approval (Approvals item {result.item_id})")
        return True
    if now["state"] == "approval":  # refused by a rule or a check, or failed before it started
        _set(item["id"], state="failed", ended_at=time.time(), note=result.text[:400])
        _say(f"Pipeline: {c.name} #{item['id']}", f"not started: {result.text[:300]}", True)
        return False
    return now["state"] in ("launching", "running")


def TICK(ctx) -> None:
    """Runs every few seconds (hub/engine/services.py), in a worker thread."""
    global _notify
    _notify = ctx.notify
    with P.LOCK:
        _blocked.clear()
        chans = {c.name: c for c in P.channels() if c.name != "?"}
        _watch(ctx, chans)
        _start_next(ctx, chans)


def BUSY() -> str:
    going = _rows("state IN ('launching', 'running')")
    return f"{len(going)} pipeline run(s) are still going: stop them on the Queue first, or let them finish" \
        if going else ""


ITEM = {"item": {"description": "The queue number of the item", "max": 12, "number": True}}
CHANNEL = {"channel": {"description": "The channel name, as list_channels shows it", "max": 40}}
ACTIONS = [
    Action(connector="pipeline_tools", name="start_run", platform="pipeline", run=start_run, show=show_start,
           show_label="Will run exactly",
           check=check_start, required=("item", "attempt", "fingerprint"),
           fields={**ITEM, "attempt": {"description": "Request number", "max": 6, "number": True},
                   "fingerprint": {"description": "The channel set-up and item it was asked for", "max": 40}},
           description="Start one queued item in its channel repo (only the queue service asks for this)."),
    Action(connector="pipeline_tools", name="list_channels", platform="pipeline", acting=False, fields={},
           run=list_channels, description="List the channel repos the user set up, with their state."),
    Action(connector="pipeline_tools", name="queue", platform="pipeline", acting=False, run=queue,
           fields={"channel": {"description": "Optional: only this channel", "max": 40}},
           description="Show the queue: what runs, what waits, what finished or failed."),
    Action(connector="pipeline_tools", name="add_items", platform="pipeline", acting=False, run=add_items,
           required=("channel", "items"),
           fields={**CHANNEL, "items": {"description": "The items to queue, one per line (for example story "
                                                       "files or topics)", "max": 30_000}},
           description="Add items to the end of a channel's queue. They start by themselves when a slot is free."),
    Action(connector="pipeline_tools", name="skip", platform="pipeline", acting=False, fields=ITEM, required=("item",),
           run=skip, description="Skip a waiting item."),
    Action(connector="pipeline_tools", name="retry", platform="pipeline", acting=False, fields=ITEM, required=("item",),
           run=retry, description="Put a failed, stopped or skipped item back in the queue, first in line."),
    Action(connector="pipeline_tools", name="move", platform="pipeline", acting=False, run=move, required=("item",),
           fields={**ITEM, "where": {"description": "top, bottom, up or down", "max": 10}},
           description="Move a waiting item within its channel's queue."),
    Action(connector="pipeline_tools", name="pause_channel", platform="pipeline", acting=False, fields=CHANNEL,
           required=("channel",), run=pause_channel, description="Pause a channel: nothing new starts there."),
    Action(connector="pipeline_tools", name="resume_channel", platform="pipeline", acting=False, fields=CHANNEL,
           required=("channel",), run=resume_channel, description="Let a paused channel go on."),
    Action(connector="pipeline_tools", name="view_queue", platform="pipeline", acting=False, fields={},
           run=view_queue, description="Queue"),
    # Buttons on the Queue for me (pack.md row_actions); never a role's tool.
    *[Action(connector="pipeline_tools", name=f"row_{n}", platform="pipeline", acting=False, fields=ITEM,
             required=("item",), run=fn, description=label, ui_only=True)
      for n, fn, label in (("skip", skip, "Skip"), ("retry", retry, "Retry"),
                           ("up", lambda p: move({**p, "where": "up"}), "Up"),
                           ("down", lambda p: move({**p, "where": "down"}), "Down"),
                           ("stop", stop, "Stop"),
                           ("pause", _row_channel(pause_channel), "Pause channel"),
                           ("resume", _row_channel(resume_channel), "Resume channel"))],
]
