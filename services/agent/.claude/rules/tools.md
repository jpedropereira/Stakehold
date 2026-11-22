# Tools (agent)

## Read-only

A tool is either a *read tool* (calls `portfolio`) or a *proposal tool* (returns an
`ActionProposal` and never performs it). No tool writes. `agent` has no write scope, so no
injection can change a user's data.

## Identity comes from `ToolContext`, never from arguments

`ToolContext` carries identity (`user_id`, the delegated token) and page context. A tool that needs
"whose portfolio" reads it from the context.

- No tool input model has a `user_id`, `account`-owner or token field the model could set.
- When a tool needs a resource id (a portfolio, a backtest), `portfolio` still re-verifies that the
  token's user owns it.

That removes a whole class of escalation by construction: there is no argument the model could set
to another user's id.

## Shape

- A tool is a `Tool` Protocol implementation: name, description, a pydantic input model (its JSON
  Schema is generated, never hand-written), and `async run(ctx, args) -> ToolResult`.
- A `ToolResult` holds content for the model plus structured data for graders.
- A tool failure is returned to the model as a tool result saying it failed. Don't swallow it and
  don't raise it through the loop.
- A tool never fetches an arbitrary URL.
- Tools reach `portfolio` only through the `PortfolioClient` Protocol, so evals can swap in a fake.
