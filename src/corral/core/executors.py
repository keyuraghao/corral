"""Executors: how a worker actually runs a job.

An executor takes a JobSpec, runs it, streams each output line to a callback, and returns a TaskResult.
It knows nothing about the controller or the network protocol. The worker owns an executor per kind
and picks the one the job asks for.

The kinds that ship:
- LocalExecutor runs the command directly on the worker host. No isolation beyond the host.
- DockerExecutor runs it in a fresh container that is removed on exit and network-isolated by default.
- AgentExecutor hands the command to an independent agent program the worker is configured with, so a
  worker can be a reasoning agent rather than a dumb executor.
- VmExecutor (in vm.py) runs it inside a guest VM of any OS, reverting to a clean snapshot first.

All of them share one subprocess runner, `spawn_and_stream`, which streams output line by line, honors
a timeout, and supports cancellation that leaves nothing behind.
"""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Awaitable, Callable

from .models import Executor, JobSpec, TaskResult, TaskState, now

# A log sink is called with (stream, line) for each line of output as it arrives.
LogSink = Callable[[str, str], Awaitable[None]]
# An optional cleanup coroutine run when a task times out or is cancelled (for example, to kill a
# container or power off a VM) before the process itself is killed.
CleanupHook = Callable[[], Awaitable[None]]


class RunHandle:
    """A running execution that can be cancelled. Returned by start(); awaited for the result."""

    def __init__(self, task: asyncio.Task[tuple[TaskState, TaskResult]], canceller: Callable[[], Awaitable[None]]):
        self._task = task
        self._canceller = canceller

    async def wait(self) -> tuple[TaskState, TaskResult]:
        return await self._task

    async def cancel(self) -> None:
        await self._canceller()


async def _pump(stream: asyncio.StreamReader | None, name: str, sink: LogSink) -> None:
    """Read a subprocess stream line by line and forward each line to the sink."""

    if stream is None:
        return
    while True:
        raw = await stream.readline()
        if not raw:
            break
        # Strip the trailing newline, tolerating Windows CRLF so a line reads the same on every OS.
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        await sink(name, line)


async def spawn_and_stream(
    *,
    sink: LogSink,
    timeout_s: float,
    argv: list[str] | None = None,
    shell_cmd: str | None = None,
    stdin_data: str | None = None,
    on_cancel: CleanupHook | None = None,
) -> RunHandle:
    """Start a subprocess, stream its output, and return a handle.

    Pass exactly one of `argv` (run directly) or `shell_cmd` (run through a shell). `stdin_data`, if
    given, is written to the process and its stdin is closed. `on_cancel` runs on timeout or cancel
    before the process is killed, so an executor can tear down whatever it created.
    """

    if (argv is None) == (shell_cmd is None):
        raise ValueError("pass exactly one of argv or shell_cmd")

    stdin = asyncio.subprocess.PIPE if stdin_data is not None else None
    if argv is not None:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=stdin, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    else:
        assert shell_cmd is not None  # guaranteed by the exactly-one check above
        proc = await asyncio.create_subprocess_shell(
            shell_cmd, stdin=stdin, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

    if stdin_data is not None and proc.stdin is not None:
        proc.stdin.write(stdin_data.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

    async def runner() -> tuple[TaskState, TaskResult]:
        result = TaskResult(started_at=now())
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    _pump(proc.stdout, "stdout", sink),
                    _pump(proc.stderr, "stderr", sink),
                ),
                timeout=timeout_s,
            )
            await proc.wait()
        except asyncio.TimeoutError:
            if on_cancel is not None:
                await on_cancel()
            proc.kill()
            await proc.wait()
            result.finished_at = now()
            result.error = f"timed out after {timeout_s:g}s"
            return TaskState.TIMEOUT, result
        except asyncio.CancelledError:
            if on_cancel is not None:
                await on_cancel()
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


def _as_shell(command: list[str]) -> str:
    return " ".join(command) if isinstance(command, list) else command


class BaseExecutor:
    kind: Executor

    async def start(self, task_id: str, spec: JobSpec, sink: LogSink) -> RunHandle:
        raise NotImplementedError


class LocalExecutor(BaseExecutor):
    kind = Executor.LOCAL

    async def start(self, task_id: str, spec: JobSpec, sink: LogSink) -> RunHandle:
        if spec.shell:
            return await spawn_and_stream(sink=sink, timeout_s=spec.timeout_s, shell_cmd=_as_shell(spec.command))
        return await spawn_and_stream(sink=sink, timeout_s=spec.timeout_s, argv=list(spec.command))


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
            argv += ["/bin/sh", "-c", _as_shell(spec.command)]
        else:
            argv += list(spec.command)
        return argv

    async def start(self, task_id: str, spec: JobSpec, sink: LogSink) -> RunHandle:
        argv = self._build_argv(task_id, spec)
        name = self._container_name(task_id)

        async def kill_container() -> None:
            killer = await asyncio.create_subprocess_exec(
                self.docker_bin,
                "kill",
                name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()

        return await spawn_and_stream(sink=sink, timeout_s=spec.timeout_s, argv=argv, on_cancel=kill_container)


class AgentExecutor(BaseExecutor):
    """Run the command through an independent agent program the worker is configured with.

    The agent binary is fixed per worker (for example a `claude` or `codex` CLI, or any program that
    takes a task and does work). The job's command is appended as the agent's arguments, and the job's
    `agent_prompt`, if any, is written to the agent's stdin. This is what lets a worker be a reasoning
    agent instead of a dumb executor, while still riding the same fan-out spine, so many agents work in
    parallel across the fleet.
    """

    kind = Executor.AGENT

    def __init__(self, agent_bin: list[str]):
        if not agent_bin:
            raise ValueError("agent executor needs an agent command")
        self.agent_bin = agent_bin

    async def start(self, task_id: str, spec: JobSpec, sink: LogSink) -> RunHandle:
        argv = [*self.agent_bin, *spec.command]
        stdin_data = spec.agent_prompt or None
        return await spawn_and_stream(sink=sink, timeout_s=spec.timeout_s, argv=argv, stdin_data=stdin_data)


def default_executors(docker_bin: str = "docker") -> dict[Executor, BaseExecutor]:
    """The executor set a worker offers out of the box: local and docker."""

    return {Executor.LOCAL: LocalExecutor(), Executor.DOCKER: DockerExecutor(docker_bin=docker_bin)}


def shlex_join(command: list[str]) -> str:
    """Readable single-string form of an argv, for logs and the dashboard."""

    return " ".join(shlex.quote(c) for c in command)
