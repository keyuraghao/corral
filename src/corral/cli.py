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
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True, help="Show the version and exit."),
) -> None:
    pass


# ----- controller -----


@app.command()
def controller(
    host: str = typer.Option("127.0.0.1", help="Address to bind. Keep on localhost unless you add your own auth."),
    port: int = typer.Option(8787, help="Port to bind."),
    dashboard: bool = typer.Option(False, "--dashboard", help="Also serve the read-only web dashboard at /."),
) -> None:
    """Start the controller daemon."""

    import uvicorn

    from .core.server import build_app

    application = build_app(with_dashboard=dashboard)
    where = f"http://{host}:{port}"
    console.print(Panel.fit(
        f"[bold]Corral controller[/bold]\nAPI    {where}\nWorker ws://{host}:{port}/ws/worker"
        + (f"\nBoard  {where}/" if dashboard else ""),
        border_style="cyan",
    ))
    uvicorn.run(application, host=host, port=port, log_level="warning")


# ----- worker -----


@app.command()
def worker(
    controller: str = typer.Option("ws://127.0.0.1:8787", "--controller", "-c", help="Controller URL (ws:// or http://)."),
    label: list[str] = typer.Option([], "--label", "-l", help="A tag a job selector can match. Repeatable."),
    node_id: str = typer.Option(None, "--node-id", help="Stable id for this worker. Default is random."),
    docker_bin: str = typer.Option("docker", help="The docker executable to use."),
) -> None:
    """Start a worker that dials into a controller and runs what it is told."""

    from .core.worker import Worker

    w = Worker(controller_url=controller, labels=list(label), node_id=node_id, docker_bin=docker_bin)
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
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]could not reach controller at {api}:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_job_view(view, show_logs=True)


@app.command()
def cancel(job_id: str, api: str = typer.Option("http://127.0.0.1:8787", help="Controller API base URL.")) -> None:
    """Cancel a running job."""

    client = ControllerClient(base_url=api)
    try:
        res = asyncio.run(client.cancel(job_id))
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]could not reach controller at {api}:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print("[green]cancelled[/green]" if res.get("cancelled") else "[yellow]no such job[/yellow]")


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
    console.print(Panel.fit(
        f"[bold]{job['job_id']}[/bold]  {job['spec'].get('name') or ''}\nstate: {state}   tasks: {len(tasks)}",
        border_style="cyan" if state in ("done",) else "yellow",
    ))
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
