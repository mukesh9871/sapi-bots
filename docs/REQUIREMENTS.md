# Personal AI Automation Hub: Requirements

Nov 11, 2026 · @Mukesh Kumar

## Overview and goals

Build a personal AI automation hub that runs on my PC on my Claude subscription. A Grok-style chat assistant sits at the centre, a generic engine for agents, teams, schedules and approvals sits around it, and instruction packs add job applications, social media, routines, software projects and video production.

**Goals**

1. A fast chat window with a configurable personality (witty, blunt, or plain), local history and web search.
2. Uses only my Claude subscription through the Claude Agent SDK, signed in with my Claude account. No Anthropic API key.
3. A generic platform built first: roles, teams, tools, schedules, approvals, logs and budgets.
4. Instruction packs on top of it: job applications, social media manager, routine automation, a software team, and video production with ComfyUI on my RTX 5090.
5. Anything done on my behalf follows a rule I set (automatic, needs my approval, or never) and is logged.
6. Everything runs and is stored on my own machine.

**Primary user:** me, on a single Windows PC (Mac or Linux support is a bonus, not a requirement).

## Scope

The work comes in three phases: the core chat app, then the generic platform, then instruction packs one at a time.

| Phase | Delivers | Not included |
| --- | --- | --- |
| 1. Core chat app | Streaming chat, personalities, local history, web search | Voice, image and file attachments (later) |
| 2. Generic platform | Roles, teams, tools and connectors, approvals inbox, scheduler, activity log, budgets, pack format | Running while the PC is off; cloud hosting; more than one user |
| 3. Instruction packs | Job applications, social media manager, routine automation, software team, video production | Getting around CAPTCHAs or platform rules; automated likes, follows or comments; posting by imitating a browser |
| All phases | Subscription only, local, single user, every outside action ruled and logged | Anthropic API key billing; sharing the app with others |

## Key constraint: subscription only, no API key

The app must run entirely on my Claude subscription by using the Claude Agent SDK while signed in with my Claude account. Per Anthropic's help center, when signed in with a Claude plan, the Agent SDK draws from subscription limits ([source](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)). A paid subscription does not include general API access ([source](https://support.claude.com/en/articles/9876003-i-have-a-paid-claude-ai-plan-pro-max-team-or-enterprise-plans-why-do-i-have-to-pay-separately-to-use-the-claude-api-and-console)).

**Requirements**

1. **R-AUTH-1:** ✅ The app authenticates only through the Claude Code login (`claude`, then sign in with "Claude account with subscription").
2. **R-AUTH-2:** ✅ The app never asks for, stores or uses an Anthropic API key.
3. **R-AUTH-3:** ✅ On startup, the app checks that the `ANTHROPIC_API_KEY` environment variable is not set. If it is set, the app removes it from its own process before starting the SDK and shows a notice, because the key would switch billing to the API. The same check covers the Bedrock and Vertex switches.
4. **R-AUTH-4:** ✅ If the user is not logged in, show a clear message with the steps to log in, instead of a raw error.
5. **R-AUTH-5:** ✅ The app does not extract, copy or reuse the login token outside the official SDK.
6. **R-AUTH-6:** ✅ When the subscription usage limit is reached, show a friendly message with the time it resets (if available), and keep the typed message so it is not lost.
7. **R-AUTH-7:** The app is for one person, me. It is never shared, hosted or handed to others with my login: Anthropic does not allow developers to offer claude.ai login or rate limits to other people's products unless approved ([source](https://code.claude.com/docs/en/agent-sdk/overview)).
8. **R-AUTH-8:** ✅ On startup the app confirms that the session is using the subscription login and not a key, where the SDK reports it, and shows the result in the status line.
9. **R-AUTH-9:** Platform connectors (YouTube, Instagram, X, job sites) sign in with those platforms' own keys or logins. These are not Anthropic keys, they are billed (if at all) by that platform, and they are stored as N-SEC-6 requires.
10. **R-AUTH-10:** Teams and scheduled runs use the same subscription limits as chat. Budgets (G-BUDGET-1) and the pause switch (G-BUDGET-2) keep them from using the whole limit.

## Core chat app: functional requirements

The app must let me chat, pick a personality, keep history, and search the web; every item below is version 1 unless marked Later.

