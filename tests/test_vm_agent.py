"""Tests for the agent and vm executors.

These use portable Python commands, the loopback vm provider (no hypervisor), and the local vm
transport, so they run anywhere without Docker, a VM, or SSH. They prove the executor logic: agent
argument and stdin handling, vm snapshot-revert ordering, and the fatal-revert path.
"""

from __future__ import annotations

import pytest

from _util import PYTHON, py_exit, py_print
from corral.core.executors import AgentExecutor
from corral.core.models import Executor, JobSpec, TaskState
from corral.core.vm import (
    CommandProvider,
    LocalTransport,
    LoopbackProvider,
    SshTransport,
    VirshProvider,
    VmExecutor,
    VmProvider,
    build_provider,
    build_transport,
)


class _ArgvProvider(VmProvider):
    """A test provider that returns fixed argv lists, avoiding shell-string quoting across OSes."""

    def __init__(self, revert: list[str] | None, start: list[str] | None = None):
        self._revert = revert
        self._start = start

    def revert_argv(self, snapshot: str) -> list[str] | None:
        return self._revert

    def start_argv(self) -> list[str] | None:
        return self._start


async def _run(executor, spec: JobSpec) -> tuple[TaskState, list[tuple[str, str]]]:
    lines: list[tuple[str, str]] = []

    async def sink(stream: str, line: str) -> None:
        lines.append((stream, line))

    handle = await executor.start("t1", spec, sink)
    state, _ = await handle.wait()
    return state, lines


# ----- agent executor -----


async def test_agent_passes_command_as_args():
    agent = AgentExecutor([PYTHON])
    state, lines = await _run(agent, JobSpec(command=["-c", "print('agent-ran')"], executor=Executor.AGENT))
    assert state == TaskState.SUCCEEDED
    assert ("stdout", "agent-ran") in lines


async def test_agent_feeds_prompt_on_stdin():
    agent = AgentExecutor([PYTHON])
    spec = JobSpec(
        command=["-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        executor=Executor.AGENT,
        agent_prompt="do the thing",
    )
    state, lines = await _run(agent, spec)
    assert state == TaskState.SUCCEEDED
    assert ("stdout", "DO THE THING") in lines


def test_agent_requires_a_command():
    with pytest.raises(ValueError):
        AgentExecutor([])


# ----- vm executor -----


async def test_vm_loopback_runs_the_command():
    ex = VmExecutor(provider=LoopbackProvider(), transport=LocalTransport())
    state, lines = await _run(ex, JobSpec(command=py_print("in-vm"), executor=Executor.VM))
    assert state == TaskState.SUCCEEDED
    assert ("stdout", "in-vm") in lines


async def test_vm_reports_command_failure():
    ex = VmExecutor(provider=LoopbackProvider(), transport=LocalTransport())
    state, _ = await _run(ex, JobSpec(command=py_exit(4), executor=Executor.VM))
    assert state == TaskState.FAILED


async def test_vm_fatal_when_revert_fails():
    provider = _ArgvProvider(revert=py_exit(2))
    ex = VmExecutor(provider=provider, transport=LocalTransport(), default_snapshot="clean")
    state, lines = await _run(ex, JobSpec(command=py_print("should-not-run"), executor=Executor.VM))
    assert state == TaskState.FAILED
    assert ("stdout", "should-not-run") not in lines


async def test_vm_revert_runs_before_command():
    provider = _ArgvProvider(revert=py_print("REVERTED"))
    ex = VmExecutor(provider=provider, transport=LocalTransport(), default_snapshot="clean")
    state, lines = await _run(ex, JobSpec(command=py_print("COMMAND"), executor=Executor.VM))
    assert state == TaskState.SUCCEEDED
    stdout = [line for stream, line in lines if stream == "stdout"]
    revert_idx = next(i for i, line in enumerate(stdout) if "REVERTED" in line)
    command_idx = next(i for i, line in enumerate(stdout) if line == "COMMAND")
    assert revert_idx < command_idx


async def test_vm_start_failure_is_tolerated():
    # A non-zero start is logged but not fatal; the command still runs.
    provider = _ArgvProvider(revert=None, start=py_exit(1))
    ex = VmExecutor(provider=provider, transport=LocalTransport())
    state, lines = await _run(ex, JobSpec(command=py_print("still-ran"), executor=Executor.VM))
    assert state == TaskState.SUCCEEDED
    assert ("stdout", "still-ran") in lines


# ----- builders and argv shapes -----


def test_build_provider_variants():
    assert isinstance(build_provider("loopback"), LoopbackProvider)
    assert isinstance(build_provider("virsh", name="win11"), VirshProvider)
    assert isinstance(build_provider("command", revert_tmpl="x"), CommandProvider)


def test_build_provider_rejects_unknown():
    with pytest.raises(ValueError):
        build_provider("nope")


def test_build_transport_local_vs_ssh():
    assert build_transport("").name == "local"
    assert build_transport("user@host").name == "ssh"


def test_virsh_argv_shape():
    p = VirshProvider(domain="win11")
    assert p.revert_argv("clean") == ["virsh", "snapshot-revert", "win11", "clean", "--running"]
    assert p.start_argv() == ["virsh", "start", "win11"]


def test_command_provider_substitutes_snapshot():
    p = CommandProvider(revert_tmpl="revertvm --snap {snapshot}")
    assert p.revert_argv("gold") == ["revertvm", "--snap", "gold"]


def test_ssh_transport_builds_remote_command():
    t = SshTransport(target="user@host")
    argv, shell_cmd = t.command(JobSpec(command=["echo", "hi"], executor=Executor.VM))
    assert shell_cmd is None
    assert argv[0] == "ssh"
    assert "user@host" in argv
    assert argv[-1] == "echo hi"


def test_vm_provider_base_is_abstract():
    with pytest.raises(NotImplementedError):
        VmProvider().revert_argv("x")
