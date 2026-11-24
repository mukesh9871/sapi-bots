"""Milestone 13 offline checks: the video production pack (P-VID-1..6). No Claude calls, no GPU.

- A fake ComfyUI (a small local HTTP server) stands in for the real one: /prompt, /history, /view, /queue.
- A fake run_ffmpeg stands in for FFmpeg: it checks the arguments and writes the output file.
- M13 check (a real render of a 3-shot short) needs ComfyUI and stays open.
"""

import asyncio
import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from hub import connectors
from hub.chat import claude_session
from hub.engine import packs as packs_mod
from hub.engine import roles
from hub.engine.packs import PACKS_DIR, PackStore, install, read_pack
from hub.engine.roles import RoleStore
from hub.gate.gate import Gate
from hub.gate.store import GateStore
from hub.scheduler.schedules import ScheduleStore

TEMPLATE = {
    "3": {"class_type": "KSampler", "inputs": {"seed": "{{seed}}", "steps": 20}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}, cinematic"}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{negative}}"}},
    "9": {"class_type": "SaveAnimatedWEBP", "inputs": {"filename_prefix": "shot"}},
}


class FakeComfy:
    """Records what was queued. fail = how many renders fail before one works."""

    def __init__(self):
        self.queued = []
        self.fail = 0
        self.history_errors = 0  # answers /history with 500 this many times
        self.mode = ""  # "redirect" or "garbage" for /history
        self.posts = []  # every POST path (cancel calls too)
        self.output_name = "shot_00001.mp4"
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, body, kind="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.posts.append(self.path)
                if self.path != "/prompt":
                    return self._send({})
                fake.queued.append(body["prompt"])
                self._send({"prompt_id": f"p{len(fake.queued)}"})

            def do_GET(self):
                url = urlsplit(self.path)
                if url.path.startswith("/history/"):
                    pid = url.path.rsplit("/", 1)[1]
                    if fake.history_errors:
                        fake.history_errors -= 1
                        self.send_response(500)
                        self.end_headers()
                        return
                    if fake.mode == "redirect":
                        self.send_response(302)
                        self.send_header("Location", "http://example.com/history")
                        self.end_headers()
                        return
                    if fake.mode == "garbage":
                        return self._send(b"<html>not json</html>", "text/html")
                    if fake.fail:
                        fake.fail -= 1
                        return self._send({pid: {"status": {"status_str": "error"}, "outputs": {}}})
                    out = {"9": {"images": [{"filename": fake.output_name, "subfolder": "", "type": "output"}]}}
                    return self._send({pid: {"status": {"status_str": "success", "completed": True}, "outputs": out}})
                if url.path == "/view":
                    return self._send(b"VIDEO:" + parse_qs(url.query)["filename"][0].encode(), "image/webp")
                if url.path == "/queue":
                    return self._send({"queue_running": [1], "queue_pending": [1, 2]})
                self.send_response(404)
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(roles, "ROLE_WORK_ROOT", tmp_path / "projects")
    root = tmp_path / "packs"
    shutil.copytree(PACKS_DIR / "video", root / "video")
    ps = PackStore(root, tmp_path / "hub.db")
    monkeypatch.setattr(connectors, "pack_dirs", ps.enabled_dirs)
    monkeypatch.setattr(packs_mod, "active", ps)
    gate = Gate(GateStore(tmp_path / "hub.db"), paused=lambda: False)
    rs = RoleStore(tmp_path / "roles")
    rs.pack_dirs = ps.enabled_dirs
    install(ps, gate, ScheduleStore(tmp_path / "hub.db"), "video")
    comfy = FakeComfy()
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "short.json").write_text(json.dumps(TEMPLATE), encoding="utf-8")
    ps.set_settings("video", {"comfy_url": comfy.url, "templates_folder": str(templates)})
    monkeypatch.setattr(module("video_comfy.render"), "POLL", 0.01)
    folder = tmp_path / "projects" / "film-1"
    folder.mkdir(parents=True)
    yield tmp_path, ps, gate, rs, folder, comfy
    comfy.server.shutdown()


