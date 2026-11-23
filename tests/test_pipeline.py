"""Milestone 14 offline checks: the pipeline manager pack (P-PIPE-1..7) and pack services (G-PACK-3).

No Claude calls and no real repos: a fake channel repo (make.py) succeeds, fails, hangs, or exits
without leaving its output, depending on the item text. Runs are real detached processes.
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hub import connectors
from hub.engine import packs as packs_mod
from hub.engine import roles, services
from hub.engine.packs import PACKS_DIR, PackStore, install, read_pack
from hub.engine.services import PackServices
from hub.gate.gate import Gate
from hub.gate.store import GateStore
from hub.scheduler.schedules import ScheduleStore

MAKE = r'''
import sys, time, pathlib, os
item = sys.argv[1] if len(sys.argv) > 1 else ""
if item.endswith(".txt"):  # {input_file}
    item = pathlib.Path(item).read_text(encoding="utf-8").strip()
log = pathlib.Path("calls.log")
with log.open("a", encoding="utf-8") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
print("working on", item, flush=True)
if item.startswith("fail"):
    sys.exit(3)
if item.startswith("hang"):
    if "child" in item:  # leaves a grandchild that must be stopped too
        import subprocess
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
        pathlib.Path("child.pid").write_text(str(p.pid))
    time.sleep(300)
if item.startswith("slow"):
    time.sleep(1.5)
if not item.startswith("noout"):
    out = pathlib.Path("out"); out.mkdir(exist_ok=True)
    name = item if "/" not in item else item.replace("/", "_")
    name = "".join(ch for ch in name if ch.isalnum() or ch in "._ -")[:40]
    (out / (name + ".mp4")).write_bytes(b"video")
'''


def block(name, folder, gpu="yes", parallel=1, extra=""):
    return (f"[{name}]\nfolder: {folder}\ncommand: \"{sys.executable}\" make.py {{input}}\n"
            f"output: out\\{{input}}.mp4\ngpu: {gpu}\nparallel: {parallel}\n{extra}\n")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")
    root = tmp_path / "packs"
    shutil.copytree(PACKS_DIR / "pipeline", root / "pipeline")
    ps = PackStore(root, tmp_path / "hub.db")
    monkeypatch.setattr(connectors, "pack_dirs", ps.enabled_dirs)
    monkeypatch.setattr(packs_mod, "active", ps)
    gate = Gate(GateStore(tmp_path / "hub.db"), paused=lambda: False)
    sched = ScheduleStore(tmp_path / "hub.db")
    install(ps, gate, sched, "pipeline")
    repos = {}
    for name in ("alpha", "beta", "gamma"):
        repo = tmp_path / "repos" / name
        repo.mkdir(parents=True)
        (repo / "make.py").write_text(MAKE, encoding="utf-8")
        repos[name] = repo
    state = {"paused": False, "notices": []}
    ctx = PackServices(gate, notify=lambda t, x, f: state["notices"].append((t, x, f)),
                       paused=lambda: state["paused"]).context("pipeline")  # the same context the app gives

    class Env:
        pass

    e = Env()
    e.tmp, e.ps, e.gate, e.sched, e.repos, e.state, e.ctx = tmp_path, ps, gate, sched, repos, state, ctx
    e.set = lambda text, **kw: ps.set_settings("pipeline", {"channels": text, **kw})
    P = tools().P
    yield e
    for row in P.db().execute("SELECT pid, created FROM items WHERE state IN ('running', 'launching')"):
        P.kill(row[0], row[1])  # never leave a test process behind


def tools():
    return sys.modules[connectors.get("pipeline_tools.queue").run.__module__]


def call(action, **fields):
    act = connectors.get(f"pipeline_tools.{action}")
    return act.run(act.clean(fields))


def item(n):
    return dict(tools().P.db().execute("SELECT * FROM items WHERE id = ?", (n,)).fetchone())


def tick(e):
    tools().TICK(e.ctx)


def wait(e, cond, secs=30):
    end = time.time() + secs
    while time.time() < end:
        tick(e)
        if cond():
            return
        time.sleep(0.2)
    raise AssertionError("timed out: " + tools().queue({}))


def pending(e):
    return [a for a in e.gate.store.approvals("pending") if a["action_id"] == "pipeline_tools.start_run"]


def approve_all(e):
    for a in pending(e):
        e.gate.approve(a["id"])


def auto(e):
    e.gate.store.set_rule("pipeline", "pipeline", "start_run", "auto", 1000)


# ---- pack and platform ---------------------------------------------------------------------------

def test_pack_reads_clean_and_installs_ask(env):  # P-PIPE-5, P-PIPE-7, G-PACK-3
    pk = read_pack("pipeline", PACKS_DIR / "pipeline")
    assert not pk.problem and pk.views and len(pk.row_actions) == 7
    assert env.gate.store.rule("pipeline", "pipeline", "start_run")["mode"] == "ask"
    cat = roles.catalog()
    assert "pipeline_tools.add_items" in cat and "pipeline_tools.row_stop" not in cat  # buttons are mine only
    role_tools = (PACKS_DIR / "pipeline" / "roles" / "pipeline-manager.md").read_text(encoding="utf-8")
    assert "start_run" not in role_tools and "row_" not in role_tools  # the role never starts or stops a run


def test_hooks_only_for_switched_on_packs(env):  # G-PACK-3
    assert [p for p, _ in connectors.pack_hooks("TICK")] == ["pipeline"]
    env.ps.set_enabled("pipeline", False)
    assert connectors.pack_hooks("TICK") == [] and services.busy("pipeline") == ""


def test_services_tick_runs_the_pack_and_notices_show(env):  # G-PACK-3
    import asyncio
    env.set(block("alpha", env.repos["alpha"]))
    call("add_items", channel="alpha", items="ok1")
    svc = PackServices(env.gate, notify=env.sched.add_notice, paused=lambda: False)
    asyncio.run(svc.tick())
    assert len(pending(env)) == 1
    assert pending(env)[0]["agent"] == "pipeline service"  # the log says the pack's code asked, not an agent
    env.sched.add_notice("Pipeline: x", "it failed", True)
    n = env.sched.notices()[-1]
    assert n["status"] == "failed" and n["reason"] == "it failed"


# ---- channels and inputs (P-PIPE-1, P-PIPE-3) ----------------------------------------------------

def test_channel_problems_are_listed_and_never_run(env):  # P-PIPE-1
    P = tools().P
    chans = P.parse_channels("[good]\nfolder: %s\ncommand: python make.py {input}\noutput: out\\*.mp4\n"
                             "timeout:\n[Bad Name]\nfolder: Z:\\nope\ncommand: {input} x\ncolour: red\n"
                             "[two]\nfolder: %s\ncommand: python x.py {secret}\noutput: a\ntimeout: 12x\n"
                             % (env.repos["alpha"], env.repos["beta"]))
    good, bad, two = chans
    assert good.problems == [] and good.timeout is None and good.gpu is True  # no limit unless set
    assert any("lowercase" in p for p in bad.problems) and any("placeholder" in p for p in bad.problems)
    assert any("colour" in p or "key: value" in p for p in bad.problems) and any("output" in p for p in bad.problems)
    assert any("{secret}" in p for p in two.problems) and any("12x" in p for p in two.problems)
    assert P.duration("14h") == 14 * 3600 and P.duration("2h30m") == 9000 and P.duration("none") is None
    assert P.parse_channels("folder: x")[0].problems  # text before the first [name] is a problem


def test_unsafe_inputs_are_refused(env):  # P-PIPE-3
    env.set(block("alpha", env.repos["alpha"]))
    for bad in ("-rf", "--help", "a & calc", "100%", "x|y", 'say "hi"', "a^b", "<in",
                "story=--force", "a,--delete", "a;b", r"..\..\Users\me\secret.txt", "stories/../../x",
                r"C:\Windows\win.ini", "/etc/passwd", r"\\server\share", "file:stream"):
        with pytest.raises(Exception):
            call("add_items", channel="alpha", items=bad)
    assert tools().queue({}) == "The queue is empty."  # one bad line adds nothing
    assert "Added 1" in call("add_items", channel="alpha", items="stories/abc_song.yaml")


def test_free_text_goes_through_a_file(env):  # P-PIPE-3
    env.set(f"[alpha]\nfolder: {env.repos['alpha']}\ncommand: \"{sys.executable}\" make.py {{input_file}}\n"
            f"output: out\\*.mp4\ngpu: no\n")
    auto(env)
    call("add_items", channel="alpha", items='A "quoted" topic & 100% more')
    wait(env, lambda: item(1)["state"] == "done")
    calls = (env.repos["alpha"] / "calls.log").read_text(encoding="utf-8")
    assert "input.txt" in calls and "quoted" not in calls  # the text never touched the command line


def test_bare_program_is_never_taken_from_the_repo_folder(env, monkeypatch):  # P-PIPE-3
    repo = env.repos["alpha"]
    (repo / "notreal.exe").write_bytes(b"MZ")
    bin_dir = env.tmp / "bin"
    bin_dir.mkdir()
    (bin_dir / "tool").write_text("#!/bin/sh")  # an extensionless script next to tool.cmd, like npm
    (bin_dir / "tool.cmd").write_text("@echo off")
    monkeypatch.setenv("PATH", str(bin_dir))
    P = tools().P
    with pytest.raises(Exception, match="not on PATH"):
        P._which("notreal", repo)
    assert P._which("tool", repo) == (bin_dir / "tool.cmd").resolve()  # review M14: what Windows really runs
    (repo / "sub").mkdir()
    (repo / "sub" / "go.bat").write_text("@echo off")
    assert P._which(r"sub\go.bat", repo) == (repo / "sub" / "go.bat").resolve()  # a path: from the repo
    with pytest.raises(Exception, match="not a .exe"):
        P._which(r".\make.py", repo)  # Windows cannot start a .py file directly


def test_batch_programs_get_no_spaces_and_words_split_like_windows(env):  # P-PIPE-3
    repo = env.repos["alpha"]
    (repo / "run.bat").write_text("@echo off")
    env.set(f"[alpha]\nfolder: {repo}\ncommand: run.bat --story {{input}}\noutput: out\\*.mp4\n")
    with pytest.raises(Exception, match="space"):
        call("add_items", channel="alpha", items="two words")
    assert "Added 1" in call("add_items", channel="alpha", items="stories/one.yaml")
    P = tools().P
    assert P.split('a.exe --out="a b" "c d" e') == ["a.exe", "--out=a b", "c d", "e"]
    assert P.split(r'"C:\Program Files\x.exe" -i {input}') == [r"C:\Program Files\x.exe", "-i", "{input}"]


# ---- the queue: approvals, order, done (P-PIPE-2, P-PIPE-5) --------------------------------------

def test_ask_shows_the_exact_command_then_runs_in_order(env):  # P-PIPE-2, P-PIPE-5
    env.set(block("alpha", env.repos["alpha"]))
    call("add_items", channel="alpha", items="ok1\nok2")
    tick(env)
    tick(env)
    held = pending(env)
    assert len(held) == 1  # only one item per free slot waits in Approvals
    preview = connectors.get("pipeline_tools.start_run").preview(held[0]["payload"])
    assert sys.executable in preview and "make.py ok1" in preview and str(env.repos["alpha"]) in preview
    assert "no time limit" in preview.lower()
    assert item(2)["state"] == "waiting"
    approve_all(env)
    wait(env, lambda: item(1)["state"] == "done")
    assert item(1)["output"].endswith("ok1.mp4")
    assert any("done" in t for t, _, f in env.state["notices"] if not f)
    wait(env, lambda: len(pending(env)) == 1)  # the next one is asked for by itself
    approve_all(env)
    wait(env, lambda: item(2)["state"] == "done")


def test_changed_setup_needs_a_new_approval(env):  # P-PIPE-5
    env.set(block("alpha", env.repos["alpha"]) + block("beta", env.repos["beta"]))
    call("add_items", channel="alpha", items="ok1")
    call("add_items", channel="beta", items="ok2")
    tick(env)
    held = pending(env)[0]
    env.set(block("alpha", env.repos["alpha"], extra="timeout: 14h") + block("beta", env.repos["beta"]))
    with pytest.raises(Exception, match="changed"):
        env.gate.approve(held["id"])
    assert not (env.repos["alpha"] / "calls.log").exists() and item(1)["fails"] == 0  # no attempt used up
    tick(env)  # review M14: the old card is withdrawn and the item asked again, never stuck in its slot
    assert env.gate.store.approval(held["id"])["status"] == "withdrawn"
    again = pending(env)
    assert len(again) == 1 and again[0]["payload"]["item"] == "1" and again[0]["id"] != held["id"]
    assert "14h" in connectors.get("pipeline_tools.start_run").preview(again[0]["payload"])
    env.set(block("beta", env.repos["beta"]))  # alpha removed while it waits: beta gets the GPU slot
    tick(env)
    tick(env)
    assert [a["payload"]["item"] for a in pending(env)] == ["2"]


def test_reject_then_retry_is_a_new_request_not_a_repeat(env):  # P-PIPE-5
    env.set(block("alpha", env.repos["alpha"]))
    call("add_items", channel="alpha", items="ok1")
    tick(env)
    env.gate.reject(pending(env)[0]["id"])
    tick(env)
    call("retry", item="1")
    auto(env)
    wait(env, lambda: item(1)["state"] == "done")  # Auto runs it: not held as "you rejected this before"


def test_rejected_item_is_skipped_and_the_next_one_asked(env):  # P-PIPE-5
    env.set(block("alpha", env.repos["alpha"]))
    call("add_items", channel="alpha", items="ok1\nok2")
    tick(env)
    env.gate.reject(pending(env)[0]["id"])
    tick(env)
    assert item(1)["state"] == "skipped"
    tick(env)
    assert pending(env)[0]["payload"]["item"] == "2"


def test_failure_is_retried_once_with_the_retry_command_then_flagged(env):  # P-PIPE-2
    env.set(block("alpha", env.repos["alpha"], extra="retry_command: \"%s\" make.py {input} --resume" % sys.executable))
    auto(env)
    call("add_items", channel="alpha", items="fail1\nok2")
    wait(env, lambda: item(1)["state"] == "failed")
    calls = (env.repos["alpha"] / "calls.log").read_text(encoding="utf-8").splitlines()
    assert calls[:2] == ["fail1", "fail1 --resume"]
    assert any("tried again" in x and not f for _, x, f in env.state["notices"])  # a retry is not a red notice
    assert item(1)["tries"] == 2 and "exit code 3" in item(1)["note"]
    assert sum(1 for _, x, f in env.state["notices"] if f and "needs you" in x) == 1
    wait(env, lambda: item(2)["state"] == "done")  # the queue moves on
    call("retry", item="1")  # back in line by hand: a new attempt, never a repeat of an old approval
    assert item(1)["state"] == "waiting" and item(1)["fails"] == 0


def test_exit_0_without_a_new_output_file_is_a_failure(env):  # P-PIPE-2
    env.set(block("alpha", env.repos["alpha"], extra="retries: 0"))
    (env.repos["alpha"] / "out").mkdir()
    old = env.repos["alpha"] / "out" / "noout1.mp4"
    old.write_bytes(b"old")
    past = time.time() - 3600
    import os
    os.utime(old, (past, past))
    auto(env)
    call("add_items", channel="alpha", items="noout1")
    wait(env, lambda: item(1)["state"] == "failed")
    assert "no new file" in item(1)["note"]


# ---- slots (P-PIPE-4) ------------------------------------------------------------------------------

def test_one_gpu_run_at_a_time_and_cpu_runs_in_parallel(env):  # P-PIPE-4
    env.set(block("alpha", env.repos["alpha"]) + block("beta", env.repos["beta"])
            + block("gamma", env.repos["gamma"], gpu="no", parallel=3), max_parallel="3")
    auto(env)
    call("add_items", channel="alpha", items="slow1")
    call("add_items", channel="beta", items="slow2")
    call("add_items", channel="gamma", items="slow3\nslow4\nslow5")
    tick(env)
    running = {r["input"] for r in tools()._rows("state = 'running'")}
    assert running == {"slow1", "slow3", "slow4"}  # one GPU run, then the total cap of 3 (gamma could take 3)
    wait(env, lambda: all(r["state"] == "done" for r in tools()._rows()))


def test_a_finished_item_can_be_run_again(env):  # P-PIPE-2 (review M14 round 2)
    env.set(block("alpha", env.repos["alpha"], extra="retries: 0"))
    auto(env)
    call("add_items", channel="alpha", items="a1")
    wait(env, lambda: item(1)["state"] == "done")
    call("add_items", channel="alpha", items="a1")  # the same path is written again: still a new file
    wait(env, lambda: item(2)["state"] in ("done", "failed"))
    assert item(2)["state"] == "done", item(2)["note"]


def test_a_run_that_left_nothing_never_takes_the_last_runs_file(env):  # P-PIPE-2 (review M14 round 3)
    env.set(f"[alpha]\nfolder: {env.repos['alpha']}\ncommand: \"{sys.executable}\" make.py {{input}}\n"
            f"output: out\\*.mp4\nretries: 0\n")
    auto(env)
    call("add_items", channel="alpha", items="ok1\nnoout2")  # the second starts right after the first ends
    wait(env, lambda: item(2)["state"] in ("done", "failed"))
    assert item(1)["state"] == "done" and item(2)["state"] == "failed" and "no new file" in item(2)["note"]


def test_wrapper_never_calls_a_started_run_failed_when_its_files_are_busy(env, monkeypatch):  # P-PIPE-6
    import importlib.util
    spec = importlib.util.spec_from_file_location("wrap_probe", tools().P.WRAPPER)
    wrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrap)
    real, calls = wrap.os.replace, []

    def busy_once(src, dst):  # Windows refuses while the hub has the file open for reading
        calls.append(dst)
        if len(calls) in (2, 3):
            raise PermissionError(5, "Access is denied")
        return real(src, dst)

    run = env.tmp / "run"
    run.mkdir()
    (run / "spec.json").write_text(json.dumps({"argv": [sys.executable, "-c", "print('hi')"],
                                               "cwd": str(run), "env": {}}), encoding="utf-8")
    monkeypatch.setattr(wrap.os, "replace", busy_once)
    monkeypatch.setattr(sys, "argv", ["_wrap.py", str(run)])
    wrap.main()
    assert json.loads((run / "result.json").read_text())["code"] == 0  # not -1 "could not start"
    assert "could not start" not in (run / "output.log").read_text()


def test_parallel_runs_never_share_an_output_file(env):  # P-PIPE-2
    P = tools().P
    chans = P.parse_channels(f"[g]\nfolder: {env.repos['gamma']}\ncommand: python make.py {{input}}\n"
                             f"output: out\\*.mp4\nparallel: 2\n")
    assert any("own file" in x for x in chans[0].problems)
    out = env.repos["gamma"] / "out"
    out.mkdir()
    (out / "a.mp4").write_bytes(b"v")
    taken = frozenset({__import__("os").path.normcase(str(out / "a.mp4"))})
    assert P.output_found("out\\*.mp4", env.repos["gamma"], 0, taken) == ""


def test_pause_stops_new_starts_only(env):  # P-PIPE-4, G-BUDGET-2
    env.set(block("alpha", env.repos["alpha"]) + block("gamma", env.repos["gamma"], gpu="no"))
    auto(env)
    call("add_items", channel="alpha", items="slow1\nok2")
    tick(env)
    env.state["paused"] = True
    wait(env, lambda: item(1)["state"] == "done")  # a run already going finishes
    tick(env)
    assert item(2)["state"] == "waiting"
    env.state["paused"] = False
    call("pause_channel", channel="alpha")
    tick(env)
    assert item(2)["state"] == "waiting"
    call("resume_channel", channel="alpha")
    wait(env, lambda: item(2)["state"] == "done")


# ---- long runs, limits, restarts (P-PIPE-6) --------------------------------------------------------

def test_time_limit_stops_the_whole_tree(env, monkeypatch):  # P-PIPE-6
    env.set(block("alpha", env.repos["alpha"], extra="timeout: 1m\nretries: 0"))
    auto(env)
    call("add_items", channel="alpha", items="hang child")
    wait(env, lambda: (env.repos["alpha"] / "child.pid").exists())
    P = tools().P
    child = int((env.repos["alpha"] / "child.pid").read_text())
    real = time.time
    with monkeypatch.context() as m:
        m.setattr(time, "time", lambda: real() + 120)  # 2 minutes later
        tick(env)
    assert item(1)["state"] == "failed" and "time limit" in item(1)["note"]
    end = time.time() + 15
    while P._created(child) is not None and time.time() < end:
        time.sleep(0.2)
    assert P._created(child) is None  # the grandchild was stopped too


def test_quiet_run_gets_one_notice_and_keeps_running(env, monkeypatch):  # P-PIPE-6
    env.set(block("alpha", env.repos["alpha"], extra="quiet: 1m"))
    auto(env)
    call("add_items", channel="alpha", items="hang1")
    wait(env, lambda: item(1)["state"] == "running")
    real = time.time
    with monkeypatch.context() as m:
        m.setattr(time, "time", lambda: real() + 300)
        tick(env)
        tick(env)
    assert item(1)["state"] == "running" and "no output" in item(1)["note"]
    assert sum(1 for _, x, _f in env.state["notices"] if "no output" in x) == 1
    assert services.busy("pipeline")  # the pack cannot be switched off while it runs
    call("row_stop", item="1")
    assert item(1)["state"] == "stopped" and services.busy("pipeline") == ""


def test_stop_ends_a_renderer_whose_launcher_already_exited(env):  # P-PIPE-6 (review M14)
    # Plain Windows programs (a Microsoft Store Python never joins a job, see _pstate.kill): the run
    # starts a launcher that starts the "renderer" (ping) and exits, so the renderer has no live parent.
    repo = env.repos["alpha"]
    (repo / "run.bat").write_text("@echo off\r\nstart \"\" /b cmd /c \"%~dp0launch.bat\"\r\nping -n 300 127.0.0.1 >nul\r\n")
    (repo / "launch.bat").write_text("@echo off\r\nstart \"\" /b ping -n 300 127.0.0.29 >nul\r\n")
    env.set(f"[alpha]\nfolder: {repo}\ncommand: .\\run.bat {{input}}\noutput: out\\{{input}}.mp4\n")
    auto(env)
    call("add_items", channel="alpha", items="x1")

    def orphans():
        ps = subprocess.run(["powershell", "-NoProfile", "-Command",
                             "(Get-CimInstance Win32_Process -Filter \"Name='PING.EXE'\" | Where-Object "
                             "{ $_.CommandLine -like '*127.0.0.29*' }).ProcessId"],
                            capture_output=True, text=True, timeout=60).stdout.split()
        return [int(x) for x in ps]

    wait(env, lambda: bool(orphans()))
    P = tools().P
    found = orphans()
    assert P.wrapper_info(item(1)["run_dir"]).get("job") is True
    call("row_stop", item="1")
    end = time.time() + 15
    while any(P._created(o) is not None for o in found) and time.time() < end:
        time.sleep(0.2)
    assert all(P._created(o) is None for o in found)  # not in the launcher's tree any more, but in the run's job


def test_a_missing_program_needs_fixing_and_never_fails_the_queue(env):  # P-PIPE-1 (review M14 round 4)
    env.set(f"[alpha]\nfolder: {env.repos['alpha']}\ncommand: .venv\\Scripts\\python.exe make.py {{input}}\n"
            f"output: out\\{{input}}.mp4\n")
    auto(env)
    call("add_items", channel="alpha", items="a1\na2\na3")
    tick(env)
    assert all(r["state"] == "waiting" for r in tools()._rows())
    assert "needs fixing" in tools().queue({}) and "does not exist" in tools().queue({})


def test_two_channels_on_one_folder_need_their_own_output_names(env):  # P-PIPE-2 (review M14 round 4)
    P = tools().P
    shared = f"folder: {env.repos['alpha']}\ncommand: python make.py {{input}}\n"
    a, b = P.parse_channels(f"[shorts]\n{shared}output: out\\*.mp4\n[longs]\n{shared}output: out\\{{input}}.mp4\n")
    assert any("same folder" in x for x in a.problems) and any("same folder" in x for x in b.problems)
    a, b = P.parse_channels(f"[shorts]\n{shared}output: out\\{{item}}.mp4\n[longs]\n{shared}output: out\\{{item}}_l.mp4\n")
    assert not a.problems and not b.problems
    # Review M14 round 5: the same folder written another way, or an absolute output into it
    alpha = str(env.repos["alpha"])
    x, y, z = P.parse_channels(
        f"[x]\nfolder: {alpha}\ncommand: python make.py {{input}}\noutput: out\\*.mp4\n"
        f"[y]\nfolder: {alpha.replace(chr(92), '/')}\ncommand: python make.py {{input}}\noutput: out\\*.mp4\n"
        f"[z]\nfolder: {env.repos['beta']}\ncommand: python make.py {{input}}\noutput: {alpha}\\out\\*.mp4\n")
    assert all(any("same folder" in p for p in c.problems) for c in (x, y, z))
    star = P.parse_channels(f"[s]\nfolder: {alpha}\ncommand: python make.py {{input}}\n"
                            f"output: out\\{{input}}*.mp4\nparallel: 2\n")[0]
    assert any("own file" in p for p in star.problems)  # story1* would match story10's file
    assert P.own_name(r"out\{input}_final.mp4") and not P.own_name(r"out\{input}*.mp4")
    # Review M14 round 6: no wildcard after {input} anywhere; {item} not followed by a digit
    assert not P.own_name(r"out\{input}_*.mp4") and not P.own_name(r"out\{input}\**\*.mp4")
    assert P.own_name(r"out\{item}_*.mp4") and not P.own_name(r"out\{item}0.mp4")
    assert not P.own_name(r"out\*{item}.mp4")  # item 2 would match the file of item 12
    # Review M14 round 8: any wildcard with {input}, or before {item}, can match another item's file
    assert not P.own_name(r"out\*_{input}.mp4") and not P.own_name(r"out\**\{input}.mp4")
    assert not P.own_name(r"out\**\{item}.mp4") and P.own_name(r"out\{input}.mp4")
    assert not P.own_name(r"out\{input}.mp4", need_item=True)  # two channels: only the queue number


def test_an_old_process_number_is_never_adopted_after_a_reboot(env):  # P-PIPE-6 (review M14 round 4)
    env.set(block("alpha", env.repos["alpha"]))
    stranger = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        time.sleep(0.5)
        run = env.tmp / "cut"
        run.mkdir()
        # It started after the run (so a start-time window alone would accept it), but it is not the
        # process that wrote pid.json: the recorded start time does not match.
        (run / "pid.json").write_text(json.dumps({"wrapper": stranger.pid, "created": 1}))
        call("add_items", channel="alpha", items="a1")
        tools()._set(1, state="launching", run_dir=str(run), started_at=time.time() - 30, tries=1)
        tick(env)
        assert item(1)["state"] == "launching" and item(1)["pid"] is None  # not adopted
        call("row_stop", item="1")
        assert stranger.poll() is None  # Stop never touched it
    finally:
        stranger.kill()


def test_a_start_cut_off_by_a_restart_is_adopted_not_run_twice(env):  # P-PIPE-6 (review M14)
    env.set(block("alpha", env.repos["alpha"]))
    auto(env)
    call("add_items", channel="alpha", items="hang1")
    wait(env, lambda: item(1)["state"] == "running")
    it = item(1)
    end = time.time() + 15
    while not tools().P.wrapper_info(it["run_dir"]) and time.time() < end:
        time.sleep(0.1)
    tools()._set(1, state="launching", pid=None, created=None)  # as if the app stopped right after starting it
    tick(env)
    assert item(1)["state"] == "running" and item(1)["pid"] == it["pid"] and item(1)["tries"] == 1
    tools()._set(1, state="launching", pid=None, created=None, run_dir=str(env.tmp / "never-started"),
                 started_at=time.time() - 200)
    tick(env)
    assert item(1)["state"] != "launching"  # nothing started: failed (and retried), never stuck
    tools().P.kill(it["pid"], it["created"], it["run_dir"])


def test_run_survives_the_hub_and_is_picked_up_later(env):  # P-PIPE-6
    env.set(block("alpha", env.repos["alpha"]))
    auto(env)
    call("add_items", channel="alpha", items="slow1")
    tick(env)
    it = item(1)
    assert it["state"] == "running"
    # The wrapper is not the hub's child: it runs on its own and writes its result to a file.
    end = time.time() + 20
    while tools().P.result(it["run_dir"]) is None and time.time() < end:
        time.sleep(0.2)
    assert tools().P.result(it["run_dir"])["code"] == 0
    tick(env)  # "after the restart": the next look finishes it from the files
    assert item(1)["state"] == "done"


def test_a_run_that_vanished_is_failed_not_lost(env):  # P-PIPE-6
    env.set(block("alpha", env.repos["alpha"], extra="retries: 0"))
    auto(env)
    call("add_items", channel="alpha", items="hang1")
    wait(env, lambda: item(1)["state"] == "running")
    it = item(1)
    tools().P.kill(it["pid"], it["created"])  # as if the PC restarted
    wait(env, lambda: item(1)["state"] == "failed")
    assert "without a result" in item(1)["note"]


# ---- the role's tools and my buttons (P-PIPE-7) ---------------------------------------------------

def test_queue_tools(env):  # P-PIPE-7
    env.set(block("alpha", env.repos["alpha"]))
    out = call("add_items", channel="alpha", items="a1\na2\na3\na1\nA1")
    assert "Added 3" in out and "not added again: a1, A1" in out  # the same file name on Windows
    call("move", item="3", where="top")
    assert [r["input"] for r in tools()._rows("state = 'waiting'")] == ["a3", "a1", "a2"]
    call("row_down", item="3")
    assert [r["input"] for r in tools()._rows("state = 'waiting'")] == ["a1", "a3", "a2"]
    call("skip", item="2")
    assert item(2)["state"] == "skipped"
    view = json.loads(call("view_queue"))
    assert view["columns"][0] == "#" and [r[2] for r in view["rows"]][:2] == ["a1", "a3"]
    call("row_pause", item="1")
    assert "paused" in call("list_channels") and "paused" in call("queue")
    assert "make.py" not in call("list_channels")  # the user's command stays out of the agent's context
    with pytest.raises(Exception, match="no channel"):
        call("add_items", channel="nope", items="x")
    with pytest.raises(Exception, match="only a running"):
        call("row_stop", item="1")
