---
name: Video production
version: 1.0
description: A script writer, a director, a producer and an editor make a short video from a one-line idea, rendered by ComfyUI on this PC.
---
Pack 5 (Milestone 13). Give the **Video team** a one-line idea on the Teams page.

**How it works (P-VID-1):** the script writer writes SCRIPT.md (the script and a shot list)
and asks you to approve it; the director turns each shot into prompts and settings; the
producer renders the shots one at a time; the editor joins the clips, music and subtitles
and asks you to approve the final cut. You approve the script and the final cut in
Approvals, and each approval is pinned to exactly what you saw. Each approval stops the
run: approve it, then run the team again and it goes on from there.

- **Rendering on this PC (P-VID-2):** ComfyUI's local API only (a 127.0.0.1 or localhost
  address). The agents write prompts and manage the queue; the GPU work never leaves
  your machine.
- **Your templates (P-VID-3):** save your ComfyUI workflows in API format (.json) in the
  templates folder below, with placeholders where the agents fill in values:
  `{{prompt}}`, `{{negative}}`, `{{seed}}`, `{{length}}`, `{{width}}`, `{{height}}`,
  `{{steps}}`, `{{frames}}`, `{{fps}}`. The agents never build new node graphs. A template
  must save a video file (.mp4 or .webm, for example with Video Combine): images and
  animated .webp or .gif cannot be joined into the cut.
- **One render at a time (P-VID-4):** a failed render is tried once more, then flagged.
- **Outputs (P-VID-5)** go to the team's project folder (`renders/<shot>/`, `final/`).
  An approved final cut gets a `social-draft.md` for the social media pack.
- **Rights (P-VID-6):** each render goes through Approvals (starts as Ask) and shows its
  prompt: never approve a real person's likeness or a copyrighted character unless you
  have the rights. The director leaves such a shot out and says why.
- Editing needs FFmpeg (set its path below if it is not on PATH). The cut is re-encoded
  to one size (1280x720 unless the editor picks another) at 24 frames per second. The
  cut's sound is the music or voice file (the clips' own sound is not used). Music shorter
  than the video is padded with silence. An approved cut is never overwritten.
