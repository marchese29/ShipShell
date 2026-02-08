from __future__ import annotations

import io
import os
import sys
import warnings
from abc import ABC, abstractmethod
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, NoReturn, Self, cast, override

from .. import terminal
from ..environment import env
from ..trap import TrapType
from ..util import exit_code_from_status, try_close
from ._types import FileLike, IOConfig, ShellResult

if TYPE_CHECKING:
    from ._compound import ConditionalChain, Negated, TracedRunnable
    from ._pipeline import Pipeline
    from ._process_sub import ProcessInput, ProcessOutput
    from ._program import Program


def _resolve_fd(target: FileLike | None, flags: int, default_fd: int | None = None) -> int | None:
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
    """Wait for child process and return its exit code (bash convention).

    Returns 0 on ChildProcessError (child was already reaped).
    """
    try:
        _, status = os.waitpid(pid, 0)
        return exit_code_from_status(status)
    except ChildProcessError:
        return 0


def _stdout_is_tty() -> bool:
    """Check if real stdout is a terminal, safely handling non-fd streams.

    Returns False when sys.stdout has been replaced with a non-fd object
    (e.g., pytest's capture fixtures use StringIO which has no fileno()).
    """
    try:
        return os.isatty(sys.stdout.fileno())
    except (io.UnsupportedOperation, OSError):
        return False


def _fork_exec(
    exec_fn: Callable[[], ShellResult],
    *,
    fire_exit_trap: bool = False,
) -> NoReturn:
    """Run exec_fn in a forked child process and os._exit() with its exit code.

    Handles the SystemExit/Exception -> exit code conversion that every fork
    site needs. Optionally fires the EXIT trap before exiting.

    This function never returns -- it always calls os._exit().
    """
    exit_code = 0
    try:
        result = exec_fn()
        exit_code = result.exit_code
    except SystemExit as e:
        exit_code = e.code if isinstance(e.code, int) else (1 if e.code else 0)
    except Exception:
        exit_code = 1
    finally:
        if fire_exit_trap:
            try:
                env.traps.fire(TrapType.EXIT)
            except Exception:
                pass
            env.traps.cleanup()

    os._exit(exit_code)


