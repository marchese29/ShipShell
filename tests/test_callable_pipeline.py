"""Tests for callable pipeline support.

Verifies that Python callables can participate in shell pipelines.
"""

import sys
from pathlib import Path

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


# =============================================================================
# Conditional Chaining Tests
# =============================================================================


def test_if_success_runs_on_zero_exit():
    """if_success runs second command when first succeeds."""
    result = capture(prog('true')().if_success(prog('echo')('yes')))
    assert result.read_stdout() == 'yes'
    assert result.exit_code == 0


def test_if_success_skips_on_nonzero_exit():
    """if_success skips second command when first fails."""
    result = capture(prog('false')().if_success(prog('echo')('yes')))
    assert result.read_stdout() == ''
    assert result.exit_code == 1


def test_if_fail_runs_on_nonzero_exit():
    """if_fail runs second command when first fails."""
    result = capture(prog('false')().if_fail(prog('echo')('failed')))
    assert result.read_stdout() == 'failed'
    assert result.exit_code == 0


def test_if_fail_skips_on_zero_exit():
    """if_fail skips second command when first succeeds."""
    result = capture(prog('true')().if_fail(prog('echo')('failed')))
    assert result.read_stdout() == ''
    assert result.exit_code == 0


def test_conditional_chain_multiple():
    """Multiple conditions can be chained."""
    # true && echo a && echo b -> both echoes run, both write to stdout
    result = capture(
        prog('true')()
        .if_success(prog('echo')('a'))
        .if_success(prog('echo')('b'))
    )
    assert result.read_stdout() == 'a\nb'


def test_conditional_accepts_callable():
    """if_success/if_fail accept plain callables."""
    def say_hello():
        print('hello')

    result = capture(prog('true')().if_success(say_hello))
    assert result.read_stdout() == 'hello'


def test_conditional_callable_return_false():
    """Callable returning False has exit code 1."""
    def fail():
        return False

    result = capture(prog('true')().if_success(fail))
    assert result.exit_code == 1


def test_if_success_with_pipeline():
    """Conditional can contain a pipeline."""
    result = capture(
        prog('true')().if_success(prog('echo')('hello') | prog('cat')())
    )
    assert result.read_stdout() == 'hello'


# =============================================================================
# Operator Conditional Chaining Tests (+ for && and - for ||)
# =============================================================================


def test_plus_operator_success():
    """+ operator runs second command when first succeeds."""
    result = capture(prog('true')() + prog('echo')('yes'))
    assert result.read_stdout() == 'yes'
    assert result.exit_code == 0


def test_plus_operator_failure():
    """+ operator skips second command when first fails."""
    result = capture(prog('false')() + prog('echo')('yes'))
    assert result.read_stdout() == ''
    assert result.exit_code == 1


def test_minus_operator_failure():
    """- operator runs second command when first fails."""
    result = capture(prog('false')() - prog('echo')('recovered'))
    assert result.read_stdout() == 'recovered'
    assert result.exit_code == 0


def test_minus_operator_success():
    """- operator skips second command when first succeeds."""
    result = capture(prog('true')() - prog('echo')('fallback'))
    assert result.read_stdout() == ''
    assert result.exit_code == 0


def test_operator_chain_multiple():
    """Operators can be chained: true + echo a + echo b."""
    result = capture(prog('true')() + prog('echo')('a') + prog('echo')('b'))
    assert result.read_stdout() == 'a\nb'


def test_operator_mixed_chain():
    """Mixed operators: false - echo fallback + echo next."""
    result = capture(prog('false')() - prog('echo')('fallback') + prog('echo')('next'))
    assert result.read_stdout() == 'fallback\nnext'


def test_plus_with_callable():
    """+ operator accepts plain callables."""
    def greet():
        print('hello')

    result = capture(prog('true')() + greet)
    assert result.read_stdout() == 'hello'


def test_minus_with_callable():
    """- operator accepts plain callables."""
    def handle_error():
        print('error handled')

    result = capture(prog('false')() - handle_error)
    assert result.read_stdout() == 'error handled'


# =============================================================================
# Uncalled Program Auto-Invoke Tests
# =============================================================================


def test_uncalled_program_in_pipeline_right():
    """Uncalled Program on right side of pipe auto-invokes."""
    # prog('cat') without () should auto-invoke
    result = capture(prog('echo')('hello') | prog('cat'))
    assert result.read_stdout() == 'hello'


def test_uncalled_program_in_pipeline_left():
    """Uncalled Program on left side of pipe auto-invokes."""
    # prog('true') without () should auto-invoke
    result = capture(prog('true') | prog('echo')('piped'))
    assert result.read_stdout() == 'piped'


def test_uncalled_program_in_conditional_plus():
    """Uncalled Program with + operator auto-invokes."""
    # prog('true') without () should auto-invoke
    result = capture(prog('true') + prog('echo')('success'))
    assert result.read_stdout() == 'success'


def test_uncalled_program_in_conditional_minus():
    """Uncalled Program with - operator auto-invokes."""
    # prog('false') without () should auto-invoke
    result = capture(prog('false') - prog('echo')('fallback'))
    assert result.read_stdout() == 'fallback'


def test_uncalled_program_chain():
    """Multiple uncalled Programs can be chained."""
    # ls without args: ls | cat | cat
    result = capture(prog('echo')('test') | prog('cat') | prog('cat'))
    assert result.read_stdout() == 'test'


# =============================================================================
# Stdin Content Tests
# =============================================================================


def test_stdin_content_string():
    """stdin_content() pipes string content to command."""
    result = capture(prog('cat')().stdin_content('hello world'))
    assert result.read_stdout() == 'hello world'


def test_stdin_content_multiline():
    """stdin_content() handles multiline strings."""
    result = capture(prog('wc')('-l').stdin_content('line1\nline2\nline3\n'))
    assert result.read_stdout().strip() == '3'


def test_stdin_content_bytes():
    """stdin_content() pipes bytes content to command."""
    result = capture(prog('cat')().stdin_content(b'binary data'))
    assert result.read_stdout() == 'binary data'


def test_stdin_content_filelike():
    """stdin_content() reads from file-like object."""
    from io import StringIO

    content = StringIO('from stringio')
    result = capture(prog('cat')().stdin_content(content))
    assert result.read_stdout() == 'from stringio'


def test_stdin_content_bytesio():
    """stdin_content() reads from BytesIO."""
    from io import BytesIO

    content = BytesIO(b'from bytesio')
    result = capture(prog('cat')().stdin_content(content))
    assert result.read_stdout() == 'from bytesio'


def test_stdin_content_with_pipeline():
    """stdin_content() works in pipelines."""
    result = capture(prog('grep')('hello').stdin_content('hello world\ngoodbye') | prog('cat')())
    assert result.read_stdout() == 'hello world'


def test_stdin_content_with_redirect():
    """stdin_content() can be combined with output redirect."""
    import tempfile

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        outpath = Path(f.name)
    try:
        (prog('cat')().stdin_content('test output') > outpath)()
        assert outpath.read_text() == 'test output'
    finally:
        outpath.unlink()
