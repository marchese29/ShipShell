from __future__ import annotations

import inspect
import os
import sys
import warnings
from abc import ABC, abstractmethod
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any, Literal, Self, cast, override

from .environment import env, env_to_str
from .trap import TrapType

FileLike = int | str | Path


class ShellEscapingException(Exception):
    """Base for exceptions that escape InProcessCallable instead of becoming exit codes."""

    pass


class IOConfig:
    """Encapsulates stdin/stdout/stderr configuration with mutable builder pattern.

    Usage:
        io = IOConfig().with_stdin(pipe_fd).with_stdout('/tmp/out.txt')
        io = IOConfig(stdin=pipe_fd, stdout='/tmp/out.txt')
    """

    def __init__(
        self,
        stdin: FileLike | None = None,
        stdout: FileLike | None = None,
        stderr: FileLike | None = None,
        append_out: bool = False,
        append_err: bool = False,
    ):
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.append_out = append_out
        self.append_err = append_err

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

    def merge_over(self, base: IOConfig | None) -> IOConfig:
        """Return new IOConfig merging self over base (self takes precedence)."""
        if base is None:
            return IOConfig(
                self.stdin, self.stdout, self.stderr,
                self.append_out, self.append_err
            )
        return IOConfig(
            stdin=self.stdin if self.stdin is not None else base.stdin,
            stdout=self.stdout if self.stdout is not None else base.stdout,
            stderr=self.stderr if self.stderr is not None else base.stderr,
            append_out=self.append_out or base.append_out,
            append_err=self.append_err or base.append_err,
        )


class ShellResult:
    """Result of running a shell command."""

    def __init__(self, exit_code: int):
        self.exit_code = exit_code

    def __bool__(self) -> bool:
        """True if command succeeded (exit code 0), False otherwise."""
        return self.exit_code == 0

    def __invert__(self) -> ShellResult:
        """Negate the result: success becomes failure and vice versa."""
        return ShellResult(1 if self.exit_code == 0 else 0)

    def __repr__(self) -> str:
        return f'ShellResult(exit_code={self.exit_code})'


def _resolve_fd(
    target: FileLike | None, flags: int, default_fd: int | None = None
) -> int | None:
    """
    Convert a FileLike to an actual file descriptor.

    Args:
        target: The FileLike to resolve (int fd, str path, Path, or None)
        flags: OS flags to use when opening a file (e.g., os.O_RDONLY)
        default_fd: The fd to return if target is None

    Returns:
        A file descriptor (int) or None
    """
    if target is None:
        return default_fd

    if isinstance(target, int):
        return target

    # It's a Path or str - open it
    path = Path(target) if isinstance(target, str) else target
    return os.open(path, flags, 0o666)


def _wait_child(pid: int) -> int:
    """Wait for child process and return its exit code.

    Handles normal exit, signal termination, unexpected status, and
    ChildProcessError (returns 0 if child was already reaped).
    """
    try:
        _, status = os.waitpid(pid, 0)
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        elif os.WIFSIGNALED(status):
            return 128 + os.WTERMSIG(status)
        return 1
    except ChildProcessError:
        return 0


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


def run(runnable: ShellRunnable, io: IOConfig | None = None) -> ShellResult:
    """
    Execute a runnable, applying the given IO configuration.

    Args:
        runnable: The ShellRunnable to execute
        io: Optional IOConfig with stdin/stdout/stderr redirections.
            If raw fds are passed, they are the caller's responsibility to close.
            If paths are passed, they will be opened/closed by the runnable.
    """
    # Process any pending signals first
    env.traps.process_pending_signals()

    # Fire DEBUG before "atomic" commands (Command, InProcessCallable)
    # NOT for structural wrappers (Pipeline, Subshell, Negated, TracedRunnable)
    # InProcessCallable can opt out via is_atomic=False (e.g., function definitions)
    is_atomic = isinstance(runnable, Command) or (
        isinstance(runnable, InProcessCallable) and runnable._is_atomic
    )
    if is_atomic:
        env.current_runnable = runnable  # Set BEFORE DEBUG fires
        env.traps.fire(TrapType.DEBUG)

    if isinstance(command := runnable, Command):
        # Commands need to fork before _exec
        if (pid := os.fork()) == 0:
            # Child - _exec will resolve FileLike and replace this process
            command._exec(io)
            os._exit(127)  # Should never reach here
        else:
            # Parent - wait for child
            result = ShellResult(_wait_child(pid))
    else:
        # Everything else handles its own execution model
        result = runnable._exec(io)

    env.last_exit = result.exit_code

    # Update context AFTER execution
    if is_atomic:
        env.last_runnable = runnable
        env.current_runnable = None

        # Fire TRACE after atomic commands (regardless of exit code)
        # Save/restore last_exit around trap execution (bash preserves $? across traps)
        saved_exit = env.last_exit
        env.traps.fire(TrapType.TRACE)
        env.last_exit = saved_exit

        # Fire ERR on non-zero exit
        if result.exit_code != 0:
            saved_exit = env.last_exit
            env.traps.fire(TrapType.ERR)
            env.last_exit = saved_exit

    return result


