# Task Tracking

Planned roadmap work lives in thread files at the repo root, `tasks-<thread>-thread.md`, one per
chunk of work. Each has a status table (`done` / `ready` / `blocked`, with dependencies) and a
subsection per task. Several threads can be open at once. This rule applies whenever you work on
a task defined in one of them ("your thread").

## Threads

- Edit only your thread's file. If your work affects another thread (makes a note stale, unblocks
  or overlaps one of its tasks), tell the user instead of editing that file.
- A cross-thread dependency names the other file, e.g. `LB-4 (tasks-labs-thread.md)`. Read it to
  check its status.
- Task-ID prefixes are unique across all threads, open and archived (`docs/roadmap-archive/`), and
  never bare numbers, which already mean the walking-skeleton chunk's "task N".
- Start or archive a thread only when the user asks. Archiving moves the file to
  `docs/roadmap-archive/<thread>.md`, verbatim apart from a header and relative links. Ask which
  open thread takes over its open Deferred items.

## What goes in

- Only tasks already in the file. One-off work, standalone features and bug fixes get no row,
  however large, and don't touch Deferred lists. Their reasoning goes in the service's
  `docs/architecture.md`, `docs/schema.md`, or the commit/PR. If something seems worth tracking,
  ask the user.
- A bug fix leaves `done` tasks' notes alone, even if it contradicts them.
- New work that a tracked task surfaces goes in the same thread, with its dependencies. New work
  that a bug fix surfaces (a capability never planned): ask which thread it belongs to.

## Updating

In the same change as the task, not as a follow-up:

- When a task is completed, mark it `done` and recompute its dependents. A task is `ready` only
  when every dependency, in any thread, is `done`.
- When scope changes (a task is split, merged or reordered), rewrite the tasks, not just their
  statuses.
- When a task is abandoned or superseded, mark it with a short reason; never delete it.
- Update the status table and the task's subsection together.
