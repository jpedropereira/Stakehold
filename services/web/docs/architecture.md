# web architecture

**Status: scaffolding — not yet implemented.**

## Responsibility

`web` is the user-facing surface of Stakehold. It renders the application in the browser and
calls [`portfolio`](../../portfolio/docs/architecture.md), `data-store`, and
[`auth`](../../auth/docs/architecture.md) on the user's behalf — it owns no data of its own.

## Stack

- **FastAPI + Jinja2** — server-rendered HTML, not a separate JS/TS SPA.
- **htmx** for dynamic behavior (partial page updates, form submissions without full reloads) —
  no client-side framework, no build step, no bundler.
- **Plain CSS** with custom properties (`--color-*`, `--font-*`) for the brand tokens — no
  Tailwind, no component kit, no CSS framework. Numbers render in `--font-mono` (IBM Plex Mono)
  per the brand system.
- A **lightweight JS charting library**, loaded via a `<script>` tag for specific views (returns
  over time, allocation) — not a framework, just a library dropped in where needed.

## Key decisions

- **Server-rendered, not an SPA.** The developer isn't frontend-strong, and a same-origin
  server-rendered app means no auth token ever needs to reach browser JS.
- **htmx over a client-side framework.** Gets dynamic UI behavior without a build pipeline.
- **web is the only client of `portfolio`/`data-store`/`auth`, for now.** This is a documented
  assumption, not a guarantee — it's what justifies `auth`'s cookie-based (rather than
  bearer-token) session model. Revisit if a mobile app or third-party API consumer ever shows up.
- **web calls the other services server-to-server, never from browser JS**, forwarding the user's
  session identity as needed.
- **CSRF protection is web's responsibility, not `auth`'s**, since web is the one rendering the
  HTML forms on state-changing routes.

## Talks to

- **`auth`** — login, logout, signup, and per-request session validation calls.
- **`portfolio`** — transactions, positions, and returns.
- **`data-store`** — market data reads, once its read API exists.

All calls are server-to-server; the user's session identity is forwarded as needed, never exposed
to browser JS.

## Open questions

- Deployment shape for `web` is undecided — `data-store` is the only service with a deployment
  story so far.
- Whether `web` will ever need to serve non-browser clients, which would push `auth` toward also
  supporting bearer tokens alongside cookie sessions.
- Exact API contracts with `portfolio`, `data-store`, and `auth` — none of those services' HTTP
  APIs exist yet.
- **Service-to-service trust**: how `portfolio`/`data-store` authenticate that a forwarded user
  identity genuinely came from `web` rather than any network-reachable caller — see
  [`auth`'s architecture doc](../../auth/docs/architecture.md#open-questions) for the candidate
  mechanisms, none chosen yet.
