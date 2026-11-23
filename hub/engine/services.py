"""Pack services (Milestone 14, G-PACK-3): plain code a pack runs in the background, with no Claude.

A switched-on pack's connector module may define:

    TICK(ctx)   called every TICK seconds while the app is on (in a worker thread, one at a time per
                function), for work that is only watching and bookkeeping, like a run queue
    BUSY()      returns "" or why the pack must not be switched off or removed now

TICK never acts outside the app by itself: anything that acts goes through ctx.call, which is the
approval gate (G-APPR-1), exactly as an agent's call would. While automation is paused, ctx.paused()
is True: a service must not start new work then (G-BUDGET-2). ctx.notify(title, text, failed) shows a
notice in the window like a finished schedule does. The engine knows nothing about what a pack does.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable

from hub import connectors

TICK = 15  # seconds
log = logging.getLogger("hub.services")


@dataclass
class ServiceContext:
    pack: str
    call: Callable[[str, dict], Any]  # (action id, payload) -> GateResult, through the gate as this pack
    approval: Callable[[int], dict | None]  # an inbox item, to follow what I decided
    withdraw: Callable[[int, str], bool]  # take back this pack's own pending request (logged, never run)
    notify: Callable[[str, str, bool], None]  # (title, text, failed)
    paused: Callable[[], bool]


class PackServices:
    def __init__(self, gate: Any, notify: Callable[[str, str, bool], None], paused: Callable[[], bool],
                 tick: float = TICK):
        self.gate, self.notify, self.paused, self.tick_s = gate, notify, paused, tick
        self._task: asyncio.Task | None = None

    def context(self, pack: str) -> ServiceContext:
        agent = f"{pack} service"  # the activity log shows who asked: the pack's own code, not an agent
        return ServiceContext(pack=pack, call=lambda action_id, payload: self.gate.call(action_id, payload, agent, pack),
                              approval=self.gate.store.approval,
                              withdraw=lambda item_id, reason: self.gate.withdraw(item_id, reason, pack),
                              notify=self.notify, paused=self.paused)

    async def tick(self) -> None:
        for pack, fn in connectors.pack_hooks("TICK"):
            try:
                await asyncio.to_thread(fn, self.context(pack))
            except Exception:  # one broken pack never stops the others or the app
                log.exception("service tick of pack %s failed", pack)

    async def _loop(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self.tick_s)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


def busy(pack: str) -> str:
    """G-PACK-3: why a pack's own background work stops it being switched off or removed ("" if not)."""
    for pid, fn in connectors.pack_hooks("BUSY"):
        if pid != pack:
            continue
        try:
            reason = fn()
        except Exception as exc:  # a broken check says so, it never hides running work
            return f"its busy check failed ({type(exc).__name__})"
        if reason:
            return str(reason)
    return ""
