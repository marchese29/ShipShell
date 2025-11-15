"""
ShipShell Core API - Stubs for IDE support.

These stubs provide type hints and minimal implementations for use
outside of the ShipShell environment. In actual ShipShell, these are
replaced by Rust-native implementations.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ShipProgram",
    "ShipRunnable",
    "ShipResult",
    "CapturedResult",
    "ShipEnv",
    "prog",
    "cmd",
    "pipe",
    "sub",
    "shexec",
    "capture",
    "get_stdout",
    "get_stderr",
    "get_env",
    "set_env",
    "env",
    "repl",
]

# Import repl submodule for IDE support
from . import repl


class ShipResult:
    """Result of executing a command."""

    def __init__(self, exit_code: int = 0) -> None:
        self.exit_code = exit_code


class CapturedResult:
    """Result of capturing command output with file descriptors.

    This class manages the lifecycle of file descriptors for captured stdout
    and stderr. Each FD can only be consumed once - either by reading it as
    a string or by taking the raw FD for manual streaming.

    Attributes:
        exit_code: The exit code of the executed command.

    Examples:
        # Read stdout and stderr as strings
        result = capture(prog('ls')('/'))
        print(f"Exit code: {result.exit_code}")
        stdout = result.read_stdout()
        stderr = result.read_stderr()

        # Get raw file descriptors for streaming
        result = capture(prog('tail')('-f', 'logfile.txt'))
        stdout_fd = result.stdout_fd  # You must close this FD!
        # ... use os.read(stdout_fd, size) for streaming
        os.close(stdout_fd)
    """

    def __init__(self, exit_code: int = 0) -> None:
        """Initialize a captured result.

        Args:
            exit_code: The exit code of the command.
        """
        self.exit_code = exit_code

    def read_stdout(self) -> str:
        """Read all stdout and return as a string.

        This method consumes the stdout file descriptor and can only be
        called once. Subsequent calls will raise an error.

        Returns:
            The complete stdout content as a string.

        Raises:
            RuntimeError: If stdout has already been consumed.

        Examples:
            result = capture(prog('echo')('hello'))
            output = result.read_stdout()
            print(output)  # 'hello\\n'
        """
        raise NotImplementedError("CapturedResult only works in ShipShell REPL")

    def read_stderr(self) -> str:
        """Read all stderr and return as a string.

        This method consumes the stderr file descriptor and can only be
        called once. Subsequent calls will raise an error.

        Returns:
            The complete stderr content as a string.

        Raises:
            RuntimeError: If stderr has already been consumed.

        Examples:
            result = capture(prog('ls')('/nonexistent'))
            errors = result.read_stderr()
            print(f"Errors: {errors}")
        """
        raise NotImplementedError("CapturedResult only works in ShipShell REPL")

    @property
    def stdout_fd(self) -> int:
        """Get the raw stdout file descriptor for manual streaming.

        The FD will be automatically closed when the CapturedResult object
        is destroyed, but you may close it earlier if desired for resource
        management. This property consumes the FD and can only be accessed once.

        Returns:
            The raw file descriptor for stdout.

        Raises:
            RuntimeError: If stdout has already been consumed.

        Examples:
            import os
            result = capture(prog('cat')('largefile.txt'))
            fd = result.stdout_fd
            # Stream the output
            while True:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                # Process chunk...
            # Optional: close early for explicit cleanup
            os.close(fd)
        """
        raise NotImplementedError("CapturedResult only works in ShipShell REPL")

    @property
    def stderr_fd(self) -> int:
        """Get the raw stderr file descriptor for manual streaming.

        The FD will be automatically closed when the CapturedResult object
        is destroyed, but you may close it earlier if desired for resource
        management. This property consumes the FD and can only be accessed once.

        Returns:
            The raw file descriptor for stderr.

        Raises:
            RuntimeError: If stderr has already been consumed.

        Examples:
            import os
            result = capture(prog('command')())
            fd = result.stderr_fd
            # Read errors as they come
            error_data = os.read(fd, 1024)
            # FD will be auto-closed when result is garbage collected
        """
        raise NotImplementedError("CapturedResult only works in ShipShell REPL")


class ShipRunnable:
    """Represents a command or pipeline that can be executed."""

    def __call__(self) -> ShipResult:
        """Execute the command or pipeline."""
        raise NotImplementedError("ShipRunnable only works in ShipShell REPL")

    def __or__(self, other: ShipRunnable) -> ShipRunnable:
        """Pipe this command's output to another command."""
        raise NotImplementedError("Piping only works in ShipShell REPL")

    def __gt__(self, target: Any) -> ShipRunnable:
        """Redirect output to a file (truncate mode).

        Args:
            target: Either a string path or a file-like object with fileno()
        """
        raise NotImplementedError("Output redirection only works in ShipShell REPL")

    def __rshift__(self, target: Any) -> ShipRunnable:
        """Redirect output to a file (append mode).

        Args:
            target: Either a string path or a file-like object with fileno()
        """
        raise NotImplementedError("Output redirection only works in ShipShell REPL")

    def with_env(self, **env_vars: Any) -> ShipRunnable:
        """Apply environment variable overlay to this runnable.

        The environment variables are only set for the execution of this specific
        command and do not affect the parent shell's environment.

        Args:
            **env_vars: Environment variables to set. Supports str, int, bool,
                       Path, list, and other EnvValue types.

        Returns:
            A new ShipRunnable with the environment overlay applied.

        Examples:
            # Set single variable
            prog('printenv')('USER').with_env(USER='testuser')()

            # Set multiple variables
            prog('echo')('test').with_env(DEBUG='1', PATH='/custom/path')()

            # Use dict splatting
            env_dict = {'VAR1': 'value1', 'VAR2': 'value2'}
            prog('cmd').with_env(**env_dict)()

            # Stack overlays (they merge, later takes precedence)
            prog('cmd').with_env(A='1').with_env(B='2')()

            # Works on pipelines
            (prog('echo')('hello') | prog('cat')()).with_env(DEBUG='1')()
        """
        raise NotImplementedError("with_env() only works in ShipShell REPL")


