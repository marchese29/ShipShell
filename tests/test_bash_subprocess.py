"""Tests for bash subprocess runner (source_bash).

Tests the subprocess-based bash execution with state synchronization.
"""

from __future__ import annotations

import os
import warnings

import pytest

from shell.compat.bash import (
    BASH_PATH,
    _bash_to_python_name,
    _build_env_overlay,
    _find_state_fd,
    _generate_preamble,
    _parse_epilogue,
    source_bash,
)
from shell.environment import env
from shell.model import IOConfig, capture, prog


@pytest.fixture(autouse=True)
def init_shell_env():
    """Initialize shell environment for each test."""
    env.initialize()


class TestPreambleGeneration:
    """Tests for _generate_preamble()."""

    def test_preamble_includes_cd(self):
        """Preamble includes cd to current directory."""
        preamble = _generate_preamble()
        assert 'cd ' in preamble
        assert str(env.pwd) in preamble

    def test_preamble_with_args(self):
        """Preamble sets positional arguments."""
        preamble = _generate_preamble(['arg1', 'arg2', 'with space'])
        assert 'set --' in preamble
        assert 'arg1' in preamble
        assert 'arg2' in preamble
        assert "'with space'" in preamble


class TestEnvOverlay:
    """Tests for _build_env_overlay()."""

    def test_overlay_includes_user_vars(self):
        """Overlay includes user-defined variables."""
        env['MY_VAR'] = 'my_value'
        overlay, sent_keys = _build_env_overlay()

        assert 'MY_VAR' in overlay
        assert overlay['MY_VAR'] == 'my_value'
        assert 'MY_VAR' in sent_keys

    def test_overlay_excludes_skip_vars(self):
        """Overlay excludes read-only and managed variables."""
        overlay, sent_keys = _build_env_overlay()

        # These should not be in the overlay
        assert 'PWD' not in overlay
        assert 'PPID' not in overlay
        assert 'BASH_VERSION' not in overlay


class TestEpilogueParsing:
    """Tests for _parse_epilogue()."""

    def test_parse_env_section(self):
        """Parses environment variables from env -0 output."""
        output = '<<<ENV>>>\nFOO=bar\0BAZ=qux\0\n<<<PWD>>>\n/tmp\n<<<END>>>'
        env_dict, pwd = _parse_epilogue(output)

        assert env_dict['FOO'] == 'bar'
        assert env_dict['BAZ'] == 'qux'
        assert pwd == '/tmp'

    def test_parse_value_with_equals(self):
        """Parses values containing equals signs."""
        output = '<<<ENV>>>\nFOO=bar=baz\0\n<<<PWD>>>\n/tmp\n<<<END>>>'
        env_dict, pwd = _parse_epilogue(output)

        assert env_dict['FOO'] == 'bar=baz'

    def test_parse_empty_sections(self):
        """Handles empty sections gracefully."""
        output = '<<<ENV>>>\n<<<PWD>>>\n<<<END>>>'
        env_dict, pwd = _parse_epilogue(output)

        assert env_dict == {}
        assert pwd == ''


class TestBashToPythonName:
    """Tests for _bash_to_python_name()."""

    def test_simple_name_unchanged(self):
        """Simple names pass through unchanged."""
        assert _bash_to_python_name('foo') == 'foo'
        assert _bash_to_python_name('my_func') == 'my_func'

    def test_dashes_to_underscores(self):
        """Dashes are converted to underscores."""
        assert _bash_to_python_name('my-func') == 'my_func'
        assert _bash_to_python_name('a-b-c') == 'a_b_c'

    def test_leading_digit_prefixed(self):
        """Leading digits get underscore prefix."""
        assert _bash_to_python_name('123func') == '_123func'
        assert _bash_to_python_name('1') == '_1'

    def test_keywords_suffixed(self):
        """Python keywords get underscore suffix."""
        assert _bash_to_python_name('if') == 'if_'
        assert _bash_to_python_name('for') == 'for_'
        assert _bash_to_python_name('class') == 'class_'


