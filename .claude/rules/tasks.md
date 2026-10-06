# Task Tracking

## Scope

`tasks.md`, at the repo root, is the roadmap for the current chunk of planned work, one row per
task with its dependencies and its status (`done` / `ready` / `blocked`). Its intro names the
chunk (currently the UI/UX chunk).

When every task in a chunk is `done` and the next chunk is planned, move the old `tasks.md` into
`docs/roadmap-archive/` (verbatim apart from relative links) and start a fresh one. Give each chunk
its own task-ID prefix (e.g. `UI-3`) so new IDs never collide with the bare "task N" references
to the walking-skeleton chunk that code comments and docs throughout the repo already use.

## When to Update

Update `tasks.md` in the same change, not as a follow-up, whenever:

- A tracked task is completed — flip its status to `done`, and recompute every task that depends
  on it: a task becomes `ready` only once all of its dependencies are `done`.
- Work on a task turns out to have a different scope than described (split, merged, or reordered)
  — update the task list itself, not just its status, so the document stays a true plan rather
  than a stale one.
- New work surfaces a task not yet in the file (a dependency neither architecture doc anticipated,
  a follow-up the current task revealed) — add it, with its dependencies, rather than doing the
  work untracked.
- A task is abandoned or superseded — mark it explicitly, with a short note on why, rather than
  deleting it silently, so the history of the plan stays legible.

## Bug Fixes Don't Belong Here

`tasks.md` tracks planned roadmap work, not defects. A bug fix gets no row and no subsection, and
it doesn't count as "new work surfacing a task", even when the fix is substantial or crosses
services. The same goes for a fix that contradicts something an already-`done` task's notes say:
leave those notes alone. Record the reasoning behind the fix where it already belongs: the
service's `docs/architecture.md` (if the fix is architectural), `docs/schema.md`, and the commit
or PR description.

If fixing a bug shows that genuinely new roadmap work is needed (a capability that was never
planned, not a correction to one that was), that follow-up is a task and goes in here as usual.

## How to Update

- Update the status table and the task's own subsection together — the table is the at-a-glance
  view, the subsection is where the detail (files touched, prerequisites) lives.
- Never mark a task `ready` while any of its dependencies are not `done`.
- A change that doesn't correspond to any tracked task needs no update here — not everything is
  tracked in this file — but a change that clearly *is* one of the tracked tasks must update it,
  the same discipline `architecture.md` applies to structural changes.

## Related

- Complements [`architecture.md`](architecture.md) and [`data-schema.md`](data-schema.md) — those
  own the reasoning behind a service's structure and data; this rule owns tracking which piece of
  the overall roadmap is done, in progress, or blocked.
