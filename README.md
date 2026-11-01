<div align="center">

<img src="docs/screenshots/logo.png" alt="Corral" width="120" />

<h1 align="center">Corral</h1>

<p align="center">
A <b>controller</b> that herds a fleet of disposable <b>worker nodes</b> across machines and operating systems, and fans a job out to every node that matches. Drive it from a <b>CLI</b>, from an <b>MCP server</b> so an AI agent can run the fleet, or from a <b>web dashboard</b>. Built for work that a single session on a single machine cannot do: cross-platform testing, and reproducing a published vulnerability inside a disposable, network-isolated sandbox.
</p>

</div>

## Why

A Claude session, or any single agent, lives on one machine with one operating system. A lot of real work does not fit there. You want to run the same test on Linux, Windows and macOS at once. You want to reproduce a published CVE against a specific vulnerable version in a throwaway environment, confirm a patched version is not affected, and keep nothing behind. One box cannot be all of those at the same time.

Corral splits the problem. A long-running **controller** holds a registry of **workers** that dial in from wherever they run. Each worker is a dumb executor: it advertises what it is (os, arch, whether it has Docker, free-form labels), pulls a job, runs it in a disposable environment, streams the logs back, and returns the result. The controller matches each job against the fleet and fans it out. Add a worker on another OS and that OS is now reachable, with no change to anything else.

## Status

Alpha, built milestone by milestone.

- **Working now:** the controller, the WebSocket worker protocol, the `local` and `docker` executors, job fan-out across every matching node, live log streaming, and the CLI and MCP surfaces over all of it.
- **Scaffolded next:** the read-only web dashboard, snapshot-revert VM workers for Windows and macOS, and the defensive CVE-reproduction job template. These are present as clearly marked stubs with a design, not finished features.

See [CHANGELOG.md](CHANGELOG.md) for what landed when.

## Surfaces

| Interface | Command | For |
|---|---|---|
| CLI | `corral controller` / `corral worker` / `corral run ...` | running the fleet and scripting jobs |
| MCP server | `corral mcp` (or `corral-mcp`) | AI agents (Claude Desktop, Claude Code, Cursor, any MCP client) |
| Web dashboard | `corral controller --dashboard` | watching nodes, jobs and logs in a browser |

## Install

Corral uses a per-project [uv](https://docs.astral.sh/uv/) virtual environment. Nothing is installed globally.

```sh
uv venv
uv pip install -e ".[dev]"
```

## Quick start

Open three terminals, all in the project, each using the project venv.

```sh
# 1. the controller
.venv/bin/corral controller --host 127.0.0.1 --port 8787

# 2. a worker that can run Docker containers (label it however you like)
.venv/bin/corral worker --controller ws://127.0.0.1:8787 --label linux --label docker

# 3. a second worker, to prove fan-out
.venv/bin/corral worker --controller ws://127.0.0.1:8787 --label linux --label docker

# fan one command out to every matching Docker worker and collect both results
.venv/bin/corral run --executor docker --image alpine:3.20 -- uname -a
```

## Security posture

Corral runs commands you give it on machines you control. It is built for authorized work: your own test matrix, or reproducing a published vulnerability against infrastructure you are permitted to test.

- The controller binds to `127.0.0.1` by default.
- Docker jobs run in a **disposable** container that is **network-isolated by default**; opt into a network per job.
- The MCP server never runs a command until an operator-approved worker is connected, and it honors the same isolation defaults.
- No telemetry, no phone-home.

See [SECURITY.md](SECURITY.md).

## Docs

Per-surface docs live under [`docs/`](docs/): the CLI, the [MCP server](docs/mcp.md), the worker protocol, and the CVE-reproduction workflow design.

## License

MIT, see [LICENSE](LICENSE).
