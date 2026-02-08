"""Tests for IOConfig extra_fds and redirection of extra file descriptors.

Verifies that arbitrary file descriptors can be redirected via IOConfig.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from shell.environment import env
from shell.model import (
    InProcessCallable,
    IOConfig,
    run,
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
        result = run(cmd, silent=True)

        # Since fd 3 was redirected to stdout, the output should appear there
        assert 'goes to stdout' in result.read_stdout()
