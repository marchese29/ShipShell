"""
Namespace wiring utilities for pysh.

Functions for wiring modules and PATH programs into Python namespaces,
making shell commands and utilities conveniently accessible.
"""

import keyword
import os
import types
from pathlib import Path
from typing import IO

from .environment import env
from .model import BUILTIN_REGISTRY, prog

__all__ = [
    'exec_in_scope',
    'wire_module',
    'wire_path_programs',
]


def exec_in_scope(code: str | IO[str], scope: str | None = None) -> None:
    """
    Execute Python code in a specific namespace scope.

    Args:
        code: Python code as a string or file-like object with read() method.
        scope: Optional module name. If provided, creates/uses a module object
               accessible as __main__.{scope}. If None, executes in __main__.
    """
    import __main__  # noqa: PLC0415 - must be imported at call time, not module load

    code_str: str = code.read() if hasattr(code, 'read') else code  # type: ignore[union-attr]

    if scope is None:
        exec(code_str, __main__.__dict__)
    else:
        if hasattr(__main__, scope):
            module = getattr(__main__, scope)
            if not isinstance(module, types.ModuleType):
                raise ValueError(f"'{scope}' already exists in __main__ but is not a module")
        else:
            module = types.ModuleType(scope)
            setattr(__main__, scope, module)
        exec(code_str, module.__dict__)


def wire_module(module: str, target: str | None = None) -> None:
    """
    Wire all exported contents from a source module into a target module namespace.

    This function imports all contents (respecting __all__ if present) from a
    source module and makes them available in the target module namespace.

    Args:
        module: The module to import from (e.g., 'shell.builtins')
        target: The target module namespace to wire into (defaults to __main__)

    Example:
        # Wire shell builtins into 'c' module
        wire_module('shell.builtins', 'c')
        c.cd('/tmp')
        c.pwd()

        # Wire custom utilities
        wire_module('my_tools', 'utils')
        utils.my_function()
    """
    exec_in_scope(f'from {module} import *', scope=target)


def wire_path_programs(target: str | None = None) -> None:
    """
    Auto-wire executable programs from PATH as Program objects.

    Scans all directories in env['PATH'] and creates Program objects
    for each executable program with a valid Python identifier name (that
    is not a Python reserved word).

    Each program is wired up as: {name} = prog('{name}')

    Program objects support:
    - Calling with args: ls('-la') returns a Command
    - Direct use in operators: ls | grep('pattern') auto-calls with no args
    - Conditional chaining: cmd + ls or cmd - ls

    Note: Built-in commands are skipped to preserve their ergonomic wrappers
    that are set up before user initialization scripts run.

    Args:
        target: Optional module namespace to wire into. If None, wires into __main__.

    Example:
        wire_path_programs()
        # Now you can use commands directly:
        ls('-la')
        cat('file.txt')
        grep('pattern', 'file.txt')
        # Or in pipelines without calling:
        ls | grep('py') | wc('-l')
    """
    # Get PATH from environment
    path_list = env.get('PATH', [])
    if not path_list:
        return  # No PATH set, nothing to wire

    # Scan for executables with valid Python identifier names
    # Map from Python variable name -> actual program name
    executables: dict[str, str] = {}
    reserved_words = set(keyword.kwlist)

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
                if prog_name.replace('-', '_').isidentifier():
                    # If it's a reserved word, append underscore
                    if prog_name in reserved_words:
                        var_name = prog_name.replace('-', '_') + '_'
                    else:
                        var_name = prog_name.replace('-', '_')

                    # Only add if we haven't seen this var_name yet (PATH order)
                    if var_name not in executables:
                        executables[var_name] = prog_name

        except (PermissionError, OSError):
            # Skip directories we can't read
            continue

    # Get target namespace
    import __main__  # noqa: PLC0415 - must be imported at call time

    if target is None:
        namespace = __main__
    else:
        if hasattr(__main__, target):
            namespace = getattr(__main__, target)
            if not isinstance(namespace, types.ModuleType):
                raise ValueError(f"'{target}' already exists in __main__ but is not a module")
        else:
            namespace = types.ModuleType(target)
            setattr(__main__, target, namespace)

    # Wire Program objects directly into namespace
    # Skip built-ins to preserve their ergonomic wrappers
    for var_name, prog_name in executables.items():
        if var_name not in BUILTIN_REGISTRY:
            setattr(namespace, var_name, prog(prog_name))
