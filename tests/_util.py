"""Helpers for building portable test commands.

The worker runs a command on its host. In the test suite the worker is the local machine, which may
be Linux, Windows or macOS, so tests must not assume POSIX shell builtins like echo, sh or sleep.
These helpers build commands out of the running Python interpreter, which exists on every runner.
"""

from __future__ import annotations

import sys

PYTHON = sys.executable


def py(code: str) -> list[str]:
    """An argv that runs one line of Python on the worker host."""

    return [PYTHON, "-c", code]


def py_print(text: str) -> list[str]:
    return py(f"print({text!r})")


def py_exit(code: int) -> list[str]:
    return py(f"import sys; sys.exit({code})")


def py_sleep(seconds: float) -> list[str]:
    return py(f"import time; time.sleep({seconds})")


def py_stderr(text: str) -> list[str]:
    return py(f"import sys; print({text!r}, file=sys.stderr)")
