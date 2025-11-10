"""
Core ShipShell Python functionality.

This module contains fundamental shell features implemented in Python,
as opposed to Rust builtins or ergonomic wrappers.
"""

import sys
from pathlib import Path
from typing import IO

# Sanity check: __main__ must be available for REPL functionality
try:
    import __main__
except ImportError:
    print(
        "FATAL: Cannot import __main__ module. Python environment is broken.",
        file=sys.stderr,
    )
    sys.exit(1)

__all__ = ["source"]


def source(file: str | Path | IO[str]) -> None:
    """
    Execute Python code from a file or file-like object in the REPL's namespace.

    This is a Python-specific feature, not a shell builtin.

    Args:
        file: Path to a Python file, or a file-like object with a read() method

    Example:
        source('~/.shipshellrc')
        source(Path('/etc/shipshell/config.py'))
    """
    if isinstance(file, (str, Path)):
        # Resolve to absolute path so cd() calls in the file don't break relative paths
        abs_path = Path(file).expanduser().resolve()
        with open(abs_path) as f:
            exec(f.read(), __main__.__dict__)
    else:
        # File-like object
        exec(file.read(), __main__.__dict__)
