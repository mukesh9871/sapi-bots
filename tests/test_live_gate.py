"""Live Milestone 6 checks against real Claude. Skipped unless HUB_LIVE=1.

- M6 check 1: the demo acting tool is held under Ask, runs under Auto, is blocked under Never
- M6 check 2: every call is in the activity log, and the daily cap stops further Auto runs
- M6 check 3: a task stops when its turn budget is reached
"""

import asyncio
import os

import pytest

from hub.chat import claude_session
from hub.chat.claude_session import ChatSession, delete_transcript
from hub.connectors import demo
from hub.engine import roles
from hub.engine.roles import Role
from hub.gate.gate import Gate
from hub.gate.store import GateStore

pytestmark = pytest.mark.skipif(os.environ.get("HUB_LIVE") != "1", reason="set HUB_LIVE=1 to run live tests")

POSTER = Role("poster", "Poster", "haiku", ("demo.post_note",),
              "You post notes with your post_note tool when asked. Use the tool exactly once per note asked for. "
              "Be brief. Report what the tool told you.")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")
    monkeypatch.setattr(demo, "OUTBOX", tmp_path / "outbox" / "notes.md")
    gate = Gate(GateStore(tmp_path / "hub.db"), paused=lambda: False)
    yield gate, tmp_path / "outbox" / "notes.md"
    for d in claude_session.transcript_dirs()[1:]:  # leave no test transcripts behind
        for f in d.glob("*.jsonl"):
            delete_transcript(f.stem)


def run(role, gate, prompts):
    async def go():
        s = ChatSession(role=role, gate=gate, ctrl_c_shield=False)
        out = []
        async with s:
            for p in prompts:
                out.append([ev async for ev in s.send(p)])
        return out
    return asyncio.run(go())


def text(evs):
    return "".join(e.text for e in evs if e.kind == "text")


def test_ask_auto_never_with_a_real_agent(env):  # M6 check 1 and 2
    gate, outbox = env
    ask = run(POSTER, gate, ["Post a note with the text: ask-run."])[0]
    assert "mcp__hub__demo_post_note" in [e.text for e in ask if e.kind == "tool"]
    assert not outbox.exists()  # Ask: nothing happened yet
    item = gate.store.approvals()[0]
    assert "ask-run" in item["payload"]["text"] and item["agent"] == "Poster"
    gate.approve(item["id"])
    assert "ask-run" in outbox.read_text(encoding="utf-8")

    gate.store.set_rule("local", "demo", "post_note", "auto", 1)
    run(POSTER, gate, ["Post a note with the text: auto-run.", "Now post another note with the text: over-cap."])
    body = outbox.read_text(encoding="utf-8")
    assert "auto-run" in body and "over-cap" not in body  # the cap of 1 held the second one
    held = [i for i in gate.store.approvals() if "over-cap" in i["payload"]["text"]]
    assert held and held[0]["reason"] == "daily cap reached"

    gate.store.set_rule("local", "demo", "post_note", "never", 1)
    never = run(POSTER, gate, ["Post a note with the text: never-run."])[0]
    assert "never-run" not in outbox.read_text(encoding="utf-8")
    assert "mcp__hub__demo_post_note" in [e.text for e in never if e.kind == "tool"]

    decisions = [e["decision"] for e in reversed(gate.store.activity())]  # M6 check 2: all logged
    assert decisions[:1] == ["held"] and "approved" in decisions and "auto" in decisions
    assert decisions.count("held") >= 2 and decisions[-1] == "blocked"


def test_task_stops_at_its_budget(env):  # M6 check 3, G-BUDGET-1
    gate, _ = env
    role = Role("busy", "Busy", "haiku", ("Write",), "You do exactly what is asked, one tool call at a time.",
                max_turns=2)
    evs = run(role, gate, ["Create five files one at a time, a.txt to e.txt, each containing its own name."])[0]
    done = next(e for e in evs if e.kind == "done")
    assert done.data["subtype"] == "error_max_turns", done.data
    assert [e.data["max"] for e in evs if e.kind == "turn"][:1] == [2]
