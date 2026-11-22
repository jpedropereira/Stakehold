# CLAUDE.md (agent)

The `agent` package hosts Stakehold's conversational agents. The first is **Pedro**, who reads a
user's portfolio through `portfolio`'s HTTP API and evaluates it. It is design only today: the
package is an empty skeleton. Read [`docs/architecture.md`](docs/architecture.md) before changing
anything structural, and update it in the same change (root
`.claude/rules/architecture.md`).

## What it owns, and what it doesn't

- Owns conversations, messages, runs, their traces and per-call consumption, and each user's
  AI-processing consent.
- Owns **no financial data**. Pedro never computes a return, a volatility or a backtest. It reads
  the figures `portfolio` computes, fetched *as the user* with the delegated token.
- Never writes. Actions are proposals that the user applies through `web`.

## Layering and the dependency rule

```text
api/ -> runtime/ -> context/ -> personas/
                 -> tools/ -> clients/
                 -> models/ -> models/adapters/
                 -> observability/
api/ -> conversations/, consent/
```

Arrows point inward. `runtime/` (`AgentRunner`) doesn't know HTTP, SSE or Postgres exist: it
receives a `ChatModel`, a toolset and an assembled context, and yields `AgentEvent`s. Two drivers
run the same runner, the API and the eval harness, so a loop that depended on the transport would
make the evals test something other than what ships.

- No vendor type crosses the `ChatModel` port. Only `models/adapters/` imports a provider SDK.
- `agent` never imports `agent_evals` (the dependency runs the other way), nor `pydantic_ai`.
- Persistence modules split into a domain model, a repository that only executes SQL, and a
  service that owns the transaction boundary.
- `api/` is `async def` throughout. Don't call the sync `shared.db.pool` from a route.

## Rules

Scoped rules live in `.claude/rules/`: prompt injection, tools, evals required, consumption,
conversation recording. They apply on top of the root rules.

## Tests

```bash
uv run pytest services/agent                      # unit tests
uv run pytest services/agent -m integration       # needs docker-compose.test.yml, see root CLAUDE.md
```

- Unit tests live in `services/agent/tests/unit/`, with **no `__init__.py`** (root `CLAUDE.md`).
- Drive the runner with a scripted fake `ChatModel`, so loop behaviour is deterministic and free.
- Tests never call a real model. Evals are not tests: they live in `agent-evals`, cost money, and
  run only by hand.
