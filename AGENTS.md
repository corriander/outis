# Working in this repository

This file is for automated coding agents. Start with
[CONTRIBUTING.md](CONTRIBUTING.md) — all of it applies to agent work. What
follows is extra context for agents.

**References policy: this is a public-facing repo and we currently don't use
the issue tracker. Ensure anything published here cites only what a reader of
this visible repository can see (files and pull requests, or upstream references).
Never cite private notes, issue tracking paths or refs, local filesystem or integration
infrastructure or session content. This applies to code, comments, commit messages, PRs
or any other "publication".**

## Read first

- [CONTRIBUTING.md](CONTRIBUTING.md) — branch model, commit format, visual
  style rules, code conventions, release automation.
- [FORK.md](FORK.md) — upstream selection policy and the public integration
  boundary.
- [LICENSING.md](LICENSING.md) — licence obligations for anything you add.

## Environment

Install with pip against `requirements.txt`, as `CONTRIBUTING.md` describes.
uv works as a drop-in for the venv and install steps, but only through its pip
interface:

```bash
uv venv venv
uv pip install -r requirements.txt
```

Do not use `uv run` or `uv lock`. This is not a uv-managed project, and both
commands write a `uv.lock` into the repository root as a side effect. That file
is gitignored and unwanted — and it is self-punishing: once one exists, every
later uv command treats it as stale and re-resolves the whole dependency set,
turning a one-second invocation into a multi-minute one.

## Tests

```bash
python -m pytest
```

Never substitute `uv run pytest`. The suite is large — several thousand tests
and minutes of wall time, a noticeable share of it in collection alone — so run
the smallest relevant selection while iterating and the full suite once before
proposing a change.

Many `tests/test_*_js.py` cases shell out to `node`, so a missing or shimmed
`node` appears as a broad band of unrelated failures. Establish a baseline on
an unmodified checkout before attributing any failure to your change.

## Commits and history

- Conventional Commits, per `CONTRIBUTING.md`. Subjects feed release
  automation: `chore:` neither appears in the changelog nor moves the version.
- Keep a commit's contents inside what its subject claims. A stray file swept
  into an unrelated commit is very hard to find afterwards.
- LF line endings only; CI rejects CRLF.
- When carrying an upstream commit across, preserve its authorship: use
  `git cherry-pick -x`, and do not squash it into a commit of your own. The
  licence depends on that attribution surviving.

## Issues and planning

Planning and backlog for this fork are curated outside this repository. Do not
open issues to track intended work, record findings, or park to-dos. As
`FORK.md` puts it, the public issue and pull-request history is part of the
project's published documentation: an issue should be a self-contained problem
or implementation slice, useful to someone who has only this repository.
Propose in review; do not file unprompted. Note github issues are currently disabled.
