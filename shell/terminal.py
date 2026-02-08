"""PTY-based output capture for shell commands.

Provides dual-PTY allocation and a select()-based proxy loop so that:
- Child processes see isatty() == True on all three standard fds
- stdout and stderr are captured to separate files
- Interactive programs (vim, less) work with raw-mode stdin forwarding
- The `silent` flag suppresses terminal echo while still capturing output

Public API:
    create_context(silent) -> PtyContext  (use as context manager)
    PtyContext.setup_child()
    PtyContext.close_slaves()
    PtyContext.proxy_and_wait(pid) -> int
    PtyContext.cleanup()  (called automatically by __exit__)
"""

from __future__ import annotations

import errno
import fcntl
import io
import os
import select
import signal
import sys
import termios
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .environment import env
from .util import exit_code_from_status, try_close


@dataclass
class _DualPty:
    """Holds master/slave fd pairs for the two PTYs."""

    pty1_master: int  # stdout PTY master
    pty1_slave: int  # stdout PTY slave (also used for stdin)
    pty2_master: int  # stderr PTY master
    pty2_slave: int  # stderr PTY slave


@dataclass
class OutputFiles:
    """Holds output file handles and paths."""

    stdout_path: Path
    stderr_path: Path
    stdout_file: int  # fd for stdout output file
    stderr_file: int  # fd for stderr output file


def _allocate_dual_pty() -> _DualPty:
    """Allocate two PTY pairs for stdout and stderr capture.

    PTY₁: stdin + stdout (same device → real terminal behavior)
    PTY₂: stderr only (prevents interleaving)
    """
    m1, s1 = os.openpty()
    try:
        m2, s2 = os.openpty()
    except OSError:
        os.close(m1)
        os.close(s1)
        raise
    return _DualPty(m1, s1, m2, s2)


def _set_pty_size(master_fd: int) -> None:
    """Copy the real terminal's window size to a PTY master.

    Uses TIOCGWINSZ/TIOCSWINSZ ioctls. Silently ignores failures
    (e.g., when stdout is not a terminal).
    """
    try:
        size = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b'\x00' * 8)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, size)
    except OSError:
        pass


def _disable_opost(slave_fd: int) -> None:
    """Disable output post-processing on a PTY slave.

    OPOST converts \\n to \\r\\n. We disable it so the proxy forwards raw
    bytes and the real terminal handles line endings. Output files get
    clean \\n text.
    """
    attrs = termios.tcgetattr(slave_fd)
    attrs[1] &= ~termios.OPOST  # oflag
    termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)


@contextmanager
def _raw_input_mode(silent: bool) -> Generator[int | None]:
    """Context manager for raw terminal input during PTY proxy.

    Sets the real terminal to raw input mode (no echo, no line buffering,
    keystrokes pass through) while preserving output processing (OPOST stays
    enabled so \\n → \\r\\n on display).

    Yields the stdin fd for use in select(), or None if raw mode isn't
    needed (silent mode) or unavailable (no terminal).
    """
    if silent:
        yield None
        return

    try:
        fd = sys.stdin.fileno()
        old_termios = termios.tcgetattr(fd)
    except (termios.error, io.UnsupportedOperation, OSError):
        yield None
        return

    attrs = termios.tcgetattr(fd)
    # Input: disable processing for raw keystroke forwarding
    attrs[0] &= ~(termios.BRKINT | termios.ICRNL | termios.INPCK | termios.ISTRIP | termios.IXON)
    # Output: leave OPOST enabled (\n → \r\n on real terminal)
    # Control: 8-bit clean
    attrs[2] &= ~(termios.CSIZE | termios.PARENB)
    attrs[2] |= termios.CS8
    # Local: no echo, no canonical, no extended input, no signal keys
    attrs[3] &= ~(termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG)
    # Read 1 byte at a time, no timeout
    attrs[6][termios.VMIN] = 1
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSAFLUSH, attrs)

    try:
        yield fd
    finally:
        termios.tcsetattr(fd, termios.TCSAFLUSH, old_termios)


