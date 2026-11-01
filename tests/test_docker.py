"""Docker executor tests. The whole module skips when a working docker CLI is not reachable, so the
suite stays green on machines and CI runners without Docker. Where Docker is present, these prove the
disposable, network-isolated container behavior the harness depends on.
"""

from __future__ import annotations

import pytest

from _util import docker_linux_available
from corral.core.executors import DockerExecutor
from corral.core.models import JobSpec, TaskState

_DOCKER = docker_linux_available()
skip_no_docker = pytest.mark.skipif(not _DOCKER, reason="docker with linux containers not available")

IMAGE = "alpine:3.20"


async def _run(spec: JobSpec):
    lines: list[tuple[str, str]] = []

    async def sink(stream: str, line: str) -> None:
        lines.append((stream, line))

    handle = await DockerExecutor().start("dtest", spec, sink)
    state, result = await handle.wait()
    return state, lines, result


def test_build_argv_is_network_isolated_by_default():
    ex = DockerExecutor()
    argv = ex._build_argv("abc", JobSpec(command=["echo", "hi"], image=IMAGE))
    assert "--rm" in argv
    assert "--network" in argv and "none" in argv
    assert argv[-2:] == ["echo", "hi"]


def test_build_argv_network_opt_in():
    ex = DockerExecutor()
    argv = ex._build_argv("abc", JobSpec(command=["echo", "hi"], image=IMAGE, network=True))
    assert "none" not in argv


@skip_no_docker
async def test_docker_runs_and_captures_output():
    state, lines, _ = await _run(JobSpec(command=["echo", "in-a-box"], image=IMAGE))
    assert state == TaskState.SUCCEEDED
    assert ("stdout", "in-a-box") in lines


@skip_no_docker
async def test_docker_nonzero_exit_is_failed():
    state, _, result = await _run(JobSpec(command=["exit 7"], shell=True, image=IMAGE))
    assert state == TaskState.FAILED
    assert result.exit_code != 0


@skip_no_docker
async def test_docker_network_isolated_by_default():
    # With no network, a name lookup must fail inside the container.
    state, _, _result = await _run(
        JobSpec(
            command=["sh", "-c", "wget -T 3 -q -O- http://example.com || echo NO_NET"],
            shell=True,
            image=IMAGE,
            timeout_s=30,
        )
    )
    assert state == TaskState.SUCCEEDED
