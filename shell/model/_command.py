from __future__ import annotations

import glob
import inspect
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, override

from ..environment import env, env_to_str
from ._base import ShellRunnable, _resolve_fd
from ._types import IOConfig, RawArg, ShellResult


def _expand_range(content: str) -> list[str] | None:
    """Expand a brace range expression like '1..5' or 'a..z' or '01..10..2'.

    Returns None if the content is not a valid range expression.
    Supports numeric ranges (with optional zero-padding and increment)
    and single-character alphabetic ranges.
    """
    if '..' not in content:
        return None

    parts = content.split('..')
    if len(parts) < 2 or len(parts) > 3:
        return None

    start = parts[0].strip()
    end = parts[1].strip()
    inc = parts[2].strip() if len(parts) == 3 else None

    try:
        increment = int(inc) if inc else 1
        if increment == 0:
            return None
    except ValueError:
        return None

    # Try numeric range
    try:
        start_num = int(start)
        end_num = int(end)

        pad_width = 0
        if len(start) > 1 and start[0] == '0':
            pad_width = len(start)
        if len(end) > 1 and end[0] == '0':
            pad_width = max(pad_width, len(end))

        if start_num <= end_num:
            return [str(n).zfill(pad_width) for n in range(start_num, end_num + 1, abs(increment))]
        else:
            return [str(n).zfill(pad_width) for n in range(start_num, end_num - 1, -abs(increment))]
    except ValueError:
        pass

    # Try single-character alphabetic range (no increment support for alpha in bash)
    if len(start) == 1 and len(end) == 1 and start.isalpha() and end.isalpha() and inc is None:
        start_ord = ord(start)
        end_ord = ord(end)
        if start_ord <= end_ord:
            return [chr(c) for c in range(start_ord, end_ord + 1)]
        else:
            return [chr(c) for c in range(start_ord, end_ord - 1, -1)]

    return None


def _split_commas(string: str) -> list[str]:
    """Split a string on commas, respecting brace nesting and escapes."""
    result: list[str] = []
    current = ''
    depth = 0
    i = 0

    while i < len(string):
        char = string[i]

        if char == '\\' and i + 1 < len(string) and string[i + 1] in ('{', '}', ',', '\\'):
            current += char + string[i + 1]
            i += 2
            continue

        if char == '{':
            depth += 1
            current += char
        elif char == '}':
            depth -= 1
            current += char
        elif depth == 0 and char == ',':
            result.append(current)
            current = ''
        else:
            current += char

        i += 1

    if len(current) > 0:
        result.append(current)

    return result


def _expand_braces(string: str) -> list[str]:
    """Expand brace expressions in a string.

    Handles comma lists ({a,b,c}), numeric ranges ({1..5}), alpha ranges ({a..e}),
    nested braces ({a,{b,c}}), and Cartesian products (x{a,b}y → xay xby).
    Single-item braces ({a}) and empty braces ({}) are treated as literals.
    """
    result: list[str] = ['']
    i = 0
    while i < len(string):
        char = string[i]

        if char == '\\' and i + 1 < len(string):
            next_char = string[i + 1]
            if next_char in ('{', '}', ',', '\\'):
                result = [r + next_char for r in result]
                i += 2
                continue
            else:
                result = [r + char for r in result]
                i += 1
                continue

        if char == '{':
            depth = 1
            j = i + 1

            while j < len(string) and depth > 0:
                if string[j] == '\\' and j + 1 < len(string) and string[j + 1] in ('{', '}', '\\'):
                    j += 2
                    continue

                if string[j] == '{':
                    depth += 1
                elif string[j] == '}':
                    depth -= 1
                j += 1

            if depth == 0:
                content = string[i + 1 : j - 1]

                range_result = _expand_range(content)
                if range_result is not None:
                    expanded_options = range_result
                else:
                    comma_split = _split_commas(content)

                    # Brace expansion only happens with comma OR range
                    # {a} and {} are literals
                    if len(comma_split) <= 1:
                        result = [r + '{' + content + '}' for r in result]
                        i = j
                        continue

                    expanded_options = []
                    for option in comma_split:
                        expanded_options.extend(_expand_braces(option))

                new_result = []
                for prefix in result:
                    for option in expanded_options:
                        new_result.append(prefix + option)
                result = new_result
                i = j
            else:
                result = [r + char for r in result]
                i += 1
        else:
            result = [r + char for r in result]
            i += 1

    # Return the input unchanged if expansion produced nothing useful
    if len(result) == 1 and result[0] == string:
        return [string]
    return result if result else [string]


def _expand_tilde_and_glob(s: str) -> list[str]:
    """Apply tilde expansion followed by glob expansion to a single string."""
    # Tilde expansion
    if s == '~' or s.startswith('~/'):
        s = str(Path(s).expanduser())
    # Glob expansion (only if arg contains glob metacharacters)
    if glob.has_magic(s):
        matches = sorted(glob.glob(s))
        return matches if matches else [s]
    return [s]


def _expand_arg(arg: Any) -> list[str]:
    """Expand shell-like shortcuts in a command argument.

    Expansion order matches bash: brace → tilde → glob.
    Brace expansion can produce multiple strings, each of which gets
    tilde + glob expanded independently. Use raw() to opt out of all expansion.
    """
    if isinstance(arg, RawArg):
        return [str(arg)]
    s = str(arg)
    # 1. Brace expansion (may produce multiple strings)
    brace_expanded = _expand_braces(s)
    # 2+3. Tilde + glob expansion on each result
    result: list[str] = []
    for item in brace_expanded:
        result.extend(_expand_tilde_and_glob(item))
    return result


