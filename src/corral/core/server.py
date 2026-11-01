"""The HTTP and WebSocket surface over the controller.

This is the single server the CLI, the MCP server and the dashboard all talk to. It is a thin
FastAPI app: it translates requests into Controller calls and serialises the results. All real logic
lives in Controller.

Routes:
    WS   /ws/worker            a worker dials in here
    WS   /ws/events            a live view subscribes to controller events here
    GET  /healthz              liveness
    GET  /api/nodes            list live nodes
    GET  /api/jobs             list jobs (newest first)
    POST /api/jobs             submit a job; ?wait=1 blocks until it finishes
    GET  /api/jobs/{id}        a job with its tasks
    POST /api/jobs/{id}/cancel cancel a running job

The server binds to 127.0.0.1 by default. Each worker connection gets its own outgoing queue and a
writer task, so the controller can send to one worker without racing another task on the same socket.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from . import protocol as P
from .controller import Controller
from .models import JobSpec


def build_app(controller: Controller | None = None, *, with_dashboard: bool = False) -> FastAPI:
    ctrl = controller or Controller()
    app = FastAPI(title="Corral controller", version="0.1.0")
    app.state.controller = ctrl

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "nodes": len(ctrl.online_nodes()), "jobs": len(ctrl.jobs)}

    @app.get("/api/nodes")
    async def list_nodes() -> dict[str, Any]:
        return {"nodes": [n.model_dump(mode="json") for n in ctrl.online_nodes()]}

    @app.get("/api/jobs")
    async def list_jobs() -> dict[str, Any]:
        jobs = sorted(ctrl.jobs.values(), key=lambda j: j.created_at, reverse=True)
        return {"jobs": [j.model_dump(mode="json") for j in jobs]}

    @app.post("/api/jobs")
    async def submit_job(spec: JobSpec, wait: bool = False, timeout: float = 600.0) -> JSONResponse:
        job = await ctrl.submit(spec)
        if wait:
            view = await ctrl.wait_for_job(job.job_id, timeout_s=timeout)
            return JSONResponse(view or {"job": job.model_dump(mode="json"), "tasks": []})
        return JSONResponse(ctrl.job_view(job.job_id))

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> JSONResponse:
        view = ctrl.job_view(job_id)
        if view is None:
            return JSONResponse({"error": "no such job"}, status_code=404)
        return JSONResponse(view)

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> JSONResponse:
        ok = await ctrl.cancel_job(job_id)
        return JSONResponse({"cancelled": ok}, status_code=200 if ok else 404)

    @app.websocket("/ws/worker")
    async def worker_ws(ws: WebSocket) -> None:
        await ws.accept()
        outgoing: asyncio.Queue[str] = asyncio.Queue()

        async def send(text: str) -> None:
            await outgoing.put(text)

        async def writer() -> None:
            while True:
                text = await outgoing.get()
                await ws.send_text(text)

        writer_task = asyncio.ensure_future(writer())
        node_id: str | None = None
        try:
            # The first frame must be a hello.
            first = await ws.receive_text()
            hello = P.decode(first)
            if not isinstance(hello, P.Hello):
                await ws.close(code=4000, reason="expected hello")
                return
            node_id = hello.info.node_id
            await ctrl.register(hello.info, send)
            while True:
                raw = await ws.receive_text()
                await _handle_worker_message(ctrl, node_id, raw)
        except (WebSocketDisconnect, RuntimeError):
            pass
        except ValueError:
            with contextlib.suppress(Exception):
                await ws.close(code=4000, reason="bad message")
        finally:
            writer_task.cancel()
            if node_id is not None:
                await ctrl.unregister(node_id)

    @app.websocket("/ws/events")
    async def events_ws(ws: WebSocket) -> None:
        await ws.accept()
        queue: asyncio.Queue[str] = asyncio.Queue()

        async def sink(event: str, payload: dict) -> None:
            import json

            await queue.put(json.dumps({"event": event, "data": payload}))

        unsubscribe = ctrl.subscribe(sink)
        try:
            while True:
                text = await queue.get()
                await ws.send_text(text)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            unsubscribe()

    if with_dashboard:
        from ..dashboard.routes import mount_dashboard

        mount_dashboard(app, ctrl)

    return app


async def _handle_worker_message(ctrl: Controller, node_id: str, raw: str) -> None:
    msg = P.decode(raw)
    if isinstance(msg, P.Heartbeat):
        ctrl.touch(node_id)
    elif isinstance(msg, P.TaskLog):
        await ctrl.on_task_log(node_id, msg)
    elif isinstance(msg, P.TaskUpdate):
        await ctrl.on_task_update(node_id, msg)
    # hello after the first is ignored; the controller already knows this node.
