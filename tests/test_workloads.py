"""Tests for the test-runner and CVE-reproduction workloads.

The test-matrix tests run end to end against a real controller and worker. The CVE verdict and report
rendering are pure and tested directly. One CVE reproduction runs through real docker and skips when
docker is not present.
"""

from __future__ import annotations

import asyncio

import pytest

from _util import docker_linux_available, py_exit
from corral.core.client import ControllerClient
from corral.core.models import Executor
from corral.core.worker import Worker
from corral.workloads.cve import CveRecipe, CveReport, ProbeOutcome, render_markdown, reproduce
from corral.workloads.testrunner import run_test_matrix

_DOCKER = docker_linux_available()
skip_no_docker = pytest.mark.skipif(not _DOCKER, reason="docker with linux containers not available")


async def _worker(ws_url: str, node_id: str, labels=None):
    w = Worker(controller_url=ws_url, labels=labels or ["test"], node_id=node_id)
    return w, asyncio.ensure_future(w.run())


async def _await_nodes(client: ControllerClient, n: int, timeout=10.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if len(await client.nodes()) >= n:
            return
        await asyncio.sleep(0.1)
    raise AssertionError("workers did not register")


# ----- test matrix -----


async def test_matrix_all_pass(running_controller):
    rc = running_controller
    client = ControllerClient(base_url=rc.api)
    _w, t = await _worker(rc.ws, "tm-a")
    try:
        await _await_nodes(client, 1)
        result = await run_test_matrix(client, py_exit(0), executor=Executor.LOCAL, shell=False, network=False)
        assert result.passed is True
        assert result.passes == result.total == 1
    finally:
        t.cancel()


async def test_matrix_detects_failure(running_controller):
    rc = running_controller
    client = ControllerClient(base_url=rc.api)
    _w, t = await _worker(rc.ws, "tm-b")
    try:
        await _await_nodes(client, 1)
        result = await run_test_matrix(client, py_exit(1), executor=Executor.LOCAL, shell=False, network=False)
        assert result.passed is False
        assert result.passes == 0
    finally:
        t.cancel()


# ----- CVE verdict logic (pure) -----


def _report(affected_exit, patched_exit=None, patched_image=None):
    rec = CveRecipe(cve_id="CVE-0000-0001", affected_image="img:vuln", patched_image=patched_image, probe=["true"])
    affected = ProbeOutcome(ran=True, node_id="n1", exit_code=affected_exit, output="probe output")
    patched = ProbeOutcome(ran=patched_exit is not None, node_id="n1", exit_code=patched_exit)
    return CveReport(recipe=rec, advisory={}, affected=affected, patched=patched)


def test_exposure_confirmed_when_affected_probe_exits_zero():
    assert _report(0).exposure_confirmed is True
    assert _report(1).exposure_confirmed is False


def test_patch_closes_it_logic():
    assert _report(0, patched_exit=1, patched_image="img:fixed").patch_closes_it is True
    assert _report(0, patched_exit=0, patched_image="img:fixed").patch_closes_it is False
    assert _report(0).patch_closes_it is None


def test_render_markdown_contains_verdict_and_environment():
    md = render_markdown(_report(0, patched_exit=1, patched_image="img:fixed"))
    assert "CVE-0000-0001" in md
    assert "exposure CONFIRMED" in md
    assert "img:vuln" in md
    assert "img:fixed" in md
    assert "defensive" in md.lower()


def test_render_markdown_handles_no_probe_run():
    rec = CveRecipe(cve_id="CVE-0000-0002", affected_image="img:x", probe=["true"])
    report = CveReport(recipe=rec, advisory={}, affected=ProbeOutcome(ran=False, error="no node"))
    md = render_markdown(report)
    assert "not confirmed" in md.lower()
    assert "not run" in md.lower()


# ----- CVE reproduction through real docker -----


@skip_no_docker
async def test_cve_reproduction_confirms_a_controlled_condition(running_controller):
    rc = running_controller
    client = ControllerClient(base_url=rc.api)
    w = Worker(controller_url=rc.ws, labels=["cve"], node_id="cve-node")
    wt = asyncio.ensure_future(w.run())
    try:
        await _await_nodes(client, 1)
        # A controlled, offline probe: the "vulnerable condition" is simply that this is an alpine
        # image, which the probe confirms by exiting 0. This exercises the full reproduce path end to
        # end without pulling anything exotic or touching the network.
        recipe = CveRecipe(
            cve_id="CVE-0000-TEST",
            affected_image="alpine:3.20",
            probe=["test -f /etc/alpine-release"],
            probe_shell=True,
            network=False,
            select_labels=["cve"],
            timeout_s=60,
        )
        report = await reproduce(client, recipe, fetch=False)
        assert report.exposure_confirmed is True
        assert report.affected.node_id == "cve-node"
        md = render_markdown(report)
        assert "CVE-0000-TEST" in md
    finally:
        wt.cancel()
