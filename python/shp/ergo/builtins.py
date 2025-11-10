"""
Shell built-in command wrappers.

This module provides ergonomic wrappers around ShipShell's builtin commands,
allowing them to be used in pipelines, subshells, and other compositions.
"""

from pathlib import Path

from shp import prog, ShipRunnable

# Control what gets exported with "from ... import *"
__all__ = ["cd", "pwd", "pushd", "popd", "dirs", "exit", "quit"]


# Builtin command wrappers using prog() for composability
def cd(path: str | Path | None = None) -> ShipRunnable:
    """Change directory. No args = HOME, '-' = OLDPWD, path = specific directory."""
    if path is None:
        return prog("cd")()
    else:
        return prog("cd")(str(path))


def pwd(physical: bool = False) -> ShipRunnable:
    """Print working directory. physical=True resolves symlinks."""
    if physical:
        return prog("pwd")("-P")
    else:
        return prog("pwd")()


def pushd(path: str | Path) -> ShipRunnable:
    return prog("pushd")(str(path))


def popd() -> ShipRunnable:
    return prog("popd")()


def dirs() -> ShipRunnable:
    return prog("dirs")()


def exit(code: int = 0) -> ShipRunnable:
    """Exit the shell with given exit code."""
    if code == 0:
        return prog("exit")()
    else:
        return prog("exit")(str(code))


def quit(code: int = 0) -> ShipRunnable:
    """Quit the shell (alias for exit)."""
    if code == 0:
        return prog("quit")()
    else:
        return prog("quit")(str(code))
