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
        skip='ShipShell-kq2: double-quoted strings not working',
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
