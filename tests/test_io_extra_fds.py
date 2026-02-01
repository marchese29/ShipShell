"""Tests for IOConfig extra_fds and capture() with extra fd support.

Verifies that arbitrary file descriptors can be redirected via IOConfig
and captured via capture().
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

from shell.environment import env
from shell.model import (
    Command,
    IOConfig,
    InProcessCallable,
    ShellResult,
    capture,
    prog,
)


@pytest.fixture(autouse=True)
def init_shell_env():
    """Initialize shell environment for each test."""
    env.initialize()


class TestIOConfigExtraFds:
    """Tests for IOConfig.extra_fds and with_fd()."""

    def test_extra_fds_default_empty(self):
        """IOConfig has empty extra_fds by default."""
        io = IOConfig()
        assert io.extra_fds == {}

    def test_extra_fds_constructor(self):
        """IOConfig can be initialized with extra_fds dict."""
        io = IOConfig(extra_fds={3: '/tmp/fd3.txt', 4: 1})
        assert io.extra_fds == {3: '/tmp/fd3.txt', 4: 1}

    def test_with_fd_method(self):
        """with_fd() adds fd to extra_fds and returns self."""
        io = IOConfig()
        result = io.with_fd(3, '/tmp/out.txt')

        assert result is io  # Returns self for chaining
        assert io.extra_fds == {3: '/tmp/out.txt'}

    def test_with_fd_chaining(self):
        """with_fd() can be chained multiple times."""
        io = IOConfig().with_fd(3, '/tmp/a.txt').with_fd(4, '/tmp/b.txt').with_fd(5, 1)

        assert io.extra_fds == {3: '/tmp/a.txt', 4: '/tmp/b.txt', 5: 1}

    def test_with_fd_combined_with_other_methods(self):
        """with_fd() works with other builder methods."""
        io = IOConfig().with_stdin(0).with_stdout('/tmp/out.txt').with_fd(3, '/tmp/log.txt')

        assert io.stdin == 0
        assert io.stdout == '/tmp/out.txt'
        assert io.extra_fds == {3: '/tmp/log.txt'}


class TestIOConfigMergeExtraFds:
    """Tests for IOConfig.merge_over() with extra_fds."""

    def test_merge_over_none_preserves_extra_fds(self):
        """Merging over None preserves extra_fds."""
        io = IOConfig(extra_fds={3: '/tmp/a.txt'})
        merged = io.merge_over(None)

        assert merged.extra_fds == {3: '/tmp/a.txt'}
        # Should be a copy, not same dict
        assert merged.extra_fds is not io.extra_fds

    def test_merge_over_combines_extra_fds(self):
        """Merging combines extra_fds from both, self wins on conflict."""
        base = IOConfig(extra_fds={3: '/tmp/base3.txt', 4: '/tmp/base4.txt'})
        override = IOConfig(extra_fds={3: '/tmp/override3.txt', 5: '/tmp/override5.txt'})

        merged = override.merge_over(base)

        # override wins for fd 3, base provides fd 4, override provides fd 5
        assert merged.extra_fds == {
            3: '/tmp/override3.txt',  # override wins
            4: '/tmp/base4.txt',  # from base
            5: '/tmp/override5.txt',  # from override
        }

    def test_merge_over_empty_extra_fds(self):
        """Merging with empty extra_fds works correctly."""
        base = IOConfig(extra_fds={3: '/tmp/a.txt'})
        override = IOConfig()  # empty extra_fds

        merged = override.merge_over(base)
        assert merged.extra_fds == {3: '/tmp/a.txt'}


class TestCapturedResultExtraFds:
    """Tests for CapturedResult.read_fd()."""

    def test_capture_with_extra_fd(self):
        """capture() can capture output from arbitrary file descriptors."""

        # Create a command that writes to fd 3
        def write_to_fd3():
            os.write(3, b'hello from fd 3')
            print('hello from stdout')

        result = capture(InProcessCallable(write_to_fd3), 3)

        assert result.read_stdout() == 'hello from stdout'
        assert result.read_fd(3) == 'hello from fd 3'

    def test_capture_multiple_extra_fds(self):
        """capture() can capture from multiple extra file descriptors."""

        def write_to_multiple_fds():
            os.write(3, b'fd3 data')
            os.write(4, b'fd4 data')
            print('stdout data')

        result = capture(InProcessCallable(write_to_multiple_fds), 3, 4)

        assert result.read_stdout() == 'stdout data'
        assert result.read_fd(3) == 'fd3 data'
        assert result.read_fd(4) == 'fd4 data'

    def test_read_fd_nonexistent_returns_empty(self):
        """read_fd() returns empty string for fds not captured."""

        def noop():
            print('hello')

        result = capture(InProcessCallable(noop))  # No extra fds captured
        assert result.read_fd(3) == ''
        assert result.read_fd(99) == ''

    def test_read_fd_caches_result(self):
        """read_fd() caches the result after first read."""

        def write_to_fd3():
            os.write(3, b'cached data')

        result = capture(InProcessCallable(write_to_fd3), 3)

        first_read = result.read_fd(3)
        second_read = result.read_fd(3)

        assert first_read == 'cached data'
        assert second_read == 'cached data'
        assert first_read is second_read  # Same cached string object


class TestCaptureWithRealCommands:
    """Integration tests for capture() with real shell commands."""

    def test_capture_bash_writing_to_fd3(self):
        """Capture output from bash writing to fd 3."""
        # bash -c writes to fd 3 which we redirect to stdout equivalent
        cmd = prog('bash')('-c', 'echo "fd3 output" >&3; echo "stdout output"')
        result = capture(cmd, 3)

        assert result.read_stdout() == 'stdout output'
        assert result.read_fd(3) == 'fd3 output'

    def test_capture_preserves_exit_code(self):
        """Exit code is preserved when capturing extra fds."""

        def exit_with_code():
            os.write(3, b'before exit')
            return 42

        result = capture(InProcessCallable(exit_with_code), 3)

        assert result.exit_code == 42
        assert result.read_fd(3) == 'before exit'

    def test_capture_without_extra_fds_unchanged(self):
        """capture() without extra fds works as before."""
        result = capture(prog('echo')('hello'))

        assert result.read_stdout() == 'hello'
        assert result.exit_code == 0


class TestIOConfigRedirectedExtraFds:
    """Tests for _redirected() context manager with extra_fds."""

    def test_redirected_extra_fd_to_file(self):
        """Extra fd can be redirected to a file."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            temp_path = f.name

        try:

            def write_to_fd3():
                os.write(3, b'redirected to file')
                return 0

            cmd = InProcessCallable(write_to_fd3)
            cmd._io = IOConfig().with_fd(3, temp_path)
            result = cmd()

            assert result.exit_code == 0
            with open(temp_path) as f:
                assert f.read() == 'redirected to file'
        finally:
            os.unlink(temp_path)

    def test_redirected_extra_fd_to_existing_fd(self):
        """Extra fd can be redirected to another fd (e.g., stdout)."""

        def write_to_fd3():
            os.write(3, b'goes to stdout')
            return 0

        cmd = InProcessCallable(write_to_fd3)
        cmd._io = IOConfig().with_fd(3, 1)  # fd 3 -> stdout
        result = capture(cmd)

        # Since fd 3 was redirected to stdout, the output should appear there
        assert 'goes to stdout' in result.read_stdout()
