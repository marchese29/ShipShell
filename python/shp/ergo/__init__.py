"""
ShipShell Ergonomics Package

This package provides built-in shell functions with IDE support.

Usage in ShipShell scripts:
    try:
        from ship_shell_marker import IN_SHIP_SHELL
    except ImportError:
        from shp.ergo import *  # Import for IDE support
        from shp import prog, env  # Import shell primitives separately
"""

# Import and re-export built-in functions
from .builtins import cd, pwd, pushd, popd, dirs, source, exit, quit

__all__ = [
    "cd",
    "pwd",
    "pushd",
    "popd",
    "dirs",
    "source",
    "exit",
    "quit",
]
