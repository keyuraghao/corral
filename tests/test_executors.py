"""Tests for the local executor. Docker executor tests live in test_docker.py and skip without docker.

Commands run through the Python interpreter rather than shell builtins, so these pass on Linux,
Windows and macOS alike.
"""

from __future__ import annotations

from _util import py, py_exit, py_print, py_sleep, py_stderr
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
    state, lines = await _run(JobSpec(command=py_print("hello")))
    assert state == TaskState.SUCCEEDED
    assert ("stdout", "hello") in lines


async def test_local_nonzero_exit_is_failed():
    state, _ = await _run(JobSpec(command=py_exit(3)))
    assert state == TaskState.FAILED


async def test_local_stderr_is_captured():
    state, lines = await _run(JobSpec(command=py_stderr("oops")))
    assert state == TaskState.SUCCEEDED
    assert ("stderr", "oops") in lines


async def test_local_timeout():
    state, _ = await _run(JobSpec(command=py_sleep(5), timeout_s=0.3))
    assert state == TaskState.TIMEOUT


async def test_local_multiple_lines():
    state, lines = await _run(JobSpec(command=py("print('foo'); print('bar')")))
    assert state == TaskState.SUCCEEDED
    assert ("stdout", "foo") in lines
    assert ("stdout", "bar") in lines
