from __future__ import annotations

import os
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any, Self, cast, override

from .environment import env, env_to_str

FileLike = int | str | Path


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


# Registry of builtin commands - populated by @builtin_command decorator in builtins.py
BUILTIN_REGISTRY: dict[str, Callable[..., Builtin]] = {}


def resolve_builtin(name: str) -> Callable[..., Builtin] | None:
    """
    Resolve a builtin command by name.
    Returns the builtin factory function or None if not a builtin.
    Call the returned factory with arguments to get a Builtin instance.
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


def run(
    runnable: ShellRunnable,
    stdin: FileLike | None = None,
    stdout: FileLike | None = None,
    stderr: FileLike | None = None,
) -> ShellResult:
    """
    Execute a runnable, applying the given stream overrides.

    Accepts FileLike (int fd, str path, or Path) for redirections.
    If raw fds are passed, they are the caller's responsibility to close.
    If paths are passed, they will be opened/closed by the runnable.
    """
    if isinstance(command := runnable, Command):
        # Commands need to fork before _exec
        if (pid := os.fork()) == 0:
            # Child - _exec will resolve FileLike and replace this process
            command._exec(stdin=stdin, stdout=stdout, stderr=stderr)
            os._exit(127)  # Should never reach here
        else:
            # Parent - wait for child
            _, status = os.waitpid(pid, 0)
            if os.WIFEXITED(status):
                result = ShellResult(os.WEXITSTATUS(status))
            elif os.WIFSIGNALED(status):
                result = ShellResult(128 + os.WTERMSIG(status))
            else:
                raise RuntimeError('Unexpected process status')
    else:
        # Everything else handles its own execution model
        result = runnable._exec(stdin=stdin, stdout=stdout, stderr=stderr)

    env.last_exit = result.exit_code
    return result


class ShellRunnable(ABC):
    def __init__(self):
        self._env_overlay: dict[str, Any] = {}

        # Redirections - None means use whatever is passed to _exec
        self._stdin: FileLike | None = None
        self._stdout: FileLike | None = None
        self._append_out: bool = False
        self._stderr: FileLike | None = None
        self._append_err: bool = False

    @abstractmethod
    def _exec(
        self,
        stdin: FileLike | None = None,
        stdout: FileLike | None = None,
        stderr: FileLike | None = None,
    ) -> ShellResult: ...

    def __call__(self) -> ShellResult:
        return run(self)

    def __or__(self, value: ShellRunnable) -> Pipeline:
        """
        Allows for building pipeline like `cmd("arg") | cmd2("arg2")`
        """
        if isinstance(self, Pipeline):
            raise RuntimeError('Pipeline should override | operator')

        self = cast(NotPipeline, self)
        if isinstance(pipeline := value, Pipeline):
            return Pipeline([self, *pipeline.predecessors], pipeline.final_cmd)
        value = cast(NotPipeline, value)
        return Pipeline([self], value)

    def pipe(self, value: ShellRunnable) -> Pipeline:
        return self | value

    def __gt__(self, target: FileLike) -> Self:
        self._stdout = target
        self._append_out = False
        return self

    def __rshift__(self, target: FileLike) -> Self:
        self._stdout = target
        self._append_out = True
        return self

    def with_stdout(self, target: FileLike, append: bool = False) -> Self:
        if append:
            return self >> target
        else:
            return self > target

    def with_stderr(self, target: FileLike, append: bool = False) -> Self:
        self._stderr = target
        self._append_err = append
        return self

    def __lt__(self, source: FileLike) -> Self:
        self._stdin = source
        return self

    def with_stdin(self, source: FileLike) -> Self:
        return self < source

    def env(self, **env_overlay: Any) -> Self:
        self._env_overlay.update(env_overlay)
        return self

    def neg(self) -> Negated:
        return Negated(self)


class Command(ShellRunnable):
    def __init__(self, program: str, *args: Any):
        super().__init__()
        self._program = program
        self._args = args

    @override
    def _exec(
        self,
        stdin: FileLike | None = None,
        stdout: FileLike | None = None,
        stderr: FileLike | None = None,
    ) -> ShellResult:
        # Merge instance vars (from operators) with params (from composition)
        # Instance vars override params
        actual_stdin = self._stdin if self._stdin is not None else stdin
        actual_stdout = self._stdout if self._stdout is not None else stdout
        actual_stderr = self._stderr if self._stderr is not None else stderr

        # Resolve the command
        command = resolve_cmd(self._program)
        if command is None:
            print(f'command not found: {self._program}', file=sys.stderr)
            return ShellResult(127)
        if not os.access(command, os.X_OK):
            print(f'permission denied: {self._program}', file=sys.stderr)
            return ShellResult(126)

        # Resolve redirections to file descriptors
        stdin_fd = _resolve_fd(actual_stdin, os.O_RDONLY, None)
        stdout_flags = (
            os.O_WRONLY | os.O_CREAT | (os.O_APPEND if self._append_out else os.O_TRUNC)
        )
        stdout_fd = _resolve_fd(actual_stdout, stdout_flags, None)
        stderr_flags = (
            os.O_WRONLY | os.O_CREAT | (os.O_APPEND if self._append_err else os.O_TRUNC)
        )
        stderr_fd = _resolve_fd(actual_stderr, stderr_flags, None)

        # Apply redirections
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


class Builtin(ShellRunnable):
    def __init__(self, name: str, impl: Callable[..., int], kwargs: dict[str, Any]):
        super().__init__()
        self._name = name
        self._impl = impl  # Function: (**kwargs) -> int
        self._kwargs = kwargs

    @override
    def _exec(
        self,
        stdin: FileLike | None = None,
        stdout: FileLike | None = None,
        stderr: FileLike | None = None,
    ) -> ShellResult:
        # Merge instance vars with params - instance vars override
        actual_stdin = self._stdin if self._stdin is not None else stdin
        actual_stdout = self._stdout if self._stdout is not None else stdout
        actual_stderr = self._stderr if self._stderr is not None else stderr

        # Track whether we opened the fd (from a path) vs received it as an int
        # We should only close fds we opened ourselves
        stdin_opened = not isinstance(actual_stdin, int) and actual_stdin is not None
        stdout_opened = not isinstance(actual_stdout, int) and actual_stdout is not None
        stderr_opened = not isinstance(actual_stderr, int) and actual_stderr is not None

        # Resolve redirections to file descriptors
        stdin_fd = _resolve_fd(actual_stdin, os.O_RDONLY, None)
        stdout_flags = (
            os.O_WRONLY | os.O_CREAT | (os.O_APPEND if self._append_out else os.O_TRUNC)
        )
        stdout_fd = _resolve_fd(actual_stdout, stdout_flags, None)
        stderr_flags = (
            os.O_WRONLY | os.O_CREAT | (os.O_APPEND if self._append_err else os.O_TRUNC)
        )
        stderr_fd = _resolve_fd(actual_stderr, stderr_flags, None)

        # Save current fds before modifying
        saved_stdin = os.dup(0) if stdin_fd is not None else None
        saved_stdout = os.dup(1) if stdout_fd is not None else None
        saved_stderr = os.dup(2) if stderr_fd is not None else None

        try:
            # Flush Python's buffers before redirecting
            sys.stdout.flush()
            sys.stderr.flush()

            # Apply redirections - only close fds we opened (not ones passed in)
            if stdin_fd is not None and stdin_fd != 0:
                os.dup2(stdin_fd, 0)
                if stdin_opened and stdin_fd > 2:
                    os.close(stdin_fd)

            if stdout_fd is not None and stdout_fd != 1:
                os.dup2(stdout_fd, 1)
                if stdout_opened and stdout_fd > 2:
                    os.close(stdout_fd)

            if stderr_fd is not None and stderr_fd != 2:
                os.dup2(stderr_fd, 2)
                if stderr_opened and stderr_fd > 2:
                    os.close(stderr_fd)

            # Run builtin function with redirected fds
            exit_code = self._impl(**self._kwargs)

            # Flush after impl so output goes to redirected fds
            sys.stdout.flush()
            sys.stderr.flush()

            return ShellResult(exit_code)

        finally:
            # Restore original fds
            if saved_stdin is not None:
                os.dup2(saved_stdin, 0)
                os.close(saved_stdin)
            if saved_stdout is not None:
                os.dup2(saved_stdout, 1)
                os.close(saved_stdout)
            if saved_stderr is not None:
                os.dup2(saved_stderr, 2)
                os.close(saved_stderr)


class Pipeline(ShellRunnable):
    def __init__(
        self, predecessors: list[NotPipeline], final_cmd: NotPipeline, pipefail: bool = False
    ):
        super().__init__()
        self.predecessors = predecessors
        self.final_cmd = final_cmd
        self.pipefail = pipefail

    @override
    def _exec(
        self,
        stdin: FileLike | None = None,
        stdout: FileLike | None = None,
        stderr: FileLike | None = None,
    ) -> ShellResult:
        # Pipeline's own redirects override params
        actual_stdin = self._stdin if self._stdin is not None else stdin
        actual_stdout = self._stdout if self._stdout is not None else stdout
        actual_stderr = self._stderr if self._stderr is not None else stderr

        child_pids = []

        # Handle first predecessor - it gets FileLike stdin from pipeline
        first_stage = self.predecessors[0]
        pipe_r, pipe_w = os.pipe()
        os.set_inheritable(pipe_r, False)
        os.set_inheritable(pipe_w, False)

        if (pid := os.fork()) == 0:
            # Child process
            os.close(pipe_r)

            # First stage gets actual_stdin (FileLike)
            result = first_stage._exec(
                stdin=actual_stdin, stdout=pipe_w, stderr=actual_stderr
            )

            os.close(pipe_w)
            os._exit(result.exit_code)
        else:
            # Parent process
            child_pids.append(pid)
            os.close(pipe_w)

        # current_pipe_read starts as output from first stage
        current_pipe_read = pipe_r

        # Handle remaining predecessors - they get int pipe fds
        for stage in self.predecessors[1:]:
            pipe_r, pipe_w = os.pipe()
            os.set_inheritable(pipe_r, False)
            os.set_inheritable(pipe_w, False)

            if (pid := os.fork()) == 0:
                # Child process
                os.close(pipe_r)

                # Middle stages get int fd from previous pipe
                result = stage._exec(
                    stdin=current_pipe_read, stdout=pipe_w, stderr=actual_stderr
                )

                os.close(pipe_w)
                os.close(current_pipe_read)
                os._exit(result.exit_code)
            else:
                # Parent process
                child_pids.append(pid)
                os.close(pipe_w)
                os.close(current_pipe_read)

                current_pipe_read = pipe_r

        # Execute final command in current process
        # Use run() so Commands fork and Builtins run in current process
        result = run(
            self.final_cmd,
            stdin=current_pipe_read,
            stdout=actual_stdout,
            stderr=actual_stderr,
        )

        # Close the final input pipe
        os.close(current_pipe_read)

        # Wait for all children and collect exit codes
        exit_codes = []
        for pid in child_pids:
            _, status = os.waitpid(pid, 0)
            if os.WIFEXITED(status):
                exit_codes.append(os.WEXITSTATUS(status))
            elif os.WIFSIGNALED(status):
                exit_codes.append(128 + os.WTERMSIG(status))
            else:
                exit_codes.append(1)

        # If pipefail, use first non-zero exit code from pipeline stages
        if self.pipefail:
            for code in exit_codes:
                if code != 0:
                    result.exit_code = code
                    break

        return result

    @override
    def __or__(self, value: ShellRunnable) -> Pipeline:
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
    def __init__(self, runnable: ShellRunnable):
        super().__init__()
        self._runnable = runnable

    @override
    def _exec(
        self,
        stdin: FileLike | None = None,
        stdout: FileLike | None = None,
        stderr: FileLike | None = None,
    ) -> ShellResult:
        # Merge instance vars with params - instance vars override
        actual_stdin = self._stdin if self._stdin is not None else stdin
        actual_stdout = self._stdout if self._stdout is not None else stdout
        actual_stderr = self._stderr if self._stderr is not None else stderr

        # Flush before forking to avoid duplicated output
        sys.stdout.flush()
        sys.stderr.flush()

        if (pid := os.fork()) == 0:
            # Child process - isolated environment
            env.update(self._env_overlay)
            result = run(
                self._runnable,
                stdin=actual_stdin,
                stdout=actual_stdout,
                stderr=actual_stderr,
            )
            os._exit(result.exit_code)
        else:
            # Parent process
            _, status = os.waitpid(pid, 0)
            if os.WIFEXITED(status):
                return ShellResult(os.WEXITSTATUS(status))
            elif os.WIFSIGNALED(status):
                return ShellResult(128 + os.WTERMSIG(status))
            else:
                raise RuntimeError('Unexpected process status')


class Negated(ShellRunnable):
    def __init__(self, runnable: ShellRunnable):
        super().__init__()
        self._runnable = runnable

    @override
    def _exec(
        self,
        stdin: FileLike | None = None,
        stdout: FileLike | None = None,
        stderr: FileLike | None = None,
    ) -> ShellResult:
        # Merge instance vars with params - instance vars override
        actual_stdin = self._stdin if self._stdin is not None else stdin
        actual_stdout = self._stdout if self._stdout is not None else stdout
        actual_stderr = self._stderr if self._stderr is not None else stderr

        # Run and negate the result
        result = run(
            self._runnable,
            stdin=actual_stdin,
            stdout=actual_stdout,
            stderr=actual_stderr,
        )
        return ~result


NotPipeline = Command | Builtin | Subshell | Negated


class Program:
    def __init__(self, name: str):
        self._cmd = name

    def __call__(self, *args: Any, **env_overlay: Any) -> Command | Builtin:
        return self.args(*args, **env_overlay)

    def args(self, *args: Any, **env_overlay: Any) -> Command | Builtin:
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


def cmd(prog: str | Path, *args: Any, **env_overlay: Any) -> Command | Builtin:
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
        result = runnable._exec(stdout=stdout_w, stderr=stderr_w)

        os.close(stdout_w)
        os.close(stderr_w)
        os._exit(result.exit_code)
    else:
        # Parent process
        os.close(stdout_w)
        os.close(stderr_w)

        # Wait for child
        _, status = os.waitpid(pid, 0)
        if os.WIFEXITED(status):
            exit_code = os.WEXITSTATUS(status)
        elif os.WIFSIGNALED(status):
            exit_code = 128 + os.WTERMSIG(status)
        else:
            exit_code = 1

        return CapturedResult(exit_code, stdout_r, stderr_r)


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
    from .builtins import source

    return Subshell(source(str(file), *[str(a) for a in args]))