**Chat**

1. **F-CHAT-1:** ✅ Type a message and get a reply that streams in word by word.
2. **F-CHAT-2:** ✅ Keep context within a conversation, so follow-up questions work.
3. **F-CHAT-3:** ✅ Show replies with Markdown formatting: headings, lists, tables, and code blocks with syntax highlighting and a Copy button.
4. **F-CHAT-4:** ✅ A Stop button to cancel a reply that is still generating.
5. **F-CHAT-5:** (Later) Regenerate the last reply, and edit then resend my last message. Both need the session rewound by one turn, so they are not in version 1.
6. **F-CHAT-6:** ✅ Choose the model (for example a faster model for quick chat, a stronger model for hard questions), limited to what my plan allows.

**Personality**

1. **F-PERS-1:** ✅ Built-in modes: "Fun" (witty, sarcastic, jokes), "Straight" (direct, no jokes), and "Explainer" (patient, step by step).
2. **F-PERS-2:** ✅ A custom mode where I write my own system prompt, saved for reuse.
3. **F-PERS-3:** ✅ Switch modes at any time; the new mode applies from the next message.
4. **F-PERS-4:** ✅ I can give the bot a custom name and avatar.

**History**

1. **F-HIST-1:** ✅ Every conversation is saved automatically on my PC.
2. **F-HIST-2:** ✅ A sidebar lists past chats, newest first, titled from the first words of my first message (no extra Claude call).
3. **F-HIST-3:** ✅ Rename, delete, and search chats by keyword.
4. **F-HIST-4:** ✅ Reopen an old chat and continue it with its context. The app stores the SDK session ID for each chat and resumes it; if that session file is gone, it replays the saved messages into a new session.
5. **F-HIST-5:** ✅ Export a chat to Markdown.

**Web search**

1. **F-WEB-1:** ✅ The bot can search the web and read pages when a question needs current information.
2. **F-WEB-2:** ✅ Replies that use the web show the source links.
3. **F-WEB-3:** ✅ A toggle to turn web access off.

**Later**

1. **F-LATER-1:** Attach images and documents (PDF, text) for the bot to read.
2. **F-LATER-2:** Voice input (speech-to-text) and spoken replies (text-to-speech).
3. **F-LATER-3:** Long-term memory of facts about me, which I can view and delete.
4. **F-LATER-4:** Let the bot read files in one folder I choose, read-only.
5. **F-LATER-5:** Open the app from my phone on my home Wi-Fi.

## Generic platform (build first)

The platform is one general engine: agents with roles, teams, tools, schedules and approvals. It knows nothing about jobs, social media or video; those arrive later as instruction packs.

**Agents and roles**

1. **G-AGENT-1:** ✅ An agent is a role definition: name, job description (its instructions), the tools it may use, and the model it runs on.
2. **G-AGENT-2:** ✅ I can create, edit, copy and delete roles from the app, with no coding. Roles are stored as readable files.
3. **G-AGENT-3:** ✅ Any single agent can be given a task directly from the chat window.

**Teams**

1. **G-TEAM-1:** A team is a set of roles plus a workflow: who starts, who hands work to whom, who reviews, and who reports to me.
2. **G-TEAM-2:** I can assemble a team for a task or project by picking roles (for example architect, manager, developer 1 and 2, tester 1 and 2, reviewer), and save it as a template.
3. **G-TEAM-3:** One lead role (manager, producer) breaks the task into steps, assigns them, and collects results.
4. **G-TEAM-4:** A team board shows each member's current step, output so far, and status (waiting, working, blocked, done).
5. **G-TEAM-5:** I can pause, stop or redirect a team at any time, and answer questions a member raises.
6. **G-TEAM-6:** Each team works in its own project folder; members see only that folder.

**Tools and connectors**

1. **G-TOOL-1:** ✅ A tool is one capability an agent can call: web search, read or write files in a project folder, post to a platform, fill a web form, start a ComfyUI render.
2. **G-TOOL-2:** ✅ Each tool is marked read-only or acting. Acting tools change something outside the app (post, submit, send, delete).
3. **G-TOOL-3:** ✅ A role gets only the tools listed in its definition; everything else is unavailable to it.
4. **G-TOOL-4:** New connectors can be added without changing the engine.