class ShellRunnable(ABC):
    def __init__(self):
        self._env_overlay: dict[str, Any] = {}
        self._io = IOConfig()  # Single object for all IO configuration

    def __bool__(self) -> bool:
        """Warn when ShellRunnable is used in boolean context.

        Using a runnable in `if cmd:` or `cmd1 and cmd2` does NOT execute it.
        This warning helps catch a common mistake in the REPL.
        """
        warnings.warn(
            'ShellRunnable used in boolean context does not execute the command. '
            'Use cmd() to execute, or capture(cmd) to run and check the result.',
            UserWarning,
            stacklevel=2,
        )
        return True  # Runnables are truthy (they exist), just not executed

    @abstractmethod
    def _exec(self, io: IOConfig | None = None) -> ShellResult:
        """Execute with optional IO configuration."""
        ...

    @contextmanager
    def _redirected(
        self, io: IOConfig | None = None
    ) -> Generator[None, None, None]:
        """Context manager for FD redirection, merging instance config with passed config.

        Handles:
        - Merging self._io with passed io (self._io takes precedence)
        - Saving original FDs and Python file objects
        - Resolving FileLike (path/int) to actual FDs
        - Applying dup2 redirections + reassigning sys.stdin/stdout/stderr
        - Restoring everything on exit
        - Cleaning up on allocation failure (no FD leaks)
        """
        # Merge: instance config (self._io) takes precedence over passed io
        actual = self._io.merge_over(io)

        # Track FDs we opened (for cleanup on failure)
        fds_to_cleanup: list[int] = []

        # Saved state
        saved_stdin_fd: int | None = None
        saved_stdout_fd: int | None = None
        saved_stderr_fd: int | None = None
        saved_sys_stdin = sys.stdin
        saved_sys_stdout = sys.stdout
        saved_sys_stderr = sys.stderr

        try:
            # Track which FDs we're opening from paths (vs received as int)
            stdin_is_path = not isinstance(actual.stdin, int) and actual.stdin is not None
            stdout_is_path = not isinstance(actual.stdout, int) and actual.stdout is not None
            stderr_is_path = not isinstance(actual.stderr, int) and actual.stderr is not None

            # Resolve to FDs, tracking what we open
            stdin_fd = _resolve_fd(actual.stdin, os.O_RDONLY, None)
            if stdin_is_path and stdin_fd is not None:
                fds_to_cleanup.append(stdin_fd)

            stdout_flags = (
                os.O_WRONLY | os.O_CREAT | (os.O_APPEND if actual.append_out else os.O_TRUNC)
            )
            stdout_fd = _resolve_fd(actual.stdout, stdout_flags, None)
            if stdout_is_path and stdout_fd is not None:
                fds_to_cleanup.append(stdout_fd)

            stderr_flags = (
                os.O_WRONLY | os.O_CREAT | (os.O_APPEND if actual.append_err else os.O_TRUNC)
            )
            stderr_fd = _resolve_fd(actual.stderr, stderr_flags, None)
            if stderr_is_path and stderr_fd is not None:
                fds_to_cleanup.append(stderr_fd)

            # Save current state (only if we're actually going to redirect)
            if stdin_fd is not None and stdin_fd != 0:
                saved_stdin_fd = os.dup(0)
                fds_to_cleanup.append(saved_stdin_fd)
            if stdout_fd is not None and stdout_fd != 1:
                saved_stdout_fd = os.dup(1)
                fds_to_cleanup.append(saved_stdout_fd)
            if stderr_fd is not None and stderr_fd != 2:
                saved_stderr_fd = os.dup(2)
                fds_to_cleanup.append(saved_stderr_fd)

            # Flush before redirecting
            sys.stdout.flush()
            sys.stderr.flush()

            # Apply redirections
            if stdin_fd is not None and stdin_fd != 0:
                os.dup2(stdin_fd, 0)
                if stdin_is_path and stdin_fd > 2:
                    os.close(stdin_fd)
                    fds_to_cleanup.remove(stdin_fd)
                sys.stdin = os.fdopen(0, 'r', closefd=False)

            if stdout_fd is not None and stdout_fd != 1:
                os.dup2(stdout_fd, 1)
                if stdout_is_path and stdout_fd > 2:
                    os.close(stdout_fd)
                    fds_to_cleanup.remove(stdout_fd)
                sys.stdout = os.fdopen(1, 'w', closefd=False)

            if stderr_fd is not None and stderr_fd != 2:
                os.dup2(stderr_fd, 2)
                if stderr_is_path and stderr_fd > 2:
                    os.close(stderr_fd)
                    fds_to_cleanup.remove(stderr_fd)
                sys.stderr = os.fdopen(2, 'w', closefd=False)

            # Transfer saved fds out of cleanup list (they're managed by finally now)
            for fd in [saved_stdin_fd, saved_stdout_fd, saved_stderr_fd]:
                if fd is not None and fd in fds_to_cleanup:
                    fds_to_cleanup.remove(fd)

            yield  # Run the caller's code with redirected FDs

        except BaseException:
            # Cleanup all FDs we allocated before re-raising
            for fd in fds_to_cleanup:
                try:
                    os.close(fd)
                except OSError:
                    pass
            raise

        finally:
            # Restore original FDs and Python file objects
            if saved_stdin_fd is not None:
                os.dup2(saved_stdin_fd, 0)
                os.close(saved_stdin_fd)
                sys.stdin = saved_sys_stdin
            if saved_stdout_fd is not None:
                os.dup2(saved_stdout_fd, 1)
                os.close(saved_stdout_fd)
                sys.stdout = saved_sys_stdout
            if saved_stderr_fd is not None:
                os.dup2(saved_stderr_fd, 2)
                os.close(saved_stderr_fd)
                sys.stderr = saved_sys_stderr

    def __call__(self) -> ShellResult:
        return run(self)

    def __or__(self, value: ShellRunnable | Program | Callable[[], Any]) -> Pipeline:
        """
        Allows for building pipeline like `cmd("arg") | cmd2("arg2")`

        Also supports plain callables: `cmd("arg") | my_function`
        And uncalled Programs: `cmd("arg") | prog('cat')` auto-calls with no args
        """
        # Auto-call uncalled Programs (e.g., prog('cat') without ())
        if isinstance(value, Program):
            value = value()
        # Auto-wrap plain callables
        elif callable(value) and not isinstance(value, ShellRunnable):
            value = InProcessCallable(value)

        if isinstance(self, Pipeline):
            raise RuntimeError('Pipeline should override | operator')

        self = cast(NotPipeline, self)
        if isinstance(pipeline := value, Pipeline):
            return Pipeline([self, *pipeline.predecessors], pipeline.final_cmd)
        value = cast(NotPipeline, value)
        return Pipeline([self], value)

    def __ror__(self, value: Program | Callable[[], Any]) -> Pipeline:
        """
        Handle callable/Program | ShellRunnable (when on the left).

        Example: my_function | grep("pattern")
        """
        # Auto-call uncalled Programs
        if isinstance(value, Program):
            return value() | self
        if callable(value) and not isinstance(value, ShellRunnable):
            left = InProcessCallable(value)
            return left | self
        return NotImplemented

    def pipe(self, value: ShellRunnable) -> Pipeline:
        return self | value

    def __gt__(self, target: FileLike) -> Self:
        self._io.with_stdout(target, append=False)
        return self

    def __rshift__(self, target: FileLike) -> Self:
        self._io.with_stdout(target, append=True)
        return self

    def with_stdout(self, target: FileLike, append: bool = False) -> Self:
        self._io.with_stdout(target, append=append)
        return self

    def with_stderr(self, target: FileLike, append: bool = False) -> Self:
        self._io.with_stderr(target, append=append)
        return self

    def __lt__(self, source: FileLike) -> Self:
        self._io.with_stdin(source)
        return self

    def with_stdin(self, source: FileLike) -> Self:
        self._io.with_stdin(source)
        return self

    def stdin_content(self, content: str | bytes | IO[Any]) -> Pipeline:
        """Pipe content to stdin.

        Creates a pipeline where a writer callable feeds content to this command's stdin.

        Args:
            content: Content to pipe:
                - str: string content
                - bytes: binary content
                - IO: file-like object to read from

        Returns:
            Pipeline that writes content then runs this command.

        Example:
            prog('cat')().stdin_content('hello')()
            prog('wc')('-l').stdin_content(open('data.txt'))()
        """
        # String content
        if isinstance(content, str):
            text = content

            def write_str() -> None:
                print(text, end='')

            return InProcessCallable(write_str) | self

        # Bytes content
        if isinstance(content, bytes):
            data = content

            def write_bytes() -> None:
                sys.stdout.buffer.write(data)

            return InProcessCallable(write_bytes) | self

        # File-like object
        def write_filelike() -> None:
            data = content.read()
            if isinstance(data, str):
                print(data, end='')
            else:
                sys.stdout.buffer.write(data)

        return InProcessCallable(write_filelike) | self

    def env(self, **env_overlay: Any) -> Self:
        self._env_overlay.update(env_overlay)
        return self

    def neg(self) -> Negated:
        return Negated(self)

    def if_success(
        self, other: ShellRunnable | Program | Callable[[], Any]
    ) -> ConditionalChain:
        """Execute other only if this command succeeds (exit code 0).

        Equivalent to bash's && operator. Accepts ShellRunnable, Program, or callable.

        Example:
            prog('make')().if_success(prog('make')('install'))()
            prog('test')().if_success(lambda: print('passed'))()
            prog('true')().if_success(prog('echo'))  # auto-calls with no args
        """
        if isinstance(other, Program):
            other = other()
        elif callable(other) and not isinstance(other, ShellRunnable):
            other = InProcessCallable(other)
        return ConditionalChain(self, other, on_success=True)

    def if_fail(
        self, other: ShellRunnable | Program | Callable[[], Any]
    ) -> ConditionalChain:
        """Execute other only if this command fails (non-zero exit code).

        Equivalent to bash's || operator. Accepts ShellRunnable, Program, or callable.

        Example:
            prog('test')().if_fail(echo('test failed'))()
            prog('cmd')().if_fail(lambda: print('error'))()
            prog('false')().if_fail(prog('echo'))  # auto-calls with no args
        """
        if isinstance(other, Program):
            other = other()
        elif callable(other) and not isinstance(other, ShellRunnable):
            other = InProcessCallable(other)
        return ConditionalChain(self, other, on_success=False)

    def __add__(
        self, other: ShellRunnable | Program | Callable[[], Any]
    ) -> ConditionalChain:
        """Execute other only if this command succeeds. Alias for if_success().

        Example:
            (prog('make')() + prog('make')('install'))()
        """
        return self.if_success(other)

    def __radd__(self, other: Program | Callable[[], Any]) -> ConditionalChain:
        """Handle callable/Program + ShellRunnable (left side)."""
        if isinstance(other, Program):
            return other() + self
        if callable(other) and not isinstance(other, ShellRunnable):
            left = InProcessCallable(other)
            return left + self
        return NotImplemented

    def __sub__(
        self, other: ShellRunnable | Program | Callable[[], Any]
    ) -> ConditionalChain:
        """Execute other only if this command fails. Alias for if_fail().

        Example:
            (prog('test')() - prog('echo')('failed'))()
        """
        return self.if_fail(other)

    def __rsub__(self, other: Program | Callable[[], Any]) -> ConditionalChain:
        """Handle callable/Program - ShellRunnable (left side)."""
        if isinstance(other, Program):
            return other() - self
        if callable(other) and not isinstance(other, ShellRunnable):
            left = InProcessCallable(other)
            return left - self
        return NotImplemented

    def trace(self, display: str, prefix: str = '+ ') -> TracedRunnable:
        """Wrap with trace output (prints to stderr before execution)."""
        return TracedRunnable(self, display, prefix)

    def as_input(self) -> ProcessInput:
        """Use this command's stdout as an input file <(cmd).

        Returns a context manager. Access .path or .fd inside the with block.
        The child process starts immediately when entering the context.

        Example:
            with prog("ls")().as_input() as inp:
                run(prog("cat")(inp.path))
        """
        return ProcessInput(self)

    def as_output(self) -> ProcessOutput:
        """Use this command's stdin as an output file >(cmd).

        Returns a context manager. Access .path or .fd inside the with block.
        The child process starts immediately when entering the context.

        Example:
            with prog("grep")("error").as_output() as out:
                run(prog("echo")("test") > out.path)
        """
        return ProcessOutput(self)


