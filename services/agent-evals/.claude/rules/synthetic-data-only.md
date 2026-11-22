# Synthetic data only (agent-evals)

## Worlds are made up

A world is a frozen, **synthetic** portfolio: the JSON responses `portfolio`'s API would return for
one user. Write it by hand or generate it.

- Never copy a world from a real portfolio, the dev database or the dev stack's API.
- Never use real broker names, real figures or personal data (the repo is public).
- Don't derive a world by anonymizing a real one. The shape can match real responses, the values
  must not come from them.

## Why

The dev stack holds real portfolios. A dataset built from them would be committed to a public repo,
and every run would send them to each model provider under test.

## No live services

Worlds are served by the fake `PortfolioClient`. Evals need no running services and no database,
and never read a `*DATABASE_URL` or a `.env` file.

Promoting a real conversation into a case (tier 3, later) needs the user's consent and a synthetic
replacement of the real portfolio, as a policy to define then. Don't do it before that exists.
