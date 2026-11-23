"""Social pack LinkedIn connector (Milestone 10): posts through LinkedIn's official Posts API (P-SOC-6).

POST https://api.linkedin.com/rest/posts with headers Linkedin-Version (YYYYMM) and
X-Restli-Protocol-Version 2.0.0; a text post as the member set on the Packs page; success is
201 with the post id in the x-restli-id header. The access token (w_member_social) is kept in
Windows Credential Manager and read only here, at the moment of posting: never shown to an
agent, written to a file or logged (N-SEC-6).
- The commentary is LinkedIn "little text": every reserved character is escaped, so the post
  reads exactly as approved and cannot tag anyone (a #hashtag stays a hashtag).
- post: any text (suggested rule Ask).
- post_announcement: a routine post put together here in a fixed format (suggested Auto). Its
  fields are one plain line each (plain words, which can still name someone: the rule
  for announcements is mine to set) and its link must go to a site I listed (P-SOC-5).
The "never" topics (P-SOC-1) are checked by the gate before anything is held (Action.check).
HUB_LINKEDIN_API moves the API address (the tests use a local stand-in server).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from hub.connectors.base import Action, ActionError
from hub.engine import packs

PACK, CHANNEL = "social", "linkedin"
API_VERSION = "202610"  # LinkedIn keeps each monthly version for about a year
MAX_TEXT = 3000  # LinkedIn's limit for commentary
AUTHOR_RE = re.compile(r"urn:li:person:[A-Za-z0-9_-]{1,100}")
MARKUP = set("|{}@[]()<>#\\*_~")  # LinkedIn little-text reserved characters
PLAIN_RE = re.compile(r"[A-Za-z0-9 .,:!?'\-]*")  # an announcement field: plain ASCII words only
DOMAIN_RE = re.compile(r"[\w-]+\.[a-zA-Z]{2,}")  # looks like a web address
HOST_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")  # a plain host name
REDIRECT_PATHS = ("/redirect", "/attribution_link", "/url", "/out", "/away", "/l", "/r")


def _api() -> str:
    return os.environ.get("HUB_LINKEDIN_API", "https://api.linkedin.com").rstrip("/")


def little_text(text: str) -> str:
    """Escape every little-text reserved character with a backslash, except # starting a hashtag."""
    out = re.sub(r"([|{}@\[\]()<>\\*_~])", r"\\\1", text)
    # A # stays a hashtag only at the start of a word: "#testing" yes, "v#chapter2" or "# x" no.
    return re.sub(r"#(?!\w)|(?<=\S)#", r"\\#", out)


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


def _publish(text: str) -> str:
    _check_avoid(text)  # checked again when it runs (a setting may have changed meanwhile)
    author = packs.setting(PACK, "linkedin_author")
    if not AUTHOR_RE.fullmatch(author):
        raise ActionError("LinkedIn is not set up: the user sets the member URN on the Packs page")
    token = packs.secret(PACK, "linkedin_token")
    if not token:
        raise ActionError("LinkedIn is not connected: the user sets the access token on the Packs page")
    body = {"author": author, "commentary": little_text(text.strip()), "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                             "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
    req = urllib.request.Request(f"{_api()}/rest/posts", data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Linkedin-Version": API_VERSION,
                                          "X-Restli-Protocol-Version": "2.0.0", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 201:
                raise ActionError(f"LinkedIn answered {resp.status}")
            post_id = resp.headers.get("x-restli-id", "")
    except urllib.error.HTTPError as exc:  # the token never appears in the message
        why = {401: "the access token is missing or expired", 403: "the token lacks w_member_social",
               422: "LinkedIn could not use this post", 429: "too many posts: LinkedIn's limit"}.get(exc.code, "")
        raise ActionError(f"LinkedIn refused it ({exc.code}{': ' + why if why else ''})") from None
    except urllib.error.URLError as exc:
        raise ActionError(f"could not reach LinkedIn ({exc.reason})") from None
    return f"Posted on LinkedIn ({post_id or 'no id returned'})"


def post(payload: dict[str, str]) -> str:
    return _publish(payload["text"])


def post_announcement(payload: dict[str, str]) -> str:
    return _publish(announcement_text(payload))


ACTIONS = [
    Action(connector="social_linkedin", name="post", platform="linkedin",
           description="Post any text on LinkedIn as the user (official Posts API).",
           fields={"text": {"description": "The post, exactly as it should appear", "max": MAX_TEXT}},
           required=("text",), run=post, check=check_post),
    Action(connector="social_linkedin", name="post_announcement", platform="linkedin",
           description="Post a routine announcement on LinkedIn: a title, one line and a link to one of the "
                       "user's sites.",
           fields={"title": {"description": "What is new, for example the video title (one plain line)", "max": 120},
                   "line": {"description": "One short plain sentence about it", "max": 200},
                   "link": {"description": "The https web address", "max": 300}},
           required=("title", "link"), run=post_announcement,
           show=announcement_text,  # the approval shows exactly the text that will be posted (G-APPR-2)
           check=check_announcement),
]
