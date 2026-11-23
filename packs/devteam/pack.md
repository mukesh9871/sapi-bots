---
name: Software team
version: 1.0
description: A manager, an architect, two developers, two testers and a reviewer build a small program in a git project folder.
---
Pack 4 (Milestone 12). Give the **Software team** a task on the Teams page, for example "a
command-line tool that counts words in a text file, with tests".

**How it works (P-DEV-1):** the manager plans and makes the git repository, the architect
writes the design (DESIGN.md), developers build in their own branches, testers write and run
the tests, the reviewer reviews each branch, and the manager merges and reports.

- **Git (P-DEV-2):** everything happens in the team's project folder (`projects/team-<run>/`)
  under git, so every change is a commit you can read and undo. Commits are signed with the
  role that made them.
- **Commands only in the folder (P-DEV-3):** the team gets git actions for its own folder
  only. Running the tests runs code the team wrote on your PC with your rights, so it goes
  through Approvals as Ask. Keep it on Ask. The tests run on a throwaway copy of the exact
  commit, outside the project. Pushing to a remote is Ask.
  Installing software or touching anything outside the folder is not offered at all: the
  team hands that to you.
- **No one approves their own work (P-DEV-4):** a branch is merged into main only when its
  exact latest commit passed its tests and was accepted by a reviewer who did not make that
  commit.
- Test files are named `check_*.py` (with `test_` functions inside): the hub never lets an
  agent write `test_*.py` or `conftest.py`, so the hub's own tests can never run them.
- One member works at a time: they share one working folder.
