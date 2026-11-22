# Prompt injection (agent)

`agent` is the most injection-exposed service in the system. Assume the model *will* sometimes
follow an injected instruction, and build so that this achieves nothing.

## Every piece of content the app didn't generate goes in a labelled block

Anything we didn't write ourselves is data, not instructions:

- the user's messages;
- tool results (broker-imported instrument names and descriptions, account labels, ticker
  metadata from `data-store`'s providers);
- later, retrieved documents.

Put each piece into a block that names its origin and says it is untrusted content. Never splice it
into the system prompt or into the persona's own text. The context assembler (`context/`) is the
one place that builds these blocks; don't hand-build prompt strings elsewhere.

## Labels are not the defence

A label reduces the chance the model follows an injection; it doesn't prevent it. The real limits
are structural: identity comes only from the verified token, there is no write scope, no tool
fetches arbitrary URLs, and `web` renders model output with raw HTML off and same-origin links
only. Don't weaken one of those because "the prompt tells the model not to".

## Keep secrets out of the model's reach

The delegated token, the session cookie and provider API keys never enter model context, logs or
traces.

## A new tool or data source is new attack surface

Repeat the security review in `docs/architecture.md` when you add one, and add an eval case with an
injection hidden in the new content (see `evals-required.md`).
