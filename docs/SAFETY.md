# Safety rules built in

What the hub does to keep your subscription, your PC and your accounts safe. The design
behind these rules is in [ARCHITECTURE.md](ARCHITECTURE.md) (security model).

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
- **An agent never chooses what program runs.** The pipeline manager runs only the commands
  you typed into its settings, with no shell. Agents can only add checked items (safe
  characters, never an option) to the queue, and every start goes through Approvals with
  the exact command shown. A run executes your repo's code with your Windows rights: the hub
  decides what runs and when, but does not sandbox it.
- **Every outside action goes through the approval gate.** Its rule decides: Auto (up to
  the daily cap), Ask or Never. Pause holds everything. Only you change rules, and only
  in Settings. Every call, refused ones too, is written to the append-only activity log.
- **Nothing runs twice by accident.** An action is logged "started" before it runs; if
  the app stops mid-run it is marked "unknown" and never re-run. An identical request
  within 24 hours is never run or queued again automatically. Approving content that
  already ran asks you first.
