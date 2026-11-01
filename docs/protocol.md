# The worker protocol

A worker and the controller speak one small protocol over a single WebSocket. Every message is a JSON object with a `type` and a `data` payload. The worker dials the controller at `/ws/worker`; when the controller runs with a token, the worker appends `?token=...`.

## Messages

Worker to controller:

| type | meaning |
|---|---|
| `hello` | the worker introduces itself with its node info (sent first) |
| `heartbeat` | liveness, sent periodically and in answer to a ping |
| `task_log` | one line of output from a running task, tagged stdout or stderr |
| `task_update` | a task changed state, carrying its result on a terminal state |

Controller to worker:

| type | meaning |
|---|---|
| `welcome` | the controller accepted the worker and echoes the node id |
| `task` | run this task now, with its full job spec |
| `cancel` | stop this task if it is still running |
| `ping` | liveness request; answer with a heartbeat |

## Lifecycle

1. The worker connects and sends `hello` with its node info: id, hostname, OS, architecture, the executors it supports, its labels, and whether Docker is available.
2. The controller registers the node and replies `welcome`.
3. The controller sends a `task` whenever a job matches this node. The worker runs it, streams `task_log` lines as they appear, and sends `task_update` first to `running` and then to a terminal state (`succeeded`, `failed`, `timeout` or `cancelled`) with the result.
4. The worker sends a `heartbeat` on a timer. If the connection drops, the worker reconnects with capped exponential backoff, so the controller can restart under a running fleet.

## Node liveness

The controller treats a node as live only if it has been heard from recently. A node that goes quiet past the stale window stops matching new jobs, and a closed socket removes it from the registry at once. Node state is never persisted, because a node is only real while its socket is open.

## Guarantees

A task is recorded in the controller's store before its assignment is dispatched, so a task is always findable before its worker reports on it. The controller serialises all sends to one worker through a per-connection queue, so two concurrent dispatches never interleave on the same socket.
