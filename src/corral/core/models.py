"""The shared data model for Corral.

These types are the common vocabulary spoken by every surface. The controller, the worker, the CLI,
the MCP server and the dashboard all agree on these shapes. They are deliberately small and JSON
serialisable so the WebSocket protocol and the REST API can carry them unchanged.

Vocabulary:
- A Node is a worker that has dialed into the controller. It advertises what it can do.
- A Job is a unit of work the user submits. It names a command and a selector for which nodes run it.
- A Task is one execution of a Job on one Node. A Job that matches three nodes produces three Tasks.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum

from pydantic import BaseModel, Field


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> float:
    """Current wall-clock time as a float. One source so tests can reason about ordering."""
    return time.time()


class Executor(str, Enum):
    """How a worker runs a job. The worker advertises which of these it supports."""

    LOCAL = "local"
    DOCKER = "docker"


class NodeStatus(str, Enum):
    ONLINE = "online"
    BUSY = "busy"
    OFFLINE = "offline"


class TaskState(str, Enum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class JobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    NO_MATCH = "no_match"


# Terminal task states, kept in one place so the controller and the reporting code agree.
TERMINAL_TASK_STATES = frozenset({TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED, TaskState.TIMEOUT})


class NodeInfo(BaseModel):
    """What a worker tells the controller about itself when it dials in.

    `labels` are free-form tags the operator sets on the worker (for example "cve", "fast-disk").
    A job selector can match on os, arch, executor or any label.
    """

    node_id: str
    hostname: str = ""
    os: str = ""
    arch: str = ""
    executors: list[Executor] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    docker_available: bool = False


class Node(BaseModel):
    """The controller's live record of a connected worker."""

    info: NodeInfo
    status: NodeStatus = NodeStatus.ONLINE
    connected_at: float = Field(default_factory=now)
    last_seen: float = Field(default_factory=now)
    running_tasks: list[str] = Field(default_factory=list)


class JobSpec(BaseModel):
    """A job as submitted by a user. The selector decides which nodes run it.

    An empty selector means "every online node". The command is run as given; `shell=False` passes
    `command` as an argv list to the executor, `shell=True` runs the single string through a shell.
    """

    command: list[str]
    shell: bool = False
    executor: Executor = Executor.LOCAL
    # Environment for the docker executor. Ignored by the local executor.
    image: str = "alpine:3.20"
    network: bool = False  # disposable containers are network-isolated by default
    workdir: str = "/work"
    timeout_s: float = 300.0
    # Selector. All present constraints must match. Empty means match every online node.
    select_os: str | None = None
    select_arch: str | None = None
    select_labels: list[str] = Field(default_factory=list)
    select_executor: Executor | None = None
    select_nodes: list[str] = Field(default_factory=list)
    # Human label for the job, shown in listings.
    name: str = ""


class Job(BaseModel):
    """A submitted job and its fan-out of tasks."""

    job_id: str = Field(default_factory=lambda: _new_id("job"))
    spec: JobSpec
    state: JobState = JobState.PENDING
    created_at: float = Field(default_factory=now)
    task_ids: list[str] = Field(default_factory=list)


class TaskResult(BaseModel):
    exit_code: int | None = None
    error: str = ""
    artifacts: list[str] = Field(default_factory=list)
    started_at: float | None = None
    finished_at: float | None = None


class Task(BaseModel):
    """One execution of a job on one node."""

    task_id: str = Field(default_factory=lambda: _new_id("task"))
    job_id: str
    node_id: str
    spec: JobSpec
    state: TaskState = TaskState.PENDING
    created_at: float = Field(default_factory=now)
    result: TaskResult = Field(default_factory=TaskResult)
    # Captured output, newest appended. Each entry is (stream, line) where stream is "stdout"/"stderr".
    log: list[tuple[str, str]] = Field(default_factory=list)


def selector_matches(spec: JobSpec, node: Node) -> bool:
    """True if `node` satisfies every constraint present in `spec`.

    An absent constraint is not checked. Labels must all be present on the node. A node must support
    the job's executor to match, since it is about to be asked to run it that way.
    """

    info = node.info
    if spec.select_nodes and info.node_id not in spec.select_nodes:
        return False
    if spec.select_os and info.os.lower() != spec.select_os.lower():
        return False
    if spec.select_arch and info.arch.lower() != spec.select_arch.lower():
        return False
    if spec.select_executor and spec.select_executor not in info.executors:
        return False
    if spec.executor not in info.executors:
        return False
    return not (spec.select_labels and not set(spec.select_labels).issubset(set(info.labels)))
