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
    "ShipEnv",
    "prog",
    "cmd",
    "pipe",
    "sub",
    "shexec",
    "get_env",
    "set_env",
    "env",
]


class ShipResult:
    """Result of executing a command."""

    def __init__(self, exit_code: int = 0) -> None:
        self.exit_code = exit_code


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
