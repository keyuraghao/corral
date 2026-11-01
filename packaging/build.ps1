# Build the frozen Corral binary on Windows, smoke-test it, and pack a portable zip with a checksum.
# Linux and macOS use build.sh.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$version = & $python -c 'import sys; sys.path.insert(0, "src"); import corral; print(corral.__version__)'
Write-Host "building corral $version for windows"

Remove-Item -Recurse -Force build/pyinstaller, dist/corral -ErrorAction SilentlyContinue
& $python -m PyInstaller --noconfirm --clean --distpath dist --workpath build/pyinstaller packaging/corral.spec

Write-Host "smoke-testing the frozen binary"
& ./dist/corral/corral.exe --version
& ./dist/corral/corral.exe --help | Out-Null

$bundle = "corral-$version-windows-x86_64.zip"
Write-Host "packing $bundle"
Compress-Archive -Path dist/corral -DestinationPath "dist/$bundle" -Force
(Get-FileHash "dist/$bundle" -Algorithm SHA256).Hash | Out-File "dist/$bundle.sha256"
Write-Host "done: dist/$bundle"
