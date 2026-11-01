# Contributing

## Setup

Corral uses a per-project uv virtual environment. Do not install into a global interpreter.

```sh
uv venv
uv pip install -e ".[dev]"
```

## Checks

Before pushing, run the same checks CI runs:

```sh
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy src/corral
.venv/bin/pytest
```

## Branches and commits

Development happens on branches, not straight on `main`. Keep prose unwrapped (one physical line per paragraph) and do not use em dashes. Pull requests run the full CI matrix on Linux, Windows and macOS before merge.
