"""Defensive CVE reproduction workload.

This workload answers a defender's questions about one published vulnerability: is a given version
actually affected, does the patched version close it, and what does the evidence look like. It runs
entirely inside the harness's disposable, network-isolated containers, on infrastructure the operator
controls, one advisory at a time. It is built for authorized work: confirming exposure, validating a
patch, and producing a write-up for a team. It is not an exploit generator and holds no payloads of
its own.

The operator supplies a recipe: the CVE id, a container image pinned to the affected version, an
optional image with the fix, and a probe command. The probe is the operator's own check that the
vulnerable condition is present. By convention the probe exits zero when the condition is observed and
non-zero when it is not, so the same probe run against the affected and the patched image tells the
whole story:

    affected image  -> probe exits 0   => exposure CONFIRMED on that version
    patched image   -> probe exits != 0 => the fix closes it

The workload fetches the public advisory text for context (controller-side, where there is a network),
runs the probe in each environment through the controller, compares the outcomes, and renders a
Markdown report. Nothing about the target is assumed; the probe is whatever the operator wrote.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel, Field

from ..core.client import ControllerClient
from ..core.models import Executor, JobSpec

NVD_ENDPOINT = "https://services.nvd.nist.gov/rest/json/cves/2.0"


class CveRecipe(BaseModel):
    """A declarative, operator-authored reproduction recipe. No exploit code lives here, only the
    coordinates of the environment and the operator's own presence check."""

    cve_id: str
    summary: str = ""  # operator note; supplemented by the fetched advisory when available
    affected_image: str  # docker image pinned to the vulnerable version
    patched_image: str | None = None  # docker image carrying the fix, if the operator has one
    probe: list[str] = Field(default_factory=list)  # the presence check; exit 0 means "condition observed"
    probe_shell: bool = True
    network: bool = False  # the probe runs network-isolated by default
    timeout_s: float = 300.0
    select_labels: list[str] = Field(default_factory=list)
    select_os: str | None = None
    workdir: str = "/work"


@dataclass
class ProbeOutcome:
    ran: bool
    node_id: str = ""
    exit_code: int | None = None
    output: str = ""
    error: str = ""


@dataclass
class CveReport:
    recipe: CveRecipe
    advisory: dict = field(default_factory=dict)
    affected: ProbeOutcome = field(default_factory=lambda: ProbeOutcome(ran=False))
    patched: ProbeOutcome = field(default_factory=lambda: ProbeOutcome(ran=False))

    @property
    def exposure_confirmed(self) -> bool:
        return self.affected.ran and self.affected.exit_code == 0

    @property
    def patch_closes_it(self) -> bool | None:
        if not self.patched.ran:
            return None
        return self.patched.exit_code != 0

    def verdict_line(self) -> str:
        if self.exposure_confirmed:
            base = f"{self.recipe.cve_id}: exposure CONFIRMED on {self.recipe.affected_image}"
        else:
            base = f"{self.recipe.cve_id}: exposure NOT confirmed on {self.recipe.affected_image}"
        patch = self.patch_closes_it
        if patch is True:
            base += f"; patched image {self.recipe.patched_image} is not affected"
        elif patch is False:
            base += f"; patched image {self.recipe.patched_image} STILL affected"
        return base


async def fetch_advisory(cve_id: str, timeout: float = 15.0) -> dict:
    """Fetch the public advisory for context. Returns {} on any failure, so being offline is fine."""

    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(NVD_ENDPOINT, params={"cveId": cve_id})
            r.raise_for_status()
            data = r.json()
        vulns = data.get("vulnerabilities", [])
        if not vulns:
            return {}
        cve = vulns[0].get("cve", {})
        descriptions = cve.get("descriptions", [])
        english = next((d["value"] for d in descriptions if d.get("lang") == "en"), "")
        metrics = cve.get("metrics", {})
        score, severity = _best_cvss(metrics)
        refs = [r.get("url", "") for r in cve.get("references", [])][:8]
        return {
            "description": english,
            "cvss": score,
            "severity": severity,
            "published": cve.get("published", ""),
            "references": refs,
        }
    except (httpx.HTTPError, KeyError, ValueError):
        return {}


def _best_cvss(metrics: dict) -> tuple[float | None, str]:
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        entries = metrics.get(key)
        if entries:
            data = entries[0].get("cvssData", {})
            score = data.get("baseScore")
            severity = data.get("baseSeverity") or entries[0].get("baseSeverity", "")
            return score, severity
    return None, ""


