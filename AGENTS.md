# Agent guide

Instructions for AI coding agents working in this repository.

## What this is

`wrapkit` is the shared infrastructure of the data-API wrappers in
`github.com/hmorao95` (logging and redaction, disk cache, retries and throttle,
tidy frames, incremental updates, file output, python-fire helpers). The
wrappers depend on it by git tag. The repository is public: it must only hold
generic code, never a key, token, endpoint secret or anything specific to one
data source.

Layout: `src/wrapkit/` (one module per area, see the README), `tests/`
(network-free pytest suite).

## Setup

```bash
uv sync
```

`.python-version` pins the local environment to Python 3.12, the oldest version
that reads `.pth` files as UTF-8. On Windows, 3.10 and 3.11 read them in the
ANSI code page, so the editable install breaks when the clone's path has
non-ASCII characters and `import wrapkit` fails. The supported floor is still
3.10: CI runs the checks on 3.10 to 3.13, with `UV_PYTHON` set from the matrix.
Keep that variable. A `--python` flag on `uv sync` alone does not hold, because
the next `uv run` reads `.python-version` and rebuilds `.venv` on 3.12.

To test 3.10 or 3.11 locally, use a separate environment with `src` on the
path, e.g.
`UV_PROJECT_ENVIRONMENT=$LOCALAPPDATA/Temp/wrapkit310 PYTHONPATH=src uv run -p 3.10 pytest`.

## Quality gate (must pass before every commit)

CI runs exactly these on Python 3.10 to 3.13:

```bash
uv run ruff check .
uv run ruff format --check .
uv run codespell src tests README.md CHANGELOG.md
uv run deptry src
uv run interrogate -c pyproject.toml src tests
uv run mypy
uv run ty check
uv run pytest            # coverage >= 95%
```

Do not weaken the gate to make it pass.

## Conventions

- Google-style docstrings with `Args:` / `Returns:` / `Raises:`; `mypy --strict`;
  `from __future__ import annotations`; exception messages go in a `msg`
  variable first.
- Behaviour is part of the contract: the wrappers' own test suites rely on it.
  Changing a default, a log line or an error message is a breaking change for
  them; note it in the changelog.
- Text the user sees in a wrapper (messages in Portuguese for ine-pt-api, for
  example) comes from the caller; wrapkit's own messages are English.
- Tests never hit the network.

## Git and releases

- Branch, open a PR, squash-merge once CI is green.
- **Commit as the repo owner.**
- Releasing: move `[Unreleased]` under `## [X.Y.Z] - <date>`, bump the version
  in `pyproject.toml` and `src/wrapkit/__init__.py`, `uv lock`, merge, then
  `git tag -a vX.Y.Z` and `gh release create vX.Y.Z`. Wrappers then bump their
  pin, one PR each.
- wrapkit is not on PyPI. A wrapper that depends on it by git URL cannot be
  uploaded to PyPI (PyPI rejects direct references); publish wrapkit to PyPI
  first if that is ever needed.
