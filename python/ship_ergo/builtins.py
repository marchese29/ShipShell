"""
Shell built-in functions with full type annotations.

This module provides real implementations of shell built-ins that work
both in ShipShell and standalone Python environments.
"""

from pathlib import Path
from typing import IO, TypeVar, TYPE_CHECKING
import os
import sys

from shp import env

# Control what gets exported with "from ... import *"
__all__ = ["cd", "pwd", "pushd", "popd", "dirs", "source", "exit", "quit"]

# Type variable for silent() helper
T = TypeVar("T")

# Set of object IDs that should be silent in REPL (cleared after each evaluation)
_silent_ids: set[int] = set()


def _silent(value: T) -> T:
    """
    Mark a value as silent for the current REPL evaluation.

    Type checkers see: T -> T (identity function)
    Runtime: Adds object ID to transient silent set
    """
    if TYPE_CHECKING:
        return value  # Type checker sees this
    else:
        _silent_ids.add(id(value))
        return value


def _clear_silent_marks() -> None:
    """Internal: Clear silent markers after REPL evaluation."""
    _silent_ids.clear()


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
    new_dir = Path.cwd()
    env["PWD"] = new_dir  # Store as Path in ShipShell env
    return _silent(new_dir)


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
    result = cd(path)
    print(result)
    return _silent(result)


def popd() -> Path | None:
    """
    Pop directory from stack and change to it.

    Returns the new current directory, or None if stack was empty.
    """
    if not _dirstack:
        print("popd: directory stack empty")
        return None
    result = cd(_dirstack.pop())
    print(result)
    return _silent(result)


def dirs() -> list[Path]:
    """
    Get the directory stack.

    Returns a list with current directory first, followed by the stack.
    """
    result = [Path.cwd()] + _dirstack
    for d in result:
        print(d)
    return _silent(result)


def pwd(physical: bool = False) -> Path:
    """
    Get the current working directory.

    Args:
        physical: If True, return the physical path (resolve symlinks).
                 If False (default), return the logical path from PWD.

    Returns:
        Path object representing the current directory
    """
    if physical:
        # Physical path: resolve all symlinks
        result = Path.cwd().resolve()
    else:
        # Logical path: get from ShipShell environment (already a Path!)
        result = env.get("PWD", Path.cwd())

    print(result)
    return _silent(result)