async def _run_probe(client: ControllerClient, recipe: CveRecipe, image: str) -> ProbeOutcome:
    if not recipe.probe:
        return ProbeOutcome(ran=False, error="recipe has no probe command")
    spec = JobSpec(
        command=recipe.probe,
        shell=recipe.probe_shell,
        executor=Executor.DOCKER,
        image=image,
        network=recipe.network,
        workdir=recipe.workdir,
        timeout_s=recipe.timeout_s,
        select_labels=recipe.select_labels,
        select_os=recipe.select_os,
        select_executor=Executor.DOCKER,
        name=f"cve:{recipe.cve_id}:{image}",
    )
    view = await client.submit(spec, wait=True, timeout=recipe.timeout_s + 30)
    tasks = view.get("tasks", [])
    if not tasks:
        return ProbeOutcome(ran=False, error="no docker-capable node matched the recipe selector")
    t = tasks[0]
    res = t.get("result", {})
    return ProbeOutcome(
        ran=True,
        node_id=t["node_id"],
        exit_code=res.get("exit_code"),
        output="\n".join(line for _, line in t.get("log", [])),
        error=res.get("error", ""),
    )


async def reproduce(client: ControllerClient, recipe: CveRecipe, *, fetch: bool = True) -> CveReport:
    """Run the full defensive reproduction: advisory, affected probe, patched probe, verdict."""

    advisory = await fetch_advisory(recipe.cve_id) if fetch else {}
    affected = await _run_probe(client, recipe, recipe.affected_image)
    patched = ProbeOutcome(ran=False)
    if recipe.patched_image:
        patched = await _run_probe(client, recipe, recipe.patched_image)
    return CveReport(recipe=recipe, advisory=advisory, affected=affected, patched=patched)


def render_markdown(report: CveReport) -> str:
    """Render a defender-facing write-up. The content is evidence and context, no exploit payload."""

    r = report.recipe
    adv = report.advisory
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    lines.append(f"# {r.cve_id} reproduction report")
    lines.append("")
    lines.append(f"Generated {now} by Corral. For authorized, defensive use on infrastructure you control.")
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    lines.append(report.verdict_line())
    lines.append("")
    lines.append("## Advisory")
    lines.append("")
    if adv:
        if adv.get("cvss") is not None:
            lines.append(f"- CVSS base score: {adv['cvss']} ({adv.get('severity', '')})")
        if adv.get("published"):
            lines.append(f"- Published: {adv['published']}")
        if adv.get("description"):
            lines.append("")
            lines.append(adv["description"])
    else:
        lines.append("Advisory text was not fetched (offline or not found). Operator summary below.")
    if r.summary:
        lines.append("")
        lines.append(f"Operator note: {r.summary}")
    lines.append("")
    lines.append("## Environment")
    lines.append("")
    lines.append(f"- Affected image: `{r.affected_image}`")
    lines.append(f"- Patched image: `{r.patched_image}`" if r.patched_image else "- Patched image: not provided")
    lines.append(f"- Network during probe: {'enabled' if r.network else 'isolated (default)'}")
    lines.append(f"- Probe command: `{' '.join(r.probe)}`")
    lines.append("- Probe convention: exit 0 means the vulnerable condition was observed.")
    lines.append("")
    lines.append("## Observed outcomes")
    lines.append("")
    lines.append(_outcome_block("Affected image", report.affected))
    if report.patched.ran or r.patched_image:
        lines.append("")
        lines.append(_outcome_block("Patched image", report.patched))
    lines.append("")
    lines.append("## Reproduce it yourself")
    lines.append("")
    lines.append("```sh")
    net = "" if r.network else " --network none"
    probe = " ".join(r.probe)
    lines.append(f"docker run --rm{net} -w {r.workdir} {r.affected_image} /bin/sh -c {probe!r}")
    if r.patched_image:
        lines.append(f"docker run --rm{net} -w {r.workdir} {r.patched_image} /bin/sh -c {probe!r}")
    lines.append("```")
    if adv.get("references"):
        lines.append("")
        lines.append("## References")
        lines.append("")
        for ref in adv["references"]:
            lines.append(f"- {ref}")
    lines.append("")
    return "\n".join(lines)


def _outcome_block(title: str, outcome: ProbeOutcome) -> str:
    if not outcome.ran:
        return f"**{title}:** not run ({outcome.error or 'skipped'})"
    body = [f"**{title}:** ran on node `{outcome.node_id}`, probe exit code {outcome.exit_code}"]
    if outcome.error:
        body.append(f"- error: {outcome.error}")
    out = outcome.output.strip()
    if out:
        body.append("")
        body.append("```")
        body.append(out[:4000])
        body.append("```")
    return "\n".join(body)
