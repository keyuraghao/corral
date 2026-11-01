"""The VM executor: run a job inside a guest VM of any OS, from a clean snapshot.

This is how the fleet reaches real operating systems, not only Linux containers. A VM worker manages
one guest. Before each job it reverts the guest to a named snapshot, so every job starts from the same
known state and leaves nothing behind, then runs the command inside the guest over a transport.

Two pieces, both pluggable, so Corral does not care which hypervisor or guest OS you run:

- A provider snapshots and powers the guest. Virsh (libvirt/KVM), VirtualBox, a generic command
  template, or a loopback no-op for a guest that needs no reverting (and for tests).
- A transport runs the command inside the guest. SSH to any guest that accepts it (Linux, BSD,
  Windows with an SSH server, macOS), or a local transport that runs on the worker host itself.

Corral installs nothing. The provider and transport shell out to tools you already have (`virsh`,
`VBoxManage`, `ssh`), and a worker only advertises the `vm` executor when it is configured with one.
"""

from __future__ import annotations

import asyncio
import shlex

from .executors import BaseExecutor, LogSink, RunHandle, _as_shell, spawn_and_stream
from .models import Executor, JobSpec, TaskResult, TaskState, now

# ----- providers: snapshot and power management -----


class VmProvider:
    """Manages the guest. Each method returns an argv to run, or None when there is nothing to do."""

    name = "base"

    def revert_argv(self, snapshot: str) -> list[str] | None:
        raise NotImplementedError

    def start_argv(self) -> list[str] | None:
        raise NotImplementedError


class LoopbackProvider(VmProvider):
    """A no-op provider: no revert, no power control. For a guest that needs none, and for tests."""

    name = "loopback"

    def revert_argv(self, snapshot: str) -> list[str] | None:
        return None

    def start_argv(self) -> list[str] | None:
        return None


class VirshProvider(VmProvider):
    """libvirt / KVM / QEMU via virsh."""

    name = "virsh"

    def __init__(self, domain: str, virsh_bin: str = "virsh"):
        self.domain = domain
        self.virsh_bin = virsh_bin

    def revert_argv(self, snapshot: str) -> list[str] | None:
        return [self.virsh_bin, "snapshot-revert", self.domain, snapshot, "--running"]

    def start_argv(self) -> list[str] | None:
        return [self.virsh_bin, "start", self.domain]


class VBoxProvider(VmProvider):
    """Oracle VirtualBox via VBoxManage."""

    name = "vbox"

    def __init__(self, vm: str, vbox_bin: str = "VBoxManage"):
        self.vm = vm
        self.vbox_bin = vbox_bin

    def revert_argv(self, snapshot: str) -> list[str] | None:
        return [self.vbox_bin, "snapshot", self.vm, "restore", snapshot]

    def start_argv(self) -> list[str] | None:
        return [self.vbox_bin, "startvm", self.vm, "--type", "headless"]


class CommandProvider(VmProvider):
    """A generic provider driven by shell command templates, for any hypervisor Corral does not know.

    The templates may contain `{snapshot}`, which is substituted before the command is split and run.
    """

    name = "command"

    def __init__(self, revert_tmpl: str = "", start_tmpl: str = ""):
        self.revert_tmpl = revert_tmpl
        self.start_tmpl = start_tmpl

    def revert_argv(self, snapshot: str) -> list[str] | None:
        if not self.revert_tmpl:
            return None
        return shlex.split(self.revert_tmpl.format(snapshot=snapshot))

    def start_argv(self) -> list[str] | None:
        if not self.start_tmpl:
            return None
        return shlex.split(self.start_tmpl)


# ----- transports: run the command inside the guest -----


class VmTransport:
    """Turns a job into the argv or shell command that runs it inside the guest."""

    name = "base"

    def command(self, spec: JobSpec) -> tuple[list[str] | None, str | None]:
        """Return (argv, shell_cmd); exactly one is set, matching spawn_and_stream."""

        raise NotImplementedError


class LocalTransport(VmTransport):
    """Run the command on the worker host itself. For a worker that already lives inside the guest, and
    for tests where there is no separate VM."""

    name = "local"

    def command(self, spec: JobSpec) -> tuple[list[str] | None, str | None]:
        if spec.shell:
            return None, _as_shell(spec.command)
        return list(spec.command), None


