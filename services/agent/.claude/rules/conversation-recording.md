# Conversation recording (agent)

Every conversation is recorded, so any answer can be explained later.

- Save the user's message before the run starts. Save the assistant's message and the run when the
  run ends, whatever the outcome. A crash leaves a failed run, never a missing turn.
- Keep partial text from an aborted stream, marked incomplete.
- No code path runs a model call without a recorded run. Evals are the exception: they never write
  to the production tables.
- Content goes to the database, never to container logs.
- Retention and deletion aren't decided. Ask the owner; don't pick a default in code.
