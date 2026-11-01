# Architecture

Corral is one engine behind several surfaces. The engine knows nothing about how it is called; the CLI, the MCP server and the dashboard are thin wrappers over it.

## The pieces

```
                    ┌──────────────────────────┐
  CLI / MCP / API → │        CONTROLLER         │
                    │  node registry (in memory)│
                    │  job + task store (sqlite)│
                    │  selector-based fan-out   │
                    └────────────┬─────────────┘
                     WebSocket (workers dial in)
            ┌─────────────┬──────┴───────┬─────────────┐
        ┌───┴───┐     ┌───┴───┐      ┌───┴───┐     ┌───┴───┐
        │worker │     │worker │      │worker │     │worker │
        │ linux │     │ linux │      │windows│     │ macos │
        │+docker│     │+docker│      │  vm   │     │  vm   │
        └───────┘     └───────┘      └───────┘     └───────┘
```

- The **controller** is the only stateful process. It holds the live node registry in memory, because a node is only real while its socket is open, and persists jobs and tasks to SQLite when a database path is given.
- A **worker** is a dumb executor. It dials into the controller, advertises what it is, pulls task assignments, runs each one through an executor, streams the output back, and reports the result. It holds no job logic of its own, so everything it does is decided by the controller and every environment it creates is disposable.
- An **executor** is how a worker runs a job. `local` runs on the worker host; `docker` runs in a fresh container that is removed on exit and network-isolated by default. Windows and macOS snapshot-revert VM executors are the planned next worker types.

## Why workers dial in

Each worker opens the connection to the controller, rather than the controller reaching into each worker. A worker behind NAT, on a laptop, or in a cloud VM then needs no inbound firewall hole. It also means the controller can restart under a running fleet: the workers reconnect with backoff.

## Fan-out

A job carries a selector: an OS, an architecture, a set of labels, an executor, or specific node ids. The controller matches the selector against every live node and creates one task per match. An empty selector matches every online node that supports the job's executor. A job that matches nothing is recorded as `no_match`, so a caller can tell "ran nowhere" from "not submitted".

## The data model

Three records, all JSON-serialisable so the WebSocket protocol and the REST API carry them unchanged:

- **Node**: a connected worker and what it advertises.
- **Job**: a submitted unit of work and its fan-out of tasks.
- **Task**: one execution of a job on one node, with its state, result and captured log.

## Workloads

A workload is a job template that rides on the fan-out spine. The cross-platform test runner builds one job and shapes the per-node pass or fail. The CVE reproduction workload builds probe jobs for the affected and patched environments and assembles a report. Both use only the public controller API, so anything a workload does, you can do by hand with `corral run`.
