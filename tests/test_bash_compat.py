"""Bash compatibility tests.

Each test compares our bash interpreter output against real bash.
Test cases are defined in bash_compat_tests.json for easy maintenance.
"""

import json
from pathlib import Path

import pytest

from tests.bash import BashTest, run_bash_reference, run_isolated

# Load test cases from JSON
_TEST_FILE = Path(__file__).parent / 'bash_compat_tests.json'
BASH_TESTS = [BashTest(**t) for t in json.loads(_TEST_FILE.read_text())]


def make_test_id(test: BashTest) -> str:
    """Generate test ID from category and name."""
    if test.category and test.name:
        return f'{test.category}/{test.name}'
    return test.name or test.code[:30]


@pytest.mark.parametrize('test', BASH_TESTS, ids=make_test_id)
def test_bash_compat(test: BashTest):
    """Compare bash interpreter output against real bash."""
    if test.skip:
        pytest.skip(test.skip)

    ours = run_isolated(test.code, test.setup_env)
    bash = run_bash_reference(test.code, test.setup_env)

    if test.check_stdout:
        assert ours.stdout == bash.stdout, 'stdout mismatch'

    assert ours.exit_code == bash.exit_code, (
        f'exit_code mismatch: ours={ours.exit_code}, bash={bash.exit_code}'
    )
