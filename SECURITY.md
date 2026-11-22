# Security policy

## Reporting a vulnerability

Please report security issues privately, not in a public issue or pull request.

Use GitHub's private reporting: on this repository's **Security** tab, choose **Report a
vulnerability**. That opens an advisory that only you and the maintainer can see.

Include what you found, how to reproduce it, and what you think the impact is. You'll get a reply
within a week. Stakehold is a hobby project with one maintainer, so a fix may take longer.

## Scope

Stakehold is built for learning and personal use, and it is not a hosted service. Reports are
welcome about the code in this repository, for example:

- one user reading or changing another user's data;
- authentication or session handling (`services/auth`);
- injection: SQL, template or prompt injection;
- secrets or credentials exposed by the code or its configuration.

Vulnerabilities in third-party dependencies are tracked through Dependabot. Report them upstream,
unless Stakehold uses the dependency in a way that makes the issue worse.

## Supported versions

Only the latest commit on `main` is supported. There are no releases.
