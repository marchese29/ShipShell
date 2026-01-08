"""
Core ShipShell Python functionality stub for IDE support.

This is a stub file that provides type hints and documentation for IDEs.
The actual implementation is in python/core.py and is loaded at runtime.
"""

from pathlib import Path
from typing import IO

__all__ = ["source", "wire_path_programs"]


def source(file: str | Path | IO[str], scope: str | None = None) -> None: ...


def wire_module(module: str, target: str | None = None) -> None: ...


def wire_path_programs() -> None: ...
