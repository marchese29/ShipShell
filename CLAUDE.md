# Agent Instructions

ShipShell is a **Python REPL with ergonomic shell bindings**. The name comes from **shell-python** → sh-p → "ship" → ShipShell.

The core value is `shell/model.py` - Pythonic abstractions for shell operations (pipelines, subshells, process substitution, I/O redirection) that make the REPL powerful. Bash compatibility (`shell/compat/`) is a feature that lets you run bash syntax, but the primary interface is Python.

This is a **UV-managed project**. Always use `uv run` to execute Python code:
```bash
uv run python ...      # Run Python scripts
uv run pytest ...      # Run tests
```

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

## Dependency Management

Use `uv` for managing dependencies rather than editing `pyproject.toml` directly:

```bash
uv add <package>           # Add runtime dependency
uv add --dev <package>     # Add dev dependency
uv remove <package>        # Remove dependency
uv sync                    # Sync lockfile with pyproject.toml
```

## Commands

```bash
# Run the shell
uv run python main.py

# See a demo of ShipShell's Python+shell integration
uv run python demo.py

# Testing
uv run pytest tests/ -v                              # All tests
uv run pytest tests/test_bash_subprocess.py -v       # Bash subprocess tests
uv run pytest tests/test_callable_pipeline.py -v     # Python API tests

# Quality gates (run before committing)
uv run ruff check shell/
uv run pyright shell/
```

## Testing

Test organization:
- `tests/test_bash_subprocess.py` - Bash subprocess runner tests (`source_bash`)
- `tests/test_io_extra_fds.py` - Extra file descriptor redirection tests
- `tests/test_callable_pipeline.py` - Python API tests (pipelines, process substitution, conditional chaining)
- `tests/test_trap.py` - Trap system tests
- `tests/test_pty.py` - PTY-based output capture tests

See `tests/CLAUDE.md` for detailed testing patterns.

### Bash Version

The bash runner prefers `/opt/homebrew/bin/bash` (bash 5.x via Homebrew) over `/bin/bash` (macOS ships with bash 3.2). Install modern bash for full feature support:

```bash
brew install bash
```

## Component Documentation

Component-specific patterns and debugging utilities:
- `tests/CLAUDE.md` - Testing patterns
- `shell/compat/CLAUDE.md` - Bash subprocess runner and state synchronization
- `designs/job-control.md` - Job control design (PTY-based, file-backed output capture)

**⚠️ Keep documentation current**: Before committing code changes, ALWAYS consider if CLAUDE.md files need updates. New features, API changes, and architectural insights should be reflected here. These files are the primary onboarding path for agents - stale docs waste context and cause confusion.

## Architecture

### Core Components

- **`main.py`** - Entry point for the interactive REPL
- **`shell/model.py`** - **The heart of ShipShell**: Pythonic shell abstractions (`ShellRunnable`, `Command`, `Pipeline`, `Subshell`, `ProcessSubstitution`, `ConditionalChain`, `run()`, `prog()`)
- **`shell/terminal.py`** - Dual-PTY allocation and proxy loop for output capture (`create_context()`, `PtyContext`)
- **`shell/util.py`** - Shared low-level utilities (`try_close()`, `exit_code_from_status()`)
- **`shell/environment.py`** - Shell environment state (`ShellEnvironment` singleton, copy-on-read for mutables)
- **`shell/builtins.py`** - Shell builtins (cd, pwd, echo, test, source, trap, set, etc.)
- **`shell/trap.py`** - Trap system for shell events and signals
- **`shell/repl.py`** - Interactive REPL with hooks and execution logic
- **`shell/rl.py`** - GNU readline ctypes bindings (callback interface for event-loop-friendly input)
- **`shell/compat/bash.py`** - Bash subprocess runner with state synchronization (~400 lines)

### Job Control (Planned)

See `designs/job-control.md` for the full design. The plan: every forked process gets
dual PTYs (stdout/stderr) with file-backed output capture. Background jobs use forked
drain processes to shuttle PTY output to files. Foreground jobs use a `select()`-based
proxy loop in the parent. Jobs are first-class Python objects (`bg()`, `fg()`,
`on_exit()`, `send_input()`, late piping). The architecture is fork-safe (no threads),
portable (POSIX `select()` + self-pipe trick), and enables seamless fg/bg transitions
with full terminal fidelity.

### GNU Readline Requirement

The REPL uses GNU readline's callback interface via ctypes. On macOS, this requires Homebrew's readline (the system `libreadline` is actually libedit):

```bash
brew install readline
```

History is saved to `~/.pysh_history` by default. Override with `PYSH_HISTFILE` environment variable.

### Python Shell API (`shell/model.py`)

The primary interface - ergonomic Python for shell operations:

