"""Cross-platform test runner workload.

This is the non-security proof of the harness: take one test command and run it on every node that
matches a selector, then aggregate pass or fail per node into a single report. It is the thing that a
single machine cannot do, which is to answer "does this pass on Linux and Windows and macOS at once".

The workload is thin. It builds one JobSpec, submits it through the controller (which fans it out),
waits, and shapes the per-node results. The command is whatever the caller gives; the runner does not
assume a language or a framework.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.client import ControllerClient
from ..core.models import Executor, JobSpec


@dataclass
class TestMatrixResult:
    passed: bool
    total: int
    passes: int
    per_node: list[dict] = field(default_factory=list)
    job_id: str = ""

    def summary_line(self) -> str:
        state = "PASS" if self.passed else "FAIL"
        return f"{state}  {self.passes}/{self.total} nodes green  (job {self.job_id})"


async def run_test_matrix(
    client: ControllerClient,
    command: list[str],
    *,
    executor: Executor = Executor.LOCAL,
    image: str = "python:3.12-slim",
    shell: bool = True,
    network: bool = True,
    timeout_s: float = 900.0,
    select_os: str | None = None,
    select_labels: list[str] | None = None,
    name: str = "test-matrix",
) -> TestMatrixResult:
    """Run `command` on every matching node and collect pass/fail.

    A node passes when its task exits zero. Tests usually need their dependencies, so the docker
    executor defaults to a network here, unlike the harness-wide default. Pass network=False to pin it
    shut for a hermetic suite.
    """

    spec = JobSpec(
        command=command,
        shell=shell,
        executor=executor,
        image=image,
        network=network,
        timeout_s=timeout_s,
        select_os=select_os,
        select_labels=select_labels or [],
        select_executor=executor,
        name=name,
    )
    view = await client.submit(spec, wait=True, timeout=timeout_s + 30)
    job = view["job"]
    tasks = view.get("tasks", [])
    per_node = []
    passes = 0
    for t in tasks:
        res = t.get("result", {})
        ok = t["state"] == "succeeded" and res.get("exit_code") == 0
        passes += 1 if ok else 0
        per_node.append(
            {
                "node_id": t["node_id"],
                "state": t["state"],
                "exit_code": res.get("exit_code"),
                "passed": ok,
                "output": "\n".join(line for _, line in t.get("log", [])),
            }
        )
    total = len(tasks)
    return TestMatrixResult(
        passed=total > 0 and passes == total,
        total=total,
        passes=passes,
        per_node=per_node,
        job_id=job["job_id"],
    )
