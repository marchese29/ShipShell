"""Low-level utilities shared across shell modules."""

from __future__ import annotations

import os


def try_close(fd: int) -> None:
    """Close a file descriptor, ignoring errors if already closed."""
    try:
        os.close(fd)
    except OSError:
        pass


def exit_code_from_status(status: int) -> int:
    """Convert a waitpid() status to an exit code using bash conventions.

    Normal exit: returns the exit code directly (0-255).
    Signal kill: returns 128 + signal number (e.g., SIGKILL=9 → 137).

    This matches bash's $? behavior, which many tools and scripts depend on.
    Wraps os.waitstatus_to_exitcode() and converts its negative signal
    values (-signum) to the bash convention (128+signum).
    """
    code = os.waitstatus_to_exitcode(status)
    if code < 0:
        return 128 + (-code)
    return code
