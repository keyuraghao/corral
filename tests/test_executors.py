"""Tests for the local executor. Docker executor tests live in test_docker.py and skip without docker."""

from __future__ import annotations

from corral.core.executors import LocalExecutor
from corral.core.models import JobSpec, TaskState


async def _run(spec: JobSpec) -> tuple[TaskState, list[tuple[str, str]]]:
    lines: list[tuple[str, str]] = []

    async def sink(stream: str, line: str) -> None:
        lines.append((stream, line))

    handle = await LocalExecutor().start("t1", spec, sink)
    state, _result = await handle.wait()
    return state, lines


async def test_local_success_and_stdout():
    state, lines = await _run(JobSpec(command=["echo", "hello"]))
    assert state == TaskState.SUCCEEDED
    assert ("stdout", "hello") in lines


async def test_local_nonzero_exit_is_failed():
    state, _ = await _run(JobSpec(command=["sh", "-c", "exit 3"], shell=False))
    assert state == TaskState.FAILED


async def test_local_stderr_is_captured():
    state, lines = await _run(JobSpec(command=["sh", "-c", "echo oops 1>&2"]))
    assert state == TaskState.SUCCEEDED
    assert ("stderr", "oops") in lines


async def test_local_timeout():
    state, _ = await _run(JobSpec(command=["sleep", "5"], timeout_s=0.3))
    assert state == TaskState.TIMEOUT


async def test_local_shell_mode():
    state, lines = await _run(JobSpec(command=["echo foo && echo bar"], shell=True))
    assert state == TaskState.SUCCEEDED
    assert ("stdout", "foo") in lines
    assert ("stdout", "bar") in lines
