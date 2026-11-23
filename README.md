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
| | 4. Web search and polish | Done |
| 2. Generic platform | 5. Roles and single agents | Done |
| | 6. Approvals, log and budgets | Done |
| | 7. Teams | Done |
| | 8. Scheduler and pack format | Ready to test |
| 3. Instruction packs | 9. Routine automation | Ready to test (the week-long check is yours) |
| | 10 to 13. Social media, jobs, software team, video | Not started |

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
- **Roles (agents):** the **Roles** button opens a page where you create, edit, copy
  and delete roles: a name, a job description, a model and the tools it may use. Pick a
  role in **Talk to** before the first message of a new chat (or press **Give it a
  task** on the Roles page), and that chat runs as the role.
- **Approvals and acting tools:** a role can list acting tools (today only the demo
  "post a note" tool, which writes to `data/outbox/notes.md`). Every call goes through
  your rule for it: **Auto** runs it and logs it (up to a daily cap, then it waits for
  you), **Ask** holds it in **Approvals**, **Never** refuses it. New rules start as Ask.
  Set rules in ⚙ Settings, Approval rules.
- **Approvals inbox:** the sidebar badge shows how many actions wait. Each one shows
  exactly what will be done. You can change the text, then Approve, or Reject.
- **Activity log:** everything done (or refused) on your behalf, searchable and
  filterable by pack, platform and date. Entries can never be changed or deleted.
- **Budgets and pause:** each role has a turn budget per task (default 30); a task that
  uses it all stops and says so. The **Pause** button at the bottom stops role tasks and
  holds every action, so plain chat stays available when your limit is nearly used.
- **Teams:** the **Teams** button opens a page where you build a team from your roles:
  a lead, an optional reviewer and the workers (a role can appear twice), plus "most
  members at once" (default 2, not counting the lead) and "most turns per run" (default
  100). Save it as a template, give it a task, and watch the board.
  - The lead splits the task into steps, gives them to members, collects the results
    and ends with a report (saved as `REPORT.md`). It cannot finish until the reviewer
    has checked the latest work.
  - Each member runs as its own role (its tools, model and approvals), one step at a
    time, and works only in the run's folder `projects/team-<run>/`. A role with no
    model uses the Settings model.
  - The review gate counts only members' steps. Give the lead a role without `Write`
    or `Edit`, so it cannot change files after the review.
  - The board shows one column per member: status (waiting, working, blocked, done),
    current step and output so far, plus the report and a log of what happened.
  - **Pause** interrupts running steps (and the lead, if it is working) and keeps what
    they wrote. **Resume**, **Stop** and **Message** work at any time. A message
    (redirect) reaches the lead the next time it checks for results, and the lead cannot
    finish before it has read it. A member can ask you a question, and the board shows
    it with an answer box.
  - A run that uses its turn budget stops and says so. The global Pause pauses every
    team. After a restart, unfinished runs are marked stopped and never re-run.
- **Schedules:** the **Schedules** button lets a role or a team do a task on its own:
  every day at a time, on chosen days, every few minutes or hours, when a new file appears
  in a folder, or after another schedule finished well. Each schedule shows its last run,
  result and next run. A role's run becomes a chat in history (open it and carry on); a
  team's run shows on the Teams board. **Run now** works whenever automation is not paused,
  and **Stop** at any time. A run that uses its turn budget does not count as finished well.
  - Runs happen only while the app is on. A run that came due while the app was off (or
    more than 2 minutes late), or while automation was paused, is listed under **Missed
    runs** with **Run late** and **Dismiss**. The badge on the button shows how many.
  - Pause also stops scheduled role tasks. A schedule never runs twice at once, and a
    chain of "after" schedules can never loop.
  - A folder watch counts file names that are new since it was made or switched on
    (copied or moved in). Saving a file that is already there does not count. Files that
    come during a run are handled by the next run. Files that arrived while the app was
    off are listed as missed, with their names. The role
    gets the file names. It can open them only if the folder is inside its own folder.
  - A scheduled chat cannot be continued or deleted while its run is still writing it.
- **Packs:** the **Packs** button lists the packs in the `packs` folder. **Install** adds
  the pack's roles, teams, connectors, approval rules and schedules. **Switch off** hides
  them and keeps the rules and schedules (they do not run while it is off). **Remove** deletes its rules, schedules and
  waiting approvals. None of this changes the app itself or another pack.
  - A pack's suggested Auto rules are installed as Ask, and its schedules are installed
    switched off: you turn on what you want.
  - Pack roles and teams are read-only (ids like `sample.notetaker`). Copy a role to make
    your own version.
  - The **Sample pack** shows the format: a planner and a note taker, a team, an "add a
    note" connector (writes to `data/outbox/sample-notes.md`, through Approvals) and a
    morning schedule.
- **Routine automation pack** (install it on the Packs page): four starter routines,
  installed switched off.
  - **Morning briefing** (every day 07:30): web news in ten bullets, and it tells you.
  - **Weekly summary** (Mondays 09:00): what the hub did this week, from the activity
    log and schedule runs.
  - **Stretch reminder** (every day 15:00): a short reminder that pops up in the window.
  - **Tidy my folder** (Fridays 16:00): sorts the loose files of one folder into
    subfolders by type. Set the folder on the Packs page. The tidier sees only that
    folder, and every move goes through Approvals (Ask until you choose Auto).
