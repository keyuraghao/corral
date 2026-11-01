"""Frozen-binary entry point. PyInstaller runs this as __main__, and it hands off to the package so
corral's own relative imports resolve. Not used for the normal console script, which calls corral.cli:main directly."""

from corral.cli import main

if __name__ == "__main__":
    main()