class NoopRunnable(ShellRunnable):
    """A runnable that does nothing and returns a fixed exit code.

    Useful for cases like noclobber where the command should not run
    but execution should continue with a non-zero exit code.
    """

    def __init__(self, exit_code: int = 0):
        super().__init__()
        self._exit_code = exit_code

    @override
    def _exec(self, io: IOConfig | None = None) -> ShellResult:
        return ShellResult(self._exit_code)


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
        stdout_flags = (
            os.O_WRONLY | os.O_CREAT | (os.O_APPEND if actual.append_out else os.O_TRUNC)
        )
        stdout_fd = _resolve_fd(actual.stdout, stdout_flags, None)
        stderr_flags = (
            os.O_WRONLY | os.O_CREAT | (os.O_APPEND if actual.append_err else os.O_TRUNC)
        )
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
            except ShellEscapingException:
                # Let control flow exceptions propagate (return, errexit, etc.)
                raise
            except Exception:
                # Other exceptions during execution = failure
                exit_code = 1

            sys.stdout.flush()
            sys.stderr.flush()
            return ShellResult(exit_code)


class Pipeline(ShellRunnable):
    """Execute commands connected by pipes.

    Args:
        predecessors: Commands before the final stage (each runs in a forked process).
        final_cmd: The last command (runs in current process).
        pipefail: If True, pipeline fails if any stage fails (not just last).
        inherit_traps: If True, synthetic traps (DEBUG, ERR, RETURN, TRACE) are
            inherited by forked pipeline stages. EXIT is never inherited.
    """

    def __init__(
        self,
        predecessors: list[NotPipeline],
        final_cmd: NotPipeline,
        pipefail: bool = False,
        inherit_traps: bool = False,
    ):
        super().__init__()
        self.predecessors = predecessors
        self.final_cmd = final_cmd
        self.pipefail = pipefail
        self._inherit_traps = inherit_traps

    @override
    def _exec(self, io: IOConfig | None = None) -> ShellResult:
        # Merge: instance config takes precedence over passed io
        actual = self._io.merge_over(io)

        # Print all traces in order BEFORE forking to ensure deterministic output
        all_stages: list[NotPipeline] = [*self.predecessors, self.final_cmd]
        unwrapped: list[NotPipeline] = []
        for stage in all_stages:
            if isinstance(stage, TracedRunnable):
                print(f'{stage._prefix}{stage._display_text}', file=sys.stderr)
                unwrapped.append(cast(NotPipeline, stage._inner))
            else:
                unwrapped.append(stage)

        # Flush before forking to avoid duplicated output
        sys.stdout.flush()
        sys.stderr.flush()

        predecessors = unwrapped[:-1]
        final_cmd = unwrapped[-1]

        child_pids = []

        # Handle first predecessor - it gets stdin from pipeline
        first_stage = predecessors[0]
        pipe_r, pipe_w = os.pipe()
        os.set_inheritable(pipe_r, False)
        os.set_inheritable(pipe_w, False)

        if (pid := os.fork()) == 0:
            # Child process - handle trap inheritance
            if self._inherit_traps:
                env.traps.set(TrapType.EXIT, None)
            else:
                env.traps.reset_for_child()
            os.close(pipe_r)

            exit_code = 0
            try:
                # First stage gets actual.stdin
                result = first_stage._exec(
                    IOConfig(stdin=actual.stdin, stdout=pipe_w, stderr=actual.stderr)
                )
                exit_code = result.exit_code
            except SystemExit as e:
                exit_code = e.code if isinstance(e.code, int) else (1 if e.code else 0)
            except ShellEscapingException as e:
                exit_code = getattr(e, 'exit_code', 1)
            except Exception:
                exit_code = 1
            finally:
                os.close(pipe_w)
                try:
                    env.traps.fire(TrapType.EXIT)
                except Exception:
                    pass
                env.traps.cleanup()

            os._exit(exit_code)
        else:
            # Parent process
            child_pids.append(pid)
            os.close(pipe_w)

        # current_pipe_read starts as output from first stage
        current_pipe_read = pipe_r

        # Handle remaining predecessors - they get int pipe fds
        for stage in predecessors[1:]:
            pipe_r, pipe_w = os.pipe()
            os.set_inheritable(pipe_r, False)
            os.set_inheritable(pipe_w, False)

            if (pid := os.fork()) == 0:
                # Child process - handle trap inheritance
                if self._inherit_traps:
                    env.traps.set(TrapType.EXIT, None)
                else:
                    env.traps.reset_for_child()
                os.close(pipe_r)

                exit_code = 0
                try:
                    # Middle stages get int fd from previous pipe
                    result = stage._exec(
                        IOConfig(stdin=current_pipe_read, stdout=pipe_w, stderr=actual.stderr)
                    )
                    exit_code = result.exit_code
                except SystemExit as e:
                    exit_code = e.code if isinstance(e.code, int) else (1 if e.code else 0)
                except ShellEscapingException as e:
                    exit_code = getattr(e, 'exit_code', 1)
                except Exception:
                    exit_code = 1
                finally:
                    os.close(pipe_w)
                    os.close(current_pipe_read)
                    try:
                        env.traps.fire(TrapType.EXIT)
                    except Exception:
                        pass
                    env.traps.cleanup()

                os._exit(exit_code)
            else:
                # Parent process
                child_pids.append(pid)
                os.close(pipe_w)
                os.close(current_pipe_read)

                current_pipe_read = pipe_r

        # Execute final command in current process
        # Use run() so Commands fork and InProcessCallables run in current process
        result = run(
            final_cmd,
            IOConfig(stdin=current_pipe_read, stdout=actual.stdout, stderr=actual.stderr),
        )

        # Close the final input pipe
        os.close(current_pipe_read)

        # Wait for all children and collect exit codes
        exit_codes = [_wait_child(pid) for pid in child_pids]

        # If pipefail, use first non-zero exit code from pipeline stages
        if self.pipefail:
            for code in exit_codes:
                if code != 0:
                    result.exit_code = code
                    break

        return result

    @override
    def __or__(self, value: ShellRunnable | Program | Callable[[], Any]) -> Pipeline:
        # Auto-call uncalled Programs
        if isinstance(value, Program):
            value = value()
        # Auto-wrap plain callables
        elif callable(value) and not isinstance(value, ShellRunnable):
            value = InProcessCallable(value)

        # Pipelines flatten into one another
        if isinstance(pipeline := value, Pipeline):
            return Pipeline(
                [*self.predecessors, self.final_cmd, *pipeline.predecessors],
                pipeline.final_cmd,
                pipefail=self.pipefail or pipeline.pipefail,
            )
        value = cast(NotPipeline, value)
        return Pipeline([*self.predecessors, self.final_cmd], value, pipefail=self.pipefail)