```python
from shell.model import prog, run, sub
from shell.wiring import wire_path_programs
from pathlib import Path

# Wire PATH programs into __main__ for maximum ergonomics
wire_path_programs()  # Now ls, grep, cat, etc. are available

# Clean, Pythonic shell pipelines
ls('-la') | grep('hello') > Path.home() / 'my-file.txt'
cat() < 'input.txt' | sort() | uniq()

# Native Python functions work seamlessly in pipelines
def upper():
    for line in sys.stdin:
        print(line.upper(), end='')

def add_timestamp():
    from datetime import datetime
    for line in sys.stdin:
        print(f'[{datetime.now()}] {line}', end='')

# Mix shell commands and Python functions freely
cat('server.log') | grep('ERROR') | upper | add_timestamp > 'errors.txt'

# Capture output for further Python processing
result = run(ls('-la') | grep('.py'), silent=True)
for line in result.read_stdout().splitlines():
    print(f'Found: {line}')

# Subshells isolate environment changes
sub(cd('/tmp') | ls())()  # cd doesn't affect parent

# Process substitution
with echo('hello').as_input() as inp:
    cat(inp.path)()
```

For explicit control without wiring, use `prog()`:

```python
prog('my-cmd')('--flag', 'arg')()      # Build any command
run(prog('grep')('pattern', 'file'))   # Explicit run()

# Programs auto-invoke with no args for operators and methods
prog('echo')('hello') | prog('cat')    # No need for prog('cat')()
prog('true') + prog('echo')('done')    # No need for prog('true')()
prog('cat') < 'input.txt'              # Redirects work too
prog('cat').stdin_content('hello')()   # Methods delegate automatically

# Pipe content directly to stdin
prog('cat')().stdin_content('hello world')()
prog('wc')('-l').stdin_content(open('data.txt'))()  # File-like objects too
```

### Bash Compatibility (`shell/compat/bash.py`)

The bash compatibility layer runs **real bash** as a subprocess and synchronizes state back to Python:

```python
from shell.compat.bash import source_bash

# Source bash code - modifies environment, wires functions
source_bash('export FOO=bar')()
source_bash('greet() { echo "hi $1"; }')()

# Composable with pipelines
source_bash('ls -la') | grep('txt')

# Wired functions return runnables
greet('world')()              # Execute
greet('world') | prog('cat')  # Compose
```

State synchronized: environment variables, working directory, bash functions (wired as Python callables).

### Process Substitution (`shell/model.py`)

Process substitution allows command output to be used as a file path:

```python
from shell.model import prog, run

# Input substitution <(cmd) - read from command's stdout
with prog('echo')('hello').as_input() as inp:
    run(prog('cat')(inp.path))

# Output substitution >(cmd) - write to command's stdin
with prog('grep')('error').as_output() as out:
    run(prog('echo')('error line') > out.path)

# Multiple substitutions
with (
    prog('ls')('dir1').as_input() as a,
    prog('ls')('dir2').as_input() as b,
):
    run(prog('diff')(a.path, b.path))
```

**Key details:**
- Context-managed: `.path` and `.fd` only valid inside `with` block
- Uses high FD numbers (63+) for macOS `/dev/fd/N` compatibility
- Eager execution: child process starts on `__enter__`

### Conditional Chaining (`shell/model.py`)

Short-circuit conditional execution, equivalent to bash's `&&` and `||`:

```python
from shell.model import prog, run

# if_success() - run second only if first succeeds (bash &&)
prog('make')().if_success(prog('make')('install'))()

# if_fail() - run second only if first fails (bash ||)
prog('test')('-f', 'config.json').if_fail(prog('cp')('config.example.json', 'config.json'))()

# Ergonomic operators: + for &&, - for ||
prog('true')() + prog('echo')('succeeded')   # runs echo
prog('false')() - prog('echo')('recovered')  # runs echo

# Chain multiple conditions
prog('build')() + prog('test')() + prog('deploy')() - notify_failure

# Plain callables auto-wrap (like | operator)
prog('true')() + lambda: print('done!')
```

**Key details:**
- Returns `ConditionalChain`, a `ShellRunnable` (deferred execution)
- `+` is alias for `if_success()`, `-` is alias for `if_fail()`
- Uncalled `Program` objects auto-call with no args: `prog('true') + prog('echo')` works
- Plain callables auto-wrapped in `InProcessCallable`
- Exit code comes from last command that actually ran

### Trap System (`shell/trap.py`)

The shell supports traps for responding to events and signals:

**Synthetic traps** (shell events):
- `DEBUG` - Fires BEFORE command execution
- `TRACE` - Fires AFTER every command (regardless of exit code)
- `ERR` - Fires AFTER command with non-zero exit only
- `EXIT` - Fires when shell/subshell exits
- `RETURN` - Fires when function returns (future)

**Signal traps**: `SIGINT`, `SIGTERM`, `SIGHUP`, `SIGQUIT`, `SIGALRM`, `SIGUSR1`, `SIGUSR2`

**Usage** (Python API, not bash syntax):
```python
from shell.trap import TrapType

# Set a trap (plain callables auto-wrapped in InProcessCallable)
env.traps.set(TrapType.EXIT, lambda: print("goodbye"))
env.traps.set(TrapType.ERR, lambda: print(f"command failed with {env.last_exit}"))

# Clear a trap
env.traps.set(TrapType.EXIT, None)

# Check current traps
trap_show()()  # or env.traps.list()
```