def run(
    runnable: ShellRunnable,
    io: IOConfig | None = None,
    *,
    silent: bool = False,
) -> ShellResult:
    """Execute a runnable, applying the given IO configuration.

    Args:
        runnable: The ShellRunnable to execute
        io: Optional IOConfig with stdin/stdout/stderr redirections.
            If raw fds are passed, they are the caller's responsibility to close.
            If paths are passed, they will be opened/closed by the runnable.
        silent: If True, suppress terminal output and capture to files.
            The result will have stdout_path/stderr_path for reading captured output.
    """
    from ._command import Command, InProcessCallable  # noqa: PLC0415

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

    # PTY branch: for non-builtins when output goes to real terminal,
    # or when silent=True (capture needed -- PTY works without a real terminal)
    use_pty = (
        not isinstance(runnable, InProcessCallable)
        and not runnable.is_output_redirected(io)
        and (silent or _stdout_is_tty())
        and not env.in_pty
    )

    if use_pty:
        with terminal.create_context(silent) as ctx:
            sys.stdout.flush()
            sys.stderr.flush()
            if (pid := os.fork()) == 0:
                env.in_pty = True
                ctx.setup_child()
                # After setup_child(), fds 0/1/2 are PTY slaves.
                # Pass the caller's original io -- setup_child() already placed
                # PTY slaves on 0/1/2, so _exec() just inherits them naturally.
                _fork_exec(lambda: runnable._exec(io))
            else:
                ctx.close_slaves()
                exit_code = ctx.proxy_and_wait(pid)
                result = ShellResult(exit_code, ctx.stdout_path, ctx.stderr_path)
    elif silent and isinstance(runnable, InProcessCallable):
        # InProcessCallable with silent=True: capture via file redirection.
        # Can't use PTY (builtins need to run in parent for side effects),
        # so redirect fds 1/2 to output files and let _redirected() handle it.
        files = terminal.create_output_files()
        capture_io = IOConfig(stdout=files.stdout_file, stderr=files.stderr_file)
        if io is not None:
            capture_io.extra_fds = dict(io.extra_fds)
        result = runnable._exec(capture_io)
        os.close(files.stdout_file)
        os.close(files.stderr_file)
        result = ShellResult(result.exit_code, files.stdout_path, files.stderr_path)
    elif isinstance(command := runnable, Command):
        # Non-PTY Command path (redirected output or non-terminal)
        if (pid := os.fork()) == 0:
            command._exec(io)
            os._exit(127)  # Should never reach here
        else:
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
            'Use cmd() to execute, or cmd(silent=True) to run and capture output.',
            UserWarning,
            stacklevel=2,
        )
        return True  # Runnables are truthy (they exist), just not executed

    @abstractmethod
    def _exec(self, io: IOConfig | None = None) -> ShellResult:
        """Execute with optional IO configuration."""
        ...

    @contextmanager
    def _redirected(self, io: IOConfig | None = None) -> Generator[None, None, None]:
        """Context manager for FD redirection, merging instance config with passed config.

        Handles:
        - Merging self._io with passed io (self._io takes precedence)
        - Saving original FDs and Python file objects
        - Resolving FileLike (path/int) to actual FDs
        - Applying dup2 redirections + reassigning sys.stdin/stdout/stderr
        - Handling extra_fds for arbitrary fd redirections
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
        saved_extra_fds: dict[int, int] = {}  # fd num -> saved dup

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

            # Resolve extra fds (for redirecting arbitrary fds like 3, 4, etc.)
            extra_fd_flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            resolved_extra_fds: dict[int, int] = {}  # target fd -> source fd
            extra_is_path: dict[int, bool] = {}
            for target_fd, source in actual.extra_fds.items():
                is_path = not isinstance(source, int)
                extra_is_path[target_fd] = is_path
                resolved = _resolve_fd(source, extra_fd_flags, None)
                if resolved is not None:
                    resolved_extra_fds[target_fd] = resolved
                    if is_path:
                        fds_to_cleanup.append(resolved)

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

            # Save extra fds if they exist
            for target_fd in resolved_extra_fds:
                try:
                    saved_extra_fds[target_fd] = os.dup(target_fd)
                    fds_to_cleanup.append(saved_extra_fds[target_fd])
                except OSError:
                    # FD doesn't exist yet, nothing to save
                    pass

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

            # Apply extra fd redirections
            for target_fd, source_fd in resolved_extra_fds.items():
                os.dup2(source_fd, target_fd)
                if extra_is_path[target_fd] and source_fd > 2 and source_fd not in {0, 1, 2}:
                    os.close(source_fd)
                    if source_fd in fds_to_cleanup:
                        fds_to_cleanup.remove(source_fd)

            # Transfer saved fds out of cleanup list (they're managed by finally now)
            for fd in [saved_stdin_fd, saved_stdout_fd, saved_stderr_fd]:
                if fd is not None and fd in fds_to_cleanup:
                    fds_to_cleanup.remove(fd)
            for saved_fd in saved_extra_fds.values():
                if saved_fd in fds_to_cleanup:
                    fds_to_cleanup.remove(saved_fd)

            yield  # Run the caller's code with redirected FDs

        except BaseException:
            # Cleanup all FDs we allocated before re-raising
            for fd in fds_to_cleanup:
                try_close(fd)
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
            # Restore extra fds
            for target_fd, saved_fd in saved_extra_fds.items():
                os.dup2(saved_fd, target_fd)
                os.close(saved_fd)

    def __call__(self, *, silent: bool = False) -> ShellResult:
        return run(self, silent=silent)

    def is_output_redirected(self, io: IOConfig | None = None) -> bool:
        """Whether stdout is redirected away from the terminal.

        Only checks stdout -- stderr redirects (2>&1, 2>/dev/null) don't
        affect whether we need a PTY for the primary output stream.
        """
        actual = self._io.merge_over(io)
        return actual.stdout is not None

    def __or__(self, value: ShellRunnable | Program | Callable[[], Any]) -> Pipeline:
        """
        Allows for building pipeline like `cmd("arg") | cmd2("arg2")`

        Also supports plain callables: `cmd("arg") | my_function`
        And uncalled Programs: `cmd("arg") | prog('cat')` auto-calls with no args
        """
        from ._command import InProcessCallable  # noqa: PLC0415
        from ._pipeline import Pipeline  # noqa: PLC0415
        from ._program import Program  # noqa: PLC0415

        # Auto-call uncalled Programs (e.g., prog('cat') without ())
        if isinstance(value, Program):
            value = value()
        # Auto-wrap plain callables
        elif callable(value) and not isinstance(value, ShellRunnable):
            value = InProcessCallable(value)

        if isinstance(self, Pipeline):
            raise RuntimeError('Pipeline should override | operator')

        self = cast(ShellRunnable, self)
        if isinstance(pipeline := value, Pipeline):
            return Pipeline([self, *pipeline.stages])
        value = cast(ShellRunnable, value)
        return Pipeline([self, value])

    def __ror__(self, value: Program | Callable[[], Any]) -> Pipeline:
        """
        Handle callable/Program | ShellRunnable (when on the left).

        Example: my_function | grep("pattern")
        """
        from ._command import InProcessCallable  # noqa: PLC0415
        from ._program import Program  # noqa: PLC0415

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
        from ._command import InProcessCallable  # noqa: PLC0415

        # String content
        if isinstance(content, str):
            text = content

            def write_str() -> None:
                print(text, end='')

            return InProcessCallable(write_str) | self

        # Binary content
        if isinstance(content, (bytes, bytearray, memoryview)):
            data = bytes(content)

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
        from ._compound import Negated  # noqa: PLC0415

        return Negated(self)

    def if_success(self, other: ShellRunnable | Program | Callable[[], Any]) -> ConditionalChain:
        """Execute other only if this command succeeds (exit code 0).

        Equivalent to bash's && operator. Accepts ShellRunnable, Program, or callable.

        Example:
            prog('make')().if_success(prog('make')('install'))()
            prog('test')().if_success(lambda: print('passed'))()
            prog('true')().if_success(prog('echo'))  # auto-calls with no args
        """
        from ._command import InProcessCallable  # noqa: PLC0415
        from ._compound import ConditionalChain  # noqa: PLC0415
        from ._program import Program  # noqa: PLC0415

        if isinstance(other, Program):
            other = other()
        elif callable(other) and not isinstance(other, ShellRunnable):
            other = InProcessCallable(other)
        return ConditionalChain(self, other, on_success=True)

    def if_fail(self, other: ShellRunnable | Program | Callable[[], Any]) -> ConditionalChain:
        """Execute other only if this command fails (non-zero exit code).

        Equivalent to bash's || operator. Accepts ShellRunnable, Program, or callable.

        Example:
            prog('test')().if_fail(echo('test failed'))()
            prog('cmd')().if_fail(lambda: print('error'))()
            prog('false')().if_fail(prog('echo'))  # auto-calls with no args
        """
        from ._command import InProcessCallable  # noqa: PLC0415
        from ._compound import ConditionalChain  # noqa: PLC0415
        from ._program import Program  # noqa: PLC0415

        if isinstance(other, Program):
            other = other()
        elif callable(other) and not isinstance(other, ShellRunnable):
            other = InProcessCallable(other)
        return ConditionalChain(self, other, on_success=False)

    def __add__(self, other: ShellRunnable | Program | Callable[[], Any]) -> ConditionalChain:
        """Execute other only if this command succeeds. Alias for if_success().

        Example:
            (prog('make')() + prog('make')('install'))()
        """
        return self.if_success(other)

    def __radd__(self, other: Program | Callable[[], Any]) -> ConditionalChain:
        """Handle callable/Program + ShellRunnable (left side)."""
        from ._command import InProcessCallable  # noqa: PLC0415
        from ._program import Program  # noqa: PLC0415

        if isinstance(other, Program):
            return other() + self
        if callable(other) and not isinstance(other, ShellRunnable):
            left = InProcessCallable(other)
            return left + self
        return NotImplemented

    def __sub__(self, other: ShellRunnable | Program | Callable[[], Any]) -> ConditionalChain:
        """Execute other only if this command fails. Alias for if_fail().

        Example:
            (prog('test')() - prog('echo')('failed'))()
        """
        return self.if_fail(other)

    def __rsub__(self, other: Program | Callable[[], Any]) -> ConditionalChain:
        """Handle callable/Program - ShellRunnable (left side)."""
        from ._command import InProcessCallable  # noqa: PLC0415
        from ._program import Program  # noqa: PLC0415

        if isinstance(other, Program):
            return other() - self
        if callable(other) and not isinstance(other, ShellRunnable):
            left = InProcessCallable(other)
            return left - self
        return NotImplemented

    def trace(self, display: str, prefix: str = '+ ') -> TracedRunnable:
        """Wrap with trace output (prints to stderr before execution)."""
        from ._compound import TracedRunnable  # noqa: PLC0415

        return TracedRunnable(self, display, prefix)

    def as_input(self) -> ProcessInput:
        """Use this command's stdout as an input file <(cmd).

        Returns a context manager. Access .path or .fd inside the with block.
        The child process starts immediately when entering the context.

        Example:
            with prog("ls")().as_input() as inp:
                run(prog("cat")(inp.path))
        """
        from ._process_sub import ProcessInput  # noqa: PLC0415

        return ProcessInput(self)

    def as_output(self) -> ProcessOutput:
        """Use this command's stdin as an output file >(cmd).

        Returns a context manager. Access .path or .fd inside the with block.
        The child process starts immediately when entering the context.

        Example:
            with prog("grep")("error").as_output() as out:
                run(prog("echo")("test") > out.path)
        """
        from ._process_sub import ProcessOutput  # noqa: PLC0415

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