class Subshell(ShellRunnable):
    """Execute a runnable in a forked subprocess.

    Args:
        runnable: The command/pipeline to execute in the subshell.
        inherit_traps: If True, synthetic traps (DEBUG, ERR, RETURN, TRACE) are
            inherited from the parent. EXIT is never inherited. Default False
            matches bash default; set True when errtrace/functrace are enabled.
    """

    def __init__(self, runnable: ShellRunnable, *, inherit_traps: bool = False):
        super().__init__()
        self._runnable = runnable
        self._inherit_traps = inherit_traps

    @override
    def _exec(self, io: IOConfig | None = None) -> ShellResult:
        # Merge: instance config takes precedence over passed io
        actual = self._io.merge_over(io)

        # Flush before forking to avoid duplicated output
        sys.stdout.flush()
        sys.stderr.flush()

        if (pid := os.fork()) == 0:
            # Child process - handle trap inheritance
            if self._inherit_traps:
                # Keep synthetic traps but clear EXIT (each subshell has its own)
                env.traps.set(TrapType.EXIT, None)
            else:
                # Default: clear all synthetic traps (bash default behavior)
                env.traps.reset_for_child()
            env.update(self._env_overlay)

            exit_code = 0
            try:
                result = run(self._runnable, actual)
                exit_code = result.exit_code
            except SystemExit as e:
                exit_code = e.code if isinstance(e.code, int) else (1 if e.code else 0)
            except ShellEscapingException as e:
                exit_code = getattr(e, 'exit_code', 1)
            except Exception:
                exit_code = 1
            finally:
                try:
                    env.traps.fire(TrapType.EXIT)
                except Exception:
                    pass
                env.traps.cleanup()

            os._exit(exit_code)
        else:
            # Parent process
            return ShellResult(_wait_child(pid))


