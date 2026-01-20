# Testing Guidelines

## Test Organization

Tests are organized by type:

- `test_bash_compat.py` - Integration tests comparing our bash interpreter against real bash
- `test_bash_pure.py` - Unit tests for pure functions (no side effects)
- `test_harness_smoke.py` - Smoke tests for the test harness itself

## Test Harness (`tests/bash/`)

The harness provides fork-based isolation for running bash code:

### Key Functions

```python
from tests.bash import run_isolated, run_bash_reference, BashTest

# Run code with our interpreter (isolated via fork)
result = run_isolated('echo hello', setup_env={'FOO': 'bar'})
# Returns: CapturedState(stdout, stderr, exit_code, env)

# Run code with real bash for comparison
bash_result = run_bash_reference('echo hello', setup_env={'FOO': 'bar'})

# Probe environment variables after execution
result = run_isolated('export FOO=bar', probe_env_vars=['FOO'])
print(result.env)  # {'FOO': 'bar'}
```

### BashTest Dataclass

```python
BashTest(
    code='echo $FOO',           # Bash code to run
    name='var_expansion',       # Test name (optional)
    setup_env={'FOO': 'bar'},   # Environment setup
    skip='reason',              # Skip with reason (optional)
)
```

### Check Types (for explicit expectations)

```python
from tests.bash import Exact, Contains, Regex, LinesUnordered, Ignore, check

assert check(result.stdout, Exact('hello\n'))
assert check(result.stdout, Contains(['hello', 'world']))
assert check(result.stdout, Regex(r'hello.*'))
assert check(result.stdout, LinesUnordered(['line1', 'line2']))
assert check(result.stderr, Ignore())  # Don't check
```

## Writing Integration Tests

Pattern for comparing against real bash:

```python
@pytest.mark.parametrize('test', TEST_CASES, ids=make_test_id)
def test_feature(test: BashTest):
    if test.skip:
        pytest.skip(test.skip)

    ours = run_isolated(test.code, test.setup_env)
    bash = run_bash_reference(test.code, test.setup_env)

    assert ours.stdout == bash.stdout
    assert ours.exit_code == bash.exit_code
```

## Writing Unit Tests

For pure functions with no side effects:

```python
from shell.compat.bash import _expand_braces, _bash_to_str

class TestExpandBraces:
    def test_simple_expansion(self):
        assert _expand_braces('{a,b,c}') == ['a', 'b', 'c']
```

## Architecture Notes

- Fork-based isolation: Child process runs bash code, parent collects results
- Exit codes come from `waitpid` status (not piped)
- Probed env vars are written to a pipe as JSON
- The global `env` singleton is used for command resolution (PATH lookup)
