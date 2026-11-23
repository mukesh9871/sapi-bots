"""Video pack approvals and status (Milestone 13, P-VID-1, P-VID-5).

- submit_script (acting, through Approvals): asks me to approve SCRIPT.md; the approval shows the
  whole script and carries its fingerprint, so I approve exactly the text I saw. Renders need exactly
  the approved text: a changed script needs a new approval.
- submit_final (acting, through Approvals): asks me to approve the final cut, pinned the same way by
  the video's fingerprint. On approval it writes social-draft.md (a draft post for the social pack).
- status (read-only): script approved or not, the renders so far, the final cut.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from hub.connectors.base import Action, ActionError

_spec = importlib.util.spec_from_file_location("hub_pack_video__vstate_tools", Path(__file__).with_name("_vstate.py"))
V = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(V)

FOLDER = {"folder": {"hub": "folder", "description": "The project folder (filled in by the hub)", "max": 400}}


def _script(payload: dict[str, str]) -> tuple:
    folder = V.folder_of(payload)
    text = V.script(folder)
    if payload.get("fingerprint", "").strip() != V.digest(text)[:16]:  # exactly the script that was shown
        raise ActionError("SCRIPT.md changed since its fingerprint was read (status shows it): ask again")
    return folder, text


def show_script(payload: dict[str, str]) -> str:
    folder, text = _script(payload)
    return f"Approve this script (SCRIPT.md in {folder}):\n\n{text}"


def check_script(payload: dict[str, str]) -> None:
    _script(payload)


def submit_script(payload: dict[str, str]) -> str:
    folder, text = _script(payload)
    V.approve_script(folder, text)
    return "The user approved the script. Renders can start."


def _final_file(payload: dict[str, str]) -> tuple:
    folder = V.folder_of(payload)
    if not V.script_approved(folder):
        raise ActionError("the script is not approved (or changed since): the final cut comes after it")
    file = V.inside(folder, payload["file"], "file")
    if not file.is_file() or file.suffix.lower() not in V.VIDEO_TYPES or (folder / "final") not in file.parents:
        raise ActionError("file must be a finished video under final/ (made by assemble), like final/cut.mp4")
    if payload.get("fingerprint", "").strip() != V.file_digest(file)[:16]:  # exactly the cut shown
        raise ActionError("the video changed since its fingerprint was read (assemble shows it): ask again")
    return folder, file


def show_final(payload: dict[str, str]) -> str:
    folder, file = _final_file(payload)
    return (f"Approve the final cut: {file} ({file.stat().st_size // 1024} KB). Open it from the project "
            f"folder before you approve.")


def check_final(payload: dict[str, str]) -> None:
    _final_file(payload)


def submit_final(payload: dict[str, str]) -> str:
    folder, file = _final_file(payload)
    V.approve_final(folder, file)
    title = next((l.lstrip("# ").strip() for l in V.script(folder).splitlines() if l.startswith("#")), "New video")
    draft = folder / "social-draft.md"
    draft.write_text(f"# Draft post for the social media pack\n\nTitle: {title}\nVideo: {file}\n\n"
                     f"Write one short line about it, then post it as an announcement (with a link once it is "
                     f"uploaded). If the video uses AI-generated footage, label it as each platform requires.\n",
                     encoding="utf-8")
    return f"The user approved the final cut ({file.name}). A draft post is in {draft.name}."


def status(payload: dict[str, str]) -> str:
    folder = V.folder_of(payload)
    try:
        fp = V.digest(V.script(folder))[:16]
    except ActionError as exc:
        fp = f"(none: {exc})"
    lines = [f"Script approved: {'yes' if V.script_approved(folder) else 'no'}", f"Script fingerprint: {fp}"]
    for r in V.renders(folder):
        lines.append(f"Render {r['shot']}: {r['status']} {r['detail'][:200]}")
    fin = V.final(folder)
    lines.append(f"Final cut approved: {fin['file'] if fin else 'not yet'}")
    approved = Path(fin["file"]).resolve() if fin else None
    final_dir = folder / "final"
    for cut in sorted(final_dir.glob("*.mp4")) if final_dir.is_dir() else []:
        if cut.name.startswith(".") or cut.is_symlink() or cut.resolve() == approved:
            continue  # review M13-3: lets the producer see a cut that already waits
        lines.append(f"Cut made, not approved: final/{cut.name} (fingerprint {V.file_digest(cut)[:16]})")
    return "\n".join(lines)


ACTIONS = [
    Action(connector="video_tools", name="submit_script", platform="video", run=submit_script, show=show_script,
           check=check_script, required=("fingerprint",),
           fields={**FOLDER, "fingerprint": {"description": "The script fingerprint that status shows", "max": 40}},
           description="Ask the user to approve SCRIPT.md (the script and shot list). Renders wait for it."),
    Action(connector="video_tools", name="submit_final", platform="video", run=submit_final, show=show_final,
           check=check_final, required=("file", "fingerprint"),
           fields={**FOLDER, "file": {"description": "The final video, for example final/cut.mp4", "max": 300},
                   "fingerprint": {"description": "The video fingerprint that assemble shows", "max": 40}},
           description="Ask the user to approve the final cut (writes a draft post for the social media pack)."),
    Action(connector="video_tools", name="status", platform="video", acting=False, fields=FOLDER, run=status,
           description="Show whether the script is approved, the renders so far and the final cut."),
]
