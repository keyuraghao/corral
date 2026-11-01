"""Executors: how a worker actually runs a job.

An executor takes a JobSpec, runs it, streams each output line to a callback, and returns a TaskResult.
It knows nothing about the controller or the network protocol. The worker owns an executor per kind
and picks the one the job asks for.

Two kinds ship today:
- LocalExecutor runs the command directly on the worker host. Use it for trusted work and for the
  worker's own self-test. It offers no isolation beyond the host.
- DockerExecutor runs the command inside a fresh container that is removed on exit and is
  network-isolated by default. This is the disposable environment the harness is built around.

Both honor a timeout and support cancellation. A cancelled or timed-out run leaves nothing behind:
the docker container is started with --rm and is killed by name on cancel.
"""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Awaitable, Callable

from .models import Executor, JobSpec, TaskResult, TaskState, now

# A log sink is called with (stream, line) for each line of output as it arrives.
LogSink = Callable[[str, str], Awaitable[None]]


class RunHandle:
    """A running execution that can be cancelled. Returned by start(); awaited for the result."""

    def __init__(self, task: asyncio.Task[tuple[TaskState, TaskResult]], canceller: Callable[[], Awaitable[None]]):
        self._task = task
        self._canceller = canceller

    async def wait(self) -> tuple[TaskState, TaskResult]:
        return await self._task

    async def cancel(self) -> None:
        await self._canceller()


async def _pump(stream: asyncio.StreamReader | None, name: str, sink: LogSink, buf: list[str]) -> None:
    """Read a subprocess stream line by line, forward each line to the sink, and keep a copy."""

    if stream is None:
        return
    while True:
        raw = await stream.readline()
        if not raw:
            break
        # Strip the trailing newline, tolerating Windows CRLF so a line reads the same on every OS.
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        buf.append(line)
        await sink(name, line)


class BaseExecutor:
    kind: Executor

    async def start(self, task_id: str, spec: JobSpec, sink: LogSink) -> RunHandle:
        raise NotImplementedError


class LocalExecutor(BaseExecutor):
    kind = Executor.LOCAL

    async def start(self, task_id: str, spec: JobSpec, sink: LogSink) -> RunHandle:
        if spec.shell:
            proc = await asyncio.create_subprocess_shell(
                " ".join(spec.command) if isinstance(spec.command, list) else spec.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *spec.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        async def runner() -> tuple[TaskState, TaskResult]:
            result = TaskResult(started_at=now())
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        _pump(proc.stdout, "stdout", sink, []),
                        _pump(proc.stderr, "stderr", sink, []),
                    ),
                    timeout=spec.timeout_s,
                )
                await proc.wait()
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                result.finished_at = now()
                result.error = f"timed out after {spec.timeout_s:g}s"
                return TaskState.TIMEOUT, result
            except asyncio.CancelledError:
                proc.kill()
                await proc.wait()
                result.finished_at = now()
                result.error = "cancelled"
                return TaskState.CANCELLED, result
            result.exit_code = proc.returncode
            result.finished_at = now()
            state = TaskState.SUCCEEDED if proc.returncode == 0 else TaskState.FAILED
            return state, result

        task = asyncio.ensure_future(runner())

        async def canceller() -> None:
            task.cancel()

        return RunHandle(task, canceller)


class DockerExecutor(BaseExecutor):
    kind = Executor.DOCKER

    def __init__(self, docker_bin: str = "docker"):
        self.docker_bin = docker_bin

    def _container_name(self, task_id: str) -> str:
        return f"corral-{task_id}"

    def _build_argv(self, task_id: str, spec: JobSpec) -> list[str]:
        name = self._container_name(task_id)
        argv = [self.docker_bin, "run", "--rm", "--name", name, "-w", spec.workdir]
        if not spec.network:
            argv += ["--network", "none"]
        argv.append(spec.image)
        if spec.shell:
            cmd = " ".join(spec.command) if isinstance(spec.command, list) else spec.command
            argv += ["/bin/sh", "-c", cmd]
        else:
            argv += list(spec.command)
        return argv

    async def start(self, task_id: str, spec: JobSpec, sink: LogSink) -> RunHandle:
        argv = self._build_argv(task_id, spec)
        name = self._container_name(task_id)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def _kill_container() -> None:
            killer = await asyncio.create_subprocess_exec(
                self.docker_bin,
                "kill",
                name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()

        async def runner() -> tuple[TaskState, TaskResult]:
            result = TaskResult(started_at=now())
            result.artifacts = []
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        _pump(proc.stdout, "stdout", sink, []),
                        _pump(proc.stderr, "stderr", sink, []),
                    ),
                    timeout=spec.timeout_s,
                )
                await proc.wait()
            except asyncio.TimeoutError:
                await _kill_container()
                proc.kill()
                await proc.wait()
                result.finished_at = now()
                result.error = f"timed out after {spec.timeout_s:g}s"
                return TaskState.TIMEOUT, result
            except asyncio.CancelledError:
                await _kill_container()
                proc.kill()
                await proc.wait()
                result.finished_at = now()
                result.error = "cancelled"
                return TaskState.CANCELLED, result
            result.exit_code = proc.returncode
            result.finished_at = now()
            state = TaskState.SUCCEEDED if proc.returncode == 0 else TaskState.FAILED
            return state, result

        task = asyncio.ensure_future(runner())

        async def canceller() -> None:
            await _kill_container()
            task.cancel()

        return RunHandle(task, canceller)


def default_executors(docker_bin: str = "docker") -> dict[Executor, BaseExecutor]:
    """The executor set a worker offers out of the box."""

    return {Executor.LOCAL: LocalExecutor(), Executor.DOCKER: DockerExecutor(docker_bin=docker_bin)}


def shlex_join(command: list[str]) -> str:
    """Readable single-string form of an argv, for logs and the dashboard."""

    return " ".join(shlex.quote(c) for c in command)
