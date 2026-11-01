# The command line

Every capability of the engine is reachable from `corral`. Run any command with `--help` for its options.

## Running the fleet

Start the controller:

```sh
corral controller --host 127.0.0.1 --port 8787 [--dashboard] [--db corral.db] [--token SECRET]
```

- `--dashboard` also serves the read-only web board at `/`.
- `--db` persists jobs and tasks to SQLite so they survive a restart. Without it they live in memory only.
- `--token` requires that token of every worker and API client. Without it the controller runs open, which is why it binds to localhost by default.

Start a worker on each machine you want in the fleet:

```sh
corral worker --controller ws://CONTROLLER:8787 --label linux --label docker [--node-id box1] [--token SECRET]
```

Labels are free-form tags a job selector can match. A worker advertises its OS, architecture and whether it has Docker automatically.

## Submitting work

Fan a command out to every matching node and print each result:

```sh
corral run --executor docker --image alpine:3.20 --select-label docker -- uname -a
corral run --executor local --os linux -- id
```

Everything after `--` is the command. Useful options: `--executor local|docker`, `--image`, `--network` (off by default for docker), `--shell`, `--timeout`, `--os`, `--arch`, `--select-label`, `--name`, `--no-wait`.

## Looking at the fleet and jobs

```sh
corral nodes            # the live fleet
corral jobs             # recent jobs, newest first
corral job JOB_ID       # one job with its tasks and logs
corral cancel JOB_ID    # cancel a running job
```

## Workloads

Run a test command across every matching node and get pass or fail per node:

```sh
corral testrun --image python:3.12-slim --select-label test -- pytest -q
```

Reproduce a published CVE in a disposable sandbox and write a defender's report. See [cve.md](cve.md):

```sh
corral cve --recipe recipe.json --out report.md
```

## The agent surface

Start the MCP server so an AI agent can drive all of the above:

```sh
corral mcp                                   # stdio
corral mcp --transport streamable-http --port 8765
```

## Authentication for the client commands

The read and submit commands pick up a token from the `CORRAL_TOKEN` environment variable, so you can export it once:

```sh
export CORRAL_TOKEN=SECRET
corral nodes
```
