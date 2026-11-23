---
name: Pipeline manager
version: 1.0
description: Keeps a queue of videos per channel repo on this PC, starts each repo's own pipeline one after another (or a few at once), watches it, and starts the next one when it finishes.
views: pipeline_tools.view_queue | Queue
row_actions: pipeline_tools.row_skip | Skip, pipeline_tools.row_retry | Retry, pipeline_tools.row_up | Up, pipeline_tools.row_down | Down, pipeline_tools.row_stop | Stop, pipeline_tools.row_pause | Pause channel, pipeline_tools.row_resume | Resume channel
---
Pack 6 (Milestone 14). Open the **Queue** from this page. Tell the **Pipeline manager** role in
chat what to queue, for example "add stories/abc_song.yaml and stories/ancient_night.yaml to
reelwright".

**Set up your channels (P-PIPE-1)** in Settings below, one block per channel repo:

```
[reelwright]
folder: D:\repos\video-gen-ai-agent
command: .venv\Scripts\python.exe main.py --input {input}
retry_command: .venv\Scripts\python.exe main.py --input {input} --resume
output: output\**\*.mp4
gpu: yes
parallel: 1
timeout:
quiet: 2h
retries: 1
```

- `command`: how this repo starts one video. It runs directly, with no shell, in `folder`.
  `{input}` is the queue item: letters, digits, spaces and `_ . @ + / \ -`, starting with a
  letter or digit, with no `..` step (so `stories/abc_song.yaml` works, `-x` or `C:\...` do
  not). For free text use `{input_file}`: the item's text is saved to a file and its path is
  passed. `{item}` is the queue number. A program with a folder in it (like
  `.venv\Scripts\python.exe`, or `.\render.bat` for a script in the repo itself) is taken from
  the repo folder; a bare name (`python`) only from PATH, never from the repo. The program must
  be a .exe, .com, .bat or .cmd file. With a .bat or .cmd program an item cannot have spaces.
- `retry_command` (optional): used when an item is tried again, for example with `--resume`.
- `output` (P-PIPE-2): the file a finished run leaves, relative to the folder (`*` and `**`
  allowed, `{input}` too). A run is done only when it exits with 0 **and** a matching file was
  written during the run that no other finished item already claimed. Use `{input}` in the
  name when you can: with a shared pattern like `output\**\*.mp4`, a half-written file left by
  a stopped run could still be picked up. Channels with `parallel` above 1 must name each
  run's file: `{input}` in a pattern with no `*` at all (`out\{input}.mp4`,
  `out\{input}_final.mp4`), or `{item}` with no `*` before it (`out\{item}_*.mp4`). Channels whose outputs land in the same folder must use
  `{item}` (the queue number), because the same item can be queued in both.
- `gpu` (P-PIPE-4): `yes` if any step uses the GPU. Only one GPU run goes at a time across all
  channels; `no` runs go in parallel up to "Most runs at once".
- `parallel`: how many items of this channel may run at once (default 1, in order).
- `timeout`: empty means no limit (a render may take 12 hours or more). Or a limit like `14h`.
- `quiet`: a notice when a run prints nothing for this long (default `2h`). It is never stopped
  for that. Empty or `none` switches the notice off.
- `retries`: a failed run is tried again this many times (default 1), then flagged to you.

**Approvals (P-PIPE-5):** every start goes through Approvals (Ask to begin with). The card shows
the exact program, arguments and folder, and is pinned to your channel set-up: change it and the
item is asked again. Only one item per free slot waits in Approvals at a time. When you trust a
channel's runs, set "pipeline: start run" to Auto in Settings.

**Long runs (P-PIPE-6):** each run is its own process, outside the hub. Closing or restarting
the hub, or pausing automation, does not stop it; the hub looks again when it is back and picks
up the result. Pausing automation (or a channel) only stops new starts. A run's output goes to
its log (the Log column). Use **Stop** on the Queue to end a run and everything it started.
One limit: if a program hands the work to a helper and that helper's parent exits, Stop can reach
the helper only when the program is a normal .exe or .bat. A venv `python.exe`, the `py`
launcher and a Microsoft Store Python move their children out of the hub's reach in that case.
For long GPU renders, prefer a python.org Python for the repo's venv, or check Task Manager after
a Stop.

**Rights:** a run executes your repo's code with your Windows rights. The hub decides which
command runs and when; it does not sandbox what that code does.
