# Personal AI Automation Hub

A personal chat assistant and automation hub that runs on your own Windows PC and
uses your **Claude subscription** through the Claude Agent SDK. No Anthropic API key,
no cloud server, one user.

All documentation is in [docs/](docs/), listed under [Documentation](#documentation).

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
| | 10. Social media manager | Ready to test (a real LinkedIn post needs your account) |
| | 11. Job applications | Ready to test (your ten reviews before any Auto are still to do) |
| | 12. Software team | Ready to test |
| | 13. Video production | Ready to test (a real render needs ComfyUI and FFmpeg) |
| | 14. Pipeline manager | Ready to test (set up your channel repos on the Packs page) |
| | 15. Models, connectors and chat set-up | Ready to test (the job pack's mail connector needs a mail connector on claude.ai) |

## What it does

- **Chat** in your browser with personalities, the models your plan offers (with effort), web search and saved history.
- **Connectors:** reuse the connectors on your claude.ai account (Gmail, Drive and others once connected there).
- **Roles and teams:** agents with only the tools you give them, working alone or as a team in their own folder.
- **Approvals:** every action outside the app follows your rule (Auto, Ask or Never) and is written to an activity log.
- **Schedules:** roles and teams can run tasks on a timer, when files arrive, or after another task.
- **Packs** (set up mostly in chat) add a type of work: routines, social media, job applications, a software team, video production, and a pipeline manager for your video repos.

Details are in the [user guide](docs/USER_GUIDE.md).

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

## Documentation

| Document | What is in it |
|---|---|
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | What each part does (chat, roles, teams, schedules, packs), using the window, where data is stored, known limits |
| [docs/SAFETY.md](docs/SAFETY.md) | The safety rules built in |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | The full specification and milestones. Ticked boxes and ✅ marks show what is built and tested. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the hub is built: components, the approval gate, packs, pack services, data, security model |
| [docs/PIPELINE_MANAGER.md](docs/PIPELINE_MANAGER.md) | Design of the pipeline manager pack (Milestone 14) |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Project layout, tests, working rules for development |

Each pack also explains itself on the Packs page (its `pack.md`).
