"""Auth tests: when a token is configured, the API refuses requests without it and a worker without
the token cannot connect, while the right token works everywhere."""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from corral.core.client import ControllerClient
from corral.core.controller import Controller
from corral.core.models import Executor, JobSpec
from corral.core.server import build_app
from corral.core.worker import Worker

TOKEN = "s3cret-token"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def secured_controller():
    host, port = "127.0.0.1", _free_port()
    app = build_app(Controller(), auth_token=TOKEN)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            httpx.get(f"http://{host}:{port}/healthz", timeout=0.5)
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    yield host, port
    server.should_exit = True
    thread.join(timeout=5)


async def test_api_rejects_without_token(secured_controller):
    host, port = secured_controller
    async with httpx.AsyncClient() as c:
        r = await c.get(f"http://{host}:{port}/api/nodes")
        assert r.status_code == 401


async def test_api_accepts_with_token(secured_controller):
    host, port = secured_controller
    client = ControllerClient(base_url=f"http://{host}:{port}", token=TOKEN)
    nodes = await client.nodes()
    assert nodes == []


async def test_worker_without_token_cannot_register(secured_controller):
    host, port = secured_controller
    client = ControllerClient(base_url=f"http://{host}:{port}", token=TOKEN)
    bad = Worker(controller_url=f"ws://{host}:{port}", node_id="bad")  # no token
    task = asyncio.ensure_future(bad.run())
    try:
        await asyncio.sleep(1.5)
        assert await client.nodes() == []
    finally:
        task.cancel()


async def test_worker_with_token_registers(secured_controller):
    host, port = secured_controller
    client = ControllerClient(base_url=f"http://{host}:{port}", token=TOKEN)
    good = Worker(controller_url=f"ws://{host}:{port}", node_id="good", token=TOKEN)
    task = asyncio.ensure_future(good.run())
    try:
        deadline = asyncio.get_event_loop().time() + 8
        while asyncio.get_event_loop().time() < deadline:
            if await client.nodes():
                break
            await asyncio.sleep(0.1)
        nodes = await client.nodes()
        assert len(nodes) == 1
        spec = JobSpec(command=["echo", "ok"], executor=Executor.LOCAL, select_executor=Executor.LOCAL)
        view = await client.submit(spec, wait=True, timeout=15)
        assert view["job"]["state"] == "done"
    finally:
        task.cancel()