def run(action, folder, agent="Producer", **fields):
    act = connectors.get(action)
    return act.run(act.clean(act.fill(fields, {"folder": str(folder), "agent": agent})))


def module(action):
    import sys
    return sys.modules[connectors.get(action).run.__module__]


def fingerprint(folder):
    line = next(l for l in run("video_tools.status", folder).splitlines() if l.startswith("Script fingerprint"))
    return line.split(": ", 1)[1]


def approve_script(folder, text="# Lighthouse\n\nShot 1: a lighthouse at dawn.\n"):
    (folder / "SCRIPT.md").write_text(text, encoding="utf-8")
    return run("video_tools.submit_script", folder, fingerprint=fingerprint(folder))


RENDER = {"template": "short", "shot": "shot01", "prompt": "a lighthouse at dawn", "negative": "people", "seed": "42"}


# ---- pack ------------------------------------------------------------------------------------------

def test_pack_reads_clean_and_installs_safe_rules(env):  # P-VID-1, P-VID-6
    tmp, ps, gate, rs, folder, comfy = env
    pack = read_pack("video", PACKS_DIR / "video")
    assert set(pack.roles) == {"script-writer", "director", "producer", "editor", "setup"}
    assert gate.store.rule("video", "video", "submit_script")["mode"] == "ask"
    assert gate.store.rule("video", "video", "submit_final")["mode"] == "ask"
    assert gate.store.rule("video", "comfyui", "render")["mode"] == "ask"  # suggested Auto, installed as Ask
    tools = {r: set(rs.get(f"video.{r}").tools) for r in pack.roles}
    assert all(rs.get(f"video.{r}").problem == "" for r in tools)
    assert [r for r, t in tools.items() if "video_comfy.render" in t] == ["producer"]
    assert [r for r, t in tools.items() if "video_tools.submit_script" in t] == ["script-writer"]
    assert [r for r, t in tools.items() if "video_tools.submit_final" in t] == ["editor"]
    assert set(connectors.get("video_comfy.render").schema()["properties"]) >= {"template", "shot"}
    assert "folder" not in connectors.get("video_comfy.render").schema()["properties"]  # hub-filled


# ---- script approval (P-VID-1) ------------------------------------------------------------------------

def test_render_waits_for_the_approved_script(env):
    tmp, ps, gate, rs, folder, comfy = env
    with pytest.raises(Exception, match="not approved"):
        run("video_comfy.render", folder, **RENDER)
    (folder / "SCRIPT.md").write_text("# A\n\nShot 1.\n", encoding="utf-8")
    with pytest.raises(Exception, match="not approved"):
        run("video_comfy.render", folder, **RENDER)
    assert comfy.queued == []


def test_script_approval_shows_the_whole_script_and_is_pinned(env):
    tmp, ps, gate, rs, folder, comfy = env
    (folder / "SCRIPT.md").write_text("# Lighthouse\n\nShot 1: dawn.\n", encoding="utf-8")
    fp = fingerprint(folder)
    tool = claude_session._gated_tool(connectors.get("video_tools.submit_script"), gate, "Script writer", "video", folder)
    asyncio.run(tool.handler({"fingerprint": fp}))
    item = gate.store.approvals()[0]
    assert "Shot 1: dawn." in connectors.get("video_tools.submit_script").show(item["payload"])
    (folder / "SCRIPT.md").write_text("# Lighthouse\n\nShot 1: something else.\n", encoding="utf-8")
    with pytest.raises(Exception, match="changed"):  # the text I saw is not the text there now
        gate.approve(item["id"])
    assert "Script approved: no" in run("video_tools.status", folder)


def test_a_changed_script_needs_a_new_approval(env):
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    assert "Script approved: yes" in run("video_tools.status", folder)
    (folder / "SCRIPT.md").write_text("# Lighthouse\n\nShot 1: now at night.\n", encoding="utf-8")
    assert "Script approved: no" in run("video_tools.status", folder)
    with pytest.raises(Exception, match="not approved"):
        run("video_comfy.render", folder, **RENDER)


# ---- rendering (P-VID-2..5) ------------------------------------------------------------------------

