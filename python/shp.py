"""
Type stubs for the shp module.

This file provides type hints and documentation for IDE support.
The actual implementations are provided by the Rust bindings.
"""

from typing import Any, Optional


class ShipProgram:
    """Represents a program that can be executed."""
    
    def __call__(self, *args: str) -> 'ShipRunnable':
        """
        Create a runnable command with the given arguments.
        
        Args:
            *args: Command-line arguments to pass to the program
            
        Returns:
            A ShipRunnable that can be executed or piped
        """
        ...


class ShipRunnable:
    """
    Represents a command or pipeline that can be executed.
    
    In REPL mode, ShipRunnable objects are automatically executed
    when returned as the result of an expression.
    """
    
    def __call__(self) -> 'ShipResult':
        """
        Execute the command or pipeline.
        
        Returns:
            A ShipResult containing the exit code
        """
        ...
    
    def __or__(self, other: 'ShipRunnable') -> 'ShipRunnable':
        """
        Pipe this command's output to another command.
        
        Args:
            other: The command to receive the piped output
            
        Returns:
            A new ShipRunnable representing the pipeline
        """
        ...


class ShipResult:
    """Result of executing a command."""
    
    exit_code: int
    """The exit code returned by the command."""


class ShipEnv:
    """
    Dictionary-like access to environment variables.
    
    Supports getting, setting, deleting, and iterating over
    environment variables with type preservation.
    """
    
    def __getitem__(self, key: str) -> Any:
        """Get an environment variable value."""
        ...
    
    def __setitem__(self, key: str, value: Any) -> None:
        """Set an environment variable value."""
        ...
    
    def __delitem__(self, key: str) -> None:
        """Delete an environment variable."""
        ...
    
    def __contains__(self, key: str) -> bool:
        """Check if an environment variable exists."""
        ...
    
    def __len__(self) -> int:
        """Get the number of environment variables."""
        ...
    
    def keys(self) -> list[str]:
        """Get all environment variable names."""
        ...
    
    def values(self) -> list[Any]:
        """Get all environment variable values."""
        ...
    
    def items(self) -> list[tuple[str, Any]]:
        """Get all environment variable name-value pairs."""
        ...
    
    def get(self, key: str, default: Optional[Any] = None) -> Any:
        """Get an environment variable with an optional default."""
        ...


def prog(name: str) -> ShipProgram:
    """
    Create a program reference by name.
    
    Args:
        name: The name of the program to execute
        
    Returns:
        A ShipProgram that can be called with arguments
        
    Example:
        >>> ls = prog("ls")
        >>> ls("-la")  # Auto-executes in REPL
    """
    ...


def cmd(prog: ShipProgram, *args: str) -> ShipRunnable:
    """
    Create a runnable command with the given program and arguments.
    
    Args:
        prog: The program to execute
        *args: Command-line arguments
        
    Returns:
        A ShipRunnable that can be executed or piped
        
    Example:
        >>> cmd(prog("ls"), "-la")  # Auto-executes in REPL
    """
    ...


def pipe(cmd1: ShipRunnable, cmd2: ShipRunnable, *cmds: ShipRunnable) -> ShipRunnable:
    """
    Create a pipeline of commands.
    
    Args:
        cmd1: First command in the pipeline
        cmd2: Second command in the pipeline
        *cmds: Additional commands to append to the pipeline
        
    Returns:
        A ShipRunnable representing the complete pipeline
        
    Example:
        >>> pipe(cmd(prog("ls")), cmd(prog("grep"), "txt"))
    """
    ...


def sub(runnable: ShipRunnable) -> ShipRunnable:
    """
    Execute a command in a subshell.
    
    Args:
        runnable: The command to execute in a subshell
        
    Returns:
        A ShipRunnable that executes in a subshell
    """
    ...


def shexec(runnable: ShipRunnable) -> ShipResult:
    """
    Explicitly execute a runnable command.
    
    Args:
        runnable: The command to execute
        
    Returns:
        The result of the execution
    """
    ...


def get_env(key: str) -> Any:
    """
    Get an environment variable value.
    
    Args:
        key: The environment variable name
        
    Returns:
        The value, or None if not found
    """
    ...


def set_env(key: str, value: Any) -> None:
    """
    Set an environment variable value.
    
    Args:
        key: The environment variable name
        value: The value to set (str, int, float, None, or list)
    """
    ...


# Global environment variable dictionary
env: ShipEnv = ShipEnv()
"""Global dictionary-like interface to environment variables."""