@contextmanager
def _sigchld_pipe() -> Generator[int]:
    """Context manager for SIGCHLD self-pipe trick.

    Installs a SIGCHLD handler that writes a byte to a pipe, allowing
    select() to wake up when a child exits. Yields the read end of the
    pipe for use in select(). Restores the previous handler on exit.
    """
    sigchld_r, sigchld_w = os.pipe()
    os.set_blocking(sigchld_r, False)
    os.set_blocking(sigchld_w, False)

    old_handler = signal.getsignal(signal.SIGCHLD)

    def _handler(signum: int, frame: object) -> None:
        try:
            os.write(sigchld_w, b'\x00')
        except OSError:
            pass

    signal.signal(signal.SIGCHLD, _handler)
    try:
        yield sigchld_r
    finally:
        signal.signal(signal.SIGCHLD, old_handler)
        os.close(sigchld_r)
        os.close(sigchld_w)


def _create_output_files(config_dir: Path) -> OutputFiles:
    """Create output files for stdout and stderr capture.

    Files are created in config_dir/job_output/ with timestamp and PID
    in the filename.
    """
    output_dir = config_dir / 'job_output'
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = int(time.time() * 1000)
    pid = os.getpid()

    stdout_path = output_dir / f'{timestamp}_{pid}_stdout.log'
    stderr_path = output_dir / f'{timestamp}_{pid}_stderr.log'

    stdout_fd = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        stderr_fd = os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    except OSError:
        os.close(stdout_fd)
        raise

    return OutputFiles(stdout_path, stderr_path, stdout_fd, stderr_fd)


def create_output_files() -> OutputFiles:
    """Create output files for capturing stdout/stderr.

    Public wrapper for use by run() when capturing InProcessCallable output
    without a full PTY context.
    """
    return _create_output_files(env.config_dir)


def _read_pty(fd: int) -> bytes:
    """Read from PTY master. Returns b'' on EIO (slave closed).

    EIO is the PTY equivalent of EOF on pipes — it means the slave side
    has been closed and there's no more data.
    """
    try:
        return os.read(fd, 4096)
    except OSError as e:
        if e.errno == errno.EIO:
            return b''
        raise


def _setup_child_fds(pty: _DualPty) -> None:
    """Set up file descriptors in the child process after fork.

    - Creates a new session (setsid) so the child gets its own
      controlling terminal
    - Sets PTY₁ as the controlling terminal via TIOCSCTTY
    - Dups PTY slaves onto fds 0, 1, 2
    - Rewraps sys.stdin/stdout/stderr around the new fds
    - Closes the original slave fds
    """
    os.setsid()

    # Set PTY₁ as controlling terminal
    try:
        fcntl.ioctl(pty.pty1_slave, termios.TIOCSCTTY, 0)
    except OSError:
        pass  # Non-fatal: some systems don't support TIOCSCTTY

    # stdin and stdout → PTY₁ slave (same device = real terminal)
    os.dup2(pty.pty1_slave, 0)
    os.dup2(pty.pty1_slave, 1)
    # stderr → PTY₂ slave (separate capture)
    os.dup2(pty.pty2_slave, 2)

    # Rewrap Python file objects around the new fds.
    # After fork, sys.stdout may point to a non-fd object (e.g., pytest's
    # StringIO capture). Rewrapping ensures print() goes to the PTY slave.
    sys.stdin = os.fdopen(0, 'r', closefd=False)
    sys.stdout = os.fdopen(1, 'w', closefd=False)
    sys.stderr = os.fdopen(2, 'w', closefd=False)

    # Close original slave fds (now duped onto 0/1/2)
    for fd in (pty.pty1_slave, pty.pty2_slave, pty.pty1_master, pty.pty2_master):
        if fd > 2:
            try_close(fd)