class TestFindStateFd:
    """Tests for _find_state_fd()."""

    def test_default_is_high_fd(self):
        """Default fd is high (62) for macOS compatibility."""
        fd = _find_state_fd(None)
        assert fd >= 62

    def test_avoids_extra_fds_in_io_config(self):
        """Avoids fds already in use by IOConfig."""
        io = IOConfig(extra_fds={62: '/tmp/a', 63: '/tmp/b'})
        fd = _find_state_fd(io)
        assert fd not in {62, 63}
        assert fd >= 62


class TestSourceBashBasic:
    """Basic tests for source_bash()."""

    def test_simple_echo(self):
        """source_bash can run simple echo."""
        result = capture(source_bash('echo hello'))
        assert result.read_stdout() == 'hello'
        assert result.exit_code == 0

    def test_exit_code_preserved(self):
        """Exit code from bash is preserved."""
        result = source_bash('exit 42')()
        assert result.exit_code == 42
        assert env.last_exit == 42

    def test_stdout_passthrough(self):
        """stdout passes through (captured when using capture())."""
        result = capture(source_bash('echo line1; echo line2'))
        assert 'line1' in result.read_stdout()
        assert 'line2' in result.read_stdout()


class TestSourceBashStateSync:
    """Tests for state synchronization between bash and Python."""

    def test_export_syncs_to_python(self):
        """Variables exported in bash are synced to Python."""
        source_bash('export MY_TEST_VAR=hello123')()
        assert env['MY_TEST_VAR'] == 'hello123'

    def test_python_var_available_in_bash(self):
        """Variables set in Python are available in bash."""
        env['FROM_PYTHON'] = 'python_value'
        result = capture(source_bash('echo $FROM_PYTHON'))
        assert result.read_stdout() == 'python_value'

    def test_cd_syncs_to_python(self):
        """cd in bash changes Python's pwd."""
        from pathlib import Path

        original_pwd = env.pwd
        source_bash('cd /tmp')()
        # Use resolve() to handle symlinks (e.g., /tmp -> /private/tmp on macOS)
        assert env.pwd.resolve() == Path('/tmp').resolve()
        # Restore
        os.chdir(str(original_pwd))
        env.chdir(original_pwd)

    def test_roundtrip_variable(self):
        """Variable survives Python -> bash -> Python roundtrip."""
        env['ROUNDTRIP'] = 'original'
        source_bash('export ROUNDTRIP="${ROUNDTRIP}_modified"')()
        assert env['ROUNDTRIP'] == 'original_modified'

    def test_unset_removes_variable(self):
        """Unsetting a variable in bash removes it from Python env."""
        env['TO_UNSET'] = 'will be removed'
        source_bash('unset TO_UNSET')()
        # Variable should be unexported (not visible as exported)
        assert 'TO_UNSET' not in env._exported


