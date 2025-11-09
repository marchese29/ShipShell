"""
Internal builtin implementations for ShipShell.

These are the actual implementations called by the shell when builtins
are executed. They are separate from the ergonomic wrappers in shp.ergo.builtins.
"""

from pathlib import Path
import os
import sys
from shp import env

# Directory stack for pushd/popd
_dirstack: list[Path] = []


def cd(path: str | Path | None = None) -> int:
    """
    Change the current working directory.

    Args:
        path: Target directory. None or empty means HOME, "-" means OLDPWD.

    Returns:
        Exit code (0 for success, non-zero for error)
    """
    # Handle cd - (change to previous directory)
    if path == "-":
        old = env.get("OLDPWD")
        if old is None:
            print("cd: OLDPWD not set")
            return 1
        path = old
        # Print the directory when using cd - (bash behavior)
        print(path)

    # Store current directory as OLDPWD before changing
    env["OLDPWD"] = env.get("PWD", Path.cwd())

    # Determine target directory
    if path is None or path == "":
        target = Path.home()
    else:
        target = Path(path).expanduser()

    # Change directory
    try:
        os.chdir(target)
    except (FileNotFoundError, NotADirectoryError, PermissionError) as e:
        print(f"cd: {e}")
        return 1

    new_dir = Path.cwd()

    # Update PWD in environment
    env["PWD"] = new_dir

    return 0


def pwd(physical: bool = False) -> int:
    """
    Get the current working directory.

    Args:
        physical: If True, return physical path (resolve symlinks).
                 Otherwise, return logical path from PWD.

    Returns:
        Exit code (always 0)
    """
    if physical:
        # Physical path: resolve all symlinks
        result = Path.cwd().resolve()
    else:
        # Logical path: get from ShipShell environment
        result = env.get("PWD", Path.cwd())

    print(result)
    return 0


def pushd(path: str | Path) -> int:
    """
    Push current directory onto stack and change to path.

    Args:
        path: Directory to change to

    Returns:
        Exit code from cd operation
    """
    if not path:
        print("pushd: no directory specified")
        return 1

    _dirstack.append(Path.cwd())
    exit_code = cd(path)
    if exit_code == 0:
        print(Path.cwd())
    return exit_code


def popd() -> int:
    """
    Pop directory from stack and change to it.

    Returns:
        Exit code (0 for success, 1 if stack empty)
    """
    if not _dirstack:
        print("popd: directory stack empty")
        return 1

    target = str(_dirstack.pop())
    exit_code = cd(target)
    if exit_code == 0:
        print(Path.cwd())
    return exit_code


def dirs() -> int:
    """
    Get the directory stack.

    Prints current directory first, followed by the stack.

    Returns:
        Exit code (always 0)
    """
    result = [Path.cwd()] + _dirstack
    for d in result:
        print(d)
    return 0


def exit(code: int | str = 0) -> int:
    """
    Exit the shell.

    Args:
        code: Exit code (default 0)

    Returns:
        Never returns (calls sys.exit)
    """
    try:
        exit_code = int(code) if code else 0
    except (ValueError, TypeError):
        exit_code = 1
    sys.exit(exit_code)


def quit(code: int | str = 0) -> int:
    """
    Exit the shell (alias for exit).

    Args:
        code: Exit code (default 0)

    Returns:
        Never returns (calls sys.exit)
    """
    return exit(code)
