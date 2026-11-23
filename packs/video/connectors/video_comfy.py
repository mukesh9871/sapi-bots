"""Video pack rendering through ComfyUI's local API (Milestone 13, P-VID-2, P-VID-3, P-VID-4).

- list_templates (read-only): the workflow templates I saved (API-format .json files in the folder set
  on the Packs page) and the placeholders each one has, like {{prompt}}, {{seed}}, {{length}}.
- render (acting, through Approvals): fills one template's placeholders (never builds a new node graph,
  P-VID-3), queues it on ComfyUI (POST /prompt), waits for it (/history), and saves the outputs in the
  project folder under renders/<shot>/ (P-VID-5). One render at a time on the GPU; a failed render is
  tried once more, then flagged (P-VID-4). It needs the approved script.
- queue (read-only): how many jobs ComfyUI has running and waiting.
ComfyUI must be on this PC (a local address only): the GPU work never leaves the machine (P-VID-2).
"""

from __future__ import annotations

import importlib.util
import http.client
import json
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from hub.connectors.base import Action, ActionError
from hub.engine import packs

_spec = importlib.util.spec_from_file_location("hub_pack_video__vstate_comfy", Path(__file__).with_name("_vstate.py"))
V = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(V)

GPU = threading.Lock()  # P-VID-4: one render at a time
PLACEHOLDER = re.compile(r"\{\{([a-z_]{1,30})\}\}")
NUMBERS = ("seed", "length", "width", "height", "steps", "frames", "fps")
WAIT = 3600  # seconds a render may take
POLL = 2.0
POLL_ERRORS = 5  # a busy ComfyUI may miss a few answers before a render counts as failed
FIELDS = ("prompt", "negative", *NUMBERS)


class _InUse(ActionError):
    """The render worked but its folder could not be swapped in: never worth a second GPU render."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Review M13: a reply that points elsewhere is never followed (renders never leave this PC)."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)
SHOT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,40}")
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
VIDEO_FILES = (".mp4", ".mov", ".webm", ".mkv")


def _base() -> str:
    url = (packs.setting(V.PACK, "comfy_url") or "http://127.0.0.1:8188").rstrip("/")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or (parts.hostname or "") not in LOCAL_HOSTS or parts.username:
        raise ActionError("ComfyUI must be on this PC (a 127.0.0.1 or localhost address): renders never leave it")
    return url


def _request(path: str, body: dict | None = None, raw: bool = False, timeout: int = 30) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(_base() + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            content = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:500].decode("utf-8", "replace")
        raise ActionError(f"ComfyUI refused it ({exc.code}): {detail}") from None
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        raise ActionError(f"ComfyUI cannot be reached (is it running?): {type(exc).__name__}") from None
    if raw:
        return content
    try:
        data = json.loads(content or b"{}")
    except ValueError:
        raise ActionError("ComfyUI sent an answer that is not JSON") from None
    if not isinstance(data, dict):
        raise ActionError("ComfyUI sent an unexpected answer")
    return data


def _templates_dir() -> Path:
    value = packs.setting(V.PACK, "templates_folder")
    if not value or not Path(value).is_dir():
        raise ActionError("the templates folder is not set on the Packs page (Video production, Settings)")
    return Path(value)


def _template(name: str) -> dict:
    name = name.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,80}", name) or ".." in name:
        raise ActionError("template must be the name of one of your templates (list_templates)")
    path = _templates_dir() / (name if name.endswith(".json") else f"{name}.json")
    if not path.is_file():
        raise ActionError(f"there is no template {name}")
    try:
        graph = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        raise ActionError(f"{name} is not a ComfyUI API-format .json file") from None
    if not isinstance(graph, dict) or not all(isinstance(v, dict) and "class_type" in v for v in graph.values()):
        raise ActionError(f"{name} is not in ComfyUI's API format (Save, API format)")
    return graph


def _fill(value: Any, values: dict[str, str]) -> Any:
    """Puts my values into the template's placeholders; the node graph itself never changes (P-VID-3)."""
    if isinstance(value, dict):
        return {k: _fill(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, values) for v in value]
    if isinstance(value, str):
        whole = PLACEHOLDER.fullmatch(value)
        if whole and whole.group(1) in NUMBERS:
            return int(values[whole.group(1)])
        return PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), value)
    return value


def list_templates(payload: dict[str, str]) -> str:
    folder = _templates_dir()
    lines = []
    for path in sorted(folder.glob("*.json"))[:100]:
        names = sorted(set(PLACEHOLDER.findall(path.read_text(encoding="utf-8-sig", errors="replace"))))
        lines.append(f"{path.stem}: placeholders {', '.join(names) or '(none)'}")
    return "Your ComfyUI templates (information):\n" + ("\n".join(lines) or "(none: save them as API-format .json)")


