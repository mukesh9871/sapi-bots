# Personal AI Automation Hub

A personal chat assistant and automation hub that runs on your own Windows PC and
uses your **Claude subscription** through the Claude Agent SDK. No Anthropic API key,
no cloud server, one user.

The full specification is in [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md). Ticked
boxes and ✅ marks there show what is built and tested.

## Status

| Phase | Milestone | State |
|---|---|---|
| 1. Core chat app | 1. Terminal chat | Done |
| | 2. Desktop chat window | Done |
| | 3. History and settings | Done |
| | 4. Web search and polish | Ready to test |
| 2. Generic platform | 5 to 8. Roles, approvals, log, budgets, teams, scheduler, packs | Not started |
| 3. Instruction packs | 9 to 13. Routines, social media, jobs, software team, video | Not started |

## What it does today

- **Chat window in your browser** with replies that stream in, Markdown and code
  highlighting, a Copy button on code blocks, and a Stop button.
- **Personalities:** Fun (witty), Straight (blunt and short), Explainer (step by step),
  or your own custom prompt. You can switch in the middle of a chat.
- **Model choice:** plan default, Haiku, Sonnet or Opus.
- **Web search** on or off (read-only: it never posts or sends anything). Answers that
  used the web show a folded **Sources** list with the pages it searched and read.
- **Chat history** saved on your PC: a sidebar with every chat (newest first), search
  over titles and messages (matching at the start of words), rename, delete, and
  Markdown export. You can switch chats while the bot is still answering: the reply
  keeps going and is saved to its own chat.
- **Plain error messages with Retry:** usage limit (with the reset time), lost
  internet connection, and other problems. If Claude does not answer, the window shows
  "trying again (2 of 5)" and gives up after about 15 seconds.
- **Picks up where you left off:** reopen an old chat after a restart and Claude
  still remembers it.
- **Settings page (⚙):** bot name and avatar, theme (system, light or dark), default
  model, web search, editable built-in prompts, and Delete all history.
- **Terminal chat** for quick use without the window.

## Requirements

- Windows 10 or 11
- Python 3.11 or newer
- A Claude subscription (Pro or Max) and the Claude Code CLI signed in with it

## Setup (once)

1. Sign in to Claude with your subscription:
   ```
   claude
   /login
   ```
   Choose **"Claude account with subscription"** and finish in the browser.
2. Create the virtual environment and install the dependencies:
   ```
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```

Do **not** set `ANTHROPIC_API_KEY`. If it is set, the app removes it from its own
process at start-up and shows a notice, so you are never billed on an API account.

## Running it

**Chat window:** double-click `start.bat`, or the **AI Hub** desktop shortcut. It
starts the server and opens the chat in your browser. Close the console window (or
press Ctrl+C in it) to stop.

**Desktop shortcut** (once):
```
powershell -ExecutionPolicy Bypass -File scripts\make_shortcut.ps1
```
It creates "AI Hub" on your Desktop. The console starts minimized in the taskbar.

**Terminal chat:**
```
.venv\Scripts\python -m hub.cli_chat [--personality fun|straight|explainer] [--model sonnet] [--no-web]
```
Commands while chatting: `/usage`, `/retry`, `/quit`. Ctrl+C while a reply is
streaming stops the reply. Ctrl+C at the prompt quits.

**Usage test** (how much of your plan limit a 20-message chat uses):
```
.venv\Scripts\python scripts\usage_test.py [--model sonnet] [--web]
```
Check the meter at claude.ai, Settings, Usage, before and after. The report goes to
`data/usage_report.md`.

## Using the chat window

```
┌──────────────┬──────────────────────────────────────────────────────────┐
│ + New chat   │ ☰  Chat title     Personality ▾  Model ▾  Web ☑  Export ⚙ │
│ [search...]  ├──────────────────────────────────────────────────────────┤
│              │                                                          │
│ Trip plan  ⋯ │   You: ...                                               │
│ Pasta      ⋯ │   🤖 Bot: ... (streams in)                                │
│ ...          │                                                          │
│              ├──────────────────────────────────────────────────────────┤
│              │ [ type a message ...                    ]  Send / Stop   │
└──────────────┴──────────────────────────────────────────────────────────┘
```

