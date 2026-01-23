"""Tests for bash function wiring to Python namespace.

Verifies that bash functions defined with run_bash_code() are accessible
as callable Python functions after the script runs.
"""

import types

import pytest

from shell.compat.bash import run_bash_code
from shell.environment import env
from shell.model import InProcessCallable, capture


@pytest.fixture(autouse=True)
def init_shell_env():
    """Initialize shell environment for each test."""
    env.initialize()


@pytest.fixture
def clean_main():
    """Clean up any functions added to __main__ after each test."""
    import __main__

    # Track what was there before
    before = set(__main__.__dict__.keys())

    yield __main__

    # Remove anything we added
    after = set(__main__.__dict__.keys())
    for name in after - before:
        delattr(__main__, name)


class TestFunctionWiringToMain:
    """Test that functions get wired to __main__ by default."""

    def test_function_available_in_main(self, clean_main):
        """A defined function becomes available in __main__."""
        run_bash_code('greet() { echo hello; }')
        assert hasattr(clean_main, 'greet')

    def test_function_is_callable(self, clean_main):
        """Wired functions are callable."""
        run_bash_code('greet() { echo hello; }')
        assert callable(clean_main.greet)

    def test_function_has_correct_name(self, clean_main):
        """Wired function has __name__ set correctly."""
        run_bash_code('my_func() { echo test; }')
        assert clean_main.my_func.__name__ == 'my_func'

    def test_function_has_docstring(self, clean_main):
        """Wired function has a docstring."""
        run_bash_code('foo() { echo bar; }')
        assert clean_main.foo.__doc__ is not None
        assert 'foo' in clean_main.foo.__doc__


class TestFunctionWiringToCustomScope:
    """Test wiring functions to a custom module scope."""

    def test_custom_scope_creates_module(self, clean_main):
        """Using scope parameter creates a module in __main__."""
        run_bash_code('foo() { echo bar; }', scope='mymodule')
        assert hasattr(clean_main, 'mymodule')
        assert isinstance(clean_main.mymodule, types.ModuleType)

    def test_custom_scope_contains_function(self, clean_main):
        """Function is accessible via custom scope."""
        run_bash_code('greet() { echo hello; }', scope='funcs')
        assert hasattr(clean_main.funcs, 'greet')

    def test_multiple_functions_same_scope(self, clean_main):
        """Multiple functions can be wired to the same scope."""
        run_bash_code("""
            foo() { echo foo; }
            bar() { echo bar; }
        """, scope='utils')
        assert hasattr(clean_main.utils, 'foo')
        assert hasattr(clean_main.utils, 'bar')


class TestFunctionWiringDisabled:
    """Test that wiring can be disabled."""

    def test_scope_none_skips_wiring(self, clean_main):
        """scope=None prevents wiring to Python namespace."""
        run_bash_code('should_not_wire() { echo test; }', scope=None)
        assert not hasattr(clean_main, 'should_not_wire')


class TestWiredFunctionExecution:
    """Test that wired functions execute correctly."""

    def test_function_produces_output(self, clean_main, capsys):
        """Wired function produces stdout when called."""
        run_bash_code('greet() { echo hello; }')
        clean_main.greet()
        captured = capsys.readouterr()
        assert captured.out == 'hello\n'

    def test_function_with_arguments(self, clean_main, capsys):
        """Wired function receives and uses arguments."""
        run_bash_code('greet() { echo "Hello, $1!"; }')
        clean_main.greet('World')
        captured = capsys.readouterr()
        assert captured.out == 'Hello, World!\n'

    def test_function_with_multiple_args(self, clean_main, capsys):
        """Wired function can receive multiple arguments."""
        run_bash_code('add() { echo $(($1 + $2)); }')
        clean_main.add(3, 5)
        captured = capsys.readouterr()
        assert captured.out == '8\n'

    def test_function_returns_exit_code(self, clean_main):
        """Wired function returns the exit code."""
        run_bash_code('succeed() { return 0; }')
        result = clean_main.succeed()
        assert result == 0

    def test_function_returns_nonzero_exit(self, clean_main):
        """Wired function returns non-zero exit codes."""
        run_bash_code('fail42() { return 42; }')
        result = clean_main.fail42()
        assert result == 42

    def test_function_calls_other_functions(self, clean_main, capsys):
        """Wired function can call other bash functions."""
        run_bash_code("""
            inner() { echo "inner called"; }
            outer() { inner; echo "outer done"; }
        """)
        clean_main.outer()
        captured = capsys.readouterr()
        assert 'inner called' in captured.out
        assert 'outer done' in captured.out


class TestWiredFunctionInPipeline:
    """Test that wired functions work in shell pipelines."""

    def test_function_in_pipeline_as_sink(self, clean_main):
        """Wired function can be used as pipeline sink via InProcessCallable."""
        run_bash_code('upper() { tr a-z A-Z; }')
        # Wrap the wired function for pipeline use
        result = capture(
            InProcessCallable(clean_main.upper, name='upper')
        )
        # Note: This tests that the function can be wrapped;
        # actual pipeline usage would require more setup
        assert result.exit_code == 0


class TestWiringNameTransformation:
    """Test transformation of function names for Python compatibility."""

    def test_dash_converted_to_underscore(self, clean_main, capsys):
        """Functions with dashes are wired with underscores."""
        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter('always')
            run_bash_code('my-func() { echo dash_worked; }')

        # Function should be wired with underscore
        assert hasattr(clean_main, 'my_func')
        assert not hasattr(clean_main, 'my-func')
        # Warning about transformation
        assert len(w) == 1
        assert "wired as 'my_func'" in str(w[0].message)
        # Verify it actually works
        clean_main.my_func()
        captured = capsys.readouterr()
        assert 'dash_worked' in captured.out

    def test_numeric_start_prefixed(self, clean_main, capsys):
        """Functions starting with numbers get underscore prefix."""
        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter('always')
            run_bash_code('123func() { echo num_worked; }')

        # Function should be wired with underscore prefix
        assert hasattr(clean_main, '_123func')
        # Warning about transformation
        assert len(w) == 1
        assert "wired as '_123func'" in str(w[0].message)
        # Verify it works
        clean_main._123func()
        captured = capsys.readouterr()
        assert 'num_worked' in captured.out

    def test_keyword_gets_underscore_suffix(self, clean_main, capsys):
        """Functions named after Python keywords get underscore suffix."""
        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter('always')
            run_bash_code('None() { echo keyword_worked; }')

        # Function should be wired with underscore suffix
        assert hasattr(clean_main, 'None_')
        assert not hasattr(clean_main, 'None')
        # Warning about transformation
        assert len(w) == 1
        assert "wired as 'None_'" in str(w[0].message)
        # Verify it works
        clean_main.None_()
        captured = capsys.readouterr()
        assert 'keyword_worked' in captured.out

    def test_builtin_shadow_warns_but_wires(self, clean_main):
        """Functions shadowing builtins are wired with a warning."""
        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter('always')
            run_bash_code('len() { echo custom_len; }')

        # Function SHOULD be wired (user might want this)
        assert hasattr(clean_main, 'len')
        # But a warning should be issued
        assert len(w) == 1
        assert 'shadows Python builtin' in str(w[0].message)

    def test_valid_underscore_name_wired(self, clean_main):
        """Functions with underscores are valid and wired without warnings."""
        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter('always')
            run_bash_code('my_func() { echo test; }')

        assert hasattr(clean_main, 'my_func')
        # No warnings for already-valid names
        assert len(w) == 0