# Registry of builtin commands - populated by @builtin_command decorator in builtins.py
BUILTIN_REGISTRY: dict[str, Callable[..., InProcessCallable]] = {}


def resolve_builtin(name: str) -> Callable[..., InProcessCallable] | None:
    """
    Resolve a builtin command by name.
    Returns the builtin factory function or None if not a builtin.
    Call the returned factory with arguments to get an InProcessCallable instance.
    """
    return BUILTIN_REGISTRY.get(name)


def resolve_cmd(name: str) -> Path | None:
    if name == '' or name == './':
        raise ValueError('Empty string cannot be expanded on PATH')

    if '/' in name:
        # Path from root
        if name.startswith('/'):
            path = Path(name)
            if path.exists() and path.is_file():
                return path
            return None

        # Deal with ~/file and ~user/file
        if name.startswith('~'):
            path = Path(name).expanduser()
            if path.exists() and path.is_file():
                return path
            return None

        # Check in the current directory
        path_name = name
        # Deal with ./file
        if name.startswith('./'):
            path_name = path_name[2:]
        path = Path.cwd() / path_name
        if path.exists() and path.is_file():
            return path

        return None

    env_path: list[Path] = env.get('PATH', [])
    for path_item in env_path:
        path = path_item / name
        if path.exists() and path.is_file():
            return path
    return None


class Command(ShellRunnable):
    def __init__(self, program: str, *args: Any):
        super().__init__()
        self._program = program
        self._args = args

    @override
    def _exec(self, io: IOConfig | None = None) -> ShellResult:
        # Merge: instance config takes precedence over passed io
        actual = self._io.merge_over(io)

        # Resolve the command
        command = resolve_cmd(self._program)
        if command is None:
            print(f'command not found: {self._program}', file=sys.stderr)
            return ShellResult(127)
        if not os.access(command, os.X_OK):
            print(f'permission denied: {self._program}', file=sys.stderr)
            return ShellResult(126)

        # Resolve redirections to file descriptors
        stdin_fd = _resolve_fd(actual.stdin, os.O_RDONLY, None)
        stdout_flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if actual.append_out else os.O_TRUNC)
        stdout_fd = _resolve_fd(actual.stdout, stdout_flags, None)
        stderr_flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if actual.append_err else os.O_TRUNC)
        stderr_fd = _resolve_fd(actual.stderr, stderr_flags, None)

        # Apply redirections (no save/restore needed - execve replaces process)
        if stdin_fd is not None and stdin_fd != 0:
            os.dup2(stdin_fd, 0)
            if stdin_fd > 2:
                os.close(stdin_fd)

        if stdout_fd is not None and stdout_fd != 1:
            os.dup2(stdout_fd, 1)
            if stdout_fd > 2:
                os.close(stdout_fd)

        if stderr_fd is not None and stderr_fd != 2:
            os.dup2(stderr_fd, 2)
            if stderr_fd > 2:
                os.close(stderr_fd)

        # Prepare arguments with tilde + glob expansion
        expanded: list[str] = []
        for a in self._args:
            expanded.extend(_expand_arg(a))
        args = [str(self._program), *expanded]
        env_vars = {k: env_to_str(v) for k, v in env.exported().items()}
        env_vars.update({k: env_to_str(v) for (k, v) in self._env_overlay.items()})

        # Replace current process
        os.execve(command, args, env_vars)

        # Should never reach here
        return ShellResult(127)


class InProcessCallable(ShellRunnable):
    """Runs a Python callable in the current process with FD redirection.

    Used for:
    - Shell builtins (echo, cd, pwd, etc.)
    - User-defined functions in pipelines

    Handles FD save/redirect/restore and interprets return values as exit codes.

    Examples:
        # As a builtin (created by @builtin_command decorator):
        echo = InProcessCallable(lambda: echo_impl(args=['hello']), name='echo')

        # In a pipeline:
        def upper():
            for line in sys.stdin:
                print(line.upper(), end='')
        prog('echo')('hello') | upper  # Auto-wrapped to InProcessCallable
    """

    def __init__(
        self,
        func: Callable[[], Any],
        *,
        name: str | None = None,
        is_atomic: bool = True,
    ):
        super().__init__()
        # Ban async callables - they need a fundamentally different execution model
        if inspect.iscoroutinefunction(func) or inspect.isasyncgenfunction(func):
            raise TypeError(
                f'Async callables cannot be used in pipelines: {func.__name__}. '
                'Use a synchronous function instead.'
            )
        self._func = func
        self._name = name  # Optional, for debugging/error messages
        self._is_atomic = is_atomic  # Whether DEBUG/TRACE/ERR traps should fire

    @override
    def _exec(self, io: IOConfig | None = None) -> ShellResult:
        with self._redirected(io):
            try:
                result = self._func()

                # Interpret return value as exit code
                if inspect.isgenerator(result):
                    for item in result:
                        print(item)
                    exit_code = 0
                elif result is None:
                    exit_code = 0
                elif isinstance(result, bool):
                    # Check bool before int since bool is a subclass of int
                    exit_code = 0 if result else 1
                elif isinstance(result, int):
                    exit_code = result
                else:
                    # Non-int return value treated as success
                    exit_code = 0
            except Exception:
                # Other exceptions during execution = failure
                exit_code = 1

            sys.stdout.flush()
            sys.stderr.flush()
            return ShellResult(exit_code)
