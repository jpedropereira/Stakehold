# Evals required (agent)

Pedro's behaviour is statistical, so unit tests can't tell you whether a change made it better or
worse. Evals can, but only if every change that could move behaviour is measured.

## Every prompt or workflow change ships its eval, or updates the baseline

These changes can move Pedro's behaviour:

- a prompt or persona text;
- the model, or the model policy;
- a tool (its description, schema or result shape);
- the agent loop, or context assembly.

Such a change either adds or updates an eval case in `services/agent-evals` that covers it, or
updates the stored baseline with the new result, and says which in the PR. A PR that changes any of
the above and does neither isn't done.

## What counts as covered

- A new tool: a case where it's required, and one where it fails and Pedro says so.
- A new data source or content type: a case with an injection hidden in it.
- A fixed behaviour bug: a case that fails before the fix.

## Running evals costs money

Don't start a paid eval run yourself. Say which suite should be run and why, and let the user start
it. See `services/agent-evals/.claude/rules/cost.md`.

A free smoke run with the scripted fake model checks the harness wiring and is fine to run.