**Key behaviors:**
- Traps fire via `run()` for "atomic" runnables (`Command`, `InProcessCallable`)
- EXIT traps fire from REPL finally block and subshell cleanup
- Synthetic traps don't inherit to subshells (bash default); signal traps do
- Reentrancy guard prevents recursive trap firing

### Builtin Commands (`shell/builtins.py`)

The `@builtin_command` decorator provides **bash calling convention compatibility** for shell builtins. It is NOT a universal wrapper for all functions.

**When to use `@builtin_command`:**
- Builtins that users call with bash-style arguments: `cd`, `echo`, `pwd`, `test`
- Functions that need flag parsing (`-n`, `-e`, `-P`) via the `_Flag` annotation
- Commands where positional args come as a tuple of strings

**When NOT to use it:**
- Python-first APIs where you want native types (e.g., `env.traps.set()`)
- Internal helper functions
- Functions that accept complex Python objects as arguments

**Example - good use:**
```python
@builtin_command
def echo(
    args: tuple[str, ...] = (),
    escape: Annotated[bool, _Flag('e')] = False,  # -e flag
):
    ...
```

**Anti-pattern - don't do this:**
```python
# DON'T wrap Python-first APIs in @builtin_command
@builtin_command  # Wrong!
def set_trap(handler: ShellRunnable, signal: TrapType):
    ...

# DO expose a clean Python API instead
env.traps.set(TrapType.EXIT, handler)
```

### Environment (`shell/environment.py`)

The shell environment uses **copy-on-read semantics** for mutable values. When you access a mutable value like `PATH`, you get a copy—mutations don't affect the stored value:

```python
# This does NOT modify PATH:
path = env.path
path.append(Path('/new/dir'))  # Mutates a copy

# This DOES modify PATH:
path = env.path
path.append(Path('/new/dir'))
env.path = path  # Explicit reassignment
# Or equivalently:
env['PATH'] = path
```

Both `env.path = value` and `env['PATH'] = value` work. Read-only variables (`HOME`, `PPID`, `SHLVL`, `$`, `?`) raise `ValueError` on assignment.

Iteration (`env.items()`, `for k in env`) includes both regular variables and computed variables (`HOME`, `PATH`, `PWD`, etc.). This ensures `source_bash()` can pass all variables to the subprocess.

This design:
- Prevents silent sync failures (mutations not propagating to `os.environ`)
- Makes the assignment intent explicit
- Matches how `os.environ` works (strings are immutable anyway)

### User Initialization Phases

1. `env.initialize()` - Base shell environment
2. `user.initialize_config()` - `~/.config/pysh/config.py` (Phase 1, before venv)
3. `py_env.initialize_shell_venv()` - Bootstrap venv at `~/.config/pysh/.venv`
4. `user.initialize_user()` - `~/.config/pysh/user.py` (Phase 2, full functionality)

### Error Handling Philosophy

ShipShell bridges two error paradigms: **Python exceptions** and **shell exit codes**. The design principle is simple:

> **Above `run()` = Python exceptions. Inside `run()` and below = shell exit codes.**

**User experience goal:** Running normal Python produces exceptions; running anything through the shell abstraction (even pure Python via `InProcessCallable`) returns exit codes.

**The boundary points** where exceptions convert to exit codes:

| Location | What Happens |
|----------|--------------|
| **`builtins.py`** | `ShellError` → its `.exit_code`; `TypeError`/`ValidationError` → 2; `OSError` → 1; other → 1 |
| **`InProcessCallable._exec()`** | Any exception → exit code 1. Return values: `None`→0, `bool`→0/1, `int`→int |
| **Forked children** (Pipeline, Subshell) | `SystemExit` → extracted code; other exceptions → 1; then `os._exit()` |
| **Bash subprocess** | `exit_code_from_status()` from child process (bash 128+signal convention) |

**Key type:** `ShellError` (`shell/types.py`) is the domain exception for builtins. It carries both a message and an exit code:

```python
class ShellError(Exception):
    def __init__(self, message: str, exit_code: int = 1):
        self.message = message
        self.exit_code = exit_code
```

**Central tracking:** `env.last_exit` is set exactly once, at the end of every `run()` call. This is the single source of truth for `$?`.

**REPL boundary:** If an exception escapes the shell abstraction entirely (bubbles up through `run()`), the REPL catches it with `traceback.print_exc()` but does NOT set `env.last_exit`. This is intentional—exceptions that escape are "pure Python" and shouldn't pollute shell state.

## Code Style

- Python 3.12+, line length 100
- Single quotes for strings
- Ruff for linting (E, F, I, Q, UP, B, C4 rules)
- Pyright for type checking
- **No string forward references** - All modules use `from __future__ import annotations`, so type hints are lazily evaluated. Use `Program` not `'Program'`. Enforced by ruff's UP037.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds

