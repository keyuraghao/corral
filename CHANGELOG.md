# Changelog

All notable changes to Corral are documented here. The format follows Keep a Changelog, and the project uses semantic versioning. The newest entries are at the top.

## [Unreleased]

## [0.1.0] - 2026-11-01

### Added
- Controller daemon: a live, in-memory node registry, a selector-based fan-out that creates one task per matching node, task tracking to a terminal state, and a live event stream for views.
- Worker: a dumb executor that dials into the controller over WebSocket, advertises its OS, architecture, executors and labels, runs assignments, streams logs, and reconnects with backoff.
- Executors: `local` runs on the worker host; `docker` runs in a disposable container that is removed on exit and network-isolated by default. Both honor a timeout and support cancellation that leaves nothing behind.
- CLI: `controller`, `worker`, `run`, `nodes`, `jobs`, `job`, `cancel`, `testrun`, `cve`, and `mcp`.
- MCP server over the controller API, supporting stdio, streamable-http and sse, with tools for listing nodes, running and submitting jobs, inspecting and cancelling them, the cross-platform test matrix, and defensive CVE reproduction. Works with both the mcp 2.x MCPServer and the 1.x FastMCP.
- Read-only web dashboard with a live event feed.
- Optional SQLite persistence for jobs and tasks, so a restarted controller still answers for past jobs.
- Optional shared-token authentication for the worker WebSocket and the REST API.
- Cross-platform test-runner workload: run one command across the OS matrix and aggregate pass or fail.
- Defensive CVE-reproduction workload: reproduce a published advisory in a disposable sandbox from an operator recipe, compare affected versus patched, fetch the public advisory for context, and render a defender's Markdown report.
- Packaging: wheel and sdist, plus a PyInstaller build script for a portable per-OS binary. CI lints and type-checks, tests on Linux, Windows and macOS across Python 3.10 to 3.13, and does a build check. A release workflow attaches the wheel and the per-OS binaries on a version tag.

[Unreleased]: https://github.com/keyuraghao/corral/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/keyuraghao/corral/releases/tag/v0.1.0
