"""Milestone 4 offline checks: web source links (F-WEB-2) and plain errors (N-REL-1). No network.

SDK message shapes below were captured live (claude-agent-sdk 0.2.180).
"""

import asyncio
import sqlite3

from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock, UserMessage

from hub.chat.claude_session import MAX_SOURCES, ChatSession, build_options, merge_sources, web_sources
from hub.chat.history import HistoryStore

SEARCH = {"query": "q", "results": [
    {"tool_use_id": "s1", "content": [{"title": "Python 3.15", "url": "https://www.python.org/downloads/"},
                                      {"title": "Evil", "url": "javascript:alert(1)"},
                                      {"title": "No scheme", "url": "example.com/x"}]},
    "Some summary text from the search tool"]}
FETCH = {"bytes": 1, "code": 200, "result": "page text", "url": "https://blog.python.org/x"}


# ---- F-WEB-2: sources ------------------------------------------------------------------

def test_web_sources_reads_search_and_fetch_shapes():
    assert web_sources(SEARCH) == ([], [{"url": "https://www.python.org/downloads/", "title": "Python 3.15"}])
    assert web_sources(FETCH) == ([{"url": "https://blog.python.org/x", "title": ""}], [])
    assert web_sources("plain text") == ([], [])  # other tools: nothing


def test_only_plain_http_links_pass():  # web data is information, not instructions
    for bad in ["javascript:alert(1)", "data:text/html,x", "file:///C:/x", "https://a b", "/relative", 5, None,
                "https://a.com\n", "https://x/>)[c](javascript:alert(1)", 'https://a"b',
                "https://a.com" + chr(92)]:  # review M4: export-safe (a trailing backslash escapes the >)
        assert web_sources({"url": bad}) == ([], []), bad


def test_titles_are_one_line():  # review M4: a title cannot start a new Markdown block in the export
    hit = {"results": [{"content": [{"title": "Foo\n\n# Injected  heading", "url": "https://a.b"}]}]}
    assert web_sources(hit)[1][0]["title"] == "Foo # Injected heading"


def test_merge_puts_pages_read_first_dedupes_and_caps():
    read = [{"url": "https://b", "title": ""}]
    hits = [{"url": f"https://h{i}", "title": f"H{i}"} for i in range(20)] + [{"url": "https://b", "title": "B"}]
    out = merge_sources(read, hits)
    assert out[0] == {"url": "https://b", "title": "B"}  # read first, and a title fills the gap
    assert len(out) == MAX_SOURCES and len({s["url"] for s in out}) == MAX_SOURCES


def test_sources_saved_with_reply_and_exported(tmp_path):
    store = HistoryStore(tmp_path / "c.db")
    chat = store.create_chat("q")
    src = [{"url": "https://x.y/a", "title": "A [b] <img> title"}]
    store.add_message(chat["id"], "user", "q")
    store.add_message(chat["id"], "bot", "answer", sources=src)
    msgs = store.messages(chat["id"])
    assert msgs[0]["sources"] == [] and msgs[1]["sources"] == src
    md = store.export_markdown(chat["id"], folder=tmp_path).read_text(encoding="utf-8")
    # brackets cannot break the link; a tag stays text in viewers that render raw HTML
    assert "- [A \\[b\\] \\<img\\> title](<https://x.y/a>)" in md


def test_old_database_gets_sources_column(tmp_path):  # a chats.db from Milestone 3
    db = tmp_path / "c.db"
    with sqlite3.connect(db) as c:
        c.executescript("""
            CREATE TABLE chats (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, session_id TEXT, system_prompt TEXT);
            CREATE TABLE messages (id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, role TEXT NOT NULL,
                text TEXT NOT NULL, status TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);
            INSERT INTO chats VALUES (1, 'old', 0, 0, NULL, NULL);
            INSERT INTO messages VALUES (1, 1, 'bot', 'old reply', '', 0);
        """)
    store = HistoryStore(db)
    assert store.messages(1)[0]["sources"] == []
    store.add_message(1, "bot", "new", sources=[{"url": "https://a", "title": ""}])
    assert store.messages(1)[1]["sources"][0]["url"] == "https://a"


