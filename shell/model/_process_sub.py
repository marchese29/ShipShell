from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Literal, Self

from ..environment import env
from ..util import try_close
from ._base import ShellRunnable, _fork_exec, _wait_child
from ._compound import Subshell
from ._types import IOConfig


class ProcessSubstitution:
    """Exposes a runnable's I/O as a /dev/fd/N path.

    Creates a pipe and forks a child process to run the command. The parent
    receives a file descriptor that can be accessed via path or fd properties.

    This is an internal class - users should use the ProcessInput/ProcessOutput
    context managers via .as_input() and .as_output() on ShellRunnable.

    Args:
        runnable: The command to run behind the file descriptor.
        mode: 'r' for input substitution <(cmd) - reading yields command's stdout
              'w' for output substitution >(cmd) - writing feeds command's stdin
    """

    # macOS requires high FD numbers for /dev/fd/N to work reliably after fork+exec.
    # Bash uses FDs starting at 63 for process substitution.
    _next_fd = 63

    def __init__(self, runnable: ShellRunnable, mode: Literal['r', 'w']):
        self._mode = mode
        self._pid: int
        self._fd: int
        self._waited = False

        # Create pipe and fork immediately (eager execution)
        pipe_r, pipe_w = os.pipe()
        sys.stdout.flush()
        sys.stderr.flush()

        if (pid := os.fork()) == 0:
            # Child process
            env.traps.reset_for_child()
            if mode == 'r':
                # <(cmd): child writes to pipe, parent reads
                os.close(pipe_r)
                _fork_exec(lambda: runnable._exec(IOConfig(stdout=pipe_w)))
            else:
                # >(cmd): child reads from pipe, parent writes
                os.close(pipe_w)
                _fork_exec(lambda: runnable._exec(IOConfig(stdin=pipe_r)))
        else:
            # Parent process - dup to high FD for macOS /dev/fd compatibility
            self._pid = pid
            if mode == 'r':
                os.close(pipe_w)
                low_fd = pipe_r
            else:
                os.close(pipe_r)
                low_fd = pipe_w

            # Move to high FD (bash uses 63+) for reliable /dev/fd/N access
            high_fd = ProcessSubstitution._next_fd
            ProcessSubstitution._next_fd += 1
            os.dup2(low_fd, high_fd)
            os.close(low_fd)
            self._fd = high_fd

    @property
    def fd(self) -> int:
        """The raw file descriptor number."""
        return self._fd

    @property
    def path(self) -> Path:
        """The /dev/fd/N path as a Path object."""
        return Path(f'/dev/fd/{self._fd}')

    def wait(self) -> int:
        """Wait for child process and close FD. Returns exit code."""
        if self._waited:
            return 0

        # Close the FD first (signals EOF to child if mode='w')
        try_close(self._fd)

        exit_code = _wait_child(self._pid)
        self._waited = True
        return exit_code

    def __del__(self):
        """Cleanup if wait() wasn't called."""
        if not self._waited:
            self.wait()


class _ProcessSubContext:
    """Base context manager for process substitution.

    Internal class - use ProcessInput or ProcessOutput instead.
    """

    _mode: Literal['r', 'w']  # Set by subclasses

    def __init__(self, runnable: ShellRunnable):
        self._runnable = runnable
        self._sub: ProcessSubstitution | None = None

    def __enter__(self) -> Self:
        self._sub = ProcessSubstitution(self._runnable, mode=self._mode)
        return self

    def __exit__(self, exc_type: type | None, exc_val: Exception | None, exc_tb: Any) -> bool:
        if self._sub:
            self._sub.wait()
        return False

    @property
    def path(self) -> Path:
        """The /dev/fd/N path as a Path object."""
        if self._sub is None:
            raise RuntimeError(f'{type(self).__name__} must be used as context manager')
        return self._sub.path

    @property
    def fd(self) -> int:
        """The raw file descriptor number."""
        if self._sub is None:
            raise RuntimeError(f'{type(self).__name__} must be used as context manager')
        return self._sub.fd


class ProcessInput(_ProcessSubContext):
    """Context manager for input process substitution <(cmd).

    Runs a command and provides access to its stdout via a file path.
    The path is only valid within the context manager block.

    Usage:
        with prog("echo")("hello").as_input() as inp:
            run(prog("cat")(inp.path))
    """

    _mode: Literal['r', 'w'] = 'r'


class ProcessOutput(_ProcessSubContext):
    """Context manager for output process substitution >(cmd).

    Runs a command and provides access to its stdin via a file path.
    The path is only valid within the context manager block.

    Usage:
        with prog("grep")("error").as_output() as out:
            run(prog("echo")("test") > out.path)
    """

    _mode: Literal['r', 'w'] = 'w'


def pyshexec(file: str | Path, *args: Any) -> Subshell:
    """
    Execute a Python script file in a subshell with sys.argv set up.

    The script runs in an isolated subshell environment. sys.argv[0] will
    be the script path, and subsequent elements will be the provided args.

    Equivalent to: sub(source(file, *args))

    Args:
        file: Path to the Python script (supports ~ expansion)
        *args: Arguments to pass to the script (available as sys.argv[1:])

    Returns:
        A Subshell runnable that can be executed or composed with other operations.

    Example:
        pyshexec("~/scripts/process.py", "input.txt", "--verbose")()
        result = pyshexec("script.py", "arg1")(silent=True)
    """
    # Circular: builtins -> model (BUILTIN_REGISTRY) -> builtins (source)
    from ..builtins import source  # noqa: PLC0415

    return Subshell(source(str(file), *[str(a) for a in args]))
