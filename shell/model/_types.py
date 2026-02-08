from __future__ import annotations

from pathlib import Path
from typing import Self

FileLike = int | str | Path


class RawArg(str):
    """String argument that bypasses shell expansions (tilde, etc.).

    Use raw() to create: prog('grep')(raw('~pattern'), 'file.txt')
    """

    pass


def raw(s: str) -> RawArg:
    """Mark a string argument as raw, bypassing shell expansions like tilde."""
    return RawArg(s)


class IOConfig:
    """Encapsulates I/O redirection configuration with mutable builder pattern.

    This is for REDIRECTING fds to targets (files, other fds, etc.).
    For CAPTURING output, use run(cmd, silent=True).

    Usage:
        io = IOConfig().with_stdin(pipe_fd).with_stdout('/tmp/out.txt')
        io = IOConfig(stdin=pipe_fd, stdout='/tmp/out.txt')
        io = IOConfig().with_fd(3, '/tmp/log.txt')  # Redirect fd 3 to file
    """

    def __init__(
        self,
        stdin: FileLike | None = None,
        stdout: FileLike | None = None,
        stderr: FileLike | None = None,
        append_out: bool = False,
        append_err: bool = False,
        extra_fds: dict[int, FileLike] | None = None,
    ):
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.append_out = append_out
        self.append_err = append_err
        self.extra_fds: dict[int, FileLike] = extra_fds or {}

    def with_stdin(self, source: FileLike) -> Self:
        """Set stdin source. Returns self for chaining."""
        self.stdin = source
        return self

    def with_stdout(self, target: FileLike, append: bool = False) -> Self:
        """Set stdout target. Returns self for chaining."""
        self.stdout = target
        self.append_out = append
        return self

    def with_stderr(self, target: FileLike, append: bool = False) -> Self:
        """Set stderr target. Returns self for chaining."""
        self.stderr = target
        self.append_err = append
        return self

    def with_fd(self, fd: int, target: FileLike) -> Self:
        """Redirect fd to target (file path or fd number). Returns self for chaining.

        Example: IOConfig().with_fd(3, '/tmp/log.txt')  # Redirect fd 3 to file
        Example: IOConfig().with_fd(3, 1)               # Redirect fd 3 to stdout
        """
        self.extra_fds[fd] = target
        return self

    def merge_over(self, base: IOConfig | None) -> IOConfig:
        """Return new IOConfig merging self over base (self takes precedence)."""
        if base is None:
            return IOConfig(
                self.stdin,
                self.stdout,
                self.stderr,
                self.append_out,
                self.append_err,
                dict(self.extra_fds),
            )
        merged_extra = {**base.extra_fds, **self.extra_fds}  # self wins
        return IOConfig(
            stdin=self.stdin if self.stdin is not None else base.stdin,
            stdout=self.stdout if self.stdout is not None else base.stdout,
            stderr=self.stderr if self.stderr is not None else base.stderr,
            append_out=self.append_out or base.append_out,
            append_err=self.append_err or base.append_err,
            extra_fds=merged_extra,
        )


class ShellResult:
    """Result of running a shell command.

    When a command runs through the PTY layer, stdout_path and stderr_path
    point to files containing the captured output. Use read_stdout() and
    read_stderr() to access captured content.
    """

    def __init__(
        self,
        exit_code: int,
        stdout_path: Path | None = None,
        stderr_path: Path | None = None,
    ):
        self.exit_code = exit_code
        self.stdout_path = stdout_path
        self.stderr_path = stderr_path

    def read_stdout(self) -> str:
        """Read captured stdout (trailing whitespace stripped). Returns '' if no capture."""
        if self.stdout_path is None:
            return ''
        try:
            return self.stdout_path.read_text().rstrip()
        except OSError:
            return ''

    def read_stderr(self) -> str:
        """Read captured stderr (trailing whitespace stripped). Returns '' if no capture."""
        if self.stderr_path is None:
            return ''
        try:
            return self.stderr_path.read_text().rstrip()
        except OSError:
            return ''

    def __bool__(self) -> bool:
        """True if command succeeded (exit code 0), False otherwise."""
        return self.exit_code == 0

    def __invert__(self) -> ShellResult:
        """Negate the result: success becomes failure and vice versa."""
        return ShellResult(1 if self.exit_code == 0 else 0)

    def __repr__(self) -> str:
        return f'ShellResult(exit_code={self.exit_code})'