class SshTransport(VmTransport):
    """Run the command in the guest over SSH. The guest can be any OS with an SSH server. The command
    is handed to the guest's own shell, so this does not assume a POSIX shell on the worker host."""

    name = "ssh"

    def __init__(self, target: str, ssh_bin: str = "ssh", opts: list[str] | None = None):
        self.target = target
        self.ssh_bin = ssh_bin
        self.opts = opts or ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]

    def command(self, spec: JobSpec) -> tuple[list[str] | None, str | None]:
        remote = _as_shell(spec.command) if spec.shell else shlex.join(spec.command)
        return [self.ssh_bin, *self.opts, self.target, remote], None


# ----- the executor -----


def _completed_handle(state: TaskState, result: TaskResult) -> RunHandle:
    """A handle for a task that already finished (for example, when a revert failed before running)."""

    async def done() -> tuple[TaskState, TaskResult]:
        return state, result

    task = asyncio.ensure_future(done())

    async def noop() -> None:
        task.cancel()

    return RunHandle(task, noop)


async def _run_mgmt(argv: list[str], sink: LogSink, label: str) -> tuple[int, str]:
    """Run a management command to completion, forwarding its output to the sink as it goes."""

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except OSError as exc:
        await sink("stderr", f"[vm {label}] could not run {argv[0]!r}: {exc}")
        return 127, str(exc)
    assert proc.stdout is not None
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            break
        await sink("stdout", f"[vm {label}] " + raw.decode("utf-8", errors="replace").rstrip("\r\n"))
    await proc.wait()
    return proc.returncode or 0, ""


class VmExecutor(BaseExecutor):
    kind = Executor.VM

    def __init__(self, provider: VmProvider, transport: VmTransport, default_snapshot: str | None = None):
        self.provider = provider
        self.transport = transport
        self.default_snapshot = default_snapshot

    async def start(self, task_id: str, spec: JobSpec, sink: LogSink) -> RunHandle:
        snapshot = spec.vm_snapshot or self.default_snapshot

        # Revert to a clean snapshot first. A failed revert is fatal: running on an unknown state would
        # make the result meaningless.
        if snapshot:
            revert = self.provider.revert_argv(snapshot)
            if revert is not None:
                code, err = await _run_mgmt(revert, sink, "revert")
                if code != 0:
                    result = TaskResult(error=f"snapshot revert failed (exit {code}) {err}".strip(), finished_at=now())
                    return _completed_handle(TaskState.FAILED, result)

        # Make sure the guest is running. A non-zero here is tolerated (it is commonly "already
        # running"); we log it and carry on to the command, which will fail clearly if the guest is down.
        start = self.provider.start_argv()
        if start is not None:
            code, _ = await _run_mgmt(start, sink, "start")
            if code != 0:
                await sink("stderr", f"[vm start] exit {code} (continuing; guest may already be running)")

        argv, shell_cmd = self.transport.command(spec)
        return await spawn_and_stream(sink=sink, timeout_s=spec.timeout_s, argv=argv, shell_cmd=shell_cmd)


def build_provider(
    kind: str, *, name: str = "", revert_tmpl: str = "", start_tmpl: str = "", bin_override: str = ""
) -> VmProvider:
    kind = kind.lower()
    if kind == "loopback":
        return LoopbackProvider()
    if kind == "virsh":
        return VirshProvider(domain=name, virsh_bin=bin_override or "virsh")
    if kind == "vbox":
        return VBoxProvider(vm=name, vbox_bin=bin_override or "VBoxManage")
    if kind == "command":
        return CommandProvider(revert_tmpl=revert_tmpl, start_tmpl=start_tmpl)
    raise ValueError(f"unknown vm provider: {kind!r}")


def build_transport(target: str = "", *, ssh_bin: str = "ssh", opts: list[str] | None = None) -> VmTransport:
    """An empty target gives the local transport; any target gives SSH to it."""

    if not target:
        return LocalTransport()
    return SshTransport(target=target, ssh_bin=ssh_bin, opts=opts)
