"""Bash compatibility tests.

Each test compares our bash interpreter output against real bash.
"""

import pytest

from tests.bash import BashTest, Contains, Exact, run_bash_reference, run_isolated

# === Test Cases ===

ECHO_TESTS = [
    BashTest(
        name='simple_echo',
        code='echo hello',
    ),
    BashTest(
        name='echo_multiple_args',
        code='echo hello world',
    ),
    BashTest(
        name='echo_quoted',
        code='echo "hello world"',
    ),
    BashTest(
        name='echo_single_quoted',
        code="echo 'hello world'",
    ),
]

VARIABLE_TESTS = [
    BashTest(
        name='var_expansion',
        code='echo $FOO',
        setup_env={'FOO': 'bar'},
    ),
    BashTest(
        name='var_braces',
        code='echo ${FOO}',
        setup_env={'FOO': 'bar'},
    ),
    BashTest(
        name='var_default',
        code='echo ${UNSET:-default}',
    ),
    BashTest(
        name='var_default_set',
        code='echo ${FOO:-default}',
        setup_env={'FOO': 'bar'},
    ),
]

CONTROL_FLOW_TESTS = [
    BashTest(
        name='and_operator',
        code='echo one && echo two',
    ),
    BashTest(
        name='or_operator_first_succeeds',
        code='echo one || echo two',
    ),
    BashTest(
        name='semicolon',
        code='echo one; echo two',
    ),
]

ANSI_C_STRING_TESTS = [
    BashTest(
        name='ansi_newline',
        code=r"echo $'hello\nworld'",
    ),
    BashTest(
        name='ansi_tab',
        code=r"echo $'col1\tcol2'",
    ),
    BashTest(
        name='ansi_quote',
        code=r"echo $'it\'s working'",
    ),
    BashTest(
        name='ansi_backslash',
        code=r"echo $'back\\slash'",
    ),
    BashTest(
        name='ansi_hex',
        code=r"echo $'\x48\x49'",
    ),
    BashTest(
        name='ansi_octal',
        code=r"echo $'\110\111'",
    ),
    BashTest(
        name='ansi_escape',
        code=r"echo $'\e[31mred\e[0m'",
    ),
]

BRACE_EXPANSION_TESTS = [
    BashTest(
        name='simple_brace',
        code='echo {a,b,c}',
    ),
    BashTest(
        name='brace_with_prefix',
        code='echo file{1,2}.txt',
    ),
    BashTest(
        name='brace_range',
        code='echo {1..5}',
    ),
    BashTest(
        name='brace_cartesian',
        code='echo {a,b}{1,2}',
    ),
]

EXIT_CODE_TESTS = [
    BashTest(
        name='true_command',
        code='true',
    ),
    BashTest(
        name='false_command',
        code='false',
    ),
    BashTest(
        name='exit_zero',
        code='exit 0',
    ),
    BashTest(
        name='exit_one',
        code='exit 1',
    ),
    BashTest(
        name='exit_42',
        code='exit 42',
    ),
]

HEREDOC_TESTS = [
    BashTest(
        name='simple_heredoc',
        code='cat <<EOF\nhello world\nEOF',
    ),
    BashTest(
        name='heredoc_with_var',
        code='NAME=Alice; cat <<EOF\nhello $NAME\nEOF',
    ),
    BashTest(
        name='heredoc_leading_text',
        code='NAME=Bob; cat <<EOF\nDear $NAME, welcome!\nEOF',
    ),
    BashTest(
        name='heredoc_multiple_vars',
        code='A=1; B=2; cat <<EOF\n$A + $B = 3\nEOF',
    ),
    BashTest(
        name='heredoc_multiline',
        code='cat <<EOF\nline one\nline two\nline three\nEOF',
    ),
    BashTest(
        name='heredoc_tab_strip',
        code='cat <<-EOF\n\thello\n\tworld\nEOF',
    ),
    BashTest(
        name='heredoc_with_leading_tab',
        code='cat <<EOF\n\thello\n\tworld\nEOF',
    ),
]

HERESTRING_TESTS = [
    BashTest(
        name='simple_herestring',
        code='cat <<< "hello"',
    ),
    BashTest(
        name='herestring_with_var',
        code='NAME=world; cat <<< "hello $NAME"',
    ),
    BashTest(
        name='herestring_unquoted',
        code='cat <<< hello',
    ),
]


