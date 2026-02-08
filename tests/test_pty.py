"""Tests for PTY-based output capture.

Tests the dual-PTY allocation, proxy loop, silent mode, and integration
with pipelines and subshells.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

from shell.environment import env
from shell.model import (
    InProcessCallable,
    Subshell,
    prog,
    run,
)


@pytest.fixture(autouse=True)
def init_shell_env():
    """Initialize shell environment for each test."""
    env.initialize()




class TestPtyBasic:
    """Basic PTY functionality tests."""

    def test_simple_command_with_pty(self):
        """Commands run through PTY and produce output files."""
        result = run(prog('echo')('hello pty'), silent=True)
        assert result.exit_code == 0
        assert result.stdout_path is not None
        assert result.stderr_path is not None
        assert 'hello pty' in result.read_stdout()

    def test_exit_code_preserved(self):
        """Non-zero exit codes are preserved through PTY."""
        result = run(prog('false')(), silent=True)
        assert result.exit_code != 0

    def test_stderr_captured_separately(self):
        """stderr goes to separate file via PTY₂."""
        result = run(
            prog('bash')('-c', 'echo stdout_msg; echo stderr_msg >&2'),
            silent=True,
        )
        assert 'stdout_msg' in result.read_stdout()
        assert 'stderr_msg' in result.read_stderr()

    def test_multiline_output(self):
        """Multi-line output is captured correctly."""
        result = run(
            prog('bash')('-c', 'echo line1; echo line2; echo line3'),
            silent=True,
        )
        lines = result.read_stdout().splitlines()
        assert len(lines) == 3
        assert lines[0] == 'line1'
        assert lines[1] == 'line2'
        assert lines[2] == 'line3'

    def test_output_files_exist(self):
        """Output files are created in the config directory."""
        result = run(prog('echo')('file check'), silent=True)
        assert result.stdout_path is not None
        assert result.stdout_path.exists()
        assert result.stderr_path is not None
        assert result.stderr_path.exists()


class TestPtySilentMode:
    """Tests for silent=True behavior."""

    def test_silent_captures_output(self):
        """silent=True captures stdout without echoing to terminal."""
        result = run(prog('echo')('silent output'), silent=True)
        assert result.read_stdout() == 'silent output'

    def test_silent_result_has_paths(self):
        """Silent mode result includes file paths for stdout and stderr."""
        result = run(prog('echo')('paths'), silent=True)
        assert result.stdout_path is not None
        assert result.stderr_path is not None

    def test_silent_empty_stderr(self):
        """stderr file is empty when command produces no stderr."""
        result = run(prog('echo')('no errors'), silent=True)
        assert result.read_stderr() == ''


class TestPtySkipConditions:
    """Tests for when PTY should NOT be used."""

    def test_redirect_skips_pty(self):
        """Output redirection bypasses PTY (no stdout_path)."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            temp_path = f.name

        try:
            result = (prog('echo')('redirected') > temp_path)()
            # When redirected, PTY is skipped — no stdout_path
            assert result.stdout_path is None
            with open(temp_path) as f:
                assert 'redirected' in f.read()
        finally:
            os.unlink(temp_path)

    def test_inprocess_callable_skips_pty(self):
        """InProcessCallable runs in parent process, no PTY."""
        result = InProcessCallable(lambda: print('builtin') or 0)()
        assert result.stdout_path is None


class TestPtyPipeline:
    """Tests for PTY with pipelines."""

    def test_pipeline_through_pty(self):
        """Pipelines work through PTY layer."""
        result = run(prog('echo')('hello world') | prog('cat')(), silent=True)
        assert result.exit_code == 0
        assert 'hello world' in result.read_stdout()

    def test_pipeline_with_grep(self):
        """Pipeline filtering works through PTY."""
        result = run(
            prog('bash')('-c', 'echo apple; echo banana; echo cherry')
            | prog('grep')('an'),
            silent=True,
        )
        assert 'banana' in result.read_stdout()
        assert 'apple' not in result.read_stdout()

    def test_pipeline_exit_code(self):
        """Pipeline exit code comes from last stage."""
        result = run(prog('echo')('test') | prog('false')(), silent=True)
        assert result.exit_code != 0

    def test_pipeline_with_callable(self):
        """Python callable in pipeline works through PTY."""

        def upper():
            for line in sys.stdin:
                print(line.upper(), end='')

        result = run(prog('echo')('hello') | upper, silent=True)
        assert result.read_stdout() == 'HELLO'


class TestPtySubshell:
    """Tests for PTY with subshells."""

    def test_subshell_through_pty(self):
        """Subshell runs through PTY layer."""
        result = run(Subshell(prog('echo')('sub hello')), silent=True)
        assert 'sub hello' in result.read_stdout()


class TestShellResultReadMethods:
    """Tests for ShellResult.read_stdout() and read_stderr()."""

    def test_read_stdout_strips_trailing_whitespace(self):
        """read_stdout strips trailing whitespace like old CapturedResult."""
        result = run(prog('echo')('stripped'), silent=True)
        stdout = result.read_stdout()
        assert stdout == 'stripped'
        assert not stdout.endswith('\n')

    def test_read_stdout_no_capture_returns_empty(self):
        """read_stdout returns '' when no PTY was used."""
        result = InProcessCallable(lambda: 0)()
        assert result.read_stdout() == ''

    def test_read_stderr_no_capture_returns_empty(self):
        """read_stderr returns '' when no PTY was used."""
        result = InProcessCallable(lambda: 0)()
        assert result.read_stderr() == ''

    def test_read_methods_idempotent(self):
        """Multiple reads return the same result."""
        result = run(prog('echo')('idempotent'), silent=True)
        first = result.read_stdout()
        second = result.read_stdout()
        assert first == second == 'idempotent'
