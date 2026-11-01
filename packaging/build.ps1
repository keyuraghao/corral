# Build the frozen Corral binary on Windows, smoke-test it, and pack a portable zip with a checksum.
# Linux and macOS use build.sh.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = if ($env:PYTHON) { $env:PYTHON } else { "python" }
# corral is installed in the venv, so read the version straight from the package. No inner quotes,
# which Windows PowerShell would otherwise mangle when passing -c.
$version = & $python -c "import corral; print(corral.__version__)"
Write-Host "building corral $version for windows"

Remove-Item -Recurse -Force build/pyinstaller, dist/corral -ErrorAction SilentlyContinue
& $python -m PyInstaller --noconfirm --clean --distpath dist --workpath build/pyinstaller packaging/corral.spec

Write-Host "smoke-testing the frozen binary"
& ./dist/corral/corral.exe --version
& ./dist/corral/corral.exe --help | Out-Null

$bundle = "corral-$version-windows-x86_64.zip"
Write-Host "packing $bundle"
Compress-Archive -Path dist/corral -DestinationPath "dist/$bundle" -Force

# Hash and write the .sha256 through Python, so the script does not depend on Get-FileHash being
# present in whichever PowerShell the runner uses.
& $python -c "import hashlib,os,sys; p=sys.argv[1]; h=hashlib.sha256(open(p,'rb').read()).hexdigest(); open(p+'.sha256','w').write(h+'  '+os.path.basename(p)+'\n')" "dist/$bundle"
Write-Host "done: dist/$bundle"