# ---- events from a fake client -----------------------------------------------------------

class FakeClient:
    def __init__(self, messages):
        self.messages = messages

    async def query(self, text):
        pass

    def receive_response(self):
        async def gen():
            for m in self.messages:
                yield m
        return gen()


def events_for(messages):
    async def run():
        s = ChatSession(web_enabled=True)
        s.client = FakeClient(messages)
        return [ev async for ev in s.send("hi")]
    return asyncio.run(run())


def result(is_error=False):
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=is_error,
                         num_turns=1, session_id="s")


def test_sources_event_carries_full_list_so_far():
    evs = events_for([UserMessage(content=[], tool_use_result=SEARCH),
                      UserMessage(content=[], tool_use_result=FETCH),
                      AssistantMessage(content=[TextBlock("answer")], model="m"), result()])
    src = [e.data["sources"] for e in evs if e.kind == "sources"]
    assert len(src) == 2
    assert [s["url"] for s in src[-1]] == ["https://blog.python.org/x", "https://www.python.org/downloads/"]


def test_retry_events_and_cli_error_text_is_not_a_reply():  # N-REL-1, measured live
    retry = {"type": "system", "subtype": "api_retry", "attempt": 2, "max_retries": 5,
             "retry_delay_ms": 1250, "error_status": None, "error": "unknown"}
    evs = events_for([SystemMessage("api_retry", retry),
                      AssistantMessage(content=[TextBlock("API Error: Connection refused (ECONNREFUSED)")],
                                       model="m", error="server_error"),
                      result(is_error=True)])
    kinds = [e.kind for e in evs]
    assert "text" not in kinds  # never shown or saved as the bot's reply
    r = next(e for e in evs if e.kind == "retry")
    assert r.data["attempt"] == 2 and r.data["max_retries"] == 5
    err = next(e for e in evs if e.kind == "error")
    assert err.text == "server_error" and "ECONNREFUSED" in err.data["detail"]


def test_retries_limited_so_an_outage_fails_fast(tmp_path):  # N-REL-1: default was 3 silent minutes
    assert build_options(cwd=tmp_path).env["CLAUDE_CODE_MAX_RETRIES"] == "5"


def test_every_error_code_has_plain_words():  # N-REL-1 / R-AUTH-6: no raw codes in the window
    from pathlib import Path
    from typing import get_args

    from claude_agent_sdk.types import AssistantMessageError

    js = Path(__file__).parent.parent.joinpath("hub", "web", "app.js").read_text(encoding="utf-8")
    block = js[js.index("const ERRORS = {"):js.index("};", js.index("const ERRORS = {"))]
    for code in [*get_args(AssistantMessageError), "network", "exception"]:
        assert f"{code}:" in block, code


def test_failed_fetch_is_not_a_source():  # review M4: a 404 page was not read
    assert web_sources({"url": "https://a.b/gone", "code": 404, "result": "Not found"}) == ([], [])
    assert web_sources({"url": "https://a.b/ok", "code": 200, "result": "x"})[0]


def test_reset_time_from_an_earlier_turn_is_forgotten():  # review M4, R-AUTH-6
    async def run():
        s = ChatSession(web_enabled=False)
        s.last_rate_limit = {"status": "allowed", "resets_at": 1}  # left over from the last turn
        s.client = FakeClient([AssistantMessage(content=[TextBlock("You've hit your limit · resets 5pm")],
                                                model="m", error="rate_limit"), result(is_error=True)])
        evs = [ev async for ev in s.send("hi")]
        return s.last_rate_limit, evs
    last, evs = asyncio.run(run())
    assert last is None  # so the window shows Claude's own words with the real reset time
    assert "resets 5pm" in next(e for e in evs if e.kind == "error").data["detail"]
