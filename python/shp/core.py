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
