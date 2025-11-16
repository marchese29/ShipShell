"""
Core ShipShell Python functionality.

This module contains fundamental shell features implemented in Python,
as opposed to Rust builtins or ergonomic wrappers.
"""

import io
import keyword
import os
import sys
import types
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

__all__ = ["source", "wire_module", "wire_path_programs"]


def source(file: str | Path | IO[str], scope: str | None = None) -> None:
    """
    Execute Python code from a file or file-like object in the REPL's namespace.

    This is a Python-specific feature, not a shell builtin.

    Args:
        file: Path to a Python file, or a file-like object with a read() method
        scope: Optional module name to execute the code in. If provided, creates
               a module object and makes it accessible as __main__.{scope}

    Example:
        source('~/.shipshellrc')
        source(Path('/etc/shipshell/config.py'))

        # Execute in a module namespace
        source(some_file, scope='mymod')
        # Now accessible as mymod.* in __main__
    """
    # Read the code
    if isinstance(file, (str, Path)):
        # Resolve to absolute path so cd() calls in the file don't break relative paths
        abs_path = Path(file).expanduser().resolve()
        with open(abs_path) as f:
            code = f.read()
    else:
        # File-like object
        code = file.read()

    # Execute in appropriate namespace
    if scope is None:
        # Default: execute in __main__ namespace
        exec(code, __main__.__dict__)
    else:
        # Create or get module object
        if hasattr(__main__, scope):
            module = getattr(__main__, scope)
            if not isinstance(module, types.ModuleType):
                raise ValueError(
                    f"'{scope}' already exists in __main__ but is not a module"
                )
        else:
            # Create new module
            module = types.ModuleType(scope)
            setattr(__main__, scope, module)

        # Execute code in the module's namespace
        exec(code, module.__dict__)


def wire_module(module: str, target: str | None = None) -> None:
    """
    Wire all exported contents from a source module into a target module namespace.

    This function imports all contents (respecting __all__ if present) from a
    source module and makes them available in the target module namespace.

    Args:
        module: The module to import from (e.g., 'shp.builtins')
        target: The target module namespace to wire into (defaults to __main__)

    Example:
        # Wire shell builtins into 'c' module
        wire_module_contents('shp.builtins', 'c')
        c.cd('/tmp')
        c.pwd()

        # Wire custom utilities
        wire_module_contents('my_tools', 'utils')
        utils.my_function()
    """
    source(io.StringIO(f"from {module} import *"), scope=target)


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