def test_render_fills_the_template_only_and_saves_in_the_project(env):
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    out = run("video_comfy.render", folder, **RENDER)
    assert "renders/shot01/shot_00001.mp4" in out
    assert (folder / "renders" / "shot01" / "shot_00001.mp4").read_bytes() == b"VIDEO:shot_00001.mp4"
    graph = comfy.queued[0]
    assert set(graph) == set(TEMPLATE)  # the same nodes: never a new graph (P-VID-3)
    assert graph["3"]["inputs"]["seed"] == 42 and graph["3"]["inputs"]["steps"] == 20
    assert graph["6"]["inputs"]["text"] == "a lighthouse at dawn, cinematic"
    assert "Render shot01: done" in run("video_tools.status", folder)


def test_a_missing_placeholder_or_bad_number_is_refused(env):
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    with pytest.raises(Exception, match="needs: negative"):
        run("video_comfy.render", folder, **{**RENDER, "negative": ""})
    with pytest.raises(Exception):
        run("video_comfy.render", folder, **{**RENDER, "seed": "4e2"})
    for bad in ("../short", "C:\\x", "nope"):
        with pytest.raises(Exception):
            run("video_comfy.render", folder, **{**RENDER, "template": bad})
    for bad in ("../x", "a/b", "-x"):
        with pytest.raises(Exception):
            run("video_comfy.render", folder, **{**RENDER, "shot": bad})
    assert comfy.queued == []


def test_a_failed_render_is_tried_once_more_then_flagged(env):  # P-VID-4
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    comfy.fail = 1
    assert "shot01" in run("video_comfy.render", folder, **RENDER)
    assert len(comfy.queued) == 2
    comfy.fail = 2
    with pytest.raises(Exception, match="failed twice"):
        run("video_comfy.render", folder, **{**RENDER, "shot": "shot02"})
    assert "Render shot02: failed (flagged)" in run("video_tools.status", folder)


def test_one_render_at_a_time(env):  # P-VID-4
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    m = module("video_comfy.render")
    active, peak, lock = [0], [0], threading.Lock()
    real = m._once

    def slow(*a):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        try:
            import time
            time.sleep(0.2)
            return real(*a)
        finally:
            with lock:
                active[0] -= 1

    m._once = slow
    try:
        threads = [threading.Thread(target=run, args=("video_comfy.render", folder), kwargs={**RENDER, "shot": f"s{i}"})
                   for i in range(3)]
        [t.start() for t in threads]
        [t.join() for t in threads]
    finally:
        m._once = real
    assert peak[0] == 1 and len(comfy.queued) == 3


def test_comfyui_must_be_on_this_pc(env):  # P-VID-2
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    for url in ("http://example.com:8188", "http://192.168.1.5:8188", "http://user@127.0.0.1:8188", "file:///c:/x"):
        ps.set_settings("video", {"comfy_url": url})
        with pytest.raises(Exception, match="on this PC"):
            run("video_comfy.render", folder, **RENDER)
    assert comfy.queued == []


def test_an_output_name_from_comfyui_never_escapes_the_folder(env):
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    comfy.output_name = "..\\..\\evil.webp"
    run("video_comfy.render", folder, **RENDER)
    assert (folder / "renders" / "shot01" / "evil.webp").is_file()
    assert not (folder / "evil.webp").exists() and not (tmp / "projects" / "evil.webp").exists()


def test_templates_and_queue_are_information(env):
    tmp, ps, gate, rs, folder, comfy = env
    assert "short: placeholders negative, prompt, seed" in run("video_comfy.list_templates", folder)
    assert "1 running, 2 waiting" in run("video_comfy.queue", folder)


# ---- assembly and final cut (P-VID-1, P-VID-5) ------------------------------------------------------

@pytest.fixture
def fake_ffmpeg(monkeypatch):
    calls = []

    def fake(args):
        import subprocess
        from pathlib import Path
        calls.append(args)
        Path(args[-1]).write_bytes(b"MP4:" + "|".join(args).encode())
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(module("video_edit.assemble"), "run_ffmpeg", fake)
    return calls


