"""Shared test fixtures.

The end-to-end tests run the real FastAPI controller in-process on a random port using uvicorn in a
background thread, then connect a real worker to it. Nothing is mocked on the controller side, so the
WebSocket protocol, the fan-out, and the REST API are all exercised together.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn

from corral.core.controller import Controller
from corral.core.server import build_app


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class RunningController:
    def __init__(self, controller: Controller, host: str, port: int):
        self.controller = controller
        self.host = host
        self.port = port

    @property
    def api(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def ws(self) -> str:
        return f"ws://{self.host}:{self.port}"


@pytest.fixture
def running_controller() -> Iterator[RunningController]:
    host = "127.0.0.1"
    port = _free_port()
    controller = Controller()
    app = build_app(controller)
    config = uvicorn.Config(app, host=host, port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for the server to answer before handing it to the test.
    deadline = time.time() + 10
    ready = False
    while time.time() < deadline:
        try:
            r = httpx.get(f"http://{host}:{port}/healthz", timeout=0.5)
            if r.status_code == 200:
                ready = True
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    if not ready:
        raise RuntimeError("controller did not start")

    yield RunningController(controller, host, port)

    server.should_exit = True
    thread.join(timeout=5)
