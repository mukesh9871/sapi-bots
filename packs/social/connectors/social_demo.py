"""Social pack demo channel (Milestone 10): posts go to data/outbox/social-demo.md.

It behaves like a real platform connector (acting, through Approvals, the daily cap and the
activity log), so the whole flow can be tried without an account. HUB_OUTBOX moves the file.
- post: any text (suggested rule Ask).
- post_announcement: a routine post put together here in a fixed format (suggested Auto). Its
  fields are one plain line each and its link must go to a site I listed, so it cannot carry a
  tag or a link elsewhere (P-SOC-5). The words themselves are plain text that can still name
  someone: that is why its rule starts as Ask and switching it to Auto is my choice.
The "never" topics (P-SOC-1) are checked by the gate before anything is held (Action.check).
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from hub.connectors.base import Action, ActionError
from hub.engine import packs

PACK, CHANNEL = "social", "demo"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
MARKUP = set("|{}@[]()<>#\\*_~")  # never in an announcement field: no tags, links or formatting
PLAIN_RE = re.compile(r"[A-Za-z0-9 .,:!?'\-]*")  # an announcement field: plain ASCII words only
DOMAIN_RE = re.compile(r"[\w-]+\.[a-zA-Z]{2,}")  # looks like a web address
HOST_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")  # a plain host name
REDIRECT_PATHS = ("/redirect", "/attribution_link", "/url", "/out", "/away", "/l", "/r")


def _outbox() -> Path:
    return Path(os.environ.get("HUB_OUTBOX") or PROJECT_ROOT / "data" / "outbox") / "social-demo.md"


def _check_avoid(text: str) -> None:
    """P-SOC-1: never post about the channel's "never" topics (whole words)."""
    avoid = [t.strip().lower() for t in packs.setting(PACK, f"{CHANNEL}_avoid").split(",") if t.strip()]
    def found(topic: str) -> bool:  # whole words; "climate change" also across a line break
        words = r"[\s_\-]*".join(re.escape(w) for w in topic.split())  # also #ClimateChange, climate-change
        return bool(re.search(r"(?<!\w)" + words + r"(?!\w)", text.lower()))

    hit = [t for t in avoid if found(t)]
    if hit:
        raise ActionError(f"the post mentions a topic the user never posts about ({', '.join(hit)})")


def announcement_text(payload: dict[str, str]) -> str:
    """The fixed format of a routine announcement: the agent fills in fields, never free text."""
    for key in ("title", "line"):
        value = payload.get(key, "")
        # ASCII letters, digits, spaces and . , : ! ? ' - only, and nothing that looks like a web address (review M10).
        if not PLAIN_RE.fullmatch(value) or DOMAIN_RE.search(value) or "  " in value:
            raise ActionError(f"{key} must be one plain line: letters A-Z, numbers and . , : ! ? ' - only, "
                              "no links, tags or symbols")
    url = payload["link"].strip()
    # Printable ASCII, no backslash, @ or #: a browser reads "\\" as "/" and "#" would become a hashtag.
    if not url.isascii() or any(c in url for c in "\\@#") or any(not c.isprintable() or c.isspace() for c in url):
        raise ActionError("link must be one plain https web address (no spaces, \\, @ or #)")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        has_login = parts.username is not None or parts.password is not None
    except ValueError:
        raise ActionError("link must be one https web address") from None
    allowed = [d.strip().lower().lstrip(".") for d in packs.setting(PACK, f"{CHANNEL}_announce_domains").split(",")
               if d.strip()]
    if parts.scheme != "https" or not HOST_RE.fullmatch(host) or has_login:
        raise ActionError("link must be one https web address")
    # An allowed site must not pass the reader on: no address inside the ? part, no redirect pages (review M10).
    query, path = parts.query.lower(), parts.path.lower().rstrip("/")
    if any(x in query for x in ("http", "//", "%2f", "%3a", "www.")) or path.endswith(REDIRECT_PATHS):
        raise ActionError("link must go straight to your page, not through a redirect")
    if not any(host == d or host.endswith("." + d) for d in allowed):
        raise ActionError("an announcement may only link to the sites set on the Packs page "
                          f"({', '.join(allowed) or 'none set yet'})")
    title = payload["title"].strip().rstrip(".!?:,") or payload["title"].strip()
    return " ".join(f"New: {title}. {payload.get('line', '').strip()} {url}".split())


def check_post(payload: dict[str, str]) -> None:
    _check_avoid(payload["text"])


def check_announcement(payload: dict[str, str]) -> None:
    _check_avoid(announcement_text(payload))


def _write(kind: str, text: str) -> str:
    _check_avoid(text)  # checked again when it runs (a setting may have changed meanwhile)
    path = _outbox()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"## {kind} ({time.strftime('%Y-%m-%d %H:%M')})\n\n{text.strip()}\n\n")
    return f"Posted on the demo channel ({path.name})"


def post(payload: dict[str, str]) -> str:
    return _write("Post", payload["text"])


def post_announcement(payload: dict[str, str]) -> str:
    return _write("Announcement", announcement_text(payload))


ACTIONS = [
    Action(connector="social_demo", name="post", platform="demo_social",
           description="Post any text on the demo channel.",
           fields={"text": {"description": "The post, exactly as it should appear", "max": 3000}},
           required=("text",), run=post, check=check_post),
    Action(connector="social_demo", name="post_announcement", platform="demo_social",
           description="Post a routine announcement on the demo channel: a title, one line and a link to one of "
                       "the user's sites.",
           fields={"title": {"description": "What is new, for example the video title (one plain line)", "max": 120},
                   "line": {"description": "One short plain sentence about it", "max": 200},
                   "link": {"description": "The https web address", "max": 300}},
           required=("title", "link"), run=post_announcement,
           show=announcement_text,  # the approval shows exactly the text that will be posted (G-APPR-2)
           check=check_announcement),
]
