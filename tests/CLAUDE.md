# Testing Guidelines

## Test Organization

Tests are organized by type:

- `test_bash_subprocess.py` - Tests for bash subprocess runner (`source_bash`)
- `test_io_extra_fds.py` - Tests for `IOConfig.extra_fds` and fd redirection
- `test_callable_pipeline.py` - Python API tests (pipelines, callables, process substitution)
- `test_trap.py` - Trap system tests (DEBUG, ERR, EXIT, signals)
- `test_pty.py` - PTY-based output capture tests (all use `silent=True`, run in CI)

## Running Tests

```bash
# All tests
uv run pytest tests/ -v

# Specific test file
uv run pytest tests/test_bash_subprocess.py -v

# Single test
uv run pytest tests/test_bash_subprocess.py::TestSourceBashBasic::test_simple_echo -v
```

## Capturing Output

Use `run(cmd, silent=True)` to capture command output:

```python
from shell.model import run, prog

# Capture stdout/stderr
result = run(prog('echo')('hello'), silent=True)
assert result.read_stdout() == 'hello'

# Works with pipelines, conditionals, subshells
result = run(prog('echo')('hello') | prog('cat')(), silent=True)
assert result.read_stdout() == 'hello'
```

When a real terminal is available, `run()` uses dual PTYs for capture (programs see `isatty()=True`). In non-terminal environments (CI, pytest), `silent=True` still works — it bypasses the `isatty()` check and uses PTYs for capture without terminal echo.

## Bash Subprocess Tests (`test_bash_subprocess.py`)

Tests for `source_bash()` which runs real bash with state synchronization:

```python
from shell.compat.bash import source_bash
from shell.model import run

# Basic execution
result = run(source_bash('echo hello'), silent=True)
assert result.read_stdout() == 'hello'

# State sync
source_bash('export FOO=bar')()
assert env['FOO'] == 'bar'

# Function wiring
source_bash('greet() { echo "hi $1"; }')()
result = run(greet('world'), silent=True)
assert result.read_stdout() == 'hi world'
```

Key test classes:
- `TestSourceBashBasic` - echo, exit codes, stdout
- `TestSourceBashStateSync` - env vars, pwd, roundtrips
- `TestSourceBashFunctions` - function wiring and calling
- `TestSourceBashComposability` - pipelines, redirects

## Extra FDs Tests (`test_io_extra_fds.py`)

Tests for redirecting arbitrary file descriptors via IOConfig:

```python
from shell.model import IOConfig, InProcessCallable
import os

# Redirect fd 3 to file
io = IOConfig().with_fd(3, '/tmp/log.txt')

# Use in a command
def write_to_fd3():
    os.write(3, b'hello from fd 3')

cmd = InProcessCallable(write_to_fd3)
cmd._io = IOConfig().with_fd(3, '/tmp/log.txt')
cmd()
```

## PTY Tests (`test_pty.py`)

Tests for PTY-based output capture. All tests use `silent=True`, which works without a real terminal — PTYs are allocated for capture regardless of `isatty()` status.

## Bash Version

The bash runner uses `BASH_PATH` (defined in `shell/compat/bash.py`):
- Prefers `/opt/homebrew/bin/bash` (bash 5.x via Homebrew)
- Falls back to `/bin/bash` (macOS ships with bash 3.2)

**Install modern bash for full feature support:**
```bash
brew install bash
```

## Architecture Notes

- Fork-based isolation: Child process runs bash, parent collects state
- State sync via dedicated pipe (fd 62) to avoid stdout/stderr interference
- Exit codes come from `waitpid` status
- Functions are wired as Python callables that return `BashSource` runnables
