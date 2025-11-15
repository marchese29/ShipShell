"""
Core ShipShell Python functionality.

This module contains fundamental shell features implemented in Python,
as opposed to Rust builtins or ergonomic wrappers.
"""

import io
import keyword
import os
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

__all__ = ["source", "wire_path_programs"]


def source(file: str | Path | IO[str], scope: str | None = None) -> None:
    """
    Execute Python code from a file or file-like object in the REPL's namespace.

    This is a Python-specific feature, not a shell builtin.

    Args:
        file: Path to a Python file, or a file-like object with a read() method
        scope: Optional module to run the code in

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


def wire_path_programs(module: str | None = None) -> None:
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
    # Import shp here to avoid potential circular dependencies
    import shp

    # Skip shell built-ins that have ergonomic wrappers in shp.ergo.builtins
    # These are special because they're wired up before user init scripts run
    SKIP_BUILTINS = {
        "cd",
        "pwd",
        "pushd",
        "popd",
        "dirs",
        "exit",
        "quit",
        "which",
        "source",
    }

    # Get PATH from environment
    path_list = shp.env.get("PATH", [])
    if not path_list:
        return  # No PATH set, nothing to wire

    # Scan for executables with valid Python identifier names
    # Map from Python variable name -> actual program name
    executables = {}
    reserved_words = set(keyword.kwlist)  # Python reserved words

    for path_entry in path_list:
        # Convert to Path object if it's a string
        if isinstance(path_entry, str):
            path_dir = Path(path_entry)
        else:
            path_dir = path_entry

        # Skip if directory doesn't exist or can't be accessed
        if not path_dir.exists() or not path_dir.is_dir():
            continue

        try:
            # Scan directory for executable files
            for entry in path_dir.iterdir():
                # Skip directories, only process regular files
                if not entry.is_file():
                    continue

                # Check if file is executable
                if not os.access(entry, os.X_OK):
                    continue

                prog_name = entry.name

                # Check if name is a valid Python identifier
                if prog_name.replace("-", "_").isidentifier():
                    # If it's a reserved word, append underscore
                    if prog_name in reserved_words:
                        var_name = prog_name.replace("-", "_") + "_"
                    else:
                        var_name = prog_name.replace("-", "_")

                    # Only add if we haven't seen this var_name yet (PATH order)
                    if var_name not in executables:
                        executables[var_name] = prog_name

        except (PermissionError, OSError):
            # Skip directories we can't read
            continue

    # Build Python code string with lambda definitions
    # Skip built-ins to preserve their ergonomic wrappers
    code_lines = []
    for var_name, prog_name in sorted(executables.items()):
        # Skip if this is a builtin command
        if var_name not in SKIP_BUILTINS:
            code_lines.append(f"{var_name} = lambda *args: prog('{prog_name}')(*args)")

    # Execute the generated code via source()
    if code_lines:
        code_str = "\n".join(code_lines)
        source(io.StringIO(code_str), module)
