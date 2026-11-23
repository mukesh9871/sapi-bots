"""The approval gate (Milestone 6): every acting tool call passes here (G-APPR-1..4, G-LOG-1).

    agent calls an acting tool
        -> paused?            hold in the inbox ("automation paused")      G-BUDGET-2
        -> rule Never         refuse, log "blocked"
        -> rule Ask           hold in the inbox, log "held"                 G-APPR-2
        -> rule Auto, cap hit hold in the inbox ("daily cap reached")      G-APPR-4
        -> rule Auto          log "started", run, log "done" or "failed"   N-REL-3

Under Ask the agent is told at once that the action is held, and its reply goes on. When I
approve (maybe after editing), the gate runs the action itself and logs it as "approved".
Text the agent read on a web page cannot change this: the rule decides, not the agent (N-SEC-7).
Approving runs the action even while automation is paused: an approval is my own act, not automation.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from hub import connectors
from hub.connectors.base import Action, ActionError
from hub.gate.store import GateStore

DEFAULT_PACK = "local"  # until packs exist (Milestone 8), every rule belongs to "local"
REPEAT_WINDOW = 24 * 3600  # N-REL-3: an identical call within this time is treated as a repeat


def _normal(payload: dict) -> dict:
    return {k: v.replace("\r\n", "\n").strip() if isinstance(v, str) else v for k, v in payload.items()
            if not (isinstance(v, str) and not v.strip())}


def _jsonable(payload: Any) -> dict:
    """Whatever the agent sent, in a form the log can store (it may not be an object at all)."""
    return payload if isinstance(payload, dict) else {"value": repr(payload)[:1000]}


class RepeatRun(ValueError):
    """Approving content that already ran recently: the window asks me to confirm (N-REL-3)."""


@dataclass
class GateResult:
    text: str  # what the agent is told
    is_error: bool = False
    decision: str = ""  # auto | held | blocked
    item_id: int | None = None


class Gate:
    def __init__(self, store: GateStore, paused: Callable[[], bool],
                 lookup: Callable[[str], Action | None] = connectors.get):
        self.store = store
        self.paused = paused
        self.lookup = lookup
        self._decide = threading.Lock()  # see call(): one decision at a time

    def _run(self, action: Action, payload: dict, agent: str, pack: str, decision: str,
             call_id: str | None = None, note: str = "") -> tuple[bool, str]:
        """Logs "started" before running, so a crash mid-run is never re-run blindly (N-REL-3).
        call_id given: the "started" row is already written (an Auto run, see store.start_auto)."""
        if call_id is None:
            call_id = str(uuid.uuid4())
            self.store.log(call_id, agent, pack, action.platform, action.id, payload, decision, "started", note)
        try:
            result = action.run(payload)
        except Exception as exc:  # the connector failed: log it and say so plainly
            detail = f"{type(exc).__name__}: {exc}"
            self.store.log(call_id, agent, pack, action.platform, action.id, payload, decision, "failed", detail)
            return False, detail
        self.store.log(call_id, agent, pack, action.platform, action.id, payload, decision, "done", result)
        return True, result

    def call(self, action_id: str, payload: Any, agent: str, pack: str = DEFAULT_PACK) -> GateResult:
        action = self.lookup(action_id)
        if action is None or not action.acting:
            # G-LOG-1: a refused call is logged too, even if nothing could have happened (review M6).
            self.store.log(str(uuid.uuid4()), agent, pack, "", str(action_id), _jsonable(payload), "blocked",
                           detail="unknown action")
            return GateResult(f"Unknown action {action_id}.", True, "blocked")
        try:
            clean = action.clean(payload)
            if action.check is not None:  # content rules first: never hold what can never be published
                try:
                    action.check(clean)
                except ActionError:
                    raise
                except Exception as exc:  # a connector's check that breaks still refuses, and is logged (G-LOG-1)
                    raise ActionError(f"the content could not be checked ({type(exc).__name__})") from None
        except ActionError as exc:
            self.store.log(str(uuid.uuid4()), agent, pack, action.platform, action.id, _jsonable(payload),
                           "blocked", detail=f"bad input: {exc}")
            return GateResult(f"Not done: {exc}.", True, "blocked")
        # One decision at a time: the repeat check, the cap check and the "started" or "held"
        # row happen together, so two identical calls at once can never both run (review M6).
        # The connector itself runs after the lock is released.
        with self._decide:
            decided = self._decide_call(action, clean, agent, pack)
        if isinstance(decided, GateResult):
            return decided
        ok, result = self._run(action, clean, agent, pack, "auto", decided)
        if ok:
            return GateResult(result, False, "auto")
        return GateResult(f"The action failed: {result}. It may still have happened, so do not retry it: "
                          "the user will check.", True, "auto")

    def _decide_call(self, action: Action, clean: dict, agent: str, pack: str) -> GateResult | str:
        """Returns the answer for the agent, or the call_id of an Auto run already logged "started"."""
        rule = self.store.rule(pack, action.platform, action.name)
        earlier = self.store.earlier_same(action.id, clean, time.time() - REPEAT_WINDOW)
        if rule["mode"] == "never":
            self.store.log(str(uuid.uuid4()), agent, pack, action.platform, action.id, clean, "blocked",
                           detail="rule is Never")
            return GateResult("Not done: the user's rules do not allow this action.", True, "blocked")
        if earlier and earlier["kind"] == "item":
            # The same request is already waiting: never queue it twice, but log the call (review M6).
            self.store.log(str(uuid.uuid4()), agent, pack, action.platform, action.id, clean, "held",
                           detail=f"repeat of inbox item {earlier['id']}, which is still waiting")
            return GateResult(f"This exact action is already waiting for the user as item {earlier['id']}. "
                              "Do not try it again.", False, "held", earlier["id"])
        # N-REL-3: a repeat of something that ran, failed after maybe acting, or that I rejected is
        # never run automatically, and the inbox says why (review M6).
        repeat = ""
        if earlier and earlier["kind"] == "rejected":
            repeat = f"you rejected this exact content before (item {earlier['id']})"
        elif earlier and earlier["kind"] == "edited":
            repeat = f"you approved an edited version of this before (item {earlier['id']})"
        elif earlier:
            repeat = f"repeat of an earlier call ({earlier['outcome']}): check whether it already happened"
        if self.paused():
            held = "; ".join(x for x in ("automation paused", repeat) if x)
        elif rule["mode"] == "ask" or repeat:
            held = repeat
        else:
            call_id = str(uuid.uuid4())
            if self.store.start_auto(call_id, agent, pack, action.platform, action.id, clean, rule["daily_cap"]):
                return call_id
            held = "daily cap reached"
        item = self.store.hold(agent, pack, action.platform, action.id, clean, held)
        self.store.log(str(uuid.uuid4()), agent, pack, action.platform, action.id, clean, "held",
                       detail=f"inbox item {item}" + (f" ({held})" if held else ""))
        return GateResult(f"Held for the user's approval as item {item}. It runs only if the user approves it; "
                          "do not try it again.", False, "held", item)

    def approve(self, item_id: int, payload: Any = None, confirm_repeat: bool = False) -> dict[str, Any]:
        """G-APPR-2: Approve, or Edit then Approve. Runs once, even on a double click (N-REL-3)."""
        item = self.store.approval(item_id)
        if item is None:
            raise KeyError("item not found")
        action = self.lookup(item["action_id"])
        if action is None:
            raise ValueError("this connector is no longer installed")
        rule = self.store.rule(item["pack"], item["platform"], action.name)
        if rule["mode"] == "never":
            raise ValueError("the rule for this action is now Never: change it in Settings to approve this")
        clean = action.clean(payload if payload is not None else item["payload"])  # raises ActionError
        if action.check is not None:  # an edit must meet the content rules too; the item stays pending if not
            try:
                action.check(clean)
            except ActionError:
                raise
            except Exception as exc:
                raise ActionError(f"the content could not be checked ({type(exc).__name__})") from None
        # A text box turns CRLF into LF: that alone is not an edit (review M6).
        edited = _normal(clean) != _normal(item["payload"])
        if not edited:
            clean = item["payload"]
        if item["status"] != "pending":  # a double click says "already decided", not "already ran"
            raise ValueError("this item was already decided")
        # The log row names the inbox item, so "agent proposed" and "I approved" stay linked (review M6).
        note = f"inbox item {item_id}" + (", edited by the user" if edited else "")
        call_id = str(uuid.uuid4())
        # Same lock as call(): the repeat check, the claim and the "started" row happen together, so
        # two approvals of the same content at once cannot both skip the question (review M6).
        with self._decide:
            ran = self.store.recent_run(action.id, clean, time.time() - REPEAT_WINDOW)
            if ran and not confirm_repeat:
                # N-REL-3 (review M6): exactly this content already ran within a day, for example
                # through another item. Say so, and run it again only if I confirm.
                raise RepeatRun(f"exactly this content already ran in the last 24 hours ({ran}). "
                                "Approve again to post it a second time.")
            if not self.store.claim(item_id, "running", clean if edited else None):
                raise ValueError("this item was already decided")
            self.store.log(call_id, item["agent"], item["pack"], action.platform, action.id, clean, "approved",
                           "started", note)
        ok, result = self._run(action, clean, item["agent"], item["pack"], "approved", call_id)
        self.store.finish(item_id, "done" if ok else "failed", result)
        return {"ok": ok, "result": result, "edited": edited}

    def reject(self, item_id: int) -> None:
        item = self.store.approval(item_id)
        if item is None:
            raise KeyError("item not found")
        if not self.store.claim(item_id, "rejected"):
            raise ValueError("this item was already decided")
        self.store.log(str(uuid.uuid4()), item["agent"], item["pack"], item["platform"], item["action_id"],
                       item["payload"], "rejected", detail=f"inbox item {item_id}")

    def recover(self) -> int:
        """N-REL-3, at start-up: a call that started but never finished (crash) is marked
        "unknown". It is never re-run: I check the outside system myself. Also an inbox item
        left "running" goes to "failed" with that note. Returns how many calls were marked."""
        calls = self.store.unfinished_calls()
        for c in calls:
            self.store.log(c["call_id"], c["agent"], c["pack"], c["platform"], c["action_id"], c["payload"],
                           c["decision"], "unknown", "the app stopped while this ran: check whether it happened")
        for item in self.store.approvals("running"):
            if self.store.item_outcome(item["id"]) == "done":  # it finished; only the inbox row lagged behind
                self.store.finish(item["id"], "done", "finished (recorded after a restart)")
            else:
                self.store.finish(item["id"], "failed", "the app stopped while this ran: check whether it happened")
        return len(calls)

    def ensure_rules(self, pack: str = DEFAULT_PACK) -> None:
        """Every installed acting action has a rule, Ask by default, so Settings can list them (G-APPR-3)."""
        for action in connectors.actions().values():
            if action.acting:
                self.store.rule(pack, action.platform, action.name)
