"""The Corral command line.

Every capability of the engine is reachable here. The CLI is thin: it parses arguments, calls the
core, and formats the result with Rich. The subcommands:

    corral controller   start the controller daemon (and optionally the dashboard)
    corral worker       start a worker that dials into a controller
    corral run          submit a job, wait for it, and print each node's result
    corral nodes        list the live fleet
    corral jobs         list recent jobs
    corral job          show one job with its tasks and logs
    corral cancel       cancel a running job
    corral mcp          start the MCP server (see corral.mcp.server)
"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .core.client import ControllerClient
from .core.models import Executor, JobSpec

app = typer.Typer(
    name="corral",
    help="Herd a fleet of disposable worker nodes across machines and fan jobs out to them.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"corral {__version__}")
        raise typer.Exit


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show the version and exit."
    ),
) -> None:
    pass


# ----- controller -----


@app.command()
def controller(
    host: str = typer.Option("127.0.0.1", help="Address to bind. Keep on localhost unless you add your own auth."),
    port: int = typer.Option(8787, help="Port to bind."),
    dashboard: bool = typer.Option(False, "--dashboard", help="Also serve the read-only web dashboard at /."),
    db: str = typer.Option(
        None, "--db", help="SQLite file to persist jobs and tasks. Omit to keep them in memory only."
    ),
    token: str = typer.Option(
        None,
        "--token",
        envvar="CORRAL_TOKEN",
        help="Shared token required of workers and API clients. Omit to run open on localhost.",
    ),
) -> None:
    """Start the controller daemon."""

    import uvicorn

    from .core.controller import Controller
    from .core.server import build_app
    from .core.store import JobStore

    store = JobStore(db) if db else None
    application = build_app(Controller(store=store), with_dashboard=dashboard, auth_token=token)
    where = f"http://{host}:{port}"
    console.print(
        Panel.fit(
            f"[bold]Corral controller[/bold]\nAPI    {where}\nWorker ws://{host}:{port}/ws/worker"
            + (f"\nBoard  {where}/" if dashboard else "")
            + (f"\nStore  {db}" if db else "\nStore  memory only")
            + ("\nAuth   token required" if token else "\nAuth   open (localhost)"),
            border_style="cyan",
        )
    )
    uvicorn.run(application, host=host, port=port, log_level="warning")


# ----- worker -----


@app.command()
def worker(
    controller: str = typer.Option(
        "ws://127.0.0.1:8787", "--controller", "-c", help="Controller URL (ws:// or http://)."
    ),
    label: list[str] = typer.Option([], "--label", "-l", help="A tag a job selector can match. Repeatable."),
    node_id: str = typer.Option(None, "--node-id", help="Stable id for this worker. Default is random."),
    docker_bin: str = typer.Option("docker", help="The docker executable to use."),
    token: str = typer.Option(
        None,
        "--token",
        envvar="CORRAL_TOKEN",
        help="Shared token the controller requires. Omit when the controller runs open.",
    ),
) -> None:
    """Start a worker that dials into a controller and runs what it is told."""

    from .core.worker import Worker

    w = Worker(controller_url=controller, labels=list(label), node_id=node_id, docker_bin=docker_bin, token=token)
    console.print(f"[cyan]worker[/cyan] starting, dialing {controller} (labels: {', '.join(label) or 'none'})")
    try:
        asyncio.run(w.run())
    except KeyboardInterrupt:
        console.print("\n[dim]worker stopped[/dim]")


# ----- run a job -----


@app.command()
def run(
    command: list[str] = typer.Argument(..., help="The command to run. Put it after -- to avoid option parsing."),
    api: str = typer.Option("http://127.0.0.1:8787", help="Controller API base URL."),
    executor: Executor = typer.Option(Executor.LOCAL, "--executor", "-e", help="How to run: local or docker."),
    image: str = typer.Option("alpine:3.20", help="Docker image (docker executor only)."),
    network: bool = typer.Option(False, "--network", help="Give docker jobs a network. Off by default."),
    shell: bool = typer.Option(False, "--shell", help="Run the command through a shell instead of as argv."),
    timeout: float = typer.Option(300.0, help="Per-task timeout in seconds."),
    os_sel: str = typer.Option(None, "--os", help="Only nodes with this OS."),
    arch_sel: str = typer.Option(None, "--arch", help="Only nodes with this arch."),
    label_sel: list[str] = typer.Option([], "--select-label", help="Only nodes carrying all these labels."),
    name: str = typer.Option("", "--name", help="A label for the job in listings."),
    no_wait: bool = typer.Option(False, "--no-wait", help="Submit and return immediately."),
) -> None:
    """Fan a command out to every matching node and print each result."""

    spec = JobSpec(
        command=list(command),
        shell=shell,
        executor=executor,
        image=image,
        network=network,
        timeout_s=timeout,
        select_os=os_sel,
        select_arch=arch_sel,
        select_labels=list(label_sel),
        select_executor=executor,
        name=name,
    )
    client = ControllerClient(base_url=api)

    async def _go() -> dict:
        return await client.submit(spec, wait=not no_wait, timeout=timeout + 20)

    try:
        view = asyncio.run(_go())
    except Exception as exc:
        console.print(f"[red]could not reach controller at {api}:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_job_view(view, show_logs=True)


# ----- read commands -----


@app.command()
def nodes(api: str = typer.Option("http://127.0.0.1:8787", help="Controller API base URL.")) -> None:
    """List the live fleet."""

    client = ControllerClient(base_url=api)
    try:
        rows = asyncio.run(client.nodes())
    except Exception as exc:
        console.print(f"[red]could not reach controller at {api}:[/red] {exc}")
        raise typer.Exit(1) from exc
    if not rows:
        console.print("[dim]no workers connected[/dim]")
        return
    table = Table(title="Fleet")
    table.add_column("node", style="cyan")
    table.add_column("host")
    table.add_column("os/arch")
    table.add_column("executors")
    table.add_column("labels")
    table.add_column("running", justify="right")
    for n in rows:
        info = n["info"]
        table.add_row(
            info["node_id"],
            info.get("hostname", ""),
            f"{info.get('os', '?')}/{info.get('arch', '?')}",
            ",".join(info.get("executors", [])),
            ",".join(info.get("labels", [])) or "-",
            str(len(n.get("running_tasks", []))),
        )
    console.print(table)


@app.command()
def jobs(api: str = typer.Option("http://127.0.0.1:8787", help="Controller API base URL.")) -> None:
    """List recent jobs, newest first."""

    client = ControllerClient(base_url=api)
    try:
        rows = asyncio.run(client.jobs())
    except Exception as exc:
        console.print(f"[red]could not reach controller at {api}:[/red] {exc}")
        raise typer.Exit(1) from exc
    if not rows:
        console.print("[dim]no jobs yet[/dim]")
        return
    table = Table(title="Jobs")
    table.add_column("job", style="cyan")
    table.add_column("name")
    table.add_column("state")
    table.add_column("tasks", justify="right")
    for j in rows:
        table.add_row(j["job_id"], j["spec"].get("name") or "-", j["state"], str(len(j.get("task_ids", []))))
    console.print(table)


@app.command()
def job(job_id: str, api: str = typer.Option("http://127.0.0.1:8787", help="Controller API base URL.")) -> None:
    """Show one job with its tasks and logs."""

    client = ControllerClient(base_url=api)
    try:
        view = asyncio.run(client.job(job_id))
    except Exception as exc:
        console.print(f"[red]could not reach controller at {api}:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_job_view(view, show_logs=True)


@app.command()
def cancel(job_id: str, api: str = typer.Option("http://127.0.0.1:8787", help="Controller API base URL.")) -> None:
    """Cancel a running job."""

    client = ControllerClient(base_url=api)
    try:
        res = asyncio.run(client.cancel(job_id))
    except Exception as exc:
        console.print(f"[red]could not reach controller at {api}:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print("[green]cancelled[/green]" if res.get("cancelled") else "[yellow]no such job[/yellow]")


@app.command()
def testrun(
    command: list[str] = typer.Argument(..., help="The test command. Put it after -- to avoid option parsing."),
    api: str = typer.Option("http://127.0.0.1:8787", help="Controller API base URL."),
    executor: Executor = typer.Option(Executor.DOCKER, "--executor", "-e", help="local or docker."),
    image: str = typer.Option("python:3.12-slim", help="Docker image for the docker executor."),
    shell: bool = typer.Option(True, "--shell/--no-shell", help="Run the command through a shell."),
    network: bool = typer.Option(True, "--network/--no-network", help="Give the test a network (on by default here)."),
    timeout: float = typer.Option(900.0, help="Per-node timeout in seconds."),
    os_sel: str = typer.Option(None, "--os", help="Only nodes with this OS."),
    label_sel: list[str] = typer.Option([], "--select-label", help="Only nodes carrying all these labels."),
) -> None:
    """Run a test command across every matching node and report pass/fail per node."""

    from .workloads.testrunner import run_test_matrix

    client = ControllerClient(base_url=api)

    async def _go():
        return await run_test_matrix(
            client,
            list(command),
            executor=executor,
            image=image,
            shell=shell,
            network=network,
            timeout_s=timeout,
            select_os=os_sel,
            select_labels=list(label_sel),
        )

    try:
        result = asyncio.run(_go())
    except Exception as exc:
        console.print(f"[red]could not reach controller at {api}:[/red] {exc}")
        raise typer.Exit(1) from exc
    colour = "green" if result.passed else "red"
    console.print(Panel.fit(f"[{colour}]{result.summary_line()}[/{colour}]", border_style=colour))
    table = Table(title="Test matrix")
    table.add_column("node", style="cyan")
    table.add_column("result")
    table.add_column("exit", justify="right")
    for n in result.per_node:
        mark = "[green]pass[/green]" if n["passed"] else "[red]fail[/red]"
        table.add_row(n["node_id"], mark, str(n["exit_code"]))
    console.print(table)
    if not result.passed:
        raise typer.Exit(1)


@app.command()
def cve(
    recipe: str = typer.Option(..., "--recipe", "-r", help="Path to a JSON reproduction recipe."),
    api: str = typer.Option("http://127.0.0.1:8787", help="Controller API base URL."),
    out: str = typer.Option(None, "--out", "-o", help="Write the Markdown report to this path."),
    fetch: bool = typer.Option(True, "--fetch/--no-fetch", help="Fetch the public advisory for context."),
) -> None:
    """Reproduce a published CVE in a disposable sandbox and write a defender's report.

    For authorized, defensive use on infrastructure you control: confirm whether a version is affected,
    check that a patched version is not, and produce a write-up. The recipe supplies the images and the
    operator's own presence check; Corral holds no exploit payloads.
    """

    import json
    from pathlib import Path

    from .workloads.cve import CveRecipe, render_markdown, reproduce

    try:
        data = json.loads(Path(recipe).read_text(encoding="utf-8"))
        rec = CveRecipe.model_validate(data)
    except (OSError, ValueError) as exc:
        console.print(f"[red]could not load recipe:[/red] {exc}")
        raise typer.Exit(1) from exc

    client = ControllerClient(base_url=api)

    async def _go():
        return await reproduce(client, rec, fetch=fetch)

    try:
        report = asyncio.run(_go())
    except Exception as exc:
        console.print(f"[red]could not reach controller at {api}:[/red] {exc}")
        raise typer.Exit(1) from exc
    md = render_markdown(report)
    colour = "red" if report.exposure_confirmed else "green"
    console.print(Panel.fit(f"[{colour}]{report.verdict_line()}[/{colour}]", border_style=colour))
    if out:
        Path(out).write_text(md, encoding="utf-8")
        console.print(f"report written to [cyan]{out}[/cyan]")
    else:
        console.print(md)


@app.command()
def mcp(
    transport: str = typer.Option("stdio", help="stdio, streamable-http, or sse."),
    host: str = typer.Option("127.0.0.1", help="Bind address for HTTP transports."),
    port: int = typer.Option(8765, help="Port for HTTP transports."),
    api: str = typer.Option("http://127.0.0.1:8787", help="Controller API the tools drive."),
) -> None:
    """Start the MCP server so an AI agent can drive the fleet."""

    from .mcp.server import serve

    serve(transport=transport, host=host, port=port, api=api)


# ----- formatting -----


def _print_job_view(view: dict, show_logs: bool = False) -> None:
    if not view or "job" not in view:
        console.print("[yellow]no such job[/yellow]")
        return
    job = view["job"]
    tasks = view.get("tasks", [])
    state = job["state"]
    console.print(
        Panel.fit(
            f"[bold]{job['job_id']}[/bold]  {job['spec'].get('name') or ''}\nstate: {state}   tasks: {len(tasks)}",
            border_style="cyan" if state in ("done",) else "yellow",
        )
    )
    if state == "no_match":
        console.print("[yellow]no connected node matched this job's selector[/yellow]")
        return
    for t in tasks:
        res = t.get("result", {})
        code = res.get("exit_code")
        tstate = t["state"]
        colour = {"succeeded": "green", "failed": "red", "timeout": "red", "cancelled": "yellow"}.get(tstate, "white")
        header = f"[{colour}]{t['node_id']}[/{colour}]  {tstate}" + (f"  exit={code}" if code is not None else "")
        if res.get("error"):
            header += f"  [red]{res['error']}[/red]"
        console.print(header)
        if show_logs:
            for stream, line in t.get("log", []):
                prefix = "[dim]2>[/dim] " if stream == "stderr" else "   "
                console.print(f"  {prefix}{line}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
