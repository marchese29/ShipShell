from __future__ import annotations

import inspect
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, override

from ..environment import env, env_to_str
from ._base import ShellRunnable, _resolve_fd
from ._types import IOConfig, ShellResult

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

        # Prepare arguments and environment
        args = [str(self._program), *[str(arg) for arg in self._args]]
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
