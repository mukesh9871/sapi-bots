---
name: Job applications
version: 1.0
description: A scout that shortlists jobs from your sources, a writer that tailors your resume and letter without inventing anything, a fact checker, and a tracker.
views: jobs_tools.view_tracker | Tracker
row_actions: jobs_tools.mark_submitted | Mark submitted, jobs_tools.reopen | Reopen
---
Pack 1 (Milestone 11). Roles: job scout, resume writer, fact checker, application filler,
plus a job lead for the application team. Open the **Tracker** from this page.

**Set it up (Settings below):** your master resume (a .md or .txt file), skills, target
titles, locations, salary range, companies to exclude (P-JOB-1), the sources to search, and
the mode per site (P-JOB-5): one per line, `site: auto`, `site: prepare` or `site: skip`.
Unknown sites are Prepare only. LinkedIn is always Prepare only: it bans bots (P-JOB-4).
Do not list LinkedIn (or a site you set to Skip) as a source: the scout is told never to
read them, because they do not allow bots.

**How applying works:**
- *Prepare only* (the default): the bot tailors the documents, the fact checker compares
  them with your profile, and the job waits in the tracker as "ready for you". You submit it.
- *Auto*: only for jobs that ask for applications **by email**. The bot emails your letter
  and resume from your mailbox (set the mail server below; the password is kept in Windows
  Credential Manager). Every email goes through Approvals, starting as Ask, and shows exactly
  what will be sent. Review at least ten prepared applications before you set any site to Auto.
- The bot never fills in web forms and never tries to get past a CAPTCHA. At a CAPTCHA, a
  login check, a legal declaration or a question your profile cannot answer, it hands the
  job to you: it shows as "needs you" in the tracker (P-JOB-6).
- No job is added or applied to twice (P-JOB-7). In the Tracker, **Mark submitted** records
  a job you submitted yourself (applied date and a follow-up in a week). **Reopen** returns a
  job you dealt with after a hand-over to the shortlist, or an interrupted email that did not
  go out (check your sent mail) to "checked" so it can be sent again.

**Schedules (installed switched off):** the daily scout (07:00) and the daily application
round (09:00, the application team).
