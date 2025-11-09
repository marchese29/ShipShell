"""
ShipShell Ergonomics Package

This package provides IDE support for ShipShell scripts. It includes:
- Shell built-in functions (cd, pwd, pushd, popd, dirs, source, exit, quit)
- Shell functionality stubs (prog, cmd, pipe, env, etc.)

Usage in ShipShell scripts:
    try:
        IN_SHIP_SHELL  # Check if in actual ShipShell
    except NameError:
        from ship_ergo import *  # Import for IDE support
"""

# Import built-in functions
from .builtins import cd, pwd, pushd, popd, dirs, source, exit, quit

# Try to import real shell functionality, fall back to stubs
try:
    from shp import (
        ShipProgram,
        ShipRunnable,
        ShipResult,
        ShipEnv,
        prog,
        cmd,
        pipe,
        sub,
        shexec,
        get_env,
        set_env,
        env,
    )
except ImportError:
    # Not in ShipShell, use stubs for IDE support
    from .shell import (
        ShipProgram,
        ShipRunnable,
        ShipResult,
        ShipEnv,
        prog,
        cmd,
        pipe,
        sub,
        shexec,
        get_env,
        set_env,
        env,
    )

# Export all public symbols
__all__ = [
    # Built-in functions
    "cd",
    "pwd",
    "pushd",
    "popd",
    "dirs",
    "source",
    "exit",
    "quit",
    # Shell functionality
    "ShipProgram",
    "ShipRunnable",
    "ShipResult",
    "ShipEnv",
    "prog",
    "cmd",
    "pipe",
    "sub",
    "shexec",
    "get_env",
    "set_env",
    "env",
    # Detection constant
    "IN_SHIP_SHELL",
]

# Default to False when imported (will be True in actual ShipShell)
IN_SHIP_SHELL = False