- **Routines in plain words:** in Schedules, describe a routine ("every weekday at 8,
  brief me on AI news and tell me") and press **Draft it**. The hub fills in who does
  it, the steps and the time. You check it, and nothing is saved until you press Save.
  The form also shows which outside actions the chosen role may take and the approval
  rule for each.
- **Tell me when done:** a schedule with this ticked shows its result in the window
  (and as a desktop notice, if you allow it) when it finishes. Good for reminders.
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
| Roles | `packs/local/roles/<id>.md` (readable, safe to edit by hand) |
| Each role's working folder | `projects/role-<id>/` |
| Team templates | `packs/local/teams/<id>.md` (readable, safe to edit by hand) |
| Packs | `packs/<id>/` (the sample and routines packs are part of the app; which ones are installed, and their settings, are in `data/hub.db`) |
| Schedules and their runs | `data/hub.db` (SQLite) |
| Each team run's working folder (its files and `REPORT.md`) | `projects/team-<run>/` |
| Approval rules, inbox, activity log, team runs (steps, log, questions) | `data/hub.db` (SQLite; the activity log is append-only) |
| Demo connector output | `data/outbox/notes.md` and `data/outbox/sample-notes.md` (sample pack) |

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
| `automation_paused` | `true` or `false` (the Pause button) | `false` |

A broken or hand-mangled value falls back to its default instead of crashing the app.
The one exception is the pause switch: if the file cannot be read, or
`automation_paused` is not `true` or `false`, the app starts **paused** and says so.
Never put passwords or keys in this file.

**How "remembering" works:** each chat is linked to a Claude session. When you reopen
a chat, the app resumes that session. If the session file is gone, it starts a new one
and sends the last 40 messages from `chats.db` as background (marked as information,
not instructions), with a note if older messages were left out.

**A role file** looks like this. The header lists the model (blank = the Settings
default) and the tools. The job description goes below it:

```
---
name: Researcher
model: sonnet
tools: WebSearch, WebFetch, Read, Write
max_turns: 30
---
You research a topic on the web and write report.md in your folder.
```

Tools a role may list: `WebSearch`, `WebFetch` (read-only), `Read`, `Glob`, `Grep`
(read-only, its own folder) and `Write`, `Edit` (change files in its own folder; they
bring `Read` with them), plus connector actions such as `demo.post_note` (acting: every
call goes through Approvals). `max_turns` is the task budget (1 to 200, default 30).
A file that lists anything else (for example `Bash`) is marked
"needs fixing" and never runs. Deleting a role keeps its chats (readable, not
continuable) and its folder.

**A pack** is a folder `packs/<id>/`:

```
packs/<id>/pack.md          header: name, version, description; below it, what the pack does
packs/<id>/roles/*.md       roles (same format as your own)
packs/<id>/teams/*.md       teams (a plain role id means this pack's role)
packs/<id>/connectors/*.py  connectors (modules that list ACTIONS, like hub/connectors/)
packs/<id>/rules.md         suggested rules, one per line: "<action id>: ask|never|auto [cap N]"
packs/<id>/settings.md      settings you fill in on the Packs page: "<key> | folder|text | <label>"
packs/<id>/schedules/*.md   schedules: header "name", "run: role <id>" or "run: team <id>",
                            "notify: yes" (tell me when done),
                            "when: daily 08:00" (or weekly mon,fri 09:30 / every 90 minutes /
                            file C:\folder / after <another schedule file name>);
                            the task below the header
```

A pack connector loads only while its pack is installed and switched on. An action that
clashes with one that already exists (same id, same tool name, or same platform and
action) is not loaded: the pack installed first keeps it, and the other cannot be
installed. A pack that does not read cleanly is shown with the problem and
cannot be installed.

**A team file** looks like this. Members are role ids (comma-separated), and the notes
below the header go to the lead:

```
---
name: Writers
lead: manager
reviewer: reviewer
members: writer, writer
max_agents: 2
max_turns: 100
---
Keep each step short.
```

`max_agents` is 1 to 5 and `max_turns` is 10 to 1000 (all members added up). The lead
and reviewer cannot also be workers, and a team has at most 8 workers. Deleting a run
removes its board records and session files but keeps its folder. Delete all history
also removes all team runs (not their folders), and is refused while a team is running.

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
  on). A role gets only the tools it lists. Every other tool is removed and denied, and
  permission bypass mode is never used. There is no shell tool at all.
- **A role stays in its folder.** A check before every tool call refuses file paths
  outside `projects/role-<id>/` (`..`, other drives, network paths, links pointing out,
  hidden characters) and refuses writing config files that coding agents or editors
  read (`CLAUDE.md`, `AGENTS.md`, `.claude`, `.git`, `.vscode` and similar), Windows
  shortcut-type files (`.url`, `.lnk`, `desktop.ini`) and files pytest would run
  (`conftest.py`, `test_*.py`). `pytest.ini` also keeps pytest out of `projects/`.
- **Installing a pack never switches a rule to Auto** and never edits files in `hub/`, and
  the app never changes a pack's files. But a pack's connectors are Python code that runs
  with the app's full rights: install only packs you trust (the sample pack is part of
  the app).
- **A team stays in its folder.** Every member, the lead too, gets the same folder
  check, on the run's folder `projects/team-<run>/`. Workers can only ask you
  questions. Only the lead can assign steps or finish.
- **Web text is information, not instructions.**
- **Every outside action goes through the approval gate.** Its rule decides: Auto (up to
  the daily cap), Ask or Never. Pause holds everything. Only you change rules, and only
  in Settings. Every call, refused ones too, is written to the append-only activity log.
- **Nothing runs twice by accident.** An action is logged "started" before it runs; if
  the app stops mid-run it is marked "unknown" and never re-run. An identical request
  within 24 hours is never run or queued again automatically. Approving content that
  already ran asks you first.

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
docs/REQUIREMENTS.md full specification and milestones
packs/sample/         sample pack (Milestone 8)
packs/routines/       routine automation pack (Milestone 9)
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
- Current result: 447 passed (417 offline, 30 live).

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