# === Test Runner ===


def make_test_id(test: BashTest) -> str:
    return test.name or test.code[:30]


@pytest.mark.parametrize('test', ECHO_TESTS, ids=make_test_id)
def test_echo(test: BashTest):
    """Test echo command variants."""
    if test.skip:
        pytest.skip(test.skip)

    ours = run_isolated(test.code, test.setup_env)
    bash = run_bash_reference(test.code, test.setup_env)

    assert ours.stdout == bash.stdout, f'stdout mismatch'
    assert ours.exit_code == bash.exit_code, f'exit_code mismatch'


@pytest.mark.parametrize('test', VARIABLE_TESTS, ids=make_test_id)
def test_variables(test: BashTest):
    """Test variable expansion."""
    if test.skip:
        pytest.skip(test.skip)

    ours = run_isolated(test.code, test.setup_env)
    bash = run_bash_reference(test.code, test.setup_env)

    assert ours.stdout == bash.stdout, f'stdout mismatch'
    assert ours.exit_code == bash.exit_code, f'exit_code mismatch'


@pytest.mark.parametrize('test', CONTROL_FLOW_TESTS, ids=make_test_id)
def test_control_flow(test: BashTest):
    """Test control flow operators."""
    if test.skip:
        pytest.skip(test.skip)

    ours = run_isolated(test.code, test.setup_env)
    bash = run_bash_reference(test.code, test.setup_env)

    assert ours.stdout == bash.stdout, f'stdout mismatch'
    assert ours.exit_code == bash.exit_code, f'exit_code mismatch'


@pytest.mark.parametrize('test', ANSI_C_STRING_TESTS, ids=make_test_id)
def test_ansi_c_strings(test: BashTest):
    """Test ANSI-C quoted strings ($'...')."""
    if test.skip:
        pytest.skip(test.skip)

    ours = run_isolated(test.code, test.setup_env)
    bash = run_bash_reference(test.code, test.setup_env)

    assert ours.stdout == bash.stdout, f'stdout mismatch'
    assert ours.exit_code == bash.exit_code, f'exit_code mismatch'


@pytest.mark.parametrize('test', BRACE_EXPANSION_TESTS, ids=make_test_id)
def test_brace_expansion(test: BashTest):
    """Test brace expansion."""
    if test.skip:
        pytest.skip(test.skip)

    ours = run_isolated(test.code, test.setup_env)
    bash = run_bash_reference(test.code, test.setup_env)

    assert ours.stdout == bash.stdout, f'stdout mismatch'
    assert ours.exit_code == bash.exit_code, f'exit_code mismatch'


@pytest.mark.parametrize('test', EXIT_CODE_TESTS, ids=make_test_id)
def test_exit_codes(test: BashTest):
    """Test exit code capture."""
    if test.skip:
        pytest.skip(test.skip)

    ours = run_isolated(test.code, test.setup_env)
    bash = run_bash_reference(test.code, test.setup_env)

    assert ours.exit_code == bash.exit_code, f'exit_code mismatch: ours={ours.exit_code}, bash={bash.exit_code}'


@pytest.mark.parametrize('test', HEREDOC_TESTS, ids=make_test_id)
def test_heredocs(test: BashTest):
    """Test heredoc redirects."""
    if test.skip:
        pytest.skip(test.skip)

    ours = run_isolated(test.code, test.setup_env)
    bash = run_bash_reference(test.code, test.setup_env)

    assert ours.stdout == bash.stdout, f'stdout mismatch'
    assert ours.exit_code == bash.exit_code, f'exit_code mismatch'


@pytest.mark.parametrize('test', HERESTRING_TESTS, ids=make_test_id)
def test_herestrings(test: BashTest):
    """Test here string redirects (<<<)."""
    if test.skip:
        pytest.skip(test.skip)

    ours = run_isolated(test.code, test.setup_env)
    bash = run_bash_reference(test.code, test.setup_env)

    assert ours.stdout == bash.stdout, f'stdout mismatch'
    assert ours.exit_code == bash.exit_code, f'exit_code mismatch'
