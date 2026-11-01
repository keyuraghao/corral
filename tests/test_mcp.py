"""MCP surface tests.

The first checks that every expected tool is registered on the server. The second is a true
end-to-end: it calls the MCP `run` tool, which drives the live controller REST API, against a real
worker, and reads the result back the way an agent would.
"""

from __future__ import annotations

from corral.core.client import ControllerClient
from corral.core.worker import Worker
from corral.mcp.server import build_server

EXPECTED_TOOLS = {"list_nodes", "run", "submit", "get_job", "list_jobs", "cancel_job"}


async def test_all_tools_registered():
    server = build_server("http://127.0.0.1:1")
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS.issubset(names)


async def test_mcp_run_tool_drives_the_fleet(running_controller):
    import asyncio

    rc = running_controller
    client = ControllerClient(base_url=rc.api)
    worker = Worker(controller_url=rc.ws, labels=["mcp"], node_id="mcp-node")
    wtask = asyncio.ensure_future(worker.run())
    try:
        # Wait for the worker to register.
        deadline = asyncio.get_event_loop().time() + 10
        while asyncio.get_event_loop().time() < deadline:
            if await client.nodes():
                break
            await asyncio.sleep(0.1)

        server = build_server(rc.api)
        result = await server.call_tool("run", {"command": ["echo", "via-mcp"], "executor": "local"})
        # call_tool returns (content, structured) in mcp 2.x; be tolerant of shape.
        structured = _extract_structured(result)
        payload = structured.get("result", structured)
        assert payload["state"] == "done"
        assert payload["nodes"][0]["exit_code"] == 0
        assert "via-mcp" in payload["nodes"][0]["output"]
    finally:
        wtask.cancel()


def _extract_structured(result) -> dict:
    """call_tool return shape varies across SDK versions; pull out the structured dict."""

    if isinstance(result, tuple):
        for part in result:
            if isinstance(part, dict):
                return part
        result = result[-1]
    if isinstance(result, dict):
        return result
    structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    raise AssertionError(f"could not extract structured content from {type(result)}")
