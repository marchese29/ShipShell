"""
Core ShipShell Python functionality stub for IDE support.

This is a stub file that provides type hints and documentation for IDEs.
The actual implementation is in python/core.py and is loaded at runtime.
"""

from pathlib import Path
from typing import IO

__all__ = ["source"]


def source(file: str | Path | IO[str]) -> None:
    """
    Execute Python code from a file or file-like object in the current namespace.

    This is a Python-specific feature, not a shell builtin.

    Args:
        file: Path to a Python file, or a file-like object with a read() method

    Example:
        source('~/.shipshellrc')
        source(Path('/etc/shipshell/config.py'))
    """
    ...


def wire_path_programs() -> None:
    """
    Auto-wire executable programs from PATH as callable Python functions.

    Scans all directories in shp.env['PATH'] and creates lambda wrappers
    for each executable program with a valid Python identifier name (that
    is not a Python reserved word).

    Each program is wired up as: {name} = lambda *args: prog('{name}')(*args)

    This makes system commands directly callable without needing to use prog()
    explicitly each time.

    Note: Built-in commands are skipped to preserve their ergonomic wrappers
    that are set up before user initialization scripts run.

    Example:
        wire_path_programs()
        # Now you can use commands directly:
        ls('-la')
        cat('file.txt')
        grep('pattern', 'file.txt')
    """
    ...
