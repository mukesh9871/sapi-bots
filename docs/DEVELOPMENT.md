# Development

For changing the hub: where the code is, how to run the tests, and the working rules. How
the parts fit together is in [ARCHITECTURE.md](ARCHITECTURE.md).

## Project layout

```
hub/
  server.py          FastAPI app: token, Host and Origin checks, chat stream, history API
  auth_check.py      start-up checks: remove billing vars, detect sign-in, status line
  config.py          config.json load and save
  cli_chat.py        terminal chat (Milestone 1)
  chat/
    claude_session.py  wraps the Agent SDK client, tool limits and folder check, transcripts
    history.py         SQLite chat store, search, replay text, Markdown export
    personalities.py   built-in prompts
  engine/
    roles.py           role files: read, check, save, copy, delete (Milestone 5)
    teams.py           team template files: read, check, save, delete (Milestone 7)
    runs.py            team runs in data/hub.db: steps, log, questions, restart handling
    orchestrator.py    runs a team: lead tools, member steps, pause, stop, review gate, budgets
    packs.py           packs: read, install, switch off, remove, settings (Milestones 8, 9)
    services.py        pack services: plain background code per pack, TICK and BUSY (Milestone 14)
  scheduler/
    schedules.py       schedules and their runs in data/hub.db; when they run next
    runner.py          the scheduler loop: due runs, missed runs, file and after triggers
  gate/
    gate.py            approval gate: rules, caps, pause, repeats, approve and reject (Milestone 6)
    store.py           data/hub.db: rules, approvals inbox, append-only activity log
  connectors/
    base.py            what a connector action looks like
    demo.py            demo "post a note" acting tool
    __init__.py        finds every connector here and in switched-on packs (no engine change)
  web/               index.html, app.css, app.js, vendor/ (marked, DOMPurify, highlight.js)
scripts/usage_test.py  plan-usage measurement
scripts/make_shortcut.ps1  creates the desktop shortcut
tests/               pytest suite (offline and live)
docs/                REQUIREMENTS.md, ARCHITECTURE.md, PIPELINE_MANAGER.md, USER_GUIDE.md,
                     SAFETY.md, DEVELOPMENT.md
packs/sample/         sample pack (Milestone 8)
packs/routines/       routine automation pack (Milestone 9)
packs/social/         social media manager pack (Milestone 10)
packs/jobs/           job applications pack (Milestone 11)
packs/devteam/        software team pack (Milestone 12)
packs/video/          video production pack (Milestone 13)
packs/pipeline/       pipeline manager pack (Milestone 14)
start.bat            starts the chat window
```

How a message flows:

```
Browser (app.js) ──POST /api/chat──▶ server.py ──▶ ChatSession (Agent SDK) ──▶ Claude CLI ──▶ Claude (subscription)
      ▲                                  │
      └──── streamed reply (events) ─────┤
                                         └──▶ history.py ──▶ data/chats.db
```

## Tests

```
.venv\Scripts\python -m pytest -q                 # offline: no Claude calls
set HUB_LIVE=1 && .venv\Scripts\python -m pytest -q   # also the live tests against real Claude
```

In PowerShell, use `$env:HUB_LIVE='1'` instead of `set HUB_LIVE=1`.

- Offline tests use a temporary database and a fake Claude session. They never touch
  your real `data/chats.db`.
- Live tests use your subscription (a few short messages, mostly on Haiku).
- Current result: 597 passed (560 offline, 37 live).

## Working rules for development

The full list is in [CLAUDE.md](../CLAUDE.md). In short:

- One milestone at a time, in order. Each one goes through: understand, analyse, plan,
  code, test (offline, live, smoke run), independent review and rework until clean,
  final test, hand over, then commit after it has been checked by hand.
- Requirement IDs (for example F-HIST-4, R-AUTH-3) appear in code comments and commits.
- No new dependency without asking first.
- Never commit `data/`, `projects/`, `exports/`, `config.json` or anything secret.
