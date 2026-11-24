# User guide

How to use the Personal AI Automation Hub day to day. Set-up and start-up are in the
[README](../README.md). The safety rules are in [SAFETY.md](SAFETY.md).

## What each part does

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
- **Social media manager pack** (install it on the Packs page): a trend analyst, a
  content writer, a publisher, an analytics reporter and a social lead (plus a "Post
  team" of lead, writer and publisher).
  - **Channels:** LinkedIn, through LinkedIn's official Posts API, and a **Demo channel**
    (writes to `data/outbox/social-demo.md`) to try everything without an account.
  - **Brand profile per channel** on the Packs page: niche, voice, audience and topics
    never to post about. A post that mentions a "never" topic is refused before it
    reaches Approvals.
  - **Two kinds of post per channel:** *Post* (any text, Ask) and *Announcement* (a title,
    one line and a link to one of the sites you list on the Packs page, put together by the
    connector in a fixed format with no tags or symbols; suggested Auto, installed as Ask). Approvals show exactly the text that will be posted. Switch the
    announcement rule to Auto in Settings as the first Auto step. The daily cap stops a
    runaway loop.
  - **Schedules** (installed switched off): daily trend report with links (08:00),
    weekly content calendar (Mondays 08:30), weekly analytics summary (Fridays 17:00). The
    summary covers what was posted, held or refused (engagement numbers come later, from each
    platform's statistics API), and the next calendar reads it.
  - **LinkedIn set-up:** your member URN (`urn:li:person:...`) and an access token with
    `w_member_social` from your LinkedIn developer app. The token goes straight into
    Windows Credential Manager: the Packs page only shows "set", and **Forget** removes it.
- **Job applications pack** (install it on the Packs page): a job scout, a resume writer,
  a fact checker, an application filler and a job lead (plus an "Application team" with
  the fact checker as its reviewer). Open the **Tracker** from the Packs page.
  - **Profile** in Settings: your master resume (a .md or .txt file), skills, target titles,
    locations, salary range, companies to exclude and the sources to search.
  - **The writer never invents:** it may only use your profile and resume, and the fact
    checker compares every claim with them. Nothing moves on without a pass.
  - **Mode per site** (`site: auto`, `site: prepare`, `site: skip`, one per line). Unknown
    sites are Prepare only, and LinkedIn always is (it bans bots).
  - **Prepare only** (the default): the job waits as "ready for you", with the tailored
    resume and letter in `data/pack-data/jobs/documents/`. You submit it.
  - **Auto** is only for jobs that take applications by email. The bot emails your letter
    and resume from your mailbox (server settings below; the password is kept in Windows
    Credential Manager). Every email goes through Approvals, starting as Ask, and shows
    exactly what will be sent. Review ten prepared applications before setting any site to
    Auto.
  - The bot never fills in web forms and never tries to get past a CAPTCHA. At a CAPTCHA,
    a login check, a legal declaration or a question your profile cannot answer, the job
    shows as "needs you" in the tracker. No job is added or applied to twice.
  - **Tracker buttons:** **Mark submitted** records a job you submitted yourself (applied
    date and a follow-up in a week). **Reopen** returns a job you dealt with after a
    hand-over, or an interrupted email that did not go out, so it can go on.
  - Every email approval carries a fingerprint of the exact documents: if they change
    after it was asked for, it is not sent. The approval shows the letter and the full
    resume.
  - Schedules (installed switched off): the daily scout (07:00) and the daily application
    round (09:00).
- **Software team pack** (install it on the Packs page): a manager, an architect, two
  developers, two testers and a reviewer. Give the **Software team** a task on the Teams page.
  - It works in the team's project folder under git, so every change is a commit you can
    read and undo. Developers build in their own branches; commits are signed by role.
  - The team only gets git actions for its own folder. **Running the tests** runs code the
    team wrote on your PC with your rights, so it goes through Approvals as Ask (keep it
    there). The tests run on a throwaway copy of the exact commit. **Pushing** is Ask.
    Installing software or touching anything outside the folder is not offered.
  - A branch is merged into main only when its exact latest commit passed its tests and was
    accepted by a reviewer who did not make that commit.
  - Test files are named `check_*.py`: the hub never lets an agent write `test_*.py` or
    `conftest.py`, so the hub's own tests never run agent code.
- **Video production pack** (install it on the Packs page): a script writer, a director, a
  producer and an editor. Give the **Video team** a one-line idea on the Teams page.
  - It needs ComfyUI running on this PC and FFmpeg. On the Packs page set the ComfyUI
    address (only a 127.0.0.1 or localhost address is accepted), the folder with your
    workflow templates and, if FFmpeg is not on PATH, the full path of ffmpeg.exe.
  - Templates are ComfyUI workflows saved in API format, with `{{prompt}}`, `{{negative}}`,
    `{{seed}}`, `{{length}}`, `{{width}}`, `{{height}}`, `{{steps}}`, `{{frames}}` or `{{fps}}`
    where the agents fill in values. The agents never build a new workflow. A template must
    save a video file (.mp4 or .webm), because images cannot be joined into the cut.
  - You approve the script first: the approval shows the whole SCRIPT.md, and nothing
    renders until you do. If the script changes after that, it needs approving again.
    Each approval stops the run: approve it, then run the team again.
  - Renders go through Approvals (Ask until you choose Auto), one at a time. A failed
    render is tried once more, then flagged. Clips are saved under renders/ in the
    project folder.
  - The editor joins the clips (with subtitles and music if there are any) into final/,
    and you approve the final cut. That writes social-draft.md, a draft post for the
    social media pack.
- **Pipeline manager pack** (install it on the Packs page): keeps a queue of videos per
  channel repo on this PC, starts each repo's own command, watches it, and starts the next
  item when it finishes. Ask the **Pipeline manager** role in chat to add items or say what
  is running, or open the **Queue** on the Packs page.
  - Set up each channel in the pack's Settings: folder, command (with `{input}` for the
    item), the output file a finished run leaves, `gpu: yes|no`, `parallel`, an optional
    `timeout` (none by default, renders can take 12 hours or more), `quiet` and `retries`.
    The pack page shows a full example.
  - Every start goes through Approvals and shows the exact command. Only one GPU run goes
    at a time; non-GPU runs go in parallel up to a cap.
  - A run is its own process: closing or restarting the hub does not stop it, and its
    result is picked up when the hub is back. A failed run is tried again once, then flagged.
- **Pack tables:** a pack can show read-only tables on the Packs page (the Tracker is
  the first).
- **Tell me when done:** a schedule with this ticked shows its result in the window
  (and as a desktop notice, if you allow it) when it finishes. Good for reminders.
- **Terminal chat** for quick use without the window.

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
| Platform logins (for example the LinkedIn token) | Windows Credential Manager, under `sapi-ai-hub` (never in a file) |
| Social demo channel output | `data/outbox/social-demo.md` |
| A pack's own data (for example the job tracker and the tailored documents) | `data/pack-data/<pack>/` |
| Pipeline queue, and each run's log and result files | `data/pack-data/pipeline/queue.db` and `data/pack-data/pipeline/runs/<item>-<ask>/` |
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
packs/<id>/pack.md          header: name, version, description, views and row_actions
                            ("<action id> | <label>, ...": read-only tables on the Packs page, and
                            your buttons per row: their actions must be marked ui_only, so they are
                            never offered to a role); below it, what the pack does
packs/<id>/roles/*.md       roles (same format as your own)
packs/<id>/teams/*.md       teams (a plain role id means this pack's role)
packs/<id>/connectors/*.py  connectors (modules that list ACTIONS, like hub/connectors/)
packs/<id>/rules.md         suggested rules, one per line: "<action id>: ask|never|auto [cap N]"
packs/<id>/settings.md      settings you fill in on the Packs page:
                            "<key> | folder|file|text|longtext|secret | <label>" (a secret goes to
                            Windows Credential Manager, never to a file)
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

A connector action can mark a field `{"hub": "folder"}` or `{"hub": "agent"}`: the hub fills
it with the agent's own working folder (inside `projects/`) or role name. The agent never
sees that field and cannot set it, and an edit at approval cannot change it.

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

## Known limits

- The chat folder location cannot be changed from the settings page yet.
- If a message fails before any reply arrives, that message is not kept in history
  (it goes back into the input box instead).
- In the terminal chat, Ctrl+C pressed while Claude is still starting is ignored.
- There are no automated browser tests for the window in the test suite yet (smoke
  runs in a real browser are done by hand for each milestone).
