"""End-to-end tests: a real controller, real workers over WebSocket, real REST submissions.

These prove the whole spine: a worker dials in and registers, a job fans out to every matching node,
each task runs and reports back, and the REST API returns the collected results.
"""

from __future__ import annotations

import asyncio

from corral.core.client import ControllerClient
from corral.core.models import Executor, JobSpec
from corral.core.worker import Worker


async def _wait_for_nodes(client: ControllerClient, count: int, timeout: float = 10.0) -> list[dict]:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        nodes = await client.nodes()
        if len(nodes) >= count:
            return nodes
        await asyncio.sleep(0.1)
    raise AssertionError(f"expected {count} nodes, saw {len(await client.nodes())}")


async def _start_worker(ws_url: str, labels: list[str], node_id: str) -> tuple[Worker, asyncio.Task]:
    worker = Worker(controller_url=ws_url, labels=labels, node_id=node_id)
    task = asyncio.ensure_future(worker.run())
    return worker, task


async def test_single_worker_runs_a_local_job(running_controller):
    rc = running_controller
    client = ControllerClient(base_url=rc.api)
    _w, task = await _start_worker(rc.ws, ["test"], "solo")
    try:
        await _wait_for_nodes(client, 1)
        spec = JobSpec(command=["echo", "fleet-up"], executor=Executor.LOCAL, select_executor=Executor.LOCAL)
        view = await client.submit(spec, wait=True, timeout=20)
        assert view["job"]["state"] == "done"
        tasks = view["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["state"] == "succeeded"
        output = "\n".join(line for _, line in tasks[0]["log"])
        assert "fleet-up" in output
    finally:
        task.cancel()


async def test_job_fans_out_to_every_matching_node(running_controller):
    rc = running_controller
    client = ControllerClient(base_url=rc.api)
    _w1, t1 = await _start_worker(rc.ws, ["test", "linux"], "node-a")
    _w2, t2 = await _start_worker(rc.ws, ["test", "linux"], "node-b")
    try:
        await _wait_for_nodes(client, 2)
        spec = JobSpec(
            command=["sh", "-c", "echo from $(hostname) ok"],
            executor=Executor.LOCAL,
            select_executor=Executor.LOCAL,
            select_labels=["test"],
            name="fanout",
        )
        view = await client.submit(spec, wait=True, timeout=20)
        assert view["job"]["state"] == "done"
        tasks = view["tasks"]
        assert len(tasks) == 2
        assert {t["state"] for t in tasks} == {"succeeded"}
        node_ids = {t["node_id"] for t in tasks}
        assert node_ids == {"node-a", "node-b"}
    finally:
        t1.cancel()
        t2.cancel()


async def test_selector_can_target_a_subset(running_controller):
    rc = running_controller
    client = ControllerClient(base_url=rc.api)
    _w1, t1 = await _start_worker(rc.ws, ["alpha"], "only-alpha")
    _w2, t2 = await _start_worker(rc.ws, ["beta"], "only-beta")
    try:
        await _wait_for_nodes(client, 2)
        spec = JobSpec(
            command=["echo", "hi"], executor=Executor.LOCAL, select_executor=Executor.LOCAL, select_labels=["beta"]
        )
        view = await client.submit(spec, wait=True, timeout=20)
        tasks = view["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["node_id"] == "only-beta"
    finally:
        t1.cancel()
        t2.cancel()


async def test_no_match_is_reported(running_controller):
    rc = running_controller
    client = ControllerClient(base_url=rc.api)
    _w, task = await _start_worker(rc.ws, ["test"], "solo2")
    try:
        await _wait_for_nodes(client, 1)
        spec = JobSpec(
            command=["echo", "hi"],
            executor=Executor.LOCAL,
            select_executor=Executor.LOCAL,
            select_labels=["does-not-exist"],
        )
        view = await client.submit(spec, wait=True, timeout=20)
        assert view["job"]["state"] == "no_match"
        assert view["tasks"] == []
    finally:
        task.cancel()