class ShipProgram:
    """Represents a program that can be executed."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(self, *args: str) -> ShipRunnable:
        """Create a runnable command with the given arguments."""
        raise NotImplementedError("ShipProgram only works in ShipShell REPL")


class ShipEnv:
    """Dictionary-like access to environment variables."""

    def __getitem__(self, key: str) -> Any:
        """Get an environment variable value."""
        import os

        return os.environ.get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        """Set an environment variable value."""
        import os

        os.environ[key] = str(value)

    def __delitem__(self, key: str) -> None:
        """Delete an environment variable."""
        import os

        del os.environ[key]

    def __contains__(self, key: str) -> bool:
        """Check if an environment variable exists."""
        import os

        return key in os.environ

    def __len__(self) -> int:
        """Get the number of environment variables."""
        import os

        return len(os.environ)

    def keys(self) -> list[str]:
        """Get all environment variable names."""
        import os

        return list(os.environ.keys())

    def values(self) -> list[Any]:
        """Get all environment variable values."""
        import os

        return list(os.environ.values())

    def items(self) -> list[tuple[str, Any]]:
        """Get all environment variable name-value pairs."""
        import os

        return list(os.environ.items())

    def get(self, key: str, default: Any = None) -> Any:
        """Get an environment variable with an optional default."""
        import os

        return os.environ.get(key, default)


def prog(name: str) -> ShipProgram:
    """Create a program reference by name."""
    return ShipProgram(name)


def cmd(prog: ShipProgram, *args: str) -> ShipRunnable:
    """Create a runnable command with the given program and arguments."""
    raise NotImplementedError("cmd() only works in ShipShell REPL")


def pipe(cmd1: ShipRunnable, cmd2: ShipRunnable, *cmds: ShipRunnable) -> ShipRunnable:
    """Create a pipeline of commands."""
    raise NotImplementedError("pipe() only works in ShipShell REPL")


def sub(runnable: ShipRunnable) -> ShipRunnable:
    """Execute a command in a subshell."""
    raise NotImplementedError("sub() only works in ShipShell REPL")


def shexec(runnable: ShipRunnable) -> ShipResult:
    """Explicitly execute a runnable command."""
    return runnable()


def capture(runnable: ShipRunnable) -> CapturedResult:
    """Execute a runnable and capture its stdout and stderr.

    This function executes the command and returns a CapturedResult object
    with file descriptors for both stdout and stderr. The streams are captured
    independently and can be read separately.

    Args:
        runnable: The ShipRunnable to execute (command, pipeline, etc.)

    Returns:
        A CapturedResult containing exit_code and file descriptors for stdout/stderr.

    Examples:
        # Capture both streams
        result = capture(prog('ls')('/'))
        print(f"Exit: {result.exit_code}")
        print(f"Output: {result.read_stdout()}")
        print(f"Errors: {result.read_stderr()}")

        # Capture pipeline output
        result = capture(prog('echo')('hello\\nworld') | prog('grep')('world'))
        output = result.read_stdout()

        # Capture with environment overlay
        result = capture(prog('sh')('-c', 'echo $VAR').with_env(VAR='value'))
        print(result.read_stdout())
    """
    raise NotImplementedError("capture() only works in ShipShell REPL")


def get_stdout(runnable: ShipRunnable) -> str:
    """Execute a runnable and return its stdout as a string.

    This is a convenience function that executes the command, captures stdout,
    reads it as a string, and discards stderr. Equivalent to:
        capture(runnable).read_stdout()

    Args:
        runnable: The ShipRunnable to execute.

    Returns:
        The complete stdout output as a string.

    Examples:
        # Get command output
        output = get_stdout(prog('echo')('Hello World'))
        print(output)  # 'Hello World\\n'

        # Capture pipeline
        lines = get_stdout(prog('ls')('-1') | prog('head')('-n', '5'))

        # With environment
        path = get_stdout(prog('sh')('-c', 'echo $PATH').with_env(PATH='/usr/bin'))
    """
    raise NotImplementedError("get_stdout() only works in ShipShell REPL")


def get_stderr(runnable: ShipRunnable) -> str:
    """Execute a runnable and return its stderr as a string.

    This is a convenience function that executes the command, captures stderr,
    reads it as a string, and discards stdout. Equivalent to:
        capture(runnable).read_stderr()

    Args:
        runnable: The ShipRunnable to execute.

    Returns:
        The complete stderr output as a string.

    Examples:
        # Get error output
        errors = get_stderr(prog('ls')('/nonexistent'))
        print(errors)

        # Check for warnings
        warnings = get_stderr(prog('some_command')('--verbose'))
        if warnings:
            print(f"Warnings: {warnings}")
    """
    raise NotImplementedError("get_stderr() only works in ShipShell REPL")


def get_env(key: str) -> Any:
    """Get an environment variable value."""
    import os

    return os.environ.get(key)


def set_env(key: str, value: Any) -> None:
    """Set an environment variable value."""
    import os

    os.environ[key] = str(value)


# Global environment variable dictionary (stub implementation)
env = ShipEnv()
