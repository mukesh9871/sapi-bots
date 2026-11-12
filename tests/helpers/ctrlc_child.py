"""Helper for test_console_ctrl_c_keeps_chat_alive. Runs in its own console.

Sends a real console Ctrl+C mid-reply (as pressing Ctrl+C in a terminal does) and
checks the chat still answers afterwards. Writes "turn1 <reason>" / "turn2 <subtype>" lines.
"""

import asyncio
import ctypes
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hub.chat.claude_session import ChatSession  # noqa: E402

k32 = ctypes.windll.kernel32
out = open(sys.argv[1], "w")


def log(*a):
    print(*a, file=out, flush=True)


async def main():
    k32.SetConsoleCtrlHandler(None, False)  # behave like a normal terminal (Ctrl+C on)
    loop = asyncio.get_running_loop()
    async with ChatSession(model="haiku", web_enabled=False) as chat:
        signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(lambda: asyncio.ensure_future(chat.stop())))

        async def press_ctrl_c():
            await asyncio.sleep(3)
            k32.GenerateConsoleCtrlEvent(0, 0)  # Ctrl+C to every process on this console

        asyncio.create_task(press_ctrl_c())
        async for ev in chat.send("Write a 400-word story about a lighthouse."):
            if ev.kind == "done":
                log("turn1", ev.data["terminal_reason"])
        try:
            async for ev in chat.send("Say just: still here"):
                if ev.kind == "done":
                    log("turn2", ev.data["subtype"])
        except Exception as exc:
            log("turn2 FAILED", type(exc).__name__)


asyncio.run(main())