def _ready(payload: dict[str, str]) -> tuple:
    folder = V.folder_of(payload)
    if not V.script_approved(folder):
        raise ActionError("the script is not approved (or changed since): renders wait for it (P-VID-1)")
    shot = payload["shot"].strip().lower()  # review M13-6: Shot01 and shot01 are one folder on Windows
    if not SHOT_RE.fullmatch(shot):
        raise ActionError("shot must be a short name like shot01")
    graph = _template(payload["template"])
    needed = set(PLACEHOLDER.findall(json.dumps(graph)))
    values = {k: payload.get(k, "").strip() for k in FIELDS}
    missing = [n for n in needed if not values.get(n)]
    if missing:
        raise ActionError(f"the template needs: {', '.join(sorted(missing))}")
    for n in NUMBERS:
        if values.get(n) and not (values[n].isascii() and values[n].isdigit() and int(values[n]) <= 100_000_000):
            raise ActionError(f"{n} must be a whole number")
    return folder, shot, _fill(graph, values)


def show(payload: dict[str, str]) -> str:
    folder, shot, _graph = _ready(payload)
    return (f"Render {shot} with template {payload['template']} on this PC's ComfyUI.\n"
            f"Prompt: {payload.get('prompt', '')}\nNegative: {payload.get('negative', '')}\n"
            f"Seed {payload.get('seed', '-')}, length {payload.get('length', '-')}, size {payload.get('width', '-')}x"
            f"{payload.get('height', '-')}. Saved in {folder}\\renders\\{shot}.\n"
            "Check that it shows no real person and no copyrighted character you have no rights to (P-VID-6).")


def check(payload: dict[str, str]) -> None:
    _ready(payload)


def _cancel(pid: str) -> None:
    """A render I give up on is taken off ComfyUI's queue and stopped, so the next one does not wait
    behind it (review M13). Best effort: ComfyUI may already be gone."""
    try:
        _request("/queue", {"delete": [pid]})
        running = _request("/queue").get("queue_running") or []
        # Only my job is stopped: /interrupt stops whatever runs, which may be the user's own job.
        if any(isinstance(job, list) and pid in job for job in running):
            _request("/interrupt", {"prompt_id": pid})
    except ActionError:
        pass


def _once(graph: dict, folder: Path, shot: str) -> list[str]:
    """One try. Its files go to a fresh folder first and replace renders/<shot>/ only when the render
    worked, so a failed re-render never loses the earlier clip (review M13-2)."""
    renders = folder / "renders"
    target = renders / shot
    _tidy(renders, shot)
    tag = _tag()
    # "." never appears in a shot name (SHOT_RE), so these names belong to this shot only (review M13-4).
    work, old = renders / f".{shot}.{tag}", renders / f".{shot}.old.{tag}"
    keep = False
    try:
        names = _try(graph, work)
        try:  # review M13-3: the old clips move aside whole (a locked file refuses the move, losing nothing)
            if target.is_dir():
                target.rename(old)
        except OSError:
            keep = True
            raise _InUse(f"the new render is in renders/{work.name}, but renders/{shot} is in use (open in a "
                         f"player?): close it and render again") from None
        try:
            work.rename(target)
        except OSError:  # put the old clips back and keep the new render where it is
            keep = True
            if old.is_dir() and not target.exists():
                try:
                    old.rename(target)
                except OSError:
                    pass
            raise _InUse(f"the new render is in renders/{work.name}, but it could not be moved to renders/{shot} "
                         f"(a file in it is in use): render again") from None
        if old.exists():  # renamed to a throwaway name first: an ".old." folder is always complete
            trash = renders / f".{shot}.{_tag()}"
            try:
                old.rename(trash)
                shutil.rmtree(trash, ignore_errors=True)
            except OSError:
                pass  # left whole; _tidy removes it once the shot has its folder
        return [f"renders/{shot}/{n}" for n in names]
    finally:
        if work.exists() and not keep:
            shutil.rmtree(work, ignore_errors=True)


def _tag() -> str:
    """A folder tag that sorts by time (review M13-5: the newest set-aside clips are the ones put back)."""
    return f"{time.time_ns():016x}"


def _tidy(renders: Path, shot: str) -> None:
    """Leftovers of this shot's earlier tries (the hub closed mid-render, or a render that could not be
    swapped in). If the shot has no folder (a crash between the two moves), the newest set-aside clips
    come back first. Nothing is deleted while that has not worked."""
    if not renders.is_dir():
        return
    mine = re.compile(rf"\.{re.escape(shot)}\.(old\.)?[0-9a-f]{{16}}")
    target = renders / shot
    found = [(e, m) for e in renders.iterdir() if (m := mine.fullmatch(e.name)) and e.is_dir() and not e.is_symlink()]
    asides = sorted((e for e, m in found if m.group(1)), key=lambda e: e.name, reverse=True)
    if asides and not target.exists():
        try:
            asides[0].rename(target)
        except OSError:
            return  # review M13-5: a locked file: keep every copy as it is, try again next time
    for entry, _m in found:
        if entry.exists():
            shutil.rmtree(entry, ignore_errors=True)


