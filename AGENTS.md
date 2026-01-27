# Agent Instructions

ShipShell is a Python REPL with a custom bash compatibility layer. The name comes from **shell-python** → sh-p → "ship" → ShipShell. It uses tree-sitter-bash for parsing and provides shell-like functionality within Python.

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

# Testing
uv run pytest tests/ -v                    # All tests
uv run pytest tests/test_bash_compat.py -v # Integration tests vs real bash
uv run pytest tests/test_bash_pure.py -v   # Unit tests for pure functions
uv run pytest tests/test_bash_compat.py::test_bash_compat[category/name] -v  # Single test

# Quality gates (run before committing)
uv run ruff check shell/
uv run pyright shell/
```

## Testing

Test organization:
- `tests/test_bash_compat.py` - Integration tests comparing against real bash
- `tests/test_bash_pure.py` - Unit tests for pure functions
- `tests/test_harness_smoke.py` - Harness smoke tests

See `tests/AGENTS.md` for detailed testing patterns and the test harness API.

## Component Documentation

Component-specific patterns and debugging utilities:
- `tests/AGENTS.md` - Test harness API and testing patterns
- `shell/compat/AGENTS.md` - Bash interpreter debugging and visitor patterns

## Architecture

### Core Components

- **`shell/model.py`** - Command execution abstraction (`ShellResult`, `IOConfig`, `InProcessCallable`)
- **`shell/builtins.py`** - Shell builtins (cd, pwd, echo, test, source, etc.)
- **`shell/environment.py`** - Shell environment state (`ShellEnvironment` singleton)
- **`shell/compat/bash.py`** - Bash interpreter using tree-sitter (2500+ lines)
- **`shell/repl/`** - Interactive REPL using prompt_toolkit

### Bash Interpreter Pattern

The interpreter in `shell/compat/bash.py` uses a visitor pattern:
- `visit_*` methods execute nodes with side effects (commands, assignments)
- `evaluate_*` methods return values without side effects (expansions, expressions)

Pure functions (tested in `test_bash_pure.py`): `_bash_to_str()`, `_expand_braces()`, `_split_commas()`, etc.

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

### User Initialization Phases

1. `env.initialize()` - Base shell environment
2. `user.initialize_config()` - `~/.config/pysh/config.py` (Phase 1, before venv)
3. `py_env.initialize_shell_venv()` - Bootstrap venv at `~/.config/pysh/.venv`
4. `user.initialize_user()` - `~/.config/pysh/user.py` (Phase 2, full functionality)

## Code Style

- Python 3.12+, line length 100
- Single quotes for strings
- Ruff for linting (E, F, I, Q, UP, B, C4 rules)
- Pyright for type checking

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

