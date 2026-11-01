#!/usr/bin/env bash
# Build the frozen Corral binary for the current OS, smoke-test it, and pack a portable tarball with a
# checksum. Run from anywhere; paths are resolved relative to the repo. Linux and macOS use this script;
# Windows uses build.ps1.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/.." && pwd)"
cd "$root"

python="${PYTHON:-python3}"
version="$("$python" -c 'import sys; sys.path.insert(0, "src"); import corral; print(corral.__version__)')"
os="$(uname -s | tr '[:upper:]' '[:lower:]')"
arch="$(uname -m)"
echo "building corral $version for $os/$arch"

rm -rf build/pyinstaller dist/corral
"$python" -m PyInstaller --noconfirm --clean \
  --distpath dist --workpath build/pyinstaller \
  packaging/corral.spec

echo "smoke-testing the frozen binary"
./dist/corral/corral --version
./dist/corral/corral --help >/dev/null

bundle="corral-${version}-${os}-${arch}.tar.gz"
echo "packing $bundle"
tar -czf "dist/$bundle" -C dist corral
( cd dist && sha256sum "$bundle" > "$bundle.sha256" 2>/dev/null || shasum -a 256 "$bundle" > "$bundle.sha256" )
echo "done: dist/$bundle"
