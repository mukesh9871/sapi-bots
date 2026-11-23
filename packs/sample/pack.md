---
name: Sample pack
version: 1.0
description: Shows the pack format: two roles, a team, a connector, a suggested rule and a schedule.
---
A small pack to show how packs work (Milestone 8). Install it, and the Planner and
Note taker roles, the Note duo team and the "add a note" connector appear. Remove it,
and they are gone again. The engine is never changed.

- The Note taker can add a note to `data/outbox/sample-notes.md`. That counts as an
  outside action, so it goes through your approval rule (installed as Ask).
- The "Morning note" schedule is installed switched off. Turn it on in Schedules.
