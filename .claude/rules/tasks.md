# Task Tracking

## Scope

`tasks.md`, at the repo root, is the cross-service roadmap tracking the path to a basic working
app across all four services — one row per task, its dependencies, and its status (`done` /
`ready` / `blocked`).

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
