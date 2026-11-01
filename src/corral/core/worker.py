"""The worker: a dumb executor that dials into a controller and runs what it is told.

The worker holds no job logic of its own. It introduces itself, then waits for task assignments,
runs each one through the matching executor, streams the output back line by line, and reports the
final result. It keeps the connection alive with heartbeats and reconnects with backoff if the
controller goes away, so a worker can be left running and the controller restarted under it.

A worker is deliberately simple to reason about: everything it will ever do is decided by the
controller, and every environment it creates is disposable.
"""

from __future__ import annotations

import asyncio
import contextlib
import platform
import shutil
import socket
import subprocess
import uuid

import websockets

from . import protocol as P
from .executors import AgentExecutor, BaseExecutor, RunHandle, default_executors
from .models import Executor, NodeInfo, TaskResult, TaskState


def detect_docker(docker_bin: str = "docker") -> bool:
    """True if a working docker CLI is reachable. Never raises."""

    if shutil.which(docker_bin) is None:
        return False
    try:
        out = subprocess.run(
            [docker_bin, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            timeout=8,
            check=False,
        )
        return out.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def gather_node_info(node_id: str, labels: list[str], executors: list[Executor], docker_available: bool) -> NodeInfo:
    """Describe this host for the controller's registry from the executors the worker actually offers."""

    return NodeInfo(
        node_id=node_id,
        hostname=socket.gethostname(),
        os=platform.system().lower(),
        arch=platform.machine().lower(),
        executors=executors,
        labels=labels,
        docker_available=docker_available,
    )


class Worker:
    """A worker process. Construct it, then `await run()` to serve until interrupted.

    By default a worker offers the local and docker executors. Pass `agent_cmd` to also offer the
    agent executor (the worker becomes a reasoning agent), and `vm_executor` to offer the vm executor
    (the worker manages a guest VM). A worker advertises exactly the executors it can run, so the
    controller only ever sends it work it is equipped for.
    """

    def __init__(
        self,
        controller_url: str,
        labels: list[str] | None = None,
        node_id: str | None = None,
        docker_bin: str = "docker",
        heartbeat_s: float = 15.0,
        reconnect_max_s: float = 30.0,
        token: str | None = None,
        agent_cmd: list[str] | None = None,
        vm_executor: BaseExecutor | None = None,
    ):
        self.controller_url = controller_url.rstrip("/")
        self.node_id = node_id or f"node_{uuid.uuid4().hex[:8]}"
        self.labels = labels or []
        self.docker_bin = docker_bin
        self.heartbeat_s = heartbeat_s
        self.reconnect_max_s = reconnect_max_s
        self.token = token
        self.docker_available = detect_docker(docker_bin)
        self.executors: dict[Executor, BaseExecutor] = default_executors(docker_bin=docker_bin)
        if not self.docker_available:
            self.executors.pop(Executor.DOCKER, None)
        if agent_cmd:
            self.executors[Executor.AGENT] = AgentExecutor(agent_cmd)
        if vm_executor is not None:
            self.executors[Executor.VM] = vm_executor
        self._running: dict[str, RunHandle] = {}
        self._bg: set[asyncio.Task] = set()
        self._ws: websockets.ClientConnection | None = None

    def _ws_endpoint(self) -> str:
        base = self.controller_url
        if base.startswith("http://"):
            base = "ws://" + base[len("http://") :]
        elif base.startswith("https://"):
            base = "wss://" + base[len("https://") :]
        if not base.endswith("/ws/worker"):
            base = base + "/ws/worker"
        if self.token:
            base = f"{base}?token={self.token}"
        return base

    async def run(self) -> None:
        """Connect and serve, reconnecting with capped exponential backoff on failure."""

        backoff = 1.0
        while True:
            try:
                await self._serve_once()
                backoff = 1.0
            except (OSError, websockets.WebSocketException) as exc:
                print(f"[worker {self.node_id}] disconnected: {exc}; retrying in {backoff:g}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.reconnect_max_s)

    async def _serve_once(self) -> None:
        endpoint = self._ws_endpoint()
        async with websockets.connect(endpoint, max_size=None) as ws:
            self._ws = ws
            info = gather_node_info(self.node_id, self.labels, list(self.executors.keys()), self.docker_available)
            await ws.send(P.encode(P.Hello(info=info)))
            execs = ",".join(e.value for e in info.executors)
            print(f"[worker {self.node_id}] connected to {endpoint} as {info.os}/{info.arch}, executors={execs}")
            hb = asyncio.ensure_future(self._heartbeat_loop(ws))
            try:
                async for raw in ws:
                    await self._handle(ws, raw)
            finally:
                hb.cancel()
                self._ws = None

    async def _heartbeat_loop(self, ws: websockets.ClientConnection) -> None:
        try:
            while True:
                await asyncio.sleep(self.heartbeat_s)
                await ws.send(P.encode(P.Heartbeat()))
        except (asyncio.CancelledError, websockets.WebSocketException):
            return

    async def _handle(self, ws: websockets.ClientConnection, raw: str | bytes) -> None:
        try:
            msg = P.decode(raw)
        except ValueError as exc:
            print(f"[worker {self.node_id}] ignoring bad message: {exc}")
            return
        if isinstance(msg, P.Welcome):
            self.node_id = msg.node_id
        elif isinstance(msg, P.Ping):
            await ws.send(P.encode(P.Heartbeat()))
        elif isinstance(msg, P.TaskAssignment):
            # Keep a reference so the task is not garbage collected mid-run; drop it when done.
            bg = asyncio.ensure_future(self._run_task(ws, msg))
            self._bg.add(bg)
            bg.add_done_callback(self._bg.discard)
        elif isinstance(msg, P.Cancel):
            handle = self._running.get(msg.task_id)
            if handle is not None:
                await handle.cancel()

    async def _run_task(self, ws: websockets.ClientConnection, assignment: P.TaskAssignment) -> None:
        task_id = assignment.task_id
        spec = assignment.spec
        executor = self.executors.get(spec.executor)
        if executor is None:
            await ws.send(
                P.encode(
                    P.TaskUpdate(
                        task_id=task_id,
                        state=TaskState.FAILED,
                        result=TaskResult(error=f"no executor for {spec.executor.value!r}"),
                    )
                )
            )
            return

        async def sink(stream: str, line: str) -> None:
            with contextlib.suppress(websockets.WebSocketException):
                await ws.send(P.encode(P.TaskLog(task_id=task_id, stream=stream, line=line)))  # type: ignore[arg-type]

        await ws.send(P.encode(P.TaskUpdate(task_id=task_id, state=TaskState.RUNNING)))
        try:
            handle = await executor.start(task_id, spec, sink)
        except (OSError, ValueError) as exc:
            await ws.send(
                P.encode(
                    P.TaskUpdate(
                        task_id=task_id,
                        state=TaskState.FAILED,
                        result=TaskResult(error=f"failed to start: {exc}"),
                    )
                )
            )
            return
        self._running[task_id] = handle
        try:
            state, result = await handle.wait()
        finally:
            self._running.pop(task_id, None)
        await ws.send(P.encode(P.TaskUpdate(task_id=task_id, state=state, result=result)))
