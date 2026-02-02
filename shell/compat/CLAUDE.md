# Bash Compatibility Layer

This module provides bash syntax support for ShipShell. It's a **feature** of the REPL, not the core - the core is `shell/model.py` which provides the Pythonic shell abstractions.

## Architecture

The bash compatibility layer runs **real bash** as a subprocess and synchronizes state back to Python. This gives perfect bash compatibility without reimplementing bash semantics.

### Key Components

- `source_bash(code, args, scope)` - Entry point to execute bash code with state sync
- `BashSource` - A `ShellRunnable` that wraps bash subprocess execution
- State synchronization via epilogue script piped over fd 62

### How It Works

1. **Preamble**: Set up working directory and positional args
2. **Execute**: Run user's bash code in subprocess
3. **Epilogue**: Dump state (`env -0`, `pwd`) to dedicated pipe
4. **Sync**: Parse epilogue and update Python environment

```python
from shell.compat.bash import source_bash

# Source bash code - modifies environment, wires functions
source_bash('export FOO=bar')()
source_bash('my_func() { echo "hi"; }')()

# Composable with pipelines
source_bash('ls -la') | grep('txt')

# Functions are composable too
source_bash('greet() { echo "hi $1"; }')()
greet('world') | prog('cat')  # Returns a runnable

# Skip function wiring with scope=None
source_bash('helper() { ... }', scope=None)()
```

## State Synchronization

### What Gets Synced

| State | Mechanism |
|-------|-----------|
| Environment variables | `env -0` output → `env[name] = value` |
| Working directory | `pwd` → `cd(pwd)()` |
| Bash functions | `BASH_FUNC_*` env vars + Python wrappers |

### What Doesn't Sync

- Shell options (`set -e`, etc.) - bash handles these internally
- Local variables - only globals visible after function returns
- Job control - background jobs not supported
- Read-only variables (`HOME`, `PPID`, `SHLVL`) - sent to bash but not synced back

### Variable Categories

The state sync uses two variable sets:

| Set | Purpose | Examples |
|-----|---------|----------|
| `_SKIP_VARS` | Not sent or synced (bash-internal) | `PWD`, `OLDPWD`, `BASH_VERSION`, `LINENO` |
| `_READ_ONLY_VARS` | Sent but not synced back | `HOME`, `PPID`, `SHLVL` |

`HOME` must be sent so bash scripts work correctly, but it's read-only in `ShellEnvironment`.

### Function Wiring

Bash functions are:
1. Exported via `export -f` (creates `BASH_FUNC_name%%` env vars)
2. Wrapped in Python callables that return `BashSource` runnables
3. Wired into `__main__` (or custom scope)

```python
source_bash('greet() { echo "Hello, $1!"; }')()

# Now callable from Python
greet('World')()           # Prints: Hello, World!
greet('World') | upper()   # Composable in pipelines
```

Name transformations for Python compatibility:
- `my-func` → `my_func` (dashes to underscores)
- `123func` → `_123func` (prefix underscore for leading digit)
- `if` → `if_` (suffix underscore for keywords)

## Testing

Tests are in `tests/test_bash_subprocess.py`:

```bash
uv run pytest tests/test_bash_subprocess.py -v
```

Key test classes:
- `TestSourceBashBasic` - echo, exit codes, stdout
- `TestSourceBashStateSync` - env vars, pwd, roundtrips
- `TestSourceBashFunctions` - function wiring and calling
- `TestSourceBashComposability` - pipelines, redirects

## Debugging

### Check Bash Version

```python
from shell.compat.bash import BASH_PATH
print(BASH_PATH)  # /opt/homebrew/bin/bash or /bin/bash
```

### Trace State Sync

The epilogue writes to fd 62. To debug, capture it:

```python
from shell.model import capture
result = capture(source_bash('export FOO=bar'), 62)
print(result.read_fd(62))  # Shows epilogue output
```

## Implementation Notes

### Pipe Deadlock Prevention

The parent reads from the state pipe **before** calling `waitpid()`. This prevents deadlock when the child's output exceeds the pipe buffer (~64KB on macOS).

Scripts that define many functions (like nvm.sh with 114 functions) can produce 100KB+ of `env -0` output after `export -f`. If the parent waits first, both processes block forever.

```python
# Correct order in BashSource._exec():
with os.fdopen(state_r, 'r') as f:
    state_output = f.read()     # Read FIRST
_, status = os.waitpid(pid, 0)  # Wait SECOND
```

## Limitations

- **No job control**: Background jobs (`&`) not supported
- **No interactive features**: `read`, job control, etc.
- **Subshell isolation**: Changes in `(...)` don't propagate (bash semantics)