**Approvals**

1. **G-APPR-1:** Every acting tool call follows a rule I set per pack, per platform and per action type: Auto (do it and log it), Ask (hold until I approve), or Never.
2. **G-APPR-2:** Held items wait in an approvals inbox showing exactly what will be done (the post text and media, the filled application) with Approve, Edit and Reject.
3. **G-APPR-3:** New rules default to Ask. I switch a rule to Auto only by my own choice in Settings.
4. **G-APPR-4:** A daily cap per Auto rule (for example 3 posts a day per platform, 10 applications a day) stops runaway loops.

**Schedules and routines**

1. **G-SCHED-1:** Any agent or team task can run on a schedule (daily, weekly, custom) or on a trigger (new file in a folder, a finished render).
2. **G-SCHED-2:** Schedules run only while the PC and app are on; missed runs are listed, and I choose whether to run them late.

**Log and budgets**

1. **G-LOG-1:** An activity log records every acting tool call: time, agent, what was done, where, and whether it was Auto or approved by me.
2. **G-BUDGET-1:** Each task and each team has a budget (maximum turns and maximum agents working at once). The run stops and reports when the budget is reached.
3. **G-BUDGET-2:** A global switch pauses all automation, so chatting stays available when my subscription limit is nearly used.

**Instruction packs**

1. **G-PACK-1:** A pack is a folder that bundles roles, a team template, the connectors it needs, default approval rules and default schedules for one type of work.
2. **G-PACK-2:** Installing, switching off or removing a pack never changes the engine or other packs.

## Instruction packs (specific types)

Each pack adds roles, connectors and default rules on top of the generic platform. Five packs are planned; more can follow the same pattern.

### Pack 1: Job applications

Roles: job scout, resume writer, application filler, tracker.

