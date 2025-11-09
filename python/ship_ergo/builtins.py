"""
Shell built-in functions with full type annotations.

This module provides real implementations of shell built-ins that work
both in ShipShell and standalone Python environments.
"""

from pathlib import Path
from typing import IO
import os
import sys

# Control what gets exported with "from ... import *"
__all__ = ["cd", "pwd", "pushd", "popd", "dirs", "source", "exit", "quit"]

# Directory stack for pushd/popd
_dirstack: list[Path] = []


def cd(path: str | Path | None = None) -> Path:
    """
    Change the current working directory.

    Returns the new current directory as a Path object.
    """
    if path is None:
        target = Path.home()
    else:
        target = Path(path).expanduser()
    os.chdir(target)
    return Path.cwd()


def source(file: str | Path | IO[str]) -> None:
    """Execute Python code from a file or file-like object in the current namespace."""
    if isinstance(file, (str, Path)):
        # Resolve to absolute path so cd() calls in the file don't break relative paths
        abs_path = Path(file).expanduser().resolve()
        with open(abs_path) as f:
            exec(f.read(), globals())
    else:
        # File-like object
        exec(file.read(), globals())


def exit(code: int = 0) -> None:
    """Exit the shell."""
    sys.exit(code)


def quit(code: int = 0) -> None:
    """Exit the shell."""
    sys.exit(code)


def pushd(path: str | Path) -> Path:
    """
    Push current directory onto stack and change to path.

    Returns the new current directory as a Path object.
    """
    _dirstack.append(Path.cwd())
    return cd(path)


def popd() -> Path | None:
    """
    Pop directory from stack and change to it.

    Returns the new current directory, or None if stack was empty.
    """
    if not _dirstack:
        print("popd: directory stack empty")
        return None
    return cd(_dirstack.pop())


def dirs() -> list[Path]:
    """
    Get the directory stack.

    Returns a list with current directory first, followed by the stack.
    """
    return [Path.cwd()] + _dirstack


def pwd() -> Path:
    """Get the current working directory as a Path object."""
    return Path.cwd()