def clips(folder, n=3):
    names = []
    for i in range(1, n + 1):
        p = folder / "renders" / f"shot0{i}" / "clip.mp4"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"clip")
        names.append(f"renders/shot0{i}/clip.mp4")
    return names


def test_assemble_joins_clips_in_order_with_subtitles(env, fake_ffmpeg):
    tmp, ps, gate, rs, folder, comfy = env
    names = clips(folder)
    (folder / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
    out = run("video_edit.assemble", folder, agent="Editor", clips=", ".join(names), subtitles="subtitles.srt")
    assert "final/cut.mp4" in out and (folder / "final" / "cut.mp4").is_file()
    expected = [str(folder.resolve() / "renders" / f"shot0{i}" / "clip.mp4") for i in (1, 2, 3)]
    first = fake_ffmpeg[0]
    assert [first[i + 1] for i, a in enumerate(first) if a == "-i"] == expected  # one input per clip, in order
    assert "concat=n=3:v=1:a=0[v]" in first[first.index("-filter_complex") + 1]
    second = fake_ffmpeg[1]  # subtitles added in a copy-only second step
    assert "mov_text" in second and "copy" in second and second[-1].endswith("cut.mp4")
    assert not any(p.name.startswith(".") for p in (folder / "final").iterdir())  # the picture-only file is gone


def test_assemble_refuses_files_outside_the_project(env, fake_ffmpeg):
    tmp, ps, gate, rs, folder, comfy = env
    clips(folder)
    (tmp / "outside.mp4").write_bytes(b"x")
    for bad in ("../outside.mp4", str(tmp / "outside.mp4"), "C:outside.mp4", "renders/shot01/missing.mp4",
                "SCRIPT.md"):
        with pytest.raises(Exception):
            run("video_edit.assemble", folder, agent="Editor", clips=bad)
    with pytest.raises(Exception):
        run("video_edit.assemble", folder, agent="Editor", clips="renders/shot01/clip.mp4", output="../x")
    with pytest.raises(Exception):
        run("video_edit.assemble", folder, agent="Editor", clips="renders/shot01/clip.mp4", audio="../outside.mp4")
    assert fake_ffmpeg == []


def test_final_cut_is_pinned_and_writes_a_draft_post(env, fake_ffmpeg):
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    out = run("video_edit.assemble", folder, agent="Editor", clips=",".join(clips(folder)))
    fp = out.split("Video fingerprint: ")[1].split(" ")[0]
    with pytest.raises(Exception, match="changed"):
        run("video_tools.submit_final", folder, agent="Editor", file="final/cut.mp4", fingerprint="0" * 16)
    assert "approved the final cut" in run("video_tools.submit_final", folder, agent="Editor",
                                           file="final/cut.mp4", fingerprint=fp)
    draft = (folder / "social-draft.md").read_text(encoding="utf-8")
    assert "Title: Lighthouse" in draft and "AI-generated" in draft
    assert "Final cut approved:" in run("video_tools.status", folder) and "not yet" not in run("video_tools.status", folder)


def test_final_cut_waits_for_the_script(env, fake_ffmpeg):
    tmp, ps, gate, rs, folder, comfy = env
    (folder / "SCRIPT.md").write_text("# A\n\nShot.\n", encoding="utf-8")
    out = run("video_edit.assemble", folder, agent="Editor", clips=",".join(clips(folder, 1)))
    fp = out.split("Video fingerprint: ")[1].split(" ")[0]
    with pytest.raises(Exception, match="not approved"):
        run("video_tools.submit_final", folder, agent="Editor", file="final/cut.mp4", fingerprint=fp)


def test_ffmpeg_path_setting_must_be_ffmpeg(env):
    tmp, ps, gate, rs, folder, comfy = env
    m = module("video_edit.assemble")
    (tmp / "calc.exe").write_bytes(b"x")
    ps.set_settings("video", {"ffmpeg_path": str(tmp / "calc.exe")})
    with pytest.raises(Exception, match="ffmpeg.exe"):
        m._ffmpeg()
    ps.set_settings("video", {"ffmpeg_path": "ffmpeg.exe"})
    with pytest.raises(Exception, match="ffmpeg.exe"):
        m._ffmpeg()


# ---- review M13 round 1 ------------------------------------------------------------------------------

def test_ffmpeg_inputs_are_local_files_of_known_formats_only(env, fake_ffmpeg):  # finding 1
    tmp, ps, gate, rs, folder, comfy = env
    names = clips(folder, 2)
    (folder / "music.mp3").write_bytes(b"m")
    (folder / "subs.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
    run("video_edit.assemble", folder, agent="Editor", clips=",".join(names), audio="music.mp3", subtitles="subs.srt")
    for args, count in ((fake_ffmpeg[0], 3), (fake_ffmpeg[1], 2)):
        inputs = [i for i, a in enumerate(args) if a == "-i"]
        assert len(inputs) == count
        for i in inputs:  # each input carries its own whitelist, right before it
            assert args[i - 4:i] == ["-protocol_whitelist", "file", "-format_whitelist", args[i - 1]]
            assert args[i - 1].startswith("concat,mov,mp4")
    args = fake_ffmpeg[0]
    graph = args[args.index("-filter_complex") + 1]
    assert "[2:a]apad[a]" in graph and "scale=1280:720" in graph and "fps=24" in graph  # findings 2, 3
    assert "copy" not in args and "-shortest" in args and "mov_text" not in args


def test_assemble_size_and_approved_cut_are_kept(env, fake_ffmpeg):  # findings 3, 8
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    names = ",".join(clips(folder, 1))
    for bad in ("1280", "1281x720", "99999x720", "1280x720;x"):
        with pytest.raises(Exception, match="size"):
            run("video_edit.assemble", folder, agent="Editor", clips=names, size=bad)
    out = run("video_edit.assemble", folder, agent="Editor", clips=names, size="720x1280")
    assert "scale=720:1280" in fake_ffmpeg[-1][fake_ffmpeg[-1].index("-filter_complex") + 1]
    fp = out.split("Video fingerprint: ")[1].split(" ")[0]
    run("video_tools.submit_final", folder, agent="Editor", file="final/cut.mp4", fingerprint=fp)
    with pytest.raises(Exception, match="approved cut"):
        run("video_edit.assemble", folder, agent="Editor", clips=names)
    assert "final/cut2.mp4" in run("video_edit.assemble", folder, agent="Editor", clips=names, output="cut2")


def test_a_few_missed_polls_do_not_start_a_second_render(env):  # finding 4
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    comfy.history_errors = 3
    run("video_comfy.render", folder, **RENDER)
    assert len(comfy.queued) == 1


def test_a_given_up_render_is_cancelled_and_its_leftovers_cleared(env):  # finding 4
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    stale = folder / "renders" / "shot01" / "old_00001.webp"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    comfy.history_errors = 10_000
    with pytest.raises(Exception, match="failed twice"):
        run("video_comfy.render", folder, **RENDER)
    assert comfy.posts.count("/queue") == 2  # taken off the queue
    assert "/interrupt" not in comfy.posts  # not running: the user's own running job is left alone
    assert stale.exists()  # a failed re-render keeps the earlier clip (review M13-2)
    comfy.history_errors = 0
    run("video_comfy.render", folder, **RENDER)
    assert [p.name for p in (folder / "renders").iterdir()] == ["shot01"]  # no try folders left
    assert [p.name for p in (folder / "renders" / "shot01").iterdir()] == ["shot_00001.mp4"]


def test_odd_replies_and_redirects_are_flagged_not_followed(env):  # findings 7, 9
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    m = module("video_comfy.render")
    m.POLL_ERRORS, saved = 1, m.POLL_ERRORS
    try:
        for i, mode in enumerate(("garbage", "redirect")):
            comfy.mode = mode
            with pytest.raises(Exception, match="failed twice"):
                run("video_comfy.render", folder, **{**RENDER, "shot": f"odd{i}"})
            assert f"Render odd{i}: failed (flagged)" in run("video_tools.status", folder)
    finally:
        m.POLL_ERRORS = saved


def test_steps_frames_and_fps_placeholders_can_be_filled(env, tmp_path):  # finding 6
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    graph = {"1": {"class_type": "X", "inputs": {"steps": "{{steps}}", "frames": "{{frames}}", "fps": "{{fps}}",
                                                 "text": "{{prompt}}"}}}
    (tmp / "templates" / "anim.json").write_text(json.dumps(graph), encoding="utf-8")
    with pytest.raises(Exception, match="needs: fps, frames, steps"):
        run("video_comfy.render", folder, template="anim", shot="a1", prompt="sea")
    run("video_comfy.render", folder, template="anim", shot="a1", prompt="sea", steps="25", frames="49", fps="16")
    assert comfy.queued[-1]["1"]["inputs"] == {"steps": 25, "frames": 49, "fps": 16, "text": "sea"}


# ---- review M13 round 2 ------------------------------------------------------------------------------

def test_a_render_without_a_video_file_says_so(env):
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    comfy.output_name = "shot_00001.webp"
    assert "None of these is a video file" in run("video_comfy.render", folder, **RENDER)
    assert "Render shot01: done (no video file)" in run("video_tools.status", folder)


def test_the_final_cut_must_be_under_final(env, fake_ffmpeg):
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    clip = clips(folder, 1)[0]
    fp = module("video_tools.submit_final").V.file_digest(folder / clip)[:16]
    with pytest.raises(Exception, match="under final"):
        run("video_tools.submit_final", folder, agent="Editor", file=clip, fingerprint=fp)


def test_cancel_interrupts_only_our_running_job(env):
    tmp, ps, gate, rs, folder, comfy = env
    m = module("video_comfy.render")
    comfy_queue = {"queue_running": [[0, "p-ours", {}]], "queue_pending": []}
    real = m._request

    def fake(path, body=None, **kw):
        if path == "/queue" and body is None:
            return comfy_queue
        return real(path, body, **kw)

    m._request = fake
    try:
        m._cancel("p-ours")
        assert comfy.posts[-1] == "/interrupt"
        comfy.posts.clear()
        m._cancel("p-other")
        assert comfy.posts == ["/queue"]
    finally:
        m._request = real


# ---- review M13 round 3 ------------------------------------------------------------------------------

def test_a_clip_in_use_loses_nothing_and_is_not_rendered_twice(env):
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    run("video_comfy.render", folder, **RENDER)
    clip = folder / "renders" / "shot01" / "shot_00001.mp4"
    with clip.open("rb"):  # open in a player: Windows refuses to move its folder
        with pytest.raises(Exception, match="in use"):
            run("video_comfy.render", folder, **{**RENDER, "seed": "7"})
    assert clip.read_bytes() == b"VIDEO:shot_00001.mp4"  # the old clip is all there
    kept = [p for p in (folder / "renders").iterdir() if p.name.startswith(".shot01.")]
    assert len(kept) == 1 and (kept[0] / "shot_00001.mp4").is_file()  # and the new one is kept
    assert len(comfy.queued) == 2  # no second GPU render for it
    assert "rendered, not swapped in" in run("video_tools.status", folder)
    run("video_comfy.render", folder, **{**RENDER, "seed": "7"})  # closed: works, leftovers cleared
    assert [p.name for p in (folder / "renders").iterdir()] == ["shot01"]


def test_hidden_try_folders_are_never_clips(env, fake_ffmpeg):
    tmp, ps, gate, rs, folder, comfy = env
    stale = folder / "renders" / ".shot01-1a2b3c4d" / "half.mp4"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"half")
    with pytest.raises(Exception, match="hidden folder"):
        run("video_edit.assemble", folder, agent="Editor", clips="renders/.shot01-1a2b3c4d/half.mp4")
    assert fake_ffmpeg == []


def test_status_shows_a_cut_that_waits(env, fake_ffmpeg):
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    out = run("video_edit.assemble", folder, agent="Editor", clips=",".join(clips(folder, 1)))
    fp = out.split("Video fingerprint: ")[1].split(" ")[0]
    assert f"Cut made, not approved: final/cut.mp4 (fingerprint {fp})" in run("video_tools.status", folder)
    run("video_tools.submit_final", folder, agent="Editor", file="final/cut.mp4", fingerprint=fp)
    assert "Cut made, not approved" not in run("video_tools.status", folder)


# ---- review M13 round 4 ------------------------------------------------------------------------------

def test_tidying_one_shot_never_touches_another(env):
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    other = folder / "renders" / ".a-b.0000000000001234"  # shot a-b's kept render
    other.mkdir(parents=True)
    (other / "x.mp4").write_bytes(b"x")
    run("video_comfy.render", folder, **{**RENDER, "shot": "a"})
    assert (other / "x.mp4").is_file()


def test_old_clips_come_back_after_a_crash_between_the_moves(env):
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    aside = folder / "renders" / ".shot01.old.0000000000001234"  # the hub stopped after moving the old clips aside
    aside.mkdir(parents=True)
    (aside / "good.mp4").write_bytes(b"good")
    comfy.history_errors = 10_000
    with pytest.raises(Exception, match="failed twice"):
        run("video_comfy.render", folder, **RENDER)
    assert (folder / "renders" / "shot01" / "good.mp4").read_bytes() == b"good"


def test_a_failed_swap_puts_the_old_clips_back(env, monkeypatch):
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    run("video_comfy.render", folder, **RENDER)
    from pathlib import Path
    real = Path.rename

    def flaky(self, target):
        if self.name.startswith(".shot01.") and ".old." not in self.name:  # the new render cannot move in
            raise PermissionError("in use")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", flaky)
    with pytest.raises(Exception, match="could not be moved"):
        run("video_comfy.render", folder, **{**RENDER, "seed": "9"})
    assert (folder / "renders" / "shot01" / "shot_00001.mp4").is_file()  # old clips back in place
    assert len(comfy.queued) == 2  # not rendered a third time
    assert any(p.name.startswith(".shot01.") and ".old." not in p.name for p in (folder / "renders").iterdir())



# ---- review M13 round 5 ------------------------------------------------------------------------------

def test_a_locked_aside_folder_is_never_deleted(env):
    tmp, ps, gate, rs, folder, comfy = env
    m = module("video_comfy.render")
    aside = folder / "renders" / ".shot01.old.0000000000001234"
    aside.mkdir(parents=True)
    (aside / "a.mp4").write_bytes(b"a")
    (aside / "b.mp4").write_bytes(b"b")
    with (aside / "b.mp4").open("rb"):  # a scanner holds one file: the folder cannot move
        m._tidy(folder / "renders", "shot01")
    assert sorted(p.name for p in aside.iterdir()) == ["a.mp4", "b.mp4"]
    m._tidy(folder / "renders", "shot01")  # free again: it comes back whole
    assert sorted(p.name for p in (folder / "renders" / "shot01").iterdir()) == ["a.mp4", "b.mp4"]


def test_the_newest_aside_folder_is_the_one_put_back(env):
    tmp, ps, gate, rs, folder, comfy = env
    m = module("video_comfy.render")
    for tag, name in (("0000000000000001", "stale.mp4"), ("0000000000000002", "newer.mp4")):
        d = folder / "renders" / f".shot01.old.{tag}"
        d.mkdir(parents=True)
        (d / name).write_bytes(b"x")
    m._tidy(folder / "renders", "shot01")
    assert [p.name for p in (folder / "renders").iterdir()] == ["shot01"]
    assert [p.name for p in (folder / "renders" / "shot01").iterdir()] == ["newer.mp4"]


def test_shot_names_in_other_letters_are_the_same_shot(env):  # review M13-6
    tmp, ps, gate, rs, folder, comfy = env
    approve_script(folder)
    run("video_comfy.render", folder, **RENDER)
    assert "renders/shot01/" in run("video_comfy.render", folder, **{**RENDER, "shot": "Shot01"})
    assert [p.name for p in (folder / "renders").iterdir()] == ["shot01"]
    assert "Render Shot01" not in run("video_tools.status", folder)
