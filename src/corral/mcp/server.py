"""The Corral MCP server.

This exposes the same controller the CLI drives, as tools an AI agent can call. The server is a thin
client of the controller's REST API: it holds no fleet state of its own, so an agent and a human
using the CLI see the same jobs and the same nodes.

A session reads as a pipeline: look at the fleet (`list_nodes`), fan a command out and wait for it
(`run`), inspect a specific job (`get_job`), or stop one (`cancel_job`). Every tool that starts work
honors the harness defaults: docker jobs run in a disposable, network-isolated container unless the
agent explicitly asks for a network.

Transports: stdio by default (Claude Desktop, Claude Code, Cursor), plus streamable-http and sse for
networked clients. HTTP transports bind to 127.0.0.1 by default.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from ..core.client import ControllerClient
from ..core.models import Executor, JobSpec

# The reference SDK renamed FastMCP to MCPServer in 2.x. Both expose the same .tool() decorator and
# .run(transport=...), so Corral supports either: try the 2.x name first, then fall back to 1.x.
try:
    from mcp.server.mcpserver import MCPServer as _McpServer  # mcp >= 2.0
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP as _McpServer  # type: ignore[attr-defined,no-redef]  # mcp >= 1.6, < 2.0
    except ImportError as exc:  # pragma: no cover - exercised only without the mcp extra
        raise SystemExit("The MCP server needs the 'mcp' extra. Install it with: uv pip install -e '.[mcp]'") from exc


def build_server(api: str) -> _McpServer:
    client = ControllerClient(base_url=api)
    mcp = _McpServer("corral")

    @mcp.tool()
    async def list_nodes() -> dict[str, Any]:
        """List every worker currently connected to the controller, with its OS, architecture, executors and labels. Call this first to see what the fleet can run."""
        return {"result": {"nodes": await client.nodes()}}

    @mcp.tool()
    async def run(
        command: list[str],
        executor: str = "local",
        image: str = "alpine:3.20",
        network: bool = False,
        shell: bool = False,
        timeout_s: float = 300.0,
        select_os: str | None = None,
        select_arch: str | None = None,
        select_labels: list[str] | None = None,
        name: str = "",
    ) -> dict[str, Any]:
        """Fan a command out to every connected node that matches the selector, wait for all of them, and return each node's exit code and output. executor is "local" (run on the host) or "docker" (run in a disposable, network-isolated container built from image). Set network=True only if the job genuinely needs one. Use select_os, select_arch and select_labels to target a subset; leave them empty to run on every matching node."""
        spec = JobSpec(
            command=command,
            shell=shell,
            executor=Executor(executor),
            image=image,
            network=network,
            timeout_s=timeout_s,
            select_os=select_os,
            select_arch=select_arch,
            select_labels=select_labels or [],
            select_executor=Executor(executor),
            name=name,
        )
        view = await client.submit(spec, wait=True, timeout=timeout_s + 20)
        return {"result": _summarise(view)}

    @mcp.tool()
    async def submit(
        command: list[str],
        executor: str = "local",
        image: str = "alpine:3.20",
        network: bool = False,
        shell: bool = False,
        timeout_s: float = 300.0,
        select_os: str | None = None,
        select_arch: str | None = None,
        select_labels: list[str] | None = None,
        name: str = "",
    ) -> dict[str, Any]:
        """Like run, but return immediately with the job id instead of waiting. Poll get_job to follow its progress. Use this for long jobs you do not want to block on."""
        spec = JobSpec(
            command=command,
            shell=shell,
            executor=Executor(executor),
            image=image,
            network=network,
            timeout_s=timeout_s,
            select_os=select_os,
            select_arch=select_arch,
            select_labels=select_labels or [],
            select_executor=Executor(executor),
            name=name,
        )
        view = await client.submit(spec, wait=False, timeout=timeout_s + 20)
        return {"result": {"job_id": view["job"]["job_id"], "state": view["job"]["state"]}}

    @mcp.tool()
    async def get_job(job_id: str) -> dict[str, Any]:
        """Fetch one job by id, with its per-node tasks, their states, exit codes and captured output."""
        return {"result": _summarise(await client.job(job_id))}

    @mcp.tool()
    async def list_jobs() -> dict[str, Any]:
        """List recent jobs, newest first, with their state and how many nodes each fanned out to."""
        jobs = await client.jobs()
        return {
            "result": {
                "jobs": [
                    {
                        "job_id": j["job_id"],
                        "name": j["spec"].get("name"),
                        "state": j["state"],
                        "tasks": len(j.get("task_ids", [])),
                    }
                    for j in jobs
                ]
            }
        }

    @mcp.tool()
    async def cancel_job(job_id: str) -> dict[str, Any]:
        """Cancel a running job. Any still-running container is killed and leaves nothing behind."""
        return {"result": await client.cancel(job_id)}

    return mcp


def _summarise(view: dict | None) -> dict[str, Any]:
    """Shape a job view into something compact for an agent: per-node result plus the output."""

    if not view or "job" not in view:
        return {"error": "no such job"}
    job = view["job"]
    out = {"job_id": job["job_id"], "state": job["state"], "name": job["spec"].get("name"), "nodes": []}
    for t in view.get("tasks", []):
        res = t.get("result", {})
        out["nodes"].append(
            {
                "node_id": t["node_id"],
                "state": t["state"],
                "exit_code": res.get("exit_code"),
                "error": res.get("error") or None,
                "output": "\n".join(line for _, line in t.get("log", [])),
            }
        )
    return out


def serve(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8765, api: str | None = None) -> None:
    api = api or os.environ.get("CORRAL_API", "http://127.0.0.1:8787")
    mcp = build_server(api)
    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport in ("streamable-http", "sse"):
        # mcp 2.x takes host/port as run kwargs; 1.x read them from .settings. Support both.
        settings = getattr(mcp, "settings", None)
        if settings is not None:
            settings.host = host
            settings.port = port
            mcp.run(transport=transport)  # type: ignore[call-overload]
        else:
            mcp.run(transport=transport, host=host, port=port)  # type: ignore[call-overload]
    else:
        raise SystemExit(f"unknown transport: {transport!r}")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Corral MCP server")
    ap.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http", "sse"])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument(
        "--api", default=None, help="Controller API base URL (default: $CORRAL_API or http://127.0.0.1:8787)"
    )
    args = ap.parse_args()
    serve(transport=args.transport, host=args.host, port=args.port, api=args.api)


# asyncio is used by the tool bodies via the client; keep the import referenced for clarity.
_ = asyncio

if __name__ == "__main__":
    main()