- **Enter** sends. **Shift+Enter** adds a new line.
- **Right-click** a chat in the sidebar (or click ⋯) to rename, export or delete it.
- **Export** writes a Markdown file to `exports/`.
- **Delete all history** (in ⚙) asks you to confirm, then removes every chat and every
  saved Claude session for this app.

## Where things are stored

Everything stays on your PC. None of these are committed to git.

| What | Where |
|---|---|
| Settings | `config.json` (readable, safe to edit by hand) |
| Chats and messages | `data/chats.db` (SQLite) |
| Claude's own session files | `%USERPROFILE%\.claude\projects\<chat folder name>\*.jsonl` |
| Claude's working folder for chat | `data/chat_workdir/` |
| Exports | `exports/` |

**Settings in `config.json`:**

| Key | Values | Default |
|---|---|---|
| `personality` | `fun`, `straight`, `explainer`, `custom` | `straight` |
| `custom_prompt` | any text | empty |
| `model` | `""` (plan default), `haiku`, `sonnet`, `opus` | `""` |
| `web_enabled` | `true` or `false` | `true` |
| `bot_name` | short text | `Bot` |
| `avatar` | one emoji or up to 4 letters | 🤖 |
| `theme` | `system`, `light`, `dark` | `system` |
| `prompts` | edited built-in prompts, e.g. `{"fun": "..."}` | `{}` |
| `port` | local port for the server | `8765` |

A broken or hand-mangled value falls back to its default instead of crashing the app.
Never put passwords or keys in this file.

**How "remembering" works:** each chat is linked to a Claude session. When you reopen
a chat, the app resumes that session. If the session file is gone, it starts a new one
and sends the last 40 messages from `chats.db` as background (marked as information,
not instructions), with a note if older messages were left out.

Deleting a chat removes its rows in `chats.db` and its session files in this app's own
folder under `.claude\projects`. It never touches your other Claude Code projects.

## Safety rules built in

- **Subscription only.** Billing-switch variables (`ANTHROPIC_API_KEY`,
  `ANTHROPIC_AUTH_TOKEN`, Bedrock, Vertex, Foundry) are removed at start-up. The
  window's status line shows which login is in use.
- **Login stays private.** The app only asks the CLI *whether* you are signed in. It
  never reads, copies or prints your login token or email.
- **Local only.** The server listens on `127.0.0.1` only. Each launch makes a random
  token that the browser receives once. Every request must carry it and pass Host and
  Origin checks, so other websites and other machines cannot use the hub.
- **Limited tools.** In chat, Claude gets only web search and web fetch (when web is
  on). Every other tool is removed and denied, and permission bypass mode is never used.
- **Web text is information, not instructions.**
- Coming in Phase 2: every outside action (post, submit, send, delete) goes through an
  approval rule (Auto, Ask or Never) and is written to an append-only activity log.

## Project layout

```
hub/
  server.py          FastAPI app: token, Host and Origin checks, chat stream, history API
  auth_check.py      start-up checks: remove billing vars, detect sign-in, status line
  config.py          config.json load and save
  cli_chat.py        terminal chat (Milestone 1)
  chat/
    claude_session.py  wraps the Agent SDK client, session resume, transcript files
    history.py         SQLite chat store, search, replay text, Markdown export
    personalities.py   built-in prompts
  web/               index.html, app.css, app.js, vendor/ (marked, DOMPurify, highlight.js)
scripts/usage_test.py  plan-usage measurement
scripts/make_shortcut.ps1  creates the desktop shortcut
tests/               pytest suite (offline and live)
docs/REQUIREMENTS.md full specification and milestones
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
- Current result: 130 passed (111 offline, 19 live).

## Working rules for development

The full list is in [CLAUDE.md](CLAUDE.md). In short:

- One milestone at a time, in order. Each one goes through: understand, analyse, plan,
  code, test (offline, live, smoke run), independent review and rework until clean,
  final test, hand over, then commit after it has been checked by hand.
- Requirement IDs (for example F-HIST-4, R-AUTH-3) appear in code comments and commits.
- No new dependency without asking first.
- Never commit `data/`, `projects/`, `exports/`, `config.json` or anything secret.

## Known limits

- The chat folder location cannot be changed from the settings page yet.
- If a message fails before any reply arrives, that message is not kept in history
  (it goes back into the input box instead).
- In the terminal chat, Ctrl+C pressed while Claude is still starting is ignored.
- There are no automated browser tests for the window in the test suite yet (smoke
  runs in a real browser are done by hand for each milestone).