class PtyContext:
    """Encapsulates PTY state for run()'s PTY branch.

    Created by create_context(). Manages the lifecycle of PTY pairs,
    output files, and the proxy loop.
    """

    def __init__(
        self,
        pty: _DualPty,
        files: OutputFiles,
        silent: bool,
    ):
        self._pty = pty
        self._files = files
        self.silent = silent

    def __enter__(self) -> PtyContext:
        return self

    def __exit__(self, *args: object) -> None:
        self.cleanup()

    @property
    def stdout_path(self) -> Path:
        return self._files.stdout_path

    @property
    def stderr_path(self) -> Path:
        return self._files.stderr_path

    def setup_child(self) -> None:
        """Set up fds in the forked child process.

        Must be called immediately after fork, in the child only.
        """
        _setup_child_fds(self._pty)

    def close_slaves(self) -> None:
        """Close slave fds in the parent process after fork.

        The child has its own copies of these fds via fork.
        """
        for fd in (self._pty.pty1_slave, self._pty.pty2_slave):
            try_close(fd)

    def proxy_and_wait(self, pid: int) -> int:
        """Run the select()-based proxy loop until the child exits.

        Forwards PTY master output to the terminal (unless silent) and
        to output files. Forwards terminal stdin to the child's PTY
        (unless silent).

        Returns the child's exit code.
        """
        pty1_master = self._pty.pty1_master
        pty2_master = self._pty.pty2_master
        stdout_fd = self._files.stdout_file
        stderr_fd = self._files.stderr_file

        with _sigchld_pipe() as sigchld_r:
            # Race fix: child may have exited before handler was installed
            try:
                wpid, wstatus = os.waitpid(pid, os.WNOHANG)
                if wpid == pid:
                    self._drain_masters()
                    return exit_code_from_status(wstatus)
            except ChildProcessError:
                self._drain_masters()
                return 1

            child_exited = False
            exit_code = 0

            with _raw_input_mode(self.silent) as real_stdin_fd:
                while True:
                    read_fds = [pty1_master, pty2_master, sigchld_r]
                    if real_stdin_fd is not None:
                        read_fds.append(real_stdin_fd)

                    try:
                        readable, _, _ = select.select(read_fds, [], [])
                    except OSError as e:
                        if e.errno == errno.EINTR:
                            continue
                        raise

                    for fd in readable:
                        if fd == pty1_master:
                            data = _read_pty(fd)
                            if data:
                                os.write(stdout_fd, data)
                                if not self.silent:
                                    os.write(sys.stdout.fileno(), data)

                        elif fd == pty2_master:
                            data = _read_pty(fd)
                            if data:
                                os.write(stderr_fd, data)
                                if not self.silent:
                                    os.write(sys.stderr.fileno(), data)

                        elif fd == sigchld_r:
                            # Drain self-pipe
                            try:
                                os.read(sigchld_r, 256)
                            except OSError:
                                pass
                            try:
                                wpid, wstatus = os.waitpid(pid, os.WNOHANG)
                                if wpid == pid:
                                    exit_code = exit_code_from_status(wstatus)
                                    child_exited = True
                            except ChildProcessError:
                                child_exited = True

                        elif fd == real_stdin_fd:
                            data = _read_pty(fd)
                            if data:
                                os.write(pty1_master, data)

                    if child_exited:
                        self._drain_masters()
                        break

        return exit_code

    def _drain_masters(self) -> None:
        """Read remaining buffered data from PTY masters after child exits.

        Ensures no data is lost between the last select() wake and child exit.
        """
        pairs = [
            (self._pty.pty1_master, self._files.stdout_file, sys.stdout),
            (self._pty.pty2_master, self._files.stderr_file, sys.stderr),
        ]
        for master_fd, out_fd, stream in pairs:
            while True:
                data = _read_pty(master_fd)
                if not data:
                    break
                os.write(out_fd, data)
                if not self.silent:
                    os.write(stream.fileno(), data)

    def cleanup(self) -> None:
        """Close master fds and output files. Call after proxy_and_wait()."""
        for fd in (
            self._pty.pty1_master,
            self._pty.pty2_master,
            self._files.stdout_file,
            self._files.stderr_file,
        ):
            try_close(fd)


def create_context(silent: bool) -> PtyContext:
    """Allocate dual PTYs, configure terminals, return context for run().

    This is the entry point for PTY-based execution. It:
    1. Allocates two PTY pairs (stdout + stderr)
    2. Configures PTY terminal settings (window size, OPOST)
    3. Creates output files for capture

    The caller's IOConfig is NOT merged here — setup_child() dup2s PTY slaves
    onto fds 0/1/2, so the child's _exec() receives the original IOConfig
    (which references fds 0/1/2 as None, meaning "use inherited fds").
    """
    pty = _allocate_dual_pty()

    # Copy terminal size to PTY masters
    _set_pty_size(pty.pty1_master)
    _set_pty_size(pty.pty2_master)

    # Disable OPOST on slaves (proxy handles line endings)
    _disable_opost(pty.pty1_slave)
    _disable_opost(pty.pty2_slave)

    # Create output files
    files = _create_output_files(env.config_dir)

    return PtyContext(pty, files, silent)
