"""Tests for callable pipeline support.

Verifies that Python callables can participate in shell pipelines.
"""

import sys

import pytest

from shell.environment import env
from shell.model import InProcessCallable, capture, prog


@pytest.fixture(autouse=True)
def init_shell_env():
    """Initialize shell environment for each test."""
    env.initialize()


def test_callable_in_pipeline():
    """A callable can receive piped input and transform it."""

    def upper():
        for line in sys.stdin:
            print(line.upper(), end='')

    result = capture(prog('echo')('hello') | upper)
    assert result.read_stdout() == 'HELLO'


def test_callable_as_source():
    """A callable can be the source of a pipeline."""

    def greet():
        print('hello')
        print('goodbye')

    result = capture(greet | prog('grep')('hello'))
    assert result.read_stdout() == 'hello'


def test_generator_as_source():
    """Generator functions work - each yield becomes a line."""

    def lines():
        yield 'apple'
        yield 'banana'
        yield 'cherry'

    result = capture(lines | prog('grep')('a'))
    # grep matches 'apple' and 'banana' (both contain 'a')
    assert 'apple' in result.read_stdout()
    assert 'banana' in result.read_stdout()
    assert 'cherry' not in result.read_stdout()


def test_callable_exit_code_none():
    """Callable returning None has exit code 0."""

    def noop():
        pass

    result = capture(noop | prog('cat')())
    assert result.exit_code == 0


def test_callable_exit_code_int():
    """Callable can return an integer exit code."""

    def fail():
        return 42

    runner = InProcessCallable(fail)
    result = runner()
    assert result.exit_code == 42


def test_callable_exit_code_bool_true():
    """Callable returning True has exit code 0."""

    def success():
        return True

    runner = InProcessCallable(success)
    result = runner()
    assert result.exit_code == 0


def test_callable_exit_code_bool_false():
    """Callable returning False has exit code 1."""

    def failure():
        return False

    runner = InProcessCallable(failure)
    result = runner()
    assert result.exit_code == 1


def test_callable_exception():
    """Exception in callable results in exit code 1."""

    def explode():
        raise ValueError('boom')

    runner = InProcessCallable(explode)
    result = runner()
    assert result.exit_code == 1


def test_async_callable_rejected():
    """Async callables are rejected with clear error."""

    async def async_func():
        pass

    with pytest.raises(TypeError, match='Async callables cannot be used'):
        InProcessCallable(async_func)


def test_async_generator_rejected():
    """Async generators are rejected with clear error."""

    async def async_gen():
        yield 1

    with pytest.raises(TypeError, match='Async callables cannot be used'):
        InProcessCallable(async_gen)


def test_pipeline_chain():
    """Multiple callables can be chained in a pipeline."""

    def add_prefix():
        for line in sys.stdin:
            print(f'PREFIX: {line}', end='')

    def add_suffix():
        for line in sys.stdin:
            print(f'{line.rstrip()} :SUFFIX')

    result = capture(prog('echo')('hello') | add_prefix | add_suffix)
    assert result.read_stdout() == 'PREFIX: hello :SUFFIX'


def test_callable_with_command_on_both_sides():
    """Callable in the middle of external commands."""

    def double():
        for line in sys.stdin:
            print(line, end='')
            print(line, end='')

    result = capture(prog('echo')('test') | double | prog('wc')('-l'))
    # 'test' becomes 'test\ntest\n' which is 2 lines
    assert result.read_stdout().strip() == '2'


# =============================================================================
# Process Substitution Tests
# =============================================================================


def test_process_input_basic():
    """ProcessInput provides a readable path to command output."""
    with prog('echo')('hello').as_input() as inp:
        result = capture(prog('cat')(inp.path))
        assert result.read_stdout() == 'hello'


def test_process_input_multiple():
    """Multiple process inputs work together."""
    with (
        prog('echo')('a').as_input() as a,
        prog('echo')('b').as_input() as b,
    ):
        result = capture(prog('diff')(a.path, b.path))
        assert result.exit_code != 0  # diff exits non-zero when files differ


def test_process_output_basic():
    """ProcessOutput provides a writable path to command stdin."""
    # cat with no args reads from stdin and writes to stdout
    # We redirect echo's output to cat's stdin via the process substitution
    with prog('cat')().as_output() as out:
        # This writes "hello" to the cat process's stdin
        result = capture(prog('echo')('hello') > out.path)
        assert result.exit_code == 0


def test_process_substitution_fd_property():
    """fd property returns the raw file descriptor."""
    with prog('echo')('test').as_input() as inp:
        assert isinstance(inp.fd, int)
        assert inp.fd > 2  # Not stdin/stdout/stderr


def test_process_substitution_path_is_path_object():
    """path property returns a Path object."""
    from pathlib import Path
    with prog('echo')('test').as_input() as inp:
        assert isinstance(inp.path, Path)
        assert str(inp.path).startswith('/dev/fd/')
