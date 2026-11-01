# PyInstaller spec for the Corral CLI (which also launches the controller, workers, MCP server and dashboard).
# Build with packaging/build.sh (Linux/macOS) or packaging/build.ps1 (Windows), not directly, so the
# version and smoke test stay in one place.

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

project_root = Path(SPECPATH).parent
pkg = project_root / "src" / "corral"

# Ship the dashboard template so "corral controller --dashboard" works from the frozen binary.
datas = [(str(pkg / "dashboard" / "templates" / "index.html"), "corral/dashboard/templates")]

# uvicorn and websockets load transports and protocols dynamically; pull them in explicitly.
hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("websockets")
    + ["corral.mcp.server", "corral.workloads.cve", "corral.workloads.testrunner"]
)

a = Analysis(
    [str(project_root / "packaging" / "entry.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="corral",
    console=True,
)
coll = COLLECT(exe, a.binaries, a.datas, name="corral")
