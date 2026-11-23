"""Video pack editing with FFmpeg (Milestone 13, P-VID-1, P-VID-5): the editor joins rendered clips,
in order, into one video in the project folder, optionally with a subtitle track (.srt) and a music
or voice track. Every file must be inside the project folder; the output goes to final/. FFmpeg only
reads and writes those files: it runs no code the agents wrote.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

from hub.connectors.base import Action, ActionError
from hub.engine import packs

_spec = importlib.util.spec_from_file_location("hub_pack_video__vstate_edit", Path(__file__).with_name("_vstate.py"))
V = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(V)

CLIP_TYPES = (".mp4", ".mov", ".webm", ".mkv")
AUDIO_TYPES = (".mp3", ".wav", ".m4a", ".aac", ".flac")
# Review M13: FFmpeg goes by a file's content, not its name. Every input may only be a local file in
# one of these formats (a "clip" that is really a playlist cannot pull in other files or web hosts).
# FFmpeg passes both lists on to the files the concat list opens.
SAFE_INPUT = ["-protocol_whitelist", "file", "-format_whitelist",
              "concat,mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,srt,mp3,wav,aac,flac"]
SIZE_RE = re.compile(r"([1-9][0-9]{2,3})x([1-9][0-9]{2,3})")


def _ffmpeg() -> str:
    value = packs.setting(V.PACK, "ffmpeg_path").strip()
    if value:
        path = Path(value)
        if not path.is_absolute() or not path.is_file() or path.name.lower() not in ("ffmpeg.exe", "ffmpeg"):
            raise ActionError("the FFmpeg path on the Packs page must be the full path of ffmpeg.exe")
        return str(path)
    found = shutil.which("ffmpeg")
    if not found:
        raise ActionError("FFmpeg is not installed (or not on PATH): set its path on the Packs page")
    return found


def run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess:
    """One place that starts FFmpeg (the tests replace it)."""
    return subprocess.run([_ffmpeg(), "-hide_banner", "-nostdin", "-y", *args], capture_output=True, text=True,
                          timeout=1800, encoding="utf-8", errors="replace")


def assemble(payload: dict[str, str]) -> str:
    folder = V.folder_of(payload)
    clips = [c.strip() for c in payload["clips"].split(",") if c.strip()]
    if not 1 <= len(clips) <= 200:
        raise ActionError("list 1 to 200 clips, in order, separated by commas")
    paths = []
    for c in clips:
        if any(part.startswith(".") for part in c.replace("\\", "/").split("/")):
            raise ActionError(f"{c} is in a hidden folder (an unfinished render): use renders/<shot>/ files only")
        p = V.inside(folder, c, "clip")
        if not p.is_file() or p.suffix.lower() not in CLIP_TYPES:
            raise ActionError(f"{c} is not a video clip in the project folder")
        paths.append(p)
    name = payload.get("output", "").strip() or "cut"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,40}", name):
        raise ActionError("output must be a short name like cut")
    size = SIZE_RE.fullmatch(payload.get("size", "").strip() or "1280x720")
    if not size or not all(16 <= int(n) <= 4096 and int(n) % 2 == 0 for n in size.groups()):
        raise ActionError("size must be width x height in even pixels, like 1280x720 or 720x1280")
    w, h = size.groups()
    out_dir = folder / "final"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{name}.mp4"
    approved = V.final(folder)
    if approved and Path(approved["file"]) == out.resolve():  # the cut I approved is never replaced
        raise ActionError(f"final/{name}.mp4 is the approved cut: give the new cut another output name")
    # Review M13-2: each clip is its own input, scaled to one size and frame rate, then joined with the
    # concat filter, so clips from different templates (other codecs, sizes, rates) still join.
    # The clips' own sound is not used: the cut's sound is the music or voice file, if one is given.
    args: list[str] = []
    filters = []
    for i, p in enumerate(paths):
        args += [*SAFE_INPUT, "-i", str(p)]
        filters.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
                       f"fps=24,format=yuv420p,setsar=1[v{i}]")
    filters.append("".join(f"[v{i}]" for i in range(len(paths))) + f"concat=n={len(paths)}:v=1:a=0[v]")
    maps = ["-map", "[v]"]
    extra_out: list[str] = []
    audio = payload.get("audio", "").strip()
    if audio:
        a = V.inside(folder, audio, "audio")
        if not a.is_file() or a.suffix.lower() not in AUDIO_TYPES:
            raise ActionError("audio must be a sound file in the project folder (.mp3, .wav, .m4a)")
        args += [*SAFE_INPUT, "-i", str(a)]
        filters.append(f"[{len(paths)}:a]apad[a]")  # short music is padded with silence
        maps += ["-map", "[a]", "-c:a", "aac"]
        extra_out += ["-shortest"]  # the video's length decides
    subs = payload.get("subtitles", "").strip()
    srt = None
    if subs:
        srt = V.inside(folder, subs, "subtitles")
        if not srt.is_file() or srt.suffix.lower() != ".srt":
            raise ActionError("subtitles must be an .srt file in the project folder")
    # With subtitles, the picture and sound are made first and the subtitles added in a second step
    # that only copies streams (so -shortest never cuts the video at the last subtitle).
    first = out_dir / f".{name}-picture.mp4" if srt else out
    try:
        result = run_ffmpeg([*args, "-filter_complex", ";".join(filters), *maps, "-c:v", "libx264", "-crf", "18",
                             *extra_out, str(first)])
        if result.returncode != 0 or not first.is_file():
            raise ActionError(f"FFmpeg could not join the clips: {(result.stderr or '').strip()[-600:]}")
        if srt:
            result = run_ffmpeg([*SAFE_INPUT, "-i", str(first), *SAFE_INPUT, "-i", str(srt), "-map", "0", "-map", "1:s",
                                 "-c", "copy", "-c:s", "mov_text", str(out)])
            if result.returncode != 0 or not out.is_file():
                raise ActionError(f"FFmpeg could not add the subtitles: {(result.stderr or '').strip()[-600:]}")
    finally:
        if srt and first.exists():
            first.unlink()
    return (f"Made final/{name}.mp4 from {len(paths)} clips. Video fingerprint: "
            f"{V.file_digest(out)[:16]} (give it to submit_final).")


ACTIONS = [
    Action(connector="video_edit", name="assemble", platform="video", acting=False, run=assemble, required=("clips",),
           fields={"folder": {"hub": "folder", "description": "The project folder (filled in by the hub)", "max": 400},
                   "clips": {"description": "Clips in order, comma-separated, for example renders/shot01/a.mp4", "max": 8000},
                   "audio": {"description": "Optional music or voice file in the project folder", "max": 300},
                   "subtitles": {"description": "Optional .srt subtitle file in the project folder", "max": 300},
                   "output": {"description": "Output name (default cut): final/<name>.mp4", "max": 40},
                   "size": {"description": "Video size, width x height (default 1280x720, 720x1280 for upright)",
                            "max": 9}},
           description="Join rendered clips in order into final/<name>.mp4, with optional audio and subtitles."),
]
