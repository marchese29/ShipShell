"""
Shell built-in command wrappers.

This module provides ergonomic wrappers around ShipShell's builtin commands,
allowing them to be used in pipelines, subshells, and other compositions.
"""

from pathlib import Path
from typing import IO
import sys

from shp import prog, cmd

# Control what gets exported with "from ... import *"
__all__ = ["cd", "pwd", "pushd", "popd", "dirs", "source", "exit", "quit"]


# Builtin command wrappers using prog() for composability
cd = lambda path=None: prog("cd")(str(path) if path is not None else "")
pwd = lambda physical=False: prog("pwd")("-P" if physical else "")
pushd = lambda path: prog("pushd")(str(path))
popd = lambda: prog("popd")()
dirs = lambda: prog("dirs")()
exit = lambda code=0: prog("exit")(str(code))
quit = lambda code=0: prog("quit")(str(code))


def source(file: str | Path | IO[str]) -> None:
    """
    Execute Python code from a file or file-like object in the current namespace.

    Note: source is not a shell builtin, it's a pure Python function.
    """
    if isinstance(file, (str, Path)):
        # Resolve to absolute path so cd() calls in the file don't break relative paths
        abs_path = Path(file).expanduser().resolve()
        with open(abs_path) as f:
            exec(f.read(), globals())
    else:
        # File-like object
        exec(file.read(), globals())
