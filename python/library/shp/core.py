"""
Core ShipShell Python functionality stub for IDE support.

This is a stub file that provides type hints and documentation for IDEs.
The actual implementation is in python/core.py and is loaded at runtime.
"""

from pathlib import Path
from typing import IO

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
    ...


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
    ...


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

    Args:
        module: Optional module name to wire programs into. If provided, all
                programs will be accessible as {module}.{program} instead of
                directly in the global namespace.

    Example:
        # Wire directly into global namespace
        wire_path_programs()
        ls('-la')
        cat('file.txt')

        # Wire into a module to avoid namespace pollution
        wire_path_programs('cmd')
        cmd.ls('-la')
        cmd.grep('pattern', 'file.txt')
    """
    ...