class Negated(ShellRunnable):
    def __init__(self, runnable: ShellRunnable):
        super().__init__()
        self._runnable = runnable

    @override
    def _exec(self, io: IOConfig | None = None) -> ShellResult:
        # Merge: instance config takes precedence over passed io
        actual = self._io.merge_over(io)

        # Run and negate the result
        result = run(self._runnable, actual)
        return ~result


class ConditionalChain(ShellRunnable):
    """Execute second command conditionally based on first command's exit code.

    Used by if_success() and if_fail() methods for && and || chaining.

    Args:
        first: The command to run first.
        second: The command to run conditionally.
        on_success: If True, run second on success (&&). If False, run on failure (||).
    """

    def __init__(self, first: ShellRunnable, second: ShellRunnable, on_success: bool):
        super().__init__()
        self._first = first
        self._second = second
        self._on_success = on_success

    @override
    def _exec(self, io: IOConfig | None = None) -> ShellResult:
        actual = self._io.merge_over(io)

        # Run first command
        result = run(self._first, actual)

        # Check condition: run second if (success and on_success) or (fail and not on_success)
        should_run_second = (result.exit_code == 0) == self._on_success

        if should_run_second:
            return run(self._second, actual)
        else:
            return result


class TracedRunnable(ShellRunnable):
    """Wrapper that prints trace output before executing.

    Used by bash xtrace (-x) and can be used by Python for debugging.

    Trace output goes to sys.stderr, which reflects outer-scope redirections
    (from _redirected context). Per-command redirects don't affect trace
    because trace is written before the command's redirects are applied.
    """

    def __init__(self, inner: ShellRunnable, display_text: str, prefix: str = '+ '):
        super().__init__()
        self._inner = inner
        self._display_text = display_text
        self._prefix = prefix

    @override
    def _exec(self, io: IOConfig | None = None) -> ShellResult:
        print(f'{self._prefix}{self._display_text}', file=sys.stderr)
        sys.stderr.flush()
        # Merge our IO config with passed io so redirects propagate to inner
        actual = self._io.merge_over(io)
        return run(self._inner, actual)


