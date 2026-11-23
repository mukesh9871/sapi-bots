"""Milestone 12 offline checks: the software team pack (P-DEV-1..4) and hub-filled fields. No Claude calls.

- M12 check: the full team delivers a small tested program in a git project folder, within budget.
  Offline: the git flow the team uses (branches, commits signed by role, tests recorded per commit,
  a reviewer who is not the author, merge only after both). The live test runs a real team.
"""

import asyncio
import json
import shutil

import pytest

from hub import connectors
from hub.chat import claude_session
from hub.connectors.base import Action
from hub.engine import packs as packs_mod
from hub.engine import roles
from hub.engine.packs import PACKS_DIR, PackStore, install, read_pack
from hub.engine.roles import RoleStore
from hub.gate.gate import Gate
from hub.gate.store import GateStore
from hub.scheduler.schedules import ScheduleStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")
    root = tmp_path / "packs"
    shutil.copytree(PACKS_DIR / "devteam", root / "devteam")
    ps = PackStore(root, tmp_path / "hub.db")
    monkeypatch.setattr(connectors, "pack_dirs", ps.enabled_dirs)
    monkeypatch.setattr(packs_mod, "active", ps)
    gate = Gate(GateStore(tmp_path / "hub.db"), paused=lambda: False)
    ss = ScheduleStore(tmp_path / "hub.db")
    rs = RoleStore(tmp_path / "roles")
    rs.pack_dirs = ps.enabled_dirs
    install(ps, gate, ss, "devteam")
    folder = tmp_path / "projects" / "team-1"
    folder.mkdir(parents=True)
    return tmp_path, ps, gate, rs, folder


