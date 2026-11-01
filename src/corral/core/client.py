"""A small HTTP client for the controller's REST API.

The CLI and the MCP server both drive the fleet through this, so there is one place that knows the
endpoint shapes. It is intentionally thin: each method maps to one route and returns parsed JSON.
"""

from __future__ import annotations

from typing import Any

import httpx

from .models import JobSpec


class ControllerClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8787", timeout: float = 620.0):
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def _get(self, path: str) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(self.base_url + path)
            r.raise_for_status()
            return r.json()

    async def _post(self, path: str, json: Any = None, params: dict | None = None) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.post(self.base_url + path, json=json, params=params)
            r.raise_for_status()
            return r.json()

    async def health(self) -> dict:
        return await self._get("/healthz")

    async def nodes(self) -> list[dict]:
        data = await self._get("/api/nodes")
        return data.get("nodes", [])

    async def jobs(self) -> list[dict]:
        data = await self._get("/api/jobs")
        return data.get("jobs", [])

    async def job(self, job_id: str) -> dict:
        return await self._get(f"/api/jobs/{job_id}")

    async def submit(self, spec: JobSpec, wait: bool = True, timeout: float = 600.0) -> dict:
        return await self._post(
            "/api/jobs",
            json=spec.model_dump(mode="json"),
            params={"wait": str(wait).lower(), "timeout": timeout},
        )

    async def cancel(self, job_id: str) -> dict:
        return await self._post(f"/api/jobs/{job_id}/cancel")