class TestSourceBashFunctions:
    """Tests for bash function wiring."""

    def test_function_defined_and_callable(self):
        """Bash function is wired and callable."""
        source_bash('greet() { echo "hello $1"; }')()

        # Function should be wired into __main__
        import __main__

        assert hasattr(__main__, 'greet')

        # Calling returns a BashSource (runnable), not auto-executes
        runnable = __main__.greet('world')
        assert hasattr(runnable, '_exec')

        # Execute and verify output
        result = capture(runnable)
        assert result.read_stdout() == 'hello world'

    def test_function_persists_across_calls(self):
        """Functions defined in one source_bash call persist to next."""
        source_bash('persist_func() { echo persisted; }')()

        # Should be callable in next source_bash
        result = capture(source_bash('persist_func'))
        assert result.read_stdout() == 'persisted'

    def test_function_rename_warning(self):
        """Warning issued when function name is changed for Python."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter('always')
            source_bash('my-dashed-func() { echo hi; }')()

            # Should have warned about the rename
            assert len(w) == 1
            assert 'my-dashed-func' in str(w[0].message)
            assert 'my_dashed_func' in str(w[0].message)

    def test_scope_none_skips_wiring(self):
        """scope=None skips function wiring."""
        source_bash('no_wire_func() { echo hi; }', scope=None)()

        import __main__

        # Function should NOT be wired
        assert not hasattr(__main__, 'no_wire_func')

        # But function should still be callable via bash
        result = capture(source_bash('no_wire_func'))
        assert result.read_stdout() == 'hi'

    def test_scope_module(self):
        """Functions can be wired into a module namespace."""
        source_bash('module_func() { echo "from module"; }', scope='myfuncs')()

        import __main__

        assert hasattr(__main__, 'myfuncs')
        assert hasattr(__main__.myfuncs, 'module_func')

        result = capture(__main__.myfuncs.module_func())
        assert result.read_stdout() == 'from module'

    def test_function_with_multiple_args(self):
        """Wired function receives multiple arguments correctly."""
        source_bash('add() { echo $(($1 + $2 + $3)); }')()

        import __main__

        result = capture(__main__.add(1, 2, 3))
        assert result.read_stdout() == '6'

    def test_function_returns_exit_code(self):
        """Wired function returns bash exit code."""
        source_bash('fail42() { return 42; }')()

        import __main__

        result = __main__.fail42()()
        assert result.exit_code == 42

    def test_function_sees_python_env_vars(self):
        """Wired function can access variables set in Python."""
        env['GREETING'] = 'Hello from Python'
        source_bash('show_greeting() { echo "$GREETING"; }')()

        import __main__

        result = capture(__main__.show_greeting())
        assert result.read_stdout() == 'Hello from Python'

    def test_function_modifies_env(self):
        """Wired function can modify environment variables."""
        source_bash('set_var() { export MODIFIED_BY_FUNC="yes"; }')()

        import __main__

        __main__.set_var()()
        assert env['MODIFIED_BY_FUNC'] == 'yes'


class TestSourceBashComposability:
    """Tests for composing source_bash with other runnables."""

    def test_pipeline_with_grep(self):
        """source_bash can be piped to other commands."""
        result = capture(source_bash('echo -e "foo\\nbar\\nbaz"') | prog('grep')('ba'))
        lines = result.read_stdout().strip().split('\n')
        assert 'bar' in lines
        assert 'baz' in lines
        assert 'foo' not in lines

    def test_redirect_stdout(self):
        """source_bash output can be redirected to file."""
        import tempfile

        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            temp_path = f.name

        try:
            (source_bash('echo redirected') > temp_path)()
            with open(temp_path) as f:
                assert f.read().strip() == 'redirected'
        finally:
            os.unlink(temp_path)

    def test_function_in_pipeline(self):
        """Wired bash functions work in pipelines."""
        source_bash('loud() { tr a-z A-Z; }')()

        import __main__

        result = capture(prog('echo')('hello') | __main__.loud())
        assert result.read_stdout() == 'HELLO'


class TestSourceBashArgs:
    """Tests for positional arguments."""

    def test_args_available_as_positional(self):
        """args parameter sets $1, $2, etc."""
        result = capture(source_bash('echo "$1 $2"', args=['first', 'second']))
        assert result.read_stdout() == 'first second'

    def test_args_with_spaces(self):
        """Arguments with spaces are handled correctly."""
        result = capture(source_bash('echo "$1"', args=['hello world']))
        assert result.read_stdout() == 'hello world'


class TestBashPathDetection:
    """Tests for BASH_PATH selection."""

    def test_bash_path_is_set(self):
        """BASH_PATH is set to a valid bash."""
        assert BASH_PATH is not None
        assert os.path.exists(BASH_PATH)
        assert os.access(BASH_PATH, os.X_OK)