1. **P-JOB-1:** A profile holds my master resume, skills, target titles, locations, salary range, and companies to exclude.
2. **P-JOB-2:** The scout searches the sources I choose each day, scores each job against my profile, and shortlists the matches.
3. **P-JOB-3:** The writer tailors my resume and cover letter to each job. It never invents experience, skills or qualifications.
4. **P-JOB-4:** Submission is fully automatic where the site allows it (for example applications by email or through a site's own apply API). Where a site forbids automation, the bot prepares everything and I submit. LinkedIn is in this group: it bans bots and automated activity ([source](https://www.linkedin.com/help/linkedin/answer/a1341387)).
5. **P-JOB-5:** A per-site list records which mode applies: Auto, Prepare only, or Skip. Unknown sites start as Prepare only.
6. **P-JOB-6:** The bot stops and hands over to me at any CAPTCHA, login check, legal declaration, or question it cannot answer from my profile. It never tries to get around a CAPTCHA.
7. **P-JOB-7:** A tracker lists every job with status, date applied, documents sent and follow-up date. No job is applied to twice.

### Pack 2: Social media manager

Roles: trend analyst, content writer, publisher, analytics reporter.

1. **P-SOC-1:** A brand profile per channel holds niche, voice, audience, and topics never to post about.
2. **P-SOC-2:** A daily trend report lists rising topics in my niche for each platform, with links.
3. **P-SOC-3:** A weekly content calendar proposes what to post, where and when.
4. **P-SOC-4:** The writer drafts posts, captions, titles, descriptions and hashtags, fitted to each platform's format.
5. **P-SOC-5:** Approval rules by post type. Suggested defaults: Auto for routine posts I have defined (announcing my own new video, scheduled series posts); Ask for anything else, including replies to people, opinions, and any post naming a person or brand.
6. **P-SOC-6:** Posting goes only through each platform's official API, never by imitating a browser.
7. **P-SOC-7:** A weekly analytics summary shows what worked and adjusts the next calendar.
8. **P-SOC-8:** Posts follow each platform's rules for labelling AI-generated media.

| Platform | How the bot posts | What to know |
| --- | --- | --- |
| YouTube | YouTube Data API | Uploads from an unaudited API project are locked to private until Google audits the project, so the bot uploads as private and I publish, or I apply for the audit ([source](https://developers.google.com/youtube/v3/docs/videos/insert)) |
| Instagram | Instagram content publishing API | Needs a professional (business or creator) account; 100 API posts per 24 hours ([source](https://developers.facebook.com/docs/instagram-platform/content-publishing/)) |
| X | X API | Pay-per-use with no free tier, reported at about $0.015 per post and $0.20 per post with a link; billed by X, separate from my Claude subscription ([source](https://posteverywhere.ai/blog/x-twitter-api-pricing)) |
| Others | Added as connectors later | Each platform's automation rules are checked before its connector is built |

### Pack 3: Routine automation

1. **P-ROUT-1:** I describe a repeated activity in plain words; the bot turns it into a routine with steps, a schedule and an approval rule.
2. **P-ROUT-2:** Starter routines: morning briefing, weekly analytics summary, reminders, tidying one folder I choose.
3. **P-ROUT-3:** Each routine shows its last run, result and next run.

### Pack 4: Software team

Roles: architect, manager, developer 1 and 2, tester 1 and 2, reviewer.

1. **P-DEV-1:** Workflow: the manager plans, the architect designs, developers build in separate branches, testers write and run tests, the reviewer reviews, the manager reports to me.
2. **P-DEV-2:** The team works inside one project folder under Git, so every change is a commit I can read and undo.
3. **P-DEV-3:** Commands run only inside the project folder. Pushing to a remote, installing system software, or touching anything outside the folder is Ask.
4. **P-DEV-4:** No developer approves their own work; a change is done only when tests pass and the reviewer accepts it.

### Pack 5: Video production (ComfyUI on the RTX 5090)

Roles: script writer, director, producer, editor.

1. **P-VID-1:** Workflow: the writer produces a script and shot list; the director turns each shot into prompts and settings; the producer queues renders and tracks progress; the editor assembles clips, audio and subtitles. I approve the script and the final cut.
2. **P-VID-2:** Rendering is done by ComfyUI on my laptop through its local API. Claude agents write prompts and manage the queue; the GPU work never leaves my machine.
3. **P-VID-3:** Agents use workflow templates I have saved in ComfyUI and fill in the parameters (prompt, seed, length, resolution). They do not build new node graphs in version 1.
4. **P-VID-4:** One render at a time on the GPU. A failed render is retried once, then flagged to me.
5. **P-VID-5:** Outputs are saved in the project folder. A finished video can be passed to the social media pack as a draft post.
6. **P-VID-6:** No likeness of a real person and no copyrighted character without my confirmation that I have the rights.

## Non-functional requirements

The app should start quickly, stay private on my PC, and fail with clear messages rather than crashes.

| ID | Area | Requirement |
| --- | --- | --- |
| N-PERF-1 | Speed | App window opens in under 5 seconds on a normal PC. |
| N-PERF-2 | Speed | The first words of a reply start streaming within a few seconds (depends on Claude's servers). |
| N-REL-1 ✅ | Reliability | Network errors, timeouts and usage-limit messages are shown in plain words, with a Retry button. |
| N-REL-2 ✅ | Reliability | A crash or closed window never loses saved chat history. |
| N-SEC-1 ✅ | Security | The app listens only on `127.0.0.1` (localhost), never on the network, unless I turn on phone access later. |
| N-SEC-2 | Security | No telemetry or analytics sent anywhere except the Claude requests themselves. |
| N-SEC-3 ✅ | Security | In plain chat, the bot's available tool set is only web search and web fetch; a role in a pack gets only the tools its definition lists (G-TOOL-3). Tools a role does not list (in plain chat: shell, file-write and file-edit) are removed from the set and also blocked by deny rules, because an allow-list alone only pre-approves tools. Permission bypass mode is never used. |
| N-USE-1 ✅ | Usability | Works with mouse and keyboard; Enter sends, Shift+Enter makes a new line. |
| N-USE-2 ✅ | Usability | Light and dark theme, following the system setting. |
| N-MAINT-1 ✅ | Maintainability | Settings (personality, model, toggles) live in one readable config file. |
| N-MAINT-2 | Maintainability | Code is small and commented, so I can change it myself. |
| N-PORT-1 | Portability | Runs on Windows 10 or 11; ideally also on macOS and Linux with no code changes. |
| N-SEC-4 ✅ | Security | The local server accepts requests only from its own window: a random token created at each launch, plus Origin and Host checks, so a website open in my browser cannot talk to the bot. |
| N-SEC-5 ✅ | Security | Chat runs in its own empty working folder, each team in its own project folder, and neither loads my Claude Code settings, memory files or skills, so my coding setup cannot change the bot and the bot cannot see my projects. |
| N-PERF-3 ✅ | Speed | Only the open chat keeps a live Claude Code process; idle ones are closed, and reopening a chat resumes its session. |
| N-SEC-6 | Security | Platform logins and keys are kept in the operating system's credential store (Windows Credential Manager), never in config files, logs or chat, and are never shown to an agent; connectors use them on the agent's behalf. |
| N-SEC-7 | Security | Text that an agent reads from a web page, job listing, comment or file is treated as information, never as an instruction; an acting tool call still has to pass its approval rule. |
| N-REL-3 | Reliability | An acting tool call is never repeated blindly after a crash or retry: the log is checked first, so nothing is posted or submitted twice. |

## Architecture and tech stack

A local hub server on my PC runs everything: it serves the chat window, runs agents and teams through the Claude Agent SDK, and puts every outside action through the approval gate before a connector carries it out.

&#91;embedded content: hub architecture · window, engine, approval gate, four connector types\]

Agents never reach a platform directly: a tool call goes to the approval gate, which runs it, holds it for me, or refuses it, and writes the outcome to the log.

| Part | Choice | Why |
| --- | --- | --- |
| Language | Python 3.11 or newer | Official Agent SDK support; easy to read and change |
| Claude access | `claude-agent-sdk` (Python) | The supported way to use Claude with a subscription login |
| Login | Claude Code CLI, logged in once | Holds the subscription login so the app never handles it; the SDK ships its own copy of Claude Code and uses the same login |
| Server | FastAPI with Uvicorn, bound to 127.0.0.1 | Simple, supports streaming replies to the window |
| Interface | HTML, CSS, JavaScript; optional pywebview for a native-looking window | No build tools needed; opens in any browser |
| Storage | SQLite (built into Python) plus one JSON config file | No database server to install |
| Launch | A `start.bat` script and desktop shortcut | One click to start |
| Agents and teams | One Agent SDK session per role, coordinated by the hub; SDK subagents for short helper tasks | Each role keeps its own instructions, tools and history; the hub controls hand-offs and budgets |
| Connectors | Custom tools written in Python and offered to agents through the SDK's in-process tool servers | The approval gate and credentials live in my code, outside the agent's reach |
| Scheduler | APScheduler inside the hub server | No separate service to install; schedules stored in SQLite |
| Web forms | Playwright, only on sites marked Auto or Prepare only | Fills application forms in a visible browser so I can watch and take over |
| Video | ComfyUI local API for rendering; FFmpeg for assembly | Uses the RTX 5090 and the workflows I already have |
| Secrets | Python keyring library over Windows Credential Manager | Keeps platform logins out of files and out of agent context |

## User interface

One window with a chat list on the left, the conversation in the middle, and settings behind a gear icon.

1. **Left sidebar:** "New chat" button, search box, list of past chats (title and date), right-click to rename or delete.
2. **Top bar:** current chat title, personality selector (Fun, Straight, Explainer, Custom), model selector, web search toggle.
3. **Chat area:** my messages on the right, the bot's on the left with its name and avatar; Markdown rendering; Copy button on each reply and each code block; source links under web-based answers.
4. **Input box:** grows with text; Send and Stop buttons; shows "thinking" or "searching the web" while working.
5. **Status line:** shows "Signed in with Claude subscription" in green, or a red warning if not signed in or if an API key is detected.
6. **Settings page:** edit personality prompts, bot name and avatar, default model, theme, and the folder where chats are saved.
7. **Launch:** a desktop shortcut that starts the app and opens its window (or a browser tab at `http://127.0.0.1:<port>`).
8. **Approvals inbox:** every held action with a full preview (post text and media, filled application), and Approve, Edit, Reject buttons; a badge shows how many are waiting.
9. **Teams board:** one column per team member with current step, status and latest output; Pause, Stop and Message buttons.
10. **Roles and packs page:** create and edit roles, assemble teams, switch packs on or off, set each pack's approval rules and daily caps.
11. **Schedule page:** all routines with next run, last result and an on/off switch.
12. **Activity log:** searchable list of everything done on my behalf, filterable by pack, platform and date.
13. **Usage bar:** shows automation paused or running, and each running task's budget used.

## Data storage and privacy

App data stays in one local folder on my PC, plus a transcript of each chat that the SDK itself writes under my user folder; only my messages go to Claude to get replies.

| Data | Where it lives | Format | Notes |
| --- | --- | --- | --- |
| Chat history | `<app folder>/data/chats.db` | SQLite database | One row per message; searchable |
| Settings and personalities | `<app folder>/config.json` | JSON | Human-readable, easy to back up |
| Exports | `<app folder>/exports/` | Markdown files | Created only when I export |
| Claude login | Managed by Claude Code itself | Not touched by the app | The app never reads or copies it |
| SDK session transcripts | \~/.claude/projects/\<the app's working folder>/ | JSONL files, written by the SDK | A second copy of each chat, used to resume it; the app deletes it when I delete the chat |
| Approvals, activity log, schedules, job tracker, content calendar | \<app folder>/data/hub.db | SQLite database | The activity log is append-only; entries are never edited |
| Roles, teams and packs | \<app folder>/packs/ | Readable text files, one folder per pack | Easy to copy, edit and back up |
| Team project folders and video renders | \<app folder>/projects/\<project name>/ | Ordinary files; Git for software projects | Video files are large; the folder location can be changed to another drive |
| Platform logins and keys | Windows Credential Manager | Managed by the operating system | Not part of the app folder backup; I reconnect each platform on a new PC |
| My resume and job profile | \<app folder>/packs/jobs/profile/ | Documents I provide | Sent only to Claude for tailoring and to the job sites I apply to |

1. **D-1:** No chat data leaves the PC except the messages sent to Claude to get replies.
2. **D-2:** ✅ Deleting a chat removes it from the database and deletes its SDK session transcript.
3. **D-3:** ✅ A "Delete all history" option in Settings, with a confirmation step.
4. **D-4:** ✅ Backing up the app folder backs up every chat. On a new PC, restored chats continue by replaying their saved messages (F-HIST-4).
5. **D-5:** ✅ The app's database is the source of truth for history; SDK transcripts are only a resume aid ([where the SDK stores sessions](https://code.claude.com/docs/en/agent-sdk/sessions)).

## Risks and limitations

The biggest risks are subscription limits under team and scheduled use, platform rules on automation, and mistakes made by automatic actions.

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Shared usage limits | The app uses the same limits as claude.ai and Claude Code, so heavy use can block all three until the limit resets | Show a clear limit message; prefer a faster model for casual chat |
| Policy change | Anthropic has changed Agent SDK billing plans before (a planned change was paused on June 15, 2026), so subscription use could change again | Keep the Claude call in one module so it can be switched later; check the help article from time to time |
| Accidental API billing | If `ANTHROPIC_API_KEY` is set, usage moves to API billing | Startup check from R-AUTH-3 |
| Agent tools acting on the PC | The SDK includes tools that can run commands or edit files | Each role gets only the tools it lists; plain chat gets only web search and web fetch (N-SEC-3) |
| Terms of use | Letting other people use the app through my personal subscription may break Anthropic's consumer terms | Personal use only; localhost only |
| Personality going too far | A "sarcastic" prompt can turn rude or unhelpful | Prompt asks for humor that stays helpful; easy to edit in Settings |
| Hidden instructions in web pages | A page the bot reads can contain text that tries to steer it, for example to send chat content to another site | Web-only tool set (N-SEC-3) keeps the damage small; review this again before adding local file access (F-LATER-4) |
| Agent overhead | Each message also carries the agent's instructions and tool definitions, so a chat here may use more of the limit than the same chat on claude.ai | Keep the tool set minimal; measure usage during Milestone 1 before building the rest |
| Teams and schedules exhaust the limit | A seven-role team or frequent routines can use a whole usage window, leaving nothing for chat; subscriptions are meant for ordinary individual use | Budgets per task, few agents working at once, lighter models for simple roles, the pause switch; consider a Max plan before running teams daily |
| Platform account restricted | Job sites and social platforms can restrict accounts that break their automation rules | Official APIs only; per-site Auto or Prepare-only list; daily caps; no automated likes, follows or comments |
| Wrong automatic action | An Auto rule posts something off-brand or applies to a poor-fit job, in my name, in public | Rules default to Ask; I move a rule to Auto only after watching it work; daily caps; activity log; one-click pause |
| Inaccurate application content | A tailored resume overstates my experience, which can cost a job offer later | P-JOB-3 forbids invented facts; tailored documents are built only from my master resume; spot-check Auto applications weekly |
| Platform costs and gates | X charges per post; YouTube keeps API uploads private until an audit; Instagram needs a professional account | Listed in the platform table under Pack 2; decide per platform before building its connector |
| Team runs in circles | Agents hand work back and forth without finishing, using up the limit | Turn budget per task; a lead role that must report; the run stops and asks me when the budget is reached |

## Milestones and acceptance criteria

A ✅ next to a requirement ID, or a ticked box below, means it is built and tested.

Build in three phases, each usable on its own. Phase 1 is Milestones 1 to 4 (the chat app), Phase 2 is Milestones 5 to 8 (the generic platform), and Phase 3 adds one pack per milestone.

**Milestone 1: Working chat in the terminal**

- [x] Claude Code installed and logged in with my subscription; `/status` shows the plan, not "API".
- [x] A script sends a message through the Agent SDK and prints a streamed reply.
- [x] Multi-turn context works (a follow-up question is answered correctly).
- [x] The startup check removes the key and shows a notice when `ANTHROPIC_API_KEY` is set.
- [x] Asking the bot to run a command or write a file fails, because those tools are not available.
- [x] A 20-message test chat shows how much of my usage limit it consumes.

**Milestone 2: Desktop chat window**

- [x] Chat window with streaming replies, Markdown, Copy and Stop buttons.
- [x] Personality selector with the three built-in modes and a custom prompt.
- [x] Status line shows the sign-in state.
- [x] A request to the local server without the launch token is rejected.

**Milestone 3: History and settings**

- [x] Chats saved locally, listed in the sidebar, searchable, renamable and deletable.
- [x] Closing and reopening the app keeps all chats; an old chat can be continued.
- [x] Settings page saves to `config.json`.
- [x] Deleting a chat also removes its SDK transcript from disk.

**Milestone 4: Web search and polish**

- [x] Web search toggle works, and web-based answers show source links.
- [x] Usage-limit and network errors show friendly messages with Retry.
- [x] Desktop shortcut launches the app in one click.

**Milestone 5: Roles and single agents**

- [x] I create a role in the app, give it a task from chat, and get its result.
- [x] A role can use only the tools its definition lists.

**Milestone 6: Approvals, log and budgets**

- [ ] A test acting tool is held in the inbox under Ask, runs under Auto, and is blocked under Never.
- [ ] Every acting call appears in the activity log, and a daily cap stops further Auto runs.
- [ ] A task stops and reports when its budget is reached; the pause switch halts all automation.

**Milestone 7: Teams**

- [ ] A three-role test team (lead, worker, reviewer) finishes a small task, with the board showing each member's status.
- [ ] I can pause, stop and redirect the team mid-task.

**Milestone 8: Scheduler and pack format**

- [ ] A scheduled task runs at its time; a missed run is listed.
- [ ] A sample pack installs and removes without any change to the engine.

**Milestone 9: Routine automation pack**

- [ ] A routine described in plain words runs on schedule for a week.

**Milestone 10: Social media manager pack**

- [ ] Daily trend report and weekly calendar arrive on schedule.
- [ ] One platform posts through its official API under Ask, then one routine post type under Auto; other platforms follow one at a time.

**Milestone 11: Job applications pack**

- [ ] Ten prepared applications reviewed by me with no invented facts, before any site is set to Auto.
- [ ] The tracker shows every application, and the bot hands over at a CAPTCHA.

**Milestone 12: Software team pack**

- [ ] The full team delivers a small tested program in a Git project folder, within budget.

**Milestone 13: Video production pack**

- [ ] The team produces a short video from a one-line idea: script approved by me, clips rendered by ComfyUI, final cut assembled.

**Done means:** each phase is used daily for a week with no lost data, no charge on an Anthropic API account, no action taken outside my approval rules, and no crashes. The order of packs in Phase 3 can change.
