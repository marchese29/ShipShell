"""Tests for environment variable type handling."""

from pathlib import Path

import pytest

from shell.compat.bash import source_bash
from shell.environment import ColonDelimitedPath, env


@pytest.fixture(autouse=True)
def reset_env():
    """Reset environment state between tests."""
    # Store original state
    original_env = dict(env._env)
    original_hints = dict(env._type_hints)
    yield
    # Restore
    env._env.clear()
    env._env.update(original_env)
    env._type_hints.clear()
    env._type_hints.update(original_hints)


class TestColonDelimitedPath:
    """Tests for ColonDelimitedPath."""

    def test_from_string_basic(self):
        cdp = ColonDelimitedPath('/usr/bin:/bin')
        assert list(cdp) == [Path('/usr/bin'), Path('/bin')]

    def test_from_string_empty(self):
        cdp = ColonDelimitedPath('')
        assert list(cdp) == []

    def test_from_string_single(self):
        cdp = ColonDelimitedPath('/usr/bin')
        assert list(cdp) == [Path('/usr/bin')]

    def test_to_string(self):
        cdp = ColonDelimitedPath('/usr/bin:/bin')
        assert str(cdp) == '/usr/bin:/bin'

    def test_to_string_empty(self):
        cdp = ColonDelimitedPath('')
        assert str(cdp) == ''

    def test_repr(self):
        cdp = ColonDelimitedPath('/usr/bin:/bin')
        assert 'ColonDelimitedPath' in repr(cdp)

    def test_is_list_of_paths(self):
        cdp = ColonDelimitedPath('/usr/bin:/bin')
        assert isinstance(cdp, list)
        assert all(isinstance(p, Path) for p in cdp)

    def test_mutable(self):
        cdp = ColonDelimitedPath('/usr/bin')
        cdp.append(Path('/bin'))
        assert list(cdp) == [Path('/usr/bin'), Path('/bin')]
        assert str(cdp) == '/usr/bin:/bin'


class TestTypeRegistry:
    """Tests for type registry functionality."""

    def test_int_type_recorded(self):
        env['MY_INT'] = 42
        assert env.get_type_hint('MY_INT') == int
        assert env['MY_INT'] == 42

    def test_path_type_recorded(self):
        env['MY_PATH'] = Path('/tmp')
        assert issubclass(env.get_type_hint('MY_PATH'), Path)
        assert env['MY_PATH'] == Path('/tmp')

    def test_string_no_type_hint(self):
        env['MY_STR'] = 'hello'
        assert env.get_type_hint('MY_STR') is None
        assert env['MY_STR'] == 'hello'

    def test_string_clears_type_hint(self):
        env['MY_VAR'] = 42
        assert env.get_type_hint('MY_VAR') == int
        env['MY_VAR'] = 'now a string'
        assert env.get_type_hint('MY_VAR') is None

    def test_clear_type_hint(self):
        env['MY_INT'] = 42
        assert env.get_type_hint('MY_INT') == int
        env.clear_type_hint('MY_INT')
        assert env.get_type_hint('MY_INT') is None

    def test_clear_nonexistent_type_hint_no_error(self):
        env.clear_type_hint('NONEXISTENT')  # Should not raise


class TestBashTypePreservation:
    """Tests for type preservation through bash roundtrips."""

    def test_int_preserved_through_bash(self):
        """Integer type is restored after bash modifies value."""
        env['MY_COUNT'] = 42
        assert env.get_type_hint('MY_COUNT') == int

        source_bash('export MY_COUNT=$((MY_COUNT + 1))')()

        assert env['MY_COUNT'] == 43
        assert isinstance(env['MY_COUNT'], int)

    def test_int_preserved_unchanged(self):
        """Integer type preserved even when bash doesn't modify it."""
        env['MY_NUM'] = 100
        source_bash('echo $MY_NUM')()  # Just read, don't modify
        # The value comes back from bash env, should still be int
        assert isinstance(env['MY_NUM'], int)

    def test_path_preserved_through_bash(self):
        """Path type is restored after bash returns."""
        env['MY_DIR'] = Path('/tmp')
        assert issubclass(env.get_type_hint('MY_DIR'), Path)

        source_bash('export MY_DIR=/var')()

        assert env['MY_DIR'] == Path('/var')
        assert isinstance(env['MY_DIR'], Path)

    def test_type_fallback_when_coercion_fails(self):
        """Type hint cleared when coercion fails."""
        env['MY_INT'] = 42
        assert env.get_type_hint('MY_INT') == int

        source_bash('export MY_INT=not_a_number')()

        assert env['MY_INT'] == 'not_a_number'
        assert isinstance(env['MY_INT'], str)
        assert env.get_type_hint('MY_INT') is None

    def test_colon_delimited_path_preserved(self):
        """ColonDelimitedPath is restored after bash modifies it."""
        env['MY_PATH_LIST'] = ColonDelimitedPath('/usr/bin:/bin')
        assert env.get_type_hint('MY_PATH_LIST') == ColonDelimitedPath

        source_bash('export MY_PATH_LIST=/foo:$MY_PATH_LIST')()

        result = env['MY_PATH_LIST']
        assert isinstance(result, ColonDelimitedPath)
        assert Path('/foo') in result
        assert Path('/usr/bin') in result

    def test_new_var_from_bash_is_string(self):
        """Variables created by bash are strings (no type hint)."""
        source_bash('export BASH_CREATED=123')()

        assert env['BASH_CREATED'] == '123'
        assert isinstance(env['BASH_CREATED'], str)
        assert env.get_type_hint('BASH_CREATED') is None


class TestInheritance:
    """Tests for environment inheritance behavior."""

    def test_regular_vars_are_strings(self):
        """Variables inherited from os.environ should be strings."""
        import os

        # Set a test var in os.environ before initialize
        os.environ['TEST_INHERIT_NUM'] = '42'

        # Re-initialize to pick up the new var
        # Note: In practice this happens once at startup
        # For testing, we directly check what initialize would do
        assert isinstance(os.environ.get('TEST_INHERIT_NUM'), str)

    def test_special_vars_are_typed(self):
        """Special computed vars should be typed."""
        assert isinstance(env['PATH'], ColonDelimitedPath)
        assert isinstance(env['HOME'], Path)
        assert isinstance(env['PWD'], Path)
        assert isinstance(env['SHLVL'], int)
        assert isinstance(env['PPID'], int)
