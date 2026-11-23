"""Jobs pack email submission (Milestone 11, P-JOB-4): the one way the bot applies by itself.

send_application emails a checked job's cover letter and tailored resume to the employer's
application address, from my mailbox (SMTP; the password is kept in Windows Credential Manager).
It is acting: every send goes through Approvals (installed as Ask), shows exactly what will be
sent, and only works for a job that
- passed the fact check (P-JOB-3: nothing invented),
- is on a site set to Auto (P-JOB-5; unknown sites are Prepare only, LinkedIn always is),
- asks for applications by email, and was never applied to before (P-JOB-7).
Web forms are never filled in by the bot: those jobs are prepared, and I submit them (P-JOB-4, P-JOB-6).
"""

from __future__ import annotations

import importlib.util
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path

from hub.connectors.base import Action, ActionError
from hub.engine import packs

_spec = importlib.util.spec_from_file_location("hub_pack_jobs__tracker_email", Path(__file__).with_name("_tracker.py"))
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)

FOLLOW_UP_DAYS = 7


def _ready(payload: dict[str, str]) -> dict:
    j = T.get(payload["job"])
    if j["status"] in ("applied", "sending") or j["applied_at"]:
        raise ActionError(f"job {j['id']} was already applied to: never twice")
    if j["status"] != "checked":
        raise ActionError(f"job {j['id']} has not passed the fact check (status: {j['status']})")
    if T.site_mode(j["url"]) != "auto":  # read again now: the list may have changed
        raise ActionError("this job's site is Prepare only: prepare it and hand it to the user")
    if j["apply_method"] != "email" or not T.EMAIL_RE.fullmatch(j["apply_email"]):
        raise ActionError("this job does not take applications by email")
    if not T.EMAIL_RE.fullmatch(packs.setting(T.PACK, "email_from")):
        raise ActionError("the sending address is not set on the Packs page")
    if payload.get("fingerprint", "").strip() != T.fingerprint(j):  # exactly the documents that were shown
        raise ActionError("the documents changed after this was asked for: it is not sent (ask again)")
    return j


def show(payload: dict[str, str]) -> str:
    """Exactly what will be sent (G-APPR-2)."""
    j = _ready(payload)
    return (f"Email to {j['apply_email']} from {packs.setting(T.PACK, 'email_from')}\n"
            f"Subject: Application: {j['title']}\n\n{j['cover_letter']}\n\n"
            f"Attached: resume.md (the tailored resume):\n{j['resume']}")


def check(payload: dict[str, str]) -> None:
    _ready(payload)


def send_application(payload: dict[str, str]) -> str:
    j = _ready(payload)
    host, user = packs.setting(T.PACK, "smtp_host"), packs.setting(T.PACK, "smtp_user")
    try:
        port = int(packs.setting(T.PACK, "smtp_port") or "587")
    except ValueError:
        raise ActionError("the mail server port on the Packs page is not a number") from None
    password = packs.secret(T.PACK, "smtp_password")
    if not host or not user or not password:
        raise ActionError("the mailbox is not set up on the Packs page (server, user and password)")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = packs.setting(T.PACK, "email_from"), j["apply_email"], f"Application: {j['title']}"
    msg.set_content(j["cover_letter"])
    msg.add_attachment(j["resume"].encode("utf-8"), maintype="text", subtype="markdown", filename="resume.md")
    context = ssl.create_default_context()
    with T.closing(T.db()) as conn, conn:  # claimed first: two approvals at once can never both send
        claimed = conn.execute("UPDATE jobs SET status = 'sending' WHERE id = ? AND status = 'checked' "
                               "AND applied_at IS NULL", (j["id"],)).rowcount
    if claimed != 1:
        raise ActionError(f"job {j['id']} is already being sent or was sent: never twice")
    sending = False  # True once the message itself is on its way: a failure after that is "unclear"
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30, context=context) as smtp:
                smtp.login(user, password)
                sending = True
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.starttls(context=context)  # never sends the password unencrypted
                smtp.login(user, password)
                sending = True
                smtp.send_message(msg)
    except Exception as exc:
        # Never the exception's own text: a login error can quote part of the password (review M11).
        refused = isinstance(exc, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused))
        if not sending or refused:  # nothing went out: it can be tried again
            T.update(j["id"], status="checked")
            why = {smtplib.SMTPAuthenticationError: "the mail server refused the login (check the user and password)",
                   UnicodeError: "the user name or password has characters the mail login cannot send"}
            reason = next((v for k, v in why.items() if isinstance(exc, k)),
                          "the address was refused" if refused else f"the mail server could not be reached ({type(exc).__name__})")
            raise ActionError(f"not sent: {reason}") from None
        # It may or may not have gone out: never sent again by itself. I check my sent mail and use the
        # Tracker button ("Reopen" puts it back as checked, "Mark submitted" records it as sent).
        T.update(j["id"], reason=f"sending was interrupted ({type(exc).__name__}): check your sent mail")
        raise ActionError(f"sending was interrupted ({type(exc).__name__}): it may have gone out") from None
    now = time.time()
    T.update(j["id"], status="applied", applied_at=now, follow_up=now + FOLLOW_UP_DAYS * 86400,
             reason=f"emailed to {j['apply_email']}")
    return f"Applied to job {j['id']} by email ({j['apply_email']}). Follow up on {T.day(now + FOLLOW_UP_DAYS * 86400)}."


ACTIONS = [
    Action(connector="jobs_email", name="send_application", platform="email",
           description="Email a checked job's cover letter and resume to the employer (only Auto sites that take "
                       "applications by email).",
           fields={"job": {"description": "The job number from the tracker", "max": 12, "number": True},
                   "fingerprint": {"description": "The fingerprint the job tool shows for these documents", "max": 40}},
           required=("job", "fingerprint"),
           run=send_application, show=show, check=check),
]