def tip(folder):
    import subprocess
    return subprocess.run(["git", "-C", str(folder), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def run(action, folder, agent="Developer", **fields):
    act = connectors.get(action)
    return act.run(act.clean(act.fill(fields, {"folder": str(folder), "agent": agent})))


# ---- hub-filled fields (engine, Milestone 12) -----------------------------------------------------

def test_hub_filled_fields_are_hidden_and_cannot_be_forged(env):
    tmp, ps, gate, rs, folder = env
    commit = connectors.get("dev_git.commit")
    assert set(commit.schema()["properties"]) == {"message"}  # the agent never sees folder or agent
    filled = commit.fill({"message": "x", "folder": "C:\\Windows", "agent": "Reviewer"},
                         {"folder": str(folder), "agent": "Developer"})
    assert filled == {"message": "x", "folder": str(folder), "agent": "Developer"}


def test_project_folder_must_be_inside_projects(env):
    tmp, ps, gate, rs, folder = env
    assert packs_mod.project_folder(str(folder)) == folder.resolve()
    for bad in ("", str(tmp), str(tmp / "projects"), "relative", str(tmp / "elsewhere")):
        (tmp / "elsewhere").mkdir(exist_ok=True)
        with pytest.raises(ValueError):
            packs_mod.project_folder(bad)


def test_hub_field_is_kept_at_approval(env):
    tmp, ps, gate, rs, folder = env
    build_branch(folder)
    tool = claude_session._gated_tool(connectors.get("dev_run.run_tests"), gate, "Tester", "devteam", folder)
    asyncio.run(tool.handler({"folder": "C:\\Windows", "commit": tip(folder)}))  # held (Ask); its folder is ignored
    item = gate.store.approvals()[0]
    assert item["payload"]["folder"] == str(folder)
    other = tmp / "projects" / "team-2"
    other.mkdir()
    gate.approve(item["id"], {"folder": str(other), "commit": tip(folder)})  # an edit cannot move it to another folder
    assert any(e["action_id"] == "dev_run.run_tests" and e["payload"].get("folder") == str(folder)
               for e in gate.store.activity())


# ---- the git flow (P-DEV-1, P-DEV-2, P-DEV-4) -------------------------------------------------------

def build_branch(folder):
    run("dev_git.init", folder, agent="Manager")
    run("dev_git.branch", folder, name="feature/count")
    (folder / "count.py").write_text("def count(text):\n    return len(text.split())\n", encoding="utf-8")
    run("dev_git.commit", folder, agent="Developer", message="Add count")
    (folder / "check_count.py").write_text("from count import count\n\ndef test_count():\n    assert count('a b c') == 3\n",
                                           encoding="utf-8")
    run("dev_git.commit", folder, agent="Tester", message="Add tests")


def test_branch_commit_test_review_merge(env):  # M12 check (offline): the flow end to end
    tmp, ps, gate, rs, folder = env
    build_branch(folder)
    assert "Tester (AI)" in run("dev_git.log", folder)
    with pytest.raises(Exception, match="no passing test run"):
        run("dev_git.merge", folder, agent="Manager", name="feature/count")
    out = run("dev_run.run_tests", folder, agent="Tester", commit=tip(folder))
    assert "PASSED" in out and "1 passed" in out
    with pytest.raises(Exception, match="not accepted"):
        run("dev_git.merge", folder, agent="Manager", name="feature/count")
    with pytest.raises(Exception, match="you made this commit"):
        run("dev_review.accept", folder, agent="Tester", name="feature/count")  # P-DEV-4
    assert "Accepted" in run("dev_review.accept", folder, agent="Reviewer", name="feature/count", notes="Looks right")
    assert "Merged feature/count into main" in run("dev_git.merge", folder, agent="Manager", name="feature/count")
    assert "Merge feature/count (tests passed, accepted by Reviewer (AI))" in run("dev_git.log", folder)


def test_new_commit_needs_new_tests_and_review(env):  # the exact commit counts
    tmp, ps, gate, rs, folder = env
    build_branch(folder)
    run("dev_run.run_tests", folder, agent="Tester", commit=tip(folder))
    run("dev_review.accept", folder, agent="Reviewer", name="feature/count")
    (folder / "count.py").write_text("def count(text):\n    return 0\n", encoding="utf-8")
    run("dev_git.commit", folder, agent="Developer", message="Break it")
    with pytest.raises(Exception, match="no passing test run"):
        run("dev_git.merge", folder, agent="Manager", name="feature/count")
    assert "FAILED" in run("dev_run.run_tests", folder, agent="Tester", commit=tip(folder))
    with pytest.raises(Exception, match="no passing test run"):
        run("dev_git.merge", folder, agent="Manager", name="feature/count")


def test_runner_uses_only_check_files_and_needs_a_commit(env):
    tmp, ps, gate, rs, folder = env
    run("dev_git.init", folder, agent="Manager")
    run("dev_git.branch", folder, name="feature/x")
    (folder / "x.py").write_text("X = 1\n", encoding="utf-8")
    with pytest.raises(Exception, match="commit your changes first"):
        run("dev_run.run_tests", folder, agent="Tester", commit=tip(folder))
    run("dev_git.commit", folder, agent="Developer", message="x")
    out = run("dev_run.run_tests", folder, agent="Tester", commit=tip(folder))
    assert "FAILED" in out and "no test ran" in out  # the hub's own tests are never collected


def test_never_on_main_and_safe_branch_names(env):
    tmp, ps, gate, rs, folder = env
    run("dev_git.init", folder, agent="Manager")
    (folder / "a.py").write_text("a = 1\n", encoding="utf-8")
    with pytest.raises(Exception, match="never commit on main"):
        run("dev_git.commit", folder, agent="Developer", message="x")
    for bad in ("-D", "--upload-pack=evil", "a..b", "main", "x/", "a b"):
        with pytest.raises(Exception):
            run("dev_git.branch", folder, name=bad)


def test_push_needs_a_remote_and_approval(env):  # P-DEV-3
    tmp, ps, gate, rs, folder = env
    run("dev_git.init", folder, agent="Manager")
    assert gate.store.rule("devteam", "devteam", "push")["mode"] == "ask"
    assert gate.store.rule("devteam", "devteam", "run_tests")["mode"] == "ask"
    with pytest.raises(Exception, match="no remote"):
        run("dev_run.push", folder)


def test_roles_get_only_their_jobs(env):  # P-DEV-4: who can do what
    tmp, ps, gate, rs, folder = env
    tools = {r: set(rs.get(f"devteam.{r}").tools) for r in read_pack("devteam", PACKS_DIR / "devteam").roles}
    assert all(rs.get(f"devteam.{r}").problem == "" for r in tools)
    assert "dev_review.accept" in tools["reviewer"] and not any("dev_review.accept" in t for r, t in tools.items() if r != "reviewer")
    assert "dev_git.merge" in tools["manager"] and "dev_git.merge" not in tools["developer"]
    assert "dev_run.run_tests" in tools["tester"] and "dev_run.run_tests" not in tools["developer"]
    assert "dev_run.push" not in set().union(*tools.values())  # nobody pushes by default
    assert "Write" not in tools["reviewer"]


def test_agent_cannot_write_pytest_files_but_can_write_check_files(env):
    tmp, ps, gate, rs, folder = env
    assert claude_session.outside_folder("Write", {"file_path": str(folder / "test_x.py"), "content": ""}, folder)
    assert claude_session.outside_folder("Write", {"file_path": str(folder / "conftest.py"), "content": ""}, folder)
    assert claude_session.outside_folder("Write", {"file_path": str(folder / "check_x.py"), "content": ""}, folder) is None
    assert claude_session.outside_folder("Write", {"file_path": str(folder / ".git" / "config"), "content": ""}, folder)



def test_a_planted_hook_never_runs(env):  # review M12-1 finding 1
    tmp, ps, gate, rs, folder = env
    marker = tmp / "hook-ran.txt"
    for place in (folder / ".hub-no-hooks", folder / "hooks"):
        place.mkdir(parents=True, exist_ok=True)
        (place / "pre-commit").write_text(f"#!/bin/sh\necho ran > '{marker.as_posix()}'\n", encoding="utf-8")
    build_branch(folder)
    run("dev_git.switch", folder, name="main")
    assert not marker.exists()


def test_each_commit_is_its_own_test_request(env):  # review M12-1 finding 2
    tmp, ps, gate, rs, folder = env
    build_branch(folder)
    gate.store.set_rule("devteam", "devteam", "run_tests", "auto", 50)
    tool = claude_session._gated_tool(connectors.get("dev_run.run_tests"), gate, "Tester", "devteam", folder)
    first = asyncio.run(tool.handler({"commit": tip(folder)}))
    assert "PASSED" in first["content"][0]["text"]
    (folder / "count.py").write_text("def count(text):\n    return len(text.split())  # same\n", encoding="utf-8")
    run("dev_git.commit", folder, agent="Developer", message="Tidy")
    second = asyncio.run(tool.handler({"commit": tip(folder)}))
    assert "PASSED" in second["content"][0]["text"] and gate.store.pending_count() == 0  # not held as a repeat
    stale = asyncio.run(tool.handler({"commit": "0" * 40}))
    assert stale["is_error"] and "not at commit" in stale["content"][0]["text"]


def test_merge_conflict_is_undone_and_update_helps(env):  # review M12-1 finding 3
    tmp, ps, gate, rs, folder = env
    run("dev_git.init", folder, agent="Manager")
    for name, text in (("a", "A version\n"), ("b", "B version\n")):
        run("dev_git.switch", folder, name="main")
        run("dev_git.branch", folder, name=name)
        (folder / "notes.txt").write_text(text, encoding="utf-8")
        (folder / f"check_{name}.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        run("dev_git.commit", folder, agent="Developer", message=f"{name}")
        run("dev_run.run_tests", folder, agent="Tester", commit=tip(folder))
        run("dev_review.accept", folder, agent="Reviewer", name=name)
    run("dev_git.merge", folder, agent="Manager", name="a")
    with pytest.raises(Exception, match="main has moved on"):
        run("dev_git.merge", folder, agent="Manager", name="b")
    assert "On branch main" in run("dev_git.status", folder) and "(no changes)" in run("dev_git.status", folder)
    run("dev_git.switch", folder, name="b")
    with pytest.raises(Exception, match="notes.txt"):
        run("dev_git.update", folder, agent="Developer")
    (folder / "notes.txt").write_text("A version\n", encoding="utf-8")
    run("dev_git.commit", folder, agent="Developer", message="Fit main")
    assert "Updated b from main" in run("dev_git.update", folder, agent="Developer")


def test_push_and_runs_need_their_own_repository(env):  # review M12-1 finding 4
    tmp, ps, gate, rs, folder = env
    with pytest.raises(Exception, match="no repository"):
        run("dev_run.push", folder)


def test_ignored_files_never_count_in_a_test_run(env):  # review M12-1 finding 5, M12-3 finding 3
    tmp, ps, gate, rs, folder = env
    build_branch(folder)
    (folder / ".gitignore").write_text("__pycache__/\nhelper.py\n", encoding="utf-8")
    (folder / "check_helper.py").write_text("import sys, os\nsys.path.insert(0, os.path.join(os.path.dirname("
                                            "__file__), 'pkg', '__pycache__'))\n\ndef test_h():\n"
                                            "    import helper, hidden\n", encoding="utf-8")
    run("dev_git.commit", folder, agent="Developer", message="ignore helper")
    (folder / "helper.py").write_text("X = 1\n", encoding="utf-8")
    (folder / "pkg" / "__pycache__").mkdir(parents=True)
    (folder / "pkg" / "__pycache__" / "hidden.py").write_text("Y = 1\n", encoding="utf-8")
    out = run("dev_run.run_tests", folder, agent="Tester", commit=tip(folder))
    assert "FAILED" in out  # the tests ran on the commit only: neither file is there


def test_short_file_names_are_refused(env):  # review M12-1 note
    tmp, ps, gate, rs, folder = env
    assert claude_session.outside_folder("Write", {"file_path": str(folder / "GIT~1" / "config"), "content": ""}, folder)


def test_main_in_other_letters_is_still_main(env):  # review M12-2 finding 1
    tmp, ps, gate, rs, folder = env
    run("dev_git.init", folder, agent="Manager")
    before = tip(folder)
    for name in ("Main", "MAIN"):
        with pytest.raises(Exception):
            run("dev_git.switch", folder, name=name)
        with pytest.raises(Exception):
            run("dev_git.branch", folder, name=name)
    (folder / "x.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(Exception, match="never commit on main"):
        run("dev_git.commit", folder, agent="Developer", message="straight to main")
    assert tip(folder) == before


def test_what_lands_on_main_is_what_was_tested(env):  # review M12-2 finding 2
    tmp, ps, gate, rs, folder = env
    run("dev_git.init", folder, agent="Manager")
    for name in ("c", "b"):
        run("dev_git.switch", folder, name="main")
        run("dev_git.branch", folder, name=name)
        (folder / f"{name}.py").write_text("V = 1\n", encoding="utf-8")
        (folder / f"check_{name}.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        run("dev_git.commit", folder, agent="Developer", message=name)
        run("dev_run.run_tests", folder, agent="Tester", commit=tip(folder))
        run("dev_review.accept", folder, agent="Reviewer", name=name)
    run("dev_git.merge", folder, agent="Manager", name="c")
    with pytest.raises(Exception, match="main has moved on"):
        run("dev_git.merge", folder, agent="Manager", name="b")  # b was tested without c
    run("dev_git.switch", folder, name="b")
    run("dev_git.update", folder, agent="Developer")
    with pytest.raises(Exception, match="no passing test run"):
        run("dev_git.merge", folder, agent="Manager", name="b")  # the updated tip needs new tests and review
    run("dev_run.run_tests", folder, agent="Tester", commit=tip(folder))
    run("dev_review.accept", folder, agent="Reviewer", name="b")
    assert "Merged b" in run("dev_git.merge", folder, agent="Manager", name="b")


def test_a_committed_fake_pytest_cannot_fake_a_pass(env):  # review M12-3 finding 1
    tmp, ps, gate, rs, folder = env
    build_branch(folder)
    (folder / "pytest.py").write_text("print('5 passed')\nraise SystemExit(0)\n", encoding="utf-8")
    (folder / "check_fail.py").write_text("def test_no():\n    assert False\n", encoding="utf-8")
    run("dev_git.commit", folder, agent="Developer", message="sneaky")
    assert "FAILED" in run("dev_run.run_tests", folder, agent="Tester", commit=tip(folder))
    for name in ("pytest.py", "sitecustomize.py", "pluggy.py", "_pytest/__init__.py"):
        assert claude_session.outside_folder("Write", {"file_path": str(folder / name), "content": ""}, folder)


def test_test_code_cannot_leave_git_settings_or_forge_results(env):  # review M12-3 finding 2
    tmp, ps, gate, rs, folder = env
    build_branch(folder)
    config = folder / ".git" / "config"
    before = config.read_bytes()
    db = packs_mod.data_dir("devteam") / "state.db"
    (folder / "check_evil.py").write_text(
        "import pathlib, sqlite3\n"
        f"CONFIG = pathlib.Path(r'{config}')\n"
        f"DB = r'{db}'\n"
        "def test_evil():\n"
        "    CONFIG.write_text(CONFIG.read_text() + '[core]\\n\\tfsmonitor = evil.sh\\n')\n"
        "    c = sqlite3.connect(DB)\n"
        "    c.execute(\"INSERT OR REPLACE INTO review_results VALUES (?, ?, 1, 'Reviewer', '', 0, 'x')\",\n"
        f"              (r'{folder.resolve()}', 'f' * 40))\n"
        "    c.commit()\n", encoding="utf-8")
    run("dev_git.commit", folder, agent="Tester", message="evil")
    s = connectors.get("dev_git.merge").run.__globals__["S"]
    s.db().close()  # the tables exist, so the planted insert really lands
    out = run("dev_run.run_tests", folder, agent="Tester", commit=tip(folder))
    assert "FAILED" in out and "changed .git/config" in out  # review M12-4: counts as failed
    assert config.read_bytes() == before  # put back after the run
    import sqlite3
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM review_results WHERE commit_id = ?", ("f" * 40,)).fetchone()[0] == 1
    assert s.review(folder.resolve(), "f" * 40) is None  # a row the hub did not sign does not count
    s.record_review(folder.resolve(), "e" * 40, True, "Reviewer", "")
    assert s.review(folder.resolve(), "e" * 40)["accepted"] == 1


def test_merge_ancestor_check_uses_the_exit_code(env):  # review M12-3 finding 4
    tmp, ps, gate, rs, folder = env
    build_branch(folder)
    s = connectors.get("dev_git.merge").run.__globals__["S"]
    with pytest.raises(Exception):
        s.git(folder, "merge-base", "--is-ancestor", tip(folder), "main")  # the branch is not in main


def test_design_is_saved_to_main_before_branches(env):  # review M12-2 finding 4
    tmp, ps, gate, rs, folder = env
    run("dev_git.init", folder, agent="Manager")
    (folder / "DESIGN.md").write_text("# Design\n", encoding="utf-8")
    assert "Saved DESIGN.md" in run("dev_git.save_design", folder, agent="Manager")
    run("dev_git.branch", folder, name="feature/a")
    assert (folder / "DESIGN.md").is_file()
    run("dev_git.switch", folder, name="main")
    (folder / "DESIGN.md").write_text("# Design 2\n", encoding="utf-8")
    (folder / "code.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(Exception, match="only DESIGN.md"):
        run("dev_git.save_design", folder, agent="Manager")



# ---- review M12 round 4 ------------------------------------------------------------------------------

def _commit_check(folder, body, name="check_x.py"):
    (folder / name).write_text(body, encoding="utf-8")
    run("dev_git.commit", folder, agent="Tester", message=f"add {name}")
    return run("dev_run.run_tests", folder, agent="Tester", commit=tip(folder))


def test_skipped_or_cut_short_runs_are_not_a_pass(env):
    tmp, ps, gate, rs, folder = env
    run("dev_git.init", folder, agent="Manager")
    run("dev_git.branch", folder, name="feature/x")
    out = _commit_check(folder, "import pytest\n\n@pytest.mark.skip\ndef test_a():\n    assert False\n")
    assert "FAILED" in out and "no test ran" in out
    out = _commit_check(folder, "import os\nos._exit(0)\n\ndef test_b():\n    pass\n", "check_y.py")
    assert "FAILED" in out


def test_a_deleted_config_and_a_moved_main_are_put_back(env):
    tmp, ps, gate, rs, folder = env
    build_branch(folder)
    config = folder / ".git" / "config"
    before, main = config.read_bytes(), tip(folder) and __import__("subprocess").run(
        ["git", "-C", str(folder), "rev-parse", "main"], capture_output=True, text=True).stdout.strip()
    out = _commit_check(folder, "import pathlib, subprocess\n"
                                f"GIT = pathlib.Path(r'{folder / '.git'}')\n"
                                "def test_c():\n"
                                f"    subprocess.run(['git', '-C', r'{folder}', 'update-ref', 'refs/heads/main', 'HEAD'])\n"
                                "    (GIT / 'config').unlink()\n")
    assert "FAILED" in out and "changed .git/config and main" in out
    assert config.read_bytes() == before
    import subprocess
    assert subprocess.run(["git", "-C", str(folder), "rev-parse", "main"], capture_output=True,
                          text=True).stdout.strip() == main


def test_a_process_left_running_does_not_break_the_run(env):
    tmp, ps, gate, rs, folder = env
    build_branch(folder)
    out = _commit_check(folder, "import subprocess, sys\n"
                                "def test_d():\n"
                                "    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'])\n")
    assert "PASSED" in out


# ---- review M12 round 5 ------------------------------------------------------------------------------

def test_main_cannot_move_while_the_tests_run(env):
    tmp, ps, gate, rs, folder = env
    build_branch(folder)
    run("dev_run.run_tests", folder, agent="Tester", commit=tip(folder))
    run("dev_review.accept", folder, agent="Reviewer", name="feature/count")
    s = connectors.get("dev_git.merge").run.__globals__["S"]
    with s.busy(folder.resolve(), "run the tests"):  # as if a test run were going on
        with pytest.raises(Exception, match="in progress in this folder"):
            run("dev_git.merge", folder, agent="Manager", name="feature/count")
        with pytest.raises(Exception, match="in progress in this folder"):
            run("dev_run.run_tests", folder, agent="Tester", commit=tip(folder))
    assert "Merged" in run("dev_git.merge", folder, agent="Manager", name="feature/count")


@pytest.mark.parametrize("kind", ["readonly-folder", "junction"])
def test_a_config_replaced_by_a_folder_is_put_back(env, kind):
    tmp, ps, gate, rs, folder = env
    build_branch(folder)
    config = folder / ".git" / "config"
    before = config.read_bytes()
    elsewhere = tmp / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("mine", encoding="utf-8")
    if kind == "junction":
        body = (f"    import subprocess\n    CONFIG.unlink()\n"
                f"    subprocess.run(['cmd', '/c', 'mklink', '/J', str(CONFIG), r'{elsewhere}'], check=True)\n")
    else:
        body = "    import os, stat\n    CONFIG.unlink()\n    CONFIG.mkdir()\n    (CONFIG / 'x').write_text('x')\n" \
               "    os.chmod(CONFIG / 'x', stat.S_IREAD)\n"
    out = _commit_check(folder, f"import pathlib\nCONFIG = pathlib.Path(r'{config}')\ndef test_e():\n" + body)
    assert "FAILED" in out and "could not be put back" not in out
    assert config.is_file() and config.read_bytes() == before
    assert (elsewhere / "keep.txt").read_text(encoding="utf-8") == "mine"  # a junction's target is left alone


def test_a_process_holding_the_output_cannot_hang_the_run(env, monkeypatch):
    tmp, ps, gate, rs, folder = env
    build_branch(folder)
    import time as _t
    monkeypatch.setattr(connectors.get("dev_run.run_tests").run.__globals__["sys"].modules[
        connectors.get("dev_run.run_tests").run.__module__], "TEST_TIMEOUT", 20)
    start = _t.time()
    out = _commit_check(folder, "import subprocess, sys\n"
                                "def test_f(capfd):\n"
                                "    with capfd.disabled():\n"
                                "        subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)'])\n")
    assert "PASSED" in out and _t.time() - start < 60
