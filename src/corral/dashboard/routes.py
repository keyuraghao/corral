"""The read-only web dashboard.

A single self-contained page served at `/`. It shows the live fleet and recent jobs, and streams
controller events over the `/ws/events` WebSocket so the view updates as workers connect and jobs
run. It is read-only: it submits nothing. Driving the fleet stays with the CLI and the MCP server.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from ..core.controller import Controller

_TEMPLATE = Path(__file__).parent / "templates" / "index.html"


def mount_dashboard(app: FastAPI, controller: Controller) -> None:
    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _TEMPLATE.read_text(encoding="utf-8")