def _try(graph: dict, work: Path) -> list[str]:
    queued = _request("/prompt", {"prompt": graph, "client_id": str(uuid.uuid4())})
    pid = queued.get("prompt_id")
    if not isinstance(pid, str) or not pid:
        raise ActionError(f"ComfyUI did not queue it: {json.dumps(queued)[:300]}")
    end, misses = time.time() + WAIT, 0
    try:
        while time.time() < end:
            try:
                hist = _request(f"/history/{urllib.parse.quote(pid)}").get(pid)
                misses = 0
            except ActionError:
                misses += 1
                if misses >= POLL_ERRORS:
                    raise
                hist = None
            if isinstance(hist, dict):
                status = hist.get("status") if isinstance(hist.get("status"), dict) else {}
                if status.get("status_str") == "error":
                    raise ActionError("ComfyUI reported an error in the workflow")
                outputs = hist.get("outputs") if isinstance(hist.get("outputs"), dict) else {}
                if status.get("completed") or outputs:
                    return _save_outputs(outputs, work)
            time.sleep(POLL)
        raise ActionError("the render took longer than an hour")
    except Exception:
        _cancel(pid)
        raise


def _save_outputs(outputs: dict, target: Path) -> list[str]:
    target.mkdir(parents=True, exist_ok=True)
    saved = []
    for node in outputs.values():
        for kind in ("images", "gifs", "videos"):
            items = node.get(kind) if isinstance(node, dict) else None
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                name = Path(str(item.get("filename", ""))).name  # never a path from ComfyUI
                if not name or name.startswith("."):
                    continue
                query = urllib.parse.urlencode({"filename": item.get("filename", ""), "subfolder": item.get("subfolder", ""),
                                                "type": item.get("type", "output")})
                data = _request(f"/view?{query}", raw=True, timeout=300)
                (target / name).write_bytes(data)
                saved.append(name)
    if not saved:
        raise ActionError("the render finished without any output file")
    return saved


def render(payload: dict[str, str]) -> str:
    folder, shot, graph = _ready(payload)
    with GPU:  # P-VID-4: one render at a time; a failed render is tried once more, then flagged
        try:
            files = _once(graph, folder, shot)
        except _InUse as busy:
            V.log_render(folder, shot, "rendered, not swapped in", str(busy))
            raise
        except Exception as first:  # any failure, also an odd reply, is retried and then flagged
            V.log_render(folder, shot, "retrying", str(first) if isinstance(first, ActionError) else type(first).__name__)
            try:
                files = _once(graph, folder, shot)
            except _InUse as busy:
                V.log_render(folder, shot, "rendered, not swapped in", str(busy))
                raise
            except Exception as second:
                second = second if isinstance(second, ActionError) else ActionError(type(second).__name__)
                V.log_render(folder, shot, "failed (flagged)", str(second))
                raise ActionError(f"render {shot} failed twice and is flagged for the user: {second}") from None
    video = [f for f in files if f.lower().endswith(VIDEO_FILES)]
    V.log_render(folder, shot, "done" if video else "done (no video file)", ", ".join(files))
    if not video:  # review M13-2: the editor can only join video files
        return (f"Rendered {shot}: {', '.join(files)}. None of these is a video file (.mp4, .webm, .mov, .mkv), so "
                "the editor cannot use them: tell the user the template must save a video (for example Video Combine, "
                "h264-mp4).")
    return f"Rendered {shot}: {', '.join(files)}"


def queue(payload: dict[str, str]) -> str:
    q = _request("/queue")
    return f"ComfyUI: {len(q.get('queue_running', []))} running, {len(q.get('queue_pending', []))} waiting."


ACTIONS = [
    Action(connector="video_comfy", name="list_templates", platform="comfyui", acting=False, fields={},
           run=list_templates, description="List the user's saved ComfyUI workflow templates and their placeholders."),
    Action(connector="video_comfy", name="render", platform="comfyui", run=render, show=show, check=check,
           required=("template", "shot"),
           fields={"folder": {"hub": "folder", "description": "The project folder (filled in by the hub)", "max": 400},
                   "template": {"description": "Template name (list_templates)", "max": 80},
                   "shot": {"description": "Shot name, for example shot01", "max": 40},
                   "prompt": {"description": "What the shot shows", "max": 2000},
                   "negative": {"description": "What it must not show", "max": 1000},
                   "seed": {"description": "Seed (a whole number)", "max": 12, "number": True},
                   "length": {"description": "Length in frames", "max": 6, "number": True},
                   "width": {"description": "Width in pixels", "max": 5, "number": True},
                   "height": {"description": "Height in pixels", "max": 5, "number": True},
                   "steps": {"description": "Sampling steps (only if the template has {{steps}})", "max": 4,
                             "number": True},
                   "frames": {"description": "Frames (only if the template has {{frames}})", "max": 6, "number": True},
                   "fps": {"description": "Frames per second (only if the template has {{fps}})", "max": 3,
                           "number": True}},
           description="Render one shot on this PC's ComfyUI with one of the user's templates (needs the approved script)."),
    Action(connector="video_comfy", name="queue", platform="comfyui", acting=False, fields={}, run=queue,
           description="Show how many jobs ComfyUI is running and has waiting."),
]