NotPipeline = Command | InProcessCallable | Subshell | Negated | ConditionalChain | TracedRunnable


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

    def with_stdout(
        self, target: FileLike, append: bool = False
    ) -> Command | InProcessCallable:
        """Stdout redirect to file. Auto-calls with no args."""
        return self().with_stdout(target, append)

    def with_stderr(
        self, target: FileLike, append: bool = False
    ) -> Command | InProcessCallable:
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

    def if_success(
        self, other: ShellRunnable | Program | Callable[[], Any]
    ) -> ConditionalChain:
        """Execute other only if this succeeds. Auto-calls with no args."""
        return self().if_success(other)

    def if_fail(
        self, other: ShellRunnable | Program | Callable[[], Any]
    ) -> ConditionalChain:
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


class CapturedResult:
    """Result of a captured command execution with access to stdout/stderr."""

    def __init__(self, exit_code: int, stdout_fd: int, stderr_fd: int):
        self.exit_code = exit_code
        self._stdout_fd: int | None = stdout_fd
        self._stderr_fd: int | None = stderr_fd
        self._stdout_cache: str | None = None
        self._stderr_cache: str | None = None

    def __bool__(self) -> bool:
        """True if command succeeded (exit code 0)."""
        return self.exit_code == 0

    def read_stdout(self) -> str:
        """Read and return captured stdout as a string (trailing whitespace stripped)."""
        if self._stdout_cache is not None:
            return self._stdout_cache
        if self._stdout_fd is None:
            return ''
        try:
            with os.fdopen(self._stdout_fd, 'r') as f:
                self._stdout_cache = f.read().rstrip()
        finally:
            self._stdout_fd = None
        return self._stdout_cache

    def read_stderr(self) -> str:
        """Read and return captured stderr as a string (trailing whitespace stripped)."""
        if self._stderr_cache is not None:
            return self._stderr_cache
        if self._stderr_fd is None:
            return ''
        try:
            with os.fdopen(self._stderr_fd, 'r') as f:
                self._stderr_cache = f.read().rstrip()
        finally:
            self._stderr_fd = None
        return self._stderr_cache

    def __del__(self):
        """Clean up any unclosed file descriptors."""
        if self._stdout_fd is not None:
            try:
                os.close(self._stdout_fd)
            except OSError:
                pass
        if self._stderr_fd is not None:
            try:
                os.close(self._stderr_fd)
            except OSError:
                pass

    def __repr__(self) -> str:
        return f'CapturedResult(exit_code={self.exit_code})'


