"""The controller: the brain of the fleet.

It keeps a live registry of connected workers, accepts jobs, decides which workers a job matches,
fans the job out as one task per matching worker, and tracks every task to a terminal state while
streaming its log. It is pure engine: it does not import FastAPI or any surface. The HTTP and
WebSocket wiring lives in `corral.core.server`, which drives this class.

Concurrency model: a single asyncio event loop owns all mutation of the registry and the stores.
Workers each hold a connection object with an outgoing send queue, so the controller can address one
worker without blocking on another.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from . import protocol as P
from .models import (
    TERMINAL_TASK_STATES,
    Job,
    JobSpec,
    JobState,
    Node,
    NodeInfo,
    NodeStatus,
    Task,
    TaskResult,
    TaskState,
    now,
    selector_matches,
)

# How long without any message before a node is considered stale and dropped from matching.
NODE_STALE_AFTER_S = 45.0


class WorkerConn:
    """The controller's side of one worker connection: its identity plus a way to send to it."""

    def __init__(self, node: Node, send: Callable[[str], Awaitable[None]]):
        self.node = node
        self.send = send

    async def dispatch(self, message: P.Message) -> None:
        await self.send(P.encode(message))


# A listener is called with (event_name, payload) whenever controller state changes. The dashboard
# and any live view subscribe to this. Listeners must not raise.
EventSink = Callable[[str, dict], Awaitable[None]]


class Controller:
    def __init__(self) -> None:
        self.nodes: dict[str, WorkerConn] = {}
        self.jobs: dict[str, Job] = {}
        self.tasks: dict[str, Task] = {}
        self._listeners: list[EventSink] = []
        self._lock = asyncio.Lock()

    # ----- event fan-out to live views -----

    def subscribe(self, sink: EventSink) -> Callable[[], None]:
        self._listeners.append(sink)

        def unsubscribe() -> None:
            if sink in self._listeners:
                self._listeners.remove(sink)

        return unsubscribe

    async def _emit(self, event: str, payload: dict) -> None:
        for sink in list(self._listeners):
            # A broken live-view listener must never break the controller.
            with contextlib.suppress(Exception):
                await sink(event, payload)

    # ----- worker lifecycle -----

    async def register(self, info: NodeInfo, send: Callable[[str], Awaitable[None]]) -> WorkerConn:
        node = Node(info=info, status=NodeStatus.ONLINE)
        conn = WorkerConn(node, send)
        async with self._lock:
            self.nodes[info.node_id] = conn
        await conn.dispatch(P.Welcome(node_id=info.node_id))
        await self._emit("node_connected", {"node_id": info.node_id, "info": info.model_dump(mode="json")})
        return conn

    async def unregister(self, node_id: str) -> None:
        async with self._lock:
            self.nodes.pop(node_id, None)
        await self._emit("node_disconnected", {"node_id": node_id})

    def touch(self, node_id: str) -> None:
        conn = self.nodes.get(node_id)
        if conn is not None:
            conn.node.last_seen = now()

    def online_nodes(self) -> list[Node]:
        """Nodes seen recently enough to be considered live."""

        cutoff = now() - NODE_STALE_AFTER_S
        return [c.node for c in self.nodes.values() if c.node.last_seen >= cutoff]

    # ----- jobs and fan-out -----

    def match_nodes(self, spec: JobSpec) -> list[Node]:
        return [n for n in self.online_nodes() if selector_matches(spec, n)]

    async def submit(self, spec: JobSpec) -> Job:
        """Create a job, fan it out to every matching node, and return the job record.

        A job that matches nothing is recorded with state NO_MATCH so a caller can tell the
        difference between "ran nowhere" and "not submitted".
        """

        job = Job(spec=spec)
        matched = self.match_nodes(spec)
        if not matched:
            job.state = JobState.NO_MATCH
            self.jobs[job.job_id] = job
            await self._emit("job_submitted", {"job": job.model_dump(mode="json"), "matched": 0})
            return job

        for node in matched:
            task = Task(job_id=job.job_id, node_id=node.info.node_id, spec=spec)
            self.tasks[task.task_id] = task
            job.task_ids.append(task.task_id)

        job.state = JobState.RUNNING
        self.jobs[job.job_id] = job
        await self._emit("job_submitted", {"job": job.model_dump(mode="json"), "matched": len(matched)})

        # Dispatch after recording, so a task is always findable before its worker reports on it.
        for task_id in job.task_ids:
            task = self.tasks[task_id]
            conn = self.nodes.get(task.node_id)
            if conn is None:
                task.state = TaskState.FAILED
                task.result = TaskResult(error="node disconnected before dispatch")
                continue
            task.state = TaskState.DISPATCHED
            conn.node.running_tasks.append(task_id)
            await conn.dispatch(P.TaskAssignment(task_id=task_id, job_id=job.job_id, spec=spec))
        await self._maybe_finish_job(job.job_id)
        return job

    async def cancel_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None:
            return False
        for task_id in job.task_ids:
            task = self.tasks.get(task_id)
            if task is None or task.state in TERMINAL_TASK_STATES:
                continue
            conn = self.nodes.get(task.node_id)
            if conn is not None:
                await conn.dispatch(P.Cancel(task_id=task_id))
        return True

    # ----- inbound worker reports -----

    async def on_task_log(self, node_id: str, msg: P.TaskLog) -> None:
        self.touch(node_id)
        task = self.tasks.get(msg.task_id)
        if task is None:
            return
        task.log.append((msg.stream, msg.line))
        await self._emit("task_log", {"task_id": msg.task_id, "stream": msg.stream, "line": msg.line})

    async def on_task_update(self, node_id: str, msg: P.TaskUpdate) -> None:
        self.touch(node_id)
        task = self.tasks.get(msg.task_id)
        if task is None:
            return
        task.state = msg.state
        if msg.result is not None:
            task.result = msg.result
        if msg.state in TERMINAL_TASK_STATES:
            conn = self.nodes.get(node_id)
            if conn is not None and msg.task_id in conn.node.running_tasks:
                conn.node.running_tasks.remove(msg.task_id)
        await self._emit("task_update", {"task_id": msg.task_id, "state": msg.state.value})
        await self._maybe_finish_job(task.job_id)

    async def _maybe_finish_job(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job is None or job.state != JobState.RUNNING:
            return
        tasks = [self.tasks[t] for t in job.task_ids if t in self.tasks]
        if tasks and all(t.state in TERMINAL_TASK_STATES for t in tasks):
            job.state = JobState.DONE
            await self._emit("job_done", {"job_id": job_id})

    # ----- read helpers for the surfaces -----

    def job_view(self, job_id: str) -> dict | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        return {
            "job": job.model_dump(mode="json"),
            "tasks": [self.tasks[t].model_dump(mode="json") for t in job.task_ids if t in self.tasks],
        }

    async def wait_for_job(self, job_id: str, timeout_s: float = 600.0) -> dict | None:
        """Block until a job reaches a terminal state or the timeout elapses. For the synchronous CLI."""

        deadline = now() + timeout_s
        while now() < deadline:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if job.state in (JobState.DONE, JobState.NO_MATCH):
                return self.job_view(job_id)
            await asyncio.sleep(0.1)
        return self.job_view(job_id)
