from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from ._base import ShellRunnable
from ._command import Command, InProcessCallable, resolve_builtin
from ._compound import Subshell
from ._types import FileLike

if TYPE_CHECKING:
    from ._compound import ConditionalChain, Negated, TracedRunnable
    from ._pipeline import Pipeline
    from ._process_sub import ProcessInput, ProcessOutput


class Program:
    """A program builder that can be called with arguments to create a Command.

    When used directly in operators without being called, auto-invokes with no args.
    This allows ergonomic usage like: ls | grep('hello')
    """

    def __init__(self, name: str):
        self._cmd = name

    def __call__(self, *args: Any, **env_overlay: Any) -> Command | InProcessCallable:
        return self.args(*args, **env_overlay)

    def args(self, *args: Any, **env_overlay: Any) -> Command | InProcessCallable:
        # Check if it's a builtin first
        if builtin_factory := resolve_builtin(self._cmd):
            builtin = builtin_factory(*args)
            if len(env_overlay) > 0:
                builtin.env(**env_overlay)
            return builtin

        # External command
        command = Command(self._cmd, *args)
        if len(env_overlay) > 0:
            command.env(**env_overlay)
        return command

    # Operator support: auto-call with no args when used in operators
    # Enables: ls | grep('hello'), ls + echo('ok'), etc.

    def __or__(self, other: Any) -> Pipeline:
        """Pipe: ls | grep('hello') - auto-calls ls with no args."""
        return self() | other

    def __ror__(self, other: Any) -> Pipeline:
        """Reverse pipe: my_func | ls - auto-calls ls with no args."""
        return other | self()

    def __add__(self, other: Any) -> ConditionalChain:
        """Conditional success: ls + echo('ok') - auto-calls ls with no args."""
        return self() + other

    def __radd__(self, other: Any) -> ConditionalChain:
        """Reverse conditional success: my_func + ls."""
        return other + self()

    def __sub__(self, other: Any) -> ConditionalChain:
        """Conditional failure: cmd - fallback."""
        return self() - other

    def __rsub__(self, other: Any) -> ConditionalChain:
        """Reverse conditional failure: my_func - cmd."""
        return other - self()

    def __lt__(self, source: FileLike) -> Command | InProcessCallable:
        """Stdin redirect: cat < 'file.txt' - auto-calls with no args."""
        return self() < source

    def __gt__(self, target: FileLike) -> Command | InProcessCallable:
        """Stdout redirect: ls > 'file.txt' - auto-calls with no args."""
        return self() > target

    def __rshift__(self, target: FileLike) -> Command | InProcessCallable:
        """Append redirect: ls >> 'file.txt' - auto-calls with no args."""
        return self() >> target

    # Methods delegated from ShellRunnable - enables autocomplete and clean stack traces

    def with_stdin(self, source: FileLike) -> Command | InProcessCallable:
        """Stdin redirect from file. Auto-calls with no args."""
        return self().with_stdin(source)

    def with_stdout(self, target: FileLike, append: bool = False) -> Command | InProcessCallable:
        """Stdout redirect to file. Auto-calls with no args."""
        return self().with_stdout(target, append)

    def with_stderr(self, target: FileLike, append: bool = False) -> Command | InProcessCallable:
        """Stderr redirect to file. Auto-calls with no args."""
        return self().with_stderr(target, append)

    def stdin_content(self, content: str | bytes | IO[Any]) -> Pipeline:
        """Pipe content to stdin. Auto-calls with no args."""
        return self().stdin_content(content)

    def env(self, **env_overlay: Any) -> Command | InProcessCallable:
        """Set environment variables. Auto-calls with no args."""
        return self().env(**env_overlay)

    def neg(self) -> Negated:
        """Negate exit code. Auto-calls with no args."""
        return self().neg()

    def if_success(self, other: ShellRunnable | Program | Callable[[], Any]) -> ConditionalChain:
        """Execute other only if this succeeds. Auto-calls with no args."""
        return self().if_success(other)

    def if_fail(self, other: ShellRunnable | Program | Callable[[], Any]) -> ConditionalChain:
        """Execute other only if this fails. Auto-calls with no args."""
        return self().if_fail(other)

    def as_input(self) -> ProcessInput:
        """Use stdout as input file <(cmd). Auto-calls with no args."""
        return self().as_input()

    def as_output(self) -> ProcessOutput:
        """Use stdin as output file >(cmd). Auto-calls with no args."""
        return self().as_output()

    def trace(self, display: str, prefix: str = '+ ') -> TracedRunnable:
        """Wrap with trace output. Auto-calls with no args."""
        return self().trace(display, prefix)


def cmd(prog: str | Path, *args: Any, **env_overlay: Any) -> Command | InProcessCallable:
    """Construct a command or builtin with the given name, args, and optional environment overlay"""
    program_name = prog if isinstance(prog, str) else str(prog)

    # Check if it's a builtin first
    if builtin_factory := resolve_builtin(program_name):
        builtin = builtin_factory(*args)
        if len(env_overlay) > 0:
            builtin.env(**env_overlay)
        return builtin

    # External command
    command = Command(program_name, *args)
    if len(env_overlay) > 0:
        command.env(**env_overlay)
    return command


def prog(name: str) -> Program:
    """Construct a program for the given program name"""
    return Program(name)


def sub(runnable: ShellRunnable) -> Subshell:
    """Creates a runnable that runs the given runnable in a subshell"""
    return Subshell(runnable)