def capture(runnable: ShellRunnable) -> CapturedResult:
    """
    Execute a runnable and capture its stdout and stderr.

    Returns a CapturedResult with the exit code and methods to read
    the captured output.

    Example:
        result = capture(prog("ls")("-la"))
        print(result.read_stdout())
        if not result:
            print("Error:", result.read_stderr())
    """
    # Flush before forking to avoid duplicated output
    sys.stdout.flush()
    sys.stderr.flush()

    # Create pipes for stdout and stderr
    stdout_r, stdout_w = os.pipe()
    stderr_r, stderr_w = os.pipe()

    # Fork to run the command
    pid = os.fork()
    if pid == 0:
        # Child process
        os.close(stdout_r)
        os.close(stderr_r)

        # Execute with redirected stdout/stderr
        result = runnable._exec(IOConfig(stdout=stdout_w, stderr=stderr_w))

        os.close(stdout_w)
        os.close(stderr_w)
        os._exit(result.exit_code)
    else:
        # Parent process
        os.close(stdout_w)
        os.close(stderr_w)

        return CapturedResult(_wait_child(pid), stdout_r, stderr_r)


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
            exit_code = 1

            try:
                if mode == 'r':
                    # <(cmd): child writes to pipe, parent reads
                    os.close(pipe_r)
                    # Use _exec() directly - avoids double-fork for Commands
                    # Command._exec() does execve() which replaces this process
                    # Other runnables handle their own execution model
                    result = runnable._exec(IOConfig(stdout=pipe_w))
                    exit_code = result.exit_code
                else:
                    # >(cmd): child reads from pipe, parent writes
                    os.close(pipe_w)
                    result = runnable._exec(IOConfig(stdin=pipe_r))
                    exit_code = result.exit_code
            except SystemExit as e:
                exit_code = e.code if isinstance(e.code, int) else 1
            except Exception:
                exit_code = 1

            # Only reached if _exec() returned (not for Command which does execve)
            os._exit(exit_code)
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
        try:
            os.close(self._fd)
        except OSError:
            pass

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
        result = capture(pyshexec("script.py", "arg1"))
    """
    # Avoid circular: model.py ← builtins.py
    from .builtins import source  # noqa: PLC0415

    return Subshell(source(str(file), *[str(a) for a in args]))
