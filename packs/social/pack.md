---
name: Social media manager
version: 1.0
description: Daily trend report, weekly content calendar, post drafts, posting through official APIs (LinkedIn first) and a weekly summary.
---
Pack 2 (Milestone 10). Roles: trend analyst, content writer, publisher, analytics
reporter, plus a social lead for the team.

**Channels.** LinkedIn posts through LinkedIn's official Posts API (P-SOC-6). "Demo
channel" writes to `data/outbox/social-demo.md` so you can try everything without an
account. More platforms follow one at a time, each after its automation rules are checked.

**Set it up (Packs page, Settings):**
- The brand profile per channel: niche, voice, audience and topics never to post about
  (P-SOC-1). A post that mentions a "never" topic (as a whole word) is refused before it
  reaches Approvals.
- For announcements: the sites they may link to (for example your YouTube channel).
- For LinkedIn: your member URN (`urn:li:person:...`) and an access token with the
  `w_member_social` permission from your LinkedIn developer app. The token is kept in
  Windows Credential Manager, never in a file, a log or an agent's context.

**Approvals (P-SOC-5).** Two kinds of post per channel:
- *Post* (any text): Ask. Use this for replies, opinions and anything naming a person or brand.
- *Announcement* (a routine post you defined: a title, one line and a link, put together
  by the connector in a fixed format with no tags, symbols or other links): suggested Auto,
  installed as Ask. Its words are still plain text that could name someone, so switch it to
  Auto only when you trust the publisher's announcements. Switch it to Auto
  in Settings when you are happy with it, as the first Auto step.

**AI labels (P-SOC-8).** Version 1 posts text only. If you add AI-generated images or
video later, label them as each platform requires.

**Schedules (installed switched off):** daily trend report (08:00), weekly content
calendar (Mondays 08:30), weekly analytics summary (Fridays 17:00).
